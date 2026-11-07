"""Pinned additive metadata preserves provenance and old evidence identities."""
import json
import pytest
from test_release_cycle import job, SID
from gitrx_backend.config import open_reader
from gitrx_backend.evidence_metadata import build, Projection, _version_record, _write_projection
from gitrx_backend.query import Query


def test_real_fixture_build_adapter_parity_and_corruption(job,tmp_path):
    config=tmp_path/'parent/backend.json';cfg=json.loads(config.read_bytes())
    root=config.parent/cfg['release_root']
    target=tmp_path/'metadata';pin=build(root,target,cfg['snapshot'])
    assert build(root,target,cfg['snapshot'])==pin
    cfg.update(evidence_metadata=str(target/'evidence.sqlite'),evidence_metadata_sha256=pin['sha256'])
    enriched=config.with_name('enriched.json');enriched.write_text(json.dumps(cfg))
    evidence=[]
    for backend in ('git','sqlite'):
        with open_reader(enriched,backend) as r:
            result=Query(r).show(SID,2)
            assert result['evidence']['metadata_status']=='pinned'
            assert result['citation']['evidence']==result['evidence']
            assert result['evidence']['extraction']['status'] in ('complete','partial')
            evidence.append(result['evidence'])
    assert evidence[0]==evidence[1]
    with open_reader(config,'sqlite') as r:
        assert Query(r).show(SID,2)['evidence']['metadata_status']=='unavailable'
    with pytest.raises(ValueError,match='snapshot'):build(root,tmp_path/'bad','0'*64)
    with (target/'evidence.sqlite').open('ab') as f:f.write(b'corrupt')
    with pytest.raises(ValueError,match='hash'):open_reader(enriched)


def test_bulk_routes_diagnostics_and_unknown_inventory_are_not_invented(tmp_path):
    row={'setid':SID,'version':1,'selected_sha256':'a'*64,
        'parse_runs':[{'id':'x:1','status':'partial','diagnostics_json':'[{"code":"image_without_text"}]','parser_version':'test'}],
        'retrievals':[{'url':'https://dailymed-data.nlm.nih.gov/month.zip','status':None,'kind':'bulk_member','artifact_sha256':'a'*64},
                      {'url':'https://dailymed.nlm.nih.gov/getFile.cfm','status':404,'artifact_sha256':'b'*64}],
        'bulk_member_provenance':{'url':'https://dailymed-data.nlm.nih.gov/month.zip','member':'nested.zip','xml_member':'label.xml','transport':'offline member from retained HTTP206 ranges','source_ledger_sha256':'b'*64}}
    record=_version_record(row)
    assert record['extraction']['diagnostics'][0]['code']=='image_without_text'
    assert len(record['publisher_references'])==1
    ref=record['publisher_references'][0]
    assert ref['kind']=='bulk-member' and ref['status'] is None and ref['member']=='nested.zip'
    assert [r['status'] for r in record['retrieval_routes']]==[None,404]
    manifest={'versions':[row],'upstream_history_unknown_families':[SID],'known_target_failures':0}
    target=tmp_path/'metadata';pin=_write_projection(manifest,target,'f'*64)
    p=Projection(target/'evidence.sqlite',pin['sha256'],'f'*64)
    try:
        inventory=p.inventory(SID)
        assert inventory['unknown_upstream_history'] is True
        assert inventory['known_target_count']==1 and not inventory['historical_completeness_claim']
        with pytest.raises(ValueError,match='source hash'):p.evidence(SID,1,'c'*64)
    finally:p.close()
    with pytest.raises(ValueError,match='snapshot'):Projection(target/'evidence.sqlite',pin['sha256'],'0'*64)
