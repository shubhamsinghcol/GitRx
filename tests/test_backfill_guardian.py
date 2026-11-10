import json
from pathlib import Path
import sqlite3
import pytest
import gitrx_backend.backfill_guardian as module


class Child:
    serial=10000
    def __init__(self,*args,**kwargs):
        Child.serial+=1;self.pid=Child.serial;self.code=None;self.terminated=False;self.killed=False
    def poll(self):return self.code
    def terminate(self):self.terminated=True;self.code=-15
    def kill(self):self.killed=True;self.code=-9
    def wait(self,timeout=None):return self.code


@pytest.fixture
def guardian(tmp_path,monkeypatch):
    support=tmp_path/'support';workspace=tmp_path/'ssd'/'work'
    cfg={'format':'gitrx-finite-preparation-1','state_directory':str(support),
         'workspace':str(workspace),'volume':str(tmp_path/'ssd'),'volume_marker':'marker','volume_token':'token',
         'steps':[{'name':'backfill','command':['python','-m','gitrx_backend.backfill','--config','fixture'],
                   'complete':str(workspace/'COMPLETE.json'),'retry_ledger':str(workspace/'network/ledger.sqlite')}]}
    config=tmp_path/'runner.json';config.write_text(json.dumps(cfg))
    clock=[100.];mounted=[True];network=[True];children=[]
    monkeypatch.setattr(module.time,'time',lambda:clock[0])
    monkeypatch.setattr(module,'volume_ready',lambda _:mounted[0])
    monkeypatch.setattr(module,'network_ready',lambda:network[0])
    def popen(*a,**kw):
        c=Child();children.append(c);return c
    monkeypatch.setattr(module.subprocess,'Popen',popen)
    g=module.Guardian(config)
    yield g,clock,mounted,network,children
    g.close()


def test_missing_ssd_waits_without_creating_workspace_then_recovers(guardian):
    g,clock,mounted,network,children=guardian;mounted[0]=False
    assert g.tick()=='waiting-for-SSD' and not children and not g.workspace.exists()
    mounted[0]=True
    assert g.tick()=='running' and len(children)==1
    assert json.loads((g.support/'preparation-status.json').read_bytes())['child_pid']==children[0].pid


def test_network_loss_waits_and_resumes_without_lifetime_retry_exhaustion(guardian):
    g,clock,mounted,network,children=guardian;network[0]=False
    assert g.tick()=='waiting-for-network' and not children
    network[0]=True;assert g.tick()=='running'
    for _ in range(9):
        children[-1].code=1
        with g.log_path.open('ab') as f:f.write(b'URLError: Network is unreachable\n')
        assert g.tick()=='retrying-same-stage'
        clock[0]+=400
        assert g.tick()=='running'
    assert len(children)==10 and not (g.support/'ATTENTION').exists()


def test_manual_pause_survives_reconnect_until_explicit_start(guardian):
    g,clock,mounted,network,children=guardian;g.tick();first=children[-1]
    (g.support/'PAUSED').write_text('pause')
    assert g.tick()=='paused' and first.terminated
    mounted[0]=False;assert g.tick()=='paused'
    (g.support/'PAUSED').unlink();(g.support/'RESUME_REQUESTED').write_text('start')
    assert g.tick()=='waiting-for-SSD' and (g.support/'RESUME_REQUESTED').exists()
    g.workspace.mkdir(parents=True);(g.workspace/'STOP').write_text('pause')
    mounted[0]=True;assert g.tick()=='running'
    assert not (g.workspace/'STOP').exists() and not (g.support/'RESUME_REQUESTED').exists()


def test_ssd_removal_stops_owned_child_then_resumes(guardian):
    g,clock,mounted,network,children=guardian;g.tick();first=children[-1]
    mounted[0]=False;assert g.tick()=='waiting-for-SSD' and first.terminated
    mounted[0]=True;assert g.tick()=='running' and len(children)==2


def test_old_log_network_error_cannot_mask_new_integrity_error(guardian):
    g,clock,mounted,network,children=guardian
    g.log_path.write_text('HTTP 503: old network failure\n')
    g.tick();children[-1].code=1
    with g.log_path.open('ab') as f:f.write(b'Traceback\nRuntimeError: Frozen batch database changed\n')
    assert g.tick()=='needs-attention' and (g.support/'ATTENTION').exists()
    clock[0]+=1000;assert g.tick()=='needs-attention' and len(children)==1
    (g.support/'RECHECK').write_text('explicit start')
    assert g.tick()=='running' and len(children)==2
    assert g.state['attention_acknowledged'] is True and (g.support/'ATTENTION').exists()


def test_retry_after_survives_guardian_restart(guardian):
    g,clock,mounted,network,children=guardian
    ledger=Path(g.step['retry_ledger']);ledger.parent.mkdir(parents=True)
    c=sqlite3.connect(ledger);c.execute('CREATE TABLE transport_state(key PRIMARY KEY,value)')
    c.execute("INSERT INTO transport_state VALUES('retry_after',200)");c.commit();c.close()
    assert g.tick()=='waiting-for-server' and not children
    clock[0]=201;assert g.tick()=='running'


def test_finished_plan_never_starts_new_collection(guardian):
    g,clock,mounted,network,children=guardian
    g.workspace.mkdir(parents=True);Path(g.step['complete']).write_text('{}')
    assert g.tick()=='complete' and not children
    clock[0]+=1000;assert g.tick()=='complete' and not children


def test_stale_worker_heartbeat_restarts_but_long_family_progress_not_required(guardian):
    g,clock,mounted,network,children=guardian;g.tick();child=children[-1]
    g.heartbeat_path.parent.mkdir(parents=True,exist_ok=True)
    clock[0]=1100
    g.heartbeat_path.write_text(json.dumps({'pid':child.pid,'heartbeat_epoch':1099}))
    assert g.tick()=='running' and not child.terminated
    g.heartbeat_path.write_text(json.dumps({'pid':child.pid,'heartbeat_epoch':100}))
    assert g.tick()=='retrying-same-stage' and child.terminated


def test_progress_cache_keeps_last_observation_for_offline_controls(guardian):
    g,clock,mounted,network,children=guardian
    g.workspace.mkdir(parents=True)
    status={'attempted_families':5,'target_families':100,'versions':{'new_downloaded_valid':12}}
    (g.workspace/'status.json').write_text(json.dumps(status));g.tick()
    cached=json.loads((g.support/'last-progress.json').read_bytes())
    assert cached['attempted_families']==5 and cached['_cached_at_epoch']==100
    mounted[0]=False;assert g.tick()=='waiting-for-SSD'
    assert json.loads((g.support/'last-progress.json').read_bytes())==cached

def test_storage_recovery_hook_is_opt_in_and_restarts_only_after_verified_success(guardian):
    g,clock,mounted,network,children=guardian
    g.policy['recovery_command']=['python','verified-recovery','--config','fixture']
    g.tick();children[-1].code=1
    with g.log_path.open('ab') as f:f.write(b'sqlite3.DatabaseError: database disk image is malformed\n')
    assert g.tick()=='recovering' and len(children)==2
    assert g.state['recovery_pending'] and g.state['child_role']=='recovery'
    children[-1].code=0
    assert g.tick()=='retrying-same-stage' and not g.state['recovery_pending']
    clock[0]+=2;assert g.tick()=='running' and len(children)==3
    children[-1].code=1
    with g.log_path.open('ab') as f:f.write(b'sqlite3.DatabaseError: database disk image is malformed\n')
    assert g.tick()=='needs-attention' and len(children)==3


def test_recovery_remains_pending_across_ssd_loss_and_does_not_blindly_skip_it(guardian):
    g,clock,mounted,network,children=guardian
    g.policy['recovery_command']=['python','verified-recovery']
    g.tick();children[-1].code=1
    with g.log_path.open('ab') as f:f.write(b'sqlite3.DatabaseError: file is not a database\n')
    assert g.tick()=='recovering'
    recovery=children[-1];mounted[0]=False
    assert g.tick()=='waiting-for-SSD' and recovery.terminated and g.state['recovery_pending']
    mounted[0]=True;assert g.tick()=='recovering' and children[-1] is not recovery
    children[-1].code=1
    with (g.support/'recovery.log').open('ab') as f:f.write(b'proof did not establish safe recovery\n')
    assert g.tick()=='needs-attention' and (g.support/'ATTENTION').exists()


def test_each_worker_has_new_session_and_close_terminates_owned_child(guardian,monkeypatch):
    g,clock,mounted,network,children=guardian;seen=[]
    def popen(*args,**kwargs):seen.append(kwargs);child=Child();children.append(child);return child
    monkeypatch.setattr(module.subprocess,'Popen',popen)
    assert g.tick()=='running' and seen[0]['start_new_session'] is True
    child=children[-1];g.stop_child()
    assert child.terminated and g.child is None


def test_prepared_local_mode_keeps_worker_running_without_ssd(guardian,tmp_path):
    g,clock,mounted,network,children=guardian
    local=tmp_path/'mobile';local.mkdir();config=local/'config.json';config.write_text('{}')
    pin=module.sha(config);(local/'MOBILE-READY.json').write_text(json.dumps({'config_sha256':pin}))
    g.policy.update(mobile_workspace=str(local),mobile_config_sha256=pin)
    g.workspace=local;g.step['complete']=str(local/'COMPLETE.json');g.heartbeat_path=local/'worker-heartbeat.json'
    mounted[0]=False
    assert g.tick()=='running' and len(children)==1
    assert g.tick()=='running' and not children[0].terminated
    config.write_text('{"changed":true}')
    assert g.tick()=='needs-attention' and children[0].terminated
