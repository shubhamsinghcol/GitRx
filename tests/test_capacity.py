import io
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
import zipfile

import pytest
import gitrx_backend.capacity as module
from gitrx_backend.capacity import Capacity, sample_indices, sampled_versions, scenarios

SID='a0000000-0000-0000-0000-000000000000'


def test_seeded_sampling_and_family_weighted_expansion():
    assert sample_indices(100,6,1,'rx')==sample_indices(100,6,1,'rx')
    assert len(set(sample_indices(100,6,1,'rx')))==6
    versions=sampled_versions([[v,[]] for v in range(-1,20)],1,SID)
    assert len(versions)==2 and all(v>0 for v in versions)
    assert sampled_versions([[1,[]]],1,SID)==[1]
    result=scenarios({'rx':100},[
       {'groups':['rx'],'history_state':'accepted','known_versions':4,
        'zip_results':[{'state':'downloaded','bytes':10},{'state':'downloaded','bytes':30}]},
       {'groups':['rx'],'history_state':'unknown'}])['rx']
    assert result['sampled_version_expansion_bytes']==8000
    assert result['unknown_or_failed_families']==1


@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(Capacity,'check_volume',lambda self:None)
    monkeypatch.setattr(module,'TYPES',{})
    config=tmp_path/'config.json'
    config.write_text(json.dumps({'format':'gitrx-capacity-1','workspace':str(tmp_path/'work'),
                     'volume':str(tmp_path),'request_cap':100,'global_sample':1,'seed':1}))
    calls=[];wrong=[False]
    import gitrx_backend.jobs as jobs
    monkeypatch.setattr(jobs.time,'sleep',lambda _:None)
    class Response(io.BytesIO):
        code=200;headers={}
    class Opener:
        def open(self,req,**kw):
            url=req.full_url;calls.append(url)
            if 'getFile.cfm' in url:
                version=int(parse_qs(urlsplit(url).query)['version'][0])
                sid='b0000000-0000-0000-0000-000000000000' if wrong[0] else SID
                xml=f'<document xmlns="urn:hl7-org:v3"><setId root="{sid}"/><versionNumber value="{version}"/><code code="34391-3" codeSystem="2.16.840.1.113883.6.1"/><component><structuredBody><component><section><code code="34066-1" codeSystem="2.16.840.1.113883.6.1"/><title>Warning</title><text>Fixture {version}</text></section></component></structuredBody></component></document>'
                output=io.BytesIO()
                with zipfile.ZipFile(output,'w') as z:z.writestr('label.xml',xml)
                return Response(output.getvalue())
            if '/history.json' in url:
                value={'metadata':{'current_page':1,'total_elements':3,'total_pages':1},
                       'data':{'spl':{'setid':SID},'history':[{'spl_version':v,'published_date':'Nov 01, 2026'} for v in (1,2,3)]}}
            else:
                value={'metadata':{'current_page':1,'total_elements':1,'total_pages':1},
                       'data':[{'setid':SID,'spl_version':3,'title':'Fixture'}]}
            return Response(json.dumps(value).encode())
    monkeypatch.setattr(jobs.urllib.request,'build_opener',lambda *a:Opener())
    return config,calls,wrong


def test_sample_preserves_identity_and_real_parse_then_reuses_finished(setup):
    config,calls,_=setup
    job=Capacity(config)
    report=job.run()
    assert job.state['complete']
    assert len(job.state['samples'][SID]['zip_results'])==2
    assert all(r['parses'][0]['status']!='failed' for r in job.state['samples'][SID]['zip_results'])
    assert report['scenarios']['all_current']['usable_families']==1
    assert job.c.execute('select count(*) from document_type_observations').fetchone()[0]==2
    pinned=(job.state_path.read_bytes(),(job.root/'REPORT.json').read_bytes())
    count=len(calls);job.close()
    job=Capacity(config);job.run();assert len(calls)==count
    assert (job.state_path.read_bytes(),(job.root/'REPORT.json').read_bytes())==pinned
    job.close()


def test_identity_conflict_is_not_counted_as_downloaded(setup):
    config,calls,wrong=setup;wrong[0]=True
    job=Capacity(config);report=job.run()
    assert report['scenarios']['all_current']['usable_families']==0
    assert report['scenarios']['all_current']['sampled_version_expansion_bytes'] is None
    assert job.c.execute('select count(*) from identity_conflicts').fetchone()[0]==2
    job.close()


def test_restart_after_first_zip_checkpoint_reuses_history_and_zip(setup,monkeypatch):
    config,calls,_=setup
    original=module.atomic;crashed=[False]
    def interrupt(path,value):
        result=original(path,value)
        if Path(path).name=='sample.json' and any(len(s.get('zip_results',[]))==1 for s in value.get('samples',{}).values()) and not crashed[0]:
            crashed[0]=True;raise RuntimeError('simulated interruption')
        return result
    monkeypatch.setattr(module,'atomic',interrupt)
    job=Capacity(config)
    with pytest.raises(RuntimeError,match='simulated interruption'):job.run()
    before=list(calls);job.close()
    monkeypatch.setattr(module,'atomic',original)
    job=Capacity(config);job.run();job.close()
    assert sum('/history.json' in x for x in calls)==2
    assert sum('getFile.cfm' in x for x in calls)==2
    assert len(calls)==len(before)+1


def test_same_body_urls_keep_distinct_http_observations(setup):
    config,calls,_=setup;job=Capacity(config)
    first=job.response('https://dailymed.nlm.nih.gov/fixture-a')
    second=job.response('https://dailymed.nlm.nih.gov/fixture-b')
    cached=job.response('https://dailymed.nlm.nih.gov/fixture-a')
    assert first['sha256']==second['sha256']
    assert first['id']!=second['id'] and cached['id']==first['id']
    job.close()


def test_finished_derived_rows_are_checked_on_resume(setup):
    config,calls,_=setup;job=Capacity(config);job.run()
    job.c.execute("UPDATE narrative_blocks SET text='modified',normalized_text='modified' WHERE id=(SELECT MIN(id) FROM narrative_blocks)")
    job.c.commit();job.close()
    job=Capacity(config)
    with pytest.raises(RuntimeError,match='derived source checkpoint changed'):job.run()
    job.close()


def test_explicit_legacy_adoption_independently_reparses_and_keeps_original(setup):
    config,calls,_=setup;job=Capacity(config);job.run();job.close()
    state=Path(json.loads(config.read_bytes())['workspace'])/'sample.json'
    value=json.loads(state.read_bytes())
    for item in value['samples'].values():
        item.pop('history_checkpoint_sha256',None)
        for r in item['zip_results']:r.pop('source_checkpoint_sha256',None)
    state.write_text(json.dumps(value))
    before=state.read_bytes();count=len(calls)
    job=Capacity(config)
    with pytest.raises(RuntimeError,match='history checkpoint changed or missing'):job.run()
    adoption=job.adopt_checkpoints()
    assert len(adoption['verified'])==2
    assert next((job.root/'adoptions').glob('*/sample-before.json')).read_bytes()==before
    job.run();assert len(calls)==count;job.close()


def test_adoption_refuses_preexisting_derived_tamper(setup):
    config,calls,_=setup;job=Capacity(config);job.run()
    job.c.execute("UPDATE narrative_blocks SET text='modified',normalized_text='modified' WHERE id=(SELECT MIN(id) FROM narrative_blocks)")
    job.c.commit()
    with pytest.raises(RuntimeError,match='independently parsed rows differ'):job.adopt_checkpoints()
    job.close()
