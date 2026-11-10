"""Canonical local SQLite working state with guarded external frozen snapshots.

Call attach only while that database has no live writers. Open the returned local
path directly so WAL/SHM files stay local. freeze requires all connections closed;
release is only valid after the caller commits its external batch/control pins.
"""
import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
import threading
import uuid

from .jobs import atomic, sha
from gitrx_services.io import ensure_space

FLOOR=20*1024**3


def _sync_dir(path):
    fd=os.open(path,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def _row_hash(row):
    h=hashlib.sha256()
    for value in row:
        if value is None:kind=b'n';body=b''
        elif isinstance(value,bytes):kind=b'b';body=value
        elif isinstance(value,int):kind=b'i';body=str(value).encode()
        elif isinstance(value,float):kind=b'f';body=value.hex().encode()
        else:kind=b't';body=str(value).encode('utf-8')
        h.update(kind);h.update(str(len(body)).encode()+b':');h.update(body)
    return int.from_bytes(h.digest(),'big')


def semantic_digest(c):
    """Order-independent typed row multiset plus exact schema, without large sorts."""
    schema=c.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
    summary=[]
    modulus=1<<256
    for name, in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        quoted='"'+name.replace('"','""')+'"'
        count=0;total=0;xor=0
        for row in c.execute('SELECT * FROM '+quoted):
            value=_row_hash(row);count+=1;total=(total+value)%modulus;xor^=value
        summary.append((name,count,hex(total),hex(xor)))
    body=json.dumps({'schema':schema,'tables':summary},ensure_ascii=False,separators=(',',':')).encode()
    return hashlib.sha256(body).hexdigest()


def _readonly(path,immutable=False):
    suffix='?mode=ro'+('&immutable=1' if immutable else '')
    return sqlite3.connect(Path(path).absolute().as_uri()+suffix,uri=True,timeout=30)


def _integrity(c):
    rows=c.execute('PRAGMA integrity_check').fetchall()
    if rows!=[('ok',)]:raise RuntimeError('Hot SQLite integrity check failed: '+repr(rows[:3]))


class HotStorage:
    def __init__(self,policyfile,guard):
        self.policy_path=Path(policyfile).absolute()
        self.policy=json.loads(self.policy_path.read_bytes())
        if self.policy['format']!='gitrx-hot-storage-1':raise ValueError('Unsupported hot-storage policy')
        self.root=Path(self.policy['local_root']).expanduser().resolve()
        self.guard=guard
        self.limit=int(self.policy.get('max_bytes',8*1024**3))
        self.scan_interval=float(self.policy.get('scan_interval_seconds',0))
        if not 0<=self.scan_interval<=.05:raise ValueError('Hot storage scan interval must be 0..0.05 seconds')
        self.scan_lock=threading.Lock();self.last_scan=0;self.last_used=0
        if self.limit<128*1024**2:raise ValueError('Hot storage needs a bounded budget of at least 128 MiB')
        self.objects=self.root/'objects';self.entries=self.root/'entries'
        ensure_space(self.root,32*1024**2)
        self.objects.mkdir(parents=True,exist_ok=True);self.entries.mkdir(exist_ok=True)

    def check(self,additional=0):
        """Cheap local write guard; the caller should also use it during SQL writes."""
        # DirEntry caches the type/stat lookup: do not construct Paths and
        # issue three separate stat calls for every file on every SQL write.
        with self.scan_lock:
            # Opt-in coalescing only while a full 256 MiB growth reserve is
            # available. Near the cap every call scans, as before. Explicit
            # allocation checks always scan rather than using an old size.
            if (not additional and time.monotonic()-self.last_scan<self.scan_interval
                and self.last_used+256*1024**2<=self.limit):return
            ensure_space(self.root,additional+32*1024**2)
            used=0
            with os.scandir(self.objects) as entries:
                for entry in entries:
                    try:info=entry.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        # SQLite can unlink WAL/SHM files while another thread
                        # scans. A vanished file consumes no working storage.
                        continue
                    if stat.S_ISREG(info.st_mode):used+=info.st_size
            self.last_used=used;self.last_scan=time.monotonic()
        if used+additional+32*1024**2>self.limit:raise RuntimeError('Hot SQLite working-storage cap reached')

    def _identity(self,path):
        external=Path(os.path.abspath(os.fspath(path)))
        if external.is_relative_to(self.root):raise ValueError('External alias must be outside local working storage')
        key=hashlib.sha256(str(external).encode()).hexdigest()
        return external,key,self.objects/(key+'.sqlite'),self.entries/(key+'.json')

    def _state(self,path):
        external,key,local,record=self._identity(path)
        if not record.exists():raise RuntimeError('No hot-storage registration for database')
        state=json.loads(record.read_bytes())
        if state['external_path']!=str(external) or state['local_path']!=str(local):raise RuntimeError('Hot-storage registration changed')
        return external,key,local,record,state

    def _save(self,record,state,phase=None):
        self.check()
        if phase:state['phase']=phase
        state['updated_epoch']=time.time()
        atomic(record,state)

    def _copy(self,source,destination,external=False):
        size=source.stat().st_size
        if external:
            self.guard();ensure_space(destination,size+1024**2)
        else:self.check(size)
        with source.open('rb') as src,destination.open('xb') as dst:
            while chunk:=src.read(1024*1024):
                if external:self.guard();ensure_space(destination,len(chunk)+1024**2)
                else:self.check(len(chunk))
                dst.write(chunk)
            dst.flush();os.fsync(dst.fileno())
        if sha(source)!=sha(destination):raise RuntimeError('Hot-storage copied bytes differ')
        _sync_dir(destination.parent)

    def _history(self,external,key):
        self.guard()
        directory=external.parent/'.hot-storage-history'/key
        directory.mkdir(parents=True,exist_ok=True)
        return directory

    def _preserve(self,external,key,state,record):
        directory=self._history(external,key)
        originals=state.setdefault('original_files',{})
        for suffix in ('','-wal','-shm'):
            source=Path(str(external)+suffix)
            if not source.exists():continue
            if source.is_symlink():raise RuntimeError('Unexpected SQLite sidecar/ordinary source symlink')
            digest=sha(source);name='original'+(suffix or '.sqlite')
            dest=directory/name
            if dest.exists():
                if sha(dest)!=originals.get(suffix,{}).get('sha256',digest):raise RuntimeError('Retained migration original changed')
            else:
                temporary=directory/(name+'.copy-'+uuid.uuid4().hex)
                self._copy(source,temporary,external=True)
                os.rename(temporary,dest);_sync_dir(directory)
            originals[suffix]={'path':str(dest),'sha256':sha(dest),'bytes':dest.stat().st_size}
        self._save(record,state)

    def _alias(self,external,local):
        self.guard()
        external.parent.mkdir(parents=True,exist_ok=True)
        if external.is_symlink():
            if external.resolve()!=local.resolve():raise RuntimeError('External SQLite alias points elsewhere')
            return
        if external.exists():raise RuntimeError('Refusing to overwrite an ordinary external database')
        temporary=external.with_name(external.name+'.hot-link-'+uuid.uuid4().hex)
        os.symlink(str(local),temporary)
        os.replace(temporary,external);_sync_dir(external.parent)

    def _retire(self,external,key,state,record):
        directory=self._history(external,key)
        for suffix in ('','-wal','-shm'):
            source=Path(str(external)+suffix)
            if not source.exists():continue
            if source.is_symlink():raise RuntimeError('Unexpected symlink while retiring SQLite source')
            digest=sha(source)
            original=state.get('original_files',{}).get(suffix)
            if original and original['sha256']==digest and sha(original['path'])==digest:
                source.unlink()
            else:
                dest=directory/('retired-'+digest+(suffix or '.sqlite'))
                if dest.exists():
                    if sha(dest)!=digest:raise RuntimeError('Retained migration bytes changed')
                    source.unlink()
                else:os.rename(source,dest)
            self._save(record,state,'source-retiring')
        _sync_dir(external.parent);_sync_dir(directory)

    def attach(self,path,label):
        """Return local canonical SQLite path, preserving any ordinary original."""
        self.guard();self.check()
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',label) or label in ('.','..'):
            raise ValueError('Unsafe hot-storage label')
        external,key,local,record=self._identity(path)
        state=json.loads(record.read_bytes()) if record.exists() else {
            'format':'gitrx-hot-sqlite-1','external_path':str(external),'local_path':str(local),
            'label':label,'created_epoch':time.time(),'phase':'new'}
        if state.get('external_path')!=str(external) or state.get('local_path')!=str(local) or state.get('label')!=label:
            raise RuntimeError('Hot-storage registration conflicts with requested database')
        if state['phase'] in ('released','release-pending'):raise RuntimeError('Frozen/released database cannot become mutable again')
        if state['phase']=='snapshot-prepared' and not local.exists():
            prepared=Path(state['prepared_snapshot'])
            if not prepared.is_relative_to(self.objects) or sha(prepared)!=state['local_snapshot_sha256']:
                raise RuntimeError('Interrupted SQLite snapshot changed or disappeared')
            with closing(_readonly(prepared,immutable=True)) as c:_integrity(c)
            os.replace(prepared,local);_sync_dir(self.objects)
            self._save(record,state,'snapshot-ready')
        if external.is_symlink():
            if external.resolve()!=local.resolve() or not local.exists():raise RuntimeError('Hot SQLite alias target missing or changed')
            with closing(_readonly(local)) as c:_integrity(c)
            self._save(record,state,'attached');return local
        if local.exists():
            with closing(_readonly(local)) as c:_integrity(c)
            if state['phase'] in ('snapshot-prepared','snapshot-ready','source-retiring','frozen','freeze-prepared'):
                if sha(local)!=state.get('local_snapshot_sha256'):raise RuntimeError('Interrupted migration local snapshot changed')
                if external.exists():
                    with closing(_readonly(external)) as c:
                        _integrity(c)
                        if semantic_digest(c)!=state['semantic_sha256']:raise RuntimeError('External and local databases conflict')
                    self._retire(external,key,state,record)
            elif external.exists():
                raise RuntimeError('Ordinary external database conflicts with canonical local working state')
            self._alias(external,local);self._save(record,state,'attached');return local
        if state['phase'] not in ('new','source-preserved'):
            raise RuntimeError('Canonical hot SQLite database is missing')
        temporary=self.objects/(key+'.snapshot-'+uuid.uuid4().hex)
        if external.exists():
            if not external.is_file():raise RuntimeError('SQLite source is not a regular file')
            self._preserve(external,key,state,record)
            self._save(record,state,'source-preserved')
            with closing(_readonly(external)) as source,closing(sqlite3.connect(temporary)) as dest:
                _integrity(source)
                source.execute('BEGIN')
                signature=semantic_digest(source)
                source.backup(dest,pages=256,progress=lambda *_:(self.guard(),self.check()))
                _integrity(dest)
                if semantic_digest(dest)!=signature:raise RuntimeError('SQLite backup rows/schema differ')
        else:
            with closing(sqlite3.connect(temporary)) as dest:
                dest.execute('PRAGMA user_version=0');dest.commit();_integrity(dest)
                signature=semantic_digest(dest)
        with temporary.open('rb') as f:os.fsync(f.fileno())
        state.update(local_snapshot_sha256=sha(temporary),semantic_sha256=signature,prepared_snapshot=str(temporary))
        self._save(record,state,'snapshot-prepared')
        os.replace(temporary,local);_sync_dir(self.objects)
        self._save(record,state,'snapshot-ready')
        if external.exists():self._retire(external,key,state,record)
        self._alias(external,local);self._save(record,state,'attached')
        return local

    def freeze(self,path):
        """Close callers first; write a validated byte-identical external snapshot."""
        self.guard();self.check()
        external,key,local,record,state=self._state(path)
        if state['phase'] in ('released','release-pending'):
            if sha(external)!=state.get('frozen_sha256'):raise RuntimeError('Released external database changed')
            return external
        if state['phase']=='frozen' and external.is_file() and not external.is_symlink():
            if sha(external)!=state.get('frozen_sha256') or sha(local)!=state.get('frozen_sha256'):
                raise RuntimeError('Frozen SQLite snapshot changed')
            return external
        if state['phase']=='freeze-prepared' and external.is_file() and not external.is_symlink():
            if sha(external)!=state.get('frozen_sha256') or sha(local)!=state.get('frozen_sha256'):
                raise RuntimeError('Interrupted freeze pins changed')
            self._save(record,state,'frozen');return external
        if not local.is_file():raise RuntimeError('Canonical hot SQLite database missing')
        if external.exists() and not external.is_symlink():raise RuntimeError('Unexpected ordinary database at external alias')
        if external.is_symlink() and external.resolve()!=local.resolve():raise RuntimeError('External SQLite alias changed')
        with closing(sqlite3.connect(local)) as c:
            checkpoint=c.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
            if checkpoint[0]!=0:raise RuntimeError('Hot SQLite still has live connections/writes')
            _integrity(c);signature=semantic_digest(c)
        digest=sha(local)
        temp=external.with_name(external.name+'.freeze-'+uuid.uuid4().hex)
        self._copy(local,temp,external=True)
        with closing(_readonly(temp,immutable=True)) as c:
            _integrity(c)
            if semantic_digest(c)!=signature:raise RuntimeError('Frozen SQLite rows/schema differ')
        if sha(temp)!=digest:raise RuntimeError('Frozen SQLite bytes differ')
        state.update(frozen_sha256=digest,local_snapshot_sha256=digest,semantic_sha256=signature)
        self._save(record,state,'freeze-prepared')
        self.guard();os.replace(temp,external);_sync_dir(external.parent)
        self._save(record,state,'frozen')
        return external

    def release(self,path):
        """Caller must first commit the external batch and control hashes."""
        self.guard()
        external,key,local,record,state=self._state(path)
        if state['phase']=='released':
            if sha(external)!=state['frozen_sha256']:raise RuntimeError('Released SQLite snapshot changed')
            return external
        if state['phase'] not in ('frozen','release-pending') or external.is_symlink():raise RuntimeError('Only a frozen external database can be released')
        if sha(external)!=state['frozen_sha256'] or (local.exists() and sha(local)!=state['frozen_sha256']):
            raise RuntimeError('Freeze pins changed before release')
        if not local.exists() and state['phase']!='release-pending':raise RuntimeError('Canonical SQLite disappeared before release')
        for suffix in ('-wal','-shm'):
            sidecar=Path(str(local)+suffix)
            if suffix=='-wal' and sidecar.exists() and sidecar.stat().st_size:
                raise RuntimeError('Canonical database gained writes after freeze')
        self._save(record,state,'release-pending')
        local.unlink(missing_ok=True)
        for suffix in ('-wal','-shm'):Path(str(local)+suffix).unlink(missing_ok=True)
        _sync_dir(self.objects);self._save(record,state,'released')
        return external
