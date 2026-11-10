import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import pytest
from gitrx_backend.hot_storage import HotStorage,semantic_digest
from gitrx_backend.jobs import sha


def setup(tmp_path):
    policy=tmp_path/'policy.json'
    policy.write_text(json.dumps({'format':'gitrx-hot-storage-1','local_root':str(tmp_path/'internal'),'max_bytes':1024**3}))
    ready=[True]
    def guard():
        if not ready[0]:raise RuntimeError('SSD unavailable')
    hot=HotStorage(policy,guard)
    external=tmp_path/'external'/'batch'/'data.sqlite';external.parent.mkdir(parents=True)
    return hot,external,ready


def create(path,value=1):
    c=sqlite3.connect(path);c.execute('CREATE TABLE t(id INTEGER PRIMARY KEY, value TEXT, payload BLOB)')
    c.execute('INSERT INTO t VALUES(?,?,?)',(value,'hello',b'\x00\xff'));c.commit();c.close()


def rows(path):
    c=sqlite3.connect(path);result=c.execute('select * from t order by id').fetchall();c.close();return result


def test_attach_keeps_original_and_uses_internal_wal_then_freezes_and_releases(tmp_path):
    hot,external,ready=setup(tmp_path);create(external);original=sha(external)
    local=hot.attach(external,'batch-1')
    assert local.is_relative_to(hot.root) and external.is_symlink() and rows(local)==[(1,'hello',b'\x00\xff')]
    state=json.loads(next(hot.entries.glob('*.json')).read_bytes())
    assert sha(state['original_files']['']['path'])==original
    c=sqlite3.connect(local);c.execute('pragma journal_mode=wal');c.execute("insert into t values(2,'new',X'01')");c.commit()
    assert Path(str(local)+'-wal').exists() and not Path(str(external)+'-wal').exists()
    c.close()
    frozen=hot.freeze(external)
    assert frozen==external and not external.is_symlink() and sha(external)==sha(local)
    assert len(rows(external))==2
    hot.release(external);assert not local.exists() and rows(external)[1][1]=='new'
    assert hot.release(external)==external
    with pytest.raises(RuntimeError,match='Frozen/released'):hot.attach(external,'batch-1')


def test_committed_wal_from_crashed_writer_is_preserved_and_backed_up(tmp_path):
    hot,external,ready=setup(tmp_path)
    code="""import sqlite3,os,sys
c=sqlite3.connect(sys.argv[1]);c.execute('pragma journal_mode=wal');c.execute('create table t(x)');c.execute('insert into t values(42)');c.commit();os._exit(0)
"""
    subprocess.check_call([sys.executable,'-c',code,str(external)])
    assert Path(str(external)+'-wal').exists()
    originals={suffix:sha(Path(str(external)+suffix)) for suffix in ('','-wal','-shm')}
    local=hot.attach(external,'network')
    c=sqlite3.connect(local);assert c.execute('select x from t').fetchall()==[(42,)];c.close()
    state=json.loads(next(hot.entries.glob('*.json')).read_bytes())
    assert all(sha(state['original_files'][suffix]['path'])==digest for suffix,digest in originals.items())
    assert not Path(str(external)+'-wal').exists()


def test_missing_ssd_never_creates_external_fallback_or_replaces_local(tmp_path):
    hot,external,ready=setup(tmp_path);create(external);local=hot.attach(external,'control')
    external.unlink();ready[0]=False;before=sha(local)
    with pytest.raises(RuntimeError,match='SSD unavailable'):hot.attach(external,'control')
    assert not external.exists() and sha(local)==before
    ready[0]=True;assert hot.attach(external,'control')==local and external.is_symlink()


def test_interrupted_alias_install_resumes_without_overwriting_canonical(tmp_path,monkeypatch):
    hot,external,ready=setup(tmp_path);create(external);alias=hot._alias
    monkeypatch.setattr(hot,'_alias',lambda *a:(_ for _ in ()).throw(RuntimeError('interrupted before alias')))
    with pytest.raises(RuntimeError,match='interrupted'):hot.attach(external,'active')
    assert not external.exists()
    local=next(hot.objects.glob('*.sqlite'));before=sha(local)
    monkeypatch.setattr(hot,'_alias',alias)
    assert hot.attach(external,'active')==local and sha(local)==before


def test_distinct_ordinary_external_database_conflicts_with_local(tmp_path):
    hot,external,ready=setup(tmp_path);create(external);local=hot.attach(external,'control')
    before=sha(local);external.unlink();create(external,2)
    other=sha(external)
    with pytest.raises(RuntimeError,match='conflicts'):hot.attach(external,'control')
    assert sha(local)==before and sha(external)==other


def test_freeze_resumes_after_atomic_alias_replacement_before_marker(tmp_path,monkeypatch):
    hot,external,ready=setup(tmp_path);create(external);local=hot.attach(external,'batch')
    save=hot._save
    def crash(record,state,phase=None):
        if phase=='frozen':raise RuntimeError('interrupted freeze marker')
        return save(record,state,phase)
    monkeypatch.setattr(hot,'_save',crash)
    with pytest.raises(RuntimeError,match='freeze marker'):hot.freeze(external)
    assert not external.is_symlink() and sha(external)==sha(local)
    monkeypatch.setattr(hot,'_save',save)
    assert hot.freeze(external)==external


def test_invalid_source_is_not_repaired_or_replaced(tmp_path):
    hot,external,ready=setup(tmp_path);external.write_bytes(b'not a sqlite database');before=external.read_bytes()
    with pytest.raises(sqlite3.DatabaseError):hot.attach(external,'network')
    assert external.read_bytes()==before and not external.is_symlink()


def test_semantic_digest_is_order_independent_but_detects_typed_content_changes():
    a=sqlite3.connect(':memory:');b=sqlite3.connect(':memory:')
    for c in (a,b):c.execute('create table t(x,y)')
    a.executemany('insert into t values(?,?)',[(1,b'one'),(2,b'two')])
    b.executemany('insert into t values(?,?)',[(2,b'two'),(1,b'one')])
    assert semantic_digest(a)==semantic_digest(b)
    b.execute("update t set y='one' where x=1")
    assert semantic_digest(a)!=semantic_digest(b)
    a.close();b.close()

def test_interruption_after_snapshot_promotion_before_state_update_is_idempotent(tmp_path,monkeypatch):
    hot,external,ready=setup(tmp_path);create(external);save=hot._save;crashed=[False]
    def crash(record,state,phase=None):
        if phase=='snapshot-ready' and not crashed[0]:
            crashed[0]=True;raise RuntimeError('interrupted snapshot state')
        return save(record,state,phase)
    monkeypatch.setattr(hot,'_save',crash)
    with pytest.raises(RuntimeError,match='snapshot state'):hot.attach(external,'active')
    local=next(hot.objects.glob('*.sqlite'));before=sha(local)
    monkeypatch.setattr(hot,'_save',save)
    assert hot.attach(external,'active')==local and sha(local)==before and external.is_symlink()


def test_interruption_before_snapshot_promotion_uses_validated_staged_file(tmp_path,monkeypatch):
    hot,external,ready=setup(tmp_path);create(external);replace=os.replace;crashed=[False]
    def crash(source,dest):
        if str(dest).endswith('.sqlite') and Path(dest).parent==hot.objects and not crashed[0]:
            crashed[0]=True;raise RuntimeError('interrupted snapshot rename')
        return replace(source,dest)
    monkeypatch.setattr(os,'replace',crash)
    with pytest.raises(RuntimeError,match='snapshot rename'):hot.attach(external,'active')
    monkeypatch.setattr(os,'replace',replace)
    local=hot.attach(external,'active')
    assert rows(local)==[(1,'hello',b'\x00\xff')] and external.is_symlink()


def test_opt_in_scan_coalescing_expires_and_explicit_allocations_scan(tmp_path,monkeypatch):
    import gitrx_backend.hot_storage as module
    hot,_,_=setup(tmp_path);hot.scan_interval=.05
    clock=[100.0];monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    calls=[];original=module.os.scandir
    def scan(path):calls.append(path);return original(path)
    monkeypatch.setattr(module.os,'scandir',scan)
    hot.check();hot.check();assert len(calls)==1
    hot.check(1);assert len(calls)==2
    clock[0]+=.051;hot.check();assert len(calls)==3
    hot.last_used=hot.limit-128*1024**2;hot.check();assert len(calls)==4


def test_usage_scan_tolerates_sqlite_sidecar_removed_during_iteration(tmp_path,monkeypatch):
    from contextlib import contextmanager
    import gitrx_backend.hot_storage as module
    hot,_,_=setup(tmp_path)
    (hot.objects/'retained.sqlite').write_bytes(b'retained database')
    original=module.os.scandir
    class RemovedSidecar:
        def is_file(self,**kwargs):return True
        def stat(self,**kwargs):raise FileNotFoundError('SQLite removed its -shm file')
    @contextmanager
    def scan(path):
        with original(path) as entries:
            yield iter([RemovedSidecar(),*entries])
    monkeypatch.setattr(module.os,'scandir',scan)
    hot.check()
    assert hot.last_used==len(b'retained database')
