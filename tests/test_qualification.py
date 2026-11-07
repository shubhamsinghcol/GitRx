"""Real isolated readers and exhaustive paging, without production source data."""
import json
import pytest
from test_backend_portable import release,SID
from gitrx_backend.qualification import run,worker,validate_case


def case(backend='sqlite'):
    return dict(id=backend,profile='fixture',backend=backend,method='search',arguments={'phrase':'warning','limit':1},traverse='all',repeat=2,equivalence_group='same')


def test_isolated_replay_and_concurrent_equivalence(release,tmp_path):
    plan={'format':'gitrx-qualification-plan-1','profiles':{'fixture':str(release)},'cases':[case(),case('git')], 'concurrency':{'case':'sqlite','workers':2}}
    path=tmp_path/'plan.json';path.write_text(json.dumps(plan));result=run(path)
    assert result['passed'] and result['equivalence_groups']=={'same':True}
    assert result['cases'][0]['first']['records']==2 and result['cases'][0]['first']['pages']==2
    assert result['concurrent']['same_results']
    assert all(x['process_peak_rss_bytes']>0 for x in result['concurrent']['workers'])


def test_refuse_unfinished_or_invalid_measurement(release):
    c=case();c['max_pages']=1
    with pytest.raises(ValueError,match='max_pages'):worker(release,c)
    for change in ({'backend':'automatic'},{'method':'shell'},{'repeat':True},{'max_seconds':float('inf')},{'arguments':{'phrase':'x','unknown':3}}):
        c=case();c.update(change)
        with pytest.raises(ValueError):validate_case(c)


def test_measurement_rejects_software_drift(release,tmp_path,monkeypatch):
    import gitrx_backend.qualification as module
    plan={'format':'gitrx-qualification-plan-1','profiles':{'fixture':str(release)},'cases':[case()]}
    path=tmp_path/'plan.json';path.write_text(json.dumps(plan))
    hashes=iter([{'query.py':'before'},{'query.py':'after'}])
    monkeypatch.setattr(module,'code_hashes',lambda:next(hashes))
    monkeypatch.setattr(module,'isolated',lambda *_:{'first':{'result_digest':'same'},'repeat_results_identical':True})
    with pytest.raises(RuntimeError,match='software changed'):run(path)
