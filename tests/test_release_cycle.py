import io,json,zipfile
from pathlib import Path
import pytest
from gitrx_backend.selftest import run
from gitrx_backend.jobs import Job,sha
from gitrx_backend.config import open_reader
from gitrx_backend.query import Query
from gitrx.current.parser import PARSER_VERSION
SID='a0000000-0000-0000-0000-000000000000'

@pytest.fixture
def job(tmp_path):
    parent=tmp_path/'parent';run(parent)
    cfg=tmp_path/'job.json';cfg.write_text(json.dumps({'format':'gitrx-job-config-1','workspace':'workspace','parent_config':'parent/backend.json','parser_version':PARSER_VERSION,'section_codes':['34066-1']}))
    return Job(cfg)

def inbox(root,version=4,xml_sid=SID,name='inbox.json'):
    xml=f'<document xmlns="urn:hl7-org:v3"><setId root="{xml_sid}"/><versionNumber value="{version}"/><code code="34391-3" codeSystem="2.16.840.1.113883.6.1"/><component><structuredBody><component><section><code code="34066-1" codeSystem="2.16.840.1.113883.6.1"/><title>WARNING</title><text>new wording {version}</text></section></component></structuredBody></component></document>'
    target=root/(name+'.zip')
    with zipfile.ZipFile(target,'w') as z:z.writestr('label.xml',xml)
    row={'setid':SID,'version':version,'zip_path':str(target),'publication_dates':['2026-11-06']}
    path=root/name;path.write_text(json.dumps({'entries':[row,row]}));return path

def test_cycle_duplicate_rerun_late_arrival_and_old_source_unchanged(job,tmp_path):
    old=sha(tmp_path/'parent/source/data/gitrx.sqlite')
    path=inbox(tmp_path);result=job.cycle(path)
    assert result['state']=='promoted' and result['new_versions']==1
    assert job.cycle(path)['reused']
    first=result['config']
    late=job.cycle(inbox(tmp_path,3,name='late.json'));assert late['new_versions']==1
    with open_reader(late['config'],'sqlite') as reader:
        q=Query(reader);assert [r['version'] for r in q.versions(SID)['versions']]==[1,2,3,4]
        assert q.coverage(SID)['missing_version_count']==0
        assert len(q.section_history(SID,'34066-1')['history'])==4
    with open_reader(first,'git') as reader:assert [r['version'] for r in reader.versions(SID)]==[1,2,4]
    assert sha(tmp_path/'parent/source/data/gitrx.sqlite')==old

def test_identity_quarantine_does_not_promote(job,tmp_path):
    result=job.cycle(inbox(tmp_path,xml_sid='b0000000-0000-0000-0000-000000000000'))
    assert result['state']=='quarantined' and not (job.root/'current.json').exists()

def test_interrupted_promotion_resumes_frozen_source(job,tmp_path,monkeypatch):
    import gitrx_backend.jobs as module
    original=module.atomic
    def interrupt(path,value):
        if Path(path)==job.root/'current.json':raise RuntimeError('simulated interruption')
        return original(path,value)
    path=inbox(tmp_path);monkeypatch.setattr(module,'atomic',interrupt)
    with pytest.raises(RuntimeError,match='simulated'):job.cycle(path)
    source=next((job.root/'jobs').glob('*/source/data/gitrx.sqlite'));before=sha(source)
    monkeypatch.setattr(module,'atomic',original);result=job.cycle(path)
    assert result['promoted'] and sha(source)==before

def test_refresh_reconciles_two_passes_and_refuses_shifted_pagination(job,tmp_path,monkeypatch):
    import gitrx_backend.jobs as module
    rows=[{'spl_version':4,'published_date':'Nov 06, 2026'},{'spl_version':2,'published_date':'Jan 01, 2026'}]
    history=tmp_path/'history.json';history.write_text(json.dumps({'metadata':{'total_elements':2,'total_pages':1,'current_page':1},'data':{'spl':{'setid':SID},'history':rows}}))
    zip_inbox=inbox(tmp_path);zip_path=Path(json.loads(zip_inbox.read_bytes())['entries'][0]['zip_path'])
    class HTTP:
        def __init__(self,*args):pass
        def get(self,url,reuse=False):return history if 'history.json' in url else zip_path
        def close(self):pass
    monkeypatch.setattr(module,'HTTP',HTTP)
    result=job.refresh([SID]);assert not result['incidents'] and result['new_candidates']==1
    result=job.cycle(result['inbox']);assert result['new_versions']==1
    data=json.loads(history.read_bytes());data['metadata']['current_page']=2;history.write_text(json.dumps(data))
    result=job.refresh([SID]);assert result['incidents']
    with pytest.raises(ValueError,match='incidents'):job.cycle(result['inbox'])

def test_competing_bytes_for_same_identity_are_retained_not_selected(job,tmp_path):
    path=inbox(tmp_path);data=json.loads(path.read_bytes());first=Path(data['entries'][0]['zip_path'])
    with zipfile.ZipFile(first) as z:xml=z.read('label.xml').replace(b'new wording',b'competing wording')
    other=tmp_path/'other.zip'
    with zipfile.ZipFile(other,'w') as z:z.writestr('label.xml',xml)
    data['entries'][1]['zip_path']=str(other);path.write_text(json.dumps(data))
    result=job.cycle(path);assert result['state']=='quarantined'
    assert len(result['incidents'][0]['hashes'])==2
    assert not (job.root/'current.json').exists()

def test_http_budget_persists_and_successful_zip_is_reused(tmp_path,monkeypatch):
    import gitrx_backend.jobs as module
    class Response(io.BytesIO):
        code=200
        headers={'Content-Type':'application/zip'}
    class Opener:
        def open(self,*args,**kwargs):return Response(b'retained bytes')
    monkeypatch.setattr(module.urllib.request,'build_opener',lambda *args:Opener())
    http=module.HTTP(tmp_path/'network',cap=1)
    first=http.get('https://dailymed.nlm.nih.gov/file.zip')
    assert first.read_bytes()==b'retained bytes'
    http.close()
    http=module.HTTP(tmp_path/'network',cap=1)
    assert http.get('https://dailymed.nlm.nih.gov/file.zip',reuse=True)==first
    with pytest.raises(RuntimeError,match='cap'):http.get('https://dailymed.nlm.nih.gov/another.zip')
    assert http.c.execute('SELECT headers_json FROM requests').fetchone()[0]
    http.close()

def test_changed_http_cycle_rollback_and_old_citations(job,tmp_path,monkeypatch):
    """Only HTTP transport is mocked; real ledger/ZIP/parser/index/Git/promotion."""
    import gitrx_backend.jobs as module
    old_config=tmp_path/'parent/backend.json'
    with open_reader(old_config,'git') as r:old=Query(r).show(SID,2)
    path=inbox(tmp_path);body=Path(json.loads(path.read_bytes())['entries'][0]['zip_path']).read_bytes()
    history=json.dumps({'metadata':{'total_elements':3,'total_pages':1,'current_page':1},'data':{'spl':{'setid':SID},'history':[{'spl_version':v,'published_date':day} for v,day in [(1,'Jan 01, 2026'),(2,'Jan 01, 2026'),(4,'Nov 06, 2026')]]}}).encode()
    class Response(io.BytesIO):
        code=200;headers={'Content-Type':'application/octet-stream'}
    class Opener:
        def open(self,req,**kwargs):return Response(history if 'history.json' in req.full_url else body)
    monkeypatch.setattr(module.urllib.request,'build_opener',lambda *a:Opener())
    monkeypatch.setattr(module.time,'sleep',lambda _:None)
    refreshed=job.refresh([SID]);assert not refreshed['incidents']
    result=job.cycle(refreshed['inbox']);assert result['state']=='promoted'
    new_cfg=result['config']
    with open_reader(new_cfg,'git') as r:
        new=Query(r).show(SID,4);assert 'new wording 4' in new['sections'][0]['text']
    ledger=module.HTTP(job.root/'network');assert ledger.c.execute('select count(*) from requests').fetchone()[0]==3;ledger.close()
    original=json.loads(old_config.read_bytes())['snapshot']
    rolled=job.activate(old_config,result['snapshot']);assert rolled['to']==original
    replay=job.cycle(refreshed['inbox'])
    assert replay['state']=='retained' and not replay['promoted'] and not replay['currently_selected']
    with open_reader(job.parent(),'git') as r:assert Query(r).show(SID,2)==old
    with open_reader(new_cfg,'git') as r:assert Query(r).show(SID,4)==new
    with pytest.raises(ValueError,match='changed'):job.activate(new_cfg,result['snapshot'])
    job.activate(new_cfg,original)
    with open_reader(job.parent(),'git') as r:assert Query(r).show(SID,4)==new

    # An incomplete/tampered target must leave the selected pointer intact.
    complete=Path(new_cfg).parent/'COMPLETE.json';saved=complete.read_bytes()
    complete.write_text(json.dumps({'snapshot':'invalid'}))
    pointer=(job.root/'current.json').read_bytes()
    with pytest.raises(ValueError,match='incomplete'):job.activate(new_cfg,result['snapshot'])
    assert (job.root/'current.json').read_bytes()==pointer
    complete.write_bytes(saved)
