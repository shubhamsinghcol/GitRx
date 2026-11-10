"""Bounded local raw working set; verified external CAS handoff at batch boundaries."""
import os
from pathlib import Path
import shutil
import uuid
import time
import threading
from .jobs import sha,sync_body,atomic
from gitrx_services.io import ensure_space


def syncdir(path):
    fd=os.open(path,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


def copy_verified(source,target,digest=None,link_immutable=False):
    source=Path(source);target=Path(target);want=digest or sha(source)
    if target.exists():
        if sha(target)!=want:raise RuntimeError('Staging destination hash mismatch')
        return
    target.parent.mkdir(parents=True,exist_ok=True)
    if link_immutable and source.stat().st_dev==target.parent.stat().st_dev:
        # Only immutable CAS bodies opt in. Local mobile handoff must not
        # transiently double a large future-batch buffer's physical disk use.
        if sha(source)!=want:raise RuntimeError('Staging source hash mismatch')
        os.link(source,target);syncdir(target.parent)
        return
    temporary=target.with_name('.handoff-'+uuid.uuid4().hex)
    with source.open('rb') as src,temporary.open('xb') as dst:
        shutil.copyfileobj(src,dst,1024**2);sync_body(dst)
    if sha(temporary)!=want:raise RuntimeError('Staging transfer hash mismatch; original retained')
    os.link(temporary,target);temporary.unlink();syncdir(target.parent)


class RawStaging:
    def __init__(self,local,external,cap,guard):
        self.root=Path(local);self.external=Path(external);self.cap=cap;self.guard=guard
        if not self.root.is_absolute() or self.root.is_symlink() or self.root==Path.home() or len(self.root.parts)<4:
            raise ValueError('Raw staging requires a dedicated absolute local directory')
        self.root.mkdir(parents=True,exist_ok=True)
        if self.root.stat().st_dev!=Path.home().stat().st_dev:raise ValueError('Raw staging must use the laptop filesystem')
        self.network=self.root/'network';(self.network/'bodies').mkdir(parents=True,exist_ok=True)
        (self.network/'partial').mkdir(exist_ok=True)
        self.last_check=0;self.check_lock=threading.Lock()
        self.check()

    def check(self):
        with self.check_lock:
            if time.monotonic()-self.last_check<1:return
            used=0;directories=[self.root]
            while directories:
                with os.scandir(directories.pop()) as entries:
                    for entry in entries:
                        if entry.is_dir(follow_symlinks=False):directories.append(entry.path)
                        elif entry.is_file(follow_symlinks=False):
                            used+=entry.stat(follow_symlinks=False).st_size
            if used+100*1024**2>self.cap:raise RuntimeError('Local raw staging cap reached; pending evidence retained')
            ensure_space(self.root,100*1024**2)
            self.last_check=time.monotonic()

    def attach(self,batch):
        batch=Path(batch);alias=batch/'artifacts';local=self.root/'batches'/batch.name/'artifacts'
        if (batch/'RAW-HANDOFF.json').exists():
            if alias.is_symlink() or not alias.is_dir():raise RuntimeError('Completed raw handoff missing artifacts')
            return
        if (batch/'RAW-HANDOFF-STARTED.json').exists() and alias.is_dir() and not alias.is_symlink():
            for source in local.rglob('*'):
                if source.is_file() and sha(alias/source.relative_to(local))!=sha(source):
                    raise RuntimeError('Interrupted raw handoff differs from local evidence')
            atomic(batch/'RAW-HANDOFF.json',{'format':'gitrx-raw-handoff-1','batch':batch.name})
            return
        local.mkdir(parents=True,exist_ok=True);batch.mkdir(parents=True,exist_ok=True)
        if alias.is_symlink():
            if alias.resolve()!=local.resolve():raise RuntimeError('Unexpected staged artifacts alias')
            return
        if alias.exists():
            # Existing pre-migration bytes stay immutable in the retained directory.
            for p in alias.rglob('*'):
                if p.is_file():copy_verified(p,local/p.relative_to(alias))
            old=batch/'artifacts.before-local-staging'
            if old.exists():raise RuntimeError('Ambiguous artifacts migration; review required')
            os.rename(alias,old);syncdir(batch)
        alias.symlink_to(local,target_is_directory=True);syncdir(batch)
        self.check()

    def flush(self,batch,keep_hashes=()):
        self.guard();batch=Path(batch);alias=batch/'artifacts'
        local=self.root/'batches'/batch.name/'artifacts'
        # A mobile job can leave an SSD-dependent family in another mutable
        # batch. Its saved observation paths must remain readable until retry.
        keep=set(keep_hashes)
        for other in (self.root/'batches').glob('*/artifacts'):
            if other==local:continue
            keep.update(p.name for p in other.rglob('*') if p.is_file())
        # Publish every retained HTTP body, including unsuccessful responses.
        for source in (self.network/'bodies').iterdir():
            if source.is_file():
                self.guard();copy_verified(source,self.external/'network/bodies'/source.name,source.name,link_immutable=True)
        for source in (self.network/'partial').iterdir():
            if source.is_file():
                self.guard();copy_verified(source,self.external/'network/partial'/source.name)
        if alias.is_symlink() or not alias.exists():
            prepared=batch/'artifacts.handoff';prepared.mkdir(exist_ok=True)
            for source in local.rglob('*'):
                if not source.is_file():continue
                self.guard();target=prepared/source.relative_to(local)
                canonical=self.external/'network/bodies'/source.name
                copy_verified(source,canonical,source.name)
                target.parent.mkdir(parents=True,exist_ok=True)
                if target.exists():
                    if sha(target)!=source.name:raise RuntimeError('Staged artifact handoff changed')
                else:os.link(canonical,target);syncdir(target.parent)
            self.guard()
            atomic(batch/'RAW-HANDOFF-STARTED.json',{'batch':batch.name})
            if alias.is_symlink():alias.unlink();syncdir(batch)
            os.rename(prepared,alias);syncdir(batch)
        atomic(batch/'RAW-HANDOFF.json',{'format':'gitrx-raw-handoff-1','batch':batch.name})
        # Only remove local copies after all external bytes have been verified.
        # A disconnect before this point leaves the full local working set.
        self.guard()
        for source in (self.network/'bodies').iterdir():
            if sha(self.external/'network/bodies'/source.name)!=source.name:raise RuntimeError('External handoff verification failed')
        if local.exists():shutil.rmtree(local)
        for folder in (self.network/'bodies',self.network/'partial'):
            for source in folder.iterdir():
                if folder.name=='bodies' and source.name in keep:continue
                source.unlink()
            syncdir(folder)
