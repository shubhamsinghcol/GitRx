"""Observed checks do not imply global freshness or successful acquisition."""
import json
from pathlib import Path
import pytest
from test_release_cycle import job, inbox, SID
from gitrx_backend.config import open_reader
from gitrx_backend.query import Query
from gitrx_backend.jobs import Job


def history_response(root,rows):
    path=root/'history.json';path.write_text(json.dumps({'metadata':{'total_elements':len(rows),'total_pages':1,'current_page':1},'data':{'spl':{'setid':SID},'history':rows}}));return path


def test_status_without_observations_is_read_only(job):
    assert not (job.root/'operations.sqlite').exists()
    result=job.status()
    assert result['operation_mode']=='manual' and result['observation_status']=='no recorded operations'
    assert not (job.root/'operations.sqlite').exists()
    from gitrx_backend.operational_status import start
    start(job.root,'cycle')
    row=job.status()['recent_operations'][0]
    assert row['state']=='started' and row['completion_status']=='unfinished-outcome-unknown'


def test_nochange_inventory_check_then_failure_preserves_last_success(job,tmp_path,monkeypatch):
    import gitrx_backend.jobs as module
    path=history_response(tmp_path,[{'spl_version':v,'published_date':'Jan 01, 2026'} for v in (1,2)])
    class HTTP:
        def __init__(self,*a):pass
        def get(self,*a,**kw):return path
        def close(self):pass
    monkeypatch.setattr(module,'HTTP',HTTP)
    before=job.status()['selected_release']['snapshot']
    result=job.refresh([SID]);assert not result['incidents']
    cycle=job.cycle(result['inbox'])
    assert cycle['state']=='no-change' and not (job.root/'current.json').exists()
    row=job.status()['family_checks'][0]
    assert row['state']=='no-change' and row['last_successful_agreeing_inventory']
    stamp=row['last_successful_agreeing_inventory']
    # Successful HTTP transport is insufficient when the history is malformed.
    path.write_text('{}')
    result=job.refresh([SID]);assert result['incidents']
    row=job.status()['family_checks'][0]
    assert row['state']=='inventory-failed' and row['last_successful_agreeing_inventory']==stamp
    assert job.status()['selected_release']['snapshot']==before
    assert job.status()['family_checks_total']==1
    assert not (job.root/'current.json').exists()


def test_failed_cycle_and_quarantine_are_distinct_from_selection(job,tmp_path):
    before=job.status()['selected_release']['snapshot']
    result=job.cycle(inbox(tmp_path,xml_sid='b0000000-0000-0000-0000-000000000000'))
    assert result['state']=='quarantined'
    assert job.status()['recent_operations'][0]['state']=='quarantined'
    with pytest.raises(FileNotFoundError):job.cycle(tmp_path/'missing.json')
    assert job.status()['recent_operations'][0]['state']=='failed'
    assert job.status()['selected_release']['snapshot']==before


def test_search_projection_is_fresh_pinned_and_survives_activation(job,tmp_path,monkeypatch):
    cfg=json.loads(job.path.read_text());cfg['search_projection']=True;job.path.write_text(json.dumps(cfg));job=Job(job.path)
    import gitrx_backend.jobs as module
    original=module.atomic
    def interrupt(path,value):
        if Path(path)==job.root/'current.json':raise RuntimeError('simulated selection interruption')
        return original(path,value)
    candidate=inbox(tmp_path);monkeypatch.setattr(module,'atomic',interrupt)
    with pytest.raises(RuntimeError,match='interruption'):job.cycle(candidate)
    failed=job.status()['recent_operations'][0]
    assert failed['state']=='failed' and failed['details']['last_recorded_build_phase']=='selection'
    assert failed['details']['snapshot']
    projection=next((job.root/'releases').glob('*/search-projection/search.sqlite'))
    pin_before=module.sha(projection)
    monkeypatch.setattr(module,'atomic',original)
    result=job.cycle(candidate);assert module.sha(projection)==pin_before
    new=Path(result['config']);saved=json.loads(new.read_text())
    assert Path(saved['search_index']).exists()
    assert job.status()['serving_profile']['search_accelerated']
    with open_reader(new,'sqlite') as reader:
        assert reader.snapshot==result['snapshot']
        assert Query(reader).search('new wording')['hits']
    second=job.cycle(inbox(tmp_path,version=5,name='second.json'))
    next_cfg=json.loads(Path(second['config']).read_text())
    assert next_cfg['search_index']!=saved['search_index']
    pin=json.loads((Path(next_cfg['search_index']).parent/'PIN.json').read_text())
    assert pin['snapshot']==second['snapshot']
    job.activate(new,second['snapshot'])
    assert job.status()['selected_release']['snapshot']==result['snapshot']
    assert job.status()['serving_profile']['search_accelerated']
    assert job.status()['recent_operations'][0]['state']=='selected'


def test_profile_validation_and_outside_refresh_scope(job,monkeypatch):
    cfg=json.loads(job.path.read_text());cfg['search_projection']='yes';job.path.write_text(json.dumps(cfg))
    with pytest.raises(ValueError,match='boolean'):Job(job.path)
    import gitrx_backend.jobs as module
    class NoNetwork:
        def __init__(self,*a):pass
        def get(self,*a,**kw):raise AssertionError('Outside-scope family must not be fetched')
        def close(self):pass
    monkeypatch.setattr(module,'HTTP',NoNetwork)
    result=job.refresh(['ffffffff-ffff-ffff-ffff-ffffffffffff'])
    assert result['incidents'] and result['attempted_family_count']==0
    assert result['unattempted_family_count']==1 and job.status()['family_checks_total']==0



def test_later_family_failure_does_not_erase_earlier_success_or_mark_rest_checked(job,tmp_path,monkeypatch):
    import gitrx_backend.jobs as module
    parent=json.loads(job.parent().read_text());scope=json.loads((job.parent().parent/parent['scope_path']).read_text())['records']
    others=[x for x in scope if x!=SID];second,third=others[:2]
    path=history_response(tmp_path,[{'spl_version':v,'published_date':'Jan 01, 2026'} for v in (1,2)])
    class HTTP:
        def __init__(self,*a):pass
        def get(self,url,**kw):
            if second in url:raise OSError('source unavailable')
            return path
        def close(self):pass
    monkeypatch.setattr(module,'HTTP',HTTP)
    result=job.refresh([SID,second,third])
    assert result['requested_family_count']==3 and result['attempted_family_count']==2
    assert result['unattempted_family_count']==1
    rows={r['setid']:r for r in job.status()['family_checks']}
    assert rows[SID]['last_successful_agreeing_inventory']
    assert rows[second]['state']=='inventory-failed' and rows[second]['last_successful_agreeing_inventory'] is None
    assert third not in rows
