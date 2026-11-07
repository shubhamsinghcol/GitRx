"""Research helpers expose evidence identities and stop uncertain lineage."""
import json
import pytest
from test_backend_portable import release,SID
from test_changes import Reader,section
from gitrx_backend.query import Query
from gitrx_backend.config import open_reader
from gitrx_backend.research import provenance
from gitrx_backend.selective_install import install
from gitrx_backend.cli import main
from gitrx_backend.mcp import Server


class Complete(Reader):
    def evidence(self,sid,version):return {'extraction':{'status':'complete'}}
    def inventory(self,sid):return {'status':'unknown'}


def test_conservative_token_replay_return_and_page_equivalence():
    q=Query(Complete({1:[section('risk low')],2:[section('risk high')],3:[section('risk low')]}))
    result=q.token_provenance(SID,3,1)
    risk,low=result['tokens']
    assert risk['uninterrupted_from_version']==1
    assert low['uninterrupted_from_version']==3 and low['first_observed_token_version']==1 and low['observed_return']
    assert result['boundary']=='record_start'
    assert q.token_provenance(SID,3,1,limit=1,offset=1)['tokens']==[low]
    assert low['origin_citation']['raw_sha256']=='3' and low['first_observed_token_citation']['raw_sha256']=='1'


def test_provenance_stops_at_gaps_partial_or_unpaired_occurrences():
    for rows,boundary in [({1:[section('risk')],3:[section('risk')]},'numeric_gap'),
                          ({1:[section('risk',1),section('risk',2)],2:[section('risk',3),section('risk',4)]},'unpaired_occurrence')]:
        v=max(rows);ordinal=rows[v][0]['ordinal'];r=Query(Complete(rows)).token_provenance(SID,v,ordinal)
        assert r['boundary']==boundary and r['earlier_lineage_unknown'] and r['tokens'][0]['uninterrupted_from_version']==v
    class Partial(Complete):
        def evidence(self,*args):return {'extraction':{'status':'partial'}}
    r=Query(Partial()).token_provenance(SID,2,1)
    assert r['boundary']=='extraction_unknown' and r['earlier_lineage_unknown']
    r=Query(Complete({1:[section('risk risk')],2:[section('risk risk risk')]})).token_provenance(SID,2,1)
    assert all(t['token_alignment_ambiguous'] for t in r['tokens'])
    with pytest.raises(ValueError,match='bound'):
        provenance(Query(Complete({1:[section('a '*501)],2:[section('b '*501)]})),SID,2,1)


def test_citation_check_client_parity_negative_and_unknown_metadata(release,capsys):
    with open_reader(release,'sqlite') as reader:
        q=Query(reader);citation=q.show(SID,2)['citation']
        args=dict(sid=SID,version=2,raw_sha256=citation['raw_sha256'],snapshot=reader.snapshot,ordinal=1,quote='warning changed')
        assert q.verify_citation(**args)['verified']
        for change in ({'raw_sha256':'0'*64},{'ordinal':99},{'quote':'absent'}):
            changed=dict(args,**change);assert not q.verify_citation(**changed)['verified']
        with pytest.raises(ValueError):q.verify_citation(**dict(args,snapshot='0'*64))
        server=Server(reader);server.initialized=server.ready=True
        result=server.dispatch({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'verify_citation','arguments':args}})
        assert result['result']['structuredContent']==q.verify_citation(**args)
        assert main(['--config',str(release),'verify-citation',SID,'2','--raw-sha256',citation['raw_sha256'],'--snapshot',reader.snapshot,'--ordinal','1','--quote','warning changed'])==0
        assert json.loads(capsys.readouterr().out)['verified']


def test_version_citation_exists_even_when_extraction_has_no_occurrences():
    class Empty(Complete):
        def read(self,sid,version):return [],{'snapshot':self.snapshot,'raw_sha256':'a'*64}
    q=Query(Empty({1:[]}))
    args=dict(sid=SID,version=1,snapshot=q.reader.snapshot,raw_sha256='a'*64)
    assert q.verify_citation(**args)['verified']
    assert not q.verify_citation(**args,ordinal=1)['verified']
    assert not q.verify_citation(**args,quote='absent')['verified']


def test_explicit_cohort_date_inventory_delegates_selection(release):
    with open_reader(release) as r:
        q=Query(r);result=q.inventory_on_date([SID,SID],'2026-01-01',limit=1)
        assert result['total_families']==1 and result['rows'][0]['date_selection']['selected_version']==2
        assert result['rows'][0]['inventory']['status']=='unknown'
        assert q.inventory_on_date([SID],'2025-01-01')['rows'][0]['citation'] is None
        with pytest.raises(ValueError):q.inventory_on_date([SID],'bad',offset=100)
        assert q.inventory_on_date(on_date='2026-01-01',all_families=True)['rows']==result['rows']
        with pytest.raises(ValueError):q.inventory_on_date([SID],'2026-01-01',all_families=True)


def test_selective_install_reads_selected_shards_and_preserves_pins(release,tmp_path):
    with pytest.raises(ValueError,match='outside evidence'):
        install(release,[SID],release.parent/'unsafe-installation')
    target=tmp_path/'selected';config=install(release,[SID],target)
    assert not (target/'shards/b').exists() and (target/'shards/a').is_dir()
    with open_reader(config,'git') as reader:
        q=Query(reader);assert q.show(SID,2)['version']==2
        assert q.families()['uninstalled_shards']==list('0123456789bcdef')
        plan=q.installation_plan([SID]);assert len(plan['required_shards'])==1 and plan['required_shards'][0]['installed']
        assert len(plan['omitted_shards'])==15
    with pytest.raises(ValueError,match='new destination'):install(release,[SID],target)
    with open_reader(release,'git') as source,open_reader(config,'git') as selected:
        assert source.read(SID,2)==selected.read(SID,2)
    shard=target/'shards/a';shard.rename(target/'temporarily-unavailable')
    try:
        with open_reader(config,'git') as missing:
            with pytest.raises(ValueError,match='uninstalled'):Query(missing).show(SID,2)
            assert 'a' in Query(missing).families()['uninstalled_shards']
    finally:(target/'temporarily-unavailable').rename(shard)
