import hashlib
import json
from pathlib import Path
import sqlite3
import zipfile

import pytest

from gitrx.current.archive import Fetcher,validate_label
from gitrx.current.db import connect,dumps
from gitrx.current.parser import persist_document
from gitrx_services.release1_parse_pool import persist_prepared
from gitrx_backend.backfill_processing import ProcessingPool,ProcessingFailure,settings,GIB
from gitrx_backend.backfill import version_pin
from gitrx_backend.capacity import derived_document

SID='11000000-0000-0000-0000-000000000001'


def sources(tmp_path,count=2,no_sections=False):
    root=tmp_path/'source';c=connect(root/'data/gitrx.sqlite')
    c.execute('CREATE TABLE backfill_versions(version_id INTEGER PRIMARY KEY,state TEXT,pin TEXT,details_json TEXT)')
    c.execute('INSERT INTO families VALUES(?,?,?)',(SID,'Fixture','{}'));c.commit()
    versions=[]
    for n in range(1,count+1):
        c.execute('INSERT INTO versions(setid,version) VALUES(?,?)',(SID,n));c.commit()
        vid=c.execute('SELECT id FROM versions WHERE version=?',(n,)).fetchone()[0]
        path=tmp_path/f'{n}.zip'
        xml=f'<document xmlns="urn:hl7-org:v3"><setId root="{SID}"/><versionNumber value="{n}"/><code code="34391-3" codeSystem="2.16.840.1.113883.6.1"/><component><structuredBody><component><section><code code="34066-1" codeSystem="2.16.840.1.113883.6.1"/><title>WARNING</title><text><paragraph>Fixture {n} exact text.</paragraph><table><tbody><tr><td>A</td><td>{n}</td></tr></tbody></table></text></section></component></structuredBody></component></document>'
        if no_sections:
            xml=xml[:xml.index('<component>')]+'</document>'
        with zipfile.ZipFile(path,'w') as z:z.writestr('label.xml',xml)
        rid=Fetcher(c,root,None).record({'url':'https://dailymed.nlm.nih.gov/fixture/'+str(n),'retrieved_at':'2026-11-08T00:00:00Z','status':200,'headers':[]},path.read_bytes(),'label',vid)
        result=validate_label(c,root,rid,SID,n)
        digest=hashlib.sha256(path.read_bytes()).hexdigest()
        c.execute("UPDATE versions SET state='downloaded',selected_sha256=?,xml_member=? WHERE id=?",(digest,result['xml_member'],vid));c.commit()
        versions.append(dict(c.execute('SELECT * FROM versions WHERE id=?',(vid,)).fetchone()))
    return c,root,versions


def commit(c,root,version,registry,prepared,details):
    if isinstance(prepared,dict) and 'extraction_error' in prepared:
        from types import SimpleNamespace
        from gitrx_backend.backfill import Backfill
        c.execute('CREATE TABLE IF NOT EXISTS meta(key PRIMARY KEY,value)');c.commit()
        return Backfill.commit_parse(SimpleNamespace(c=c),c,root,version,registry,prepared,details)
    persist_prepared(c,root,version,registry,prepared)
    pin=version_pin(c,version['id'])
    c.execute('INSERT OR REPLACE INTO backfill_versions VALUES(?,?,?,?)',(version['id'],'complete',pin,dumps(details)));c.commit()
    return {'durable':True,'version_pin':pin}


def pool(tmp_path,callback=commit):
    beats=[]
    return ProcessingPool(tmp_path/'work',{'max_workers':2},callback,lambda *args:None,lambda:beats.append(1)),beats


def test_policy_caps_and_no_fake_core_assignment():
    assert settings({})['workers']==6
    assert settings({'max_workers':12})['workers']==12
    assert settings({'max_workers':12})['worker_RSS_limit_bytes']==GIB//2
    assert settings({'max_workers':12})['queue_limit']==24
    with pytest.raises(ValueError):settings({'max_workers':13})
    with pytest.raises(ValueError):settings({'worker_RSS_limit_bytes':GIB+1})
    with pytest.raises(ValueError):settings({'coordinator_allowance_bytes':2*GIB+1})
    with pytest.raises(ValueError):settings({'total_RSS_budget_bytes':8*GIB+1})
    with pytest.raises(ValueError):settings({'total_RSS_budget_bytes':GIB})


def test_no_sections_records_failed_parse_and_continues(tmp_path):
    c,root,versions=sources(tmp_path,1,no_sections=True);p,_=pool(tmp_path)
    try:
        p.submit(c,root,versions[0],{'34066-1'},{})
        p.drain(all=True)
        assert not p.pending and p.committed==1 and p.recycles==0
        assert c.execute('SELECT status FROM parse_runs').fetchone()[0]=='extraction_failed'
        assert c.execute('SELECT COUNT(*) FROM sections').fetchone()[0]==0
    finally:p.close(interrupted=True);c.close()


def test_real_parallel_extraction_matches_native_rows_and_cleans_spool(tmp_path):
    c,root,versions=sources(tmp_path);p,beats=pool(tmp_path)
    try:
        for v in versions:p.submit(c,root,v,{'34066-1'},{'request_id':v['id']})
        assert len(p.pending)==2
        p.drain_family(SID)
        assert not p.pending and p.committed==2 and beats
        assert c.execute('SELECT COUNT(*) FROM parse_runs').fetchone()[0]==2
        assert not list(p.spool.rglob('payload.pickle'))
        assert not list(p.spool.rglob('manifest.json'))
        reference=tmp_path/'reference';other=connect(reference/'data/gitrx.sqlite')
        c.backup(other)
        # Drop generated native rows in the independent copy while preserving
        # source evidence via exact CAS hardlinks, then run the ordinary adapter.
        import shutil
        shutil.copytree(root/'artifacts',reference/'artifacts',copy_function=__import__('os').link)
        other.execute('PRAGMA foreign_keys=OFF')
        for table in ['section_excerpts','narrative_blocks','narrative_tables','sections','parse_runs']:
            other.execute('DELETE FROM '+table)
        other.commit()
        for v in versions:
            persist_document(other,reference,other.execute('SELECT * FROM versions WHERE id=?',(v['id'],)).fetchone(),{'34066-1'})
            assert derived_document(c,v['id'])==derived_document(other,v['id'])
        other.close()
    finally:p.close(interrupted=True);c.close()


def test_missing_durable_ack_keeps_spool_for_resume(tmp_path):
    c,root,versions=sources(tmp_path,1)
    def noack(*args):return None
    p,_=pool(tmp_path,noack)
    try:
        p.submit(c,root,versions[0],{'34066-1'},{})
        with pytest.raises(ProcessingFailure,match='durable version pin'):p.drain(all=True)
        assert list(p.spool.rglob('payload.pickle'))
    finally:p.close(interrupted=True)
    p,_=pool(tmp_path)
    try:
        p.submit(c,root,versions[0],{'34066-1'},{})
        p.drain(all=True)
        assert not list(p.spool.rglob('payload.pickle'))
    finally:p.close(interrupted=True);c.close()


def test_duplicate_submission_and_existing_parse_are_idempotent(tmp_path):
    c,root,versions=sources(tmp_path,1);p,_=pool(tmp_path)
    try:
        assert p.submit(c,root,versions[0],{'34066-1'},{})
        assert not p.submit(c,root,versions[0],{'34066-1'},{})
        p.drain(all=True)
        assert p.submit(c,root,versions[0],{'34066-1'},{})
        assert not p.pending and c.execute('SELECT COUNT(*) FROM parse_runs').fetchone()[0]==1
    finally:p.close(interrupted=True);c.close()


def test_coordinator_rss_breach_fails_closed_without_local_parse(tmp_path,monkeypatch):
    c,root,versions=sources(tmp_path,1);p,_=pool(tmp_path)
    monkeypatch.setattr(p,'_rss_snapshot',lambda:{'total_RSS_bytes':3*GIB,'coordinator_RSS_bytes':3*GIB,'worker_RSS_bytes_by_pid':{}})
    try:
        with pytest.raises(ProcessingFailure,match='coordinator_RSS_budget'):p.submit(c,root,versions[0],{'34066-1'},{})
        assert c.execute('SELECT COUNT(*) FROM parse_runs').fetchone()[0]==0
    finally:p.close(interrupted=True);c.close()

def test_aggregate_memory_recycle_preserves_pending_jobs(tmp_path,monkeypatch):
    p,_=pool(tmp_path)
    from concurrent.futures import Future
    class Executor:
        _processes={}
        def submit(self,*args):return Future()
        def shutdown(self,**kwargs):pass
    p.executor=Executor()
    job={'key':('fixture',1),'version':{'selected_sha256':'a'*64},'estimate':10,'args':('fixture',),'tries':0}
    p.pending[Future()]=job
    monkeypatch.setattr(p,'_rss_snapshot',lambda:{'total_RSS_bytes':9*GIB,'coordinator_RSS_bytes':GIB,'worker_RSS_bytes_by_pid':{}})
    monkeypatch.setattr(p,'_start',lambda:setattr(p,'executor',Executor()))
    try:
        p._check_memory()
        assert p.recycles==1 and p.options['workers']==1 and len(p.pending)==1
        assert next(iter(p.pending.values()))['tries']==1
    finally:p.close(interrupted=True)


def test_corrupted_worker_spool_is_not_persisted_or_deleted(tmp_path):
    import time
    c,root,versions=sources(tmp_path,1);p,_=pool(tmp_path)
    try:
        p.submit(c,root,versions[0],{'34066-1'},{})
        until=time.monotonic()+15
        while not all(f.done() for f in p.pending):
            assert time.monotonic()<until
            time.sleep(.01)
        payload=next(p.spool.rglob('payload.pickle'));payload.write_bytes(b'corrupt')
        with pytest.raises(ProcessingFailure,match='integrity check failed'):p.drain(all=True)
        assert payload.exists() and c.execute('SELECT COUNT(*) FROM parse_runs').fetchone()[0]==0
    finally:p.close(interrupted=True);c.close()

def test_explicit_internal_spool_is_cleaned_only_after_durable_parse(tmp_path):
    c,root,versions=sources(tmp_path,1)
    internal=tmp_path/'internal-staging/processing-spool'
    p=ProcessingPool(tmp_path/'external-work',{'max_workers':1,'processing_spool_root':str(internal)},commit,lambda *args:None,lambda:None)
    try:
        p.submit(c,root,versions[0],{'34066-1'},{})
        p.drain(all=True)
        assert p.committed==1 and not list(internal.rglob('payload.pickle'))
        assert not (p.root/'processing-spool').exists()
    finally:p.close(interrupted=True);c.close()
    with pytest.raises(ProcessingFailure,match='dedicated absolute'):ProcessingPool(tmp_path/'work',{'processing_spool_root':'relative'},commit,lambda:None,lambda:None)


def test_exhausted_worker_resource_failure_defers_version(tmp_path):
    from concurrent.futures import Future
    from concurrent.futures.process import BrokenProcessPool
    c,root,versions=sources(tmp_path,1);p,_=pool(tmp_path)
    v=versions[0]
    future=Future();future.set_exception(BrokenProcessPool('worker exited'))
    p.pending[future]={'key':(str(root),v['id']),'c':c,'root':root,'version':v,'registry':{'34066-1'},
        'details':{},'estimate':1,'args':(),'tries':3}
    try:
        p.drain(all=True)
        assert not p.pending and p.committed==1
        assert c.execute('SELECT state FROM backfill_versions').fetchone()[0]=='parse_failed'
        assert 'WorkerResourceFailure' in c.execute('SELECT diagnostics_json FROM parse_runs').fetchone()[0]
    finally:p.close(interrupted=True);c.close()


def test_repeated_failed_versions_trip_persistent_systemic_breaker(tmp_path):
    from types import SimpleNamespace
    from gitrx_backend.backfill import Backfill
    c,root,versions=sources(tmp_path,10)
    c.execute('CREATE TABLE meta(key PRIMARY KEY,value)');c.commit()
    backend=SimpleNamespace(c=c)
    failure={'extraction_error':{'type':'ValueError','message':'fixture source failure'}}
    for v in versions[:9]:Backfill.commit_parse(backend,c,root,v,{'34066-1'},failure,{})
    with pytest.raises(RuntimeError,match='Systemic parser failure'):
        Backfill.commit_parse(backend,c,root,versions[9],{'34066-1'},failure,{})
    assert c.execute("SELECT value FROM meta WHERE key='consecutive_parse_failures'").fetchone()[0]=='10'
    assert c.execute('SELECT COUNT(*) FROM backfill_versions').fetchone()[0]==10
    c.close()


def test_hung_version_is_deferred_after_bounded_deadline_retries(tmp_path):
    from concurrent.futures import Future
    import time
    c,root,versions=sources(tmp_path,1);p,_=pool(tmp_path);v=versions[0]
    p.pending[Future()]={'key':(str(root),v['id']),'c':c,'root':root,'version':v,'registry':{'34066-1'},
        'details':{},'estimate':1,'args':(),'tries':3,'submitted':time.monotonic()-1000}
    try:
        p.drain(all=True)
        assert not p.pending and p.committed==1
        assert 'worker_task_deadline' in c.execute('SELECT diagnostics_json FROM parse_runs').fetchone()[0]
    finally:p.close(interrupted=True);c.close()


def test_twelve_worker_configuration_extracts_native_versions(tmp_path):
    c,root,versions=sources(tmp_path,12)
    p=ProcessingPool(tmp_path/'work',{'max_workers':12,'worker_RSS_limit_bytes':GIB//2,'queue_limit':24},commit,lambda *a:None,lambda:None)
    try:
        for v in versions:p.submit(c,root,v,{'34066-1'},{})
        p.drain(all=True)
        assert p.committed==12 and p.recycles==0
        assert p.options['workers']==12 and p.options['total_RSS_budget_bytes']==8*GIB
        assert c.execute("SELECT COUNT(*) FROM parse_runs WHERE status='complete'").fetchone()[0]==12
    finally:p.close(interrupted=True);c.close()


def test_memory_checks_reuse_only_fresh_samples_and_watchdog_bypasses_cache(tmp_path,monkeypatch):
    p,_=pool(tmp_path);calls=[];clock=[100.0];over=[False]
    monkeypatch.setattr('gitrx_backend.backfill_processing.time.monotonic',lambda:clock[0])
    def snapshot():
        calls.append(1)
        return {'total_RSS_bytes':3*GIB if over[0] else GIB,
                'coordinator_RSS_bytes':3*GIB if over[0] else GIB,'worker_RSS_bytes_by_pid':{}}
    monkeypatch.setattr(p,'_rss_snapshot',snapshot)
    try:
        p._check_memory();p._check_memory();assert len(calls)==1
        # The watchdog always takes a fresh reading even inside the cache window.
        assert p._observe_memory() is None and len(calls)==2
        clock[0]+=.251;over[0]=True
        with pytest.raises(ProcessingFailure,match='coordinator_RSS_budget'):p._check_memory()
        assert len(calls)==3
    finally:p.close(interrupted=True)
