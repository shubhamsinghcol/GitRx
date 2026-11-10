import hashlib,json,sqlite3
from pathlib import Path
import pytest
from gitrx_backend.backfill_recovery import observation_rows,validate_candidate,BodyRepair


def fixture(tmp_path):
 root=tmp_path/'work';db=root/'batches/00001/data/gitrx.sqlite';db.parent.mkdir(parents=True)
 bodies=root/'network/bodies';bodies.mkdir(parents=True)
 c=sqlite3.connect(db)
 c.executescript('CREATE TABLE artifacts(sha256,path);CREATE TABLE backfill_http(retrieval_id,observation_json);CREATE TABLE retrievals(id,artifact_sha256,url,status,retrieved_at,headers_json);')
 rows=[]
 for ident in (1,2):
  body=('body'+str(ident)).encode();digest=hashlib.sha256(body).hexdigest();p=bodies/digest;p.write_bytes(body)
  alias=db.parent.parent/'artifacts'/digest;alias.parent.mkdir(exist_ok=True);alias.hardlink_to(p)
  url='https://dailymed.nlm.nih.gov/fixture'+str(ident)
  obs={'id':ident,'namespace':'backfill:fixture','url':url,'final_url':url,'retrieved_at':'2026-11-08T00:00:00+00:00','status':200,'sha256':digest,'headers':{},'bytes':len(body),'path':str(p),'error':None}
  c.execute('INSERT INTO artifacts VALUES(?,?)',(digest,str(alias.relative_to(db.parent.parent))))
  c.execute('INSERT INTO backfill_http VALUES(?,?)',(ident,json.dumps(obs)))
  c.execute('INSERT INTO retrievals VALUES(?,?,?,?,?,?)',(ident,digest,url,200,obs['retrieved_at'],'[]'));rows.append(obs)
 c.commit();c.close()
 candidate=tmp_path/'candidate.sqlite';c=sqlite3.connect(candidate)
 c.executescript('CREATE TABLE requests(id INTEGER PRIMARY KEY,url,started REAL,status,sha256,error,headers_json,bytes); CREATE TABLE transport_state(key PRIMARY KEY,value);')
 from datetime import datetime
 o=rows[0];c.execute('INSERT INTO requests VALUES(?,?,?,?,?,?,?,?)',(1,o['url'],datetime.fromisoformat(o['retrieved_at']).timestamp(),200,o['sha256'],None,'{}',o['bytes']));c.commit();c.close()
 return root,candidate,rows


def test_missing_ledger_rows_reconstructed_from_matching_source_observations(tmp_path):
 root,candidate,rows=fixture(tmp_path);obs,sources=observation_rows(root)
 result=validate_candidate(candidate,root,obs)
 assert result['requests']==2 and result['reconstructed_ids']==[2]
 c=sqlite3.connect(candidate)
 assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
 assert c.execute('SELECT count(*) FROM ledger_reconstruction').fetchone()[0]==1
 c.close()


def test_raw_mismatch_is_not_silently_accepted(tmp_path):
 root,candidate,rows=fixture(tmp_path);obs,_=observation_rows(root)
 Path(rows[1]['path']).write_bytes(b'changed')
 with pytest.raises(RuntimeError,match='hash/size mismatch'):validate_candidate(candidate,root,obs)


def test_conflicting_recovered_metadata_is_not_promoted(tmp_path):
 root,candidate,rows=fixture(tmp_path);obs,_=observation_rows(root)
 c=sqlite3.connect(candidate);c.execute("update requests set url='https://dailymed.nlm.nih.gov/wrong'");c.commit();c.close()
 with pytest.raises(RuntimeError,match='conflicts'):validate_candidate(candidate,root,obs)
