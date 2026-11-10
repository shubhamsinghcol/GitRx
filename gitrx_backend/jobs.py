"""Configured additive ingestion and immutable native release cycles."""
from pathlib import Path
from contextlib import contextmanager
import argparse
import fcntl
import gzip
import http.client
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import time
import urllib.request
import urllib.error
from urllib.parse import urlsplit,urlencode
from uuid import UUID

from gitrx.current.archive import Fetcher, validate_label, now
from gitrx.current.db import connect,dumps
from gitrx.current.parser import persist_document, PARSER_VERSION
from gitrx.current.export_git import export_git
from gitrx_services.native_query import build as build_index
from gitrx_services.export_payloads import query_recipe,PayloadCache
from gitrx_services.federation import One
from gitrx_services.io import readonly,ensure_space
from gitrx_services.shard_release import ShardView
from .config import open_reader
from .operational_status import tracked, check as record_check, status as operational_status, progress as operation_progress


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
def sync_body(stream):
    """Flush raw bytes, including the device-cache barrier on macOS if supported."""
    stream.flush();os.fsync(stream.fileno())
    import sys,errno
    if sys.platform=='darwin':
        try:fcntl.fcntl(stream.fileno(),getattr(fcntl,'F_FULLFSYNC',51));return True
        except OSError as exc:
            if exc.errno not in (errno.EINVAL,errno.ENOTSUP,errno.ENOTTY):raise
    return False
def atomic(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_name(path.name+'.tmp')
    with tmp.open('w') as f:f.write(dumps(value)+'\n');f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)
    directory=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(directory)
    finally:os.close(directory)
def git(repo,*args):return subprocess.check_output(['git','--no-replace-objects','-C',str(repo),*args],text=True).strip()

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

class HTTP:
    """One shared workspace ledger; failed bodies and interrupted files retained."""
    def __init__(self,root,cap=1000,spacing=2,guard=None,max_bytes=None,max_response_seconds=120,retain_completed_partials=True,transport_policy=None,ledger_path=None,raw_journal=None,body_fallback=None,threaded_coordinator=False):
        if not 1<=cap<=1000000 or spacing<2:raise ValueError('Request cap must be 1..1000000; baseline spacing at least 2 seconds')
        self.root=Path(root);self.cap=cap;self.spacing=spacing
        self.guard=guard or (lambda: None);self.guard()
        if max_bytes is not None and (type(max_bytes) is not int or max_bytes<1):raise ValueError('Positive byte budget required')
        self.max_bytes=max_bytes
        if max_response_seconds<=0:raise ValueError('Positive response deadline required')
        self.max_response_seconds=max_response_seconds
        self.retain_completed_partials=retain_completed_partials
        self.raw_journal=raw_journal
        self.body_fallbacks=[Path(p) for p in (body_fallback if isinstance(body_fallback,(list,tuple)) else [body_fallback])] if body_fallback else []
        (self.root/'bodies').mkdir(parents=True,exist_ok=True);(self.root/'partial').mkdir(exist_ok=True)
        self.c=sqlite3.connect(Path(ledger_path) if ledger_path is not None else self.root/'ledger.sqlite',check_same_thread=not threaded_coordinator);self.c.row_factory=sqlite3.Row
        self.c.executescript('PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA fullfsync=ON; PRAGMA checkpoint_fullfsync=ON;')
        self.c.execute('CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY,url,started REAL,status,sha256,error,headers_json)');self.c.commit()
        if 'headers_json' not in {r[1] for r in self.c.execute('PRAGMA table_info(requests)')}:
            self.c.execute('ALTER TABLE requests ADD COLUMN headers_json');self.c.commit()
        if 'bytes' not in {r[1] for r in self.c.execute('PRAGMA table_info(requests)')}:
            self.c.execute('ALTER TABLE requests ADD COLUMN bytes INTEGER DEFAULT 0');self.c.commit()
        self.c.execute('CREATE TABLE IF NOT EXISTS transport_state(key TEXT PRIMARY KEY,value REAL)')
        self.c.execute('CREATE INDEX IF NOT EXISTS requests_url_id ON requests(url,id DESC)')
        self.c.execute('CREATE INDEX IF NOT EXISTS requests_started ON requests(started)')
        columns={r[1] for r in self.c.execute('PRAGMA table_info(requests)')}
        for name in ('latency_seconds','spacing_seconds','connection_serial'):
            if name not in columns:self.c.execute('ALTER TABLE requests ADD COLUMN '+name+' REAL')
        self.c.commit()
        self.pacing=None;self.opener=None
        if transport_policy is not None:
            from .transport_policy import AdaptivePacing
            from gitrx_services.release1_connection import SingleConnection
            self.pacing=AdaptivePacing(self.root,transport_policy,ledger_path);self.opener=SingleConnection()
        # A request start is durable before its body. A killed process must
        # never turn a retained response prefix into a successful cached body.
        for row in self.c.execute('SELECT id FROM requests WHERE status IS NULL').fetchall():
            part=self.root/'partial'/str(row['id']);digest=None
            if part.exists():
                with part.open('rb') as f:os.fsync(f.fileno())
                digest=sha(part);target=self.root/'bodies'/digest
                if not target.exists():os.link(part,target)
                directory=os.open(target.parent,os.O_RDONLY)
                try:os.fsync(directory)
                finally:os.close(directory)
            with self.c:self.c.execute("UPDATE requests SET status=0,error='Interrupted transfer retained; not reusable',sha256=?,bytes=? WHERE id=?",(digest,part.stat().st_size if part.exists() else 0,row['id']))
        self.bytes_received=max(self.c.execute('SELECT COALESCE(SUM(bytes),0) FROM requests').fetchone()[0],sum(p.stat().st_size for p in (self.root/'partial').iterdir() if p.is_file()))
        self.request_count=self.c.execute('SELECT COUNT(*) FROM requests').fetchone()[0]
    def get(self,url,reuse=False):
        self.guard()
        if reuse:
            row=self.c.execute('SELECT sha256 FROM requests WHERE url=? AND status=200 AND error IS NULL ORDER BY id DESC LIMIT 1',(url,)).fetchone()
            if row:
                cached=self.body_path(row['sha256'])
                if sha(cached)!=row['sha256']:raise RuntimeError('Cached response hash mismatch')
                return cached
        original=url
        for attempt in range(3):
            for hop in range(6):
                u=urlsplit(url)
                if u.scheme!='https' or not (u.hostname=='nlm.nih.gov' or (u.hostname or '').endswith('.nlm.nih.gov')):raise ValueError('Official NLM HTTPS URLs only')
                if self.request_count>=self.cap:raise RuntimeError('Workspace request cap reached')
                if self.max_bytes is not None and self.bytes_received>=self.max_bytes:raise RuntimeError('Workspace byte cap reached')
                last=self.c.execute('SELECT MAX(started) FROM requests').fetchone()[0] or 0
                deferred=self.c.execute("SELECT value FROM transport_state WHERE key='retry_after'").fetchone()
                while deferred and deferred[0]>time.time():
                    if not (self.pacing and self.pacing.policy.get('wait_for_retry_after',False)):
                        raise RuntimeError('Retry deferred by upstream Retry-After; retained in ledger')
                    self.guard();time.sleep(min(.1,max(0,deferred[0]-time.time())))
                    deferred=self.c.execute("SELECT value FROM transport_state WHERE key='retry_after'").fetchone()
                spacing=self.pacing.spacing if self.pacing else self.spacing
                time.sleep(max(0,last+spacing-time.time()));ensure_space(self.root,100*1024**2)
                self.guard()
                with self.c:ident=self.c.execute('INSERT INTO requests(url,started) VALUES(?,?)',(url,time.time())).lastrowid
                self.request_count+=1
                part=self.root/'partial'/str(ident);status=0;failure=None;headers={};retryable_failure=False
                response_started=time.monotonic()
                try:
                    agent='GitRx/0.10 local DailyMed research archive; bounded adaptive transport' if self.pacing else 'GitRx/0.10 native history archive; sequential >=2 seconds'
                    request=urllib.request.Request(url,headers={'User-Agent':agent,'Accept-Encoding':'identity'})
                    try:response=(self.opener or urllib.request.build_opener(NoRedirect)).open(request,timeout=30)
                    except urllib.error.HTTPError as exc:response=exc
                    with response,part.open('xb') as f:
                        status=response.code;headers={k.lower():v for k,v in response.headers.items()};total=0
                        while chunk:=response.read(1024*1024):
                            if time.monotonic()-response_started>self.max_response_seconds:raise TimeoutError('Response execution deadline exceeded')
                            self.guard()
                            ensure_space(self.root,2*1024**2)
                            if self.max_bytes is not None and self.bytes_received+len(chunk)>self.max_bytes:
                                prefix=chunk[:max(0,self.max_bytes-self.bytes_received)]
                                f.write(prefix);self.bytes_received+=len(prefix)
                                sync_body(f)
                                raise ValueError('Workspace byte cap reached; response prefix retained')
                            total+=len(chunk)
                            if total>100*1024**2:raise ValueError('Response exceeds 100 MiB')
                            f.write(chunk);self.bytes_received+=len(chunk)
                        self.fullfsync_supported=sync_body(f)
                except (OSError,http.client.HTTPException) as exc:failure=str(exc);retryable_failure=True
                except ValueError as exc:failure=str(exc)
                if failure and self.opener:self.opener.close()
                self.guard()
                if not part.exists():part.write_bytes(b'')
                digest=sha(part);target=self.root/'bodies'/digest
                if target.exists():
                    if sha(target)!=digest:raise RuntimeError('Existing HTTP content hash changed')
                else:os.link(part,target)
                directory=os.open(target.parent,os.O_RDONLY)
                try:os.fsync(directory)
                finally:os.close(directory)
                if self.raw_journal:self.raw_journal.store(target,digest,external_path=target)
                if headers.get('retry-after'):
                    from gitrx.current.archive import retry_delay
                    until=time.time()+retry_delay(list(headers.items()),0)
                    with self.c:self.c.execute("INSERT OR REPLACE INTO transport_state VALUES('retry_after',?)",(until,))
                with self.c:self.c.execute('UPDATE requests SET status=?,sha256=?,error=?,headers_json=?,bytes=? WHERE id=?',(status,digest,failure,json.dumps(headers),part.stat().st_size,ident))
                latency=time.monotonic()-response_started
                with self.c:self.c.execute('UPDATE requests SET latency_seconds=?,spacing_seconds=?,connection_serial=? WHERE id=?',(latency,spacing,self.opener.serial if self.opener else None,ident))
                if self.pacing:self.pacing.note(status,failure,latency,headers)
                if not self.retain_completed_partials and not failure:part.unlink()
                if status in (301,302,303,307,308) and not failure:
                    from urllib.parse import urljoin
                    url=urljoin(url,headers.get('location',''));continue
                if status==200 and not failure:return target
                if (not retryable_failure and status not in (0,408,429,500,502,503,504)) or attempt==2:raise ValueError(f'HTTP {status}: {failure or original}')
                # Do not defeat an upstream Retry-After with a short retry loop.
                if headers.get('retry-after') and not (self.pacing and self.pacing.policy.get('wait_for_retry_after',False)):
                    raise RuntimeError('Retry deferred by upstream Retry-After; retained in ledger')
                break
            else:raise ValueError('Redirect limit exceeded')
            url=original
        raise ValueError('Retrieval failed')
    def body_path(self,digest):
        local=self.root/'bodies'/digest
        if local.exists():return local
        for directory in self.body_fallbacks:
            candidate=directory/digest
            if candidate.exists():return candidate
        return local

    def get_many(self,urls,interrupt=None,on_result=None,reuse=True):
        from .parallel_downloads import get_many
        return get_many(self,urls,interrupt=interrupt,on_result=on_result,reuse=reuse)

    def close(self):
        if getattr(self,'download_pool',None):self.download_pool.close()
        if self.opener:self.opener.close()
        self.c.close()

class Base:
    """Union exporter adapter with prepared payloads and exact raw source pointers."""
    def __init__(self,manifest,cache_root,cache):
        self.manifest=manifest;self.raw=dumps(manifest);self.snapshot=hashlib.sha256(self.raw.encode()).hexdigest()
        self.cache=PayloadCache(cache_root,cache);self.sources={};self.raw_sources={}
        for v in manifest['versions']:self.raw_sources[v['selected_sha256']]=v['parse_runs'][0]['id'].split(':',1)[0]
    def execute(self,sql,parameters=()):
        if 'SELECT manifest_json FROM snapshots' in sql:return One((self.raw,)) if parameters[0]==self.snapshot else One(None)
        if 'parse_run_id=?' in sql:return self.cache.read(str(parameters[0]),'tables' if 'FROM narrative_tables' in sql else 'sections')
        if 'SELECT path,artifact_class FROM artifacts' in sql:
            prefix=self.raw_sources[parameters[0]];source=self.manifest['sources'][prefix];root=Path(source['root'])
            if prefix not in self.sources:self.sources[prefix]=readonly(root/'data/gitrx.sqlite')
            row=self.sources[prefix].execute(sql,parameters).fetchone()
            if row is None:raise ValueError('Missing raw evidence pointer')
            return One({'path':str(root/row['path']),'artifact_class':row['artifact_class']})
        raise ValueError('Unsupported export query')
    def close(self):
        for c in self.sources.values():c.close()

class Job:
    def __init__(self,config):
        self.path=Path(config).resolve();self.config=json.loads(self.path.read_bytes())
        if self.config['format']!='gitrx-job-config-1' or self.config['parser_version']!=PARSER_VERSION:raise ValueError('Unsupported job/parser version')
        self.root=(self.path.parent/self.config['workspace']).resolve()
        if 'search_projection' in self.config and type(self.config['search_projection']) is not bool:raise ValueError('search_projection must be boolean')
        if 'promote' in self.config and type(self.config['promote']) is not bool:raise ValueError('promote must be boolean')
        admission_keys={'admission_scope_path','admission_scope_sha256'} & self.config.keys()
        if admission_keys and (len(admission_keys)!=2 or not self.config.get('admission_scope_path') or not self.config.get('admission_scope_sha256')):
            raise ValueError('Admission scope requires both path and SHA-256 pin')
    def admitted_scope(self,parent_config,parent_path):
        """Explicit additive register; retained family records are never rewritten."""
        original=(Path(parent_path).parent/parent_config['scope_path']).resolve()
        original_raw=original.read_bytes();old=json.loads(original_raw)['records']
        release=(Path(parent_path).parent/parent_config['release_root']).resolve()
        parent_raw=(release/'reports/EXPANDED-SELECTION.json').read_bytes().removesuffix(b'\n')
        if hashlib.sha256(parent_raw).hexdigest()!=parent_config['snapshot']:raise ValueError('Parent selection pin mismatch')
        expected_scope=json.loads(parent_raw).get('scope_sha256')
        if expected_scope and hashlib.sha256(original_raw).hexdigest()!=expected_scope:
            raise ValueError('Parent scope pin mismatch')
        if 'admission_scope_path' not in self.config:return original,original_raw,old
        path=(self.path.parent/self.config['admission_scope_path']).resolve()
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=self.config['admission_scope_sha256']:
            raise ValueError('Admission scope SHA-256 pin mismatch')
        scope=json.loads(raw);records=scope['records']
        if not isinstance(records,dict):raise ValueError('Admission scope records must be a register')
        for sid,row in records.items():
            if str(UUID(sid))!=sid or not isinstance(row,dict) or not isinstance(row.get('title'),str) or not row['title'].strip():
                raise ValueError('Admission scope requires canonical setids and nonempty titles')
        if any(records.get(sid)!=row for sid,row in old.items()):
            raise ValueError('Admission scope must preserve every parent family record unchanged')
        return path,raw,records
    @contextmanager
    def lock(self):
        self.root.mkdir(parents=True,exist_ok=True)
        with (self.root/'writer.lock').open('a+') as f:
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            yield
    def parent(self):
        current=self.root/'current.json'
        return current if current.exists() else (self.path.parent/self.config['parent_config']).resolve()
    def status(self):return operational_status(self.root,self.parent())
    @staticmethod
    def publication_observations(rows):
        """Preserve source date strings; ISO dates are derived, not replacements."""
        from datetime import date,datetime
        result={}
        def iso(day):
            if not isinstance(day,str) or date.fromisoformat(day).isoformat()!=day:
                raise ValueError('Publication dates require YYYY-MM-DD')
            return day
        for row in rows:
            supplied={iso(day) for day in row.get('publication_dates',[]) if day}
            if 'publication_observations' not in row:
                for day in supplied:result.setdefault((day,day),{'published_date':day,'published_raw':day})
                continue
            observations=row['publication_observations']
            if not isinstance(observations,list):raise ValueError('Publication observations must be a list')
            known=set()
            for observed in observations:
                if not isinstance(observed,dict):raise ValueError('Publication observation must be a record')
                for left,right in (('publication_raw','published_raw'),('publication_date','published_date')):
                    if left in observed and right in observed and observed[left]!=observed[right]:
                        raise ValueError('Conflicting publication observation aliases')
                raw=observed.get('publication_raw',observed.get('published_raw'))
                if raw is not None and not isinstance(raw,str):raise ValueError('Raw publication date must be text or null')
                try:derived=datetime.strptime(raw,'%b %d, %Y').date().isoformat()
                except (ValueError,TypeError):derived=None
                given=observed.get('publication_date',observed.get('published_date',derived))
                if given is not None:iso(given)
                if given!=derived:raise ValueError('Publication date does not match retained source string')
                if derived is not None:known.add(derived)
                normalized={k:v for k,v in observed.items() if k not in ('publication_raw','publication_date','published_raw','published_date')}
                normalized.update(published_date=derived,published_raw=raw)
                result.setdefault((derived,raw),normalized)
            if 'publication_dates' in row and supplied!=known:
                raise ValueError('Publication dates do not match retained source observations')
        return [result[key] for key in sorted(result,key=lambda key:(key[0] or '',key[1] or ''))]
    @tracked('activate')
    def activate(self,target,expected_snapshot):
        """Select a retained, verified release without modifying its evidence or refs."""
        with self.lock():
            current=self.parent();before=json.loads(current.read_bytes())
            if before['snapshot']!=expected_snapshot:raise ValueError('Current release changed; activation refused')
            target=Path(target).resolve()
            original=(self.path.parent/self.config['parent_config']).resolve()
            if target!=original:
                if not target.is_relative_to(self.root/'releases') or target.name!='backend.json':raise ValueError('Target must be a retained release or configured original parent')
                complete=json.loads((target.parent/'COMPLETE.json').read_bytes())
                if complete['snapshot']!=json.loads(target.read_bytes())['snapshot']:raise ValueError('Target is incomplete')
            selected=json.loads(target.read_bytes())
            # Relative paths must retain target-config meaning after pointer selection.
            for key in ('release_root','export_root','scope_path','search_index','evidence_metadata'):
                if key in selected:selected[key]=str((target.parent/selected[key]).resolve())
            release=Path(selected['release_root'])
            raw=(release/'reports/EXPANDED-SELECTION.json').read_bytes().removesuffix(b'\n')
            if hashlib.sha256(raw).hexdigest()!=selected['snapshot']:raise ValueError('Target selection pin mismatch')
            with open_reader(target,'sqlite') as sql,open_reader(target,'git') as g:
                families=sql.families()
                if families!=g.families():raise ValueError('Target family mismatch')
                # Verify both citation paths in every shard before selection.
                for prefix in '0123456789abcdef':
                    sid=next(f['setid'] for f in families if f['setid'].startswith(prefix))
                    vs=sql.versions(sid)
                    if [{k:v for k,v in x.items() if k!='commit'} for x in g.versions(sid)]!=vs:raise ValueError('Target history mismatch')
                    if sql.read(sid,vs[-1]['version'])[0]!=g.read(sid,vs[-1]['version'])[0]:raise ValueError('Target text mismatch')
            event={'from':before['snapshot'],'to':selected['snapshot'],'target':str(target),'at':now()}
            atomic(self.root/'activations'/(str(time.time_ns())+'.json'),dict(event,state='validated'))
            atomic(self.root/'current.json',selected)
            return dict(event,state='selected')

    @tracked('refresh')
    def refresh(self,setids):
        """Two uncached agreeing inventory passes, then fetch only missing identities."""
        with self.lock():
            config=json.loads(self.parent().read_bytes());release=(self.parent().parent/config['release_root']).resolve()
            manifest=json.loads((release/'reports/EXPANDED-SELECTION.json').read_bytes());known={(v['setid'],v['version']):v for v in manifest['versions']}
            _,_,approved=self.admitted_scope(config,self.parent())
            http=HTTP(self.root/'network',self.config.get('request_cap',1000),self.config.get('spacing_seconds',2));entries=[];incidents=[]
            known_families=set(approved) if 'admission_scope_path' in self.config else {sid for sid,_ in known}
            if len(setids)>len(known_families):raise ValueError('Refresh scope exceeds pinned family register')
            sid=None;verified=False;attempted_ids=[]
            try:
                for sid in setids:
                    sid=str(UUID(sid));inventories=[];verified=False
                    if sid not in known_families:raise ValueError('Refresh family outside pinned supported scope')
                    attempted_ids.append(sid)
                    record_check(self.root,self._operation_id,sid,'attempted',{})
                    for _ in range(2):
                        rows=[];total=None;pages=None
                        for page in range(1,101):
                            url=f'https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/{sid}/history.json?'+urlencode({'pagesize':100,'page':page})
                            d=json.loads(http.get(url).read_bytes());meta=d['metadata'];data=d['data']
                            if data['spl']['setid'].lower()!=sid:raise ValueError('History identity mismatch')
                            if total is None:total=int(meta['total_elements']);pages=max(1,int(meta['total_pages']))
                            if int(meta['total_elements'])!=total or max(1,int(meta['total_pages']))!=pages or int(meta['current_page'])!=page:raise ValueError('History pagination shifted')
                            rows.extend((int(r['spl_version']),r.get('published_date')) for r in data['history'])
                            if page>=pages:break
                        if not rows or len(rows)!=total or pages>100:raise ValueError('Incomplete/empty history inventory')
                        if any(version<1 for version,_ in rows):raise ValueError('History contains nonpositive version identities; retained source requires reconciliation')
                        inventories.append(sorted(set(rows)))
                    if inventories[0]!=inventories[1]:raise ValueError('History passes disagree; retry a later cycle')
                    verified=True;before_entries=len(entries)
                    record_check(self.root,self._operation_id,sid,'inventory-agreed',{'inventory_count':len(inventories[0]),'inventory_sha256':hashlib.sha256(dumps(inventories[0]).encode()).hexdigest()},verified=True)
                    for version,published in inventories[0]:
                        from datetime import datetime
                        try:day=datetime.strptime(published,'%b %d, %Y').date().isoformat()
                        except (ValueError,TypeError):day=None
                        old=known.get((sid,version))
                        if old is not None:
                            if day is None or day in {o['published_date'] for o in old['publication_observations']}:continue
                            prefix=old['parse_runs'][0]['id'].split(':',1)[0];source=Path(manifest['sources'][prefix]['root'])
                            with readonly(source/'data/gitrx.sqlite') as c:
                                artifact=c.execute('SELECT path FROM artifacts WHERE sha256=?',(old['selected_sha256'],)).fetchone()
                            body=source/artifact['path']
                        else:
                            body=http.get('https://dailymed.nlm.nih.gov/dailymed/getFile.cfm?'+urlencode({'type':'zip','setid':sid,'version':version}),reuse=True)
                        entries.append({'setid':sid,'version':version,'zip_path':str(body),'publication_dates':[day] if day else [],'publication_raw':published})

                    record_check(self.root,self._operation_id,sid,'candidates' if len(entries)>before_entries else 'no-change',{'new_candidates':len(entries)-before_entries})
            except (ValueError,RuntimeError,OSError,KeyError,TypeError) as exc:
                incidents.append(str(exc))
                if sid is not None and str(sid) in attempted_ids:record_check(self.root,self._operation_id,str(sid),'acquisition-failed' if verified else 'inventory-failed',{'error_type':type(exc).__name__,'message':str(exc)[:2000]})
            finally:http.close()
            inbox=self.root/('inbox-'+str(time.time_ns())+'.json');atomic(inbox,{'entries':entries,'incidents':incidents})
            return {'inbox':str(inbox),'new_candidates':len(entries),'incidents':incidents,
                    'requested_family_count':len(setids),'attempted_family_count':len(attempted_ids),
                    'unattempted_family_count':max(0,len(setids)-len(attempted_ids)),
                    'requested_family_ids':[str(x)[:200] for x in setids[:200]],'requested_scope_truncated':len(setids)>200}
    @tracked('cycle')
    def cycle(self,inbox):
        with self.lock():
            scope_parent=self.parent();scope_config=json.loads(scope_parent.read_bytes())
            admitted_path,admitted_raw,admitted_records=self.admitted_scope(scope_config,scope_parent)
            inbox=Path(inbox).resolve();input_data=json.loads(inbox.read_bytes())
            if input_data.get('incidents'):raise ValueError('Resolve retained acquisition incidents before promotion')
            entries=[]
            for r in input_data['entries']:
                path=(inbox.parent/r['zip_path']).resolve();ensure_space(self.root,256*1024**2)
                if path.stat().st_size>100*1024**2:raise ValueError('ZIP exceeds 100 MiB')
                entries.append(dict(r,setid=str(UUID(r['setid'])),zip_path=str(path),sha256=sha(path)))
            identity_input={'entries':sorted(entries,key=lambda r:(r['setid'],r['version'],r['sha256'])),'parser_version':PARSER_VERSION,'section_codes':sorted(self.config['section_codes']),'search_projection':self.config.get('search_projection','inherit')}
            if 'admission_scope_path' in self.config:identity_input['admission_scope_sha256']=self.config['admission_scope_sha256']
            if not self.config.get('promote',True):identity_input['promote']=False
            identity=hashlib.sha256(dumps(identity_input).encode()).hexdigest()
            job=self.root/'jobs'/identity;job.mkdir(parents=True,exist_ok=True)
            if (job/'DONE.json').exists():
                if 'admission_scope_path' in self.config and (job/'SCOPE.json').read_bytes()!=admitted_raw:
                    raise ValueError('Frozen admission scope changed')
                done=json.loads((job/'DONE.json').read_bytes())
                current=json.loads(self.parent().read_bytes())['snapshot']
                selected=done.get('snapshot')==current
                return dict(done,reused=True,promoted=False,currently_selected=selected,
                            state=('already-selected' if selected else 'retained') if done.get('snapshot') else done['state'])
            if (job/'BUILD-INPUT.json').exists():
                prepared=json.loads((job/'BUILD-INPUT.json').read_bytes())
                return self._build(job,identity,**prepared)
            if (job/'QUARANTINE.json').exists():
                incidents=json.loads((job/'QUARANTINE.json').read_bytes())
                if incidents:return {'state':'quarantined','job':identity,'incidents':incidents,'promoted':False,'reused':True}
            parent_path=self.parent();config=json.loads(parent_path.read_bytes());config['_build_search_projection']=self.config.get('search_projection','search_index' in config);parent_root=(parent_path.parent/config['release_root']).resolve()
            parent_raw=(parent_root/'reports/EXPANDED-SELECTION.json').read_bytes().removesuffix(b'\n')
            if hashlib.sha256(parent_raw).hexdigest()!=config['snapshot']:raise ValueError('Parent selection pin mismatch')
            parent=json.loads(parent_raw);selected={(r['setid'],r['version']):r for r in parent['versions']}
            parent_cache=json.loads((parent_root/'export-payloads/INDEX.json').read_bytes())
            pin=json.loads((parent_root/'reports/NATIVE-INDEX.json').read_bytes())
            if parent_cache['expanded_snapshot']!=config['snapshot'] or sha(parent_root/'export-payloads/INDEX.json')!=pin['cache_index_sha256']:raise ValueError('Parent cache pin mismatch')
            scope=admitted_path;names=admitted_records
            if 'admission_scope_path' in self.config:
                # A candidate keeps an exact, immutable copy rather than a
                # mutable operator path that could invalidate old citations.
                scope=job/'SCOPE.json'
                if scope.exists() and scope.read_bytes()!=admitted_raw:raise ValueError('Frozen admission scope changed')
                if not scope.exists():
                    with scope.open('xb') as f:f.write(admitted_raw);f.flush();os.fsync(f.fileno())
            c=connect(job/'source/data/gitrx.sqlite');accepted=[];quarantine=[];updates=[];groups={}
            for r in entries:groups.setdefault((r['setid'],r['version']),[]).append(r)
            try:
                for key,rows in sorted(groups.items()):
                    sid,version=key
                    if sid not in names or type(version) is not int or version<1:
                        quarantine.append({'identity':key,'reason':'Outside pinned scope or invalid version'});continue
                    c.execute('INSERT OR IGNORE INTO families VALUES(?,?,?)',(sid,names[sid]['title'],'{}'))
                    c.execute('INSERT OR IGNORE INTO versions(setid,version) VALUES(?,?)',key)
                    vid=c.execute('SELECT id FROM versions WHERE setid=? AND version=?',key).fetchone()[0]
                    observations=[]
                    for row in rows:
                        observation={'url':'file:'+row['zip_path'],'retrieved_at':now(),'status':200,'headers':[]}
                        if row.get('source_observation') is not None:
                            observed=dict(row['source_observation'])
                            if observed.get('sha256')!=row['sha256'] or observed.get('status')!=200 or observed.get('error'):
                                raise ValueError('Source observation must pin successful matching ZIP bytes')
                            if not isinstance(observed.get('url'),str) or not isinstance(observed.get('retrieved_at'),str):raise ValueError('Source observation requires URL and retrieval time')
                            observation={k:observed[k] for k in ('url','retrieved_at','status','headers','final_url','error','attempt') if k in observed}
                            observation.setdefault('headers',[])
                            if isinstance(observation['headers'],dict):observation['headers']=list(observation['headers'].items())
                        rid=Fetcher(c,job/'source',None).record(observation,Path(row['zip_path']).read_bytes(),'label',vid)
                        if row.get('source_observation') is not None:
                            c.execute('CREATE TABLE IF NOT EXISTS job_source_observations(retrieval_id INTEGER PRIMARY KEY,observation_json TEXT NOT NULL)')
                            c.execute('INSERT OR IGNORE INTO job_source_observations VALUES(?,?)',(rid,dumps(row['source_observation'])))
                        checked=validate_label(c,job/'source',rid,sid,version)
                        observations.append((row,rid,checked))
                    if len({r['sha256'] for r in rows})>1 or any(v['state']!='downloaded' for _,_,v in observations):
                        quarantine.append({'identity':key,'reason':'Competing bytes or invalid XML/ZIP identity','hashes':[r['sha256'] for r in rows]});continue
                    row,rid,checked=observations[0]
                    publication_observations=self.publication_observations(rows)
                    if key in selected:
                        if selected[key]['selected_sha256']!=row['sha256']:
                            quarantine.append({'identity':key,'reason':'Competing bytes against pinned parent','sha256':row['sha256']});continue
                        previous={(o.get('published_date'),o.get('published_raw')) for o in selected[key]['publication_observations']}
                        additions=[dict(o,job=identity) for o in publication_observations if (o['published_date'],o['published_raw']) not in previous]
                        if additions:
                            updated=dict(selected[key],publication_observations=selected[key]['publication_observations']+additions)
                            selected[key]=updated;updates.append(key)
                        continue
                    c.execute("UPDATE versions SET state='downloaded',selected_sha256=?,xml_member=?,effective_date=? WHERE id=?",(row['sha256'],checked['xml_member'],checked['effective_date'],vid));c.commit()
                    v=c.execute('SELECT * FROM versions WHERE id=?',(vid,)).fetchone()
                    persist_document(c,job/'source',v,set(self.config['section_codes']))
                    v=dict(c.execute('SELECT * FROM versions WHERE id=?',(vid,)).fetchone())
                    parses=[dict(p) for p in c.execute('SELECT * FROM parse_runs WHERE version_id=? ORDER BY id',(vid,))]
                    if len(parses)!=1 or parses[0]['status'] not in ('complete','partial'):
                        quarantine.append({'identity':key,'reason':'Failed or ambiguous parse'});continue
                    v['parse_runs']=parses;v['publication_observations']=publication_observations
                    v['retrievals']=[dict(r) for r in c.execute('SELECT * FROM retrievals WHERE version_id=?',(vid,))];accepted.append(v)
                atomic(job/'QUARANTINE.json',quarantine)
                if quarantine:
                    c.commit();return {'state':'quarantined','job':identity,'incidents':quarantine,'promoted':False}
                if not accepted and not updates:
                    result={'state':'no-change','job':identity,'config':str(parent_path),'promoted':False};atomic(job/'DONE.json',result);return result
                raw=dumps({'format':'gitrx-job-evidence-1','versions':accepted,'parser_version':PARSER_VERSION,'job':identity})
                source_snapshot=hashlib.sha256(raw.encode()).hexdigest()
                c.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?,?)',(source_snapshot,now(),raw));c.commit();c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            finally:c.close()
            prefix='job'+identity[:16];sources=dict(parent['sources']);sources[prefix]={'root':str(job/'source'),'snapshot':source_snapshot,'database_sha256':sha(job/'source/data/gitrx.sqlite')}
            for v in accepted:
                v=dict(v,id=prefix+':'+str(v['id']),parse_runs=[dict(p,id=prefix+':'+str(p['id'])) for p in v['parse_runs']]);selected[(v['setid'],v['version'])]=v
            new_families={v['setid'] for v in accepted}-{v['setid'] for v in parent['versions']}
            unknown_histories=sorted(set(parent.get('upstream_history_unknown_families',[]))|new_families)
            manifest={'format':'gitrx-expanded-release-1','versions':[selected[k] for k in sorted(selected)],'sources':sources,
                      'scope_sha256':sha(scope),'parent_selection_sha256':config['snapshot'],'parser_version':PARSER_VERSION,
                      'growth_policy':'immutable-separately-tagged-expanded-snapshots','historical_completeness_claim':False,'upstream_history_unknown_families':unknown_histories,'known_target_failures':None if new_families else parent.get('known_target_failures'),'job':identity,'parser_configuration':{'version':PARSER_VERSION,'section_codes_sha256':hashlib.sha256(dumps(sorted(self.config['section_codes'])).encode()).hexdigest()}}
            prepared={'manifest':manifest,'accepted':accepted,'parent_cache':parent_cache,'parent_root':str(parent_root),'scope':str(scope),'config':config,'updates':updates}
            atomic(job/'BUILD-INPUT.json',prepared)
            return self._build(job,identity,**prepared)

    def _build(self,job,identity,manifest,accepted,parent_cache,parent_root,scope,config,updates):
        parent_root=Path(parent_root);scope=Path(scope);prefix='job'+identity[:16]
        if sha(scope)!=manifest['scope_sha256']:raise ValueError('Frozen candidate scope pin mismatch')
        raw=dumps(manifest);snapshot=hashlib.sha256(raw.encode()).hexdigest();release=self.root/'releases'/snapshot;release.mkdir(parents=True,exist_ok=True)
        atomic(job/'CHECKPOINT.json',{'state':'building','snapshot':snapshot,'parent':config['snapshot']})
        operation_progress(self.root,self._operation_id,'payloads',{'job':identity,'snapshot':snapshot})
        payloads=release/'native/export-payloads';payloads.mkdir(parents=True,exist_ok=True);cache={'expanded_snapshot':snapshot,'entries':{}}
        for v in manifest['versions']:
            key=v['parse_runs'][0]['id']
            if key in parent_cache['entries']:
                entry=parent_cache['entries'][key];source=parent_root/'export-payloads'/entry['path'];target=payloads/entry['path'];target.parent.mkdir(parents=True,exist_ok=True)
                if not target.exists():
                    try:os.link(source,target)
                    except OSError as exc:
                        import errno
                        if exc.errno!=errno.EXDEV:raise
                        ensure_space(target.parent,source.stat().st_size);shutil.copyfile(source,target)
                cache['entries'][key]=entry
            else:
                number=int(key.split(':',1)[1]);version_id=int(v['id'].split(':',1)[1])
                with readonly(job/'source/data/gitrx.sqlite') as source:
                    body={name:[dict(r) for r in source.execute(sql,(number,))] for name,sql in query_recipe(Path(__file__).parents[1]).items()}
                from gitrx.current.db import dumps as canonical
                encoded=canonical(body).encode();relative=prefix+'/'+str(number)+'.json.gz';target=payloads/relative;target.parent.mkdir(parents=True,exist_ok=True)
                if not target.exists():target.write_bytes(gzip.compress(encoded,mtime=0))
                cache['entries'][key]={'path':relative,'sha256':hashlib.sha256(encoded).hexdigest()}
        atomic(payloads/'INDEX.json',cache);reports=release/'native/reports';reports.mkdir(parents=True,exist_ok=True)
        (reports/'EXPANDED-SELECTION.json').write_text(raw+'\n');build_index(release/'native',scope)
        base=Base(manifest,payloads,cache);export=release/'export';shards={};tag='release-'+snapshot
        try:
            operation_progress(self.root,self._operation_id,'git-export',{'job':identity,'snapshot':snapshot})
            for p in '0123456789abcdef':
                repo=export/'shards'/p
                if repo.exists() and subprocess.run(['git','-C',str(repo),'rev-parse','--verify','refs/tags/'+tag],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0:
                    shards[p]={'head':git(repo,'rev-parse','refs/tags/'+tag),'tree':git(repo,'rev-parse','refs/tags/'+tag+'^{tree}')};git(repo,'symbolic-ref','HEAD','refs/heads/build/'+tag);continue
                if repo.exists():repo.rename(repo.with_name(p+'-interrupted-'+str(time.time_ns())))
                view=ShardView(base,p)
                if not view.versions:raise ValueError('Scope requires at least one captured version in every shard')
                built=export_git(view,self.root,repo,view.snapshot,tag);shards[p]={k:built[k] for k in ('head','tree')};git(repo,'symbolic-ref','HEAD','refs/heads/build/'+tag)
            meta=export/'meta'
            if meta.exists() and subprocess.run(['git','-C',str(meta),'rev-parse','--verify','refs/tags/'+tag],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode:
                meta.rename(meta.with_name('meta-interrupted-'+str(time.time_ns())))
            if not meta.exists():
                meta.mkdir();git(meta,'init','-q');atomic(meta/'manifest.json',{'expanded_snapshot':snapshot,'parent_snapshot':config['snapshot'],'scope_sha256':sha(scope),'shards':shards})
                (meta/'.gitmodules').write_text(''.join(f'[submodule "{p}"]\n\tpath = shards/{p}\n\turl = ../shards/{p}\n' for p in shards))
                git(meta,'add','manifest.json','.gitmodules')
                for p,row in shards.items():git(meta,'update-index','--add','--cacheinfo','160000,'+row['head']+',shards/'+p)
                tree=git(meta,'write-tree');env=dict(os.environ,GIT_AUTHOR_NAME='GitRx',GIT_AUTHOR_EMAIL='archive@gitrx.invalid',GIT_COMMITTER_NAME='GitRx',GIT_COMMITTER_EMAIL='archive@gitrx.invalid',GIT_AUTHOR_DATE='2000-01-01T00:00:00Z',GIT_COMMITTER_DATE='2000-01-01T00:00:00Z')
                head=subprocess.check_output(['git','-C',str(meta),'commit-tree',tree,'-m','Native release '+snapshot],env=env,text=True).strip();git(meta,'update-ref','refs/tags/'+tag,head)
            head=git(meta,'rev-parse','refs/tags/'+tag)
            git(meta,'update-ref','refs/heads/release',head);git(meta,'symbolic-ref','HEAD','refs/heads/release')
            backend={'format':'gitrx-backend-config-1','backend':'git','snapshot':snapshot,'meta_commit':head,'export_root':str(export),'release_root':str(release/'native'),'scope_path':str(scope)}
            operation_progress(self.root,self._operation_id,'evidence-metadata',{'job':identity,'snapshot':snapshot})
            from .evidence_metadata import build as build_metadata
            metadata_pin=build_metadata(release/'native',release/'evidence-metadata',snapshot)
            backend.update(evidence_metadata=str(release/'evidence-metadata/evidence.sqlite'),
                           evidence_metadata_sha256=metadata_pin['sha256'])
            atomic(release/'backend.json',backend)
            if config.get('_build_search_projection','search_index' in config):
                operation_progress(self.root,self._operation_id,'search-projection',{'job':identity,'snapshot':snapshot})
                from .search_index import build as build_search, Projection
                target=release/'search-projection'
                if not target.exists():
                    staged=release/('search-projection-building-'+str(time.time_ns()))
                    search_pin=build_search(release/'backend.json',staged)
                    os.rename(staged,target)
                else:search_pin=json.loads((target/'PIN.json').read_bytes())
                # A retained completed projection must match this exact candidate.
                checked=Projection(target/'search.sqlite',search_pin['sha256'],snapshot);checked.close()
                backend.update(search_index=str(target/'search.sqlite'),search_index_sha256=search_pin['sha256'])
                atomic(release/'backend.json',backend)
            operation_progress(self.root,self._operation_id,'reader-verification',{'job':identity,'snapshot':snapshot})
            with open_reader(release/'backend.json','sqlite') as sql,open_reader(release/'backend.json','git') as g:
                changed={(v['setid'],v['version']) for v in accepted}|{tuple(x) for x in updates}
                for sid,version in sorted(changed):
                    if sql.read(sid,version)[0]!=g.read(sid,version)[0]:raise ValueError('Changed release text parity failure')
                    a=next(v for v in sql.versions(sid) if v['version']==version)
                    b=next(v for v in g.versions(sid) if v['version']==version)
                    if a!={k:v for k,v in b.items() if k!='commit'}:raise ValueError('Changed release metadata parity failure')
            atomic(release/'COMPLETE.json',{'snapshot':snapshot,'parent':config['snapshot'],'versions':len(manifest['versions']),'job':identity})
            if not self.config.get('promote',True):
                result={'state':'staged','job':identity,'snapshot':snapshot,'new_versions':len(accepted),'date_updates':len(updates),'config':str(release/'backend.json'),'promoted':False}
                atomic(job/'DONE.json',result);return result
            live=json.loads(self.parent().read_bytes())
            if live['snapshot'] not in (config['snapshot'],snapshot):raise ValueError('Parent changed; rebase this cycle before promotion')
            operation_progress(self.root,self._operation_id,'selection',{'job':identity,'snapshot':snapshot})
            atomic(self.root/'current.json',backend)
            result={'state':'promoted','job':identity,'snapshot':snapshot,'new_versions':len(accepted),'date_updates':len(updates),'config':str(release/'backend.json'),'promoted':True}
            atomic(job/'DONE.json',result);return result
        finally:base.close()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True,type=Path);sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('cycle');a.add_argument('--inbox',required=True,type=Path)
    a=sub.add_parser('refresh');a.add_argument('--setid',action='append',required=True)
    a=sub.add_parser('run');a.add_argument('--setid',action='append',required=True)
    sub.add_parser('status')
    a=sub.add_parser('activate');a.add_argument('--target',required=True,type=Path);a.add_argument('--expect-snapshot',required=True)
    a=p.parse_args();job=Job(a.config)
    if a.command=='activate':result=job.activate(a.target,a.expect_snapshot)
    elif a.command=='cycle':result=job.cycle(a.inbox)
    elif a.command in ('refresh','run'):
        result=job.refresh(a.setid)
        if a.command=='run' and not result['incidents']:result=job.cycle(result['inbox'])
    else:result=job.status()
    print(json.dumps(result,indent=2))
    if result.get('state')=='quarantined' or result.get('incidents'):raise SystemExit(2)
if __name__=='__main__':main()
