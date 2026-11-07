"""Content-addressed local backups, explicit inventory pins and verified restores."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile
import time

FLOOR=20*1024**3

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
def safe(root,name):
    p=Path(name)
    if p.is_absolute() or '..' in p.parts or not p.parts:raise ValueError('Unsafe inventory path')
    result=root/p
    if not result.resolve().is_relative_to(root.resolve()):raise ValueError('Path escapes destination')
    return result
def space(path,needed=0):
    if shutil.disk_usage(path).free-needed<FLOOR:raise RuntimeError('20 GiB free-space floor')
def copy(source,target,clone=False):
    target.parent.mkdir(parents=True,exist_ok=True)
    space(target.parent,0 if clone else source.stat().st_size)
    if target.exists():raise ValueError('Refusing to overwrite file')
    if clone:
        if not hasattr(os,'uname') or os.uname().sysname!='Darwin':raise ValueError('Clone mode requires macOS clonefile support')
        library=ctypes.CDLL(None,use_errno=True)
        if library.clonefile(os.fsencode(source),os.fsencode(target),0)!=0:
            raise OSError(ctypes.get_errno(),'clonefile failed; choose a supported volume or a full-copy destination')
    else:
        with source.open('rb') as src,target.open('xb') as dest:
            shutil.copyfileobj(src,dest,1024*1024);dest.flush();os.fsync(dest.fileno())
    # Source read-only files must not make subsequent restore impossible.
    os.chmod(target,0o600)

def inventory(root,includes):
    root=Path(root).resolve();files={};links={};queue=[safe(root,n) for n in includes];seen=set()
    while queue:
        p=queue.pop();name=str(p.relative_to(root))
        if name in seen or any(part in ('__pycache__','.pytest_cache','.DS_Store','.venv') for part in p.parts):continue
        seen.add(name)
        if p.is_symlink():
            target=p.resolve()
            if not target.is_relative_to(root):raise ValueError('External symlink requires explicit archival mapping: '+name)
            links[name]=os.path.relpath(target,p.parent);queue.append(target)
        elif p.is_dir():queue.extend(p.iterdir())
        elif p.is_file():
            if p.name.endswith(('-wal','-shm','-journal')):continue
            files[name]=p
        else:raise ValueError('Missing or special input: '+name)
    return files,links

def create(root,destination,includes,mutable_sqlite=(),clone=False):
    root=Path(root).resolve();destination=Path(destination).resolve()
    if destination.exists() or destination.is_relative_to(root):raise ValueError('Choose a new backup directory outside source tree')
    files,links=inventory(root,includes)
    destination.mkdir(parents=True);os.chmod(destination,0o700)
    objects=destination/'objects';objects.mkdir();entries=[];inodes={};started=time.time()
    for number,(name,path) in enumerate(sorted(files.items()),1):
        before=path.stat();key=(before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)
        temporary=None;source=path;method='immutable-file'
        if name in mutable_sqlite:
            space(destination,before.st_size)
            temporary=destination/'sqlite-working-copy'
            src=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True);dst=sqlite3.connect(temporary)
            try:src.backup(dst)
            finally:dst.close();src.close()
            source=temporary;method='sqlite-online-backup'
        elif path.suffix in ('.sqlite','.db'):
            if any(Path(str(path)+suffix).exists() and Path(str(path)+suffix).stat().st_size for suffix in ('-wal','-journal')):
                raise ValueError('Immutable SQLite has a live journal: '+name)
        digest=inodes.get(key) if temporary is None else None
        if digest is None:digest=sha(source)
        target=objects/digest[:2]/digest
        if not target.exists():
            copy(source,target,clone)
            if sha(target)!=digest:raise ValueError('Copied object hash mismatch')
        if temporary is not None:temporary.unlink()
        else:
            after=path.stat()
            if (after.st_size,after.st_mtime_ns,after.st_ino)!=(before.st_size,before.st_mtime_ns,before.st_ino):raise ValueError('Source changed during backup: '+name)
            inodes[key]=digest
        entries.append({'path':name,'sha256':digest,'bytes':target.stat().st_size,'method':method})
        if number%10000==0:print(json.dumps({'backed_up_files':number,'total':len(files)}),flush=True)
    manifest={'format':'gitrx-recovery-1','source_root':str(root),'created_epoch':started,'clone_mode':clone,
              'independent_media_verified':False,'includes':includes,'files':entries,'symlinks':links,
              'elapsed_seconds':time.time()-started}
    raw=(json.dumps(manifest,sort_keys=True,indent=2)+'\n').encode();digest=hashlib.sha256(raw).hexdigest()
    with (destination/'manifest.json').open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    with (destination/'COMPLETE').open('x') as f:f.write(digest+'\n');f.flush();os.fsync(f.fileno())
    return {'manifest_sha256':digest,'files':len(entries),'logical_bytes':sum(r['bytes'] for r in entries),'elapsed_seconds':manifest['elapsed_seconds']}

def load(backup,expected):
    backup=Path(backup).resolve();raw=(backup/'manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=expected or (backup/'COMPLETE').read_text().strip()!=expected:raise ValueError('Backup inventory pin mismatch/incomplete backup')
    manifest=json.loads(raw)
    if manifest['format']!='gitrx-recovery-1':raise ValueError('Unsupported inventory format')
    seen=set()
    for row in manifest['files']:
        safe(backup/'validation-root',row['path'])
        if row['path'] in seen:raise ValueError('Duplicate inventory path')
        seen.add(row['path'])
        digest=row['sha256']
        if len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise ValueError('Invalid object hash')
    for name,target in manifest['symlinks'].items():
        link=safe(backup/'validation-root',name)
        if Path(target).is_absolute() or not (link.parent/target).resolve().is_relative_to((backup/'validation-root').resolve()):raise ValueError('Unsafe symlink')
        if name in seen:raise ValueError('Duplicate symlink path')
        seen.add(name)
    return backup,manifest

def audit(backup,expected):
    backup,manifest=load(backup,expected);checked=set()
    for row in manifest['files']:
        digest=row['sha256'];path=safe(backup,'objects/'+digest[:2]+'/'+digest)
        if digest not in checked:
            if path.stat().st_size!=row['bytes'] or sha(path)!=digest:raise ValueError('Backup object hash mismatch')
            checked.add(digest)
    return {'files':len(manifest['files']),'unique_objects_verified':len(checked),'manifest_sha256':expected}

def restore(backup,expected,destination,clone=False):
    backup,manifest=load(backup,expected);destination=Path(destination).resolve()
    if destination.exists():raise ValueError('Restore requires a new directory')
    # Verify the whole store before producing any restored evidence.
    audit(backup,expected);destination.mkdir(parents=True);os.chmod(destination,0o700)
    for row in manifest['files']:
        source=safe(backup,'objects/'+row['sha256'][:2]+'/'+row['sha256']);target=safe(destination,row['path'])
        copy(source,target,clone)
        if sha(target)!=row['sha256']:raise ValueError('Restored file hash mismatch')
    for name,target in manifest['symlinks'].items():
        link=safe(destination,name);link.parent.mkdir(parents=True,exist_ok=True);link.symlink_to(target)
    return {'restored_files':len(manifest['files']),'destination':str(destination),'source_root_mapping':{manifest['source_root']:str(destination)},'manifest_sha256':expected}

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    c=sub.add_parser('create');c.add_argument('--plan',type=Path,required=True);c.add_argument('--destination',type=Path,required=True);c.add_argument('--clone',action='store_true')
    for name in ('audit','restore'):
        c=sub.add_parser(name);c.add_argument('--backup',required=True);c.add_argument('--manifest-sha256',required=True)
        if name=='restore':c.add_argument('--destination',required=True);c.add_argument('--clone',action='store_true')
    a=p.parse_args()
    if a.command=='create':
        plan=json.loads(a.plan.read_bytes());result=create(plan['root'],a.destination,plan['includes'],plan.get('mutable_sqlite',[]),a.clone)
    elif a.command=='audit':result=audit(a.backup,a.manifest_sha256)
    else:result=restore(a.backup,a.manifest_sha256,a.destination,a.clone)
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
