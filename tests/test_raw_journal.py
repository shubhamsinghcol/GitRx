import hashlib
from pathlib import Path
import pytest
from gitrx_backend.raw_journal import RawJournal,JournalCapacityError


def body(tmp_path,name,data):
    p=tmp_path/name;p.write_bytes(data)
    return p,hashlib.sha256(data).hexdigest()


def test_exact_binary_body_is_durable_and_fifo_is_bounded(tmp_path):
    journal=RawJournal(tmp_path/'journal',max_bytes=8)
    a,ha=body(tmp_path,'external-a',b'aaaa');b,hb=body(tmp_path,'external-b',b'bbbb');c,hc=body(tmp_path,'external-c',b'cccc')
    journal.store(a,ha);journal.store(b,hb)
    journal.store(c,hc)
    assert journal.lookup(ha) is None
    assert journal.lookup(hb).read_bytes()==b'bbbb' and journal.lookup(hc).read_bytes()==b'cccc'
    assert a.read_bytes()==b'aaaa'
    assert journal.c.execute('select sum(bytes) from objects').fetchone()[0]==8
    journal.close();journal=RawJournal(tmp_path/'journal',max_bytes=8)
    assert journal.lookup(hb).read_bytes()==b'bbbb';journal.close()


def test_missing_external_original_blocks_eviction_of_its_only_copy(tmp_path):
    journal=RawJournal(tmp_path/'journal',max_bytes=4)
    a,ha=body(tmp_path,'ssd-a',b'aaaa');b,hb=body(tmp_path,'ssd-b',b'bbbb')
    journal.store(a,ha,external_path=a);a.unlink()
    with pytest.raises(JournalCapacityError,match='retained'):journal.store(b,hb)
    assert journal.lookup(ha).read_bytes()==b'aaaa'
    assert journal.lookup(hb) is None
    journal.close()


def test_corrupt_body_is_never_served_or_silently_rebuilt(tmp_path):
    journal=RawJournal(tmp_path/'journal')
    a,ha=body(tmp_path,'ssd-a',b'original\x00\xff')
    local=journal.store(a,ha);local.write_bytes(b'changed')
    with pytest.raises(RuntimeError,match='Corrupt raw journal'):journal.lookup(ha)
    journal.close()
    with pytest.raises(RuntimeError,match='Corrupt raw journal'):RawJournal(tmp_path/'journal')
    assert local.read_bytes()==b'changed' and a.read_bytes()==b'original\x00\xff'


def test_corrupt_index_rebuilds_from_verified_sha_objects_and_preserves_incident(tmp_path):
    root=tmp_path/'journal';journal=RawJournal(root)
    a,ha=body(tmp_path,'ssd-a',b'response-prefix\x00\xff');journal.store(a,ha);journal.close()
    (root/'index.sqlite').write_bytes(b'not a database')
    journal=RawJournal(root)
    assert journal.lookup(ha).read_bytes()==a.read_bytes()
    assert list(root.glob('index.sqlite.incident-*'))
    assert journal.c.execute('select path from originals where digest=?',(ha,)).fetchone()[0]==str(a)
    journal.close()


def test_interrupted_eviction_recovers_missing_local_without_stale_index_claim(tmp_path):
    root=tmp_path/'journal';journal=RawJournal(root,max_bytes=4)
    a,ha=body(tmp_path,'ssd-a',b'aaaa');journal.store(a,ha)
    with journal.c:journal.c.execute('insert into evictions values(?)',(ha,))
    journal.lookup(ha).unlink()  # Power loss after safe unlink, before index commit.
    journal.close();journal=RawJournal(root,max_bytes=4)
    assert journal.lookup(ha) is None and a.read_bytes()==b'aaaa'
    b,hb=body(tmp_path,'ssd-b',b'bbbb');journal.store(b,hb);journal.close()


def test_rebuild_discovers_completed_copy_before_index_commit(tmp_path):
    root=tmp_path/'journal';journal=RawJournal(root,max_bytes=16)
    a,ha=body(tmp_path,'ssd-a',b'aaaa');journal.store(a,ha)
    with journal.c:
        journal.c.execute('delete from originals where digest=?',(ha,));journal.c.execute('delete from objects where digest=?',(ha,))
    journal.close();journal=RawJournal(root,max_bytes=16)
    assert journal.lookup(ha).read_bytes()==b'aaaa'
    assert journal.c.execute('select bytes from objects where digest=?',(ha,)).fetchone()[0]==4
    journal.close()
