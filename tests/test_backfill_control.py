import json,sqlite3,time
from pathlib import Path
from types import SimpleNamespace
import pytest
from gitrx_backend import backfill_control as control


def cfg(tmp_path):
    return {'workspace':str(tmp_path/'missing-ssd/work'),'volume':str(tmp_path/'missing-ssd'),
            'volume_marker':'marker','volume_token':'token','state_directory':str(tmp_path/'support'),
            'runner_config':str(tmp_path/'support/runner.json'),'label':'fixture.gitrx','plist':str(tmp_path/'support/fixture.plist')}


def test_pause_persists_while_ssd_absent_without_fallback_directory(tmp_path,monkeypatch):
    config=cfg(tmp_path);monkeypatch.setattr(control,'volume_ready',lambda c:False)
    text=control.pause(config)
    assert (tmp_path/'support/PAUSED').exists() and not (tmp_path/'missing-ssd').exists()
    assert 'Pause requested' in text


def test_start_never_kills_running_job_and_requests_resume_recheck(tmp_path,monkeypatch):
    config=cfg(tmp_path);support=tmp_path/'support';support.mkdir()
    (support/'PAUSED').write_text('pause');(support/'ATTENTION').write_text('retained safety evidence')
    monkeypatch.setattr(control,'volume_ready',lambda c:False)
    calls=[]
    def launch(*args):calls.append(args);return SimpleNamespace(returncode=0,stdout='state = running',stderr='')
    monkeypatch.setattr(control,'launchctl',launch)
    text=control.start(config)
    assert not (support/'PAUSED').exists()
    assert (support/'RESUME_REQUESTED').exists() and (support/'RECHECK').exists()
    assert (support/'ATTENTION').read_text()=='retained safety evidence'
    assert len(calls)==1 and calls[0][0]=='print' and 'SSD is disconnected' in text
    assert not (tmp_path/'missing-ssd').exists()


def test_start_bootstraps_stopped_service(tmp_path,monkeypatch):
    config=cfg(tmp_path);monkeypatch.setattr(control,'volume_ready',lambda c:False)
    calls=[]
    def launch(*args):
        calls.append(args);return SimpleNamespace(returncode=1 if args[0]=='print' else 0,stdout='',stderr='')
    monkeypatch.setattr(control,'launchctl',launch)
    control.start(config)
    assert [c[0] for c in calls]==['print','bootstrap']


def test_cached_progress_is_read_only_and_does_not_add_baseline(tmp_path,monkeypatch):
    config=cfg(tmp_path);support=tmp_path/'support';support.mkdir()
    (support/'last-progress.json').write_text(json.dumps({'target_families':100,'attempted_families':10,
        'family_states':{'complete':6,'partial':3,'quarantined':1},'versions':{'new_downloaded_valid':20,'retained_parent':5}}))
    monkeypatch.setattr(control,'volume_ready',lambda c:False)
    monkeypatch.setattr(control,'launchctl',lambda *a:pytest.fail('status must not start acquisition'))
    text=control.progress(config)
    assert '10 / 100 families (10.000%)' in text and 'cached/offline' in text
    assert 'must not simply be added' in text and 'New valid versions downloaded: 20' in text
    assert not (tmp_path/'missing-ssd').exists()


def test_captured_family_coverage_removes_baseline_overlap(tmp_path,monkeypatch):
    config=cfg(tmp_path);workspace=Path(config['workspace']);workspace.mkdir(parents=True)
    baseline=tmp_path/'baseline.json';baseline.write_text(json.dumps(['a','b']))
    config['baseline_setids_path']=str(baseline)
    c=sqlite3.connect(workspace/'control.sqlite');c.execute('CREATE TABLE queue(setid,state)')
    c.executemany('INSERT INTO queue VALUES(?,?)',[('a','complete'),('c','complete'),('d','partial'),('e','quarantined')]);c.commit();c.close()
    monkeypatch.setattr(control,'volume_ready',lambda c:True)
    result=control.captured_coverage(config,{'target_families':10})
    assert result=={'baseline':2,'new_ready':2,'represented':4,'target':10,'percent':40.0}


def test_health_requires_real_worker_and_current_heartbeat(tmp_path,monkeypatch):
    config=cfg(tmp_path);support=tmp_path/'support';support.mkdir()
    state={'guardian_pid':10,'child_pid':20,'heartbeat_epoch':time.time(),'phase':'running'}
    (support/'guardian-state.json').write_text(json.dumps(state))
    monkeypatch.setattr(control,'volume_ready',lambda c:False)
    monkeypatch.setattr(control,'process_matches',lambda pid,*a:pid==10)
    text,ok=control.health(config)
    assert ok and 'WAITING — correct SSD' in text
    state['heartbeat_epoch']=time.time()-1000;(support/'guardian-state.json').write_text(json.dumps(state))
    text,ok=control.health(config)
    assert not ok and 'heartbeat is stale' in text


def test_health_refuses_alive_guardian_with_missing_worker_when_connected(tmp_path,monkeypatch):
    config=cfg(tmp_path);support=tmp_path/'support';support.mkdir()
    (support/'guardian-state.json').write_text(json.dumps({'guardian_pid':10,'child_pid':20,'heartbeat_epoch':time.time(),'phase':'running'}))
    monkeypatch.setattr(control,'volume_ready',lambda c:True)
    monkeypatch.setattr(control,'progress_snapshot',lambda c:({},False))
    monkeypatch.setattr(control.shutil,'disk_usage',lambda p:SimpleNamespace(free=100*1024**3))
    monkeypatch.setattr(control,'process_matches',lambda pid,*a:pid==10)
    text,ok=control.health(config)
    assert not ok and 'no matching worker' in text


def test_process_check_rejects_reused_pid_and_zombie(monkeypatch):
    monkeypatch.setattr(control.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout='S unrelated-worker --config fixture'))
    assert not control.process_matches(10,'gitrx_backend.backfill','fixture')
    monkeypatch.setattr(control.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout='Z python -m gitrx_backend.backfill --config fixture'))
    assert not control.process_matches(10,'gitrx_backend.backfill','fixture')

def test_pause_remains_persistent_across_offline_readonly_status_calls(tmp_path,monkeypatch):
    config=cfg(tmp_path);monkeypatch.setattr(control,'volume_ready',lambda c:False)
    control.pause(config);marker=Path(config['state_directory'])/'PAUSED';before=marker.read_bytes()
    monkeypatch.setattr(control,'process_matches',lambda *a:False)
    control.progress(config);text,ok=control.health(config)
    assert ok and 'PAUSED' in text and marker.read_bytes()==before
    assert not (tmp_path/'missing-ssd').exists()


def test_progress_displays_observed_pacing_and_processing_without_inventing_cpu_use(tmp_path,monkeypatch):
    config=cfg(tmp_path);workspace=Path(config['workspace']);(workspace/'network').mkdir(parents=True)
    (workspace/'status.json').write_text(json.dumps({'target_families':100,'attempted_families':5,'versions':{},'family_states':{'complete':5}}))
    (workspace/'network/pacing.json').write_text(json.dumps({'spacing_seconds':.2,'backoffs':2}))
    (workspace/'PROCESSING-POLICY.json').write_text(json.dumps({'max_workers':6,'total_rss_budget_bytes':8*1024**3}))
    (workspace/'processing-status.json').write_text(json.dumps({'active_workers':4,'pending_versions':3,'total_RSS_bytes':1024**3}))
    monkeypatch.setattr(control,'volume_ready',lambda c:True)
    text=control.progress(config)
    assert 'up to 5 starts/second' in text and 'up to 6 local workers' in text
    assert 'pending versions: 3' in text and 'not a count of CPUs busy' in text


def test_health_uses_matching_fresh_worker_heartbeat_for_long_family(tmp_path,monkeypatch):
    config=cfg(tmp_path);support=tmp_path/'support';support.mkdir();workspace=Path(config['workspace']);workspace.mkdir(parents=True)
    (support/'guardian-state.json').write_text(json.dumps({'guardian_pid':10,'child_pid':20,'heartbeat_epoch':time.time(),'phase':'running'}))
    (workspace/'worker-heartbeat.json').write_text(json.dumps({'pid':20,'heartbeat_epoch':time.time()}))
    monkeypatch.setattr(control,'volume_ready',lambda c:True)
    monkeypatch.setattr(control,'process_matches',lambda *a:True)
    monkeypatch.setattr(control.shutil,'disk_usage',lambda p:SimpleNamespace(free=100*1024**3))
    stale='2000-01-01T00:00:00Z'
    monkeypatch.setattr(control,'progress_snapshot',lambda c:({'updated_at':stale},False))
    text,ok=control.health(config)
    assert ok and 'matching worker heartbeat is current' in text
    (workspace/'worker-heartbeat.json').write_text(json.dumps({'pid':99,'heartbeat_epoch':time.time()}))
    text,ok=control.health(config)
    assert not ok and 'no matching current worker heartbeat' in text


def test_unified_status_shows_percent_remaining_eta_and_health(tmp_path,monkeypatch):
    config=cfg(tmp_path)
    monkeypatch.setattr(control,'health',lambda c:('GitRx health\nRUNNING — current heartbeats.',True))
    monkeypatch.setattr(control,'progress_snapshot',lambda c:({'target_families':100,'attempted_families':25,
        'eta_seconds':172800,'recent_families_per_hour':2},False))
    monkeypatch.setattr(control,'progress',lambda c:'GitRx progress\n25 families attempted.')
    text,ok=control.status(config)
    assert ok and '25.000%' in text and '75.000%' in text
    assert '2.0 days' in text and 'RUNNING' in text
    assert not Path(config['state_directory']).exists()
    monkeypatch.setattr(control,'health',lambda c:('GitRx health\nPAUSED',True))
    assert 'ETA: unavailable' in control.status(config)[0]


def test_mobile_health_reads_live_local_progress_without_ssd(tmp_path,monkeypatch):
    config=cfg(tmp_path);workspace=tmp_path/'local/work';workspace.mkdir(parents=True)
    config.update(workspace=str(workspace),mobile_workspace=str(workspace),worker_config=str(workspace/'config.json'))
    support=Path(config['state_directory']);support.mkdir()
    (support/'guardian-state.json').write_text(json.dumps({'guardian_pid':10,'child_pid':20,'heartbeat_epoch':time.time(),'phase':'running'}))
    (workspace/'worker-heartbeat.json').write_text(json.dumps({'pid':20,'heartbeat_epoch':time.time()}))
    (workspace/'status.json').write_text(json.dumps({'attempted_families':2,'target_families':10}))
    monkeypatch.setattr(control,'volume_ready',lambda c:False)
    monkeypatch.setattr(control,'process_matches',lambda *a:True)
    snapshot,cached=control.progress_snapshot(config)
    assert snapshot['attempted_families']==2 and not cached
    text,ok=control.health(config)
    assert ok and 'RUNNING — working locally' in text and 'completed batches wait' in text
    assert not (tmp_path/'missing-ssd').exists()


def test_status_fresh_sample_uses_observed_progress_not_old_eta(tmp_path,monkeypatch):
    config=cfg(tmp_path)
    monkeypatch.setattr(control,'health',lambda c:('GitRx health\nRUNNING — current heartbeats.',True))
    monkeypatch.setattr(control,'progress_snapshot',lambda c:({'target_families':100,'attempted_families':30,'eta_seconds':999999,'recent_families_per_hour':1},False))
    counts=iter([(20,80),(30,70)]);clock=iter([100,160]);waits=[]
    monkeypatch.setattr(control,'sample_counts',lambda c:next(counts))
    monkeypatch.setattr(control.time,'monotonic',lambda:next(clock))
    monkeypatch.setattr(control.time,'sleep',lambda seconds:waits.append(seconds))
    text,ok=control.status(config,60)
    assert ok and waits==[60] and '0.1 hours' in text and '10 families completed in 60 seconds' in text


def test_status_zero_sample_progress_does_not_reuse_stale_eta(tmp_path,monkeypatch):
    config=cfg(tmp_path)
    monkeypatch.setattr(control,'health',lambda c:('GitRx health\nRUNNING — current heartbeats.',True))
    monkeypatch.setattr(control,'progress_snapshot',lambda c:({'target_families':100,'attempted_families':30,'eta_seconds':999999,'recent_families_per_hour':1},False))
    monkeypatch.setattr(control,'sample_counts',lambda c:(30,70))
    clock=iter([100,160]);monkeypatch.setattr(control.time,'monotonic',lambda:next(clock))
    monkeypatch.setattr(control.time,'sleep',lambda seconds:None)
    assert 'no measurable family progress' in control.status(config,60)[0]


def test_status_details_disclose_live_server_cooldown(tmp_path,monkeypatch):
    config=cfg(tmp_path);config['mobile_workspace']=config['workspace']
    p=tmp_path/'pacing.json';p.write_text(json.dumps({'spacing_seconds':1/12,'download_lanes':55,'hold_until':160}))
    config['pacing_state']=str(p)
    monkeypatch.setattr(control,'volume_ready',lambda c:False)
    monkeypatch.setattr(control.time,'time',lambda:100)
    text='\n'.join(control.operating_details(config,{}))
    assert 'HTTP cooldown: 60 seconds remaining' in text
