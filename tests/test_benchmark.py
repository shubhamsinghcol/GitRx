"""Benchmark comparison uses declared scope and records deterministic bounded results."""
import json
import pytest
from test_backend_portable import release, SID
from gitrx_backend.benchmark import measure, compare, record


def run(key='same',seconds=1.0,digest='a'):
    return {'format':'gitrx-performance-run-1','recorded_at':'2026-11-07T00:00:00Z',
            'comparison_key':key,'reader_startup_seconds':.1,
            'operations':[{'id':'show','median_seconds':seconds,'result_digest':digest,'repeat_results_identical':True}]}


def test_comparison_requires_matching_environment_and_both_tolerances():
    old=run();new=run(seconds=1.7)
    assert compare(new,[old])['regressions'][0]['operation']=='show'
    assert compare(new,[run(key='other')])['baseline'] is None
    assert not compare(run(seconds=1.4),[old])['regressions']
    assert not compare(run(seconds=.02),[run(seconds=.01)])['regressions']
    assert compare(run(digest='changed'),[old])['correctness_changes']==['show']
    # Software versions may differ for a comparable regression experiment.
    new['package_version']='new';old['package_version']='old'
    assert compare(new,[old])['baseline']
    with pytest.raises(ValueError):compare(new,[old],ratio=1)


def test_fixture_run_records_scope_stable_results_and_no_source_changes(release):
    before=(release.parent/'native-query.sqlite').read_bytes()
    a=measure(release,'sqlite',[SID],['changed'],repeat=2)
    assert len(a['operations'])==3
    assert a['comparison_descriptor']['backend']=='sqlite'
    assert a['comparison_descriptor']['snapshot']=='f'*64
    assert a['comparison_descriptor']['repeat']==2
    assert all(r['repeat_results_identical'] and r['serialized_bytes']>0 for r in a['operations'])
    assert a['operations'][-1]['page_scope']['partial'] is False
    assert 'OS disk cache uncontrolled' in a['comparison_descriptor']['cache_condition']
    assert (release.parent/'native-query.sqlite').read_bytes()==before
    b=measure(release,'git',[SID],[],repeat=1)
    assert a['comparison_key']!=b['comparison_key']


def test_ledger_append_and_protected_destinations(release,tmp_path):
    ledger=tmp_path/'performance.jsonl'
    first=record(release,'sqlite',[SID],[],ledger,repeat=1)
    second=record(release,'sqlite',[SID],[],ledger,repeat=1)
    assert len(ledger.read_text().splitlines())==2
    assert second['comparison']['baseline']==first['recorded_at']
    with pytest.raises(ValueError,match='outside evidence'):
        record(release,'sqlite',[SID],[],release.parent/'unsafe.jsonl',repeat=1)
    other=tmp_path/'other.jsonl';other.write_text('{"source":"ledger"}\n')
    with pytest.raises(ValueError,match='not a native performance'):
        record(release,'sqlite',[SID],[],other,repeat=1)
