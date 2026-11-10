"""Desktop safe-eject/reconnect controls for a prepared local working job."""
import argparse
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import subprocess
import time
from . import backfill_control as control
from .jobs import sha,atomic
from .mobile_archive import MobileArchive


def prepared(config):
    root=Path(config.get('mobile_workspace',''))
    marker=control.read_json(root/'MOBILE-READY.json') or {}
    path=root/'config.json'
    if not root.is_absolute() or not path.is_file() or marker.get('config_sha256')!=sha(path):
        raise RuntimeError('Mobile workspace is not prepared; SSD was not ejected')
    return root,json.loads(path.read_bytes())


def pause_wait(config,timeout=60):
    control.pause(config);deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        state=control.read_json(Path(config['state_directory'])/'guardian-state.json') or {}
        if state.get('phase')=='paused' and not state.get('child_pid'):return
        time.sleep(.2)
    raise RuntimeError('Worker has not stopped yet; SSD was not ejected')


@contextmanager
def action_lock(config):
    path=Path(config['state_directory'])/'mobile-action.lock'
    with path.open('a+') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('Another SSD action is already running')
        yield


@contextmanager
def writer_lock(root):
    with (root/'writer.lock').open('a+') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('A worker still owns the workspace; SSD was not ejected')
        yield


def diskutil(*args):return subprocess.run(['/usr/sbin/diskutil',*args],capture_output=True,text=True)


def needs_review(config):
    support=Path(config['state_directory']);state=control.read_json(support/'guardian-state.json') or {}
    marker=support/'ATTENTION'
    return (state.get('phase') in ('needs-attention','attention','blocked') or
        marker.exists() and sha(marker)!=state.get('attention_acknowledged_sha256'))


def transfer_attention(config,exc):
    support=Path(config['state_directory']);marker=support/'ATTENTION'
    if marker.exists():
        retained=support/'mobile-action-incidents'/str(time.time_ns());retained.mkdir(parents=True)
        (retained/'ATTENTION.before.json').write_bytes(marker.read_bytes())
    atomic(marker,{'reason':'SSD transfer stopped for review: '+str(exc),'at_epoch':time.time(),'action':'reconnect'})


def eject(config):
    with action_lock(config):
        root,job=prepared(config);archive=MobileArchive(job);resume=not needs_review(config)
        pause_wait(config)
        try:
            with writer_lock(root):
                control.write_marker(root/'SSD-DISABLED')
                if archive.available(ignore_disabled=True):
                    result=diskutil('eject',job['volume'])
                    if result.returncode:
                        raise RuntimeError('macOS did not eject the SSD. Do not unplug it yet. '+(result.stderr or result.stdout).strip())
                elif Path(job['volume']).is_mount():
                    raise RuntimeError('A different/unverified volume is mounted there. It was not ejected.')
        finally:
            if resume:control.start(config)
        if not resume:return 'SSD safely ejected. You can unplug it. The job stays paused for the existing integrity review; this button does not clear that stop.'
        return 'SSD safely ejected (or already disconnected). You can unplug it. GitRx resumes downloads and parsing locally; completed batches wait on the laptop.'


def reconnect(config):
    with action_lock(config):
        root,job=prepared(config);archive=MobileArchive(job,enforce_budget=False);resume=not needs_review(config)
        if not archive.available(ignore_disabled=True):
            uuid=config.get('ssd_volume_uuid')
            if uuid:diskutil('mount',uuid)
        if not archive.available(ignore_disabled=True):
            raise RuntimeError('The expected SSD is not mounted and verified. Plug it in and click Reconnect again; local work can continue.')
        pause_wait(config)
        success=False
        try:
            with writer_lock(root):
                control.remove_marker(root/'SSD-DISABLED')
                archive.sync_pending()
            success=True
        except BaseException as exc:
            control.write_marker(root/'SSD-DISABLED')
            if isinstance(exc,Exception):transfer_attention(config,exc)
            raise
        finally:
            if success and resume:control.start(config)
        if not resume:return 'SSD verified and enabled; waiting batches transferred. The job stays paused for its existing integrity review.'
        return 'SSD verified and enabled. Waiting completed batches were hash-checked and transferred. GitRx resumes; future completed batches can use the SSD again.'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('eject','reconnect'));parser.add_argument('--config',required=True)
    args=parser.parse_args()
    try:print(globals()[args.action](control.load_config(args.config)));return 0
    except (OSError,ValueError,RuntimeError) as exc:print('GitRx SSD: '+str(exc));return 1


if __name__=='__main__':raise SystemExit(main())
