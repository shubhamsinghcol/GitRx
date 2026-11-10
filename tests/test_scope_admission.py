"""Explicit, additive scope admission and unselected candidate builds."""
import json
from pathlib import Path
import zipfile

import pytest
from gitrx_backend.selftest import run
from gitrx_backend.jobs import Job, sha
from gitrx_backend.config import open_reader
from gitrx_backend.query import Query
from gitrx.current.parser import PARSER_VERSION

OLD='a0000000-0000-0000-0000-000000000000'
NEW='a1111111-1111-1111-1111-111111111111'


@pytest.fixture
def prepared(tmp_path):
    run(tmp_path/'parent')
    config={'format':'gitrx-job-config-1','workspace':'work','parent_config':'parent/backend.json',
            'parser_version':PARSER_VERSION,'section_codes':['34066-1'],'promote':False}
    scope=json.loads((tmp_path/'parent/scope.json').read_bytes())
    scope['records'][NEW]={'title':'New admitted family','source':'controlled fixture'}
    admission=tmp_path/'approved.json';admission.write_text(json.dumps(scope))
    config.update(admission_scope_path='approved.json',admission_scope_sha256=sha(admission))
    path=tmp_path/'job.json';path.write_text(json.dumps(config))
    xml=f'<document xmlns="urn:hl7-org:v3"><setId root="{NEW}"/><versionNumber value="1"/><code code="34391-3" codeSystem="2.16.840.1.113883.6.1"/><component><structuredBody><component><section><code code="34066-1" codeSystem="2.16.840.1.113883.6.1"/><title>Warning</title><text>New family fixture text</text></section></component></structuredBody></component></document>'
    archive=tmp_path/'new.zip'
    with zipfile.ZipFile(archive,'w') as z:z.writestr('label.xml',xml)
    entry={'setid':NEW,'version':1,'zip_path':str(archive),'publication_dates':['2026-11-07']}
    inbox=tmp_path/'inbox.json';inbox.write_text(json.dumps({'entries':[entry]}))
    return path,inbox,admission,entry


def test_explicit_scope_stages_new_family_preserving_parent_and_source_provenance(prepared,tmp_path):
    config,inbox,admission,entry=prepared
    parent_scope=sha(tmp_path/'parent/scope.json');parent_db=sha(tmp_path/'parent/source/data/gitrx.sqlite')
    with open_reader(tmp_path/'parent/backend.json','git') as reader:old=Query(reader).show(OLD,2)
    entry['source_observation']={'url':'https://dailymed.nlm.nih.gov/dailymed/getFile.cfm?setid='+NEW,
         'final_url':'https://dailymed.nlm.nih.gov/dailymed/getFile.cfm?setid='+NEW,
         'retrieved_at':'2026-11-07T12:00:00Z','sha256':sha(entry['zip_path']),'status':200,
         'headers':{'Content-Type':'application/zip'},'ledger_observation_id':42}
    inbox.write_text(json.dumps({'entries':[entry]}))
    job=Job(config);result=job.cycle(inbox)
    assert result['state']=='staged' and not result['promoted'] and not (job.root/'current.json').exists()
    backend=json.loads(Path(result['config']).read_bytes())
    candidate=json.loads((Path(backend['release_root'])/'reports/EXPANDED-SELECTION.json').read_bytes())
    assert NEW in candidate['upstream_history_unknown_families'] and candidate['known_target_failures'] is None
    frozen=Path(backend['scope_path'])
    assert frozen!=admission and frozen.read_bytes()==admission.read_bytes()
    for adapter in ('sqlite','git'):
        with open_reader(result['config'],adapter) as reader:
            assert 'New family fixture text' in Query(reader).show(NEW,1)['sections'][0]['text']
    with open_reader(tmp_path/'parent/backend.json','git') as reader:assert Query(reader).show(OLD,2)==old
    assert sha(tmp_path/'parent/scope.json')==parent_scope and sha(tmp_path/'parent/source/data/gitrx.sqlite')==parent_db
    import sqlite3
    source=next((job.root/'jobs').glob('*/source/data/gitrx.sqlite'))
    c=sqlite3.connect(source)
    assert c.execute('SELECT url,retrieved_at FROM retrievals').fetchone()==(entry['source_observation']['url'],'2026-11-07T12:00:00Z')
    assert json.loads(c.execute('SELECT observation_json FROM job_source_observations').fetchone()[0])['ledger_observation_id']==42
    c.close()
    assert job.cycle(inbox)['reused'] and not (job.root/'current.json').exists()


def test_default_scope_refuses_new_family(prepared):
    config,inbox,_,_=prepared
    value=json.loads(config.read_bytes());value.pop('admission_scope_path');value.pop('admission_scope_sha256')
    config.write_text(json.dumps(value))
    result=Job(config).cycle(inbox)
    assert result['state']=='quarantined'


def test_admission_pin_and_retained_records_are_enforced(prepared):
    config,inbox,admission,_=prepared
    admission.write_text(admission.read_text()+' ')
    with pytest.raises(ValueError,match='pin mismatch'):Job(config).cycle(inbox)
    value=json.loads(admission.read_bytes());value['records'][OLD]['title']='rewritten old name'
    admission.write_text(json.dumps(value))
    cfg=json.loads(config.read_bytes());cfg['admission_scope_sha256']=sha(admission);config.write_text(json.dumps(cfg))
    with pytest.raises(ValueError,match='preserve every parent'):Job(config).cycle(inbox)


def test_refresh_requires_explicit_admission(prepared,tmp_path,monkeypatch):
    import gitrx_backend.jobs as module
    config,_,_,entry=prepared
    history=tmp_path/'history.json';history.write_text(json.dumps({'metadata':{'current_page':1,'total_pages':1,'total_elements':1},
        'data':{'spl':{'setid':NEW},'history':[{'spl_version':1,'published_date':'Nov 07, 2026'}]}}))
    class HTTP:
        def __init__(self,*a,**k):pass
        def get(self,url,reuse=False):return history if 'history.json' in url else Path(entry['zip_path'])
        def close(self):pass
    monkeypatch.setattr(module,'HTTP',HTTP)
    result=Job(config).refresh([NEW]);assert not result['incidents'] and result['new_candidates']==1
    value=json.loads(config.read_bytes());value.pop('admission_scope_path');value.pop('admission_scope_sha256');config.write_text(json.dumps(value))
    assert Job(config).refresh([NEW])['incidents']


def test_extraction_failed_is_quarantined(prepared,monkeypatch):
    import gitrx_backend.jobs as module
    config,inbox,_,_=prepared
    original=module.persist_document
    def failed(c,root,version,registry):
        original(c,root,version,registry)
        c.execute("UPDATE parse_runs SET status='extraction_failed' WHERE version_id=?",(version['id'],));c.commit()
    monkeypatch.setattr(module,'persist_document',failed)
    result=Job(config).cycle(inbox)
    assert result['state']=='quarantined' and 'parse' in result['incidents'][0]['reason'].lower()

def test_admission_rejects_noncanonical_family_id_and_source_hash(prepared):
    config,inbox,admission,entry=prepared
    scope=json.loads(admission.read_bytes());scope['records']['not-a-setid']={'title':'invalid'}
    admission.write_text(json.dumps(scope))
    value=json.loads(config.read_bytes());value['admission_scope_sha256']=sha(admission);config.write_text(json.dumps(value))
    with pytest.raises(ValueError):Job(config).cycle(inbox)
    scope['records'].pop('not-a-setid');admission.write_text(json.dumps(scope))
    value['admission_scope_sha256']=sha(admission);config.write_text(json.dumps(value))
    entry['source_observation']={'sha256':'0'*64,'status':200,'url':'https://dailymed.nlm.nih.gov/fixture','retrieved_at':'2026-11-07T12:00:00Z'}
    inbox.write_text(json.dumps({'entries':[entry]}))
    with pytest.raises(ValueError,match='matching ZIP bytes'):Job(config).cycle(inbox)

def test_publication_observations_preserve_raw_strings_and_validate_dates():
    rows=[{'publication_observations':[
        {'publication_raw':'Nov 7, 2026','publication_date':'2026-11-07','source_pass_id':4},
        {'published_raw':'Nov 07, 2026','published_date':'2026-11-07'},
        {'publication_raw':'date unavailable','publication_date':None},
        {'publication_raw':None,'publication_date':None}],
        'publication_dates':['2026-11-07']}]
    observations=Job.publication_observations(rows)
    assert len(observations)==4
    assert {o['published_raw'] for o in observations}=={'Nov 7, 2026','Nov 07, 2026','date unavailable',None}
    assert next(o for o in observations if o['published_raw']=='Nov 7, 2026')['source_pass_id']==4
    assert Job.publication_observations([{'publication_dates':['2026-11-07']}])==[
        {'published_date':'2026-11-07','published_raw':'2026-11-07'}]
    with pytest.raises(ValueError,match='does not match'):
        Job.publication_observations([{'publication_observations':[{'publication_raw':'Nov 07, 2026','publication_date':'2026-11-08'}]}])
    with pytest.raises(ValueError,match='do not match'):
        Job.publication_observations([{'publication_observations':[{'publication_raw':'unknown','publication_date':None}], 'publication_dates':['2026-11-07']}])


def test_retained_version_appends_different_raw_observations_without_changing_parent(prepared,tmp_path):
    config,inbox,_,_=prepared
    parent=tmp_path/'parent/backend.json'
    with open_reader(parent,'sqlite') as reader:
        old=Query(reader).show(OLD,1)
    record=next(r for r in json.loads((tmp_path/'parent/release/reports/EXPANDED-SELECTION.json').read_bytes())['versions'] if r['setid']==OLD and r['version']==1)
    original=json.loads(config.read_bytes());original.pop('admission_scope_path');original.pop('admission_scope_sha256');config.write_text(json.dumps(original))
    scope_before=sha(tmp_path/'parent/scope.json');database_before=sha(tmp_path/'parent/source/data/gitrx.sqlite')
    raw=tmp_path/'parent/source/artifacts/sha256'/record['selected_sha256'][:2]/record['selected_sha256']
    inbox.write_text(json.dumps({'entries':[{'setid':OLD,'version':1,'zip_path':str(raw),
         'publication_dates':['2026-01-01'], 'publication_observations':[
           {'publication_raw':'Jan 1, 2026','publication_date':'2026-01-01'},
           {'published_raw':'Jan 01, 2026','published_date':'2026-01-01'},
           {'publication_raw':'not supplied','publication_date':None}]}]}))
    result=Job(config).cycle(inbox)
    assert result['state']=='staged' and result['date_updates']==1 and result['new_versions']==0
    backend=json.loads(Path(result['config']).read_bytes())
    selected=json.loads((Path(backend['release_root'])/'reports/EXPANDED-SELECTION.json').read_bytes())
    obs=next(r for r in selected['versions'] if r['setid']==OLD and r['version']==1)['publication_observations']
    pairs={(o['published_date'],o['published_raw']) for o in obs}
    assert {('2026-01-01','2026-01-01'),('2026-01-01','Jan 1, 2026'),('2026-01-01','Jan 01, 2026'),(None,'not supplied')}<=pairs
    for adapter in ('sqlite','git'):
        with open_reader(result['config'],adapter) as reader:
            assert reader.versions(OLD)[0]['publication_dates']==['2026-01-01']
    with open_reader(parent,'sqlite') as reader:assert Query(reader).show(OLD,1)==old
    assert sha(tmp_path/'parent/scope.json')==scope_before and sha(tmp_path/'parent/source/data/gitrx.sqlite')==database_before
