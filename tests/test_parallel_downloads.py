import io,json,threading,time
from pathlib import Path
import pytest
from gitrx_backend.jobs import HTTP,sha
from gitrx_backend import parallel_downloads as parallel


def transport(tmp_path,monkeypatch,status=200,headers=None,cap=10,lanes=2,delay=.22,spacing=.1):
    p=tmp_path/'policy.json';p.write_text(json.dumps({'format':'gitrx-transport-policy-1','version':1,
        'minimum_spacing_seconds':spacing,'initial_spacing_seconds':spacing,'maximum_spacing_seconds':30,
        'download_lanes':lanes,'clean_requests_to_speed_up':20}))
    observed={'active':0,'maximum':0,'starts':[]};lock=threading.Lock()
    class Response(io.BytesIO):
        code=status
        def __init__(self):super().__init__(b'original downloaded bytes');self.headers=headers or {}
        def __enter__(self):return self
        def __exit__(self,*args):
            with lock:observed['active']-=1
            self.close()
    class Connection:
        serial=1
        def open(self,request,timeout):
            with lock:
                observed['active']+=1;observed['maximum']=max(observed['maximum'],observed['active']);observed['starts'].append(time.time())
            time.sleep(delay)
            return Response()
        def close(self):pass
    monkeypatch.setattr(parallel,'SingleConnection',Connection)
    owner=threading.get_ident()
    def guard():assert threading.get_ident()==owner
    h=HTTP(tmp_path/'network',cap=cap,transport_policy=p,guard=guard,retain_completed_partials=False)
    return h,observed


def test_two_lanes_overlap_with_shared_spacing_and_single_ledger(tmp_path,monkeypatch):
    h,observed=transport(tmp_path,monkeypatch)
    try:
        results=h.get_many(['https://dailymed.nlm.nih.gov/a','https://dailymed.nlm.nih.gov/b'])
        assert observed['maximum']==2
        assert observed['starts'][1]-observed['starts'][0]>=.1
        starts=[r[0] for r in h.c.execute('SELECT started FROM requests ORDER BY id')]
        assert starts[1]-starts[0]>=.1
        assert len(results)==h.request_count==2
        for url,row,path in results:assert row['status']==200 and row['sha256']==sha(path)
        assert not list((h.root/'partial').iterdir())
        assert len(h.get_many([result[0] for result in results]))==2 and h.request_count==2
    finally:h.close()


def test_shared_retry_after_stops_new_requests_and_survives_restart(tmp_path,monkeypatch):
    h,_=transport(tmp_path,monkeypatch,status=429,headers={'retry-after':'30'})
    try:
        with pytest.raises(RuntimeError,match='Retry deferred'):
            h.get_many(['https://dailymed.nlm.nih.gov/a','https://dailymed.nlm.nih.gov/b'])
        assert h.pacing.spacing>=2
        assert h.c.execute("SELECT value FROM transport_state WHERE key='retry_after'").fetchone()[0]>time.time()
        assert h.c.execute('SELECT COUNT(*) FROM requests WHERE sha256 IS NOT NULL').fetchone()[0]==h.request_count
    finally:h.close()
    h=HTTP(tmp_path/'network',transport_policy=tmp_path/'policy.json')
    try:
        with pytest.raises(RuntimeError,match='Retry deferred'):h.get('https://dailymed.nlm.nih.gov/c')
    finally:h.close()


def test_shared_request_cap_retains_inflight_response(tmp_path,monkeypatch):
    h,_=transport(tmp_path,monkeypatch,cap=1)
    try:
        with pytest.raises(RuntimeError,match='request cap'):
            h.get_many(['https://dailymed.nlm.nih.gov/a','https://dailymed.nlm.nih.gov/b'])
        assert h.request_count==1
        assert h.c.execute('SELECT sha256 FROM requests').fetchone()[0]
    finally:h.close()


@pytest.mark.parametrize('lanes',[10,20,25,30,40,60])
def test_parallel_lanes_have_one_global_start_interval(tmp_path,monkeypatch,lanes):
    h,observed=transport(tmp_path,monkeypatch,lanes=lanes,delay=lanes*.1+.2,cap=lanes)
    try:
        results=h.get_many([f'https://dailymed.nlm.nih.gov/{n}' for n in range(lanes)])
        assert observed['maximum']==lanes and len(results)==lanes
        starts=observed['starts']
        assert all(b-a>=.1 for a,b in zip(starts,starts[1:]))
        assert h.request_count==lanes
    finally:h.close()


def test_prefetch_can_yield_without_starting_requests(tmp_path,monkeypatch):
    h,observed=transport(tmp_path,monkeypatch,lanes=10)
    try:
        assert h.get_many(['https://dailymed.nlm.nih.gov/a'],interrupt=lambda:True)==[]
        assert h.request_count==0 and observed['maximum']==0
    finally:h.close()


def test_twenty_hertz_global_spacing_and_completed_response_publication(tmp_path,monkeypatch):
    h,observed=transport(tmp_path,monkeypatch,lanes=20,cap=20,delay=2.2,spacing=.05)
    delivered=[]
    def ready(url,row,path):
        assert row['status']==200 and row['sha256']==sha(path) and row['url']==url
        durable=h.c.execute('SELECT status FROM requests WHERE id=?',(row['id'],)).fetchone()[0]
        assert durable==200
        delivered.append(url)
    try:
        result=h.get_many([f'https://dailymed.nlm.nih.gov/{n}' for n in range(20)],on_result=ready)
        assert len(result)==len(delivered)==len(set(delivered))==20
        assert observed['maximum']==20
        assert all(b-a>=.05 for a,b in zip(observed['starts'],observed['starts'][1:]))
    finally:h.close()


def test_scheduling_window_refills_lanes_before_slow_request_finishes(tmp_path,monkeypatch):
    h,observed=transport(tmp_path,monkeypatch,lanes=2,cap=4,delay=.01)
    original=parallel.SingleConnection;slow_done=threading.Event();early=[]
    class Connection(original):
        def open(self,request,timeout):
            if request.full_url.endswith('/0'):
                time.sleep(.8);slow_done.set()
            return super().open(request,timeout)
    monkeypatch.setattr(parallel,'SingleConnection',Connection)
    try:
        result=h.get_many([f'https://dailymed.nlm.nih.gov/{n}' for n in range(4)],
            on_result=lambda url,row,path:early.append((url,slow_done.is_set())))
        assert len(result)==4
        assert any(url.endswith('/2') and not done for url,done in early)
        assert all(b-a>=.1 for a,b in zip(observed['starts'],observed['starts'][1:]))
    finally:h.close()


def test_parallel_confirmation_bypasses_prior_successful_observation(tmp_path,monkeypatch):
    h,_=transport(tmp_path,monkeypatch)
    urls=['https://dailymed.nlm.nih.gov/a','https://dailymed.nlm.nih.gov/b']
    try:
        first=h.get_many(urls);second=h.get_many(urls,reuse=False)
        assert h.request_count==4
        for old,new in zip(first,second):
            assert old[1]['id']!=new[1]['id'] and old[1]['started']<new[1]['started']
            assert old[1]['sha256']==new[1]['sha256']==sha(new[2])
    finally:h.close()


def test_completion_batch_does_not_starve_next_request_start(tmp_path,monkeypatch):
    from concurrent.futures import Future
    h,_=transport(tmp_path,monkeypatch,lanes=3,cap=6)
    clock=[10000.];monkeypatch.setattr(parallel.time,'time',lambda:clock[0])
    monkeypatch.setattr(parallel.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(parallel.time,'sleep',lambda delay:clock.__setitem__(0,clock[0]+delay))
    def waiting(futures,timeout,**kwargs):clock[0]+=max(timeout,.001)
    monkeypatch.setattr(parallel,'wait',waiting)
    pool=parallel.DownloadPool(h);pool.executor.shutdown()
    starts=[];futures=[]
    class Executor:
        def submit(self,fn,url,ident):
            starts.append(clock[0]);f=Future();futures.append((f,{'id':ident,'url':url,'status':200,'failure':None,'fatal':None}))
            if len(futures)>=3:
                for future,value in futures:
                    if not future.done():future.set_result(value)
            return f
        def shutdown(self,**kwargs):pass
    pool.executor=Executor();h.download_pool=pool
    monkeypatch.setattr(pool,'retain',lambda value,spacing:tmp_path/'retained')
    def consumed(*args):clock[0]+=.08
    try:
        result=pool.pair([f'https://dailymed.nlm.nih.gov/{n}' for n in range(6)],on_result=consumed)
        assert len(result)==6 and starts[3]-starts[0]<.31
    finally:h.close()


def test_server_cooldown_waits_in_process_preserving_observations(tmp_path,monkeypatch):
    h,observed=transport(tmp_path,monkeypatch,cap=5,delay=.01)
    h.pacing.policy.update(controller='server-feedback-v1',fixed_rate=True,hold_seconds=1,wait_for_retry_after=True)
    original=parallel.SingleConnection;calls=[0]
    class Connection(original):
        def open(self,request,timeout):
            response=super().open(request,timeout);calls[0]+=1
            if calls[0]==1:response.code=429;response.headers={'retry-after':'1'}
            return response
    monkeypatch.setattr(parallel,'SingleConnection',Connection)
    import gitrx_services.release1_connection as module
    monkeypatch.setattr(module,'SingleConnection',Connection)
    try:
        output=h.get_many(['https://dailymed.nlm.nih.gov/limited','https://dailymed.nlm.nih.gov/normal'])
        assert len(output)==2 and all(row['status']==200 for _,row,_ in output)
        assert observed['starts'][1]-observed['starts'][0]>=1
        assert h.c.execute('SELECT COUNT(*) FROM requests WHERE status=429').fetchone()[0]==1
        assert all(path.exists() for _,_,path in output)
    finally:h.close()


def test_completed_response_is_delivered_while_server_cooldown_blocks_new_starts(tmp_path,monkeypatch):
    h,observed=transport(tmp_path,monkeypatch,cap=8,delay=.01)
    h.pacing.policy.update(controller='server-feedback-v1',fixed_rate=True,hold_seconds=1,wait_for_retry_after=True)
    original=parallel.SingleConnection;limited=[False];delivered=[]
    class Connection(original):
        def open(self,request,timeout):
            response=super().open(request,timeout)
            if request.full_url.endswith('/ready'):time.sleep(.25)
            if request.full_url.endswith('/limited') and not limited[0]:
                limited[0]=True;response.code=429;response.headers={'retry-after':'1'}
            return response
    monkeypatch.setattr(parallel,'SingleConnection',Connection)
    import gitrx_services.release1_connection as module
    monkeypatch.setattr(module,'SingleConnection',Connection)
    try:
        h.get_many(['https://dailymed.nlm.nih.gov/ready','https://dailymed.nlm.nih.gov/limited','https://dailymed.nlm.nih.gov/third'],on_result=lambda url,row,path:delivered.append((url,time.time())))
        deadline=h.c.execute("SELECT value FROM transport_state WHERE key='retry_after'").fetchone()[0]
        assert next(when for url,when in delivered if url.endswith('/ready'))<deadline
    finally:h.close()
