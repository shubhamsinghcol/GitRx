"""Local Start/Pause/Progress/Health controls for the one pinned backfill job.

Progress and health never collect data, start a worker, or create SSD directories.
"""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import time
from zoneinfo import ZoneInfo


def read_json(path):
    try:return json.loads(Path(path).read_bytes())
    except (OSError,ValueError,TypeError):return None


def load_config(path):
    config=read_json(path)
    if not isinstance(config,dict):raise ValueError('The local control configuration is missing or unreadable.')
    runner=read_json(config['runner_config']) or {}
    for key in ('workspace','state_directory','volume','volume_marker','volume_token'):
        if key not in config and key in runner:config[key]=runner[key]
    required=('workspace','state_directory','volume','volume_marker','volume_token','label','plist')
    if any(not config.get(k) for k in required):raise ValueError('The local control configuration is incomplete.')
    return config


def volume_ready(config):
    volume=Path(config['volume'])
    try:
        return (volume.is_mount() and os.stat(volume).st_dev!=os.stat('/').st_dev
                and (volume/config['volume_marker']).read_text().strip()==config['volume_token']
                and Path(config.get('archive_workspace',config['workspace'])).resolve().is_relative_to(volume.resolve()))
    except OSError:return False


def write_marker(path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.control-tmp')
    with temporary.open('w') as f:
        f.write(datetime.now(timezone.utc).isoformat()+'\n');f.flush();os.fsync(f.fileno())
    os.replace(temporary,path)
    directory=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(directory)
    finally:os.close(directory)


def remove_marker(path):
    try:Path(path).unlink()
    except FileNotFoundError:return
    directory=os.open(Path(path).parent,os.O_RDONLY)
    try:os.fsync(directory)
    finally:os.close(directory)


def launchctl(*args):
    return subprocess.run(['launchctl',*args],capture_output=True,text=True)


def start(config):
    state=Path(config['state_directory'])
    remove_marker(state/'PAUSED')
    write_marker(state/'RESUME_REQUESTED')
    if (state/'ATTENTION').exists():write_marker(state/'RECHECK')
    connected=volume_ready(config)
    if (connected or config.get('mobile_workspace')) and Path(config['workspace']).is_dir():remove_marker(Path(config['workspace'])/'STOP')
    target=f"gui/{os.getuid()}/{config['label']}"
    observed=launchctl('print',target)
    if observed.returncode:
        loaded=launchctl('bootstrap',f'gui/{os.getuid()}',str(config['plist']))
        if loaded.returncode:raise RuntimeError('macOS could not load the backfill runner: '+loaded.stderr.strip())
    elif 'state = running' not in observed.stdout:
        kicked=launchctl('kickstart',target)
        if kicked.returncode:raise RuntimeError('macOS could not start the backfill runner: '+kicked.stderr.strip())
    if connected:return 'Start requested. The same saved job will continue; completed work is reused.'
    if config.get('mobile_workspace'):return 'Start requested in local mode. Completed batches wait on the laptop until SSD reconnection.'
    return 'Start enabled. The SSD is disconnected; the same saved job will resume when the correct SSD returns.'


def pause(config):
    state=Path(config['state_directory']);write_marker(state/'PAUSED')
    if (volume_ready(config) or config.get('mobile_workspace')) and Path(config['workspace']).is_dir():write_marker(Path(config['workspace'])/'STOP')
    return 'Pause requested. The guardian will stop the worker and preserve its checkpoints. It stays paused after login or SSD reconnection until you click Start.'


def progress_snapshot(config):
    if volume_ready(config) or config.get('mobile_workspace'):
        value=read_json(Path(config['workspace'])/'status.json')
        if isinstance(value,dict):return value,False
    value=read_json(Path(config['state_directory'])/'last-progress.json')
    if isinstance(value,dict):
        if isinstance(value.get('status'),dict):value=value['status']
        return value,True
    return {},True


def _number(value,default=0):
    try:return max(0,int(value))
    except (ValueError,TypeError):return default


def captured_coverage(config,status):
    """Union baseline with ready staged families; never add overlapping counts."""
    baseline=read_json(config['baseline_setids_path']) if config.get('baseline_setids_path') else None
    if isinstance(baseline,dict):baseline=baseline.get('setids')
    if not isinstance(baseline,list):return None
    if config.get('control_database'):
        path=Path(config['control_database'])
    elif volume_ready(config):path=Path(config['workspace'])/'control.sqlite'
    else:return None
    if not path.is_file():return None
    try:
        c=sqlite3.connect(path.absolute().as_uri()+'?mode=ro',uri=True,timeout=1)
        try:ready={r[0] for r in c.execute("SELECT setid FROM queue WHERE state IN ('complete','partial')")}
        finally:c.close()
    except sqlite3.Error:return None
    old=set(baseline);denominator=_number(status.get('target_families'),_number(config.get('target_families'),158982))
    if not denominator:return None
    represented=old|ready
    return {'baseline':len(old),'new_ready':len(ready-old),'represented':len(represented),
            'target':denominator,'percent':100*len(represented)/denominator}


def operating_details(config,status,cached=False):
    """Human-readable policy and optional observed pacing; no data collection."""
    workspace=Path(config['workspace']);connected=volume_ready(config)
    pacing=status.get('pacing') or status.get('transport') or {}
    if (connected or config.get('mobile_workspace')) and not cached:
        pacing=read_json(config.get('pacing_state',workspace/'network/pacing.json')) or read_json(workspace/'network/pacing.json') or pacing
    lines=[]
    try:spacing=float(pacing['spacing_seconds'])
    except (KeyError,ValueError,TypeError):spacing=None
    if spacing is not None and .05<=spacing<=60:
        lanes=_number(pacing.get('download_lanes'),1)
        wording='one connection' if lanes==1 else f'{lanes} download lanes'
        lines.append(f'HTTP pacing: {wording}, at least {spacing:g} seconds between starts (up to {1/spacing:g} starts/second total)'+(' at cached checkpoint.' if cached else '.'))
        hold=pacing.get('hold_until',0)
        if isinstance(hold,(int,float)) and hold>time.time():
            lines.append(f'HTTP cooldown: {hold-time.time():.0f} seconds remaining; new requests are paused, buffered processing can continue.')
        if pacing.get('backoffs'):lines.append(f'Automatic server/load backoffs recorded: {_number(pacing["backoffs"]):,}.')
    elif connected:
        policy=read_json(config.get('transport_policy',workspace/'TRANSPORT-POLICY.json')) or {}
        if policy.get('format')=='gitrx-transport-policy-1':
            lines.append('HTTP pacing: adaptive single-connection policy enabled; current rate not yet observed.')
    policy=read_json(config.get('processing_policy',workspace/'PROCESSING-POLICY.json')) if connected or config.get('mobile_workspace') else None
    if isinstance(policy,dict) and policy.get('max_workers'):
        text=f'Processing policy: up to {_number(policy["max_workers"])} local workers'
        if policy.get('total_rss_budget_bytes'):text+=f', {float(policy["total_rss_budget_bytes"])/1024**3:g} GiB total memory budget'
        lines.append(text+'.')
    if connected or config.get('mobile_workspace'):
        processing=read_json(workspace/'processing-status.json') or read_json(workspace/'run/processing.json') or {}
        active=processing.get('active_workers',processing.get('worker_count'))
        if isinstance(active,(int,float)):
            detail=f'Processing pool workers alive at observation: {_number(active)}'
            if isinstance(processing.get('pending_versions'),(int,float)):detail+=f'; pending versions: {_number(processing["pending_versions"])}'
            if isinstance(processing.get('total_RSS_bytes'),(int,float)):
                detail+=f'; total RSS: {processing["total_RSS_bytes"]/1024**3:.2f} GiB'
            lines.append(detail+'. Workers alive is not a count of CPUs busy.')
    return lines


def display_time(value,config):
    try:
        stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
        return stamp.astimezone(ZoneInfo(config.get('timezone','America/Chicago'))).strftime('%b %d, %Y %I:%M:%S %p %Z')
    except (ValueError,TypeError,AttributeError,KeyError):return str(value)


def progress(config):
    status,cached=progress_snapshot(config)
    if not status:return 'No progress record is available yet. Click Start with the SSD connected.'
    target=_number(status.get('target_families'),_number(config.get('target_families'),158982))
    attempted=_number(status.get('attempted_families'))
    states=status.get('family_states') or {};versions=status.get('versions') or {}
    percent=100*attempted/target if target else 0
    lines=['GitRx progress'+(' — cached/offline' if cached else ''),
           f'Finite plan attempted: {attempted:,} / {target:,} families ({percent:.3f}%).',
           f'Families fully retrieved and parsed: {_number(states.get("complete"))+_number(states.get("partial")):,} '
           f'({_number(states.get("partial")):,} with partial extraction).',
           f'Other attempted outcomes: {max(0,attempted-_number(states.get("complete"))-_number(states.get("partial"))):,}; inspect Health for current operating state.',
           f'New valid versions downloaded: {_number(versions.get("new_downloaded_valid")):,}.',
           f'New parses: {_number(versions.get("new_parsed_complete")):,} complete; '
           f'{_number(versions.get("new_parsed_partial")):,} partial; {_number(versions.get("new_parse_failed")):,} failed.',
           f'Existing versions reused: {_number(versions.get("retained_parent")):,}.',
           f'Unavailable targets: {_number(versions.get("unavailable")):,}; quarantined targets: {_number(versions.get("quarantined")):,}.']
    coverage=captured_coverage(config,status)
    if coverage:
        lines.append(f'Captured-family coverage: {coverage["represented"]:,} / {coverage["target"]:,} ({coverage["percent"]:.3f}%) '
                     f'= {coverage["baseline"]:,} baseline + {coverage["new_ready"]:,} ready new families, with overlap removed.')
        lines.append('New families are staged; they are not yet promoted into the selected application release.')
    else:
        lines.append(f'Selected baseline: {_number(config.get("baseline_families"),7239):,} families / '
                     f'{_number(config.get("baseline_versions"),51357):,} versions. Run counts overlap it and must not simply be added.')
    lines.extend(operating_details(config,status,cached))
    if status.get('updated_at'):lines.append('Progress observed: '+display_time(status['updated_at'],config))
    lines.append('These percentages describe the pinned plan, not all historical DailyMed. Archived-only scope remains unknown.')
    return '\n'.join(lines)


def process_matches(pid,module,config_path):
    try:pid=int(pid)
    except (ValueError,TypeError):return False
    if pid<2:return False
    result=subprocess.run(['ps','-p',str(pid),'-o','stat=','-o','command='],capture_output=True,text=True)
    text=result.stdout.strip()
    return (result.returncode==0 and text and not text.startswith('Z')
            and module in text and str(config_path) in text)


def health(config):
    support=Path(config['state_directory']);state=read_json(support/'guardian-state.json') or read_json(support/'preparation-status.json') or {}
    status,cached=progress_snapshot(config)
    connected=volume_ready(config)
    guardian=process_matches(state.get('guardian_pid',state.get('supervisor_pid')),
        config.get('supervisor_module','gitrx_backend.backfill_guardian'),config['runner_config'])
    worker=process_matches(state.get('child_pid'),'gitrx_backend.backfill',config.get('worker_config',str(Path(config['workspace'])/'config.json')))
    try:age=max(0,time.time()-float(state.get('heartbeat_epoch',0)))
    except (TypeError,ValueError):age=float('inf')
    heartbeat=age<=_number(config.get('heartbeat_stale_seconds'),120)
    lines=['GitRx health']
    paused=(support/'PAUSED').exists()
    phase=state.get('phase','unknown')
    if paused:
        lines.append('PAUSING — worker is stopping.' if worker else 'PAUSED — no worker is running. Click Start to continue.')
        ok=not worker
    elif not guardian or not heartbeat:
        lines.append('CHECK REQUIRED — guardian is not running or its heartbeat is stale. Click Start to recheck the saved job.')
        ok=False
    elif phase in ('needs-attention','attention','blocked') or (support/'ATTENTION').exists() and not state.get('attention_acknowledged'):
        lines.append('NEEDS ATTENTION — '+str(state.get('wait_reason') or state.get('reason') or 'a safety check stopped the worker.'))
        ok=False
    elif not connected and not config.get('mobile_workspace'):
        lines.append('WAITING — correct SSD is disconnected. Guardian is alive and will resume after reconnection.')
        ok=True
    elif phase=='paused':
        lines.append('PAUSED — a saved pause/STOP request is active. Click Start to continue.')
        ok=not worker
    elif phase=='starting':
        lines.append('STARTING — guardian is alive and checking saved work before starting the worker.')
        ok=True
    elif phase=='complete':
        lines.append('COMPLETE — the finite plan finished. Check Progress for partial/unknown outcomes.')
        ok=True
    elif phase.startswith('waiting') or phase in ('retrying','retrying-same-stage','backoff','recovering'):
        lines.append('WAITING/RETRYING — '+str(state.get('wait_reason') or state.get('reason') or 'temporary connection interruption.'))
        ok=True
    elif worker:
        lines.append('RUNNING — working locally while SSD is disconnected; completed batches wait for reconnection.'
            if not connected else 'RUNNING — guardian and worker processes are alive; guardian heartbeat is current.')
        ok=True
        worker_beat=read_json(Path(config['workspace'])/'worker-heartbeat.json') if connected or config.get('mobile_workspace') else None
        fresh_worker_beat=False
        if isinstance(worker_beat,dict) and worker_beat.get('pid')==state.get('child_pid'):
            try:fresh_worker_beat=0<=time.time()-float(worker_beat['heartbeat_epoch'])<=120
            except (KeyError,ValueError,TypeError):pass
        updated=status.get('updated_at')
        if updated:
            try:progress_age=max(0,time.time()-datetime.fromisoformat(updated.replace('Z','+00:00')).timestamp())
            except (ValueError,TypeError):progress_age=None
            if progress_age is not None and progress_age>120:
                if fresh_worker_beat:
                    lines.append(f'Family progress is {progress_age/60:.1f} minutes old; a matching worker heartbeat is current while it processes the active operation.')
                else:
                    lines.append(f'Progress has not changed for {progress_age/60:.1f} minutes and no matching current worker heartbeat was found. Check the current operation.')
                    ok=False
    else:
        lines.append('CHECK REQUIRED — guardian is alive but no matching worker is currently running.')
        ok=False
    lines.extend([f'Guardian: {"alive" if guardian else "not confirmed"}; heartbeat: {"current" if heartbeat else "stale/missing"}.',
                  f'Worker: {"alive" if worker else "not running"}.',
                  f'SSD: {"connected and identity verified" if connected else "not available"}.'])
    if connected:
        try:
            free=shutil.disk_usage(config['volume']).free
            lines.append(f'SSD free space: {free/1024**3:.1f} GiB (minimum reserve 20 GiB).')
            if free<20*1024**3:lines.append('STOP REQUIRED — free-space reserve is not available.');ok=False
        except OSError:lines.append('SSD free space could not be read.');ok=False
    if cached:lines.append('Progress display uses the last cached checkpoint; it is not a fresh SSD read.')
    lines.extend(operating_details(config,status,cached))
    lines.append('This checks operating state, not full-corpus correctness or perfect extraction.')
    return '\n'.join(lines),ok


def sample_counts(config):
    path=Path(config.get('control_database',Path(config['workspace'])/'control.sqlite'))
    with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=3) as db:
        states=dict(db.execute('SELECT state,COUNT(*) FROM queue GROUP BY state'))
    remaining=sum(states.get(k,0) for k in ('pending','in_progress','waiting_for_archive'))
    return sum(states.values())-remaining,remaining


def status(config,sample_seconds=0):
    sampled=None
    if sample_seconds:
        before,_=sample_counts(config);started=time.monotonic()
        time.sleep(sample_seconds)
        after,remaining=sample_counts(config)
        sampled=(after-before,remaining,time.monotonic()-started)
    report,ok=health(config)
    snapshot,cached=progress_snapshot(config)
    target=_number(snapshot.get('target_families'))
    done=min(target,_number(snapshot.get('attempted_families')))
    percent=100*done/target if target else 0
    eta='ETA: collecting a reliable running-rate sample.'
    running=ok and 'RUNNING —' in report and not cached
    estimate=snapshot.get('eta_seconds')
    rate=snapshot.get('recent_families_per_hour')
    if running and isinstance(estimate,(int,float)) and isinstance(rate,(int,float)):
        eta=f'ETA: approximately {estimate/86400:.1f} days ({rate:.0f} families/hour, recent observed rate).'
    elif not running:
        eta='ETA: unavailable while paused, offline, starting, or needing attention.'
    if sampled is not None:
        completed,remaining,elapsed=sampled
        eta='ETA: unavailable; no measurable family progress during the one-minute sample.'
        if running and completed>0 and elapsed>0:
            hours=remaining*elapsed/completed/3600
            eta=f'ETA: approximately {hours:.1f} hours ({completed} families completed in {elapsed:.0f} seconds; fresh sample).'
        elif not running:eta='ETA: unavailable while paused, offline, starting, or needing attention.'
    if target and done==target:eta='ETA: finite plan attempted; review deferred and incomplete outcomes.'
    states=snapshot.get('family_states',{});versions=snapshot.get('versions',{})
    lines=['GitRx status',report.splitlines()[1],
        f'Plan processed: {percent:.3f}% | Remaining: {100-percent:.3f}% ({target-done:,} families).',
        f'Attempted: {done:,} / {target:,} families; fully retrieved/parsed: {_number(states.get("complete"))+_number(states.get("partial")):,}.',
        eta,'ETA varies with history size, retries and sleep; processed includes incomplete/deferred work.',
        f'Failed extractions earmarked: {_number(versions.get("new_parse_failed")):,}; unavailable versions: {_number(versions.get("unavailable")):,}; quarantined versions: {_number(versions.get("quarantined")):,}.']
    lines.extend(line for line in report.splitlines() if line.startswith(('SSD:','SSD free space:','Progress has not','Family progress is','Processing pool','HTTP pacing:','HTTP cooldown:')))
    policy=read_json(Path(config['state_directory'])/'LOCAL-STORAGE-POLICY.json') or {}
    if policy.get('raw_staging_root'):
        lines.append(f'Storage: downloads and parsing on laptop; verified batches archived to SSD. Local raw-work cap: {policy.get("raw_staging_max_bytes",0)/1024**3:.0f} GiB.')
        buffered=read_json(Path(policy['raw_staging_root'])/'buffer/download-buffer-status.json')
        if buffered:
            lines.append(f'Download buffer: {_number(buffered.get("ready_files")):,} files ready for handling '
                f'({_number(buffered.get("ready_bytes"))/1024**2:.1f} MiB); '
                f'limits {str(_number(buffered["max_ready_files"]))+" files" if buffered.get("max_ready_files") is not None else "no file-count stop"} / '
                f'{_number(buffered.get("max_ready_bytes"))/1024**2:.0f} MiB. Downloads run independently of parsing.')
    mobile=snapshot.get('mobile_archive')
    if mobile:
        lines.append(f'SSD archive: {"enabled" if mobile.get("ssd_enabled") else "disabled for safe removal"}; '
            f'{_number(mobile.get("waiting_batches"))} completed batches waiting locally. '
            f'Local work: {_number(mobile.get("local_work_bytes"))/1024**3:.1f} / {_number(mobile.get("local_work_cap_bytes"))/1024**3:.0f} GiB.')
    if cached:lines.append('Offline: displaying the last cached progress checkpoint.')
    lines.append('Progress observed: '+display_time(snapshot['updated_at'],config) if snapshot.get('updated_at') else 'No saved progress timestamp.')
    return '\n'.join(lines),ok


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('start','pause','progress','health','status'))
    parser.add_argument('--config',required=True)
    parser.add_argument('--sample-seconds',type=int,choices=(60,),default=0,help='Sample a full minute before reporting status ETA')
    args=parser.parse_args()
    try:
        config=load_config(args.config)
        if args.action=='status':
            if args.sample_seconds:print('Sampling progress for 60 seconds before calculating ETA...',flush=True)
            text,ok=status(config,args.sample_seconds);print(text);return 0 if ok else 1
        if args.action=='health':text,ok=health(config);print(text);return 0 if ok else 1
        print(globals()[args.action](config));return 0
    except (OSError,ValueError,RuntimeError,sqlite3.Error) as exc:
        print('GitRx control: '+str(exc));return 1


if __name__=='__main__':raise SystemExit(main())
