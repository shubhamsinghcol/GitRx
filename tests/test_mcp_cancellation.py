"""Real responsive transport with private worker hooks, no public delay controls."""
import json
import multiprocessing
import os
from pathlib import Path
import select
import signal
import time

from test_backend_portable import release, SID


def slow_worker(conn, config, backend, cancelled):
    from gitrx_backend.query import Query
    from gitrx_backend.mcp_transport import query_worker
    original = Query.families
    def families(self,search='',**kwargs):
        if search in ('slow','ignore-cancel','git-slow'):
            (Path(config).parent/'started').write_text(str(os.getpid()))
            if search=='ignore-cancel':
                signal.signal(signal.SIGUSR1,signal.SIG_IGN)
                time.sleep(30)
            elif search=='git-slow':
                import subprocess
                from gitrx_backend.readers import GitReader
                original_popen=subprocess.Popen
                class Record(original_popen):
                    def __init__(self,*args,**kwargs):
                        super().__init__(*args,**kwargs)
                        if kwargs.get('start_new_session'):(Path(config).parent/'child').write_text(str(self.pid))
                subprocess.Popen=Record
                cfg=json.loads(Path(config).read_text())
                GitReader.git(Path(cfg['export_root'])/'shards/a','-c','alias.pause=!sleep 30','pause')
            else:time.sleep(3)
        return original(self,search=search,**kwargs)
    Query.families = families
    query_worker(conn,config,backend,cancelled)


def harness(config, directory, input_handle, output_handle):
    input_fd=input_handle.detach();output_fd=output_handle.detach()
    from gitrx_backend.config import open_reader
    from gitrx_backend.mcp import Server
    from gitrx_backend.mcp_transport import freeze_config, serve
    with open_reader(config,'sqlite') as reader:
        frozen=freeze_config(config,directory)
        (Path(directory)/'frozen').write_text(frozen)
        with os.fdopen(input_fd,'rb',buffering=0) as source:
            serve(Server(reader),frozen,'sqlite',source,output_fd,slow_worker)


class Client:
    def __init__(self,config,directory):
        # Transfer descriptors explicitly; use spawn even in the test harness.
        read_in,self.write_in=os.pipe();self.read_out,write_out=os.pipe()
        from multiprocessing.reduction import DupFd
        self.process=multiprocessing.get_context('spawn').Process(target=harness,args=(str(config),str(directory),DupFd(read_in),DupFd(write_out)))
        self.process.start();os.close(read_in);os.close(write_out)
        self.buffer=b''
    def send(self,method,ident=None,params=None):
        msg={'jsonrpc':'2.0','method':method}
        if ident is not None:msg['id']=ident
        if params is not None:msg['params']=params
        os.write(self.write_in,(json.dumps(msg)+'\n').encode())
    def receive(self,timeout=8):
        until=time.monotonic()+timeout
        while b'\n' not in self.buffer:
            ready,_,_=select.select([self.read_out],[],[],max(0,until-time.monotonic()))
            assert ready,'MCP response timed out'
            raw=os.read(self.read_out,65536);assert raw,'MCP output closed unexpectedly'
            self.buffer+=raw
        line,self.buffer=self.buffer.split(b'\n',1)
        return json.loads(line)
    def ready(self):
        self.send('initialize',1,{'protocolVersion':'2025-11-25'})
        assert self.receive()['id']==1
        self.send('notifications/initialized')
    def close(self):
        os.close(self.write_in)
        self.process.join(8)
        if self.process.is_alive():self.process.terminate();self.process.join(3)
        os.close(self.read_out)


def wait_started(directory):
    until=time.monotonic()+8
    while not (directory/'started').exists():
        assert time.monotonic()<until,'Slow worker did not start'
        time.sleep(.01)


def test_ping_overtakes_active_query_and_active_cancel_recovers(release,tmp_path):
    c=Client(release,tmp_path)
    try:
        c.ready()
        c.send('tools/call',2,{'name':'families','arguments':{'search':'slow'}})
        wait_started(tmp_path)
        began=time.monotonic();c.send('ping',3)
        assert c.receive(.7)['id']==3
        assert time.monotonic()-began<.7
        c.send('notifications/cancelled',params={'requestId':2})
        c.send('tools/call',4,{'name':'show','arguments':{'sid':SID,'version':1,'ordinal':1}})
        answer=c.receive(3)
        assert answer['id']==4 and not answer['result']['isError']
        assert answer['result']['structuredContent']['version']==1
    finally:c.close()
    assert c.process.exitcode==0


def test_cancel_queued_and_worker_restart_keeps_frozen_configuration(release,tmp_path):
    c=Client(release,tmp_path)
    original=release.read_bytes()
    try:
        c.ready();c.send('tools/call',2,{'name':'families','arguments':{'search':'slow'}})
        wait_started(tmp_path)
        c.send('tools/call',3,{'name':'versions','arguments':{'sid':SID}})
        c.send('notifications/cancelled',params={'requestId':3})
        # Mutating the caller's pointer cannot change worker restart selection.
        release.write_text('{}')
        os.kill(int((tmp_path/'started').read_text()),signal.SIGKILL)
        failure=c.receive(3)
        assert failure['id']==2 and failure['error']
        c.send('tools/call',4,{'name':'versions','arguments':{'sid':SID}})
        answer=c.receive(8)
        assert answer['id']==4 and len(answer['result']['structuredContent']['versions'])==2
        c.send('ping',5);assert c.receive()['id']==5
    finally:
        release.write_bytes(original);c.close()
    assert c.process.exitcode==0


def test_eof_drains_non_cancelled_queries(release,tmp_path):
    c=Client(release,tmp_path)
    try:
        c.ready()
        for ident in (2,3):c.send('tools/call',ident,{'name':'versions','arguments':{'sid':SID}})
        os.close(c.write_in);c.write_in=None
        assert [c.receive()['id'],c.receive()['id']]==[2,3]
        c.process.join(5);assert c.process.exitcode==0
    finally:
        if c.write_in is None:
            os.close(c.read_out)
            if c.process.is_alive():c.process.terminate();c.process.join()
        else:c.close()


def test_forced_cancellation_restarts_ignored_signal_worker(release,tmp_path):
    c=Client(release,tmp_path)
    try:
        c.ready();c.send('tools/call',2,{'name':'families','arguments':{'search':'ignore-cancel'}})
        wait_started(tmp_path);old=int((tmp_path/'started').read_text())
        c.send('notifications/cancelled',params={'requestId':2})
        c.send('tools/call',3,{'name':'show','arguments':{'sid':SID,'version':1,'ordinal':1}})
        assert c.receive(6)['id']==3
        try:os.kill(old,0)
        except ProcessLookupError:pass
        else:raise AssertionError('Cancelled worker remained alive')
    finally:c.close()
    assert c.process.exitcode==0


def test_cancellation_cleans_actual_git_process_group(release,tmp_path):
    c=Client(release,tmp_path)
    try:
        c.ready();c.send('tools/call',2,{'name':'families','arguments':{'search':'git-slow'}})
        wait_started(tmp_path)
        until=time.monotonic()+5
        while not (tmp_path/'child').exists():
            assert time.monotonic()<until
            time.sleep(.01)
        child=int((tmp_path/'child').read_text())
        c.send('notifications/cancelled',params={'requestId':2})
        c.send('tools/call',3,{'name':'show','arguments':{'sid':SID,'version':1,'ordinal':1}})
        assert c.receive(6)['id']==3
        try:os.killpg(child,0)
        except ProcessLookupError:pass
        else:raise AssertionError('Git child group remained alive')
    finally:c.close()
    assert c.process.exitcode==0


def test_freeze_config_keeps_data_root_placeholder(tmp_path):
    import json
    from gitrx_backend.mcp_transport import freeze_config
    src = tmp_path / 'cfg.json'
    src.write_text(json.dumps({'rebuild_root': '${GITRX_DATA_ROOT}/rebuild/x', 'names_index': 'rel/names.sqlite'}))
    out = json.loads((tmp_path / 'frozen').mkdir() or open(freeze_config(src, tmp_path / 'frozen')).read())
    assert out['rebuild_root'] == '${GITRX_DATA_ROOT}/rebuild/x'
    assert out['names_index'] == str((tmp_path / 'rel/names.sqlite').resolve())
