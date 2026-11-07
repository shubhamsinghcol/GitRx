from test_backend_portable import release,SID
from gitrx_backend.config import open_reader
from gitrx_backend.query import Query
from gitrx_backend.mcp import Server
from gitrx_backend.cli import main
import json
import pytest


def test_date_and_phrase_history_are_observed_not_inferred(release):
    for backend in ('git','sqlite'):
        with open_reader(release,backend) as reader:
            q=Query(reader)
            assert q.resolve('Synthetic')['selected']['setid']==SID
            assert q.show_date('Synthetic','2025-12-31')['date_selection']['selected_version'] is None
            assert q.show_date('Synthetic','2026-01-01')['date_selection']['selected_version']==2
            first=q.first_observed('Synthetic','CHANGED',code='34066-1')
            assert first['first_observed']['version']==2 and first['previous_captured_version']['version']==1
            assert first['first_observed']['matching_occurrences']==2
            assert q.first_observed(SID,'absent',max_versions=1)['scan_limited']
            with pytest.raises(ValueError):q.show_date(SID,'2026-02-30')
            # Two changed repeated occurrences cannot be paired by position.
            diff=q.compare(SID,1,2,code='34066-1')
            assert len(diff['comparisons'])==4
            assert all(r['match_rule']=='unmatched-or-ambiguous' for r in diff['comparisons'])


def test_date_inversions_undated_versions_gaps_and_shifted_sections():
    class Reader:
        snapshot='test'
        def families(self):return [{'setid':SID,'title':'Example'}]
        def versions(self,sid):return [dict(version=v,publication_dates=d,effective_date='19990101') for v,d in [(1,['2020-02-01']),(3,['2020-01-01']),(4,[])]]
        def read(self,sid,version):
            row=dict(ordinal=7 if version==1 else 9,loinc_code='34066-1',code_system='loinc',title='Warning',own_text='risk' if version==1 else 'risk NEW',parent_ordinal=None,ancestry_path='/')
            return [row],{'snapshot':'test','raw_sha256':str(version)}
    q=Query(Reader());a=q.show_date('Example','2020-01-15')
    assert a['version']==3 and a['date_selection']['publication_order_conflict']
    assert a['date_selection']['versions_without_usable_publication_date']==[4]
    first=q.first_observed(SID,'NEW');assert first['first_observed']['version']==3 and first['missing_version_numbers']==[2]
    comparison=q.compare(SID,1,3)['comparisons'][0]
    assert comparison['before']['ordinal']==7 and comparison['after']['ordinal']==9
    assert comparison['content_changed'] and comparison['match_rule']=='unique-heading'


def test_cli_and_mcp_use_the_same_real_adapter(release,capsys):
    assert main(['--config',str(release),'show','Synthetic','--date','2026-01-01','--ordinal','1'])==0
    assert json.loads(capsys.readouterr().out)['version']==2
    assert main(['--config',str(release),'when-added','Synthetic','changed'])==0
    assert json.loads(capsys.readouterr().out)['first_observed']['version']==2
    with open_reader(release,'sqlite') as reader:
        server=Server(reader)
        assert server.dispatch({'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25'}})['result']['capabilities']['tools']
        server.dispatch({'jsonrpc':'2.0','method':'notifications/initialized'})
        call={'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'first_observed','arguments':{'sid':SID,'phrase':'changed'}}}
        result=server.dispatch(call)['result'];assert not result['isError']
        assert result['structuredContent']['first_observed']['version']==2
        call['params']['arguments']['max_versions']=True
        assert server.dispatch(call)['error']['code']==-32602
