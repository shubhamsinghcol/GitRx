"""Local canonical working metadata with verified, explicitly armed SSD archiving."""
import json
import os
from pathlib import Path
import shutil
import stat
import threading
import time
from .jobs import atomic,sha
from .backfill_staging import copy_verified,syncdir
from gitrx_services.io import ensure_space


class ArchiveUnavailable(RuntimeError):pass


class MobileArchive:
    def __init__(self,config,enforce_budget=True):
        p=config['mobile_archive'];self.config=config;self.root=Path(config['workspace']).absolute()
        self.archive=Path(p['archive_workspace']).absolute();self.volume=Path(config['volume']).absolute()
        self.disabled=self.root/'SSD-DISABLED';self.limit=p.get('max_local_bytes',40*1024**3)
        if (not self.root.is_absolute() or self.root.is_relative_to(self.volume) or self.root==Path.home()
            or len(self.root.parts)<5 or not 8*1024**3<=self.limit<=80*1024**3):
            raise ValueError('Unsafe mobile workspace or budget')
        self.extra_roots=[Path(x) for x in p.get('local_work_roots',[])]
        self.last_scan=0;self.lock=threading.Lock();self.last_usage=0
        self.root.mkdir(parents=True,exist_ok=True)
        if self.root.stat().st_dev!=Path.home().stat().st_dev:raise RuntimeError('Mobile work must stay on the laptop filesystem')
        if enforce_budget:self.guard()

    def available(self,ignore_disabled=False):
        if not ignore_disabled and self.disabled.exists():return False
        try:
            return (self.volume.is_mount() and self.volume.stat().st_dev!=Path.home().stat().st_dev
                and (self.volume/self.config['volume_marker']).read_text().strip()==self.config['volume_token']
                and self.archive.is_relative_to(self.volume))
        except OSError:return False

    def external_guard(self):
        if not self.available():raise ArchiveUnavailable('Correct SSD not enabled; completed work stays local')
        ensure_space(self.volume,256*1024**2)

    def archive_dependency(self,path):
        return Path(path).resolve(strict=False).is_relative_to(self.volume)

    def guard(self):
        with self.lock:
            if time.monotonic()-self.last_scan<2:return
            ensure_space(self.root,256*1024**2)
            seen=set();used=0
            for root in [self.root]+self.extra_roots:
                if root.is_symlink():raise RuntimeError('Local mobile storage root cannot be a symlink')
                for directory,dirs,files in os.walk(root,followlinks=False):
                    for name in files:
                        try:info=os.lstat(Path(directory)/name)
                        except FileNotFoundError:continue
                        key=(info.st_dev,info.st_ino)
                        if stat.S_ISREG(info.st_mode) and key not in seen:used+=info.st_size;seen.add(key)
            self.last_usage=used;self.last_scan=time.monotonic()
            if used+2*1024**3>self.limit:raise RuntimeError('Mobile working-storage cap reached; reconnect SSD to archive waiting batches')

    def final_batch(self,number):return self.archive/'batches'/f'{number:05d}'

    def receipt(self,batch):
        marker=batch/'ARCHIVE-RECEIPT.json'
        if not marker.exists():return None
        r=json.loads(marker.read_bytes());m=json.loads((batch/'BATCH.json').read_bytes())
        if (r['manifest_sha256']!=sha(batch/'BATCH.json') or r['database_sha256']!=m['source_database_sha256']
            or r['inbox_sha256']!=sha(batch/'inbox.json') or r['inbox_sha256']!=m['inbox_sha256']):
            raise RuntimeError('Mobile archive receipt or frozen metadata changed')
        return r

    def _link(self,path,target):
        path.parent.mkdir(parents=True,exist_ok=True)
        tmp=path.with_name(path.name+'.mobile-link');tmp.unlink(missing_ok=True);tmp.symlink_to(target)
        os.replace(tmp,path);syncdir(path.parent)

    def _sync_network(self):
        # CAS also retains responses that were not admitted into a source batch.
        for source in (self.root/'network/bodies').glob('*'):
            if source.is_file():
                self.external_guard();copy_verified(source,self.archive/'network/bodies'/source.name,source.name)
        for source in (self.root/'network/partial').glob('*'):
            if source.is_file():
                self.external_guard();copy_verified(source,self.archive/'network/partial'/source.name)

    def sync_batch(self,batch,network_prepared=False):
        self.external_guard();batch=Path(batch)
        if self.receipt(batch):return
        m=json.loads((batch/'BATCH.json').read_bytes());number=m['batch'];final=self.final_batch(number)
        if sha(batch/'data/gitrx.sqlite')!=m['source_database_sha256'] or sha(batch/'inbox.json')!=m['inbox_sha256']:
            raise RuntimeError('Local completed batch changed before SSD transfer')
        if not network_prepared:self._sync_network()
        if (final/'BATCH.json').exists():
            if (sha(final/'BATCH.json')!=sha(batch/'BATCH.json') or sha(final/'data/gitrx.sqlite')!=m['source_database_sha256']
                or sha(final/'inbox.json')!=m['inbox_sha256']):raise RuntimeError('Existing immutable SSD batch conflicts with local outbox')
        else:
            prepared=self.archive/'batches'/('.mobile-publish-'+f'{number:05d}');prepared.mkdir(parents=True,exist_ok=True)
            for source in (batch/'artifacts').rglob('*'):
                if not source.is_file():continue
                self.external_guard();canonical=self.archive/'network/bodies'/source.name
                copy_verified(source,canonical,source.name)
                target=prepared/'artifacts'/source.relative_to(batch/'artifacts');target.parent.mkdir(parents=True,exist_ok=True)
                if target.exists():
                    if sha(target)!=source.name:raise RuntimeError('SSD staged artifact changed')
                else:os.link(canonical,target);syncdir(target.parent)
            for relative in ['data/gitrx.sqlite','inbox.json','BATCH.json']:
                self.external_guard();ensure_space(self.volume,(batch/relative).stat().st_size+256*1024**2)
                copy_verified(batch/relative,prepared/relative)
            self.external_guard()
            if final.exists():
                incident=self.archive/'incidents'/('pre-mobile-partial-'+str(time.time_ns()));incident.parent.mkdir(exist_ok=True)
                os.rename(final,incident);syncdir(final.parent)
            os.rename(prepared,final);syncdir(final.parent)
        self.external_guard()
        atomic(batch/'ARCHIVE-RECEIPT.json',{'format':'gitrx-mobile-archive-receipt-1','manifest_sha256':sha(batch/'BATCH.json'),
            'database_sha256':m['source_database_sha256'],'inbox_sha256':m['inbox_sha256'],'archive_path':str(final),'verified_epoch':time.time()})
        # External bytes are hash-verified before staging links are released.
        self._link(batch/'data/gitrx.sqlite',final/'data/gitrx.sqlite')
        artifacts=batch/'artifacts'
        if artifacts.is_dir() and not artifacts.is_symlink():
            old=batch/'artifacts.local-retired';os.rename(artifacts,old)
            artifacts.symlink_to(final/'artifacts',target_is_directory=True);syncdir(batch);shutil.rmtree(old)
        elif not artifacts.exists():artifacts.symlink_to(final/'artifacts',target_is_directory=True)
        atomic(self.root/'mobile-archive-status.json',{'last_archived_batch':number,'at_epoch':time.time(),'ssd_enabled':True})

    def sync_pending(self):
        self.external_guard()
        config=self.root/'config.json'
        if config.exists():copy_verified(config,self.archive/'mobile-configurations'/(sha(config)+'.json'))
        self._sync_network()
        for marker in sorted((self.root/'batches').glob('*/BATCH.json')):
            if not self.receipt(marker.parent):self.sync_batch(marker.parent,network_prepared=True)
        # Only completed-batch CAS files live here; active downloads stay in raw staging.
        for source in (self.root/'network/bodies').glob('*'):
            self.external_guard()
            if sha(self.archive/'network/bodies'/source.name)!=source.name:raise RuntimeError('SSD CAS verification failed before local release')
            source.unlink()
        for source in (self.root/'network/partial').glob('*'):
            self.external_guard()
            if sha(self.archive/'network/partial'/source.name)!=sha(source):raise RuntimeError('SSD failed-response prefix changed')
            source.unlink()
        self.last_scan=0

    def status(self):
        pending=sum(1 for p in (self.root/'batches').glob('*/BATCH.json') if not (p.parent/'ARCHIVE-RECEIPT.json').exists())
        return {'ssd_enabled':not self.disabled.exists(),'ssd_available':self.available(),'waiting_batches':pending,
            'local_work_bytes':self.last_usage,'local_work_cap_bytes':self.limit}
