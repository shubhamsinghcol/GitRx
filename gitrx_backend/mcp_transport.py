"""Bounded responsive stdio control with one main-thread query process (POSIX)."""
from collections import deque
import json
import multiprocessing
import os
from pathlib import Path
import queue
import select
import signal
import subprocess
import tempfile
import threading
import time

MAX_PENDING = 32
MAX_MESSAGE = 1024 * 1024
SLOW = {'tools/call', 'resources/read'}


class QueryCancelled(BaseException):
    """Unwinds query deadlines and Git child cleanup without an evidence answer."""


def freeze_config(path, directory):
    path = Path(path).resolve()
    cfg = json.loads(path.read_bytes())
    for key in ('release_root','export_root','scope_path','search_index','evidence_metadata','rebuild_root','names_index','retrieval_routes'):
        # ${GITRX_DATA_ROOT} values stay as written; open_reader expands them (resolving them
        # here would glue the placeholder onto the config directory).
        if key in cfg and '${GITRX_DATA_ROOT}' not in str(cfg[key]):
            cfg[key] = str((path.parent / cfg[key]).resolve())
    target = Path(directory) / 'backend.json'
    target.write_text(json.dumps(cfg))
    return str(target)


def query_worker(conn, config, backend, cancelled):
    from .config import open_reader
    from .mcp import Server, encode_response
    active = False
    def cancel(*_):
        if active: raise QueryCancelled()
    signal.signal(signal.SIGUSR1, cancel)
    original_popen=subprocess.Popen
    class TrackedPopen(original_popen):
        def __init__(self,*args,**kwargs):
            # Register Git's isolated process group before cancellation can escape
            # construction; cleanup here covers interruption before caller assignment.
            old_mask=signal.pthread_sigmask(signal.SIG_BLOCK,{signal.SIGUSR1})
            try:
                super().__init__(*args,**kwargs)
                self._tracked_group=bool(kwargs.get('start_new_session'))
                if self._tracked_group:conn.send(('child_started',self.pid))
                signal.pthread_sigmask(signal.SIG_SETMASK,old_mask)
            except BaseException:
                if getattr(self,'pid',None) and getattr(self,'_tracked_group',False):
                    try:os.killpg(self.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    self.wait()
                signal.pthread_sigmask(signal.SIG_SETMASK,old_mask)
                raise
        def wait(self,*args,**kwargs):
            result=super().wait(*args,**kwargs)
            if getattr(self,'_tracked_group',False):
                conn.send(('child_finished',self.pid));self._tracked_group=False
            return result
    subprocess.Popen=TrackedPopen
    try:
        with open_reader(config, backend) as reader:
            server = Server(reader); server.initialized = server.ready = True
            conn.send(('ready', None))
            while True:
                try: serial, request = conn.recv()
                except EOFError: return
                active = True
                try:
                    if cancelled.is_set(): raise QueryCancelled()
                    response = server.dispatch(request)
                except QueryCancelled:
                    response = None
                except Exception:
                    response = {'jsonrpc':'2.0','id':request.get('id'),'error':{'code':-32603,'message':'Query worker failed; no evidence answer'}}
                finally: active = False
                conn.send(('result', (serial, encode_response(response) if response is not None else None)))
    except (EOFError, BrokenPipeError):
        pass
    except Exception:
        try: conn.send(('startup_error', None))
        except (OSError, EOFError): pass
    finally: conn.close()


def _read(stream, incoming):
    try:
        while True:
            raw = stream.readline(MAX_MESSAGE + 1)
            if not raw: break
            if len(raw) > MAX_MESSAGE:
                while raw and not raw.endswith(b'\n'): raw = stream.readline(MAX_MESSAGE + 1)
                incoming.put(('bad', None)); continue
            if raw.strip(): incoming.put(('line', raw))
    finally: incoming.put(('eof', None))


def _write(fd, outgoing, gone, progress):
    while not gone.is_set():
        try: raw = outgoing.get(timeout=.1)
        except queue.Empty: continue
        if raw is None: return
        progress[0] = time.monotonic()
        try:
            view = memoryview((raw + '\n').encode('ascii'))
            while view and not gone.is_set():
                try:
                    count = os.write(fd, view)
                    view = view[count:]
                except BlockingIOError:
                    select.select([], [fd], [], .1)
        except OSError:
            gone.set(); return
        finally:
            progress[0] = 0
            outgoing.task_done()


def _stop(process, conn, force=False, child_groups=()):
    conn.close()
    def remove_groups():
        for pgid in child_groups:
            try:os.killpg(pgid,signal.SIGKILL)
            except ProcessLookupError:pass
    if not process.is_alive():
        remove_groups(); process.join(); return
    try: os.kill(process.pid, signal.SIGUSR1)
    except ProcessLookupError: pass
    if not force: process.join(.5)
    if process.is_alive():
        remove_groups()
        # Git readers run child commands in their own process groups. A forced
        # worker shutdown must also remove those groups if cooperative unwind
        # was delayed by native code. Only direct children of this worker qualify.
        try:
            children = subprocess.check_output(['ps','-o','pid=','-o','pgid=','-P',str(process.pid)], text=True)
            for line in children.splitlines():
                pid, pgid = map(int, line.split())
                if pid == pgid:
                    try: os.killpg(pgid, signal.SIGKILL)
                    except ProcessLookupError: pass
        except (OSError, subprocess.SubprocessError, ValueError): pass
        process.terminate(); process.join(.1)
        if process.is_alive(): process.kill(); process.join()


def serve(server, config, backend, stream, output_fd, worker_target=query_worker):
    """Serve controls here; run tools/resources serially in an isolated reader.

    worker_target is an internal test seam, never a wire argument or CLI option.
    """
    from .mcp import validate_message, encode_response
    if not hasattr(signal, 'SIGUSR1'): raise RuntimeError('Native MCP cancellation requires POSIX signals')
    output_was_blocking=os.get_blocking(output_fd)
    os.set_blocking(output_fd,False)
    incoming = queue.Queue(MAX_PENDING); outgoing = queue.Queue(MAX_PENDING)
    gone = threading.Event(); progress = [0.0]
    threading.Thread(target=_read,args=(stream,incoming),daemon=True).start()
    writer=threading.Thread(target=_write,args=(output_fd,outgoing,gone,progress),daemon=True)
    writer.start()
    pending = deque(); active = None; ids = set(); serial = 0; eof = False
    stopping=False; failures=0; child_groups=set()
    def emit(response):
        if response is None: return
        encoded = response if isinstance(response,str) else encode_response(response)
        try: outgoing.put_nowait(encoded)
        except queue.Full: gone.set()
    def error(ident, code, message):
        emit({'jsonrpc':'2.0','id':ident,'error':{'code':code,'message':message}})
    def spawn():
        parent, child = multiprocessing.get_context('spawn').Pipe()
        cancelled = multiprocessing.get_context('spawn').Event()
        process = multiprocessing.get_context('spawn').Process(target=worker_target,args=(child,config,backend,cancelled),daemon=True)
        process.start(); child.close()
        return process, parent, cancelled, False, time.monotonic()
    process, conn, cancelled, ready, started = spawn()
    try:
        while not gone.is_set():
            dead=False
            if not stopping and conn.poll():
                try: kind, data = conn.recv()
                except EOFError: kind, data = 'dead', None
                if kind == 'dead': dead=True
                if kind == 'child_started':child_groups.add(data)
                elif kind == 'child_finished':child_groups.discard(data)
                elif kind == 'ready': ready = True
                elif kind == 'result':
                    finished, response = data
                    if active is not None and finished == active[0]:
                        was_cancelled=active[2]
                        if not was_cancelled:emit(response)
                        ids.discard(active[1]['id']);active=None
                        if was_cancelled:
                            _stop(process,conn,child_groups=child_groups);child_groups.clear()
                            process,conn,cancelled,ready,started=spawn()
                        else:failures=0
                elif kind == 'startup_error':
                    while pending:
                        _,request=pending.popleft();error(request['id'],-32000,'Pinned query reader could not start; no evidence answer')
                    ids.clear();eof=True;stopping=True
            if not stopping and (dead or (not process.is_alive() and not conn.poll())):
                if active is not None:
                    if not active[2]:error(active[1]['id'],-32000,'Query worker exited; no evidence answer')
                    ids.discard(active[1]['id']);active=None
                _stop(process,conn,child_groups=child_groups);child_groups.clear()
                failures+=1
                if eof and not pending:break
                if failures>=3:
                    while pending:
                        _,request=pending.popleft();error(request['id'],-32000,'Query worker repeatedly exited; no evidence answer')
                    ids.clear();eof=True;stopping=True
                else:process,conn,cancelled,ready,started=spawn()
            if not stopping and active is not None and active[2] and time.monotonic()-active[3]>1:
                ids.discard(active[1]['id']);active=None
                _stop(process,conn,force=True,child_groups=child_groups);child_groups.clear()
                process,conn,cancelled,ready,started=spawn()
            if not stopping and not ready and time.monotonic()-started > 60:
                while pending:
                    _,request=pending.popleft();error(request['id'],-32000,'Pinned query reader startup timed out; no evidence answer')
                eof=True;stopping=True;ids.clear()
            if not stopping and ready and active is None and pending:
                item, request = pending.popleft(); active = [item, request, False, None]
                cancelled.clear()
                conn.send((item,request))
            if eof and progress[0] and time.monotonic()-progress[0]>10:
                gone.set();break
            if eof and not pending and active is None:
                if outgoing.unfinished_tasks == 0: break
                if progress[0] and time.monotonic()-progress[0] > 10: break
            try: kind, raw = incoming.get(timeout=.01)
            except queue.Empty: continue
            if kind == 'eof': eof = True; continue
            if kind == 'bad': error(None,-32700,'Invalid JSON or oversized request'); continue
            try:
                request = json.loads(raw); validate_message(request)
            except (ValueError, UnicodeError, RecursionError):
                error(None,-32700,'Invalid JSON, Unicode or excessive nesting'); continue
            if isinstance(request,dict) and request.get('method') == 'notifications/cancelled' and 'id' not in request:
                params = request.get('params',{})
                ident = params.get('requestId') if isinstance(params,dict) else None
                if type(ident) not in (int,str): continue
                if active is not None and active[1]['id'] == ident:
                    active[2] = True
                    if active[3] is None:active[3]=time.monotonic()
                    cancelled.set()
                    try: os.kill(process.pid,signal.SIGUSR1)
                    except ProcessLookupError: pass
                else:
                    pending = deque((n,r) for n,r in pending if r['id'] != ident)
                    ids.discard(ident)
                continue
            method = request.get('method') if isinstance(request,dict) else None
            ident = request.get('id') if isinstance(request,dict) else None
            if type(ident) in (int,str) and ident in ids:
                error(ident,-32600,'Request id is already pending'); continue
            if method in SLOW and server.ready and type(ident) in (int,str) and not (isinstance(ident,str) and len(ident)>256) and request.get('jsonrpc')=='2.0':
                if ident in ids: error(ident,-32600,'Request id is already pending'); continue
                if len(pending) >= MAX_PENDING: error(ident,-32000,'Query queue is full; retry later'); continue
                serial += 1; ids.add(ident); pending.append((serial,request)); continue
            emit(server.dispatch(request))
    finally:
        _stop(process,conn,child_groups=child_groups);child_groups.clear()
        # Drain valid final errors/results for a bounded interval, including
        # startup failures; a broken pipe has already marked the writer gone.
        until=time.monotonic()+10
        while not gone.is_set() and outgoing.unfinished_tasks and writer.is_alive() and time.monotonic()<until:
            time.sleep(.01)
        try: outgoing.put_nowait(None)
        except queue.Full: pass
        writer.join(1)
        gone.set()
        writer.join(1)
        os.set_blocking(output_fd,output_was_blocking)
