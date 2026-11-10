"""Bounded download threads, with ledger writes/pacing owned by the coordinator.

Only raw local file/network I/O runs in threads. SQLite, guard callbacks,
adaptive backoff and evidence admission remain on the calling thread.
"""
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
import http.client
import json
import os
import threading
import time
import urllib.request
from urllib.parse import urlsplit
from .jobs import sha,sync_body
from gitrx_services.io import ensure_space
from gitrx_services.release1_connection import SingleConnection


class DownloadPool:
    def __init__(self,http):
        self.http=http
        capacity=http.pacing.policy.get('ramp_max_lanes',http.pacing.policy.get('download_lanes',1))
        self.executor=ThreadPoolExecutor(max_workers=capacity,thread_name_prefix='gitrx-download')
        self.local=threading.local();self.connections=[];self.lock=threading.Lock()
        self.start_lock=threading.Lock();self.last_wire_start=0
        self.metrics={'guard_seconds':0.,'retain_seconds':0.,'callback_seconds':0.,'requests_started':0,'pending_peak':0}

    def timed(self,name,fn,*args):
        started=time.monotonic()
        try:return fn(*args)
        finally:self.metrics[name]+=time.monotonic()-started

    @property
    def lanes(self):
        return self.http.pacing.state.get('active_lanes',self.http.pacing.policy.get('download_lanes',1))

    def transfer(self,url,ident):
        h=self.http
        if not hasattr(self.local,'connection'):
            self.local.connection=SingleConnection()
            with self.lock:self.connections.append(self.local.connection)
        connection=self.local.connection
        part=h.root/'partial'/str(ident);status=0;headers={};failure=None;fatal=None
        started=time.monotonic();total=0
        try:
            request=urllib.request.Request(url,headers={'User-Agent':f"GitRx/0.10 local DailyMed research archive; {self.lanes} lanes, <={1/h.pacing.policy['minimum_spacing_seconds']:g} starts/second total",'Accept-Encoding':'identity'})
            with self.start_lock:
                time.sleep(max(0,self.last_wire_start+h.pacing.spacing-time.monotonic()))
                self.last_wire_start=time.monotonic()
            started=time.monotonic()
            with connection.open(request,timeout=30) as response,part.open('xb') as f:
                status=response.code;headers={k.lower():v for k,v in response.headers.items()}
                while chunk:=response.read(1024**2):
                    if time.monotonic()-started>h.max_response_seconds:raise TimeoutError('Response execution deadline exceeded')
                    ensure_space(h.root,2*1024**2)
                    with self.lock:
                        if h.max_bytes is not None and h.bytes_received+len(chunk)>h.max_bytes:
                            raise RuntimeError('Workspace byte cap reached; response prefix retained')
                        h.bytes_received+=len(chunk)
                    f.write(chunk);total+=len(chunk)
                    if total>100*1024**2:raise RuntimeError('Response exceeds 100 MiB; retained for review')
                sync_body(f)
        except (OSError,http.client.HTTPException) as exc:failure=str(exc);connection.close()
        except BaseException as exc:failure=str(exc);fatal=exc;connection.close()
        if not part.exists():part.write_bytes(b'')
        with part.open('rb') as f:os.fsync(f.fileno())
        return {'id':ident,'url':url,'part':part,'status':status,'headers':headers,'failure':failure,
            'fatal':fatal,'latency':time.monotonic()-started,'connection_serial':connection.serial}

    def retain(self,value,spacing):
        h=self.http;part=value['part'];digest=sha(part);target=h.root/'bodies'/digest
        if target.exists():
            if sha(target)!=digest:raise RuntimeError('Existing HTTP content hash changed')
        else:os.link(part,target)
        fd=os.open(target.parent,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
        if h.raw_journal:h.raw_journal.store(target,digest,external_path=target)
        headers=value['headers']
        if headers.get('retry-after'):
            from gitrx.current.archive import retry_delay
            until=time.time()+retry_delay(list(headers.items()),0)
            old=h.c.execute("SELECT value FROM transport_state WHERE key='retry_after'").fetchone()
            with h.c:h.c.execute("INSERT OR REPLACE INTO transport_state VALUES('retry_after',?)",(max(until,old[0] if old else 0),))
        with h.c:h.c.execute('UPDATE requests SET status=?,sha256=?,error=?,headers_json=?,bytes=?,latency_seconds=?,spacing_seconds=?,connection_serial=? WHERE id=?',
            (value['status'],digest,value['failure'],json.dumps(headers),part.stat().st_size,value['latency'],spacing,value['connection_serial'],value['id']))
        h.pacing.note(value['status'],value['failure'],value['latency'],headers)
        if not h.retain_completed_partials and not value['failure']:part.unlink()
        return target

    def pair(self,urls,interrupt=None,on_result=None,reuse=True):
        h=self.http;pending={};results={};error=None;index=0
        def finish(future):
            nonlocal error
            began=time.monotonic()
            position,spacing=pending.pop(future);value=future.result()
            path=self.timed('retain_seconds',self.retain,value,spacing);results[position]=(value,path)
            if on_result and value['status'] in (200,404,410) and not value['failure']:
                row=h.c.execute('SELECT * FROM requests WHERE id=?',(value['id'],)).fetchone()
                self.timed('callback_seconds',on_result,value['url'],row,path)
            if value['fatal'] is not None:error=error or value['fatal']
            self.completion_seconds=max(.001,time.monotonic()-began)
        try:
            while pending or index<len(urls):
                self.timed('guard_seconds',h.guard)
                if interrupt is not None and interrupt():urls=urls[:index]
                ready=[f for f in pending if f.done()]
                pressure=[f for f in ready if f.result()['status'] not in (200,404,410)
                          or f.result()['failure'] or f.result()['fatal'] is not None]
                # Always process server pressure before another request start.
                if pressure:finish(pressure[0]);continue
                if error and not pending:raise error
                if index<len(urls) and len(pending)<self.lanes and not error:
                    deferred=h.c.execute("SELECT value FROM transport_state WHERE key='retry_after'").fetchone()
                    if deferred and deferred[0]>time.time():
                        if h.pacing.policy.get('wait_for_retry_after',False):
                            if ready:
                                finish(ready[0]);continue
                            # Keep the coordinator and cached consumer alive.
                            # Revisit commands/guards and retain in-flight bodies
                            # while withholding all new starts until the deadline.
                            if pending:wait(tuple(pending),timeout=.1,return_when=FIRST_COMPLETED)
                            else:time.sleep(min(.1,max(0,deferred[0]-time.time())))
                        else:error=RuntimeError('Retry deferred by upstream Retry-After; retained in ledger')
                        continue
                    if h.request_count>=h.cap:
                        error=RuntimeError('Workspace request cap reached');continue
                    if h.max_bytes is not None and h.bytes_received>=h.max_bytes:
                        error=RuntimeError('Workspace byte cap reached');continue
                    last=h.c.execute('SELECT MAX(started) FROM requests').fetchone()[0] or 0
                    spacing=h.pacing.spacing
                    if time.time()>=last+spacing:
                        url=urls[index];u=urlsplit(url)
                        if u.scheme!='https' or not (u.hostname=='nlm.nih.gov' or (u.hostname or '').endswith('.nlm.nih.gov')):
                            error=ValueError('Official NLM HTTPS URLs only');continue
                        ensure_space(h.root,100*1024**2)
                        with h.c:ident=h.c.execute('INSERT INTO requests(url,started) VALUES(?,?)',(url,time.time())).lastrowid
                        h.request_count+=1
                        pending[self.executor.submit(self.transfer,url,ident)]=(index,spacing);index+=1
                        self.metrics['requests_started']+=1;self.metrics['pending_peak']=max(self.metrics['pending_peak'],len(pending))
                        continue
                # Use the space between starts for completed responses, but do
                # not begin a measured completion that would miss the next slot.
                budget=getattr(self,'completion_seconds',.005)
                can_finish=(index>=len(urls) or len(pending)>=self.lanes or error
                            or last+spacing-time.time()>budget)
                if ready and can_finish:
                    finish(ready[0]);continue
                if pending:
                    delay=.02
                    if index<len(urls) and len(pending)<self.lanes and not error:
                        delay=min(delay,max(0,last+spacing-time.time()))
                    waiting=[f for f in pending if not f.done()]
                    if waiting:wait(tuple(waiting),timeout=delay,return_when=FIRST_COMPLETED)
                    else:time.sleep(delay)
                elif index<len(urls):
                    if error:raise error
                    time.sleep(max(0,last+spacing-time.time()))
            if error:raise error
        except BaseException:
            # Every started request is retained before propagating a failure.
            for future,(position,spacing) in list(pending.items()):
                while not future.done():
                    h.guard();wait((future,),timeout=.1)
                self.retain(future.result(),spacing)
            raise
        output=[]
        for i,url in enumerate(urls):
            value,path=results[i]
            if value['status'] not in (200,404,410) or value['failure']:
                # Redirects and temporary failures retain the shared normal
                # transport's bounded retries and official-host checks.
                before=h.c.execute('SELECT COALESCE(MAX(id),0) FROM requests').fetchone()[0]
                path=h.get(url,reuse=reuse)
                row=h.c.execute('SELECT * FROM requests WHERE id>? AND sha256=? AND status=200 AND error IS NULL ORDER BY id DESC LIMIT 1',(before,sha(path))).fetchone()
                if row is None:row=h.c.execute('SELECT * FROM requests WHERE url=? AND sha256=? AND status=200 AND error IS NULL ORDER BY id DESC LIMIT 1',(url,sha(path))).fetchone()
                if on_result:on_result(url,row,path)
            else:row=h.c.execute('SELECT * FROM requests WHERE id=?',(value['id'],)).fetchone()
            output.append((url,row,path))
        return output

    def close(self):
        self.executor.shutdown(wait=True,cancel_futures=True)
        for connection in self.connections:connection.close()


def get_many(http,urls,interrupt=None,on_result=None,reuse=True):
    urls=list(urls)
    if not http.pacing or not 2<=http.pacing.policy.get('download_lanes',1)<=60:
        raise ValueError('Parallel download lanes require an explicit transport policy')
    if not 1<=len(urls)<=200:raise ValueError('Download scheduling window exceeds 200 items')
    cached={};missing=[]
    for position,url in enumerate(urls):
        if not reuse:
            missing.append((position,url));continue
        row=http.c.execute('SELECT * FROM requests WHERE url=? AND status=200 AND error IS NULL ORDER BY id DESC LIMIT 1',(url,)).fetchone()
        if row is None:missing.append((position,url));continue
        path=http.body_path(row['sha256'])
        if sha(path)!=row['sha256']:raise RuntimeError('Cached response hash mismatch')
        cached[position]=(url,row,path)
        if on_result:on_result(url,row,path)
    if not missing:return [cached[i] for i in range(len(urls))]
    if not getattr(http,'download_pool',None):http.download_pool=DownloadPool(http)
    for (position,_),result in zip(missing,http.download_pool.pair([url for _,url in missing],interrupt=interrupt,on_result=on_result,reuse=reuse)):cached[position]=result
    return [cached[i] for i in sorted(cached)]
