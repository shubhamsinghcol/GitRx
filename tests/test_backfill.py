import io,json,sqlite3,zipfile
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
import pytest
from gitrx_backend.selftest import run as parent_fixture
from gitrx_backend.jobs import sha
from gitrx_backend.backfill import Backfill
from gitrx.current.spl_parser_v1 import CONFIG
from gitrx.current.db import dumps
import hashlib

IDS=['11000000-0000-0000-0000-000000000001','22000000-0000-0000-0000-000000000002','33000000-0000-0000-0000-000000000003']

@pytest.fixture
def fixture(tmp_path,monkeypatch):
    parent_fixture(tmp_path/'parent')
    census=tmp_path/'census';census.mkdir()
    c=sqlite3.connect(census/'census.sqlite');c.execute('CREATE TABLE families(setid PRIMARY KEY,row_json)')
    for sid in IDS:c.execute('INSERT INTO families VALUES(?,?)',(sid,json.dumps({'setid':sid,'title':'Fixture','spl_version':2})))
    c.commit();c.close()
    (census/'manifest.json').write_text(json.dumps({'families':3,'census_index_sha256':sha(census/'census.sqlite'),'pages':[]}))
    cfg=json.loads((tmp_path/'parent/backend.json').read_bytes())
    parentroot=(tmp_path/'parent'/cfg['release_root']).resolve();scope=(tmp_path/'parent'/cfg['scope_path']).resolve()
    config={'format':'gitrx-backfill-1','workspace':str(tmp_path/'work'),'volume':str(tmp_path),
            'parser_version':'spl-sections-0.1.0','parent_config':str(tmp_path/'parent/backend.json'),
            'parent_config_sha256':sha(tmp_path/'parent/backend.json'),'parent_manifest_sha256':sha(parentroot/'reports/EXPANDED-SELECTION.json'),
            'parent_scope_sha256':sha(scope),'census_workspace':str(census),'census_manifest_sha256':sha(census/'manifest.json'),
            'request_cap':100,'network_byte_cap':1024**3,'retained_byte_cap':2*1024**3,'batch_size':2,'section_codes':['34066-1'],'refresh_retained_parent':False,
            'parser_config_sha256':hashlib.sha256(dumps(CONFIG).encode()).hexdigest()}
    p=tmp_path/'config.json';p.write_text(json.dumps(config))
    calls=[];wrong=[False]
    class Response(io.BytesIO):
        code=200;headers={}
    class Opener:
        def open(self,req,**kw):
            url=req.full_url;calls.append(url)
            if '/history.json' in url:
                sid=urlsplit(url).path.split('/')[-2]
                value={'metadata':{'current_page':1,'total_elements':2,'total_pages':1},'data':{'spl':{'setid':sid},'history':[{'spl_version':1,'published_date':'Jan 01, 2020'},{'spl_version':2,'published_date':'Nov 01, 2026'}]}}
                return Response(json.dumps(value).encode())
            q=parse_qs(urlsplit(url).query);sid=q['setid'][0];v=int(q['version'][0])
            if wrong[0]:sid=IDS[0] if sid!=IDS[0] else IDS[1]
            xml=f'<document xmlns="urn:hl7-org:v3"><setId root="{sid}"/><versionNumber value="{v}"/><code code="34391-3" codeSystem="2.16.840.1.113883.6.1"/><component><structuredBody><component><section><code code="34066-1" codeSystem="2.16.840.1.113883.6.1"/><title>Warning</title><text>Fixture {v}</text></section></component></structuredBody></component></document>'
            b=io.BytesIO()
            with zipfile.ZipFile(b,'w') as z:z.writestr('label.xml',xml)
            return Response(b.getvalue())
    import gitrx_backend.jobs as jobs
    monkeypatch.setattr(jobs.urllib.request,'build_opener',lambda *a:Opener())
    monkeypatch.setattr(jobs.time,'sleep',lambda seconds:None)
    monkeypatch.setattr(Backfill,'check_volume',lambda self:None)
    return p,calls,wrong


def test_streaming_batches_preserve_dates_raw_hardlinks_and_selected_release(fixture):
    p,calls,_=fixture;job=Backfill(p);parent=sha(job.config['parent_config'])
    result=job.run()
    assert result['family_states']=={'complete':3}
    assert result['frozen_batches']==2 and len(calls)==12
    assert sha(job.config['parent_config'])==parent and not (job.root/'current.json').exists()
    for marker in (job.root/'batches').glob('*/BATCH.json'):
        data=json.loads(marker.read_bytes());db=marker.parent/'data/gitrx.sqlite'
        assert sha(db)==data['source_database_sha256']
        with sqlite3.connect(db) as c:
            assert c.execute('select count(*) from history_entries').fetchone()[0]>0
            assert c.execute("select count(*) from tranche_publication_observations where publication_date='2020-01-01'").fetchone()[0]>0
            for digest,path in c.execute('select sha256,path from artifacts'):
                assert (marker.parent/path).stat().st_ino==(job.root/'network/bodies'/digest).stat().st_ino
        inbox=json.loads((marker.parent/'inbox.json').read_bytes())
        assert all(e['source_observation']['url'].startswith('https://') for e in inbox['entries'])
    assert not any((job.root/'network/partial').iterdir())
    count=len(calls);job.close();job=Backfill(p);assert job.run()['phase']=='complete' and len(calls)==count;job.close()


def test_restart_reuses_committed_version_and_frozen_batches(fixture,monkeypatch):
    p,calls,_=fixture;job=Backfill(p);original=job.admit;crashed=[False]
    def interrupt(*args,**kwargs):
        result=original(*args,**kwargs)
        if not crashed[0]:crashed[0]=True;raise RuntimeError('simulated interruption after parse')
        return result
    monkeypatch.setattr(job,'admit',interrupt)
    with pytest.raises(RuntimeError,match='simulated interruption'):job.run()
    job.close();job=Backfill(p);result=job.run();job.close()
    assert result['family_states']=={'complete':3}
    assert len(calls)==12


def test_identity_conflicts_quarantine_without_promoting_or_archive_guessing(fixture):
    p,calls,wrong=fixture;wrong[0]=True
    job=Backfill(p);result=job.run()
    assert result['family_states']=={'quarantined':3}
    assert all(not json.loads(m.read_bytes())['entries'] for m in (job.root/'batches').glob('*/inbox.json'))
    assert not (job.root/'current.json').exists();job.close()


def test_completed_freeze_recovery_and_tamper_refusal(fixture):
    p,calls,_=fixture;job=Backfill(p);job.run()
    # A crash after marker fsync but before control registration is recoverable.
    with job.c:job.c.execute('DELETE FROM batches WHERE number=1')
    job.close();job=Backfill(p);assert job.c.execute('select count(*) from batches').fetchone()[0]==2;job.close()
    root=Path(json.loads(p.read_bytes())['workspace'])
    with (root/'batches/00001/data/gitrx.sqlite').open('ab') as f:f.write(b'tamper')
    with pytest.raises(RuntimeError,match='Frozen batch database changed'):Backfill(p)

def test_restart_freezes_batch_after_queue_commit_before_marker(fixture,monkeypatch):
    p,calls,_=fixture;job=Backfill(p);original=job.freeze_batch;crashed=[False]
    def interrupt(number):
        terminal=job.c.execute("select count(*) from queue where batch=? and state in ('pending','in_progress')",(number,)).fetchone()[0]==0
        if terminal and not crashed[0]:crashed[0]=True;raise RuntimeError('after queue commit before freeze')
        return original(number)
    monkeypatch.setattr(job,'freeze_batch',interrupt)
    with pytest.raises(RuntimeError,match='after queue commit'):job.run()
    job.close();job=Backfill(p)
    assert job.c.execute('select count(*) from batches').fetchone()[0]==1
    assert (job.root/'batches/00001/BATCH.json').exists()
    result=job.run();assert result['frozen_batches']==2 and len(calls)==12;job.close()

def test_empty_archive_page_retains_source_without_stopping_whole_job(fixture,tmp_path,monkeypatch):
    p,calls,_=fixture;job=Backfill(p);root,c,fetcher=job.open_batch(1)
    body=tmp_path/'empty.html';body.write_bytes(b'')
    monkeypatch.setattr(job,'response',lambda url,reuse=True:{'id':1,'namespace':'fixture','url':url,'final_url':url,'retrieved_at':'2026-11-07T00:00:00Z','status':200,'headers':{},'error':None,'path':str(body),'sha256':sha(body),'bytes':0})
    assert job.archive_fallback(root,c,fetcher,IDS[0],{1})=={1}
    assert c.execute("select count(*) from retrievals where kind='archive_listing'").fetchone()[0]==1
    job.close()

def test_hot_sqlite_and_parallel_processing_freeze_preserve_native_checkpoints(fixture,tmp_path):
    p,calls,_=fixture;root=Path(json.loads(p.read_bytes())['workspace']);root.mkdir()
    (root/'LOCAL-STORAGE-POLICY.json').write_text(json.dumps({'format':'gitrx-hot-storage-1','local_root':str(tmp_path/'local-hot'),'max_bytes':8*1024**3}))
    (root/'PROCESSING-POLICY.json').write_text(json.dumps({'max_workers':2,'processing_spool_root':str(tmp_path/'local-spool')}))
    job=Backfill(p)
    assert (root/'control.sqlite').is_symlink() and (root/'network/ledger.sqlite').is_symlink()
    result=job.run()
    assert result['family_states']=={'complete':3}
    assert result['versions']['new_parsed_complete']==6
    assert job.processing.committed==6
    for db in root.glob('batches/*/data/gitrx.sqlite'):
        assert db.is_file() and not db.is_symlink()
    assert not list((tmp_path/'local-spool').rglob('payload.pickle'))
    job.close();count=len(calls)
    job=Backfill(p);assert job.run()['phase']=='complete' and len(calls)==count;job.close()


def test_local_raw_staging_freezes_verified_external_batches(fixture,tmp_path):
    p,calls,_=fixture;root=Path(json.loads(p.read_bytes())['workspace']);root.mkdir()
    (root/'LOCAL-STORAGE-POLICY.json').write_text(json.dumps({'format':'gitrx-hot-storage-1',
        'local_root':str(tmp_path/'local/hot'),'max_bytes':1024**3,
        'raw_staging_root':str(tmp_path/'local/raw'),'raw_staging_max_bytes':1024**3}))
    job=Backfill(p)
    try:
        assert job.http.root==tmp_path/'local/raw/network'
        job.run(max_families=1)
        active=job.active[0]
        assert (active/'artifacts').is_symlink()
        assert list((tmp_path/'local/raw/network/bodies').iterdir())
        assert not list((root/'network/bodies').glob('*'))
    finally:job.close()
    job=Backfill(p)
    try:
        result=job.run()
        assert result['family_states']=={'complete':3}
        for marker in (root/'batches').glob('*/BATCH.json'):
            assert not (marker.parent/'artifacts').is_symlink()
            with sqlite3.connect(marker.parent/'data/gitrx.sqlite') as c:
                for digest,path in c.execute('SELECT sha256,path FROM artifacts'):
                    assert sha(marker.parent/path)==digest
        assert not list((tmp_path/'local/raw/network/bodies').iterdir())
        from gitrx_backend.backfill_recovery import observation_rows
        observations,_=observation_rows(root)
        assert observations
    finally:job.close()
    job=Backfill(p)
    try:assert job.run()['phase']=='complete'
    finally:job.close()


def test_two_lane_backfill_preserves_native_evidence_and_freezes(fixture,tmp_path,monkeypatch):
    p,calls,_=fixture;root=Path(json.loads(p.read_bytes())['workspace']);root.mkdir()
    (root/'TRANSPORT-POLICY.json').write_text(json.dumps({'format':'gitrx-transport-policy-1','version':1,
        'minimum_spacing_seconds':.1,'initial_spacing_seconds':.1,'maximum_spacing_seconds':30,'download_lanes':2}))
    import gitrx_backend.jobs as jobs
    from gitrx_backend import parallel_downloads
    class Connection:
        serial=1
        def open(self,request,timeout):return jobs.urllib.request.build_opener().open(request,timeout=timeout)
        def close(self):pass
    monkeypatch.setattr(parallel_downloads,'SingleConnection',Connection)
    import gitrx_services.release1_connection as connections
    monkeypatch.setattr(connections,'SingleConnection',Connection)
    job=Backfill(p)
    try:
        result=job.run()
        assert result['family_states']=={'complete':3}
        assert result['versions']['new_parsed_complete']==6
        assert result['requests_used']==12
        assert result['frozen_batches']==2
    finally:job.close()


@pytest.mark.parametrize('all_batches',[False,True])
def test_buffered_backfill_consumes_prefetch_and_freezes_across_batches(fixture,tmp_path,monkeypatch,all_batches):
    p,calls,_=fixture;root=Path(json.loads(p.read_bytes())['workspace']);root.mkdir()
    (root/'LOCAL-STORAGE-POLICY.json').write_text(json.dumps({'format':'gitrx-hot-storage-1',
        'local_root':str(tmp_path/'local/hot'),'max_bytes':1024**3,
        'raw_staging_root':str(tmp_path/'local/raw'),'raw_staging_max_bytes':1024**3}))
    (root/'TRANSPORT-POLICY.json').write_text(json.dumps({'format':'gitrx-transport-policy-1','version':1,
        'minimum_spacing_seconds':.1,'initial_spacing_seconds':.1,'maximum_spacing_seconds':30,'download_lanes':10,
        'download_buffer_enabled':True,'download_ahead_all_batches':all_batches,'history_confirmation_prefetch':all_batches,'download_buffer_items':120,'download_buffer_bytes':1024**3}))
    (root/'PROCESSING-POLICY.json').write_text(json.dumps({'max_workers':12,'worker_RSS_limit_bytes':512*1024**2,
        'total_RSS_budget_bytes':8*1024**3,'queue_limit':24}))
    import gitrx_backend.jobs as jobs
    from gitrx_backend import parallel_downloads
    import gitrx_services.release1_connection as connections
    class Connection:
        serial=1
        def open(self,request,timeout):return jobs.urllib.request.build_opener().open(request,timeout=timeout)
        def close(self):pass
    monkeypatch.setattr(parallel_downloads,'SingleConnection',Connection)
    monkeypatch.setattr(connections,'SingleConnection',Connection)
    job=Backfill(p)
    original_freeze=job.hot.freeze
    def freeze_while_acquisition_runs(path):
        if all_batches:assert not job.buffer.paused
        return original_freeze(path)
    monkeypatch.setattr(job.hot,'freeze',freeze_while_acquisition_runs)
    try:
        if all_batches:
            import time
            rows=job.c.execute('SELECT * FROM queue ORDER BY ordinal').fetchall()
            plan=[(r['setid'],r['ordinal'],2,r['batch']) for r in rows]
            job.buffer.activate(rows[0]['batch'],plan,set())
            deadline=time.monotonic()+10
            while job.buffer.read_db.execute("SELECT COUNT(*) FROM tasks WHERE state='ready'").fetchone()[0]<9:
                assert time.monotonic()<deadline
                time.sleep(.01)
            job.buffer.pause();job.buffer.activate(rows[0]['batch'],[],set())
        result=job.run()
        assert result['family_states']=={'complete':3}
        assert result['versions']['new_parsed_complete']==6
        assert result['frozen_batches']==2
        assert job.processing.options['workers']==12
        assert job.processing.committed==6
        if all_batches:
            assert job.buffer.plan_seeded
            assert result['requests_used']==12  # two independent histories plus two ZIPs per family
            import gitrx_services.io as source_io
            for marker in job.root.glob('batches/*/BATCH.json'):
                with source_io.readonly(marker.parent/'data/gitrx.sqlite') as source:
                    for sid, in source.execute('SELECT setid FROM families'):
                        passes=source.execute('SELECT started_at FROM release1_history_passes WHERE setid=? ORDER BY pass_number',(sid,)).fetchall()
                        assert len(passes)==2 and passes[0][0]<passes[1][0]
        from gitrx_backend.backfill_recovery import observation_rows
        observations,_=observation_rows(root);assert observations
        assert not list((tmp_path/'local/raw/network/bodies').iterdir())
    finally:job.close()
    job=Backfill(p)
    try:assert job.run()['phase']=='complete'
    finally:job.close()


@pytest.mark.parametrize('ahead',[False,True])
def test_mobile_job_finishes_offline_then_publishes_verified_batches(fixture,tmp_path,monkeypatch,ahead):
    from gitrx_backend.mobile_setup import prepare
    from gitrx_backend.mobile_archive import MobileArchive
    p,calls,_=fixture;archive=Path(json.loads(p.read_bytes())['workspace']);archive.mkdir()
    cfg=json.loads(p.read_bytes());cfg['volume']=str(archive);p.write_text(json.dumps(cfg))
    (archive/'LOCAL-STORAGE-POLICY.json').write_text(json.dumps({'format':'gitrx-hot-storage-1',
        'local_root':str(tmp_path/'local/hot'),'max_bytes':1024**3,
        'raw_staging_root':str(tmp_path/'local/raw'),'raw_staging_max_bytes':1024**3}))
    (archive/'TRANSPORT-POLICY.json').write_text(json.dumps({'format':'gitrx-transport-policy-1','version':1,
        'minimum_spacing_seconds':.2,'initial_spacing_seconds':.2,'maximum_spacing_seconds':30}))
    (archive/'PROCESSING-POLICY.json').write_text('{}')
    import gitrx_backend.jobs as jobs
    import gitrx_services.release1_connection as connections
    class Connection:
        serial=1
        def open(self,request,timeout):return jobs.urllib.request.build_opener().open(request,timeout=timeout)
        def close(self):pass
    monkeypatch.setattr(connections,'SingleConnection',Connection)
    job=Backfill(p)
    try:job.run(max_families=1)
    finally:job.close()
    mobile=prepare(p,tmp_path/'local/mobile-workspace')
    if ahead:
        policy_path=mobile.parent/'TRANSPORT-POLICY.json';policy=json.loads(policy_path.read_bytes())
        policy.update(version=policy['version']+1,download_lanes=2,download_buffer_enabled=True,download_ahead_all_batches=True)
        policy_path.write_text(json.dumps(policy))
        monkeypatch.setattr('gitrx_backend.parallel_downloads.SingleConnection',Connection)
    unplugged=archive.with_name('unplugged-ssd');archive.rename(unplugged)
    online=[False];monkeypatch.setattr(MobileArchive,'available',lambda self,**kw:online[0])
    job=Backfill(mobile)
    try:
        if ahead:
            import time
            rows=job.c.execute("SELECT * FROM queue WHERE state='pending' ORDER BY ordinal").fetchall()
            job.buffer.activate(rows[0]['batch'],[(r['setid'],r['ordinal'],2,r['batch']) for r in rows],set())
            deadline=time.monotonic()+10
            while job.buffer.read_db.execute("SELECT COUNT(*) FROM tasks WHERE state='ready'").fetchone()[0]<3*len(rows):
                assert time.monotonic()<deadline
                time.sleep(.01)
            job.buffer.pause();job.buffer.activate(rows[0]['batch'],[],set())
            # Finish/archive one batch, then disconnect again while a future
            # batch is already downloaded. It must remain usable locally.
            job.run(max_families=1)
            unplugged.rename(archive);online[0]=True;job.mobile.sync_pending()
            archive.rename(unplugged);online[0]=False
        result=job.run()
        assert result['family_states']=={'complete':3}
        assert result['mobile_archive']['waiting_batches']==(1 if ahead else 2)
        assert not archive.exists()
        assert not list((archive/'batches').glob('*/BATCH.json'))
        assert result['versions']['new_parsed_complete']==6
        frozen=[(x,sha(x)) for x in (job.root/'batches').glob('*/BATCH.json')]
        unplugged.rename(archive);online[0]=True;job.mobile.sync_pending()
        for marker,digest in frozen:
            final=archive/'batches'/marker.parent.name
            assert sha(final/'BATCH.json')==digest
            m=json.loads(marker.read_bytes())
            assert sha(final/'data/gitrx.sqlite')==m['source_database_sha256']
            inbox=json.loads((final/'inbox.json').read_bytes())
            assert all(str(archive) in e['source_database'] for e in inbox['entries'])
            assert (marker.parent/'data/gitrx.sqlite').is_symlink()
        assert job.mobile.status()['waiting_batches']==0
    finally:job.close()
    # Archived DB bytes may be absent while disconnected. The local immutable
    # receipt preserves their previously verified identity, without claiming a fresh read.
    online[0]=False
    archive.rename(unplugged)
    job=Backfill(mobile)
    try:assert job.run()['phase']=='complete'
    finally:job.close()


def test_mobile_deferred_family_survives_other_batch_and_retry_totals(fixture,tmp_path,monkeypatch):
    from gitrx_backend.mobile_archive import MobileArchive,ArchiveUnavailable
    from gitrx_backend.mobile_setup import prepare
    p,calls,_=fixture;cfg=json.loads(p.read_bytes());archive=Path(cfg['workspace']);cfg['volume']=str(archive);p.write_text(json.dumps(cfg));archive.mkdir()
    (archive/'LOCAL-STORAGE-POLICY.json').write_text(json.dumps({'format':'gitrx-hot-storage-1','local_root':str(tmp_path/'local/hot'),
        'raw_staging_root':str(tmp_path/'local/raw'),'max_bytes':1024**3,'raw_staging_max_bytes':1024**3}))
    (archive/'TRANSPORT-POLICY.json').write_text(json.dumps({'format':'gitrx-transport-policy-1','version':1,'minimum_spacing_seconds':.2,'initial_spacing_seconds':.2,'maximum_spacing_seconds':30}))
    (archive/'PROCESSING-POLICY.json').write_text('{}')
    import gitrx_backend.jobs as jobs
    import gitrx_services.release1_connection as connections
    class Connection:
        serial=1
        def open(self,request,timeout):return jobs.urllib.request.build_opener().open(request,timeout=timeout)
        def close(self):pass
    monkeypatch.setattr(connections,'SingleConnection',Connection)
    job=Backfill(p);job.close()
    mobile=prepare(p,tmp_path/'local/mobile-workspace');online=[False]
    monkeypatch.setattr(MobileArchive,'available',lambda self,**kw:online[0])
    job=Backfill(mobile);original=job.family
    def deferred(row):
        result=original(row)
        if row['ordinal']==1:raise ArchiveUnavailable('fixture archive dependency')
        return result
    monkeypatch.setattr(job,'family',deferred)
    try:
        result=job.run(max_families=3)
        assert result['family_states']['waiting_for_archive']==1
        assert result['versions']['new_parsed_complete']==6
        assert result['frozen_batches']==1
    finally:job.close()
    online[0]=True;job=Backfill(mobile)
    try:
        result=job.run()
        assert result['family_states']=={'complete':3}
        assert result['versions']['new_parsed_complete']==6
        assert result['frozen_batches']==2
    finally:job.close()


def test_recorded_history_uses_stable_artifact_after_shared_raw_buffer_moves(fixture):
    p,calls,_=fixture;job=Backfill(p)
    try:
        row=job.c.execute('SELECT * FROM queue ORDER BY ordinal LIMIT 1').fetchone()
        root,c,fetcher=job.open_batch(row['batch'])
        url='https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/'+row['setid']+'/history.json?pagesize=100&page=1'
        response=job.response(url);original=Path(response['path']);digest=response['sha256']
        rid=job.record(c,root,fetcher,response,'history')
        original.unlink()
        assert Path(response['path']).is_relative_to(root/'artifacts')
        assert sha(response['path'])==digest
        saved=json.loads(c.execute('SELECT observation_json FROM backfill_http WHERE retrieval_id=?',(rid,)).fetchone()[0])
        assert saved['path']==str(original) and saved['sha256']==digest and saved['url']==url
    finally:job.close()


def enable_family_pipeline(p,workers=2):
    root=Path(json.loads(p.read_bytes())['workspace']);root.mkdir(exist_ok=True)
    (root/'PROCESSING-POLICY.json').write_text(json.dumps({'max_workers':workers,'family_pipeline_limit':3}))


def test_family_pipeline_admits_next_family_before_first_parse_finishes(fixture,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    p,calls,_=fixture;enable_family_pipeline(p)
    job=Backfill(p);seen=[]
    class CPU:
        _processes={}
        def __init__(self):self.pool=ThreadPoolExecutor(max_workers=2)
        def submit(self,fn,*args):
            def delayed():__import__('threading').Event().wait(.4);return fn(*args)
            return self.pool.submit(delayed)
        def shutdown(self,**kwargs):self.pool.shutdown(**kwargs)
    monkeypatch.setattr(job.processing,'_start',lambda:setattr(job.processing,'executor',job.processing.executor or CPU()))
    original=job.family
    def family(row):
        seen.append((row['setid'],set(job.inflight_families),{j['version']['setid'] for j in job.processing.pending.values()}))
        return original(row)
    monkeypatch.setattr(job,'family',family)
    try:
        result=job.run()
        assert seen[0][0] in seen[1][1] and seen[0][0] in seen[1][2]
        assert result['family_states']=={'complete':3} and result['versions']['new_parsed_complete']==6
        assert result['frozen_batches']==2 and not job.processing.pending and not job.inflight_families
    finally:job.close()


def test_pipeline_bounded_pause_and_restart_keep_native_family_counts(fixture):
    p,calls,_=fixture;enable_family_pipeline(p);job=Backfill(p)
    try:
        result=job.run(max_families=1)
        assert result['attempted_families']==1 and result['phase']=='paused'
        assert not job.processing.pending and not job.inflight_families
    finally:job.close()
    job=Backfill(p)
    try:
        result=job.run()
        assert result['family_states']=={'complete':3}
        assert result['versions']['new_parsed_complete']==6 and result['frozen_batches']==2
    finally:job.close()


def test_pipeline_interruption_reuses_native_admissions_and_earmarks(fixture,monkeypatch):
    p,calls,_=fixture;enable_family_pipeline(p);job=Backfill(p);original=job.family;families=[]
    monkeypatch.setattr(job.processing,'drain',lambda **kwargs:None)
    def family(row):
        families.append(row['setid'])
        if len(families)==2:raise RuntimeError('fixture interrupted while previous family parses')
        return original(row)
    monkeypatch.setattr(job,'family',family)
    try:
        with pytest.raises(RuntimeError,match='fixture interrupted'):job.run()
        assert job.inflight_families and job.processing.pending
    finally:job.close()
    job=Backfill(p)
    try:
        result=job.run()
        assert result['family_states']=={'complete':3} and result['versions']['new_parsed_complete']==6
        assert result['versions']['new_downloaded_valid']==6 and result['frozen_batches']==2
    finally:job.close()


def test_worker_raises_only_its_own_low_descriptor_limit(tmp_path):
    import subprocess,sys
    code='''import resource
from gitrx_backend.backfill import configure_file_limit
_,hard=resource.getrlimit(resource.RLIMIT_NOFILE)
resource.setrlimit(resource.RLIMIT_NOFILE,(64,hard))
assert configure_file_limit()==min(4096,hard) if hard!=resource.RLIM_INFINITY else configure_file_limit()==4096
assert resource.getrlimit(resource.RLIMIT_NOFILE)[1]==hard
'''
    subprocess.run([sys.executable,'-c',code],check=True)


@pytest.mark.parametrize('method',['seed','parent'])
def test_temporary_source_connections_close_even_on_read_failure(tmp_path,monkeypatch,method):
    import gitrx_backend.backfill as module
    class ReadConnection:
        closed=False
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,*args):raise RuntimeError('source read failed')
        def close(self):self.closed=True
    connection=ReadConnection();monkeypatch.setattr(module,'readonly',lambda p:connection)
    job=object.__new__(Backfill);job.mobile=None
    raw=tmp_path/'raw';raw.write_bytes(b'fixture')
    job.seed_samples={('fixture',1):{'path':str(raw),'sha256':sha(raw),'retrieval_id':1}}
    job.config={'seed_sample':str(tmp_path/'seed.json'),'seed_sample_sha256':'fixture'}
    job.known={('fixture',1):{'parse_runs':[{'id':'source:1'}],'selected_sha256':sha(raw)}}
    job.parent={'sources':{'source':{'root':str(tmp_path)}}}
    with pytest.raises(RuntimeError,match='source read failed'):
        if method=='seed':job.seed_response('fixture',1)
        else:job.parent_ref(None,'fixture',{'version':1})
    assert connection.closed


def test_restart_hashes_each_frozen_database_once_without_skipping_pins(fixture,monkeypatch):
    import gitrx_backend.backfill as module
    from collections import Counter
    p,_,_=fixture;job=Backfill(p)
    try:job.run()
    finally:job.close()
    databases={x.parent/'data/gitrx.sqlite' for x in (job.root/'batches').glob('*/BATCH.json')}
    calls=Counter();original=module.sha
    def counted(path):
        path=Path(path)
        if path in databases:calls[path]+=1
        return original(path)
    monkeypatch.setattr(module,'sha',counted)
    restarted=Backfill(p)
    try:assert calls==Counter({path:1 for path in databases})
    finally:restarted.close()


def test_released_hot_batches_reuse_verified_digest_and_check_hot_pin(fixture,tmp_path,monkeypatch):
    import gitrx_backend.hot_storage as hot_module
    p,_,_=fixture;root=Path(json.loads(p.read_text())['workspace']);root.mkdir()
    (root/'LOCAL-STORAGE-POLICY.json').write_text(json.dumps({'format':'gitrx-hot-storage-1','local_root':str(tmp_path/'local-hot'),'max_bytes':8*1024**3}))
    job=Backfill(p)
    try:job.run()
    finally:job.close()
    databases=set(root.glob('batches/*/data/gitrx.sqlite'));reads=[];original=hot_module.sha
    def hashed(path):
        if Path(path) in databases:reads.append(Path(path))
        return original(path)
    monkeypatch.setattr(hot_module,'sha',hashed)
    restarted=Backfill(p)
    try:
        assert reads==[]
        record=restarted.hot._identity(next(iter(databases)))[3]
    finally:restarted.close()
    state=json.loads(record.read_text());state['frozen_sha256']='0'*64;record.write_text(json.dumps(state))
    with pytest.raises(RuntimeError):Backfill(p)
