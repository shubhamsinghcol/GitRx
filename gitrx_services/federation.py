"""Packaged read-only federation adapter; stage scripts remain historical drivers."""
from pathlib import Path
import hashlib, json, sqlite3
from gitrx.current.db import dumps
from gitrx_services.io import readonly

class One:
 def __init__(self,row):self.row=row
 def fetchone(self):return self.row
class Federation:
 def __init__(self,sources):
  self.sources={};versions=[];seen=set();self.manifests={}
  for prefix,root,snapshot in sources:
   root=Path(root);c=readonly(root/'data/gitrx.sqlite');self.sources[prefix]=(c,root)
   record=c.execute('SELECT manifest_json FROM snapshots WHERE id=?',(snapshot,)).fetchone()
   if record is None or hashlib.sha256(record[0].encode()).hexdigest()!=snapshot:raise RuntimeError('SOURCE_SNAPSHOT_HASH')
   source=json.loads(record[0]);self.manifests[prefix]=snapshot
   for v in source['versions']:
    if (v['setid'],v['version']) in seen:raise RuntimeError('CONFLICTING_FEDERATED_IDENTITY')
    seen.add((v['setid'],v['version']));v=dict(v);v['id']=prefix+':'+str(v['id'])
    v['parse_runs']=[dict(p,id=prefix+':'+str(p['id'])) for p in v['parse_runs']]
    if v['state']=='downloaded':versions.append(v)
  self.raw=dumps({'format':'gitrx-federated-evidence-1','sources':self.manifests,'versions':versions})
  self.snapshot=hashlib.sha256(self.raw.encode()).hexdigest()
 def execute(self,sql,parameters=()):
  if 'SELECT manifest_json FROM snapshots' in sql:
   return One((self.raw,)) if parameters[0]==self.snapshot else One(None)
  if 'SELECT path,artifact_class FROM artifacts' in sql:
   found=[]
   for c,root in self.sources.values():
    row=c.execute(sql,parameters).fetchone()
    if row:found.append({'path':str(root/row['path']),'artifact_class':row['artifact_class']})
   if not found:return One(None)
   return One(found[0])
  if 'parse_run_id=?' in sql:
   prefix,ident=str(parameters[0]).split(':',1);c,root=self.sources[prefix]
   return c.execute(sql,(int(ident),*parameters[1:]))
  raise ValueError('Unsupported federated query; do not silently route: '+sql[:80])


def size(path):return sum(p.stat().st_size for p in Path(path).rglob("*") if p.is_file())


def checkpoint_pins(root):
    """Use only closed, verified DBs; never inspect source text/classifications."""
    with readonly(root / 'control.sqlite') as c:
        pins = [(root / 'tranches' / f"{r['number']:04d}", r['snapshot_id'],
                 json.loads(r['counts_json'])['database_sha256'])
                for r in c.execute("SELECT * FROM tranches WHERE status='verified'")]
        pins += [(Path(r['root']), r['snapshot_id'],
                  json.loads(r['counts_json'])['database_sha256'])
                 for r in c.execute("SELECT * FROM retry_tranches WHERE status='verified'")]
    return pins

