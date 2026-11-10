"""Shared captured-history persistence never invents targets or publication dates."""
import hashlib
import json
from pathlib import Path

import pytest

from gitrx.current.archive import Fetcher
from gitrx.current.db import connect
from gitrx_services.release1_history import collect, persist_inventory, Stop

SID='a0000000-0000-0000-0000-000000000000'


def captured(tmp_path, rows):
    c=connect(tmp_path/'data/gitrx.sqlite')
    c.execute('INSERT INTO families VALUES(?,?,?)',(SID,'Fixture',json.dumps({'scope_marker':'retained'})))
    c.commit();fetcher=Fetcher(c,tmp_path,None);paths=[]
    def fetch(url,number,started):
        value={'metadata':{'current_page':1,'total_pages':1,'total_elements':len(rows)},
               'data':{'spl':{'setid':SID},'history':rows}}
        path=tmp_path/f'pass-{number}.json';path.write_text(json.dumps(value));paths.append(path)
        return {'url':url,'final_url':url,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'status':200,'headers':[],'retrieved_at':'2026-11-07T00:00:00Z'}
    def record(response):
        return fetcher.record(response,Path(response['path']).read_bytes(),'history',None,
                              'synthetic-pass:'+str(len(paths)))
    result=collect(c,SID,4,fetch,record,sleep=lambda _:None)
    return c,result,paths


def test_publication_observations_and_listing_only_target_are_distinct(tmp_path):
    rows=[{'spl_version':1,'published_date':'Nov 01, 2026'},
          {'spl_version':2,'published_date':'Nov 02, 2026'},
          {'spl_version':2,'published_date':'upstream unknown date'}]
    c,result,paths=captured(tmp_path,rows)
    assert persist_inventory(c,SID,result,{'latest_versions_seen':[4]},'f'*64,[{'sha256':'e'*64}]) is None
    assert [r[0] for r in c.execute('SELECT version FROM versions ORDER BY version')]==[1,2,4]
    pub=[tuple(r) for r in c.execute('SELECT version,publication_raw,publication_date FROM tranche_publication_observations ORDER BY version,ordinal')]
    assert pub==[(1,'Nov 01, 2026','2026-11-01'),(2,'Nov 02, 2026','2026-11-02'),(2,'upstream unknown date',None)]
    assert not c.execute('SELECT 1 FROM history_entries h JOIN versions v ON v.id=h.version_id WHERE v.version=4').fetchone()
    listing=json.loads(c.execute('SELECT provenance_json FROM release1_listing_only_targets WHERE version=4').fetchone()[0])
    assert listing['scope_sha256']=='f'*64 and listing['publication_date']=='unknown; no history observation'
    assert listing['history_pass_id']==result['pass_id']
    source=c.execute('SELECT p.retrieval_id FROM release1_history_pages p WHERE pass_id=?',(result['pass_id'],)).fetchone()[0]
    assert {r[0] for r in c.execute('SELECT retrieval_id FROM history_entries')}=={source}
    before=c.execute('SELECT COUNT(*) FROM tranche_publication_observations').fetchone()[0]
    persist_inventory(c,SID,result,{'latest_versions_seen':[4]},'f'*64,[{'sha256':'e'*64}])
    assert c.execute('SELECT COUNT(*) FROM tranche_publication_observations').fetchone()[0]==before
    assert json.loads(c.execute('SELECT metadata_json FROM families').fetchone()[0])['scope_marker']=='retained'
    c.close()


def test_nonpositive_source_versions_remain_evidence_not_targets(tmp_path):
    c,result,paths=captured(tmp_path,[{'spl_version':-8,'published_date':'Dec 18, 2017'},
                                     {'spl_version':0,'published_date':None},
                                     {'spl_version':2,'published_date':'Nov 01, 2026'}])
    persist_inventory(c,SID,result,{'latest_versions_seen':[-1,3]},'f'*64,[])
    assert [r[0] for r in c.execute('SELECT version FROM versions ORDER BY version')]==[2,3]
    assert [r[0] for r in c.execute('SELECT version FROM release1_current_history')]==[2]
    md=json.loads(c.execute('SELECT metadata_json FROM families').fetchone()[0])['history_capture']
    assert [r['version'] for r in md['nonpositive_history_versions']]==[-8,0]
    assert md['nonpositive_listing_versions']==[-1]
    assert '-8' in paths[-1].read_text()
    c.close()


def test_unknown_history_does_not_become_zero_version_complete_family(tmp_path):
    c,result,paths=captured(tmp_path,[])
    assert result['state']=='fetch_failed'
    assert persist_inventory(c,SID,result,{'latest_versions_seen':[4]},'f'*64,[])=='fetch_failed'
    assert c.execute('SELECT COUNT(*) FROM versions').fetchone()[0]==0
    md=json.loads(c.execute('SELECT metadata_json FROM families').fetchone()[0])['history_capture']
    assert md['state']=='fetch_failed' and md['accepted_pass'] is None
    assert len(md['passes'])==3
    c.close()


def test_publication_inversions_use_publication_dates_and_explicit_run(tmp_path):
    c,result,paths=captured(tmp_path,[{'spl_version':1,'published_date':'Nov 02, 2026'},
                                     {'spl_version':2,'published_date':'Nov 01, 2026'}])
    persist_inventory(c,SID,result,{'latest_versions_seen':[2]},'f'*64,[],run_id=7)
    assert c.execute('SELECT run_id FROM date_anomalies').fetchone()[0]==7
    assert all(r[0]==1 for r in c.execute('SELECT date_anomaly FROM versions'))
    assert {r[0] for r in c.execute('SELECT published_date FROM history_entries')}=={'2026-11-01','2026-11-02'}
    c.close()


def test_changed_saved_history_refuses_inventory_admission(tmp_path):
    c,result,paths=captured(tmp_path,[{'spl_version':1,'published_date':'Nov 01, 2026'}])
    paths[-1].write_text('changed source')
    with pytest.raises(Stop,match='HASH_CHANGED'):
        persist_inventory(c,SID,result,{'latest_versions_seen':[1]},'f'*64,[])
    assert c.execute('SELECT COUNT(*) FROM versions').fetchone()[0]==0
    assert 'history_capture' not in json.loads(c.execute('SELECT metadata_json FROM families').fetchone()[0])
    c.close()

def test_inventory_projection_must_match_saved_history_rows(tmp_path):
    c,result,paths=captured(tmp_path,[{'spl_version':1,'published_date':'Nov 01, 2026'}])
    changed=[[2,['Nov 01, 2026']]]
    c.execute('UPDATE release1_history_passes SET canonical_json=? WHERE id=?',
              (json.dumps(changed),result['pass_id']));c.commit()
    result['canonical']=changed
    with pytest.raises(Stop,match='SOURCE_INVENTORY_MISMATCH'):
        persist_inventory(c,SID,result,{'latest_versions_seen':[1]},'f'*64,[])
    assert c.execute('SELECT COUNT(*) FROM versions').fetchone()[0]==0
    c.close()
