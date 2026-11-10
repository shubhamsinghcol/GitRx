"""Offline verified HTTP-ledger reconstruction; preserve damaged originals.

Never guesses missing label bytes or repairs parsed/source evidence silently.
Only the disposable HTTP index is rebuilt from SQLite recovery and independently
retained source observations, with all referenced original bodies hash-checked.
"""
import argparse
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time

from .acquisition_runner import volume_ready
from .jobs import atomic,sha,HTTP


class NetworkRepairRequired(RuntimeError):pass


def body_locations(root,digest):
    paths=[root/'network/bodies'/digest]
    policy=root/'LOCAL-STORAGE-POLICY.json'
    if policy.exists():
        p=json.loads(policy.read_bytes());local=p.get('raw_staging_root')
        if local:paths.append(Path(local)/'network/bodies'/digest)
        if p.get('archive_workspace'):paths.append(Path(p['archive_workspace'])/'network/bodies'/digest)
    return paths


def response_body(root,digest):
    paths=body_locations(root,digest)
    return next((p for p in paths if p.exists()),paths[0])


class BodyRepair:
    def __init__(self,root,config,allow_network):
        self.root=root;self.config=config;self.allow_network=allow_network
        self.incident=root/'incidents'/('raw-repair-'+str(time.time_ns()))
        self.http=None;self.events=[];self.body_count=0
        self.journal=None
        policy=root/'LOCAL-STORAGE-POLICY.json'
        if policy.exists():
            local=json.loads(policy.read_bytes())
            if local.get('raw_journal_root'):
                from .raw_journal import RawJournal
                self.journal=RawJournal(local['raw_journal_root'],local.get('raw_journal_bytes',512*1024**2))
    def guard(self):
        if not volume_ready(self.config):raise RuntimeError('Correct SSD unavailable')
    def ensure(self,body,digest,url,alternate=None):
        if body.exists() and sha(body)==digest:return
        self.guard();source=None
        if alternate and alternate.exists() and sha(alternate)==digest:source=alternate
        if source is None and self.journal:source=self.journal.lookup(digest)
        if source is None:
            if not self.allow_network:raise NetworkRepairRequired('Hash-exact raw body reacquisition requires connectivity')
            if self.body_count>=64:raise RuntimeError('Raw recovery body budget exhausted; review required')
            self.incident.mkdir(parents=True,exist_ok=True)
            if self.http is None:self.http=HTTP(self.incident/'network',cap=192,guard=self.guard,max_bytes=2*1024**3)
            try:source=self.http.get(url,reuse=True)
            except Exception as exc:
                if 'HTTP 0:' in str(exc) or 'Retry deferred' in str(exc) or 'timed out' in str(exc):
                    raise NetworkRepairRequired(str(exc)) from exc
                raise
            self.body_count+=1
            if sha(source)!=digest:raise RuntimeError('Reacquired bytes differ from original expected hash; never substituted')
        self.incident.mkdir(parents=True,exist_ok=True)
        self.replace(body,source,digest)
        self.events.append({'path':str(body),'expected_sha256':digest,'source':str(source),'verified_exact_bytes':True})
        atomic(self.incident/'VERIFIED-RAW-REPAIRS.json',{'events':self.events,'at_epoch':time.time()})
    def replace(self,path,source,digest):
        self.guard();path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists():
            if sha(path)==digest:return
            bad=self.incident/'damaged-objects'/sha(path);bad.parent.mkdir(exist_ok=True)
            if not bad.exists():
                from .backfill_staging import copy_verified
                copy_verified(path,bad,sha(path))
        temp=path.with_name(path.name+'.verified-link-'+str(time.time_ns()))
        if source.stat().st_dev==path.parent.stat().st_dev:os.link(source,temp)
        else:
            from .backfill_staging import copy_verified
            copy_verified(source,temp,digest)
        os.replace(temp,path)
        directory=os.open(path.parent,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
    def close(self):
        if self.http:self.http.close()
        if self.journal:self.journal.close()


def integrity(path):
    c=sqlite3.connect(Path(path).absolute().as_uri()+'?mode=ro',uri=True)
    try:
        result=[r[0] for r in c.execute('PRAGMA integrity_check')]
        if result!=['ok']:raise RuntimeError('SQLite integrity failure: '+str(path)+': '+str(result[:10]))
    finally:c.close()


def observation_rows(root,repair=None):
    observations={};sources=[]
    for database in sorted(root.glob('batches/*/data/gitrx.sqlite')):
        integrity(database)
        marker=database.parent.parent/'BATCH.json'
        if marker.exists():
            pin=json.loads(marker.read_bytes())
            if sha(database)!=pin['source_database_sha256']:raise RuntimeError('Frozen batch source hash changed')
            if sha(marker.parent/'inbox.json')!=pin['inbox_sha256']:raise RuntimeError('Frozen inbox changed')
        c=sqlite3.connect(database.absolute().as_uri()+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
        try:
            source_hash=sha(database)
            for record in c.execute('SELECT retrieval_id,observation_json FROM backfill_http'):
                obs=json.loads(record['observation_json'])
                if not obs.get('namespace','').startswith('backfill:'):continue
                r=c.execute('SELECT * FROM retrievals WHERE id=?',(record['retrieval_id'],)).fetchone()
                if r is None or r['artifact_sha256']!=obs['sha256'] or r['url']!=obs['url'] or r['status']!=obs['status'] or r['retrieved_at']!=obs['retrieved_at']:
                    raise RuntimeError('Preserved source observation projections disagree')
                headers=json.loads(r['headers_json'])
                if dict(headers)!=dict(obs['headers']):raise RuntimeError('Preserved source headers disagree')
                body=response_body(root,obs['sha256'])
                artifact=c.execute('SELECT path FROM artifacts WHERE sha256=?',(obs['sha256'],)).fetchone()
                alias=database.parent.parent/artifact[0] if artifact else None
                if repair:
                    repair.ensure(body,obs['sha256'],obs['url'],alias)
                    if alias and (not alias.exists() or sha(alias)!=obs['sha256']):repair.replace(alias,body,obs['sha256'])
                if Path(obs['path']).absolute() not in [p.absolute() for p in body_locations(root,obs['sha256'])] or sha(body)!=obs['sha256'] or body.stat().st_size!=obs['bytes']:
                    raise RuntimeError('Preserved response body missing/changed')
                ident=obs['id'];previous=observations.get(ident)
                value={'id':ident,'url':obs.get('final_url') or obs['url'],
                       'started':datetime.fromisoformat(obs['retrieved_at']).timestamp(),
                       'status':obs['status'],'sha256':obs['sha256'],'error':obs.get('error'),
                       'headers_json':json.dumps(dict(obs['headers'])),'bytes':obs['bytes']}
                if previous and previous['row']!=value:raise RuntimeError('HTTP ID has conflicting preserved observations')
                observations[ident]={'row':value,'source_database':str(database),'source_database_sha256':source_hash,
                    'observation_sha256':hashlib.sha256(record['observation_json'].encode()).hexdigest()}
            sources.append({'database':str(database),'sha256':source_hash})
        finally:c.close()
    return observations,sources


def validate_candidate(candidate,root,observations):
    integrity(candidate);c=sqlite3.connect(candidate);c.row_factory=sqlite3.Row
    try:
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables-{'requests','transport_state','ledger_reconstruction'}:raise RuntimeError('Recovery produced unexplained/lost-and-found tables')
        c.execute('CREATE TABLE IF NOT EXISTS ledger_reconstruction(id INTEGER PRIMARY KEY,source_json TEXT)')
        added=[]
        for ident,proof in sorted(observations.items()):
            old=c.execute('SELECT * FROM requests WHERE id=?',(ident,)).fetchone();expected=proof['row']
            if old:
                if any(old[k]!=expected[k] for k in ('url','status','sha256','error','bytes')) or abs(old['started']-expected['started'])>.00001:
                    raise RuntimeError('Recovered HTTP row conflicts with preserved source observation')
                if json.loads(old['headers_json'] or '{}')!=json.loads(expected['headers_json']):raise RuntimeError('Recovered headers conflict')
            else:
                c.execute('INSERT INTO requests(id,url,started,status,sha256,error,headers_json,bytes) VALUES(?,?,?,?,?,?,?,?)',tuple(expected[k] for k in ('id','url','started','status','sha256','error','headers_json','bytes')))
                c.execute('INSERT INTO ledger_reconstruction VALUES(?,?)',(ident,json.dumps(proof)))
                added.append(ident)
        rows=list(c.execute('SELECT * FROM requests ORDER BY id'))
        if [r['id'] for r in rows]!=list(range(1,len(rows)+1)):raise RuntimeError('Unexplained request-ID gap; manual review required')
        hashes=set()
        for row in rows:
            if not row['sha256']:
                if row['status'] not in (None,0):raise RuntimeError('Completed HTTP observation lacks bytes')
                continue
            body=response_body(root,row['sha256'])
            if not body.exists() or sha(body)!=row['sha256'] or body.stat().st_size!=row['bytes']:
                raise RuntimeError('Recovered HTTP body hash/size mismatch')
            hashes.add(row['sha256'])
        c.commit();c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        return {'requests':len(rows),'max_id':rows[-1]['id'] if rows else 0,
                'unique_referenced_bodies':len(hashes),'reconstructed_ids':added,
                'preserved_source_observations_checked':len(observations)}
    finally:c.close()


def ensure_ledger_bodies(ledger,root,repair):
    c=sqlite3.connect(ledger);c.row_factory=sqlite3.Row
    try:
        for row in c.execute('SELECT * FROM requests WHERE sha256 IS NOT NULL'):
            body=response_body(root,row['sha256'])
            if not body.exists() or sha(body)!=row['sha256']:
                if row['error'] or row['status'] not in (200,404,410):
                    raise RuntimeError('Unrecoverable failed-attempt prefix needs explicit review')
                repair.ensure(body,row['sha256'],row['url'])
            if body.stat().st_size!=row['bytes']:raise RuntimeError('HTTP body size changed')
    finally:c.close()


def recover(config_path,allow_network=False):
    config=json.loads(Path(config_path).read_bytes());root=Path(config['workspace'])
    if not volume_ready(config):raise RuntimeError('Correct SSD unavailable; no recovery writes attempted')
    with (root/'writer.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        integrity(root/'control.sqlite')
        repair=BodyRepair(root,config,allow_network)
        try:observations,sources=observation_rows(root,repair)
        finally:repair.close()
        ledger=(root/'network/ledger.sqlite').resolve()
        valid=True
        try:integrity(ledger)
        except (sqlite3.DatabaseError,RuntimeError):valid=False
        if valid:
            repair=BodyRepair(root,config,allow_network)
            try:ensure_ledger_bodies(ledger,root,repair)
            finally:repair.close()
            return {'state':'already-valid','source_databases_checked':len(sources)}
        incident=root/'incidents'/('http-recovery-'+str(time.time_ns()));incident.mkdir(parents=True)
        preserved=incident/'ledger-original.sqlite';shutil.copyfile(ledger,preserved)
        for suffix in ('-wal','-shm','-journal'):
            p=Path(str(ledger)+suffix)
            if p.exists():shutil.copyfile(p,Path(str(preserved)+suffix))
        with (incident/'recover.sql').open('wb') as output,(incident/'recover.stderr').open('wb') as errors:
            result=subprocess.run(['/usr/bin/sqlite3',str(preserved),'.recover'],stdout=output,stderr=errors)
        if result.returncode:raise RuntimeError('SQLite forensic recovery failed; original preserved at '+str(incident))
        candidate=incident/'ledger-candidate.sqlite'
        with (incident/'recover.sql').open('rb') as source:
            result=subprocess.run(['/usr/bin/sqlite3',str(candidate)],stdin=source,capture_output=True)
        if result.returncode:raise RuntimeError('Recovered schema import failed; original retained')
        repair=BodyRepair(root,config,allow_network)
        try:
            # Missing source-observation rows are added and all body hashes
            # verified by validate_candidate; other retained ledger bodies
            # are checked here before promotion as well.
            ensure_ledger_bodies(candidate,root,repair)
        finally:repair.close()
        result=validate_candidate(candidate,root,observations)
        result.update(format='gitrx-verified-http-recovery-1',state='verified-reconstructed',
                      original_sha256=sha(preserved),candidate_sha256=sha(candidate),
                      incident=str(incident),sources=sources,
                      qualification='All recoverable rows and referenced source observations verified. Missing rows reconstructed only from matching retained observations. Uncommitted activity may remain unknown.',at_epoch=time.time())
        atomic(incident/'VERIFIED.json',result)
        replacement=ledger.with_name(ledger.name+'.verified-replacement')
        if replacement.exists():raise RuntimeError('Unreviewed prior replacement exists')
        with candidate.open('rb') as source,replacement.open('xb') as target:
            shutil.copyfileobj(source,target);target.flush();os.fsync(target.fileno())
        if sha(replacement)!=result['candidate_sha256']:raise RuntimeError('Replacement copy hash mismatch')
        # No worker holds the writer lock. Original bytes/journals are preserved.
        for suffix in ('-wal','-shm','-journal'):Path(str(ledger)+suffix).unlink(missing_ok=True)
        os.replace(replacement,ledger)
        directory=os.open(ledger.parent,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)
        integrity(ledger)
        atomic(root/'network/RECOVERY-CURRENT.json',result)
        return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--allow-network-repair',action='store_true')
    args=p.parse_args()
    try:print(json.dumps(recover(args.config,args.allow_network_repair)),flush=True)
    except NetworkRepairRequired as exc:
        print(str(exc),flush=True);raise SystemExit(75)


if __name__=='__main__':main()
