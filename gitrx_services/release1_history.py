"""Durable metadata-only history passes with duplicate-aware agreement."""
from pathlib import Path
from collections import Counter, defaultdict
from datetime import datetime
import hashlib,json,time
from urllib.parse import urlencode
from .release1_http import Stop,utc,canonical,hash_file

SCHEMA='''CREATE TABLE IF NOT EXISTS release1_history_passes(
 id INTEGER PRIMARY KEY,setid TEXT,pass_number INTEGER,started_at TEXT,status TEXT,
 signature TEXT,canonical_json TEXT,anomalies_json TEXT,UNIQUE(setid,pass_number));
 CREATE TABLE IF NOT EXISTS release1_history_pages(
 pass_id INTEGER,page INTEGER,retrieval_id INTEGER,response_json TEXT,
 PRIMARY KEY(pass_id,page));
 CREATE TABLE IF NOT EXISTS release1_history_selection(setid TEXT PRIMARY KEY,pass_id INTEGER);
 CREATE TABLE IF NOT EXISTS release1_current_history(setid TEXT,version INTEGER,pass_id INTEGER,PRIMARY KEY(setid,version));'''

def init(c):c.executescript(SCHEMA);c.commit()

def _capture(c,sid,p,latest,fetch,record,identity_handler=None):
    page=1;target_pages=None;expected=None;rows=[];flags=[];raw_pages=[]
    while True:
        url='https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/'+sid+'/history.json?'+urlencode({'pagesize':100,'page':page})
        saved=c.execute('SELECT * FROM release1_history_pages WHERE pass_id=? AND page=?',(p['id'],page)).fetchone()
        if saved:response=json.loads(saved['response_json'])
        else:
            response=fetch(url,p['pass_number'],p['started_at'])
            rid=record(response)
            c.execute('INSERT INTO release1_history_pages VALUES(?,?,?,?)',(p['id'],page,rid,json.dumps(response)));c.commit()
        if response['status']!=200 or response.get('error'):
            flags.append({'code':'history_http_failed','page':page,'HTTP_status':response['status']});break
        d=json.loads(Path(response['path']).read_bytes());data=d['data']
        if data['spl']['setid'].lower()!=sid:
            if identity_handler is None:raise Stop('HISTORY_IDENTITY_MISMATCH')
            from .release1_history_incidents import HistoryIdentityConflict
            try:identity_handler(sid,url,response,d)
            except HistoryIdentityConflict:
                c.execute("UPDATE release1_history_passes SET status='identity_conflict',anomalies_json=? WHERE id=?",(json.dumps({'flags':[{'code':'history_identity_conflict','observed_setid':data['spl']['setid'],'sha256':response['sha256']}]}),p['id']));c.commit();raise
            raise Stop('HISTORY_IDENTITY_MISMATCH')
        m=d['metadata'];total=int(m['total_elements']);pages=int(m['total_pages']);current=int(m['current_page'])
        if current!=page:flags.append({'code':'shifted_current_page','requested':page,'observed':current})
        if target_pages is None:target_pages=max(1,pages);expected=total
        elif pages!=target_pages or total!=expected:
            flags.append({'code':'pagination_metadata_shift','page':page,'first_total':expected,'total':total,'first_pages':target_pages,'pages':pages})
            target_pages=max(target_pages,pages)
        items=data['history'];rows.extend((int(x['spl_version']),x.get('published_date')) for x in items)
        raw_pages.append({'page':page,'rows':len(items),'total_elements':total,'total_pages':pages,'sha256':response['sha256']})
        if page>=target_pages:break
        if page>=10000:flags.append({'code':'unbounded_history_pages'});break
        page+=1
    if expected is None:expected=0
    if len(rows)!=expected:flags.append({'code':'raw_row_count_differs_from_total','rows':len(rows),'total':expected})
    if not rows:flags.append({'code':'empty_history'})
    versions=defaultdict(set)
    for version,date in rows:versions[version].add(date)
    # Listing disagreement is retained separately; it does not invalidate stable history.
    canonical=[[v,sorted(dates,key=lambda d:'' if d is None else d)] for v,dates in sorted(versions.items())]
    signature=hashlib.sha256(json.dumps(canonical,separators=(',',':')).encode()).hexdigest()
    info={'raw_rows':len(rows),'unique_versions':len(versions),'duplicate_version_rows':len(rows)-len(versions),
          'exact_duplicate_rows':len(rows)-len(set(rows)),'pages':raw_pages,'flags':flags,
          'listing_disagreement':({'history_latest':max(versions),'scope_latest':latest} if versions and max(versions)<latest else None)}
    state='complete' if not flags else 'incomplete'
    c.execute('UPDATE release1_history_passes SET status=?,signature=?,canonical_json=?,anomalies_json=? WHERE id=?',
              (state,signature,json.dumps(canonical),json.dumps(info),p['id']));c.commit()

def collect(c,sid,latest,fetch,record,sleep=time.sleep,identity_handler=None,pass_started=None):
    init(c)
    chosen=c.execute('SELECT p.* FROM release1_history_selection s JOIN release1_history_passes p ON p.id=s.pass_id WHERE s.setid=?',(sid,)).fetchone()
    if chosen:
        return {'state':'accepted','pass_id':chosen['id'],'canonical':json.loads(chosen['canonical_json']),'reused':True,'passes':chosen['pass_number']}
    previous=None;any_complete=False;any_rows=False
    for number in range(1,4):
        p=c.execute('SELECT * FROM release1_history_passes WHERE setid=? AND pass_number=?',(sid,number)).fetchone()
        if p is None:
            if number>1 and (number==3 or previous is None or previous['status']!='complete'):sleep(min(30,2**(number-1)))
            c.execute("INSERT INTO release1_history_passes(setid,pass_number,started_at,status) VALUES(?,?,?,'running')",(sid,number,(pass_started(number) if pass_started else None) or utc()));c.commit()
            p=c.execute('SELECT * FROM release1_history_passes WHERE setid=? AND pass_number=?',(sid,number)).fetchone()
        if p['status']=='running':
            try:_capture(c,sid,p,latest,fetch,record,identity_handler)
            except Stop:raise
            except (ValueError,KeyError,TypeError,OSError) as exc:
                c.execute("UPDATE release1_history_passes SET status='incomplete',anomalies_json=? WHERE id=?",
                          (json.dumps({'flags':[{'code':'metadata_decode_or_fetch_failed','exception_type':type(exc).__name__}]}),p['id']));c.commit()
            p=c.execute('SELECT * FROM release1_history_passes WHERE id=?',(p['id'],)).fetchone()
        complete=p['status']=='complete';any_complete|=complete
        any_rows|=bool(json.loads(p['canonical_json'] or '[]'))
        if complete and previous is not None and previous['status']=='complete' and previous['signature']==p['signature']:
            c.execute('INSERT OR IGNORE INTO release1_history_selection VALUES(?,?)',(sid,p['id']));c.commit()
            return {'state':'accepted','pass_id':p['id'],'canonical':json.loads(p['canonical_json']),'reused':False,'passes':number}
        previous=p
    return {'state':'history_unstable' if any_complete or any_rows else 'fetch_failed','passes':3,'canonical':None,'pass_id':None,'reused':False}

def fetch_into_store(runner,c,root,fetcher,sid,latest):
    def fetch(url,number,started):
        if number>1:
            since=datetime.fromisoformat(started).timestamp()
            # A killed worker may have committed HTTP evidence but not the page pointer.
            for row in runner.t.c.execute('SELECT * FROM http WHERE complete=1 AND status=200 AND error IS NULL AND start_epoch>=? ORDER BY id DESC',(since,)):
                if canonical(row['origin_url'])!=canonical(url):continue
                path=runner.root/'network/responses'/f"{row['id']}.json"
                if path.exists():
                    x=json.loads(path.read_bytes())
                    if hash_file(x['path'])!=x['sha256']:raise Stop('PROTECTED_HISTORY_HASH_CHANGED')
                    return x
        return runner.t.get(url,cache=number==1 and not getattr(runner,'retry_round',0))
    from .release1_history_incidents import handle,HistoryIdentityConflict
    try:result=collect(c,sid,latest,fetch,lambda response:runner.record(c,root,fetcher,response,'history'),identity_handler=lambda *args:handle(runner,*args))
    except HistoryIdentityConflict:
        result={'state':'history_identity_conflict','pass_id':None}
        c.execute("UPDATE retrievals SET state='history_identity_conflict' WHERE id IN (SELECT h.retrieval_id FROM release1_history_pages h JOIN release1_history_passes p ON p.id=h.pass_id WHERE p.setid=? AND p.status='identity_conflict')",(sid,));c.commit()
    scope_path=runner.root/'scope.json'
    scope=json.loads(scope_path.read_bytes())
    return persist_inventory(c,sid,result,scope['records'][sid],hash_file(scope_path),scope['captures'])


def persist_inventory(c,sid,result,item,scope_sha256,captures,run_id=1):
    """Persist captured inventory/publication evidence shared by acquisition jobs.

    The family must already be explicitly approved/inserted by the caller. Full
    source pass records remain authoritative. Nonpositive upstream versions are
    retained in metadata/source pages, never invented into ordinary ZIP targets.
    Listing-only positive targets have no inferred publication observation.
    """
    init(c)
    c.executescript("""CREATE TABLE IF NOT EXISTS tranche_publication_observations(
      setid TEXT,version INTEGER,ordinal INTEGER,publication_raw TEXT,publication_date TEXT,
      PRIMARY KEY(setid,version,ordinal));
      CREATE TABLE IF NOT EXISTS release1_listing_only_targets(
      setid TEXT,version INTEGER,provenance_json TEXT,PRIMARY KEY(setid,version));""")
    row=c.execute('SELECT metadata_json FROM families WHERE setid=?',(sid,)).fetchone()
    if row is None:raise ValueError('Inventory family must be approved before persistence')
    passes=[{'number':p['pass_number'],'status':p['status'],'signature':p['signature'],
             'observations':json.loads(p['anomalies_json'] or '{}')}
            for p in c.execute('SELECT * FROM release1_history_passes WHERE setid=? ORDER BY pass_number',(sid,))]
    canonical=result.get('canonical') or []
    saved_pages=[]
    if result['state']=='accepted':
        from collections import defaultdict
        observed=defaultdict(set)
        selected=c.execute('SELECT * FROM release1_history_passes WHERE id=? AND setid=?',
                           (result['pass_id'],sid)).fetchone()
        if selected is None or selected['status']!='complete' or json.loads(selected['canonical_json'])!=canonical:
            raise ValueError('Accepted history does not match retained source pass')
        for page in c.execute('SELECT * FROM release1_history_pages WHERE pass_id=? ORDER BY page',(result['pass_id'],)).fetchall():
            response=json.loads(page['response_json'])
            if hash_file(response['path'])!=response['sha256']:raise Stop('PROTECTED_HISTORY_HASH_CHANGED')
            data=json.loads(Path(response['path']).read_bytes())
            if data['data']['spl']['setid'].lower()!=sid:raise Stop('HISTORY_IDENTITY_MISMATCH')
            for source_row in data['data']['history']:
                observed[int(source_row['spl_version'])].add(source_row.get('published_date'))
            saved_pages.append((page,data))
        source_canonical=[[v,sorted(dates,key=lambda d:'' if d is None else d)] for v,dates in sorted(observed.items())]
        source_signature=hashlib.sha256(json.dumps(source_canonical,separators=(',',':')).encode()).hexdigest()
        if source_canonical!=canonical or source_signature!=selected['signature']:
            raise Stop('HISTORY_SOURCE_INVENTORY_MISMATCH')
    latest=item.get('latest_versions_seen',[])
    if not latest and item.get('spl_version') is not None:latest=[item['spl_version']]
    latest=[int(v) for v in latest]
    capture={'state':result['state'],'passes':passes,'accepted_pass':result.get('pass_id'),
             'duplicate_versions_deduped':True}
    nonpositive=[{'version':int(v),'publication_raw_values':dates} for v,dates in canonical if int(v)<1]
    if nonpositive:capture['nonpositive_history_versions']=nonpositive
    if any(v<1 for v in latest):capture['nonpositive_listing_versions']=[v for v in latest if v<1]
    md=json.loads(row[0]);md['history_capture']=capture
    c.execute('UPDATE families SET metadata_json=? WHERE setid=?',(json.dumps(md),sid));c.commit()
    if result['state']!='accepted':return result['state']
    c.execute('INSERT OR IGNORE INTO ingest_runs(id,started_at) VALUES(?,?)',(run_id,utc()))
    for version,dates in canonical:
        version=int(version)
        if version<1:continue
        c.execute('INSERT OR IGNORE INTO versions(setid,version) VALUES(?,?)',(sid,version))
        c.execute('INSERT OR IGNORE INTO release1_current_history VALUES(?,?,?)',(sid,version,result['pass_id']))
        for raw in dates:
            try:date=datetime.strptime(raw,'%b %d, %Y').date().isoformat()
            except (TypeError,ValueError):date=None
            if not c.execute('SELECT 1 FROM tranche_publication_observations WHERE setid=? AND version=? AND publication_raw IS ?',(sid,version,raw)).fetchone():
                ordinal=c.execute('SELECT COUNT(*) FROM tranche_publication_observations WHERE setid=? AND version=?',(sid,version)).fetchone()[0]
                c.execute('INSERT INTO tranche_publication_observations VALUES(?,?,?,?,?)',(sid,version,ordinal,raw,date))
    for page,d in saved_pages:
        m=d['metadata']
        c.execute('INSERT OR IGNORE INTO history_pages VALUES(?,?,?,?,?,?)',(run_id,sid,page['page'],page['retrieval_id'],int(m['total_elements']),int(m['total_pages'])))
        for item_row in d['data']['history']:
            number=int(item_row['spl_version'])
            if number<1:continue
            version_row=c.execute('SELECT id FROM versions WHERE setid=? AND version=?',(sid,number)).fetchone()
            if version_row is None:raise ValueError('Source history page version absent from accepted inventory')
            raw=item_row.get('published_date')
            try:date=datetime.strptime(raw,'%b %d, %Y').date().isoformat()
            except (TypeError,ValueError):date=None
            c.execute('INSERT OR IGNORE INTO history_entries VALUES(?,?,?,?,?)',(run_id,version_row[0],page['retrieval_id'],raw or '',date))
    history_versions={int(v) for v,dates in canonical if int(v)>0}
    for version in latest:
        if version<1 or version in history_versions:continue
        c.execute('INSERT OR IGNORE INTO versions(setid,version) VALUES(?,?)',(sid,version))
        c.execute('INSERT OR IGNORE INTO release1_listing_only_targets VALUES(?,?,?)',(sid,version,json.dumps({
            'basis':'frozen_category_listing_only','scope_sha256':scope_sha256,
            'scope_captures':captures,'history_pass_id':result['pass_id'],
            'publication_date':'unknown; no history observation'})))
    c.commit()
    flag_publication_inversions(c,sid,run_id=run_id)
    return None

def flag_publication_inversions(c,sid,run_id=1):
    # Same existing lower-version/later-publication rule; all duplicate-date observations retained.
    rows=c.execute('''SELECT v.id,v.version,p.publication_date FROM versions v
        JOIN tranche_publication_observations p ON p.setid=v.setid AND p.version=v.version
        WHERE v.setid=? AND p.publication_date IS NOT NULL''',(sid,)).fetchall()
    for lower in rows:
        for higher in rows:
            if lower['version']<higher['version'] and lower['publication_date']>higher['publication_date']:
                c.execute('INSERT OR IGNORE INTO date_anomalies VALUES(?,?,?,?,?)',(run_id,lower['id'],higher['id'],lower['publication_date'],higher['publication_date']))
                c.execute('UPDATE versions SET date_anomaly=1 WHERE id IN (?,?)',(lower['id'],higher['id']))
    c.commit()
