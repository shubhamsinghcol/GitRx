"""Build a synthetic offline archive end to end using installed runtime modules."""
from pathlib import Path
import argparse
import hashlib
import io
import json
import subprocess
import zipfile
from gitrx.current.archive import Fetcher, now, validate_label
from gitrx.current.db import connect, dumps
from gitrx.current.parser import persist_document
from gitrx.current.export_git import export_git
from gitrx_services.federation import Federation
from gitrx_services.export_payloads import prepare
from gitrx_services.native_query import build
from gitrx_services.shard_release import ShardView
from .config import open_reader
from .query import Query

def git(repo,*args):return subprocess.check_output(['git','-C',str(repo),*args]).decode().strip()
def run(output):
    output=Path(output).resolve()
    if output.exists():raise ValueError('Selftest requires a new output directory; nothing is overwritten')
    output.mkdir(parents=True);source=output/'source';(source/'reports').mkdir(parents=True)
    c=connect(source/'data/gitrx.sqlite');versions=[];names={}
    for prefix in '0123456789abcdef':
        sid=prefix+'0000000-0000-0000-0000-000000000000';names[sid]={'title':'Synthetic '+prefix}
        c.execute('INSERT INTO families VALUES(?,?,?)',(sid,names[sid]['title'],'{}'))
        for n in ([1,2] if prefix=='a' else [1]):
            vid=c.execute('INSERT INTO versions(setid,version) VALUES(?,?)',(sid,n)).lastrowid
            xml=f'<document xmlns="urn:hl7-org:v3"><setId root="{sid}"/><versionNumber value="{n}"/><code code="34391-3" codeSystem="2.16.840.1.113883.6.1"/><effectiveTime value="20260101"/><component><structuredBody><component><section><code code="34066-1" codeSystem="2.16.840.1.113883.6.1"/><title>WARNING</title><text><paragraph>Synthetic warning {n}</paragraph></text></section></component></structuredBody></component></document>'
            buffer=io.BytesIO()
            with zipfile.ZipFile(buffer,'w') as z:z.writestr('label.xml',xml)
            rid=Fetcher(c,source,None).record({'url':'https://example.invalid/synthetic','retrieved_at':now(),'status':200,'headers':[]},buffer.getvalue(),'label',vid)
            checked=validate_label(c,source,rid,sid,n)
            raw=c.execute('SELECT artifact_sha256 FROM retrievals WHERE id=?',(rid,)).fetchone()[0]
            c.execute("UPDATE versions SET state='downloaded',selected_sha256=?,xml_member=?,effective_date=? WHERE id=?",(raw,checked['xml_member'],checked['effective_date'],vid));c.commit()
            persist_document(c,source,c.execute('SELECT * FROM versions WHERE id=?',(vid,)).fetchone(),{'34066-1'})
            v=dict(c.execute('SELECT * FROM versions WHERE id=?',(vid,)).fetchone())
            v['publication_observations']=[{'published_date':'2026-01-01','published_raw':'2026-01-01'}]
            v['retrievals']=[dict(r) for r in c.execute('SELECT * FROM retrievals WHERE version_id=?',(vid,))]
            v['parse_runs']=[dict(r) for r in c.execute('SELECT * FROM parse_runs WHERE version_id=?',(vid,))];versions.append(v)
    raw=dumps({'versions':versions});snapshot=hashlib.sha256(raw.encode()).hexdigest()
    c.execute('INSERT INTO snapshots VALUES(?,?,?)',(snapshot,now(),raw));c.commit();c.close()
    base=Federation([('fixture',source,snapshot)]);base.project=output
    scope=output/'scope.json';scope.write_text(dumps({'records':names}))
    base.manifest=json.loads(base.raw);base.manifest['scope_sha256']=hashlib.sha256(scope.read_bytes()).hexdigest()
    base.manifest['sources']={'fixture':{'root':str(source),'snapshot':snapshot,'database_sha256':hashlib.sha256((source/'data/gitrx.sqlite').read_bytes()).hexdigest()}}
    base.raw=dumps(base.manifest);base.snapshot=hashlib.sha256(base.raw.encode()).hexdigest()
    base.parse_versions={('fixture',int(v['parse_runs'][0]['id'].split(':')[1])):int(v['id'].split(':')[1]) for v in base.manifest['versions']}
    root=output/'release';(root/'reports').mkdir(parents=True)
    (root/'reports/EXPANDED-SELECTION.json').write_text(base.raw+'\n')
    prepare(base,root/'export-payloads');build(root,scope)
    export=output/'export';shards={}
    for prefix in '0123456789abcdef':
        view=ShardView(base,prefix)
        shards[prefix]=export_git(view,output,export/'shards'/prefix,view.snapshot,'selftest')
    meta=export/'meta';meta.mkdir();git(meta,'init','-q')
    (meta/'manifest.json').write_text(dumps({'expanded_snapshot':base.snapshot,'scope_sha256':base.manifest['scope_sha256'],'shards':shards}))
    git(meta,'add','manifest.json')
    for prefix,row in shards.items():git(meta,'update-index','--add','--cacheinfo','160000,'+row['head']+',shards/'+prefix)
    git(meta,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','Synthetic release')
    config=output/'backend.json';config.write_text(dumps({'format':'gitrx-backend-config-1','backend':'git','snapshot':base.snapshot,'meta_commit':git(meta,'rev-parse','HEAD'),'export_root':'export','release_root':'release','scope_path':'scope.json'}))
    answers=[]
    for backend in ('git','sqlite'):
        with open_reader(config,backend) as reader:
            q=Query(reader);answers.append(q.show('a0000000-0000-0000-0000-000000000000',2)['sections'])
            assert len(q.families(limit=200)['families'])==16
            assert 'Synthetic warning 2' in answers[-1][0]['text']
    assert answers[0]==answers[1]
    for conn,_ in base.sources.values():conn.close()
    result={'synthetic':True,'network_requests':0,'families':16,'versions':17,'adapters_equal':True,'config':str(config)}
    (output/'SELFTEST.json').write_text(json.dumps(result,indent=2)+'\n');return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',required=True,type=Path)
    print(json.dumps(run(p.parse_args().output),indent=2))
if __name__=='__main__':main()
