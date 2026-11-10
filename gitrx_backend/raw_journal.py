"""Bounded internal write-ahead tail for exact recently received response bytes.

This is not a whole-archive backup. Old journal bodies are evicted only after an
independent canonical copy is present and hash-verified. Missing external evidence
stops eviction rather than deleting its last known copy.
"""
import errno
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid

from .jobs import atomic,sha
from gitrx_services.io import ensure_space


class JournalCapacityError(RuntimeError):pass


def _sync(fd):
    os.fsync(fd)
    command=getattr(fcntl,'F_FULLFSYNC',None)
    if command is not None:
        try:fcntl.fcntl(fd,command)
        except OSError as exc:
            if exc.errno not in (errno.EINVAL,errno.ENOTSUP,errno.ENOSYS):raise


def _sync_dir(path):
    fd=os.open(path,os.O_RDONLY)
    try:_sync(fd)
    finally:os.close(fd)


class RawJournal:
    def __init__(self,root,max_bytes=512*1024**2):
        self.root=Path(root).expanduser().resolve();self.max_bytes=int(max_bytes)
        if self.max_bytes<1:raise ValueError('Positive raw journal byte cap required')
        ensure_space(self.root,1024**2)
        self.root.mkdir(parents=True,exist_ok=True)
        self.objects=self.root/'objects';self.objects.mkdir(exist_ok=True)
        self.lock=(self.root/'journal.lock').open('a+')
        fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        self.index=self.root/'index.sqlite'
        self.c=None
        try:
            self.c=sqlite3.connect(self.index,timeout=30)
            if self.c.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise sqlite3.DatabaseError('raw journal index integrity failed')
            self._schema()
            self._finish_evictions();self._rebuild()
        except sqlite3.DatabaseError:
            if self.c is not None:self.c.close()
            stamp=str(time.time_ns())
            for suffix in ('','-wal','-shm'):
                path=Path(str(self.index)+suffix)
                if path.exists():os.rename(path,self.root/('index.sqlite.incident-'+stamp+suffix))
            _sync_dir(self.root)
            self.c=sqlite3.connect(self.index,timeout=30);self._schema();self._rebuild()
        self.partial_bytes=sum(p.stat().st_size for p in self.objects.glob('.pending-*') if p.is_file())

    def _schema(self):
        self.c.executescript('''PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
        PRAGMA fullfsync=ON; PRAGMA checkpoint_fullfsync=ON;
        CREATE TABLE IF NOT EXISTS objects(id INTEGER PRIMARY KEY AUTOINCREMENT,digest TEXT UNIQUE NOT NULL,bytes INTEGER NOT NULL,created_ns INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS originals(digest TEXT,path TEXT,PRIMARY KEY(digest,path));
        CREATE TABLE IF NOT EXISTS evictions(digest TEXT PRIMARY KEY);
        ''');self.c.commit()

    def _path(self,digest):
        if not isinstance(digest,str) or not re.fullmatch('[0-9a-f]{64}',digest):raise ValueError('Invalid response SHA-256')
        return self.objects/digest

    def _metadata(self,path):
        metadata=path.with_suffix('.json')
        if not metadata.exists():return {'created_ns':path.stat().st_mtime_ns,'external_paths':[]}
        value=json.loads(metadata.read_bytes())
        if value['digest']!=path.name or value['bytes']!=path.stat().st_size:raise RuntimeError('Raw journal metadata changed')
        return value

    def _insert(self,path,metadata):
        with self.c:
            self.c.execute('INSERT INTO objects(digest,bytes,created_ns) VALUES(?,?,?) ON CONFLICT(digest) DO UPDATE SET bytes=excluded.bytes',(path.name,path.stat().st_size,int(metadata['created_ns'])))
            for original in metadata.get('external_paths',[]):
                original=Path(original).absolute()
                if not original.resolve().is_relative_to(self.root):
                    self.c.execute('INSERT OR IGNORE INTO originals VALUES(?,?)',(path.name,str(original)))

    def _rebuild(self):
        # Only on an absent/corrupt derived index, not on each saved response.
        found=[]
        for path in self.objects.iterdir():
            if re.fullmatch('[0-9a-f]{64}',path.name):
                if path.is_symlink() or sha(path)!=path.name:raise RuntimeError('Corrupt raw journal body; retained for review')
                metadata=self._metadata(path);found.append((metadata['created_ns'],path,metadata))
        for _,path,metadata in sorted(found,key=lambda row:(row[0],row[1].name)):self._insert(path,metadata)
        for digest, in self.c.execute('SELECT digest FROM objects'):
            if not self._path(digest).exists():raise RuntimeError('Indexed raw journal body is missing')

    def _external_verified(self,digest,size):
        for original, in self.c.execute('SELECT path FROM originals WHERE digest=?',(digest,)):
            path=Path(original)
            if path.resolve().is_relative_to(self.root):continue
            try:
                if path.is_file() and path.stat().st_size==size and sha(path)==digest and path.is_file():return True
            except OSError:pass
        return False

    def _finish_evictions(self):
        for digest, in self.c.execute('SELECT digest FROM evictions').fetchall():
            path=self._path(digest)
            row=self.c.execute('SELECT bytes FROM objects WHERE digest=?',(digest,)).fetchone()
            if path.exists():
                if sha(path)!=digest:raise RuntimeError('Corrupt raw journal body; retained for review')
                if not row or not self._external_verified(digest,row[0]):
                    with self.c:self.c.execute('DELETE FROM evictions WHERE digest=?',(digest,))
                    continue
                path.unlink();path.with_suffix('.json').unlink(missing_ok=True);_sync_dir(self.objects)
            with self.c:
                self.c.execute('DELETE FROM originals WHERE digest=?',(digest,))
                self.c.execute('DELETE FROM objects WHERE digest=?',(digest,))
                self.c.execute('DELETE FROM evictions WHERE digest=?',(digest,))

    def lookup(self,digest):
        path=self._path(digest)
        row=self.c.execute('SELECT bytes FROM objects WHERE digest=?',(digest,)).fetchone()
        if not path.exists():
            if row is not None:raise RuntimeError('Indexed raw journal body is missing')
            return None
        if path.is_symlink() or sha(path)!=digest:raise RuntimeError('Corrupt raw journal body; retained for review')
        if row is not None and row[0]!=path.stat().st_size:raise RuntimeError('Raw journal size changed')
        if row is None:self._insert(path,self._metadata(path))
        return path

    def _evict(self,needed):
        used=self.c.execute('SELECT COALESCE(SUM(bytes),0) FROM objects').fetchone()[0]
        while used+needed+self.partial_bytes>self.max_bytes:
            oldest=self.c.execute('SELECT digest,bytes FROM objects ORDER BY id LIMIT 1').fetchone()
            if oldest is None:raise JournalCapacityError('Response exceeds raw journal byte cap')
            digest,size=oldest;local=self.lookup(digest)
            safe=self._external_verified(digest,size)
            if not safe:raise JournalCapacityError('Raw journal cap reached; oldest external original is unavailable, so its local body was retained')
            # The external canonical copy is independent and verified. The local
            # tail is a rebuildable staging copy; no canonical source is deleted.
            with self.c:self.c.execute('INSERT OR IGNORE INTO evictions VALUES(?)',(digest,))
            local.unlink();local.with_suffix('.json').unlink(missing_ok=True);_sync_dir(self.objects)
            with self.c:
                self.c.execute('DELETE FROM originals WHERE digest=?',(digest,))
                self.c.execute('DELETE FROM objects WHERE digest=?',(digest,))
                self.c.execute('DELETE FROM evictions WHERE digest=?',(digest,))
            used-=size

    def store(self,source,digest,external_path=None):
        source=Path(source).absolute();path=self._path(digest)
        if source.is_symlink():source=source.resolve()
        if sha(source)!=digest:raise RuntimeError('Raw journal input SHA-256 mismatch')
        size=source.stat().st_size
        if size>self.max_bytes:raise JournalCapacityError('Response exceeds raw journal byte cap')
        original=Path(external_path).absolute() if external_path is not None else source
        if original.resolve().is_relative_to(self.root):raise ValueError('Canonical original must be independent of raw journal')
        if original.is_file() and sha(original)!=digest:raise RuntimeError('Canonical original differs from journal input')
        existing=self.lookup(digest)
        if existing is None:
            self._evict(size);ensure_space(self.root,size+1024**2)
            temporary=self.objects/('.pending-'+uuid.uuid4().hex)
            h=hashlib.sha256()
            try:
                with source.open('rb') as src,temporary.open('xb') as dst:
                    while chunk:=src.read(1024*1024):
                        ensure_space(self.root,len(chunk)+1024**2)
                        dst.write(chunk);h.update(chunk)
                    dst.flush();_sync(dst.fileno())
                if h.hexdigest()!=digest:raise RuntimeError('Raw response changed during journal copy')
            except BaseException:
                if temporary.exists():self.partial_bytes+=temporary.stat().st_size
                raise
            os.replace(temporary,path);_sync_dir(self.objects)
            metadata={'digest':digest,'bytes':size,'created_ns':time.time_ns(),'external_paths':[]}
        else:metadata=self._metadata(path)
        paths=set(metadata.get('external_paths',[]));paths.add(str(original));metadata['external_paths']=sorted(paths)
        atomic(path.with_suffix('.json'),metadata)
        with path.with_suffix('.json').open('rb') as f:_sync(f.fileno())
        _sync_dir(self.objects)
        self._insert(path,metadata)
        return path

    def close(self):
        if self.c is not None:
            self.c.execute('PRAGMA wal_checkpoint(FULL)');self.c.close();self.c=None
        self.lock.close()
