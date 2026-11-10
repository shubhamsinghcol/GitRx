import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from gitrx_backend import acquisition_runner as runner


def config(tmp_path):
    p=tmp_path/'runner.json'
    data={'format':'gitrx-finite-preparation-1','state_directory':str(tmp_path/'state'),
          'volume':str(tmp_path/'missing'),'volume_marker':'marker','volume_token':'token',
          'workspace':str(tmp_path/'work'),'steps':[{'name':'fixture','complete':str(tmp_path/'DONE'),
                                                    'command':['fixture']} ]}
    p.write_text(json.dumps(data));return p,data


def test_missing_volume_no_child_or_fallback_writes(tmp_path,monkeypatch):
    p,data=config(tmp_path)
    monkeypatch.setattr(runner.subprocess,'run',lambda *a,**k:pytest.fail('must not start child'))
    assert runner.run(p)==0
    assert not Path(data['volume']).exists()
    assert json.loads((tmp_path/'state/preparation-status.json').read_bytes())['phase']=='waiting-for-SSD'


def test_same_finite_job_verifies_on_restart_and_completes(tmp_path,monkeypatch):
    p,data=config(tmp_path);calls=[]
    monkeypatch.setattr(runner,'volume_ready',lambda config:True)
    def execute(*a,**k):
        calls.append(a);(tmp_path/'DONE').write_text('verified');return SimpleNamespace(returncode=0)
    monkeypatch.setattr(runner.subprocess,'run',execute)
    assert runner.run(p)==0 and runner.run(p)==0 and len(calls)==2
    state=json.loads((tmp_path/'state/preparation-status.json').read_bytes())
    assert state['phase']=='complete'
    data['restart_cap']=7;p.write_text(json.dumps(data))
    with pytest.raises(ValueError,match='pin changed'):runner.run(p)


def test_integrity_failure_does_not_retry(tmp_path,monkeypatch):
    p,data=config(tmp_path);calls=[]
    monkeypatch.setattr(runner,'volume_ready',lambda config:True)
    def execute(*a,**k):calls.append(a);return SimpleNamespace(returncode=1)
    monkeypatch.setattr(runner.subprocess,'run',execute)
    assert runner.run(p)==1 and len(calls)==1
    assert json.loads((tmp_path/'state/preparation-status.json').read_bytes())['phase']=='needs-attention'

def test_killed_child_resumes_same_finite_stage(tmp_path,monkeypatch):
    p,data=config(tmp_path);calls=[];clock=[0.]
    monkeypatch.setattr(runner,'volume_ready',lambda config:True)
    monkeypatch.setattr(runner.time,'time',lambda:clock[0])
    monkeypatch.setattr(runner.time,'sleep',lambda seconds:clock.__setitem__(0,clock[0]+seconds))
    def execute(*a,**k):
        calls.append(a)
        if len(calls)==1:return SimpleNamespace(returncode=-9)
        (tmp_path/'DONE').write_text('verified');return SimpleNamespace(returncode=0)
    monkeypatch.setattr(runner.subprocess,'run',execute)
    assert runner.run(p)==0 and len(calls)==2
    state=json.loads((tmp_path/'state/preparation-status.json').read_bytes())
    assert state['phase']=='complete' and state['failures']['fixture']==1
