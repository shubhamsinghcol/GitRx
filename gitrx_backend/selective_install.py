"""Explicit local selective Git-shard installation; no publisher or remote access."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
from .config import open_reader
from .query import Query


def install(config,setids,target):
    source=Path(config).resolve();cfg=json.loads(source.read_bytes());target=Path(target).resolve()
    for key in ('release_root','export_root'):
        if key in cfg and target.is_relative_to((source.parent/cfg[key]).resolve()):
            raise ValueError('Selective installation must be outside evidence/export roots')
    if target.exists():raise ValueError('Selective installation requires a new destination')
    with open_reader(source,'git') as reader:
        plan=Query(reader).installation_plan(setids)
        root=reader.root
        # Validate every selected shard before creating the destination.
        for shard in plan['required_shards']:
            reader.git(root/'shards'/shard['prefix'],'cat-file','-e',shard['commit']+'^{commit}')
        target.mkdir(parents=True)
        try:
            for name,pin in [('meta',cfg['meta_commit'])]+[('shards/'+r['prefix'],r['commit']) for r in plan['required_shards']]:
                destination=target/name;destination.parent.mkdir(parents=True,exist_ok=True)
                subprocess.run(['git','clone','--quiet','--no-hardlinks','--no-checkout','--',str(root/name),str(destination)],check=True)
                subprocess.run(['git','-C',str(destination),'checkout','--quiet','--detach',pin],check=True)
            shutil.copyfile((source.parent/cfg['scope_path']).resolve(),target/'scope.json')
            output={'format':cfg['format'],'backend':'git','export_root':'.','snapshot':cfg['snapshot'],
                    'meta_commit':cfg['meta_commit'],'scope_path':'scope.json'}
            if 'evidence_metadata' in cfg:
                shutil.copyfile((source.parent/cfg['evidence_metadata']).resolve(),target/'evidence.sqlite')
                output.update(evidence_metadata='evidence.sqlite',evidence_metadata_sha256=cfg['evidence_metadata_sha256'])
            (target/'backend.json').write_text(json.dumps(output,indent=2)+'\n')
            (target/'INSTALLATION.json').write_text(json.dumps(plan,indent=2)+'\n')
            with open_reader(target/'backend.json','git') as installed:
                for sid in plan['selected_setids']:
                    versions=installed.versions(sid)
                    if versions:installed.read(sid,versions[-1]['version'])
        except BaseException:
            # Retain failed installation for diagnosis; never delete user data.
            (target/'INCOMPLETE').write_text('Installation failed; not a ready reader.\n');raise
    return target/'backend.json'


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--setid',action='append',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    print(install(a.config,a.setid,a.output))

if __name__=='__main__':main()
