import json,time,signal,threading
import pytest
from test_backend_portable import release,SID
from gitrx_backend.config import open_reader
from gitrx_backend.query import Query
from gitrx_backend.limits import QueryTimeout,deadline
from gitrx_backend.search_index import build

def test_deadline_interrupts_cpu_and_restores_handler():
    old=signal.getsignal(signal.SIGALRM);start=time.monotonic()
    with pytest.raises(QueryTimeout):
        with deadline(.03):
            while True:pass
    assert time.monotonic()-start<1 and signal.getsignal(signal.SIGALRM)==old
    with pytest.raises(QueryTimeout):
        with deadline(.03):
            with deadline(2):time.sleep(.2)
    assert signal.getitimer(signal.ITIMER_REAL)[0]==0

def test_nested_expiration_survives_repeated_fresh_processes():
    import subprocess,sys
    script='''
import signal,time
from gitrx_backend.limits import deadline,QueryTimeout
original=signal.getsignal(signal.SIGALRM)
for _ in range(20):
    try:
        with deadline(.01):
            with deadline(1):
                with deadline(1):time.sleep(.05)
    except QueryTimeout:pass
    else:raise AssertionError('deadline did not expire')
    time.sleep(.001)
    assert signal.getsignal(signal.SIGALRM)==original
    assert signal.getitimer(signal.ITIMER_REAL)==(0.0,0.0)
    with deadline(.1):pass
print('survived')
'''
    for _ in range(10):
        result=subprocess.run([sys.executable,'-B','-c',script],capture_output=True,text=True,timeout=10)
        assert result.returncode==0,(result.returncode,result.stdout,result.stderr)
        assert result.stdout.strip()=='survived'

def test_inner_timeout_preserves_live_outer_timer_and_handler():
    original=signal.getsignal(signal.SIGALRM)
    with deadline(.2):
        with pytest.raises(QueryTimeout):
            with deadline(.01):time.sleep(.03)
        assert 0<signal.getitimer(signal.ITIMER_REAL)[0]<.2
    assert signal.getsignal(signal.SIGALRM)==original
    assert signal.getitimer(signal.ITIMER_REAL)==(0.0,0.0)

def test_swallowed_nested_timeout_cannot_report_success():
    with pytest.raises(QueryTimeout):
        with deadline(.01):
            with deadline(1):
                try:time.sleep(.05)
                except QueryTimeout:pass
    assert signal.getitimer(signal.ITIMER_REAL)==(0.0,0.0)

def test_live_external_timer_is_restored():
    original=signal.getsignal(signal.SIGALRM)
    def handler(*args):raise AssertionError('External timer fired early')
    signal.signal(signal.SIGALRM,handler)
    signal.setitimer(signal.ITIMER_REAL,.5,.5)
    try:
        with deadline(.1):time.sleep(.01)
        remaining,interval=signal.getitimer(signal.ITIMER_REAL)
        assert .3<remaining<.5 and interval==.5
        assert signal.getsignal(signal.SIGALRM)==handler
    finally:
        signal.setitimer(signal.ITIMER_REAL,0);signal.signal(signal.SIGALRM,original)

def test_search_timeout_resume_and_query_bound_cursor(release):
    with open_reader(release,'sqlite') as r:
        q=Query(r);original=r.read
        def slow(*args):time.sleep(.03);return original(*args)
        r.read=slow
        page=q.search('warning',time_budget_seconds=.01)
        assert page['stop_reason']=='time_budget' and not page['hits'] and page['next_section_offset']==0
        r.read=original
        a=q.search('warning',limit=1,cursor=page['continuation'])
        b=q.search('warning',limit=1,cursor=a['continuation'])
        assert [a['hits'][0]['ordinal'],b['hits'][0]['ordinal']]==[1,2] and not b['partial']
        with pytest.raises(ValueError,match='cursor'):q.search('different',cursor=a['continuation'])
        r.snapshot='changed'
        with pytest.raises(ValueError,match='cursor'):q.search('warning',cursor=a['continuation'])

def test_projection_literal_parity_and_tampering(release,tmp_path):
    target=tmp_path/'projection';pin=build(release,target)
    cfg=json.loads(release.read_text());cfg.update(search_index=str(target/'search.sqlite'),search_index_sha256=pin['sha256'])
    accelerated=release.with_name('accelerated.json');accelerated.write_text(json.dumps(cfg))
    for backend in ('sqlite','git'):
        with open_reader(release,backend) as raw,open_reader(accelerated,backend) as fast:
            for phrase in ('warning','RENAL','changed','a',' ', 'warning changed','"', '%', '不存在'):
                assert Query(raw).search(phrase)==Query(fast).search(phrase)
    with (target/'search.sqlite').open('ab') as f:f.write(b'bad')
    with pytest.raises(ValueError,match='hash'):open_reader(accelerated,'sqlite')

def test_no_false_empty_answer_after_timeout(release):
    with open_reader(release,'sqlite') as r:
        q=Query(r);original=r.read
        def slow(*a):time.sleep(.2);return original(*a)
        r.read=slow
        with pytest.raises(QueryTimeout):q.show(SID,1,timeout_seconds=.02)
        r.read=original
        assert q.show(SID,1)['version']==1

def test_mcp_numeric_bounds_and_timeout_recovery(release):
    from gitrx_backend.mcp import Server
    with open_reader(release,'sqlite') as r:
        s=Server(r);s.initialized=s.ready=True
        def call(args):return s.dispatch({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'search','arguments':args}})
        for bad in (True,float('nan'),float('inf'),0,61,10**400):
            assert call({'phrase':'renal','timeout_seconds':bad})['error']['code']==-32602
        answer=call({'phrase':'renal','time_budget_seconds':.2,'timeout_seconds':1.5})
        assert not answer['result']['isError']
        original=r.read
        def slow(*a):time.sleep(.1);return original(*a)
        r.read=slow
        assert call({'phrase':'renal','timeout_seconds':.01})['result']['isError']
        r.read=original
        assert not call({'phrase':'renal'})['result']['isError']

def test_git_deadline_kills_process_group(tmp_path):
    import subprocess,os
    from gitrx_backend.readers import GitReader
    subprocess.run(['git','init','-q',str(tmp_path)],check=True)
    start=time.monotonic()
    with pytest.raises(QueryTimeout):
        with deadline(.05):GitReader.git(tmp_path,'-c','alias.pause=!sleep 5','pause')
    assert time.monotonic()-start<1
