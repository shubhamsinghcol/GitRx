"""Explicit one-time migration of paused mutable work into a local workspace."""
import json
from pathlib import Path
import shutil
import sqlite3
import os
from .jobs import atomic,sha
from .backfill_staging import copy_verified
from .mobile_archive import MobileArchive


def sqlite_snapshot(source,target):
    source=Path(source).resolve();target=Path(target);target.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as c:
        if c.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise RuntimeError('Migration source database integrity failure')
        with sqlite3.connect(target) as out:
            c.backup(out)
            if out.execute('PRAGMA integrity_check').fetchall()!=[('ok',)]:raise RuntimeError('Local database copy integrity failure')


def prepare(original_config,local_root):
    original_config=Path(original_config);old=json.loads(original_config.read_bytes());archive=Path(old['workspace']);local=Path(local_root).absolute()
    if (local/'MOBILE-READY.json').exists():raise RuntimeError('Mobile workspace already prepared; use retained configuration')
    local.mkdir(parents=True,exist_ok=True)
    # Keep exact immutable inputs. The derived local job has its own explicit pin.
    census=Path(old['census_workspace']);cache=local/'inputs/census'
    copy_verified(census/'manifest.json',cache/'manifest.json',old['census_manifest_sha256'])
    census_manifest=json.loads((cache/'manifest.json').read_bytes())
    copy_verified(census/'census.sqlite',cache/'census.sqlite',census_manifest['census_index_sha256'])
    new=dict(old,workspace=str(local),census_workspace=str(cache))
    if old.get('seed_sample'):
        source=Path(old['seed_sample']);sample=cache/'capacity/sample.json'
        copy_verified(source,sample,old['seed_sample_sha256'])
        sqlite_snapshot(source.parent/'source/data/gitrx.sqlite',sample.parent/'source/data/gitrx.sqlite')
        new['seed_sample']=str(sample)
    storage=json.loads((archive/'LOCAL-STORAGE-POLICY.json').read_bytes())
    new['mobile_archive']={'archive_workspace':str(archive),'max_local_bytes':40*1024**3,
        'local_work_roots':[storage['local_root'],storage['raw_staging_root'],str(Path(storage['local_root']).parent/'processing-spool')]}
    storage.update(version=storage.get('version',1)+1,archive_workspace=str(archive))
    atomic(local/'LOCAL-STORAGE-POLICY.json',storage)
    for name in ['TRANSPORT-POLICY.json','PROCESSING-POLICY.json']:
        copy_verified(archive/name,local/name)
    sqlite_snapshot(archive/'control.sqlite',local/'control.sqlite')
    sqlite_snapshot(archive/'network/ledger.sqlite',local/'network/ledger.sqlite')
    for folder in ['bodies','partial']:(local/'network'/folder).mkdir(parents=True,exist_ok=True)
    for source in sorted((archive/'batches').glob('*')):
        if not source.is_dir() or not source.name.isdigit():continue
        dest=local/'batches'/source.name;dest.mkdir(parents=True,exist_ok=True)
        if (source/'BATCH.json').exists():
            m=json.loads((source/'BATCH.json').read_bytes())
            if sha(source/'data/gitrx.sqlite')!=m['source_database_sha256'] or sha(source/'inbox.json')!=m['inbox_sha256']:
                raise RuntimeError('Immutable source batch changed during mobile preparation')
            for name in ['BATCH.json','inbox.json']:copy_verified(source/name,dest/name)
            (dest/'data').mkdir(exist_ok=True);(dest/'data/gitrx.sqlite').symlink_to(source/'data/gitrx.sqlite')
            (dest/'artifacts').symlink_to(source/'artifacts',target_is_directory=True)
            atomic(dest/'ARCHIVE-RECEIPT.json',{'format':'gitrx-mobile-archive-receipt-1','manifest_sha256':sha(dest/'BATCH.json'),
                'database_sha256':m['source_database_sha256'],'inbox_sha256':m['inbox_sha256'],'archive_path':str(source),'migration_verified':True})
        else:
            if (source/'data/gitrx.sqlite').exists():sqlite_snapshot(source/'data/gitrx.sqlite',dest/'data/gitrx.sqlite')
            artifacts=source/'artifacts'
            if artifacts.is_symlink():(dest/'artifacts').symlink_to(artifacts.resolve(),target_is_directory=True)
            elif artifacts.exists():shutil.copytree(artifacts,dest/'artifacts',copy_function=lambda s,d:copy_verified(s,d))
            for name in ['RAW-HANDOFF.json','RAW-HANDOFF-STARTED.json']:
                if (source/name).exists():copy_verified(source/name,dest/name)
    # The former buffer pins the old config. A fresh operational queue reuses
    # the same hash-checked HTTP ledger/CAS without reinterpreting old records.
    buffer=Path(storage['raw_staging_root'])/'buffer'
    if buffer.exists():
        preserved=buffer.with_name('buffer-before-mobile')
        if preserved.exists():raise RuntimeError('Ambiguous prior mobile buffer migration')
        os.rename(buffer,preserved)
    config=local/'config.json';atomic(config,new)
    for name in ['PLAN.json','status.json']:
        if (archive/name).exists():copy_verified(archive/name,local/name)
    atomic(local/'MOBILE-READY.json',{'format':'gitrx-mobile-ready-1','config_sha256':sha(config),
        'original_config_sha256':sha(original_config),'archive_workspace':str(archive)})
    MobileArchive(new).guard()
    return config
