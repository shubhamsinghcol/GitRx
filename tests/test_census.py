import json
from pathlib import Path
import pytest
from gitrx_backend.census import Census, validate_page

SID='a0000000-0000-0000-0000-000000000000'
def page(n=1, sid=SID):
    return {'metadata':{'current_page':n,'total_elements':2,'total_pages':2},
            'data':[{'setid':sid,'spl_version':1,'title':'fixture'}]}

def test_pagination_and_bad_identity():
    assert validate_page(page(),1)==(2,2)
    with pytest.raises(ValueError):validate_page(page(),2)
    with pytest.raises(ValueError):validate_page(page(),1,(3,2))
    assert validate_page(page(sid='invalid'),1)==(2,2)

def test_discovery_retains_nonpositive_source_versions():
    value=page()
    value['data'][0]['spl_version']=-8
    assert validate_page(value,1)==(2,2)
    assert value['data'][0]['spl_version']==-8

def test_resume_and_hash_verification(tmp_path, monkeypatch):
    monkeypatch.setattr(Census,'check_volume',lambda self:None)
    config=tmp_path/'config.json'
    config.write_text(json.dumps({'format':'gitrx-census-1','workspace':str(tmp_path/'work'),
                                 'volume':str(tmp_path),'request_cap':10}))
    calls=[]
    def get(self,url,reuse=False):
        n=2 if 'page=2' in url else 1
        calls.append(n)
        p=tmp_path/f'page{n}.json'
        p.write_text(json.dumps(page(n, SID if n==1 else 'b0000000-0000-0000-0000-000000000000')))
        return p
    from gitrx_backend.jobs import HTTP
    monkeypatch.setattr(HTTP,'get',get)
    c=Census(config)
    assert c.run(1)['families_seen']==1
    c.close()
    c=Census(config)
    assert c.run()['complete'] and calls==[1,2]
    c.close()
    (tmp_path/'page1.json').write_text('corruption')
    c=Census(config)
    with pytest.raises(RuntimeError,match='hash changed'):c.run()
    c.close()

def test_duplicate_listing_does_not_complete(tmp_path,monkeypatch):
    monkeypatch.setattr(Census,'check_volume',lambda self:None)
    config=tmp_path/'config.json'
    config.write_text(json.dumps({'format':'gitrx-census-1','workspace':str(tmp_path/'work'),
                                 'volume':str(tmp_path),'request_cap':10}))
    def get(self,url,reuse=False):
        n=2 if 'page=2' in url else 1
        p=tmp_path/f'{n}.json';p.write_text(json.dumps(page(n)));return p
    from gitrx_backend.jobs import HTTP
    monkeypatch.setattr(HTTP,'get',get)
    c=Census(config)
    with pytest.raises(Exception):c.run()
    assert not (c.root/'manifest.json').exists()
    c.close()

def test_invalid_source_setid_is_retained_separately(tmp_path,monkeypatch):
    monkeypatch.setattr(Census,'check_volume',lambda self:None)
    config=tmp_path/'config.json'
    config.write_text(json.dumps({'format':'gitrx-census-1','workspace':str(tmp_path/'work'),
                                 'volume':str(tmp_path),'request_cap':10}))
    def get(self,url,reuse=False):
        n=2 if 'page=2' in url else 1
        p=tmp_path/f'{n}.json';p.write_text(json.dumps(page(n,SID if n==1 else 'invalid-source-id')));return p
    from gitrx_backend.jobs import HTTP
    monkeypatch.setattr(HTTP,'get',get)
    c=Census(config)
    result=c.run()
    assert result['complete'] and result['families_seen']==1
    assert result['listing_rows_seen']==2 and result['invalid_setid_rows']==1
    assert 'invalid-source-id' in c.c.execute('select row_json from listing_anomalies').fetchone()[0]
    c.close()
