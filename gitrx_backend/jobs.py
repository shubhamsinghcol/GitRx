"""Shared write-once, ledgered HTTP client and small durability helpers.

Used by the recurring DailyMed update, the SrLC update and gap repair
(gitrx_core). The former release-cycle job machinery that lived here was removed
in the 2026-11-11 cleanup; it remains recoverable at git tag codex-final
(see docs/CLEANUP-2026-11-11.md).
"""
from pathlib import Path
import fcntl
import hashlib
import http.client
import json
import os
import sqlite3
import subprocess
import time
import urllib.request
import urllib.error
from urllib.parse import urlsplit

from gitrx.current.db import dumps
from gitrx_services.io import ensure_space

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
def sync_body(stream):
    """Flush raw bytes, including the device-cache barrier on macOS if supported."""
    stream.flush();os.fsync(stream.fileno())
    import sys,errno
    if sys.platform=='darwin':
        try:fcntl.fcntl(stream.fileno(),getattr(fcntl,'F_FULLFSYNC',51));return True
        except OSError as exc:
            if exc.errno not in (errno.EINVAL,errno.ENOTSUP,errno.ENOTTY):raise
    return False
def atomic(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_name(path.name+'.tmp')
    with tmp.open('w') as f:f.write(dumps(value)+'\n');f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)
    directory=os.open(path.parent,os.O_RDONLY)
    try:os.fsync(directory)
    finally:os.close(directory)
def git(repo,*args):return subprocess.check_output(['git','--no-replace-objects','-C',str(repo),*args],text=True).strip()

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

class HTTP:
    """One shared workspace ledger; failed bodies and interrupted files retained."""
    def __init__(self,root,cap=1000,spacing=2,guard=None,max_bytes=None,max_response_seconds=120,retain_completed_partials=True,transport_policy=None,ledger_path=None,raw_journal=None,body_fallback=None,threaded_coordinator=False):
        if not 1<=cap<=1000000 or spacing<2:raise ValueError('Request cap must be 1..1000000; baseline spacing at least 2 seconds')
        self.root=Path(root);self.cap=cap;self.spacing=spacing
        self.guard=guard or (lambda: None);self.guard()
        if max_bytes is not None and (type(max_bytes) is not int or max_bytes<1):raise ValueError('Positive byte budget required')
        self.max_bytes=max_bytes
        if max_response_seconds<=0:raise ValueError('Positive response deadline required')
        self.max_response_seconds=max_response_seconds
        self.retain_completed_partials=retain_completed_partials
        self.raw_journal=raw_journal
        self.body_fallbacks=[Path(p) for p in (body_fallback if isinstance(body_fallback,(list,tuple)) else [body_fallback])] if body_fallback else []
        (self.root/'bodies').mkdir(parents=True,exist_ok=True);(self.root/'partial').mkdir(exist_ok=True)
        self.c=sqlite3.connect(Path(ledger_path) if ledger_path is not None else self.root/'ledger.sqlite',check_same_thread=not threaded_coordinator);self.c.row_factory=sqlite3.Row
        self.c.executescript('PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA fullfsync=ON; PRAGMA checkpoint_fullfsync=ON;')
        self.c.execute('CREATE TABLE IF NOT EXISTS requests(id INTEGER PRIMARY KEY,url,started REAL,status,sha256,error,headers_json)');self.c.commit()
        if 'headers_json' not in {r[1] for r in self.c.execute('PRAGMA table_info(requests)')}:
            self.c.execute('ALTER TABLE requests ADD COLUMN headers_json');self.c.commit()
        if 'bytes' not in {r[1] for r in self.c.execute('PRAGMA table_info(requests)')}:
            self.c.execute('ALTER TABLE requests ADD COLUMN bytes INTEGER DEFAULT 0');self.c.commit()
        self.c.execute('CREATE TABLE IF NOT EXISTS transport_state(key TEXT PRIMARY KEY,value REAL)')
        self.c.execute('CREATE INDEX IF NOT EXISTS requests_url_id ON requests(url,id DESC)')
        self.c.execute('CREATE INDEX IF NOT EXISTS requests_started ON requests(started)')
        columns={r[1] for r in self.c.execute('PRAGMA table_info(requests)')}
        for name in ('latency_seconds','spacing_seconds','connection_serial'):
            if name not in columns:self.c.execute('ALTER TABLE requests ADD COLUMN '+name+' REAL')
        self.c.commit()
        self.pacing=None;self.opener=None
        if transport_policy is not None:
            from .transport_policy import AdaptivePacing
            from gitrx_services.release1_connection import SingleConnection
            self.pacing=AdaptivePacing(self.root,transport_policy,ledger_path);self.opener=SingleConnection()
        # A request start is durable before its body. A killed process must
        # never turn a retained response prefix into a successful cached body.
        for row in self.c.execute('SELECT id FROM requests WHERE status IS NULL').fetchall():
            part=self.root/'partial'/str(row['id']);digest=None
            if part.exists():
                with part.open('rb') as f:os.fsync(f.fileno())
                digest=sha(part);target=self.root/'bodies'/digest
                if not target.exists():os.link(part,target)
                directory=os.open(target.parent,os.O_RDONLY)
                try:os.fsync(directory)
                finally:os.close(directory)
            with self.c:self.c.execute("UPDATE requests SET status=0,error='Interrupted transfer retained; not reusable',sha256=?,bytes=? WHERE id=?",(digest,part.stat().st_size if part.exists() else 0,row['id']))
        self.bytes_received=max(self.c.execute('SELECT COALESCE(SUM(bytes),0) FROM requests').fetchone()[0],sum(p.stat().st_size for p in (self.root/'partial').iterdir() if p.is_file()))
        self.request_count=self.c.execute('SELECT COUNT(*) FROM requests').fetchone()[0]
    def get(self,url,reuse=False):
        self.guard()
        if reuse:
            row=self.c.execute('SELECT sha256 FROM requests WHERE url=? AND status=200 AND error IS NULL ORDER BY id DESC LIMIT 1',(url,)).fetchone()
            if row:
                cached=self.body_path(row['sha256'])
                if sha(cached)!=row['sha256']:raise RuntimeError('Cached response hash mismatch')
                return cached
        original=url
        for attempt in range(3):
            for hop in range(6):
                u=urlsplit(url)
                if u.scheme!='https' or not (u.hostname=='nlm.nih.gov' or (u.hostname or '').endswith('.nlm.nih.gov')):raise ValueError('Official NLM HTTPS URLs only')
                if self.request_count>=self.cap:raise RuntimeError('Workspace request cap reached')
                if self.max_bytes is not None and self.bytes_received>=self.max_bytes:raise RuntimeError('Workspace byte cap reached')
                last=self.c.execute('SELECT MAX(started) FROM requests').fetchone()[0] or 0
                deferred=self.c.execute("SELECT value FROM transport_state WHERE key='retry_after'").fetchone()
                while deferred and deferred[0]>time.time():
                    if not (self.pacing and self.pacing.policy.get('wait_for_retry_after',False)):
                        raise RuntimeError('Retry deferred by upstream Retry-After; retained in ledger')
                    self.guard();time.sleep(min(.1,max(0,deferred[0]-time.time())))
                    deferred=self.c.execute("SELECT value FROM transport_state WHERE key='retry_after'").fetchone()
                spacing=self.pacing.spacing if self.pacing else self.spacing
                time.sleep(max(0,last+spacing-time.time()));ensure_space(self.root,100*1024**2)
                self.guard()
                with self.c:ident=self.c.execute('INSERT INTO requests(url,started) VALUES(?,?)',(url,time.time())).lastrowid
                self.request_count+=1
                part=self.root/'partial'/str(ident);status=0;failure=None;headers={};retryable_failure=False
                response_started=time.monotonic()
                try:
                    agent='GitRx/0.10 local DailyMed research archive; bounded adaptive transport' if self.pacing else 'GitRx/0.10 native history archive; sequential >=2 seconds'
                    request=urllib.request.Request(url,headers={'User-Agent':agent,'Accept-Encoding':'identity'})
                    try:response=(self.opener or urllib.request.build_opener(NoRedirect)).open(request,timeout=30)
                    except urllib.error.HTTPError as exc:response=exc
                    with response,part.open('xb') as f:
                        status=response.code;headers={k.lower():v for k,v in response.headers.items()};total=0
                        while chunk:=response.read(1024*1024):
                            if time.monotonic()-response_started>self.max_response_seconds:raise TimeoutError('Response execution deadline exceeded')
                            self.guard()
                            ensure_space(self.root,2*1024**2)
                            if self.max_bytes is not None and self.bytes_received+len(chunk)>self.max_bytes:
                                prefix=chunk[:max(0,self.max_bytes-self.bytes_received)]
                                f.write(prefix);self.bytes_received+=len(prefix)
                                sync_body(f)
                                raise ValueError('Workspace byte cap reached; response prefix retained')
                            total+=len(chunk)
                            if total>100*1024**2:raise ValueError('Response exceeds 100 MiB')
                            f.write(chunk);self.bytes_received+=len(chunk)
                        self.fullfsync_supported=sync_body(f)
                except (OSError,http.client.HTTPException) as exc:failure=str(exc);retryable_failure=True
                except ValueError as exc:failure=str(exc)
                if failure and self.opener:self.opener.close()
                self.guard()
                if not part.exists():part.write_bytes(b'')
                digest=sha(part);target=self.root/'bodies'/digest
                if target.exists():
                    if sha(target)!=digest:raise RuntimeError('Existing HTTP content hash changed')
                else:os.link(part,target)
                directory=os.open(target.parent,os.O_RDONLY)
                try:os.fsync(directory)
                finally:os.close(directory)
                if self.raw_journal:self.raw_journal.store(target,digest,external_path=target)
                if headers.get('retry-after'):
                    from gitrx.current.archive import retry_delay
                    until=time.time()+retry_delay(list(headers.items()),0)
                    with self.c:self.c.execute("INSERT OR REPLACE INTO transport_state VALUES('retry_after',?)",(until,))
                with self.c:self.c.execute('UPDATE requests SET status=?,sha256=?,error=?,headers_json=?,bytes=? WHERE id=?',(status,digest,failure,json.dumps(headers),part.stat().st_size,ident))
                latency=time.monotonic()-response_started
                with self.c:self.c.execute('UPDATE requests SET latency_seconds=?,spacing_seconds=?,connection_serial=? WHERE id=?',(latency,spacing,self.opener.serial if self.opener else None,ident))
                if self.pacing:self.pacing.note(status,failure,latency,headers)
                if not self.retain_completed_partials and not failure:part.unlink()
                if status in (301,302,303,307,308) and not failure:
                    from urllib.parse import urljoin
                    url=urljoin(url,headers.get('location',''));continue
                if status==200 and not failure:return target
                if (not retryable_failure and status not in (0,408,429,500,502,503,504)) or attempt==2:raise ValueError(f'HTTP {status}: {failure or original}')
                # Do not defeat an upstream Retry-After with a short retry loop.
                if headers.get('retry-after') and not (self.pacing and self.pacing.policy.get('wait_for_retry_after',False)):
                    raise RuntimeError('Retry deferred by upstream Retry-After; retained in ledger')
                break
            else:raise ValueError('Redirect limit exceeded')
            url=original
        raise ValueError('Retrieval failed')
    def body_path(self,digest):
        local=self.root/'bodies'/digest
        if local.exists():return local
        for directory in self.body_fallbacks:
            candidate=directory/digest
            if candidate.exists():return candidate
        return local

    def close(self):
        if self.opener:self.opener.close()
        self.c.close()

