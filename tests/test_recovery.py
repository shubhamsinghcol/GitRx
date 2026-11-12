from pathlib import Path
import json,sqlite3
import pytest
from gitrx_backend.recovery import create,restore,audit,sha

def test_backup_restore_dedup_pin_and_corruption(tmp_path,monkeypatch):
    import gitrx_backend.recovery as m
    monkeypatch.setattr(m,'FLOOR',0)
    root=tmp_path/'source';root.mkdir();(root/'a').write_bytes(b'original');(root/'b').write_bytes(b'original')
    (root/'alias').symlink_to('a')
    backup=tmp_path/'backup';result=create(root,backup,['a','b','alias'])
    assert audit(backup,result['manifest_sha256'])['unique_objects_verified']==1
    target=tmp_path/'restored';restore(backup,result['manifest_sha256'],target)
    assert (target/'alias').read_bytes()==b'original'
    (target/'a').write_bytes(b'changed')
    assert (root/'a').read_bytes()==b'original'
    assert audit(backup,result['manifest_sha256'])['files']==2
    with pytest.raises(ValueError):restore(backup,result['manifest_sha256'],target)
    with pytest.raises(ValueError):audit(backup,'0'*64)
    object_path=backup/'objects'/sha(root/'a')[:2]/sha(root/'a');object_path.write_bytes(b'broken')
    with pytest.raises(ValueError,match='hash'):restore(backup,result['manifest_sha256'],tmp_path/'badrestore')
    assert not (tmp_path/'badrestore').exists()

def test_sqlite_online_backup_and_path_refusal(tmp_path,monkeypatch):
    import gitrx_backend.recovery as m
    monkeypatch.setattr(m,'FLOOR',0)
    root=tmp_path/'source';root.mkdir()
    c=sqlite3.connect(root/'live.sqlite');c.execute('PRAGMA journal_mode=WAL');c.execute('CREATE TABLE x(n)');c.execute('INSERT INTO x VALUES(3)');c.commit()
    with pytest.raises(ValueError,match='journal'):create(root,tmp_path/'refused',['live.sqlite'])
    backup=tmp_path/'backup';pin=create(root,backup,['live.sqlite'],mutable_sqlite=['live.sqlite'])['manifest_sha256']
    restore(backup,pin,tmp_path/'restored')
    with sqlite3.connect(tmp_path/'restored/live.sqlite') as r:assert r.execute('SELECT n FROM x').fetchone()==(3,)
    c.close()
    with pytest.raises(ValueError):create(root,tmp_path/'bad',['../escape'])
    (root/'external').symlink_to(tmp_path/'backup')
    with pytest.raises(ValueError,match='escapes'):create(root,tmp_path/'bad2',['external'])
