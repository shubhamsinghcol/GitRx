"""Resume a finite, pinned acquisition preparation job; never schedules new scope."""
import argparse
import fcntl
import json
from pathlib import Path
import subprocess
import sqlite3
import time

from .jobs import atomic, sha


def volume_ready(config):
    volume=Path(config['volume'])
    if not volume.is_mount():
        return False
    try:
        return (volume/config['volume_marker']).read_text().strip()==config['volume_token']
    except OSError:
        return False


def run(config_path):
    config_path=Path(config_path).absolute()
    config=json.loads(config_path.read_bytes())
    if config['format']!='gitrx-finite-preparation-1':
        raise ValueError('Unsupported preparation configuration')
    support=Path(config['state_directory']).absolute()
    support.mkdir(parents=True,exist_ok=True)
    with (support/'preparation.lock').open('a+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        state_path=support/'preparation-status.json'
        state=json.loads(state_path.read_bytes()) if state_path.exists() else {
            'config_sha256':sha(config_path),'created_epoch':time.time(),'failures':{}}
        if state['config_sha256']!=sha(config_path):
            raise ValueError('Preparation configuration pin changed')
        def publish(phase,**details):
            if 'reason' not in details:state.pop('reason',None)
            state.update(phase=phase,updated_epoch=time.time(),**details)
            atomic(state_path,state)
        if not volume_ready(config):
            publish('waiting-for-SSD');return 0
        for step in config['steps']:
            name=step['name'];complete=Path(step['complete'])
            if (Path(config['workspace'])/'STOP').exists():
                publish('paused',step=name);return 0
            if not volume_ready(config):
                publish('waiting-for-SSD',step=name);return 0
            # Run each idempotent stage even when its marker exists: it must
            # verify retained evidence before advancing to the next stage.
            for attempt in range(config.get('restart_cap',6)+1):
                if state['failures'].get(name,0)>config.get('restart_cap',6):
                    publish('needs-attention',step=name,reason='Restart budget exhausted');return 1
                publish('running',step=name)
                with (support/(name+'.log')).open('ab',buffering=0) as output:
                    result=subprocess.run(step['command'],stdout=output,stderr=subprocess.STDOUT)
                if result.returncode==0:
                    if not complete.exists():
                        publish('paused',step=name,reason='Stage stopped before completion');return 0
                    break
                if not volume_ready(config):
                    publish('waiting-for-SSD',step=name);return 0
                state['failures'][name]=state['failures'].get(name,0)+1
                # Only retry transport failures, not integrity/scope/cap errors.
                log=(support/(name+'.log')).read_text(errors='replace')[-12000:]
                transient=result.returncode<0 or any(word in log.split('Traceback')[-1] for word in (
                    'Retry deferred','HTTP 0:','HTTP 408:','HTTP 429:',
                    'HTTP 500:','HTTP 502:','HTTP 503:','HTTP 504:',
                    'timed out','TimeoutError','Temporary failure'))
                if not transient:
                    publish('needs-attention',step=name,reason='Stage failed; see retained log');return 1
                publish('retrying-same-stage',step=name,attempt=state['failures'][name])
                due=time.time()+30
                ledger=step.get('retry_ledger')
                if ledger and Path(ledger).exists():
                    with sqlite3.connect(Path(ledger).absolute().as_uri()+'?mode=ro',uri=True) as db:
                        row=db.execute("SELECT value FROM transport_state WHERE key='retry_after'").fetchone()
                        if row:due=max(due,float(row[0]))
                # Persisted Retry-After remains authoritative inside HTTP even
                # after a wake/login. Bounded retries never bypass its deadline.
                while time.time()<due:
                    time.sleep(min(15,due-time.time()))
                    if not volume_ready(config):
                        publish('waiting-for-SSD',step=name);return 0
            else:
                publish('needs-attention',step=name,reason='Restart budget exhausted');return 1
        publish('complete',step=None)
        return 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True)
    raise SystemExit(run(parser.parse_args().config))


if __name__=='__main__':
    main()
