"""Persistent recovery for one finite pinned backfill, with a durable user pause.

The guardian waits for resources; it never expands scope, promotes a release or
starts a fresh collection after the configured completion marker exists.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import time

from .acquisition_runner import volume_ready
from .jobs import atomic, sha

PROCESS_CLASS = subprocess.Popen
STORAGE_ERRORS = ('database disk image is malformed', 'file is not a database',
                  'malformed database schema','Cached response hash mismatch',
                  'Batch evidence or parsed checkpoint changed')


TRANSIENT = ('Retry deferred', 'HTTP 0:', 'HTTP 408:', 'HTTP 429:', 'HTTP 500:',
             'HTTP 502:', 'HTTP 503:', 'HTTP 504:', 'timed out', 'TimeoutError',
             'Temporary failure', 'URLError', 'Network is unreachable',
             'network is unreachable', 'ConnectionReset', 'Connection refused',
             'RemoteDisconnected', 'Broken pipe', 'nodename nor servname')
TRANSIENT += ('Input/output error','disk I/O error','unable to open database file','database is locked','Transient HTTP failure')


def network_ready():
    """TCP reachability only, not a labeling/API request or an extra dataset read."""
    try:
        with socket.create_connection(('dailymed.nlm.nih.gov', 443), timeout=3):
            return True
    except OSError:
        return False


def retry_deadline(path):
    if not path or not Path(path).is_file():
        return 0
    try:
        with sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True) as c:
            row=c.execute("SELECT value FROM transport_state WHERE key='retry_after'").fetchone()
            return float(row[0]) if row else 0
    except (sqlite3.Error, OSError, ValueError):
        return 0


def read_tail(path, offset=0, limit=16384):
    try:
        with Path(path).open('rb') as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(offset, f.tell()-limit))
            return f.read(limit).decode(errors='replace')
    except OSError:
        return ''


class Guardian:
    def __init__(self, config_path, policy_path=None):
        self.config_path=Path(config_path).absolute()
        self.config=json.loads(self.config_path.read_bytes())
        if self.config['format']!='gitrx-finite-preparation-1' or len(self.config['steps'])!=1:
            raise ValueError('Guardian requires one finite configured backfill step')
        self.step=self.config['steps'][0]
        self.support=Path(self.config['state_directory']).absolute()
        self.support.mkdir(parents=True, exist_ok=True)
        self.workspace=Path(self.config['workspace']).absolute()
        self.policy=json.loads(Path(policy_path).read_bytes()) if policy_path else {}
        if self.policy.get('mobile_workspace'):
            self.workspace=Path(self.policy['mobile_workspace']).absolute()
            self.step=dict(self.step,command=self.policy['worker_command'],complete=str(self.workspace/'COMPLETE.json'),
                retry_ledger=str(self.workspace/'network/ledger.sqlite'))
        recovery=self.policy.get('recovery_command')
        if recovery is not None and (not isinstance(recovery,list) or not recovery or
                                     not all(isinstance(x,str) and x for x in recovery)):
            raise ValueError('Recovery command must be a nonempty argument list')
        self.poll_seconds=float(self.policy.get('poll_seconds', 10))
        if not 0.1<=self.poll_seconds<=15:
            raise ValueError('Guardian polling must be 0.1..15 seconds')
        self.terminate_seconds=float(self.policy.get('terminate_seconds', 10))
        if not 0.1<=self.terminate_seconds<=15:
            raise ValueError('Guardian termination timeout must be 0.1..15 seconds')
        self.lock=(self.support/'guardian.lock').open('a+')
        fcntl.flock(self.lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
        self.state_path=self.support/'guardian-state.json'
        self.state=json.loads(self.state_path.read_bytes()) if self.state_path.exists() else {
            'format':'gitrx-backfill-guardian-1', 'config_sha256':sha(self.config_path),
            'created_epoch':time.time(), 'consecutive_failures':0, 'restarts':0}
        if self.state['config_sha256']!=sha(self.config_path):
            raise ValueError('Guardian configuration pin changed')
        self.child=None
        self.child_role=None
        self.power=None
        self.log=None
        self.log_path=self.support/(self.step['name']+'.log')
        self.current_log_path=self.log_path
        self.log_offset=0
        self.child_started=0
        self.next_start=0
        self.stopping=False
        self.last_signature=None
        self.last_status_epoch=None
        self.heartbeat_path=Path(self.policy.get('worker_heartbeat', self.workspace/'worker-heartbeat.json'))
        self.publish('starting')

    def publish(self, phase, reason=None, **extra):
        extra.setdefault('child_role',self.child_role)
        self.state.update(phase=phase, wait_reason=reason, guardian_pid=os.getpid(),
                          supervisor_pid=os.getpid(), child_pid=self.child.pid if self.child else None,
                          heartbeat_epoch=time.time(), updated_epoch=time.time(), **extra)
        atomic(self.state_path,self.state)
        atomic(self.support/'preparation-status.json',self.state)
        return phase

    def cache_progress(self):
        # Called only after mount/marker verification. Never mkdir on the volume.
        path=self.workspace/'status.json'
        try:
            status=json.loads(path.read_bytes())
        except (OSError,ValueError):
            return
        status['_cached_at_epoch']=time.time()
        atomic(self.support/'last-progress.json',status)
        versions=status.get('versions',{})
        signature=(status.get('attempted_families'),versions.get('new_downloaded_valid'),
                   versions.get('retained_parent'),status.get('frozen_batches'))
        if signature!=self.last_signature:
            if self.last_signature is not None:
                self.state['consecutive_failures']=0
                self.state['stalls_without_progress']=0
                self.state['recoveries_without_progress']=0
            self.last_signature=signature
            self.state['last_progress_epoch']=time.time()
        self.last_status_epoch=time.time()

    def stop_child(self):
        if self.child is not None:
            group=isinstance(self.child,PROCESS_CLASS)
            if group:
                # Only our start_new_session child owns this process group.
                # The leader can have exited while parser descendants remain.
                try:os.killpg(self.child.pid,signal.SIGTERM)
                except ProcessLookupError:pass
            if self.child.poll() is None:
                if not group:self.child.terminate()
                try:self.child.wait(timeout=self.terminate_seconds)
                except subprocess.TimeoutExpired:
                    if group:
                        try:os.killpg(self.child.pid,signal.SIGKILL)
                        except ProcessLookupError:pass
                    else:self.child.kill()
                    self.child.wait(timeout=self.terminate_seconds)
            if group:
                try:os.killpg(self.child.pid,signal.SIGKILL)
                except ProcessLookupError:pass
            self.child=None
            self.child_role=None
        if self.log is not None:
            self.log.close();self.log=None
        if self.power is not None:
            if self.power.poll() is None:
                self.power.terminate()
                try:self.power.wait(timeout=3)
                except subprocess.TimeoutExpired:self.power.kill();self.power.wait(timeout=3)
            self.power=None

    def attention(self,reason,log_tail=''):
        self.stop_child()
        event={'at_epoch':time.time(),'reason':reason,'log_tail':log_tail[-6000:]}
        atomic(self.support/'ATTENTION',event)
        self.state['attention_acknowledged']=False
        with (self.support/'attention-events.jsonl').open('a') as f:
            f.write(json.dumps(event,ensure_ascii=False)+'\n');f.flush();os.fsync(f.fileno())
        return self.publish('needs-attention',reason)

    def attention_active(self):
        marker=self.support/'ATTENTION'
        recheck=self.support/'RECHECK'
        if recheck.exists():
            self.state['attention_acknowledged_sha256']=sha(marker) if marker.exists() else None
            self.state['attention_acknowledged']=True
            recheck.unlink()
            self.state['consecutive_failures']=0
        active=marker.exists() and sha(marker)!=self.state.get('attention_acknowledged_sha256')
        self.state['attention_acknowledged']=marker.exists() and not active
        return active

    def start_child(self,role='worker'):
        self.current_log_path=self.log_path if role=='worker' else self.support/'recovery.log'
        self.log_offset=self.current_log_path.stat().st_size if self.current_log_path.exists() else 0
        self.log=self.current_log_path.open('ab',buffering=0)
        command=self.step['command'] if role=='worker' else self.policy['recovery_command']
        self.child=subprocess.Popen(command,stdout=self.log,stderr=subprocess.STDOUT,start_new_session=True)
        self.child_role=role
        self.child_started=time.time()
        if self.policy.get('inhibit_idle_sleep',False) and Path('/usr/bin/caffeinate').is_file():
            self.power=subprocess.Popen(['/usr/bin/caffeinate','-i','-w',str(self.child.pid)],
                                        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        return self.publish('running' if role=='worker' else 'recovering',child_role=role)

    def stalled(self):
        timeout=float(self.policy.get('stall_seconds',900))
        try:
            beat=json.loads(self.heartbeat_path.read_bytes())
        except (OSError,ValueError):
            beat={}
        if beat.get('pid')==self.child.pid:
            value=beat.get('heartbeat_epoch')
            if isinstance(value,(int,float)):
                self.state['worker_heartbeat_epoch']=value
                return time.time()-value>timeout
        # Do not mistake long startup verification for a hung worker.
        return time.time()-self.child_started>float(self.policy.get('startup_seconds',1800))

    def tick(self):
        if (self.support/'PAUSED').exists():
            self.stop_child()
            return self.publish('paused','Paused by you')
        if self.policy.get('mobile_workspace') and not self.mobile_ready():
            self.stop_child();return self.attention('Prepared mobile workspace/configuration pin is unavailable')
        if not volume_ready(self.config) and not self.mobile_ready():
            self.stop_child()
            return self.publish('waiting-for-SSD','Waiting for the configured SSD')
        resume=self.support/'RESUME_REQUESTED'
        if resume.exists():
            (self.workspace/'STOP').unlink(missing_ok=True)
            resume.unlink()
        if (self.workspace/'STOP').exists():
            self.stop_child()
            return self.publish('paused','Workspace STOP marker is present')
        self.cache_progress()
        if self.attention_active():
            self.stop_child()
            return self.publish('needs-attention','Review the retained ATTENTION report; Start requests a recheck')
        if self.child is not None:
            code=self.child.poll()
            if code is None:
                if self.child_role=='recovery':
                    if time.time()-self.child_started>float(self.policy.get('recovery_timeout_seconds',3600)):
                        self.state['recovery_pending']=False
                        return self.attention('Verified recovery exceeded its execution deadline')
                    return self.publish('recovering',child_role='recovery')
                if self.stalled():
                    count=self.state.get('stalls_without_progress',0)+1
                    self.state['stalls_without_progress']=count
                    self.stop_child()
                    if count>=int(self.policy.get('stall_restart_cap',3)):
                        return self.attention('Worker repeatedly stopped making heartbeat progress')
                    self.state['restarts']+=1
                    self.next_start=time.time()+min(300,10*count)
                    return self.publish('retrying-same-stage','Worker heartbeat stale; resuming committed checkpoints')
                return self.publish('running')
            role=self.child_role
            tail=read_tail(self.current_log_path,self.log_offset)
            self.stop_child()
            if role=='recovery':
                if code==75:
                    self.state['recovery_pending']=True;self.state['recovery_needs_network']=True
                    self.next_start=time.time()+float(self.policy.get('initial_retry_seconds',10))
                    return self.publish('waiting-for-network','Raw repair needs connectivity; original evidence preserved')
                self.state['recovery_pending']=False;self.state['recovery_needs_network']=False
                if code!=0:
                    return self.attention('Verified recovery stopped; preserved evidence needs review',tail)
                self.state['recovery_completed_epoch']=time.time()
                self.next_start=time.time()+1
                return self.publish('retrying-same-stage','Verified recovery completed; resuming the same job')
            if code==0:
                if Path(self.step['complete']).exists():
                    return self.publish('complete','Finite plan finished; source limitations remain in its report')
                return self.attention('Worker exited before its finite plan completed',tail)
            transient=code<0 or any(word in tail.split('Traceback')[-1] for word in TRANSIENT)
            missing_raw='FileNotFoundError' in tail and any(word in tail for word in ('network/bodies/','artifacts/sha256/'))
            if (any(word in tail for word in STORAGE_ERRORS) or missing_raw) and self.policy.get('recovery_command'):
                limit=int(self.policy.get('recovery_restart_cap',1))
                if self.state.get('recoveries_without_progress',0)>=limit:
                    return self.attention('Storage error repeated without acquisition progress',tail)
                self.state['recoveries_without_progress']=self.state.get('recoveries_without_progress',0)+1
                self.state['recovery_pending']=True
                return self.start_child('recovery')
            if not transient:
                return self.attention('Worker stopped on an integrity, configuration, capacity or source error',tail)
            self.state['consecutive_failures']+=1
            self.state['restarts']+=1
            delay=min(float(self.policy.get('max_retry_seconds',300)),
                      float(self.policy.get('initial_retry_seconds',10))*2**min(self.state['consecutive_failures']-1,8))
            self.next_start=max(time.time()+delay,retry_deadline(self.step.get('retry_ledger')))
            return self.publish('retrying-same-stage','Transient interruption; saved work will resume',next_retry_epoch=self.next_start)
        if Path(self.step['complete']).exists():
            return self.publish('complete','Finite plan already finished; no new collection')
        if self.state.get('recovery_pending'):
            if time.time()<self.next_start or self.state.get('recovery_needs_network') and not network_ready():
                return self.publish('waiting-for-network','Waiting to verify original raw bytes after reconnection')
            return self.start_child('recovery')
        due=max(self.next_start,retry_deadline(self.step.get('retry_ledger')))
        if time.time()<due:
            return self.publish('waiting-for-server','Respecting persisted Retry-After/backoff',next_retry_epoch=due)
        if not network_ready():
            return self.publish('waiting-for-network','Waiting for DailyMed connectivity')
        return self.start_child()

    def mobile_ready(self):
        if not self.policy.get('mobile_workspace'):return False
        try:
            marker=json.loads((self.workspace/'MOBILE-READY.json').read_bytes())
            config=self.workspace/'config.json'
            return (self.workspace.stat().st_dev==Path.home().stat().st_dev
                and marker['config_sha256']==self.policy['mobile_config_sha256']==sha(config))
        except (OSError,ValueError,KeyError):return False

    def run(self):
        while not self.stopping:
            self.tick()
            time.sleep(self.poll_seconds)
        self.stop_child()
        self.publish('stopped','Guardian stopped; checkpoints retained')
        return 0

    def close(self):
        self.stop_child();self.lock.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True)
    parser.add_argument('--policy')
    args=parser.parse_args();guardian=Guardian(args.config,args.policy)
    def stop(*_):guardian.stopping=True
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    try:raise SystemExit(guardian.run())
    finally:guardian.close()


if __name__=='__main__':main()
