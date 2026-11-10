import io,json,threading,time
from pathlib import Path
from urllib.parse import urlsplit
import pytest
from gitrx_backend.jobs import HTTP
from gitrx_backend.download_buffer import DownloadBuffer
from gitrx_backend import parallel_downloads
import gitrx_services.release1_connection as connections


def setup(tmp_path,monkeypatch,pin='pinned-job',buffer_policy=None):
    policy={'format':'gitrx-transport-policy-1','version':1,'minimum_spacing_seconds':.1,
        'initial_spacing_seconds':.1,'maximum_spacing_seconds':30,'download_lanes':10,
        'download_buffer_items':10,'download_buffer_bytes':1024**3}
    policy.update(buffer_policy or {})
    tmp_path.mkdir(parents=True,exist_ok=True)
    p=tmp_path/'policy.json';p.write_text(json.dumps(policy))
    class Response(io.BytesIO):
        code=200;headers={}
        def __enter__(self):return self
        def __exit__(self,*args):self.close()
    class Connection:
        serial=1
        def open(self,req,timeout):
            if 'history.json' in req.full_url:
                sid=urlsplit(req.full_url).path.split('/')[-2]
                return Response(json.dumps({'metadata':{'total_pages':1,'current_page':1},
                    'data':{'spl':{'setid':sid},'history':[{'spl_version':1},{'spl_version':2}]}}).encode())
            return Response(b'fixture raw bytes')
        def close(self):pass
    monkeypatch.setattr(parallel_downloads,'SingleConnection',Connection)
    monkeypatch.setattr(connections,'SingleConnection',Connection)
    h=HTTP(tmp_path/'network',cap=200,transport_policy=p,threaded_coordinator=True,retain_completed_partials=False)
    b=DownloadBuffer(h,tmp_path/'buffer',pin,lambda:None,lambda:None,policy)
    return b,h


def until(condition,timeout=10):
    deadline=time.monotonic()+timeout
    while not condition():
        assert time.monotonic()<deadline
        time.sleep(.05)


def state(b):
    try:return json.loads((b.root/'download-buffer-status.json').read_bytes())
    except FileNotFoundError:return {}


def test_downloads_run_while_consumer_is_idle_and_buffer_backpressures(tmp_path,monkeypatch):
    b,h=setup(tmp_path,monkeypatch)
    plan=[(f'family{n}',n,2) for n in range(20)]
    try:
        b.activate(1,plan,set())
        until(lambda:state(b).get('ready_files',0)==10)
        # No consumer/parser requests have happened, yet raw files are ready.
        b.pause();count=h.request_count;time.sleep(.2)
        assert h.request_count==count==10
        assert state(b)['ready_files']==10
        url='https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/family0/history.json?pagesize=100&page=1'
        _,record,path=b.request(url)
        assert path.exists() and record['status']==200 and h.request_count==count
        b.family_done('family0')
    finally:b.close()


def test_buffer_restart_reuses_retained_observation_without_network(tmp_path,monkeypatch):
    b,h=setup(tmp_path,monkeypatch)
    url='https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/family0/history.json?pagesize=100&page=1'
    try:
        b.activate(1,[('family0',0,2)],set())
        until(lambda:state(b).get('ready_files',0)>=1)
        b.pause();_,old,path=b.request(url);count=h.request_count
    finally:b.close()
    b,h=setup(tmp_path,monkeypatch)
    try:
        _,new,path=b.request(url)
        assert new['id']==old['id'] and h.request_count==count
    finally:b.close()


def test_corrupt_buffered_source_stops_instead_of_redownloading(tmp_path,monkeypatch):
    b,h=setup(tmp_path,monkeypatch)
    url='https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/family0/history.json?pagesize=100&page=1'
    try:
        b.activate(1,[('family0',0,2)],set());until(lambda:state(b).get('ready_files',0)>=1)
        b.pause();_,row,path=b.request(url);count=h.request_count;path.write_bytes(b'corrupt')
        with pytest.raises(RuntimeError,match='hash changed'):b.request(url)
        assert h.request_count==count
    finally:b.close()


def test_download_ahead_without_file_count_stop_still_honors_byte_budget(tmp_path,monkeypatch):
    b,h=setup(tmp_path,monkeypatch,buffer_policy={'download_buffer_items':None,'download_buffer_bytes':16*1024**3})
    try:
        b.activate(1,[(f'family{n}',n,2) for n in range(5)],set())
        until(lambda:state(b).get('ready_files',0)==15)
        assert state(b)['max_ready_files'] is None
        assert h.request_count==15
        b.pause()
    finally:b.close()
    # Disk bounds still limit acquisition even with no file-count stop.
    b,h=setup(tmp_path/'small',monkeypatch,buffer_policy={'download_buffer_items':None,'download_buffer_bytes':100*1024**2})
    try:
        b.activate(1,[(f'family{n}',n,2) for n in range(5)],set())
        until(lambda:state(b).get('ready_files',0)==1)
        b.pause()
        assert h.request_count==1 and state(b)['queued_files']>0
    finally:b.close()


def test_ready_response_reaches_consumer_before_slow_wave_finishes(tmp_path,monkeypatch):
    b,h=setup(tmp_path,monkeypatch);slow_started=threading.Event();slow_finished=threading.Event()
    real=parallel_downloads.SingleConnection
    class Connection(real):
        def open(self,req,timeout):
            if '/family0/' in req.full_url:time.sleep(.3)
            if '/family1/' in req.full_url:
                slow_started.set();time.sleep(1.5);slow_finished.set()
            return super().open(req,timeout)
    monkeypatch.setattr(parallel_downloads,'SingleConnection',Connection)
    try:
        b.activate(1,[(f'family{n}',n,2) for n in range(5)],set())
        assert slow_started.wait(5)
        url='https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/family0/history.json?pagesize=100&page=1'
        start=time.monotonic();result=b.get_many([url])
        assert result[0][1]['status']==200 and result[0][2].exists()
        assert time.monotonic()-start<1 and not slow_finished.is_set()
        # A canceled speculative command is harmless when acquisition drains it.
        b.pause();b.check()
    finally:b.close()


def test_bookkeeping_does_not_interrupt_prefetch_but_sync_commands_do():
    import queue
    from concurrent.futures import Future
    b=object.__new__(DownloadBuffer);b.commands=queue.Queue()
    b.commands.put(('ack',('unused',),None))
    b.commands.put(('family_done',('unused',),None))
    assert not b._urgent_waiting()
    future=Future();b.commands.put(('pause',(),future))
    assert b._urgent_waiting()
    future.cancel();assert not b._urgent_waiting()


def test_cross_batch_downloads_keep_native_batch_identity_across_handoff_and_restart(tmp_path,monkeypatch):
    policy={'download_ahead_all_batches':True,'download_buffer_items':None}
    b,h=setup(tmp_path,monkeypatch,buffer_policy=policy)
    url='https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/future/history.json?pagesize=100&page=1'
    try:
        b.activate(1,[('current',0,2,1),('future',1,3,2)],set())
        until(lambda:state(b).get('ready_files',0)==7)
        b.pause();_,old,path=b.request(url);count=h.request_count
        # The listing-only latest version must be hinted in the future family.
        assert b.read_db.execute("select batch from tasks where url like '%setid=future&version=3'").fetchone()[0]==2
        b.family_done('current');b.activate(2,[],set());b.pause()
        assert b.read_db.execute('select state from tasks where url=?',(url,)).fetchone()[0]=='ready'
    finally:b.close()
    b,h=setup(tmp_path,monkeypatch,buffer_policy=policy)
    try:
        assert b.plan_seeded
        _,row,path=b.request(url);assert row['id']==old['id'] and h.request_count==count
        b.activate(2,[],set());b.family_done('future');b.pause()
        assert b.read_db.execute('select state from tasks where url=?',(url,)).fetchone()[0]=='consumed'
    finally:b.close()


def test_history_confirmation_is_distinct_dated_retained_and_restartable(tmp_path,monkeypatch):
    policy={'history_confirmation_prefetch':True}
    b,h=setup(tmp_path,monkeypatch,buffer_policy=policy)
    url='https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/current/history.json?pagesize=100&page=1'
    try:
        b.activate(1,[('current',1,2)],set())
        until(lambda:b.confirmation(url) is not None)
        b.pause();first=b._read_ready(url);second=b.confirmation(url);count=h.request_count
        assert first[1]['id']!=second[1]['id'] and first[1]['started']<second[1]['started']
        assert first[1]['sha256']==second[1]['sha256']
        assert second[1]['sha256'] in b.ready_hashes()
    finally:b.close()
    b,h=setup(tmp_path,monkeypatch,buffer_policy=policy)
    try:
        assert b.confirmation(url)[1]['id']==second[1]['id'] and h.request_count==count
        b.activate(1,[],set());b.family_done('current');b.pause()
        assert b.read_db.execute('select state from history_confirmations where url=?',(url,)).fetchone()[0]=='consumed'
    finally:b.close()



def test_buffer_survives_server_cooldown_without_losing_ready_reads(tmp_path,monkeypatch):
    b,h=setup(tmp_path,monkeypatch)
    h.pacing.policy.update(controller='server-feedback-v1',fixed_rate=True,hold_seconds=1,wait_for_retry_after=True)
    original=parallel_downloads.SingleConnection;calls=[0]
    class Connection(original):
        def open(self,request,timeout):
            response=super().open(request,timeout);calls[0]+=1
            if calls[0]==1:response.code=429;response.headers={'retry-after':'1'}
            return response
    monkeypatch.setattr(parallel_downloads,'SingleConnection',Connection)
    try:
        b.activate(1,[('family0',0,2)],set())
        until(lambda:state(b).get('ready_files',0)>=1)
        b.pause()
        url='https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/family0/history.json?pagesize=100&page=1'
        _,row,path=b.request(url)
        assert row['status']==200 and path.exists() and b.fault is None and b.thread.is_alive()
        assert b.read_http.execute('SELECT COUNT(*) FROM requests WHERE status=429').fetchone()[0]==1
    finally:b.close()
