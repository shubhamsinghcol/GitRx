"""Finite source-preserving DailyMed capacity sample; no release promotion.

Filter totals are observed at acquisition time. Random listed-version ZIP sizes
give a family-weighted expansion with explicit response and scope limitations.
"""
import argparse
import fcntl
import hashlib
import gzip
import json
from pathlib import Path
import random
import sqlite3
import time
import os
from urllib.parse import urlencode
from uuid import UUID

from .census import BASE, Census
from .jobs import HTTP, atomic, sha
from gitrx.current.archive import Fetcher, validate_label, now
from gitrx.current.db import connect
from gitrx.current.parser import persist_document, PARSER_VERSION
from gitrx_services.release1_history import collect
from gitrx_services.export_payloads import query_recipe
from gitrx.current.db import dumps

# FDA Document Type including Content of Labeling Type, checked 2026-11-07.
# Historical human highlights code is retained as a separately counted filter.
TYPES = {'human_rx':'34391-3', 'human_rx_highlights':'45129-4',
         'human_otc':'34390-5', 'animal_rx':'50578-4','animal_otc':'50577-6',
         'bulk_ingredient':'53409-9','vaccine':'53404-0','medical_device':'55439-4'}


def derived_document(c, vid):
    """All persisted narrative rows, normalized only for local IDs/timestamps."""
    result=[]
    def clean(row, excluded):return {k:v for k,v in dict(row).items() if k not in excluded}
    for parse in c.execute('SELECT * FROM parse_runs WHERE version_id=? ORDER BY parser_version,config_json',(vid,)):
        item={'parse':clean(parse,{'id','version_id','created_at'}),'sections':[]}
        for section in c.execute('SELECT * FROM sections WHERE parse_run_id=? ORDER BY ordinal',(parse['id'],)):
            row={'section':clean(section,{'id','parse_run_id','version_id'})}
            for table in ('section_excerpts','narrative_blocks','narrative_tables'):
                row[table]=[clean(x,{'id','section_id'}) for x in c.execute('SELECT * FROM '+table+' WHERE section_id=? ORDER BY ordinal',(section['id'],))]
            item['sections'].append(row)
        result.append(item)
    return result


def sample_indices(total, count, seed, group):
    """Stable simple random sample of listing row positions within each filter."""
    rng = random.Random(str(seed)+':'+group)
    return sorted(rng.sample(range(total), min(total, count)))


def sampled_versions(canonical, seed, sid):
    versions = sorted({int(row[0]) for row in canonical if int(row[0]) > 0})
    return sorted(random.Random(str(seed)+':versions:'+sid).sample(versions, min(2,len(versions))))


def scenarios(groups, samples):
    """Two-stage family/version expansion; failures are never assigned zero bytes."""
    output = {}
    for name, total in groups.items():
        selected = [s for s in samples if name in s.get('groups', [])]
        usable = [s for s in selected if s.get('history_state') == 'accepted'
                  and s.get('zip_results') and all(x.get('state') == 'downloaded' for x in s['zip_results'])]
        products = [s['known_versions'] * sum(x['bytes'] for x in s['zip_results']) / len(s['zip_results'])
                    for s in usable]
        lengths = [s['known_versions'] for s in usable]
        bootstrap=[]
        if len(products)>1:
            rng=random.Random('gitrx-capacity-bootstrap-1:'+name)
            bootstrap=sorted(total*sum(rng.choices(products,k=len(products)))/len(products) for _ in range(2000))
        output[name] = {'observed_listing_rows':total, 'sampled_families':len(selected),
                        'usable_families':len(usable),
                        'unknown_or_failed_families':len(selected)-len(usable),
                        'mean_known_versions':sum(lengths)/len(lengths) if lengths else None,
                        'sampled_version_expansion_bytes':total*sum(products)/len(products) if products else None,
                        'bootstrap_95pct_bytes':[bootstrap[49],bootstrap[1949]] if bootstrap else None,
                        'bootstrap_qualification':'Exploratory family-resampling interval conditional on observed responding sample. It does not cover within-family ZIP sampling uncertainty, nonresponse, unseen extremes, archived-only scope or source changes; not a capacity guarantee.',
                        'sample_family_expansion_max_bytes':max(products) if products else None,
                        'assumptions':'Uniform family listing positions and up to two uniformly selected positive listed versions/family. Expansion assumes responding families represent the filter. Small-sample estimate without a precision claim.',
                        'scope':'Current listing only; archived-only families and unavailable histories excluded from expansion, not assigned zero.'}
        for field in ('native_sqlite_growth_bytes','payload_gzip_bytes'):
            measured=[s['known_versions']*sum(x[field] for x in s['zip_results'])/len(s['zip_results'])
                      for s in usable if all(field in x for x in s['zip_results'])]
            output[name]['expanded_'+field]=total*sum(measured)/len(measured) if measured else None
    return output


class Capacity:
    def __init__(self, config):
        self.config_path = Path(config).absolute()
        raw = self.config_path.read_bytes(); self.config = json.loads(raw)
        if self.config['format'] != 'gitrx-capacity-1':
            raise ValueError('Unsupported capacity configuration')
        self.root = Path(self.config['workspace']).absolute()
        self.volume = Path(self.config['volume']).absolute()
        self.check_volume()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = (self.root/'writer.lock').open('a+')
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pin = self.root/'config-pin.json'
        if pin.exists() and json.loads(pin.read_bytes()) != self.config:
            raise ValueError('Sample configuration changed; use a new workspace')
        if not pin.exists():
            atomic(pin, self.config)
            # Canonical config pin is compared semantically on subsequent runs.
            self.config_path = pin
        elif json.loads(pin.read_bytes()) != self.config:
            raise ValueError('Sample configuration changed')
        # Use maintained ledger and raw source/schema/parser; no alternative parser.
        self.http = HTTP(self.root/'network', self.config.get('request_cap', 1000), 2,
                         guard=self.check_volume, max_bytes=self.config.get('max_bytes',2*1024**3))
        self.c = connect(self.root/'source/data/gitrx.sqlite')
        self.c.execute('PRAGMA synchronous=FULL')
        if self.c.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('Sample database integrity check failed')
        self.fetcher = Fetcher(self.c, self.root/'source', None)
        self.state_path = self.root/'sample.json'
        self.state = json.loads(self.state_path.read_bytes()) if self.state_path.exists() else {
            'format':'gitrx-capacity-sample-1', 'parser_version':PARSER_VERSION,
            'groups':{}, 'selections':{}, 'samples':{}, 'complete':False}
        if self.state['parser_version'] != PARSER_VERSION:
            raise ValueError('Parser changed; use a new sample workspace')

    def check_volume(self):
        Census.check_volume(self)

    def save(self):
        self.check_volume()
        self.state['updated_epoch'] = time.time()
        atomic(self.state_path, self.state)
        atomic(self.root/'status.json', {'complete':self.state['complete'],
             'selected':len(self.state['samples']),
             'finished':sum(s.get('finished',False) for s in self.state['samples'].values()),
             'requests_used':self.http.c.execute('SELECT COUNT(*) FROM requests').fetchone()[0],
             'updated_epoch':time.time()})

    def response(self, url, reuse=True):
        self.check_volume()
        byte_cap = self.config.get('max_bytes',2*1024**3)
        if self.http.bytes_received >= byte_cap:
            raise RuntimeError('Capacity sample retained-byte cap reached')
        before=self.http.c.execute('SELECT COALESCE(MAX(id),0) FROM requests').fetchone()[0]
        path = self.http.get(url, reuse=reuse)
        row = self.http.c.execute('SELECT * FROM requests WHERE id>? AND sha256=? AND status=200 AND error IS NULL ORDER BY id DESC', (before,sha(path))).fetchone()
        if row is None:
            row=self.http.c.execute('SELECT * FROM requests WHERE url=? AND sha256=? AND status=200 AND error IS NULL ORDER BY id DESC',(url,sha(path))).fetchone()
        if row is None:
            raise RuntimeError('Missing HTTP observation')
        return {'id':row['id'], 'url':url, 'final_url':row['url'], 'status':200,
                'sha256':sha(path), 'path':str(path), 'bytes':path.stat().st_size,
                'headers':json.loads(row['headers_json'] or '{}'),
                'retrieved_at':time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(row['started']))}

    def record(self, response, kind='history', version_id=None):
        if sha(response['path'])!=response['sha256']:
            raise RuntimeError('Sample response hash changed before evidence recording')
        headers = response['headers']
        if isinstance(headers,dict): headers=list(headers.items())
        return self.fetcher.record({'url':response['url'], 'final_url':response['final_url'],
            'retrieved_at':response['retrieved_at'], 'status':response['status'],
            'headers':headers}, Path(response['path']).read_bytes(), kind, version_id,
            'capacity-http:'+str(response['id'])+':'+kind+':'+str(version_id))

    def source_checkpoint(self, sid, version):
        """Pin just this version's derived/evidence references, stable as others grow."""
        row=self.c.execute('SELECT * FROM versions WHERE setid=? AND version=?',(sid,version)).fetchone()
        if row is None:raise RuntimeError('Sample source version missing')
        value={'version':dict(row),'derived_document':derived_document(self.c,row['id']),'retrievals':[]}
        for r in self.c.execute('SELECT * FROM retrievals WHERE version_id=? ORDER BY id',(row['id'],)):
            value['retrievals'].append(dict(r))
        return hashlib.sha256(dumps(value).encode()).hexdigest()

    def adopt_checkpoints(self):
        """Explicit migration: independently reparse raw ZIPs before adding pins.

        Original state bytes and comparison evidence are retained. Never called by
        run/resume; this requires the separate --adopt-checkpoints invocation.
        """
        self.check_volume()
        before=self.state_path.read_bytes()
        stage=self.root/'adoptions'/str(time.time_ns());stage.mkdir(parents=True)
        with (stage/'sample-before.json').open('xb') as f:
            f.write(before);f.flush();os.fsync(f.fileno())
        if (self.root/'REPORT.json').exists():
            with (stage/'report-before.json').open('xb') as f:
                f.write((self.root/'REPORT.json').read_bytes());f.flush();os.fsync(f.fileno())
        check=connect(stage/'verification/data/gitrx.sqlite')
        verified=[]
        try:
            for sid,item in sorted(self.state['samples'].items()):
                if item.get('history_state')=='accepted':
                    self.verify_history_sources(sid)
                    rows=[dict(r) for r in self.c.execute('SELECT * FROM release1_history_passes WHERE setid=? ORDER BY pass_number',(sid,))]
                    item['history_checkpoint_sha256']=hashlib.sha256(dumps(rows).encode()).hexdigest()
                for result in item.get('zip_results',[]):
                    if result.get('state')!='downloaded':continue
                    self.check_volume()
                    if sha(result['path'])!=result['sha256']:raise RuntimeError('Adoption raw ZIP hash changed')
                    old=self.c.execute('SELECT * FROM versions WHERE setid=? AND version=?',(sid,result['version'])).fetchone()
                    if old is None or old['selected_sha256']!=result['sha256']:
                        raise RuntimeError('Adoption source version identity mismatch')
                    check.execute('INSERT OR IGNORE INTO families VALUES(?,?,?)',(sid,item['listing']['title'],'{}'))
                    check.execute('INSERT OR IGNORE INTO versions(setid,version) VALUES(?,?)',(sid,result['version']));check.commit()
                    vid=check.execute('SELECT id FROM versions WHERE setid=? AND version=?',(sid,result['version'])).fetchone()[0]
                    rid=Fetcher(check,stage/'verification',None).record({'url':'file:'+result['path'],
                        'retrieved_at':now(),'status':200,'headers':[]},Path(result['path']).read_bytes(),'label',vid)
                    validation=validate_label(check,stage/'verification',rid,sid,result['version'])
                    if validation['state']!='downloaded':raise RuntimeError('Adoption raw ZIP identity invalid')
                    if old['xml_member']!=validation['xml_member'] or old['effective_date']!=validation['effective_date']:
                        raise RuntimeError('Adoption XML-derived identity metadata changed')
                    check.execute("UPDATE versions SET state='downloaded',selected_sha256=?,xml_member=?,effective_date=? WHERE id=?",
                        (result['sha256'],validation['xml_member'],validation['effective_date'],vid));check.commit()
                    persist_document(check,stage/'verification',check.execute('SELECT * FROM versions WHERE id=?',(vid,)).fetchone(),set(self.config.get('section_codes',[])))
                    actual=derived_document(self.c,old['id']);expected=derived_document(check,vid)
                    if actual!=expected:raise RuntimeError('Adoption independently parsed rows differ')
                    result['source_checkpoint_sha256']=self.source_checkpoint(sid,result['version'])
                    verified.append({'setid':sid,'version':result['version'],'raw_sha256':result['sha256'],
                                     'derived_sha256':hashlib.sha256(dumps(actual).encode()).hexdigest(),
                                     'source_checkpoint_sha256':result['source_checkpoint_sha256']})
            self.state['complete']=False
            self.save()
            report={'format':'gitrx-capacity-checkpoint-adoption-1','before_sha256':hashlib.sha256(before).hexdigest(),
                    'after_sha256':sha(self.state_path),'verified':verified,
                    'comparison':'Independent parser/identity validation from raw ZIP; all native narrative rows compared excluding local IDs and parse creation time. Source database not modified.'}
            atomic(stage/'REPORT.json',report)
            return report
        finally:check.close()

    def verify_history_sources(self,sid):
        """Reconcile retained complete-pass inventory to exact saved source pages."""
        from collections import defaultdict
        for p in self.c.execute('SELECT * FROM release1_history_passes WHERE setid=? ORDER BY pass_number',(sid,)):
            versions=defaultdict(set)
            for row in self.c.execute('SELECT response_json FROM release1_history_pages WHERE pass_id=? ORDER BY page',(p['id'],)):
                response=json.loads(row[0])
                if sha(response['path'])!=response['sha256']:raise RuntimeError('Retained sample history hash changed')
                if p['status']=='complete':
                    data=json.loads(Path(response['path']).read_bytes())['data']
                    if data['spl']['setid'].lower()!=sid:raise RuntimeError('Retained sample history identity changed')
                    for r in data['history']:versions[int(r['spl_version'])].add(r.get('published_date'))
            if p['status']=='complete':
                canonical=[[v,sorted(dates,key=lambda d:'' if d is None else d)] for v,dates in sorted(versions.items())]
                if json.loads(p['canonical_json'])!=canonical or hashlib.sha256(json.dumps(canonical,separators=(',',':')).encode()).hexdigest()!=p['signature']:
                    raise RuntimeError('Retained sample history inventory changed')

    def verify_zip(self, sid, result):
        if result.get('sha256') and sha(result['path'])!=result['sha256']:
            raise RuntimeError('Retained sample ZIP hash changed')
        if result.get('state')=='downloaded':
            want=result.get('source_checkpoint_sha256')
            if not want:raise RuntimeError('Sample source checkpoint missing; explicit requalification required')
            if self.source_checkpoint(sid,result['version'])!=want:
                raise RuntimeError('Sample derived source checkpoint changed')

    def verify_history(self,sid,item):
        if item.get('history_state')!='accepted':return
        rows=[dict(r) for r in self.c.execute('SELECT * FROM release1_history_passes WHERE setid=? ORDER BY pass_number',(sid,))]
        pin=hashlib.sha256(dumps(rows).encode()).hexdigest()
        if item.get('history_checkpoint_sha256')!=pin:
            raise RuntimeError('Sample history checkpoint changed or missing')
        self.verify_history_sources(sid)
        for p in rows:
            for row in self.c.execute('SELECT response_json FROM release1_history_pages WHERE pass_id=?',(p['id'],)):
                response=json.loads(row[0])
                if sha(response['path'])!=response['sha256']:
                    raise RuntimeError('Retained sample history hash changed')

    def listing(self, group, code, n):
        if group in self.state['selections']:
            for capture in self.state['selections'][group].get('captures',[]):
                if sha(capture['path'])!=capture['sha256']:raise RuntimeError('Retained sample listing hash changed')
            return
        params={'pagesize':100,'page':1}
        if code:params['doctype']=code
        first=self.response(BASE+'spls.json?'+urlencode(params))
        value=json.loads(Path(first['path']).read_bytes())
        total=int(value['metadata']['total_elements'])
        indices=sample_indices(total,n,self.config.get('seed',20261107),group)
        captures=[{k:first[k] for k in ('url','sha256','path','id')}]
        selected=[];cache={1:value}
        for index in indices:
            page=index//100+1
            if page not in cache:
                params['page']=page
                res=self.response(BASE+'spls.json?'+urlencode(params))
                cache[page]=json.loads(Path(res['path']).read_bytes())
                captures.append({k:res[k] for k in ('url','sha256','path','id')})
            data=cache[page]
            if int(data['metadata']['total_elements'])!=total or int(data['metadata']['current_page'])!=page:
                raise ValueError('Sample listing pagination changed')
            row=data['data'][index%100]
            sid=str(UUID(row['setid']))
            item=self.state['samples'].setdefault(sid,{'setid':sid,'listing':row,'groups':[]})
            if group not in item['groups']:item['groups'].append(group)
            selected.append({'index':index,'setid':sid})
        self.state['groups'][group]=total
        self.state['selections'][group]={'doctype':code,'selected':selected,'captures':captures,
                                         'method':'Uniform listing row positions with frozen seed'}
        self.save()

    def global_frame(self):
        """Use the finished, pinned census rather than sample shifting live pages."""
        group='all_current'
        root=Path(self.config['census_workspace'])
        manifest=root/'manifest.json'
        pin=sha(manifest)
        if self.config.get('census_manifest_sha256',pin)!=pin:
            raise ValueError('Census manifest pin mismatch')
        source=json.loads(manifest.read_bytes())
        if source.get('census_index_sha256') and sha(root/'census.sqlite')!=source['census_index_sha256']:
            raise RuntimeError('Frozen census index hash changed')
        if group in self.state['selections']:return
        c=sqlite3.connect((root/'census.sqlite').as_uri()+'?mode=ro',uri=True)
        try:
            total=c.execute('SELECT COUNT(*) FROM families').fetchone()[0]
            if total!=source['families']:raise ValueError('Census family count mismatch')
            chosen=[]
            for index in sample_indices(total,self.config.get('global_sample',16),self.config.get('seed',20261107),group):
                row=c.execute('SELECT setid,row_json FROM families ORDER BY setid LIMIT 1 OFFSET ?',(index,)).fetchone()
                sid,row_data=row[0],json.loads(row[1])
                item=self.state['samples'].setdefault(sid,{'setid':sid,'listing':row_data,'groups':[]})
                if group not in item['groups']:item['groups'].append(group)
                chosen.append({'index':index,'setid':sid})
            self.state['groups'][group]=total
            self.state['selections'][group]={'doctype':None,'selected':chosen,
                 'census_manifest_sha256':pin,'census_workspace':str(root),
                 'method':'Uniform sorted setid row positions in completed census'}
            self.save()
        finally:c.close()

    def family(self, sid, item):
        self.verify_history(sid,item)
        if item.get('finished'):
            for result in item.get('zip_results',[]):
                self.verify_zip(sid,result)
            return
        self.c.execute('INSERT OR IGNORE INTO families VALUES(?,?,?)',
                       (sid,item['listing']['title'],json.dumps(item['listing'])))
        self.c.commit()
        def fetch(url, number, started):
            return self.response(url,reuse=number==1)
        history=collect(self.c,sid,int(item['listing']['spl_version']),fetch,self.record)
        item['history_state']=history['state']
        item['history_pass_id']=history.get('pass_id')
        if history['state']!='accepted':
            item['finished']=True;self.save();return
        canonical=history['canonical']
        rows=[dict(r) for r in self.c.execute('SELECT * FROM release1_history_passes WHERE setid=? ORDER BY pass_number',(sid,))]
        item['history_checkpoint_sha256']=hashlib.sha256(dumps(rows).encode()).hexdigest()
        item['known_versions']=sum(int(row[0])>0 for row in canonical)
        item['listed_history_entries']=len(canonical)
        item['nonpositive_history_versions']=[r[0] for r in canonical if int(r[0])<1]
        item.setdefault('zip_results',[])
        self.save()
        for version in sampled_versions(canonical,self.config.get('seed',20261107),sid):
            old=next((r for r in item['zip_results'] if r['version']==version),None)
            if old:
                self.verify_zip(sid,old);continue
            before_pages=self.c.execute('PRAGMA page_count').fetchone()[0]
            self.c.execute('INSERT OR IGNORE INTO versions(setid,version) VALUES(?,?)',(sid,version));self.c.commit()
            vid=self.c.execute('SELECT id FROM versions WHERE setid=? AND version=?',(sid,version)).fetchone()[0]
            url='https://dailymed.nlm.nih.gov/dailymed/getFile.cfm?'+urlencode({'type':'zip','setid':sid,'version':version})
            try:
                response=self.response(url)
            except ValueError as exc:
                item['zip_results'].append({'version':version,'state':'unavailable','error':str(exc)})
                self.save();continue
            rid=self.record(response,'label',vid)
            result=validate_label(self.c,self.root/'source',rid,sid,version)
            out={'version':version,'state':result['state'],'sha256':response['sha256'],
                 'path':response['path'],'bytes':response['bytes'],'retrieval_id':rid,
                 'diagnostics':result.get('diagnostics',[])}
            if result['state']=='downloaded':
                self.c.execute("UPDATE versions SET state='downloaded',selected_sha256=?,xml_member=?,effective_date=? WHERE id=?",
                    (response['sha256'],result['xml_member'],result['effective_date'],vid));self.c.commit()
                v=self.c.execute('SELECT * FROM versions WHERE id=?',(vid,)).fetchone()
                persist_document(self.c,self.root/'source',v,set(self.config.get('section_codes',[])))
                out['parses']=[dict(p) for p in self.c.execute('SELECT status,section_count,diagnostics_json FROM parse_runs WHERE version_id=?',(vid,))]
                out['document_types']=[dict(r) for r in self.c.execute('SELECT * FROM document_type_observations WHERE retrieval_id=?',(rid,))]
                pid=self.c.execute('SELECT id FROM parse_runs WHERE version_id=? ORDER BY id DESC LIMIT 1',(vid,)).fetchone()[0]
                payload={name:[dict(r) for r in self.c.execute(sql,(pid,))] for name,sql in query_recipe(Path(__file__).parents[1]).items()}
                out['payload_gzip_bytes']=len(gzip.compress(dumps(payload).encode(),mtime=0))
                out['native_sqlite_growth_bytes']=max(0,self.c.execute('PRAGMA page_count').fetchone()[0]-before_pages)*self.c.execute('PRAGMA page_size').fetchone()[0]
                out['source_checkpoint_sha256']=self.source_checkpoint(sid,version)
            item['zip_results'].append(out);self.save()
        item['finished']=True;self.save()

    def run(self):
        for group,code in TYPES.items():self.listing(group,code,self.config.get('sample_per_type',4))
        if 'census_workspace' in self.config:
            self.global_frame()
        else:
            self.listing('all_current',None,self.config.get('global_sample',16))
        for sid,item in sorted(self.state['samples'].items()):
            if (self.root/'STOP').exists():self.save();return self.state
            try:self.family(sid,item)
            except (ValueError,KeyError,TypeError) as exc:
                item['finished']=True;item['error']={'type':type(exc).__name__,'message':str(exc)};self.save()
        if self.state['complete']:
            report=json.loads((self.root/'REPORT.json').read_bytes())
            if report['sample_sha256']!=sha(self.state_path):raise RuntimeError('Completed sample checkpoint hash changed')
            if report['scenarios']!=scenarios(self.state['groups'],list(self.state['samples'].values())):
                raise RuntimeError('Completed sample report differs from pinned measurements')
            return report
        self.state['complete']=True;self.save()
        self.c.execute('PRAGMA wal_checkpoint(FULL)')
        report={'format':'gitrx-capacity-report-1','sample_sha256':sha(self.state_path),
                'scenarios':scenarios(self.state['groups'],list(self.state['samples'].values())),
                'sample_source_database_bytes':(self.root/'source/data/gitrx.sqlite').stat().st_size,
                'scope':'Current listing observations; archived-only inventory unknown',
                'limitations':['Up to two uniform listed-version ZIPs/family; small sample, no precision claim.',
                               'Small samples; unknown/failed families are not zero-byte families.',
                               'Per-type filter totals must not be added to the all_current total.',
                               'No release built, no promotion, no clinical or full-category extraction qualification.']}
        atomic(self.root/'REPORT.json',report)
        return report

    def close(self):
        self.c.close();self.http.close();self.lock.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',required=True)
    parser.add_argument('--adopt-checkpoints',action='store_true',help='Explicitly verify and add integrity pins to an older sample checkpoint; never automatic')
    args=parser.parse_args();job=Capacity(args.config)
    try:print(json.dumps(job.adopt_checkpoints() if args.adopt_checkpoints else job.run()),flush=True)
    finally:job.close()


if __name__=='__main__':main()
