"""Detached raw/parse-only NDA/BLA worker, checkpoints, status and local exports."""
from pathlib import Path
from datetime import datetime,timezone
from collections import Counter
from urllib.parse import urlencode,urlsplit,parse_qs,urljoin
import argparse,fcntl,hashlib,io,json,math,os,shutil,signal,sqlite3,statistics,subprocess,sys,threading,time,traceback,zipfile
from lxml import html

from .release1_http import PROJECT,Transport,Stop,atomic,guard,hash_file,utc,canonical
from .io import readonly
from .integrity import filter_inventory, exclusion_pattern, verify_inventory

REPORT=PROJECT/'reports/block3c'
DEFAULT_CONFIG=REPORT/'config.json'

def control(root):
    c=sqlite3.connect(Path(root)/'control.sqlite',timeout=60);c.row_factory=sqlite3.Row
    c.executescript('''PRAGMA journal_mode=WAL;CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE IF NOT EXISTS families(setid TEXT PRIMARY KEY,ordinal INTEGER,tranche INTEGER,status TEXT DEFAULT 'pending',history_failure TEXT);
    CREATE TABLE IF NOT EXISTS targets(setid TEXT,version INTEGER,tranche INTEGER,state TEXT DEFAULT 'pending',route TEXT,raw_sha256 TEXT,parse_status TEXT,parse_id INTEGER,PRIMARY KEY(setid,version));
    CREATE TABLE IF NOT EXISTS tranches(number INTEGER PRIMARY KEY,status TEXT,verified_at TEXT,snapshot_id TEXT,counts_json TEXT);
    CREATE TABLE IF NOT EXISTS exports(checkpoint TEXT PRIMARY KEY,tranches INTEGER,status TEXT,pid INTEGER,error_type TEXT);
    CREATE TABLE IF NOT EXISTS retry_tranches(round INTEGER,number INTEGER,root TEXT,status TEXT,snapshot_id TEXT,counts_json TEXT,PRIMARY KEY(round,number));
    CREATE TABLE IF NOT EXISTS family_events(id INTEGER PRIMARY KEY,setid TEXT,round INTEGER,status TEXT,error_type TEXT,at TEXT);''')
    columns={r[1] for r in c.execute('PRAGMA table_info(families)')}
    for name,definition in [('attempt_round','INTEGER DEFAULT 0'),('attempts','INTEGER DEFAULT 0'),('source_root','TEXT'),('error_type','TEXT')]:
        if name not in columns:c.execute('ALTER TABLE families ADD COLUMN '+name+' '+definition)
    target_columns={r[1] for r in c.execute('PRAGMA table_info(targets)')}
    for name,definition in [('source_root','TEXT'),('attempt_round','INTEGER DEFAULT 0')]:
        if name not in target_columns:c.execute('ALTER TABLE targets ADD COLUMN '+name+' '+definition)
    c.commit();return c

class WorkerInterrupted(BaseException):pass

def process_info(pid,fragment):
    if not pid:return None
    p=subprocess.run(['ps','-p',str(pid),'-o','stat=,command='],capture_output=True,text=True)
    text=p.stdout.strip()
    if not text or text.startswith('Z') or fragment not in text:return None
    return text

def setmeta(c,**values):
    with c:c.executemany('INSERT OR REPLACE INTO meta VALUES(?,?)',[(k,json.dumps(v)) for k,v in values.items()])

def meta(c):return {r[0]:json.loads(r[1]) for r in c.execute('SELECT * FROM meta')}

def protected_check():
    pins=json.loads((REPORT/'protected-baseline.json').read_bytes())['hashes']
    return verify_inventory(PROJECT,pins)

def link_verified(source,dest,want,root):
    source,dest=Path(source),Path(dest);guard(dest,source.stat().st_size+64*1024**2)
    if hash_file(source)!=want:raise Stop('PROTECTED_SOURCE_HASH_CHANGED')
    dest.parent.mkdir(parents=True,exist_ok=True)
    if dest.exists():
        if hash_file(dest)!=want:raise Stop('CONFLICTING_ARTIFACT_BYTES')
        return
    if source.stat().st_dev==dest.parent.stat().st_dev:os.link(source,dest)
    else:
        temporary=dest.with_name(dest.name+'.copy-'+str(time.time_ns()))
        with source.open('rb') as a,temporary.open('xb') as b:
            for chunk in iter(lambda:a.read(1024*1024),b''):
                guard(dest,len(chunk)+64*1024**2);b.write(chunk)
            b.flush();os.fsync(b.fileno())
        if hash_file(temporary)!=want:raise Stop('COPIED_HASH_MISMATCH')
        os.link(temporary,dest) # retain completed/partial copy evidence; no cleanup
    if hash_file(dest)!=want:raise Stop('COPIED_HASH_MISMATCH')
    with (Path(root)/'copy-verifications.jsonl').open('a') as f:f.write(json.dumps({'at':utc(),'source':str(source),'destination':str(dest),'sha256':want,'method':'hardlink' if source.stat().st_dev==dest.parent.stat().st_dev else 'copy','verified':True})+'\n')

class GuardDB:
    """Bound each new storage statement without changing frozen parser logic."""
    def __init__(self,c,root):self.c=c;self.root=root
    def execute(self,sql,parameters=()):
        if not sql.lstrip().upper().startswith(('SELECT','PRAGMA')):
            size=sum(len(x.encode()) if isinstance(x,str) else len(x) if isinstance(x,bytes) else 16 for x in parameters)
            guard(self.root,64*1024**2+4*size)
        return self.c.execute(sql,parameters)
    def executemany(self,sql,params):
        for p in params:self.execute(sql,p)
        return self.c.execute('SELECT 1 WHERE 0')
    def __getattr__(self,name):return getattr(self.c,name)
    def __enter__(self):self.c.__enter__();return self
    def __exit__(self,*a):return self.c.__exit__(*a)

def xml_identity(path):
    from gitrx.current.archive import read_xml,NS
    try:
        with zipfile.ZipFile(path) as z:
            docs=[]
            for m in z.infolist():
                if not m.filename.lower().endswith('.xml'):continue
                if m.file_size>100*1024**2:raise ValueError('XML safety bound')
                d=read_xml(z.read(m));sid=d.find('s:setId',NS);v=d.find('s:versionNumber',NS)
                if sid is not None and v is not None:docs.append((sid.get('root','').lower(),int(v.get('value'))))
            if len(docs)!=1:raise Stop('AMBIGUOUS_XML_IDENTITY')
            return docs[0]
    except (zipfile.BadZipFile,ValueError):return None

def status(config):
    root=Path(config['data_root']);result={'data_root':str(root),'at':utc(),'phase':'not launched','tranches_done':0,'families_done':0,'listed_versions':0,'retrieved_versions':0,'archive_route_versions':0,'not_retrievable_by_attempted_routes':0,'partial':0,'parse_failed':0,'requests':config.get('prior_requests',0),'bytes':0,'free_GiB':shutil.disk_usage(root).free/1024**3,'stop_reason':None,'ETA_seconds':None,'scoring':'pending; no inference or scoring'}
    if (root/'control.sqlite').exists():
        c=sqlite3.connect('file:'+str(root/'control.sqlite')+'?mode=ro',uri=True);c.row_factory=sqlite3.Row;m=meta(c);result.update(m)
        result['tranches_done']=c.execute("SELECT COUNT(*) FROM tranches WHERE status='verified'").fetchone()[0]
        result['families_done']=c.execute("SELECT COUNT(*) FROM families WHERE status='done'").fetchone()[0]
        result['families_total']=c.execute('SELECT COUNT(*) FROM families').fetchone()[0]
        result['listed_versions']=c.execute('SELECT COUNT(*) FROM targets').fetchone()[0]
        result['retrieved_versions']=c.execute("SELECT COUNT(*) FROM targets WHERE state='retrieved'").fetchone()[0]
        result['archive_route_versions']=c.execute("SELECT COUNT(*) FROM targets WHERE route='archive_route' AND state='retrieved'").fetchone()[0]
        result['not_retrievable_by_attempted_routes']=c.execute("SELECT COUNT(*) FROM targets WHERE state='not_retrievable'").fetchone()[0]
        result['partial']=c.execute("SELECT COUNT(*) FROM targets WHERE parse_status='partial'").fetchone()[0]
        result['parse_failed']=c.execute("SELECT COUNT(*) FROM targets WHERE parse_status='extraction_failed'").fetchone()[0]
        result['history_failed_families']=c.execute("SELECT COUNT(*) FROM families WHERE history_failure IS NOT NULL").fetchone()[0]
        result['history_unstable']=c.execute("SELECT COUNT(*) FROM families WHERE status='history_unstable'").fetchone()[0]
        result['history_identity_conflict']=c.execute("SELECT COUNT(*) FROM families WHERE status='history_identity_conflict'").fetchone()[0]
        result['fetch_failed']=c.execute("SELECT COUNT(*) FROM families WHERE status='fetch_failed'").fetchone()[0]
        cols={x[1] for x in c.execute('PRAGMA table_info(families)')}
        fields='setid,ordinal,tranche,status,history_failure'+(',attempt_round,error_type' if 'attempt_round' in cols else '')
        result['family_failures']=[dict(x) for x in c.execute('SELECT '+fields+" FROM families WHERE status IN ('history_unstable','fetch_failed','history_identity_conflict') ORDER BY ordinal")]
        result['exports']=[dict(x) for x in c.execute('SELECT * FROM exports')];c.close()
    if (root/'network/ledger.sqlite').exists():
        c=sqlite3.connect('file:'+str(root/'network/ledger.sqlite')+'?mode=ro',uri=True)
        row=c.execute('SELECT COUNT(*),COALESCE(SUM(bytes),0),MIN(start_epoch),MAX(start_epoch) FROM http').fetchone();result['requests']=row[0]+config.get('prior_requests',0);result['bytes']=row[1]
        if result.get('families_done') and result.get('families_total'):
            mean=result['listed_versions']/max(1,result['families_done']);remaining=(result['families_total']-result['families_done'])*(2+mean)
            starts=[x[0] for x in c.execute('SELECT start_epoch FROM http ORDER BY id DESC LIMIT 201')][::-1]
            rate=max(2.05,statistics.median([b-a for a,b in zip(starts,starts[1:])])) if len(starts)>1 else 2.05
            result['ETA_seconds']=int(remaining*rate);result['ETA_assumption']='Approximate observed listed versions/family and median recent HTTP-start spacing; cache reuse, archive fallback, failures and parsing affect it'
        result['archive_HTTP_requests']=c.execute('SELECT COUNT(*) FROM http WHERE archive_request=1').fetchone()[0];c.close()
    pace=root/'network/pacing.json'
    if pace.exists():
        result['pacing']=json.loads(pace.read_bytes())
        nc=sqlite3.connect('file:'+str(root/'network/ledger.sqlite')+'?mode=ro',uri=True)
        now=time.time();active=result['pacing'].get('measurement_epoch',result['pacing'].get('active_epoch',result['pacing']['started_epoch']));starts=[r[0] for r in nc.execute('SELECT start_epoch FROM http WHERE start_epoch>? ORDER BY start_epoch',(max(now-600,active),))]
        result['requests_per_second_10s']=sum(x>now-10 for x in starts)/10
        duration=min(600,max(1,now-active))
        result['requests_per_second_observed']=len(starts)/duration
        observed_start=result['pacing']['started_epoch']
        result['adaptive_requests']=nc.execute('SELECT COUNT(*) FROM http WHERE spacing_seconds IS NOT NULL').fetchone()[0]
        nc.close()
        recent_rate=result['requests_per_second_observed']
        if recent_rate>0 and result.get('families_done') and result.get('families_total'):
            per_family=result['listed_versions']/max(1,result['families_done'])+2
            result['ETA_seconds']=int((result['families_total']-result['families_done'])*per_family/recent_rate)
            result['ETA_assumption']='Observed last ten-minute actual start throughput (includes idle/parse/checkpoint time) and observed versions/family plus two history requests; pending parses and future holds can increase it'
    pool=root/'run/parse-pool.json'
    if pool.exists():result['parse_pool']=json.loads(pool.read_bytes())
    result['partial_rate']=result['partial']/result['retrieved_versions'] if result['retrieved_versions'] else None
    for key in ['launcher','worker','supervisor','caffeinate']:
        f=root/'run'/f'{key}.pid'
        if f.exists():
            pid=int(f.read_text());result[key+'_pid']=pid
            fragment={'worker':'gitrx_services.release1_job','supervisor':'gitrx_services.release1_supervisor','caffeinate':'caffeinate','launcher':'release1'}[key]
            result[key+'_alive']=bool(process_info(pid,fragment))
    hb=root/'run/heartbeat.json'
    if hb.exists():
        beat=json.loads(hb.read_bytes());result['heartbeat']=beat.get('at');result['heartbeat_exited']=beat.get('exited',False)
        result['heartbeat_worker_pid']=beat.get('worker_pid')
        result['heartbeat_age_seconds']=max(0,(datetime.now(timezone.utc)-datetime.fromisoformat(beat['at'])).total_seconds())
        result['heartbeat_status']='STALE' if result['heartbeat_age_seconds']>300 else 'EXITED' if beat.get('exited') or not result.get('worker_alive') else 'WAITING' if beat.get('worker_pid')!=result.get('worker_pid') else 'FRESH'
    supervisor=root/'run/supervisor.json'
    if supervisor.exists():result['supervisor']=json.loads(supervisor.read_bytes())
    return result

def status_markdown(s):
    eta=('%.1f hours'%(s['ETA_seconds']/3600)) if s.get('ETA_seconds') is not None else 'pending scope/observed throughput'
    rate=('%.2f%%'%(100*s['partial_rate'])) if s['partial_rate'] is not None else 'not yet parsed'
    pace=s.get('pacing',{});pool=s.get('parse_pool',{})
    pacing_line=f"Pace: {pace.get('spacing_seconds','legacy')}s; backoff events: {pace.get('events',0)}; hold-until epoch: {pace.get('hold_until',0)}; permanent1s: {pace.get('permanent_one_second',False)}; measured {s.get('requests_per_second_observed',0):.3f} requests/s (observed <=10min), {s.get('requests_per_second_10s',0):.2f} requests/s (last10s)."
    pool_line=f"Parse pool: {pool.get('workers',0)} processes; pending {pool.get('pending',0)}/{pool.get('queue_limit',0)}; memory budget8GiB; worker PIDs {pool.get('worker_pids',[])}."
    return f"""Release1 status — {s['at']}

Phase: **{s['phase']}**; stop reason: **{s.get('stop_reason') or 'none'}**.
Tranches verified: **{s['tranches_done']}**; families: **{s['families_done']}/{s.get('families_total','pending scope')}**.
Version targets (history-listed plus explicit listing-only) / retrieved / archive-route / not retrievable by attempted routes: **{s['listed_versions']}/{s['retrieved_versions']}/{s['archive_route_versions']}/{s['not_retrievable_by_attempted_routes']}**.
Partials: **{s['partial']} ({rate})**; parse failures: **{s['parse_failed']}**.
Requests: **{s['requests']}/120000** (includes6 prior policy attempts); response bytes: **{s['bytes']}**.
Free target volume: **{s['free_GiB']:.2f}GiB**; floor20GiB. ETA: **{eta}**, estimate not a completion promise.
Supervisor PID: {s.get('supervisor_pid','not installed')}; worker PID: {s.get('worker_pid','pending')}; caffeinate PID: {s.get('caffeinate_pid','pending')}.
Heartbeat: **{s.get('heartbeat_status','pending')}**, {s.get('heartbeat','pending')} (age {s.get('heartbeat_age_seconds','unknown')} seconds).
Family failures: history_unstable **{s.get('history_unstable',0)}**, fetch_failed **{s.get('fetch_failed',0)}**, history_identity_conflict **{s.get('history_identity_conflict',0)}**; retry round {s.get('current_retry_round',0)}.
Data: `{s['data_root']}`. No context/confidence/anchor output is computed or displayed; scoring/publication pending.
Check: `./release1-status`; resume: `./release1-resume`; stop and disable: `./release1-stop`.
Family failure list: {json.dumps(s.get('family_failures',[]),ensure_ascii=False)}
{pacing_line}
{pool_line}
"""

class Runner:
    def __init__(self,config):
        self.config=config;self.root=Path(config['data_root']);self.root.mkdir(parents=True,exist_ok=True);(self.root/'run').mkdir(exist_ok=True)
        self.lock=(self.root/'run/worker.lock').open('a+')
        try:fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Another release1 worker owns the lock')
        self.c=control(self.root);self.t=Transport(self.root,config);self.t.recover_interrupted();self.closed=threading.Event();self.status_lock=threading.Lock();self.export_process=None
        self.protection=filter_inventory(json.loads((REPORT/'protected-baseline.json').read_bytes())['hashes']);self.protection_lock=threading.Lock()
        self.fingerprints={p:(Path(PROJECT/p).stat().st_size,Path(PROJECT/p).stat().st_mtime_ns) for p in self.protection if Path(PROJECT/p).is_file()}
        (self.root/'run/worker.pid').write_text(str(os.getpid())+'\n')
        setmeta(self.c,phase='initializing',stop_reason=None,worker_started_at=utc(),worker_pid=os.getpid())
        from .release1_parse_pool import ParsePool
        self.parse_pool=ParsePool(self);self.t.on_idle=self.parse_pool.drain
        if 'active_epoch' not in self.t.pacer.s:
            self.t.pacer.s['active_epoch']=time.time();self.t.pacer.s['clean']=0;self.t.pacer.s['latencies']=[]
            self.t.pacer.decision('adaptive_worker_activation',time.time(),spacing_seconds=self.t.pacer.s['spacing_seconds'])
        self.t.pacer.s['measurement_epoch']=time.time();self.t.pacer.save()
        self.heartbeat=threading.Thread(target=self.beat,daemon=True);self.heartbeat.start()
    def beat(self):
        while not self.closed.is_set():
            try:
                with self.protection_lock:pins=list(self.protection.items())
                for p,want in pins:
                    if exclusion_pattern(p) is not None:continue
                    path=PROJECT/p
                    try:stat=path.stat();fingerprint=(stat.st_size,stat.st_mtime_ns)
                    except FileNotFoundError:raise Stop('PROTECTED_HASH_CHANGED:'+p)
                    if self.fingerprints.get(p)!=fingerprint:
                        if hash_file(path)!=want:raise Stop('PROTECTED_HASH_CHANGED:'+p)
                        self.fingerprints[p]=fingerprint
                with self.status_lock:
                    atomic(self.root/'run/heartbeat.json',{'at':utc(),'worker_pid':os.getpid()})
                    s=status(self.config);atomic(REPORT/'STATUS.json',s);(REPORT/'STATUS.md').write_text(status_markdown(s))
            except Exception as exc:
                if isinstance(exc,Stop):atomic(self.root/'fatal-stop.json',{'at':utc(),'reason':str(exc)})
                print(json.dumps({'phase':'heartbeat_error','type':type(exc).__name__}),flush=True)
            self.closed.wait(30)
    def update(self,**fields):
        setmeta(self.c,**fields)
        print(json.dumps({'at':utc(),**fields}),flush=True)
    def import_cache(self):
        # Existing evidence stays in place; imported responses keep original timestamps/headers.
        for base in [PROJECT/'cohorts/census-e4',PROJECT/'cohorts/tranche1/net']:
            db=sqlite3.connect('file:'+str(base/'ledger.sqlite')+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
            for row in db.execute('SELECT * FROM cache WHERE status=200'):
                q=parse_qs(urlsplit(row['url']).query)
                if not ('getFile.cfm' in row['url'] and 'version' in q) and '/history.json' not in row['url']:continue
                key=canonical(row['url'])
                if self.t.c.execute('SELECT 1 FROM imports WHERE url=?',(key,)).fetchone():continue
                h=db.execute('SELECT * FROM http WHERE url=? AND status=200 ORDER BY id LIMIT 1',(row['url'],)).fetchone();mf=base/'transport'/f"{h['id']}.json";m=json.loads(mf.read_bytes()) if mf.exists() else {}
                headers=m.get('headers',{});headers=list(headers.items()) if isinstance(headers,dict) else headers
                self.t.import_observation(row['url'],base/'objects'/row['sha256'],row['sha256'],200,{'retrieved_at':m.get('started_at',h['started_at']),'headers':headers,'final_url':row['url'],'error':None,'source_namespace':str(base),'source_http_id':h['id']})
            db.close()
        p=readonly(PROJECT/'data/current/gitrx.sqlite')
        for row in p.execute("SELECT r.*,a.path FROM retrievals r JOIN artifacts a ON a.sha256=r.artifact_sha256 WHERE r.status=200 AND r.state='available' AND r.kind IN ('label','archive_route')"):
            q=parse_qs(urlsplit(row['url']).query)
            if row['kind']=='label' and 'version' not in q:continue
            if self.t.c.execute('SELECT 1 FROM imports WHERE url=?',(canonical(row['url']),)).fetchone():continue
            self.t.import_observation(row['url'],PROJECT/'runtime/current'/row['path'],row['artifact_sha256'],200,{'retrieved_at':row['retrieved_at'],'headers':json.loads(row['headers_json']),'final_url':row['final_url'],'error':None,'source_namespace':'pilot','source_retrieval_id':row['id']})
        p.close()
    def init_tranche(self,n,retry_round=0):
        from gitrx.current.db import connect
        from gitrx.current.content_storage import migrate
        from gitrx.current.parser import load_registry
        base=self.root if retry_round==0 else self.root/'retries'/f'round-{retry_round:02d}'
        root=base/'tranches'/f'{n:04d}';guard(root,2*1024**3);(root/'data').mkdir(parents=True,exist_ok=True);(root/'reports').mkdir(exist_ok=True)
        if not (root/'research').exists():(root/'research').symlink_to(PROJECT/'research',target_is_directory=True)
        raw=connect(root/'data/gitrx.sqlite')
        if not raw.execute("SELECT 1 FROM sqlite_master WHERE name='content_store_meta'").fetchone():migrate(raw)
        c=GuardDB(raw,root)
        c.executescript('''CREATE TABLE IF NOT EXISTS tranche_publication_observations(setid TEXT,version INTEGER,ordinal INTEGER,publication_raw TEXT,publication_date TEXT,PRIMARY KEY(setid,version,ordinal)); CREATE TABLE IF NOT EXISTS release1_parse_selection(version_id INTEGER PRIMARY KEY,parse_id INTEGER); CREATE TABLE IF NOT EXISTS release1_http_provenance(retrieval_id INTEGER PRIMARY KEY,provenance_json TEXT);''')
        c.execute('INSERT OR IGNORE INTO ingest_runs(id,started_at) VALUES(1,?)',(utc(),));c.commit();registry=load_registry(c,root)
        from gitrx.current.archive import Fetcher
        self.active_registry=registry
        return root,c,registry,Fetcher(c,root,1)
    def record(self,c,root,fetcher,response,kind,vid=None):
        sha=response['sha256'];dest=root/'artifacts/sha256'/sha[:2]/sha
        link_verified(response['path'],dest,sha,self.root)
        headers=response.get('headers')
        if headers is None:headers=json.loads(response.get('headers_json') or '[]')
        record={'url':response['url'],'final_url':response.get('final_url',response['url']),'retrieved_at':response.get('retrieved_at',response.get('started_at',utc())),'status':response['status'],'headers':headers,'error':response.get('error'),'attempt':response.get('attempt',1)}
        key='release1:'+kind+':'+str(vid)+':'+hashlib.sha256(json.dumps(record,sort_keys=True).encode()).hexdigest()
        rid=fetcher.record(record,Path(response['path']).read_bytes(),kind,vid,key)
        c.execute('INSERT OR IGNORE INTO release1_http_provenance VALUES(?,?)',(rid,json.dumps({k:response.get(k) for k in ['provenance','id','source_namespace','source_http_id','source_retrieval_id','path','sha256']})));c.commit();return rid
    def history(self,c,root,fetcher,sid,latest):
        from .release1_history import fetch_into_store
        return fetch_into_store(self,c,root,fetcher,sid,latest)
    def admit(self,c,root,fetcher,response,sid,number,kind,listing=None):
        from gitrx.current.archive import validate_label
        vid=c.execute('SELECT id FROM versions WHERE setid=? AND version=?',(sid,number)).fetchone()[0]
        rid=self.record(c,root,fetcher,response,kind,vid)
        if response['status']!=200:return False
        val=validate_label(c,root,rid,sid,number)
        if val['state']=='identity_mismatch':raise Stop('XML_IDENTITY_MISMATCH')
        if val['state']!='downloaded':return False
        prior=c.execute('SELECT selected_sha256 FROM versions WHERE id=?',(vid,)).fetchone()[0]
        known=self.c.execute('SELECT raw_sha256 FROM targets WHERE setid=? AND version=?',(sid,number)).fetchone()
        if known and known[0] and known[0]!=response['sha256']:raise Stop('CONFLICTING_IDENTITY_BYTES')
        if prior and prior!=response['sha256']:raise Stop('CONFLICTING_IDENTITY_BYTES')
        c.execute("UPDATE versions SET state='downloaded',selected_sha256=?,xml_member=?,effective_date=? WHERE id=?",(response['sha256'],val['xml_member'],val['effective_date'],vid))
        if listing:
            listing_id=self.record(c,root,fetcher,listing,'archive_listing');aid=parse_qs(urlsplit(response['url']).query).get('archive_id',[''])[0]
            c.execute('INSERT OR IGNORE INTO archive_route_sources VALUES(?,?,?,?,?,?)',(rid,aid,listing_id,sid,number,json.dumps({'identity_basis':'internal XML setid/version only','listing_version_used_as_identity':False})))
        c.commit()
        with self.c:self.c.execute("UPDATE targets SET state='retrieved',route=?,raw_sha256=? WHERE setid=? AND version=?",(kind,response['sha256'],sid,number))
        if hasattr(self,'parse_pool'):
            self.parse_pool.submit(c,root,c.execute('SELECT * FROM versions WHERE id=?',(vid,)).fetchone(),self.active_registry)
        return True
    def archive_family(self,c,root,fetcher,sid,wanted):
        url='https://dailymed.nlm.nih.gov/dailymed/archives/index.cfm?'+urlencode({'query':sid,'pagesize':100,'page':1});pages=set();links=set();needed=set(wanted)
        while url and url not in pages and needed:
            pages.add(url);listing=self.t.get(url,cache=getattr(self,'retry_round',0)==0)
            if listing['status']!=200:break
            self.record(c,root,fetcher,listing,'archive_listing');doc=html.fromstring(Path(listing['path']).read_bytes())
            for href in doc.xpath('//a[contains(@href,"getArchivalFile.cfm")]/@href'):
                candidate=urljoin(url,href)
                if candidate in links:continue
                links.add(candidate)
                old=self.t.cached(candidate) if getattr(self,'retry_round',0) else None
                response=self.t.get(candidate,cache=not getattr(self,'retry_round',0) or bool(old and old['status']==200))
                if response['status']!=200:continue
                ident=xml_identity(response['path'])
                if ident is None:continue
                if ident[0]!=sid:raise Stop('ARCHIVE_FAMILY_IDENTITY_MISMATCH')
                if ident[1] in needed and self.admit(c,root,fetcher,response,sid,ident[1],'archive_route',listing):needed.remove(ident[1])
            current=int(parse_qs(urlsplit(url).query).get('page',['1'])[0]);nexts=[]
            for href in doc.xpath('//a[@href]/@href'):
                link=urljoin(url,href);q=parse_qs(urlsplit(link).query)
                if q.get('query')==[sid] and q.get('page',[''])[0].isdigit() and int(q['page'][0])>current:nexts.append((int(q['page'][0]),link))
            url=min(nexts)[1] if nexts else None
        for number in needed:
            c.execute("UPDATE versions SET state='unavailable' WHERE setid=? AND version=?",(sid,number))
            with self.c:self.c.execute("UPDATE targets SET state='not_retrievable' WHERE setid=? AND version=?",(sid,number))
        c.commit()
    def family(self,n,scope,sid,c,root,registry,fetcher):
        from gitrx.current.parser import persist_document
        from .release1_history import flag_publication_inversions
        item=scope['records'][sid];c.execute('INSERT OR IGNORE INTO families VALUES(?,?,?)',(sid,item['title'],json.dumps(item)));c.commit()
        self.update(phase='backfill',current_tranche=n,current_retry_round=getattr(self,'retry_round',0),current_family_ordinal=self.c.execute('SELECT ordinal FROM families WHERE setid=?',(sid,)).fetchone()[0])
        error=self.history(c,root,fetcher,sid,max(item['latest_versions_seen']))
        if error:
            with self.c:self.c.execute('UPDATE families SET status=?,history_failure=?,error_type=? WHERE setid=?',(error,error,error,sid))
            return
        missing=[]
        selected="SELECT v.* FROM versions v WHERE v.setid=? AND (EXISTS(SELECT 1 FROM release1_current_history h WHERE h.setid=v.setid AND h.version=v.version) OR EXISTS(SELECT 1 FROM release1_listing_only_targets l WHERE l.setid=v.setid AND l.version=v.version))"
        for v in c.execute(selected+' ORDER BY v.version',(sid,)).fetchall():
            with self.c:self.c.execute('INSERT OR IGNORE INTO targets(setid,version,tranche) VALUES(?,?,?)',(sid,v['version'],n))
            with self.c:self.c.execute('UPDATE targets SET source_root=?,attempt_round=? WHERE setid=? AND version=?',(str(root),getattr(self,'retry_round',0),sid,v['version']))
            if v['state']=='downloaded':
                if hash_file(root/c.execute('SELECT path FROM artifacts WHERE sha256=?',(v['selected_sha256'],)).fetchone()[0])!=v['selected_sha256']:raise Stop('PROTECTED_SELECTED_HASH_CHANGED')
                recovered=c.execute("SELECT kind FROM retrievals WHERE version_id=? AND artifact_sha256=? AND status=200 AND state='available' ORDER BY id LIMIT 1",(v['id'],v['selected_sha256'])).fetchone()
                with self.c:self.c.execute("UPDATE targets SET state='retrieved',route=COALESCE(route,?),raw_sha256=? WHERE setid=? AND version=?",(recovered[0] if recovered else 'recorded_route',v['selected_sha256'],sid,v['version']))
                self.parse_pool.submit(c,root,v,registry)
                continue
            url='https://dailymed.nlm.nih.gov/dailymed/getFile.cfm?'+urlencode({'type':'zip','setid':sid,'version':v['version']})
            old=self.t.cached(url) if getattr(self,'retry_round',0) else None
            response=self.t.get(url,cache=not getattr(self,'retry_round',0) or bool(old and old['status']==200))
            if not self.admit(c,root,fetcher,response,sid,v['version'],'getFile_zip'):missing.append(v['version'])
        if missing:self.archive_family(c,root,fetcher,sid,missing)
        flag_publication_inversions(c,sid)
        for v in c.execute(selected+" AND v.state='downloaded' ORDER BY v.version",(sid,)).fetchall():
            if (self.root/'fatal-stop.json').exists():raise Stop(json.loads((self.root/'fatal-stop.json').read_bytes())['reason'])
            self.parse_pool.submit(c,root,v,registry)
        self.parse_pool.mark_fetched(c,root,sid)
        self.parse_pool.drain()
    def pool_commit(self,c,root,v,registry,prepared):
        from .release1_parse_pool import persist_prepared
        guard(root,2*1024**3);pid=persist_prepared(c,root,v,registry,prepared)
        p=c.execute('SELECT status FROM parse_runs WHERE id=?',(pid,)).fetchone()
        c.execute('INSERT OR IGNORE INTO release1_parse_selection VALUES(?,?)',(v['id'],pid));c.commit()
        with self.c:self.c.execute('UPDATE targets SET parse_status=?,parse_id=? WHERE setid=? AND version=?',(p['status'],pid,v['setid'],v['version']))
    def pool_failed(self,c,v,exception_type):
        with self.c:self.c.execute("UPDATE targets SET parse_status='extraction_failed' WHERE setid=? AND version=?",(v['setid'],v['version']))
        self.update(parse_worker_exception_type=exception_type)
    def finish_family(self,c,sid):
        failed=self.c.execute("SELECT 1 FROM targets WHERE setid=? AND (state!='retrieved' OR parse_status IS NULL OR parse_status='extraction_failed')",(sid,)).fetchone()
        with self.c:
            self.c.execute("UPDATE families SET status=?,history_failure=?,error_type=? WHERE setid=?",('fetch_failed' if failed else 'done','not retrievable by attempted routes or parse failed' if failed else None,'fetch_failed' if failed else None,sid))
            self.c.execute('INSERT INTO family_events(setid,round,status,error_type,at) VALUES(?,?,?,?,?)',(sid,getattr(self,'retry_round',0),'fetch_failed' if failed else 'done','fetch_failed' if failed else None,utc()))
    def process_family(self,n,scope,sid,c,root,registry,fetcher,retry_round):
        self.retry_round=retry_round
        if hasattr(self,'parse_pool'):self.parse_pool.drain()
        with self.c:self.c.execute("UPDATE families SET status='in_progress',attempt_round=?,attempts=attempts+1,source_root=?,error_type=NULL WHERE setid=?",(retry_round,str(root),sid))
        try:self.family(n,scope,sid,c,root,registry,fetcher)
        except Stop:raise
        except Exception as exc:
            if any(x in str(exc).lower() for x in ['hash mismatch','hash changed','cas integrity','cas collision','identity mismatch']):raise Stop('SCIENTIFIC_INTEGRITY:'+str(exc)) from exc
            c.rollback()
            with self.c:self.c.execute("UPDATE families SET status='fetch_failed',history_failure=?,error_type=? WHERE setid=?",('per-family operational failure: '+type(exc).__name__,type(exc).__name__,sid))
            self.update(phase='backfill',last_family_error={'setid':sid,'round':retry_round,'type':type(exc).__name__})
        result=self.c.execute('SELECT status,error_type FROM families WHERE setid=?',(sid,)).fetchone()
        with self.c:self.c.execute('INSERT INTO family_events(setid,round,status,error_type,at) VALUES(?,?,?,?,?)',(sid,retry_round,result['status'],result['error_type'],utc()))
    def verify_tranche(self,n,c,root,retry_round=0):
        for v in c.execute("SELECT * FROM versions WHERE state='downloaded'"):
            path=root/c.execute('SELECT path FROM artifacts WHERE sha256=?',(v['selected_sha256'],)).fetchone()[0]
            if hash_file(path)!=v['selected_sha256'] or xml_identity(path)!=(v['setid'],v['version']):raise Stop('TRANCHE_IDENTITY_OR_HASH_MISMATCH')
        for a in c.execute("SELECT * FROM artifacts WHERE artifact_class!='derived'"):
            if hash_file(root/a['path'])!=a['sha256']:raise Stop('TRANCHE_ARTIFACT_HASH_CHANGED')
        if c.execute('SELECT COUNT(*) FROM identity_conflicts').fetchone()[0]:raise Stop('XML_IDENTITY_MISMATCH')
        from gitrx.current.content_storage import verify
        checks=verify(c)
        if checks!={'content_hash_failures':0,'dangling_content_references':0,'foreign_key_violations':0,'integrity':'ok'}:raise Stop('TRANCHE_CONTENT_OR_DATABASE_INTEGRITY')
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='section_contexts'").fetchone() and c.execute('SELECT COUNT(*) FROM section_contexts').fetchone()[0]:raise RuntimeError('Unexpected classification rows; no output inspected')
        protected_check()
        history_failed=self.c.execute('SELECT COUNT(*) FROM families WHERE tranche=? AND history_failure IS NOT NULL',(n,)).fetchone()[0]
        c.execute('UPDATE ingest_runs SET histories_complete=?,completed_at=? WHERE id=1',(int(not history_failed),utc()));c.commit()
        versions=[]
        for v in c.execute('SELECT * FROM versions ORDER BY setid,version'):
            row=dict(v)
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='release1_listing_only_targets'").fetchone():
                basis=c.execute('SELECT provenance_json FROM release1_listing_only_targets WHERE setid=? AND version=?',(v['setid'],v['version'])).fetchone()
                if basis:row['listing_only_target_provenance']=json.loads(basis[0])
            row['publication_observations']=[{'published_raw':x['publication_raw'],'published_date':x['publication_date']} for x in c.execute('SELECT * FROM tranche_publication_observations WHERE setid=? AND version=? ORDER BY ordinal',(v['setid'],v['version']))]
            row['retrievals']=[dict(x) for x in c.execute('SELECT * FROM retrievals WHERE version_id=? ORDER BY id',(v['id'],))]
            row['parse_runs']=[dict(x) for x in c.execute('SELECT p.* FROM release1_parse_selection s JOIN parse_runs p ON p.id=s.parse_id WHERE s.version_id=?',(v['id'],))];versions.append(row)
        from gitrx.current.db import dumps
        text=dumps({'format':'release1-source-evidence-1','cohort':'release1','tranche':n,'versions':versions});snapshot=hashlib.sha256(text.encode()).hexdigest()
        c.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?,?)',(snapshot,utc(),text));c.commit();atomic(root/'reports/SNAPSHOT.json',{'snapshot':snapshot})
        c.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchall();dbsha=hash_file(root/'data/gitrx.sqlite')
        counts={'families':c.execute('SELECT COUNT(*) FROM families').fetchone()[0],'listed':len(versions),'retrieved':sum(v['state']=='downloaded' for v in versions),'archive_route':self.c.execute("SELECT COUNT(*) FROM targets WHERE tranche=? AND route='archive_route' AND state='retrieved'",(n,)).fetchone()[0],'not_retrievable_by_attempted_routes':sum(v['state']!='downloaded' for v in versions),'partial':sum(p['status']=='partial' for v in versions for p in v['parse_runs']),'parse_failed':sum(p['status']=='extraction_failed' for v in versions for p in v['parse_runs']),'identity_mismatches':0,'free_GiB':shutil.disk_usage(root).free/1024**3,'request_total':self.t.count(),'bytes':self.t.c.execute('SELECT COALESCE(SUM(bytes),0) FROM http').fetchone()[0]}
        counts['database_sha256']=dbsha;counts['history_failed_families']=history_failed
        reportname=f'tranche-{n:04d}.json' if retry_round==0 else f'retry-{retry_round}-tranche-{n:04d}.json'
        atomic(REPORT/reportname,{'at':utc(),'number':n,'retry_round':retry_round,'counts':counts,'snapshot_id':snapshot,'data_root':str(root),'protected_paths_verified':len(json.loads((REPORT/'protected-baseline.json').read_bytes())['hashes']),'classification_invoked':False})
        with self.c:
            if retry_round==0:self.c.execute("INSERT OR REPLACE INTO tranches VALUES(?,'verified',?,?,?)",(n,utc(),snapshot,json.dumps(counts)))
            else:self.c.execute("INSERT OR REPLACE INTO retry_tranches VALUES(?,?,?,'verified',?,?)",(retry_round,n,str(root),snapshot,json.dumps(counts)))
        if hasattr(self,'protection_lock'):
            with self.protection_lock:self.protection[str((root/'data/gitrx.sqlite').resolve())]=dbsha
        self.update(phase='tranche_verified',last_verified_tranche=n)
        return counts
    def exports(self,completed,total):
        if self.export_process is not None and self.export_process.poll() is None:return
        checkpoints=[('first1000',2),('25pct',max(2,math.ceil(.25*total/self.config['tranche_size']))),('50pct',max(2,math.ceil(.50*total/self.config['tranche_size'])))]
        for label,threshold in checkpoints:
            row=self.c.execute('SELECT * FROM exports WHERE checkpoint=?',(label,)).fetchone()
            if completed<threshold or (row and row['status'] in ('complete','needs_review')):continue
            if row and row['status']=='running' and row['pid']:
                try:os.kill(row['pid'],0);continue
                except ProcessLookupError:pass
            key='export_attempts_'+label;attempts=meta(self.c).get(key,0)
            if attempts>=3:
                with self.c:self.c.execute("UPDATE exports SET status='needs_review' WHERE checkpoint=?",(label,))
                continue
            setmeta(self.c,**{key:attempts+1})
            cmd=[sys.executable,'-B','-m','gitrx_services.release1_job','--config',self.config.get('_config_path',str(DEFAULT_CONFIG)),'export','--checkpoint',label,'--tranches',str(threshold)]
            log=(self.root/'run'/f'export-{label}.log').open('ab');self.export_process=subprocess.Popen(cmd,cwd=PROJECT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT);log.close()
            with self.c:self.c.execute("INSERT OR REPLACE INTO exports VALUES(?,?,'running',?,NULL)",(label,threshold,self.export_process.pid))
            break
    def run(self):
        outcome={"hard_stop":False,"phase":"crashed_resumable","reason":None}
        try:
            protected_check();self.import_cache()
            from .release1_rounds import run_all
            run_all(self)
            outcome.update(phase=meta(self.c).get('phase','complete'))
        except BaseException as exc:
            reason=str(exc) if isinstance(exc,Stop) else type(exc).__name__+': '+str(exc)
            outcome.update(hard_stop=isinstance(exc,Stop),phase='stopped' if isinstance(exc,Stop) else 'crashed_resumable',reason=reason)
            self.update(phase=outcome['phase'],stop_reason=reason)
            atomic(self.root/'run'/('stop-'+str(time.time_ns())+'.json'),{'at':utc(),'type':type(exc).__name__,'reason':reason})
            if not isinstance(exc,Stop):traceback.print_exc()
            raise
        finally:
            if hasattr(self,'parse_pool'):self.parse_pool.close(interrupted=outcome['phase'] in ('stopped','crashed_resumable'))
            self.t.opener.close()
            self.closed.set();self.heartbeat.join(timeout=35)
            with self.status_lock:
                atomic(self.root/'run/heartbeat.json',{'at':utc(),'worker_pid':os.getpid(),'exited':True})
                atomic(self.root/'run'/f'worker-exit-{os.getpid()}.json',{'at':utc(),**outcome})
                current=status(self.config);atomic(REPORT/'STATUS.json',current);(REPORT/'STATUS.md').write_text(status_markdown(current))
            self.c.close();self.t.c.close();fcntl.flock(self.lock,fcntl.LOCK_UN)

def export_checkpoint(config,label,tranches):
    from .federation import Federation,size
    from gitrx.current.export_git import export_git
    root=Path(config['data_root']);c=control(root)
    try:
        sources=[]
        for row in c.execute("SELECT * FROM tranches WHERE status='verified' AND number<=? ORDER BY number",(tranches,)):
            want=json.loads(row['counts_json']).get('database_sha256')
            if want and hash_file(root/'tranches'/f"{row['number']:04d}"/'data/gitrx.sqlite')!=want:raise Stop('VERIFIED_TRANCHE_DB_HASH_CHANGED')
            sources.append((f"release1-{row['number']:04d}",root/'tranches'/f"{row['number']:04d}",row['snapshot_id']))
        if len(sources)!=tranches:raise RuntimeError('Export checkpoint is not fully verified')
        class Bounded(Federation):
            def execute(self,*a,**k):guard(root,1024**3);return super().execute(*a,**k)
        f=Bounded(sources);results=[]
        for suffix in ['a','b']:
            base=root/'exports'/(label+'-'+suffix);attempt=0;repo=base
            while repo.exists():
                tag='release1-'+label
                check=subprocess.run(['git','-C',str(repo),'rev-parse','refs/tags/'+tag],capture_output=True,text=True)
                if check.returncode==0:break
                attempt+=1;repo=base.with_name(base.name+'-attempt'+str(attempt))
            start=time.monotonic()
            if repo.exists():
                head=check.stdout.strip();tree=subprocess.check_output(['git','-C',str(repo),'rev-parse',head+'^{tree}'],text=True).strip();result={'repo':str(repo),'head':head,'tree':tree,'snapshot':f.snapshot,'resumed_complete':True}
            else:result=export_git(f,PROJECT,repo,f.snapshot,'release1-'+label)
            result['elapsed_seconds']=time.monotonic()-start;result['repository_bytes']=size(repo);results.append(result)
        if results[0]['head']!=results[1]['head'] or results[0]['tree']!=results[1]['tree']:raise Stop('CONFLICTING_EXPORT_HASHES')
        families=sum(json.loads(row['counts_json'])['families'] for row in c.execute("SELECT * FROM tranches WHERE status='verified' AND number<=?",(tranches,)))
        result={'at':utc(),'checkpoint':label,'verified_tranches':tranches,'families':families,'versions':len(json.loads(f.raw)['versions']),'snapshot':f.snapshot,'exports':results,'identical_hashes':True,'classification_exported':False}
        atomic(REPORT/f'EXPORT-{label}.json',result)
        reports=[json.loads(p.read_bytes()) for p in sorted(REPORT.glob('EXPORT-*.json'))]
        lines=['Deterministic release1 exports of completed verified tranches only. No contexts/confidence/anchors in exported content.','', '| Checkpoint | Families | Versions | A seconds / bytes | B seconds / bytes | Hashes match |','|---|---:|---:|---:|---:|---|']
        for x in reports:
            a,b=x['exports'];lines.append(f"| {x['checkpoint']} | {x['families']} | {x['versions']} | {a['elapsed_seconds']:.2f} / {a['repository_bytes']} | {b['elapsed_seconds']:.2f} / {b['repository_bytes']} | {x['identical_hashes']} |")
        (REPORT/'EXPORT-AT-SCALE.md').write_text('\n'.join(lines)+'\n')
        with c:c.execute("UPDATE exports SET status='complete',error_type=NULL WHERE checkpoint=?",(label,))
    except BaseException as exc:
        with c:c.execute("UPDATE exports SET status='failed',error_type=? WHERE checkpoint=?",(type(exc).__name__,label))
        if isinstance(exc,Stop) or 'STOP_RAW_HASH' in str(exc) or 'Evidence snapshot integrity' in str(exc):atomic(root/'fatal-stop.json',{'at':utc(),'reason':str(exc)})
        raise
    finally:c.close()

def launch(config):
    from .release1_supervisor import launch as supervised_launch
    return supervised_launch(config)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,default=DEFAULT_CONFIG);sub=p.add_subparsers(dest='command',required=True)
    sub.add_parser('status');sub.add_parser('resume');sub.add_parser('run');e=sub.add_parser('export');e.add_argument('--checkpoint',required=True);e.add_argument('--tranches',type=int,required=True)
    a=p.parse_args();config=json.loads(a.config.read_bytes());config['_config_path']=str(a.config.resolve())
    if a.command=='status':print(status_markdown(status(config)))
    elif a.command=='resume':launch(config)
    elif a.command=='export':export_checkpoint(config,a.checkpoint,a.tranches)
    else:
        def interrupt(*_):raise WorkerInterrupted('worker interrupted; checkpoint retained')
        signal.signal(signal.SIGTERM,interrupt);signal.signal(signal.SIGINT,interrupt)
        Runner(config).run()

if __name__=='__main__':
    try:main()
    except Stop:raise SystemExit(78)
