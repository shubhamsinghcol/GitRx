"""Bounded CPU-only extraction for the resumable backfill coordinator.

Workers use the maintained extraction engine and never write source SQLite. RSS
limits are monitored thresholds, not macOS address-space or P/E-core reservations.
Only an acknowledged durable native-version checkpoint permits spool cleanup.
"""
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from concurrent.futures.process import BrokenProcessPool
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import threading
import time
import zipfile

from gitrx.current.db import dumps
from gitrx.current.spl_parser_v1 import CONFIG, PARSER_VERSION
from gitrx_services.release1_parse_pool import extraction, initialize_worker
from gitrx_services.release1_http import Stop
from .jobs import atomic, sha

GIB=1024**3
MIB=1024**2


class ProcessingFailure(RuntimeError):
    """Raw inputs remain available; parsing cannot safely continue in this pool."""


def settings(policy):
    workers=policy.get('max_workers',policy.get('workers',6))
    worker_rss=policy.get('worker_RSS_limit_bytes',min(GIB,6*GIB//workers) if type(workers) is int and workers>0 else GIB)
    coordinator=policy.get('coordinator_allowance_bytes',2*GIB)
    total=policy.get('total_RSS_budget_bytes',8*GIB)
    queue=policy.get('queue_limit',workers*2)
    estimate=policy.get('queue_inflated_estimate_budget_bytes',1536*MIB)
    deadline=policy.get('parser_task_timeout_seconds',180)
    if type(deadline) is not int or not 30<=deadline<=600:raise ValueError('Parser task deadline must be 30..600 seconds')
    if type(workers) is not int or not 1<=workers<=12:
        raise ValueError('Processing workers must be 1..12')
    for value,maximum,name in ((worker_rss,GIB,'worker RSS'),(coordinator,2*GIB,'coordinator RSS'),
                                (total,8*GIB,'aggregate RSS'),(estimate,1536*MIB,'queued inflation')):
        if type(value) is not int or not 1<=value<=maximum:raise ValueError('Invalid '+name+' budget')
    if workers*worker_rss+coordinator>total:
        raise ValueError('Configured worker/coordinator allowances exceed aggregate budget')
    if type(queue) is not int or not 1<=queue<=24:raise ValueError('Processing queue must be 1..24')
    return {'workers':workers,'worker_RSS_limit_bytes':worker_rss,
            'coordinator_allowance_bytes':coordinator,'total_RSS_budget_bytes':total,
            'queue_limit':queue,'queue_inflated_estimate_budget_bytes':estimate,
            'max_tasks_per_child':25,'max_retries':3,'start_method':'spawn','parser_task_timeout_seconds':deadline}


class ProcessingPool:
    def __init__(self,root,policy,commit_callback,guard_callback,heartbeat_callback):
        self.root=Path(root).absolute();self.options=settings(policy)
        self.commit_callback=commit_callback;self.guard_callback=guard_callback
        self.heartbeat_callback=heartbeat_callback
        explicit=policy.get('processing_spool_root')
        self.spool=Path(explicit) if explicit else self.root/'processing-spool'
        if not self.spool.is_absolute() or len(self.spool.parts)<3 or self.spool in (Path.home(),self.root):
            raise ProcessingFailure('Processing spool requires a dedicated absolute directory')
        self.status_path=self.root/'processing-status.json'
        if self.spool.is_symlink():raise ProcessingFailure('Derived spool symlink forbidden')
        if not explicit and not self.spool.resolve().is_relative_to(self.root.resolve()):
            raise ProcessingFailure('Derived spool must remain inside its workspace')
        self.spool_byte_cap=policy.get('processing_spool_byte_cap',3*GIB)
        if type(self.spool_byte_cap) is not int or not 1<=self.spool_byte_cap<=3*GIB:
            raise ValueError('Processing spool cap must be at most 3 GiB')
        self.parser_config_sha256=hashlib.sha256(dumps(CONFIG).encode()).hexdigest()
        self.pending={};self.executor=None;self.recycles=0;self.committed=0;self.committed_keys=set()
        self._stop=threading.Event();self._thread=None;self._fault=None
        self._lock=threading.RLock();self.last_memory={};self.last_memory_epoch=0;self.last_publish=0
        self.guard_callback();self.publish('ready')

    def _processes(self):
        executor=self.executor
        return list(executor._processes.values()) if executor and executor._processes else []

    def _pids(self):
        return [p.pid for p in self._processes() if p.is_alive()]

    def _rss_snapshot(self):
        pids=[os.getpid()]+self._pids()
        try:
            from multiprocessing.resource_tracker import _resource_tracker
            if _resource_tracker._pid:pids.append(_resource_tracker._pid)
        except (ImportError,AttributeError):pass
        proc=subprocess.run(['ps','-o','pid=,rss=','-p',','.join(map(str,set(pids)))],
                            capture_output=True,text=True,timeout=3)
        if proc.returncode:raise ProcessingFailure('Cannot measure processing RSS')
        values={}
        for line in proc.stdout.splitlines():
            parts=line.split()
            if len(parts)==2 and all(x.isdigit() for x in parts):values[int(parts[0])]=int(parts[1])*1024
        if os.getpid() not in values:raise ProcessingFailure('Coordinator RSS measurement missing')
        workers={pid:values[pid] for pid in self._pids() if pid in values}
        coordinator=sum(v for pid,v in values.items() if pid not in workers)
        return {'total_RSS_bytes':sum(values.values()),'coordinator_RSS_bytes':coordinator,
                'worker_RSS_bytes_by_pid':workers}

    def _observe_memory(self,max_age=0):
        # The independent watchdog still samples every 250 ms. Reuse equally
        # fresh readings instead of forking ps before/after every tiny task.
        with self._lock:
            if max_age and self.last_memory and time.monotonic()-self.last_memory_epoch<max_age:
                values=self.last_memory
            else:
                values=self._rss_snapshot();self.last_memory=values
                self.last_memory_epoch=time.monotonic()
        if values['coordinator_RSS_bytes']>self.options['coordinator_allowance_bytes']:
            return 'coordinator_RSS_budget'
        if values['total_RSS_bytes']>self.options['total_RSS_budget_bytes']:
            return 'aggregate_RSS_budget'
        if any(v>self.options['worker_RSS_limit_bytes'] for v in values['worker_RSS_bytes_by_pid'].values()):
            return 'worker_RSS_budget'
        return None

    def _spool_bytes(self):
        total=0;seen=set()
        for path in self.spool.rglob('*') if self.spool.exists() else ():
            try:
                if path.is_symlink():raise ProcessingFailure('Derived spool symlink forbidden')
                if not path.is_file():continue
                stat=path.stat();key=(stat.st_dev,stat.st_ino)
                if key not in seen:seen.add(key);total+=stat.st_size
            except FileNotFoundError:continue
        return total

    def _monitor(self):
        while not self._stop.wait(.25):
            if self.executor is None:continue
            try:reason=self._observe_memory()
            except Exception as exc:reason='RSS_monitor_failed:'+type(exc).__name__
            if reason:
                with self._lock:
                    if self._fault is None:self._fault=reason
                # Kill only this executor's tracked children. The coordinator
                # raises on its next check and never falls back to local parsing.
                for process in self._processes():
                    if process.is_alive():process.terminate()

    def _start(self):
        if self.executor is None:
            self.guard_callback()
            if self._spool_bytes()>self.spool_byte_cap:raise ProcessingFailure('Processing spool byte cap reached; raw evidence retained')
            self.executor=ProcessPoolExecutor(max_workers=self.options['workers'],
                mp_context=multiprocessing.get_context('spawn'),initializer=initialize_worker,
                max_tasks_per_child=self.options['max_tasks_per_child'])
        if self._thread is None:
            self._thread=threading.Thread(target=self._monitor,daemon=True,name='gitrx-processing-RSS')
            self._thread.start()

    def _heartbeat(self):
        self.guard_callback();self.heartbeat_callback()

    def _check_memory(self):
        with self._lock:reason=self._fault
        if not reason:
            reason=self._observe_memory(max_age=.25)
        if not reason:return
        if reason=='coordinator_RSS_budget' or reason.startswith('RSS_monitor_failed'):
            self._terminate();raise ProcessingFailure(reason+'; raw evidence retained')
        self._recycle(reason)

    def estimate(self,path,version):
        with zipfile.ZipFile(path) as archive:size=archive.getinfo(version['xml_member']).file_size
        return min(1536*MIB,max(16*MIB,size*8))

    def publish(self,phase,**extra):
        self.guard_callback()
        data={'format':'gitrx-processing-status-1','phase':phase,'workers_configured':self.options['workers'],
              'worker_pids':self._pids(),'active_workers':len(self._pids()),
              'pending_versions':len(self.pending),'queue_estimated_bytes':sum(j['estimate'] for j in self.pending.values()),
              'worker_RSS_limit_bytes':self.options['worker_RSS_limit_bytes'],
              'coordinator_allowance_bytes':self.options['coordinator_allowance_bytes'],
              'total_RSS_budget_bytes':self.options['total_RSS_budget_bytes'],
              'pool_recycles':self.recycles,'committed_versions':self.committed,
              'parser_config_sha256':self.parser_config_sha256,
              'processing_spool_root':str(self.spool),'processing_spool_byte_cap':self.spool_byte_cap,
              'memory_enforcement':'250ms worker/aggregate RSS observation; fail closed on observed limit breach',
              'cpu_assignment':'OS scheduled; no fixed P/E core affinity','updated_epoch':time.time(),
              **self.last_memory,**extra}
        atomic(self.status_path,data);self.last_publish=time.monotonic()
        return data

    def _terminate(self):
        old=self.executor;self.executor=None
        processes=list(old._processes.values()) if old and old._processes else []
        if old:old.shutdown(wait=False,cancel_futures=True)
        for process in processes:
            if process.is_alive():process.terminate()
        for process in processes:
            process.join(timeout=2)
            if process.is_alive():process.kill();process.join(timeout=2)

    def _recycle(self,reason):
        jobs=list(self.pending.values());self.pending={};self._terminate()
        self.recycles+=1
        if 'RSS' in reason:self.options['workers']=max(1,self.options['workers']//2)
        with self._lock:self._fault=None
        for job in jobs:
            job['tries']+=1
            if job['tries']>self.options['max_retries']:
                if reason not in ('worker_process_or_memory_failure','worker_task_deadline','worker_RSS_budget'):
                    self.publish('needs-attention',reason=reason)
                    raise ProcessingFailure('Parser worker retries exhausted; raw/spool retained: '+reason)
                self._commit(job,{'extraction_error':{'type':'WorkerResourceFailure','message':reason+' after bounded retries'}})
        jobs=[job for job in jobs if job['tries']<=self.options['max_retries']]
        if not jobs:return
        self._start()
        for job in jobs:
            job['submitted']=time.monotonic()
            self.pending[self.executor.submit(extraction,*job['args'])]=job
        self.publish('recycling',reason=reason)

    def _saved_prepared(self,version):
        leaf=self.spool/version['selected_sha256'][:2]/version['selected_sha256']/self.parser_config_sha256
        marker=leaf/'manifest.json'
        if not marker.exists():return None
        value=json.loads(marker.read_bytes());value['payload_path']=str(leaf/'payload.pickle')
        self._verify_prepared(version,value)
        return value

    def _verify_prepared(self,version,prepared):
        leaf=self.spool/version['selected_sha256'][:2]/version['selected_sha256']/self.parser_config_sha256
        payload=Path(prepared['payload_path'])
        if payload.is_symlink() or payload.resolve()!=((leaf/'payload.pickle').resolve()) or not payload.resolve().is_relative_to(self.spool.resolve()):
            raise ProcessingFailure('Processing spool path escaped its derived workspace')
        marker=leaf/'manifest.json'
        if marker.is_symlink():raise ProcessingFailure('Processing spool manifest symlink forbidden')
        saved=json.loads(marker.read_bytes())
        if (saved['raw_sha256']!=version['selected_sha256'] or saved['parser_config_sha256']!=self.parser_config_sha256
            or saved['payload_sha256']!=prepared['payload_sha256'] or sha(payload)!=saved['payload_sha256']):
            raise ProcessingFailure('Processing spool integrity check failed')
        return leaf

    def _commit(self,job,prepared):
        self._heartbeat();self._check_memory()
        failure=isinstance(prepared,dict) and 'extraction_error' in prepared
        if prepared is not None and not failure:self._verify_prepared(job['version'],prepared)
        ack=self.commit_callback(job['c'],job['root'],job['version'],job['registry'],prepared,job['details'])
        if (not isinstance(ack,dict) or ack.get('durable') is not True or not isinstance(ack.get('version_pin'),str)
            or len(ack['version_pin'])!=64 or any(ch not in '0123456789abcdef' for ch in ack['version_pin'])):
            raise ProcessingFailure('Coordinator did not acknowledge a durable version pin; spool retained')
        if job['c'].in_transaction:raise ProcessingFailure('Coordinator transaction remains uncommitted; spool retained')
        if job['c'].execute('PRAGMA synchronous').fetchone()[0]<2:
            raise ProcessingFailure('Coordinator SQLite must use FULL durability; spool retained')
        row=job['c'].execute('SELECT pin FROM backfill_versions WHERE version_id=?',(job['version']['id'],)).fetchone()
        if row is None or row[0]!=ack['version_pin']:
            raise ProcessingFailure('Durable version pin does not match acknowledgment; spool retained')
        if job['key'] not in self.committed_keys:self.committed+=1;self.committed_keys.add(job['key'])
        if prepared is not None and not failure:self._clean(job['version'],prepared)
        self._check_memory()

    def _clean(self,version,prepared):
        leaf=self._verify_prepared(version,prepared)
        for path in leaf.iterdir():
            if path.name in ('manifest.json','payload.pickle') or (path.name.startswith('payload-') and path.name.endswith('.partial')):
                if path.is_dir():raise ProcessingFailure('Unexpected directory in derived spool')
                path.unlink()
        for folder in (leaf,leaf.parent,leaf.parent.parent):
            try:folder.rmdir()
            except OSError:break
        directory=os.open(self.spool if self.spool.exists() else self.root,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)

    def submit(self,c,source_root,version,registry,details):
        self._heartbeat();self._check_memory();version=dict(version);source_root=Path(source_root)
        if self._spool_bytes()>self.spool_byte_cap:raise ProcessingFailure('Processing spool byte cap reached; raw evidence retained')
        key=(str(source_root.absolute()),version['id'])
        if any(j['key']==key for j in self.pending.values()):return False
        # The old extractor shares spool leaves by raw/config hash. Avoid two
        # processes racing to create the exact same prepared leaf.
        if any(j['version']['selected_sha256']==version['selected_sha256'] for j in self.pending.values()):self.drain(all=True)
        artifact=c.execute('SELECT path FROM artifacts WHERE sha256=?',(version['selected_sha256'],)).fetchone()
        if artifact is None:raise ProcessingFailure('Raw parse artifact missing')
        path=source_root/artifact[0]
        if path.resolve().is_relative_to(self.spool.resolve()):raise ProcessingFailure('Raw input points inside derived spool')
        if sha(path)!=version['selected_sha256']:raise ProcessingFailure('Raw parse input hash changed')
        cost=self.estimate(path,version)
        job={'key':key,'c':c,'root':source_root,'version':version,'registry':set(registry),
             'details':dict(details),'estimate':cost,'tries':0,
             'args':(str(path),version,sorted(registry),str(self.spool),True),'submitted':time.monotonic()}
        existing=c.execute('SELECT id FROM parse_runs WHERE version_id=? AND artifact_sha256=? AND parser_version=? AND config_json=?',
                           (version['id'],version['selected_sha256'],PARSER_VERSION,dumps(CONFIG))).fetchone()
        if existing:
            self._commit(job,self._saved_prepared(version));self.publish('processing');return True
        while self.pending and (len(self.pending)>=self.options['queue_limit'] or sum(j['estimate'] for j in self.pending.values())+cost>self.options['queue_inflated_estimate_budget_bytes']):
            self.drain(block=True)
        self._start();self.pending[self.executor.submit(extraction,*job['args'])]=job
        self.publish('processing');return True

    def drain(self,all=False,block=False):
        while self.pending:
            self._heartbeat();self._check_memory()
            if any(not future.done() and time.monotonic()-job.get('submitted',time.monotonic())>self.options['parser_task_timeout_seconds']
                   for future,job in self.pending.items()):
                self._recycle('worker_task_deadline')
                continue
            ready=[future for future in self.pending if future.done()]
            if not ready:
                if not all and not block:break
                wait(tuple(self.pending),timeout=.25,return_when=FIRST_COMPLETED)
                if time.monotonic()-self.last_publish>=2:self.publish('processing')
                continue
            recycled=False
            for future in ready:
                job=self.pending.pop(future)
                try:prepared=future.result()
                except Stop:
                    self.pending[future]=job;raise
                except (BrokenProcessPool,MemoryError):
                    self.pending[future]=job;self._recycle('worker_process_or_memory_failure');recycled=True;break
                except ValueError as exc:
                    # The native adapter records this source limitation as a
                    # failed parse, rather than asserting section absence.
                    # Replaying this early rejection creates no narrative tree.
                    if str(exc)!='No SPL sections; cannot establish section absence':
                        self.pending[future]=job
                        self._recycle('worker_error:ValueError');recycled=True;break
                    prepared=None
                except Exception as exc:
                    self.pending[future]=job
                    self._recycle('worker_error:'+type(exc).__name__);recycled=True;break
                self._commit(job,prepared)
            self.publish('processing')
            if not all and not recycled:break
        if not self.pending:self.publish('idle')

    def drain_family(self,sid,source_root=None):
        while any(j['version']['setid']==sid and (source_root is None or j['root']==Path(source_root)) for j in self.pending.values()):
            self.drain(block=True)

    def close(self,interrupted=False):
        try:
            if not interrupted:self.drain(all=True)
        finally:
            self._stop.set()
            if self._thread:self._thread.join(timeout=4)
            if interrupted:self._terminate()
            elif self.executor:
                self.executor.shutdown(wait=True,cancel_futures=True);self.executor=None
            self.publish('interrupted' if interrupted else 'closed')
