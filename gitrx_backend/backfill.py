"""Pinned finite DailyMed backfill: raw evidence and parsing together, one lane.

Reuses the maintained HTTP ledger, history collector, source schema, validator and
parser. Batches freeze independently; this command never promotes or exports Git.
"""
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime
import fcntl
import hashlib
import json
import os
import resource
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlencode,urljoin,urlsplit,parse_qs

from lxml import html,etree
from .census import Census
from .jobs import HTTP,atomic,sha
from .mobile_archive import MobileArchive,ArchiveUnavailable
from .capacity import derived_document
from gitrx.current.archive import Fetcher,validate_label,now
from gitrx.current.db import connect,dumps
from gitrx.current.parser import persist_document,PARSER_VERSION
from gitrx.current.spl_parser_v1 import CONFIG as PARSER_CONFIG
from gitrx_services.io import readonly
from gitrx_services.release1_history import collect,persist_inventory
from gitrx_services.release1_history_incidents import HistoryIdentityConflict
from gitrx_services.release1_job import xml_identity
from gitrx_services.release1_http import Stop


def day(raw):
    try:return datetime.strptime(raw,'%b %d, %Y').date().isoformat()
    except (ValueError,TypeError):return None


def version_pin(c,vid):
    row=dict(c.execute('SELECT * FROM versions WHERE id=?',(vid,)).fetchone())
    retrievals=[dict(r) for r in c.execute('SELECT * FROM retrievals WHERE version_id=? ORDER BY id',(vid,))]
    return hashlib.sha256(dumps({'version':row,'native':derived_document(c,vid),'retrievals':retrievals}).encode()).hexdigest()


class GuardedDB:
    def __init__(self,c,guard):self.c=c;self.guard=guard
    def execute(self,sql,parameters=()):
        if not sql.lstrip().upper().startswith(('SELECT','PRAGMA')):
            self.guard(sum(len(x.encode()) if isinstance(x,str) else len(x) if isinstance(x,bytes) else 16 for x in parameters)*4)
        return self.c.execute(sql,parameters)
    def executemany(self,sql,parameters):
        for row in parameters:self.execute(sql,row)
        return self.c.execute('SELECT 1 WHERE 0')
    def __getattr__(self,name):return getattr(self.c,name)
    def __enter__(self):self.c.__enter__();return self
    def __exit__(self,*args):return self.c.__exit__(*args)


class Backfill:
    def __init__(self,config):
        self.config_path=Path(config).absolute();raw=self.config_path.read_bytes();self.config=json.loads(raw)
        if self.config['format']!='gitrx-backfill-1' or self.config['parser_version']!=PARSER_VERSION:
            raise ValueError('Unsupported backfill/parser configuration')
        if self.config['parser_config_sha256']!=hashlib.sha256(dumps(PARSER_CONFIG).encode()).hexdigest():
            raise RuntimeError('Frozen parser configuration changed')
        self.root=Path(self.config['workspace']).absolute();self.volume=Path(self.config['volume']).absolute()
        self.mobile=MobileArchive(self.config) if self.config.get('mobile_archive') else None
        self.http=None;self.active=None;self.closed_bytes=0;self.hot=None;self.processing=None;self.raw_journal=None;self.staging=None;self.buffer=None;self.last_heartbeat=0
        self.inflight_families={};self.family_pipeline_limit=1
        self.check_volume();self.root.mkdir(parents=True,exist_ok=True)
        self.lock=(self.root/'writer.lock').open('a+');fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        pin=self.root/'config-pin.json'
        if pin.exists() and pin.read_bytes()!=raw:raise RuntimeError('Backfill configuration changed')
        if not pin.exists():
            with pin.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        hot_policy=self.root/'LOCAL-STORAGE-POLICY.json'
        if hot_policy.exists():
            from .hot_storage import HotStorage
            self.hot=HotStorage(hot_policy,self.check_volume)
            if self.hot.policy.get('raw_staging_root'):
                from .backfill_staging import RawStaging
                self.staging=RawStaging(self.hot.policy['raw_staging_root'],self.root,
                    self.hot.policy.get('raw_staging_max_bytes',20*1024**3),self.check_volume)
            elif self.hot.policy.get('raw_journal_root'):
                from .raw_journal import RawJournal
                self.raw_journal=RawJournal(self.hot.policy['raw_journal_root'],self.hot.policy.get('raw_journal_bytes',512*1024**2))
        control=self.hot.attach(self.root/'control.sqlite','control') if self.hot else self.root/'control.sqlite'
        self.c=sqlite3.connect(control,timeout=60);self.c.row_factory=sqlite3.Row
        self.c.executescript('''PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS queue(setid TEXT PRIMARY KEY,ordinal INTEGER UNIQUE,batch INTEGER,state TEXT DEFAULT 'pending',result_json TEXT);
        CREATE INDEX IF NOT EXISTS queue_state ON queue(state,ordinal);
        CREATE TABLE IF NOT EXISTS batches(number INTEGER PRIMARY KEY,manifest_sha256 TEXT,database_sha256 TEXT,bytes INTEGER);
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
        ''')
        self.c.executescript('PRAGMA fullfsync=ON; PRAGMA checkpoint_fullfsync=ON;')
        if self.c.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('Backfill control integrity failure')
        self.closed_bytes=self.c.execute('SELECT COALESCE(SUM(bytes),0) FROM batches').fetchone()[0]
        parent_path=Path(self.config['parent_config']);parent_cfg=json.loads(parent_path.read_bytes())
        if sha(parent_path)!=self.config['parent_config_sha256']:raise RuntimeError('Parent configuration changed')
        parent_root=(parent_path.parent/parent_cfg['release_root']).resolve()
        parent_manifest=parent_root/'reports/EXPANDED-SELECTION.json'
        if sha(parent_manifest)!=self.config['parent_manifest_sha256']:raise RuntimeError('Parent manifest changed')
        self.parent=json.loads(parent_manifest.read_bytes());self.parent_snapshot=parent_cfg['snapshot']
        self.known={(v['setid'],int(v['version'])):v for v in self.parent['versions']}
        self.parent_scope=(parent_path.parent/parent_cfg['scope_path']).resolve()
        if sha(self.parent_scope)!=self.config['parent_scope_sha256']:raise RuntimeError('Parent scope changed')
        self.names=json.loads(self.parent_scope.read_bytes())['records']
        census_root=Path(self.config['census_workspace']);manifest=census_root/'manifest.json'
        if sha(manifest)!=self.config['census_manifest_sha256']:raise RuntimeError('Census manifest changed')
        self.census_manifest=json.loads(manifest.read_bytes())
        if sha(census_root/'census.sqlite')!=self.census_manifest['census_index_sha256']:raise RuntimeError('Census index changed')
        self.frame=sqlite3.connect((census_root/'census.sqlite').as_uri()+'?mode=ro',uri=True);self.frame.row_factory=sqlite3.Row
        self.frame_columns={r[1] for r in self.frame.execute('PRAGMA table_info(families)')}
        self.seed_samples={}
        if self.config.get('seed_sample'):
            p=Path(self.config['seed_sample'])
            if sha(p)!=self.config['seed_sample_sha256']:raise RuntimeError('Seed sample changed')
            seed=json.loads(p.read_bytes())
            for sid,item in seed['samples'].items():
                for z in item.get('zip_results',[]):
                    if z.get('state')=='downloaded':self.seed_samples[(sid,z['version'])]=z
        transport=self.root/'TRANSPORT-POLICY.json'
        transport_settings=json.loads(transport.read_bytes()) if transport.exists() else {}
        buffered=transport_settings.get('download_buffer_enabled',False)
        if type(buffered) is not bool:raise ValueError('Download buffering must be explicitly boolean')
        if buffered and not self.staging:raise ValueError('Download buffering requires bounded local raw staging')
        ledger=self.hot.attach(self.root/'network/ledger.sqlite','network') if self.hot else None
        self.http=HTTP(self.staging.network if self.staging else self.root/'network',self.config['request_cap'],self.config.get('spacing_seconds',2),
                       guard=self.guard,max_bytes=self.config['network_byte_cap'],retain_completed_partials=False,
                       transport_policy=transport if transport.exists() else None,ledger_path=ledger,raw_journal=self.raw_journal,
                       body_fallback=([self.root/'network/bodies',self.mobile.archive/'network/bodies'] if self.mobile else self.root/'network/bodies') if self.staging else None,threaded_coordinator=buffered)
        self.recover_batches()
        self.prepare_queue()
        self.started=time.monotonic()
        self.last_publish=0;self.last_family=None
        processing=self.root/'PROCESSING-POLICY.json'
        if processing.exists():
            from .backfill_processing import ProcessingPool
            processing_settings=json.loads(processing.read_bytes())
            self.family_pipeline_limit=processing_settings.get('family_pipeline_limit',1)
            if type(self.family_pipeline_limit) is not int or not 1<=self.family_pipeline_limit<=24:
                raise ValueError('Family pipeline limit must be 1..24')
            self.processing=ProcessingPool(self.root,processing_settings,self.commit_parse,self.guard,self.heartbeat)
        if buffered:
            from .download_buffer import DownloadBuffer
            self.buffer=DownloadBuffer(self.http,self.staging.root/'buffer',sha(self.config_path),self.download_guard,self.guard,transport_settings)

    def check_volume(self):
        if self.mobile:self.mobile.guard()
        else:Census.check_volume(self)

    def heartbeat(self):
        if time.monotonic()-self.last_heartbeat<5:return
        self.check_volume();self.last_heartbeat=time.monotonic()
        atomic(self.root/'worker-heartbeat.json',{'pid':os.getpid(),'heartbeat_epoch':time.time(),'operation':'acquiring-validating-parsing',
            'open_file_limit':resource.getrlimit(resource.RLIMIT_NOFILE)[0],
            'open_descriptors':len(os.listdir('/dev/fd')) if Path('/dev/fd').is_dir() else None})

    def recover_batches(self):
        for registered in self.c.execute('SELECT * FROM batches'):
            marker=self.root/'batches'/f"{registered['number']:05d}"/'BATCH.json'
            if not marker.exists() or sha(marker)!=registered['manifest_sha256']:raise RuntimeError('Registered batch marker missing/changed')
        for marker in (self.root/'batches').glob('*/BATCH.json'):
            batch=json.loads(marker.read_bytes());number=batch['batch'];database=marker.parent/'data/gitrx.sqlite'
            if self.mobile and self.mobile.receipt(marker.parent):
                if not self.mobile.available() and database.is_symlink():
                    if sha(marker.parent/'inbox.json')!=batch['inbox_sha256']:raise RuntimeError('Frozen inbox changed')
                    old=self.c.execute('SELECT * FROM batches WHERE number=?',(number,)).fetchone()
                    if not old or old['manifest_sha256']!=sha(marker) or old['database_sha256']!=batch['source_database_sha256']:
                        raise RuntimeError('Offline archive receipt disagrees with selected batch pins')
                    continue
            database_digest=sha(database)
            if database_digest!=batch['source_database_sha256']:raise RuntimeError('Frozen batch database changed')
            if sha(marker.parent/'inbox.json')!=batch['inbox_sha256']:raise RuntimeError('Frozen batch inbox changed')
            old=self.c.execute('SELECT * FROM batches WHERE number=?',(number,)).fetchone()
            if old:
                if old['manifest_sha256']!=sha(marker) or old['database_sha256']!=database_digest:raise RuntimeError('Frozen batch pin changed')
            else:
                size=database.stat().st_size+marker.stat().st_size+(marker.parent/'inbox.json').stat().st_size
                with self.c:self.c.execute('INSERT INTO batches VALUES(?,?,?,?)',(number,sha(marker),database_digest,size))
            if self.hot and self.hot._identity(database)[3].exists():
                state=self.hot._state(database)[4]
                if state['phase']=='released':
                    # The immutable bytes were just hashed against both batch
                    # pins. Check the hot-storage pin using that same digest.
                    if state.get('frozen_sha256')!=database_digest:
                        raise RuntimeError('Released batch hot-storage pin changed')
                else:
                    self.hot.freeze(database);self.hot.release(database)
        self.closed_bytes=self.c.execute('SELECT COALESCE(SUM(bytes),0) FROM batches').fetchone()[0]
        finished=self.c.execute("SELECT batch FROM queue GROUP BY batch HAVING SUM(state IN ('pending','in_progress','waiting_for_archive'))=0").fetchall()
        for row in finished:
            if not self.c.execute('SELECT 1 FROM batches WHERE number=?',(row[0],)).fetchone():self.freeze_batch(row[0])

    def guard(self,additional=0):
        if self.buffer:self.buffer.check()
        self.check_volume()
        if self.hot:self.hot.check(additional)
        if self.staging:self.staging.check()
        self.heartbeat()
        network=self.http.bytes_received if self.http else 0
        opened=0
        if self.active:
            db=self.active[0]/'data/gitrx.sqlite'
            opened=sum(p.stat().st_size for p in (db,Path(str(db)+'-wal')) if p.exists())
        # Transfer bytes conservatively count retries as well as unique CAS bytes.
        # Completed batch raw files are hardlinks to network objects, not copies.
        if network+self.closed_bytes+opened+additional+256*1024**2>self.config['retained_byte_cap']:
            raise RuntimeError('Backfill retained byte cap reached')

    def download_guard(self):
        # Acquisition never touches native SQLite or the consumer heartbeat.
        self.check_volume()
        if self.hot:self.hot.check()
        if self.staging:self.staging.check()
        if self.http.bytes_received+self.closed_bytes+256*1024**2>self.config['retained_byte_cap']:
            raise RuntimeError('Backfill retained byte cap reached')

    def prepare_queue(self):
        if self.c.execute("SELECT 1 FROM meta WHERE key='plan'").fetchone():return
        baseline={sid for sid,_ in self.known}
        todo=sorted({r[0] for r in self.frame.execute('SELECT setid FROM families')}|(baseline if self.config.get('refresh_retained_parent',True) else set()))
        todo.sort(key=lambda sid:hashlib.sha256((str(self.config.get('seed',20261107))+sid).encode()).digest())
        with self.c:
            self.c.executemany('INSERT INTO queue(setid,ordinal,batch) VALUES(?,?,?)',
                [(sid,n+1,n//self.config['batch_size']+1) for n,sid in enumerate(todo)])
            self.c.execute("INSERT INTO meta VALUES('plan',?)",(dumps({'target_families':len(todo),'baseline_families':len(baseline),'baseline_versions':len(self.known),'census_manifest_sha256':self.config['census_manifest_sha256']}),))
        atomic(self.root/'PLAN.json',json.loads(self.c.execute("SELECT value FROM meta WHERE key='plan'").fetchone()[0]))

    def source(self,sid):
        row=self.frame.execute('SELECT row_json FROM families WHERE setid=?',(sid,)).fetchone()
        if row is None:
            if sid not in self.names:raise RuntimeError('Queue outside pinned census and retained parent')
            return {'setid':sid,'title':self.names[sid]['title'],'spl_version':max(v for s,v in self.known if s==sid),
                    'source_basis':'retained parent family outside current listing','parent_record':self.names[sid]}
        return json.loads(row[0])

    def captures(self,sid):
        if 'page' not in self.frame_columns:return []
        row=self.frame.execute('SELECT page FROM families WHERE setid=?',(sid,)).fetchone()
        if row is None:return []
        page=row[0]
        return [self.census_manifest['pages'][page-1]]

    def open_batch(self,number):
        if self.active and self.active[3]==number:return self.active[:3]
        if self.active:self.active[1].close();self.active=None
        root=self.root/'batches'/f'{number:05d}'
        if (root/'BATCH.json').exists():raise RuntimeError('Refusing to reopen frozen batch')
        self.guard();database=self.hot.attach(root/'data/gitrx.sqlite',f'batch-{number:05d}') if self.hot else root/'data/gitrx.sqlite'
        if self.staging:self.staging.attach(root)
        c=connect(database);c.executescript('PRAGMA synchronous=FULL; PRAGMA fullfsync=ON; PRAGMA checkpoint_fullfsync=ON;')
        if c.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise RuntimeError('Batch integrity failure')
        c.executescript('''CREATE TABLE IF NOT EXISTS backfill_versions(version_id INTEGER PRIMARY KEY,state TEXT,pin TEXT,details_json TEXT);
          CREATE TABLE IF NOT EXISTS backfill_http(retrieval_id INTEGER PRIMARY KEY,observation_json TEXT);
          CREATE TABLE IF NOT EXISTS backfill_parent_refs(version_id INTEGER PRIMARY KEY,reference_json TEXT);''')
        c.execute('INSERT OR IGNORE INTO ingest_runs(id,started_at) VALUES(1,?)',(now(),));c.commit()
        wrapped=GuardedDB(c,self.guard);fetcher=Fetcher(wrapped,root,1)
        self.active=(root,wrapped,fetcher,number)
        # Verify selected evidence and derived checkpoints on restart.
        for r in c.execute('SELECT * FROM backfill_versions'):
            if r['state'] in ('complete','partial','parse_failed'):
                v=c.execute('SELECT * FROM versions WHERE id=?',(r['version_id'],)).fetchone()
                path=root/c.execute('SELECT path FROM artifacts WHERE sha256=?',(v['selected_sha256'],)).fetchone()[0]
                if sha(path)!=v['selected_sha256'] or version_pin(c,v['id'])!=r['pin']:raise RuntimeError('Batch evidence or parsed checkpoint changed')
        return root,wrapped,fetcher

    def response(self,url,reuse=True):
        if self.buffer:
            url,row,path=self.buffer.request(url,reuse)
            return self.response_record(url,row,path)
        before=self.http.c.execute('SELECT COALESCE(MAX(id),0) FROM requests').fetchone()[0]
        try:path=self.http.get(url,reuse=reuse)
        except ValueError as exc:
            row=self.http.c.execute('SELECT * FROM requests WHERE id>? ORDER BY id DESC LIMIT 1',(before,)).fetchone()
            if row is not None and (row['status'] in (0,408,429,500,502,503,504) or row['error'] and not any(t in row['error'] for t in ('exceeds 100 MiB','byte cap'))):
                raise RuntimeError(f"Transient HTTP failure: HTTP {row['status']}: {row['error'] or 'upstream unavailable'}") from exc
            if row is None or row['error'] or row['status'] not in (404,410):raise
            path=self.http.body_path(row['sha256'])
        else:
            row=self.http.c.execute('SELECT * FROM requests WHERE id>? AND sha256=? AND status=200 AND error IS NULL ORDER BY id DESC',(before,sha(path))).fetchone()
            if row is None:row=self.http.c.execute('SELECT * FROM requests WHERE url=? AND sha256=? AND status=200 AND error IS NULL ORDER BY id DESC',(url,sha(path))).fetchone()
        return self.response_record(url,row,path)

    def response_record(self,url,row,path):
        if row is None or sha(path)!=row['sha256']:raise RuntimeError('HTTP observation identity/hash mismatch')
        return {'id':row['id'],'namespace':'backfill:'+sha(self.config_path),'url':url,'final_url':row['url'],
                'retrieved_at':datetime.fromtimestamp(row['started']).astimezone().isoformat(),
                'timestamp_basis':'request_started_at',
                'status':row['status'],'headers':json.loads(row['headers_json'] or '{}'),'error':row['error'],
                'path':str(path),'sha256':row['sha256'],'bytes':path.stat().st_size}

    def record(self,c,root,fetcher,response,kind,vid=None):
        self.guard();digest=response['sha256'];path=Path(response['path'])
        if sha(path)!=digest:raise RuntimeError('Raw source changed')
        dest=root/'artifacts/sha256'/digest[:2]/digest;dest.parent.mkdir(parents=True,exist_ok=True)
        if dest.exists():
            if sha(dest)!=digest:raise RuntimeError('Artifact hash changed')
        else:
            if path.stat().st_dev!=dest.parent.stat().st_dev:
                if not self.staging:raise RuntimeError('New raw evidence must share the configured CAS volume')
                from .backfill_staging import copy_verified
                copy_verified(path,dest,digest)
            else:os.link(path,dest)
            directory=os.open(dest.parent,os.O_RDONLY)
            try:os.fsync(directory)
            finally:os.close(directory)
        headers=response.get('headers',[])
        if isinstance(headers,dict):headers=list(headers.items())
        record={k:response.get(k) for k in ('url','final_url','retrieved_at','status','error')};record['headers']=headers
        key='backfill:'+response['namespace']+':'+str(response['id'])+':'+kind+':'+str(vid)
        rid=fetcher.record(record,path.read_bytes(),kind,vid,key)
        # The canonical per-batch artifact alias survives raw-buffer movement,
        # including an offline deferred family retried after SSD archiving.
        # Preserve the original HTTP identity/timestamps/hash; only the local
        # retained-body locator is projected onto that stable artifact.
        with c:c.execute('INSERT OR IGNORE INTO backfill_http VALUES(?,?)',(rid,dumps(response)))
        response['path']=str(dest)
        return rid

    def identity_conflict(self,*args):raise HistoryIdentityConflict('History identity mismatch; source retained')

    def seed_response(self,sid,version):
        z=self.seed_samples.get((sid,version))
        if not z:return None
        if self.mobile and self.mobile.archive_dependency(z['path']) and not self.mobile.available():return None
        if sha(z['path'])!=z['sha256']:raise RuntimeError('Seed raw evidence changed')
        source=Path(self.config['seed_sample']).parent/'source/data/gitrx.sqlite'
        with closing(readonly(source)) as c:
            r=c.execute('SELECT * FROM retrievals WHERE id=?',(z['retrieval_id'],)).fetchone()
            if not r or r['artifact_sha256']!=z['sha256']:raise RuntimeError('Seed provenance mismatch')
            return {'namespace':'capacity:'+self.config['seed_sample_sha256'],'id':r['id'],
                    'url':r['url'],'final_url':r['final_url'],'retrieved_at':r['retrieved_at'],
                    'status':r['status'],'headers':json.loads(r['headers_json']),'error':r['error'],
                    'path':z['path'],'sha256':z['sha256'],'bytes':z['bytes']}

    def parent_ref(self,c,sid,v):
        old=self.known[(sid,v['version'])];prefix=old['parse_runs'][0]['id'].split(':',1)[0]
        source=Path(self.parent['sources'][prefix]['root'])
        if (self.mobile and self.mobile.archive_dependency(source/'data/gitrx.sqlite')
            and not self.mobile.available()):raise ArchiveUnavailable('Retained source database awaits SSD')
        with closing(readonly(source/'data/gitrx.sqlite')) as parent:
            raw=parent.execute('SELECT path FROM artifacts WHERE sha256=?',(old['selected_sha256'],)).fetchone()
        if raw is None:raise RuntimeError('Retained parent raw evidence missing/changed')
        if (self.mobile and self.mobile.archive_dependency(source/raw[0])
            and not self.mobile.available()):raise ArchiveUnavailable('Retained source bytes await SSD')
        if sha(source/raw[0])!=old['selected_sha256']:raise RuntimeError('Retained parent raw evidence missing/changed')
        with c:
            c.execute("UPDATE versions SET state='retained-parent' WHERE id=?",(v['id'],))
            c.execute('INSERT OR IGNORE INTO backfill_parent_refs VALUES(?,?)',(v['id'],dumps({'snapshot':self.parent_snapshot,'version':old,'raw_path':str(source/raw[0])})))

    def admit(self,root,c,fetcher,sid,version,response,kind):
        v=c.execute('SELECT * FROM versions WHERE setid=? AND version=?',(sid,version)).fetchone()
        rid=self.record(c,root,fetcher,response,kind,v['id'])
        if response['status']!=200:return False
        checked=validate_label(c,root,rid,sid,version)
        if checked['state']=='identity_mismatch':
            with c:c.execute('INSERT OR REPLACE INTO backfill_versions VALUES(?,?,?,?)',(v['id'],'quarantined',None,dumps(checked)))
            raise HistoryIdentityConflict('XML identity mismatch; source retained in quarantine')
        if checked['state']!='downloaded':return False
        if v['selected_sha256'] and v['selected_sha256']!=response['sha256']:raise RuntimeError('Competing selected bytes')
        with c:c.execute("UPDATE versions SET state='downloaded',selected_sha256=?,xml_member=?,effective_date=? WHERE id=?",(response['sha256'],checked['xml_member'],checked['effective_date'],v['id']))
        self.guard();version=c.execute('SELECT * FROM versions WHERE id=?',(v['id'],)).fetchone()
        details={'retrieval_id':rid,'raw_sha256':response['sha256'],'validation':checked}
        if self.processing:
            self.processing.submit(c,root,version,set(self.config['section_codes']),details)
            self.processing.drain()
        else:self.commit_parse(c,root,version,set(self.config['section_codes']),None,details)
        return True

    def commit_parse(self,c,root,v,registry,prepared,details):
        failure=isinstance(prepared,dict) and 'extraction_error' in prepared
        if failure:
            error=prepared['extraction_error']
            existing=c.execute('SELECT id FROM parse_runs WHERE version_id=? AND artifact_sha256=? AND parser_version=? AND config_json=?',
                (v['id'],v['selected_sha256'],PARSER_VERSION,dumps(PARSER_CONFIG))).fetchone()
            if not existing:
                with c:c.execute('INSERT INTO parse_runs(version_id,artifact_sha256,parser_version,config_json,created_at,status,diagnostics_json,section_count) VALUES(?,?,?,?,?,?,?,?)',
                    (v['id'],v['selected_sha256'],PARSER_VERSION,dumps(PARSER_CONFIG),now(),'extraction_failed',
                     dumps([{'code':'document_extraction_error','error':error['type']+': '+error['message']}]),0))
        elif prepared is None:persist_document(c,root,v,registry)
        else:
            from gitrx_services.release1_parse_pool import persist_prepared
            persist_prepared(c,root,v,registry,prepared)
        states=[r[0] for r in c.execute('SELECT status FROM parse_runs WHERE version_id=?',(v['id'],))]
        status=states[0] if len(states)==1 and states[0] in ('complete','partial') else 'parse_failed'
        pin=version_pin(c,v['id'])
        with c:c.execute('INSERT OR REPLACE INTO backfill_versions VALUES(?,?,?,?)',(v['id'],status,pin,dumps(details)))
        # Persist the circuit breaker across worker restarts. Isolated failures
        # remain earmarked by native parse diagnostics and source/version pins.
        key='consecutive_parse_failures'
        old=self.c.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
        streak=(int(old[0]) if old else 0)+1 if status=='parse_failed' else 0
        with self.c:self.c.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,str(streak)))
        if streak>=10:raise RuntimeError('Systemic parser failure: 10 consecutive failed versions; retained for review')
        return {'durable':True,'version_pin':pin}

    def archive_fallback(self,root,c,fetcher,sid,missing):
        url='https://dailymed.nlm.nih.gov/dailymed/archives/index.cfm?'+urlencode({'query':sid,'pagesize':100,'page':1})
        seen=set();links=set();remaining=set(missing)
        for _ in range(self.config.get('archive_page_cap',20)):
            if not url or url in seen or not remaining:break
            seen.add(url);response=self.response(url);listing_id=self.record(c,root,fetcher,response,'archive_listing')
            if response['status']!=200:break
            try:doc=html.fromstring(Path(response['path']).read_bytes())
            except (etree.LxmlError,ValueError):return remaining
            nexts=[]
            for href in doc.xpath('//a[contains(@href,"getArchivalFile.cfm")]/@href'):
                link=urljoin(url,href)
                if link in links:continue
                links.add(link)
                if len(links)>self.config.get('archive_link_cap',100):return remaining
                body=self.response(link)
                if body['status']!=200:continue
                try:identity=xml_identity(body['path'])
                except (etree.LxmlError,ValueError):identity=None
                if identity and identity[0]!=sid:raise HistoryIdentityConflict('Archive route returned different family')
                if identity and identity[1] in remaining and self.admit(root,c,fetcher,sid,identity[1],body,'archive_route'):
                    vid=c.execute('SELECT id FROM versions WHERE setid=? AND version=?',(sid,identity[1])).fetchone()[0]
                    rid=c.execute('SELECT id FROM retrievals WHERE version_id=? ORDER BY id DESC LIMIT 1',(vid,)).fetchone()[0]
                    aid=parse_qs(urlsplit(link).query).get('archive_id',[''])[0]
                    with c:c.execute('INSERT OR IGNORE INTO archive_route_sources VALUES(?,?,?,?,?,?)',(rid,aid,listing_id,sid,identity[1],dumps({'identity_basis':'internal XML setid/version only'})))
                    remaining.remove(identity[1])
            current=int(parse_qs(urlsplit(url).query).get('page',['1'])[0])
            for href in doc.xpath('//a[@href]/@href'):
                candidate=urljoin(url,href);q=parse_qs(urlsplit(candidate).query)
                if q.get('query')==[sid] and q.get('page',[''])[0].isdigit() and int(q['page'][0])>current:nexts.append((int(q['page'][0]),candidate))
            url=min(nexts)[1] if nexts else None
        return remaining

    def history_start(self,sid,number):
        if not self.buffer or not self.buffer.prefetch_confirm or number not in (1,2):return None
        url='https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/'+sid+'/history.json?'+urlencode({'pagesize':100,'page':1})
        saved=self.buffer._read_ready(url) if number==1 else self.buffer.confirmation(url)
        if saved:return datetime.fromtimestamp(saved[1]['started']).astimezone().isoformat()
        return None

    def history_response(self,url,number,started):
        if self.buffer and number==2:
            saved=self.buffer.confirmation(url)
            if saved and saved[1]['started']>=datetime.fromisoformat(started).timestamp()-1e-6:
                return self.response_record(*saved)
        return self.response(url,reuse=number==1)

    def family(self,row):
        sid=row['setid'];root,c,fetcher=self.open_batch(row['batch']);item=self.source(sid)
        if self.buffer and self.buffer.batch!=row['batch']:
            plan=[]
            if not self.buffer.all_batches or not self.buffer.plan_seeded:
                query="SELECT * FROM queue WHERE state IN ('pending','in_progress','waiting_for_archive')"
                args=() if self.buffer.all_batches else (row['batch'],)
                if not self.buffer.all_batches:query+=' AND batch=?'
                for r in self.c.execute(query+' ORDER BY ordinal',args):
                    plan.append((r['setid'],r['ordinal'],int(self.source(r['setid'])['spl_version']),r['batch']))
                    if len(plan)%250==0:self.guard()
            self.buffer.activate(row['batch'],plan,set(self.known)|set(self.seed_samples))
        with self.c:self.c.execute("UPDATE queue SET state='in_progress' WHERE setid=?",(sid,))
        with c:c.execute('INSERT OR IGNORE INTO families VALUES(?,?,?)',(sid,item['title'],dumps({'listing':item})))
        result=collect(c,sid,int(item['spl_version']),lambda url,number,started:self.history_response(url,number,started),
                       lambda response:self.record(c,root,fetcher,response,'history'),identity_handler=self.identity_conflict,pass_started=lambda number:self.history_start(sid,number))
        error=persist_inventory(c,sid,result,{'latest_versions_seen':[int(item['spl_version'])]},
                                self.config['census_manifest_sha256'],self.captures(sid))
        if error:return error,{'history_state':result['state']}
        targets=c.execute('SELECT * FROM versions WHERE setid=? ORDER BY version',(sid,)).fetchall()
        if not targets:return 'source_anomaly',{'reason':'No positive source version targets'}
        missing=[];todo=[]
        for v in targets:
            self.guard()
            if (sid,v['version']) in self.known:self.parent_ref(c,sid,v);continue
            existing=c.execute('SELECT * FROM backfill_versions WHERE version_id=?',(v['id'],)).fetchone()
            if existing:
                if existing['state'] in ('complete','partial','parse_failed'):
                    if version_pin(c,v['id'])!=existing['pin']:raise RuntimeError('Parsed version checkpoint changed')
                    continue
                if existing['state']=='quarantined':continue
            todo.append(v)
        lanes=self.http.pacing.policy.get('download_lanes',1) if self.http.pacing else 1
        for offset in range(0,len(todo),lanes):
            group=todo[offset:offset+lanes];responses={};urls=[];download_versions=[]
            for v in group:
                seed=self.seed_response(sid,v['version'])
                if seed:responses[v['version']]=seed
                else:
                    urls.append('https://dailymed.nlm.nih.gov/dailymed/getFile.cfm?'+urlencode({'type':'zip','setid':sid,'version':v['version']}))
                    download_versions.append(v['version'])
            if lanes>=2 and urls:
                downloads=self.buffer.get_many(urls) if self.buffer else self.http.get_many(urls)
                for version,(url,record,path) in zip(download_versions,downloads):
                    responses[version]=self.response_record(url,record,path)
            else:
                for version,url in zip(download_versions,urls):responses[version]=self.response(url)
            for v in group:
                if not self.admit(root,c,fetcher,sid,v['version'],responses[v['version']],'getFile_zip'):missing.append(v['version'])
                if self.buffer:self.buffer.acknowledge(responses[v['version']]['url'])
                self.publish('acquiring-and-parsing',sid)
        if missing:
            missing=self.archive_fallback(root,c,fetcher,sid,missing)
            with c:
                for version in missing:
                    vid=c.execute('SELECT id FROM versions WHERE setid=? AND version=?',(sid,version)).fetchone()[0]
                    if not c.execute('SELECT 1 FROM backfill_versions WHERE version_id=?',(vid,)).fetchone():
                        c.execute('INSERT INTO backfill_versions VALUES(?,?,?,?)',(vid,'unavailable',None,dumps({'attempted_routes':['getFile_zip','bounded_archive_listing'],'archive_scan_complete':False})))
        if self.processing and self.family_pipeline_limit>1:
            return 'processing_pending',{'history_result':result}
        if self.processing:self.processing.drain_family(sid,root)
        return self.family_outcome(sid,result,c)

    def family_outcome(self,sid,result,c):
        targets=c.execute('SELECT * FROM versions WHERE setid=? ORDER BY version',(sid,)).fetchall()
        item=self.source(sid)
        counts=Counter(r[0] for r in c.execute('SELECT b.state FROM backfill_versions b JOIN versions v ON v.id=b.version_id WHERE v.setid=?',(sid,)))
        retained=c.execute("SELECT COUNT(*) FROM versions WHERE setid=? AND state='retained-parent'",(sid,)).fetchone()[0]
        retained_states=Counter(self.known[(sid,v['version'])]['parse_runs'][0]['status'] for v in targets if (sid,v['version']) in self.known)
        successful=counts['complete']+counts['partial']+retained_states['complete']+retained_states['partial']
        state='complete' if successful==len(targets) and not counts['partial'] and not retained_states['partial'] else 'partial' if successful==len(targets) else 'incomplete'
        if any(v<1 for v,_ in result['canonical']) or int(item['spl_version'])<1:state='source_anomaly'
        return state,{'targets':len(targets),'counts':dict(counts),'retained_parent_versions':retained,'retained_parent_parse_counts':dict(retained_states),
                      'history_pass_id':result['pass_id'],'nonpositive_history_versions':[v for v,_ in result['canonical'] if v<1]}

    def freeze_batch(self,number):
        if self.c.execute('SELECT 1 FROM batches WHERE number=?',(number,)).fetchone():return
        root,c,fetcher=self.open_batch(number)
        rows=[dict(r) for r in self.c.execute('SELECT * FROM queue WHERE batch=? ORDER BY ordinal',(number,))]
        if any(r['state'] in ('pending','in_progress','waiting_for_archive') for r in rows):return
        if self.buffer:self.buffer.pause()
        if self.staging:self.staging.flush(root,self.buffer.ready_hashes() if self.buffer else ())
        # Only raw-file handoff needs acquisition paused. Database freezing and
        # SSD publication operate on completed batch files, not the live spool.
        if self.buffer and self.buffer.all_batches:
            self.buffer.activate(self.buffer.batch,[],self.buffer.skip)
        c.execute('PRAGMA wal_checkpoint(TRUNCATE)');database=root/'data/gitrx.sqlite'
        final_root=self.mobile.final_batch(number) if self.mobile else root
        manifest={'format':'gitrx-backfill-batch-1','config_sha256':sha(self.config_path),'batch':number,
                  'source_database_sha256':sha(database),'families':rows,'parser_version':PARSER_VERSION,
                  'parent_snapshot':self.parent_snapshot,'historical_completeness_claim':False}
        inbox=[]
        ready={r['setid'] for r in rows if r['state'] in ('complete','partial')}
        for v in c.execute("SELECT * FROM versions WHERE state IN ('downloaded','retained-parent') ORDER BY setid,version"):
            if v['setid'] not in ready:continue
            dates=[dict(p) for p in c.execute('SELECT * FROM tranche_publication_observations WHERE setid=? AND version=? ORDER BY ordinal',(v['setid'],v['version']))]
            if v['state']=='retained-parent':
                ref=json.loads(c.execute('SELECT reference_json FROM backfill_parent_refs WHERE version_id=?',(v['id'],)).fetchone()[0])
                inbox.append({'setid':v['setid'],'version':v['version'],'zip_path':ref['raw_path'],
                              'publication_dates':sorted({p['publication_date'] for p in dates if p['publication_date']}),
                              'publication_observations':dates,'retained_parent_snapshot':self.parent_snapshot,
                              'source_database':str(final_root/'data/gitrx.sqlite'),'source_database_sha256':manifest['source_database_sha256']})
                continue
            record=c.execute('SELECT * FROM retrievals WHERE version_id=? AND artifact_sha256=? AND status=200 ORDER BY id LIMIT 1',(v['id'],v['selected_sha256'])).fetchone()
            artifact=c.execute('SELECT path FROM artifacts WHERE sha256=?',(v['selected_sha256'],)).fetchone()
            original=c.execute('SELECT observation_json FROM backfill_http WHERE retrieval_id=?',(record['id'],)).fetchone()
            observation=json.loads(original[0]) if original else {k:record[k] for k in ('url','final_url','retrieved_at','status','error','attempt')}
            observation.update(sha256=v['selected_sha256'],headers=json.loads(record['headers_json']))
            inbox.append({'setid':v['setid'],'version':v['version'],'zip_path':str(final_root/artifact[0]),
                          'publication_dates':sorted({p['publication_date'] for p in dates if p['publication_date']}),
                          'publication_observations':dates,'source_observation':observation,
                          'source_database':str(final_root/'data/gitrx.sqlite'),'source_database_sha256':manifest['source_database_sha256']})
        c.close();self.active=None
        if self.hot:self.hot.freeze(database)
        manifest['source_database_sha256']=sha(database)
        for entry in inbox:entry['source_database_sha256']=manifest['source_database_sha256']
        atomic(root/'inbox.json',{'entries':inbox,'incidents':[],'batch_qualification':'Only fully retrieved and parsed families included; complete/partial extraction is not full source fidelity.'})
        manifest['inbox_sha256']=sha(root/'inbox.json')
        atomic(root/'BATCH.json',manifest)
        size=database.stat().st_size+(root/'BATCH.json').stat().st_size+(root/'inbox.json').stat().st_size
        with self.c:self.c.execute('INSERT INTO batches VALUES(?,?,?,?)',(number,sha(root/'BATCH.json'),sha(database),size))
        self.closed_bytes+=size
        if self.hot:self.hot.release(database)
        if self.mobile and self.mobile.available():self.mobile.sync_pending()

    def publish(self,phase,current=None):
        # Progress is a display projection; native queue/version checkpoints stay
        # durable at their existing boundaries. Avoid scanning the entire plan
        # and fsyncing a display file twice per small family. Terminal states
        # always publish immediately, including bounded-run/pause results.
        if phase=='acquiring-and-parsing' and time.monotonic()-self.last_publish<5:return None
        self.last_family=current;self.last_publish=time.monotonic()
        counts=dict(self.c.execute('SELECT state,COUNT(*) FROM queue GROUP BY state'))
        totals=self.c.execute("SELECT value FROM meta WHERE key='totals'").fetchone()
        status={'format':'gitrx-backfill-status-1','phase':phase,'current_family':current,
                'target_families':sum(counts.values()),'family_states':counts,
                'attempted_families':sum(n for s,n in counts.items() if s not in ('pending','in_progress','waiting_for_archive')),
                'frozen_batches':self.c.execute('SELECT COUNT(*) FROM batches').fetchone()[0],
                'families_in_flight':len(self.inflight_families),'family_pipeline_limit':self.family_pipeline_limit,
                'versions':json.loads(totals[0]) if totals else {},
                'requests_used':self.http.request_count,'request_cap':self.config['request_cap'],
                'response_bytes_received':self.http.bytes_received,'network_byte_cap':self.config['network_byte_cap'],
                'retained_byte_cap':self.config['retained_byte_cap'],'parent_snapshot':self.parent_snapshot,
                'selected_release_changed':False,'archived_only_inventory':'unknown','updated_at':now()}
        if self.mobile:status['mobile_archive']=self.mobile.status()
        previous=self.c.execute("SELECT value FROM meta WHERE key='rate_samples'").fetchone()
        samples=json.loads(previous[0]) if previous else []
        epoch=time.time();done=status['attempted_families']
        # Clear samples across sleep/pause/restarts; ETA uses active recent work.
        if samples and epoch-samples[-1][0]>120:samples=[]
        samples=[s for s in samples if 0<=epoch-s[0]<=1800 and s[1]<=done]
        if not samples or epoch-samples[-1][0]>=5:samples.append([epoch,done])
        with self.c:self.c.execute("INSERT OR REPLACE INTO meta VALUES('rate_samples',?)",(dumps(samples),))
        if samples and epoch-samples[0][0]>=60 and done-samples[0][1]>=3:
            rate=(done-samples[0][1])*3600/(epoch-samples[0][0])
            status.update(recent_families_per_hour=rate,eta_seconds=(status['target_families']-done)*3600/rate)
        atomic(self.root/'status.json',status)
        return status

    def store_family(self,row,state,result):
        c=self.active[1]
        counts=Counter(r[0] for r in c.execute('SELECT b.state FROM backfill_versions b JOIN versions v ON v.id=b.version_id WHERE v.setid=?',(row['setid'],)))
        family_totals={'known_targets':c.execute('SELECT COUNT(*) FROM versions WHERE setid=?',(row['setid'],)).fetchone()[0],
            'new_downloaded_valid':c.execute("SELECT COUNT(*) FROM versions WHERE setid=? AND state='downloaded'",(row['setid'],)).fetchone()[0],
            'retained_parent':c.execute("SELECT COUNT(*) FROM versions WHERE setid=? AND state='retained-parent'",(row['setid'],)).fetchone()[0],
            'new_parsed_complete':counts['complete'],'new_parsed_partial':counts['partial'],
            'new_parse_failed':counts['parse_failed'],'unavailable':counts['unavailable'],'quarantined':counts['quarantined']}
        result['totals']=family_totals
        old=self.c.execute("SELECT value FROM meta WHERE key='totals'").fetchone()
        totals=Counter(json.loads(old[0]) if old else {})
        previous=json.loads(row['result_json']) if row['result_json'] else {}
        totals.subtract(previous.get('totals',{}));totals.update(family_totals)
        with self.c:
            self.c.execute('UPDATE queue SET state=?,result_json=? WHERE setid=?',(state,dumps(result),row['setid']))
            self.c.execute("INSERT OR REPLACE INTO meta VALUES('totals',?)",(dumps(dict(totals)),))
            old=self.c.execute("SELECT value FROM meta WHERE key='consecutive_family_errors'").fetchone()
            streak=(int(old[0]) if old else 0)+1 if state in ('source_error','quarantined') else 0
            self.c.execute("INSERT OR REPLACE INTO meta VALUES('consecutive_family_errors',?)",(str(streak),))
        if streak>=10:raise RuntimeError('Systemic family failure: 10 consecutive source/identity errors; review required')

    def settle_families(self,all=False,block=False):
        if not self.inflight_families:return
        self.processing.drain(all=all,block=block)
        pending={j['version']['setid'] for j in self.processing.pending.values()}
        for sid,(row,history) in list(self.inflight_families.items()):
            if sid in pending:continue
            state,result=self.family_outcome(sid,history,self.active[1])
            self.store_family(row,state,result)
            if self.buffer:self.buffer.family_done(sid)
            del self.inflight_families[sid]
        if self.active:self.freeze_batch(self.active[3])

    def run(self,max_families=None):
        if (self.root/'COMPLETE.json').exists():return self.publish('complete')
        processed=0
        include_waiting=not self.mobile or self.mobile.available()
        for row in self.c.execute("SELECT * FROM queue WHERE state IN ('pending','in_progress') OR (state='waiting_for_archive' AND ?) ORDER BY ordinal",(include_waiting,)).fetchall():
            if (self.root/'STOP').exists() or (max_families is not None and processed>=max_families):
                self.settle_families(all=True)
                return self.publish('paused')
            if self.inflight_families and self.active and self.active[3]!=row['batch']:self.settle_families(all=True)
            while len(self.inflight_families)>=self.family_pipeline_limit:self.settle_families(block=True)
            self.guard();self.publish('acquiring-and-parsing',row['setid'])
            try:state,result=self.family(row)
            except ArchiveUnavailable as exc:
                if not self.mobile:raise
                state,result='waiting_for_archive',{'reason':str(exc),'source_retained':True}
            except (HistoryIdentityConflict,Stop) as exc:
                if 'IDENTITY' not in str(exc).upper() and not isinstance(exc,HistoryIdentityConflict):raise
                state,result='quarantined',{'reason':str(exc),'source_retained':True}
            except ValueError as exc:
                if 'HTTP ' in str(exc):raise
                state,result='source_error',{'reason':str(exc)}
            if state=='processing_pending':
                self.inflight_families[row['setid']]=(row,result['history_result'])
                self.settle_families()
            else:
                if self.processing:self.processing.drain_family(row['setid'],self.active[0])
                self.store_family(row,state,result)
                if self.buffer:self.buffer.family_done(row['setid'])
                self.settle_families()
                self.freeze_batch(row['batch'])
            processed+=1;self.publish('acquiring-and-parsing')
        self.settle_families(all=True)
        if self.c.execute("SELECT 1 FROM queue WHERE state='waiting_for_archive' LIMIT 1").fetchone():
            if self.mobile and max_families is None:
                while not self.mobile.available():
                    if (self.root/'STOP').exists():return self.publish('paused')
                    self.guard();self.publish('waiting-for-archive');time.sleep(2)
                return self.run()
            return self.publish('waiting-for-archive')
        status=self.publish('complete')
        atomic(self.root/'COMPLETE.json',dict(status,meaning='Finite plan attempted; inspect failures/unknowns. Not complete historical DailyMed coverage.'))
        return status

    def close(self):
        try:
            if self.processing:self.processing.close(interrupted=bool(self.processing.pending))
        finally:
            try:
                if self.buffer:self.buffer.close()
                else:self.http.close()
            finally:
                if self.active:self.active[1].close()
                self.frame.close();self.c.close();self.lock.close()
                if self.raw_journal:self.raw_journal.close()


def configure_file_limit():
    """Bound this worker's descriptor allowance; never change launchd globally."""
    soft,hard=resource.getrlimit(resource.RLIMIT_NOFILE)
    target=4096 if hard==resource.RLIM_INFINITY else min(4096,hard)
    if soft<target:resource.setrlimit(resource.RLIMIT_NOFILE,(target,hard))
    return resource.getrlimit(resource.RLIMIT_NOFILE)[0]


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',required=True);parser.add_argument('--max-families',type=int)
    args=parser.parse_args();configure_file_limit();job=Backfill(args.config)
    try:print(json.dumps(job.run(args.max_families)),flush=True)
    except Exception as exc:
        job.check_volume();atomic(job.root/'LAST-ERROR.json',{'type':type(exc).__name__,'message':str(exc),'at':now()});raise
    finally:job.close()


if __name__=='__main__':main()
