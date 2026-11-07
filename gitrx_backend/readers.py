"""Read-only adapters with explicit release identity and bounded Git resources."""
from collections import OrderedDict
from pathlib import Path
from uuid import UUID
import gzip
import hashlib
import json
import sqlite3
import subprocess
import os
import signal

MAX_BLOB = 32 * 1024 * 1024

def sha(raw): return hashlib.sha256(raw).hexdigest()
def canonical_sid(sid): return str(UUID(sid))
def confined(base, relative):
    path=(base / relative).resolve()
    if not path.is_relative_to(base.resolve()): raise ValueError('Path escapes release root')
    return path

def read_bounded(path):
    with Path(path).open('rb') as f:
        raw=f.read(MAX_BLOB+1)
    if len(raw)>MAX_BLOB: raise ValueError('Input exceeds 32 MiB bound')
    return raw

class EvidenceReader:
    evidence_metadata_available = False
    evidence_metadata_sha256 = None

    def evidence(self, sid, version):
        sid = canonical_sid(sid)
        row = next((r for r in self.versions(sid) if r['version'] == version), None)
        if row is None: raise ValueError('Version outside selected release')
        if self.evidence_metadata_available:
            return self._evidence_metadata.evidence(sid, version, row['raw_sha256'])
        from .evidence_metadata import unknown_evidence
        return unknown_evidence(sid, version)

    def inventory(self, sid):
        sid = canonical_sid(sid)
        if self.evidence_metadata_available:
            return self._evidence_metadata.inventory(sid)
        from .evidence_metadata import unknown_inventory
        return unknown_inventory([r['version'] for r in self.versions(sid)])

    def evidence_citation(self, sid, version, citation):
        # Kept under one key so adapter-specific citation fields remain intact.
        return dict(citation, evidence=self.evidence(sid, version))


class SQLiteReader(EvidenceReader):
    def __init__(self, root):
        self.root=Path(root).resolve()
        self.pin=json.loads(read_bounded(self.root/'reports/NATIVE-INDEX.json'))
        # Stream the index hash: do not materialize arbitrarily large databases.
        h=hashlib.sha256()
        with (self.root/'native-query.sqlite').open('rb') as f:
            for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
        if h.hexdigest()!=self.pin['database_sha256']:raise ValueError('Native index hash mismatch')
        self.snapshot=self.pin['expanded_snapshot']
        self.conn=sqlite3.connect((self.root/'native-query.sqlite').as_uri()+'?mode=ro',uri=True)
        self.conn.row_factory=sqlite3.Row
        self.conn.execute('PRAGMA query_only=ON')
        if dict(self.conn.execute('SELECT key,value FROM meta'))['expanded_snapshot']!=self.snapshot:
            self.close();raise ValueError('Native index snapshot mismatch')
    def families(self):return [dict(r) for r in self.conn.execute('SELECT setid,title FROM families ORDER BY setid')]
    def versions(self,sid):
        sid=canonical_sid(sid)
        return [{'version':r['version'],'raw_sha256':r['raw_sha256'],'effective_date':r['effective_date'],
                 'publication_dates':json.loads(r['publication_dates'])} for r in self.conn.execute('SELECT * FROM versions WHERE setid=? ORDER BY version',(sid,))]
    def read(self,sid,version):
        sid=canonical_sid(sid)
        row=self.conn.execute('SELECT * FROM versions WHERE setid=? AND version=?',(sid,version)).fetchone()
        if row is None:raise ValueError('Version outside selected release')
        path=confined(self.root/'export-payloads',row['payload_path'])
        with gzip.open(path,'rb') as f:raw=f.read(MAX_BLOB+1)
        if len(raw)>MAX_BLOB or sha(raw)!=row['payload_sha256']:raise ValueError('Payload size/hash mismatch')
        return json.loads(raw)['sections'],self.evidence_citation(sid,version,{'snapshot':self.snapshot,'raw_sha256':row['raw_sha256'],'parse_key':row['parse_key']})
    def close(self):self.conn.close()
    def __enter__(self):return self
    def __exit__(self,*args):self.close()

class GitReader(EvidenceReader):
    """Bounded cached family histories and section blobs; no long-lived Git children.

    Bounded `git show` trades some throughput for predictable resource lifetime.
    Git's own pack cache is used during each invocation; a later worker adapter
    can implement the same interface without changing the query contract.
    """
    def __init__(self,root,meta_commit,snapshot,scope_path,cache_families=32):
        if not isinstance(cache_families,int) or not 1<=cache_families<=128:raise ValueError('Cache families must be 1..128')
        if len(meta_commit)!=40 or any(c not in '0123456789abcdef' for c in meta_commit):raise ValueError('Require full immutable meta commit')
        self.root=Path(root).resolve();self.cache_families=cache_families;self.cache=OrderedDict()
        self.blobs=OrderedDict();self.blob_bytes=0;self.checked_shards=set()
        self.meta_commit=meta_commit
        manifest=json.loads(self.git(self.root/'meta','show',meta_commit+':manifest.json'))
        if manifest['expanded_snapshot']!=snapshot:raise ValueError('Git snapshot mismatch')
        self.snapshot=snapshot;self.shards=manifest['shards']
        if set(self.shards)!=set('0123456789abcdef'):raise ValueError('Require sixteen pinned shards')
        self.uninstalled_shards=[p for p in sorted(self.shards) if not (self.root/'shards'/p).is_dir()]
        links={row.split('\t')[1]:row.split()[2] for row in self.git(self.root/'meta','ls-tree',meta_commit,'shards/').decode().splitlines()}
        for prefix,shard in self.shards.items():
            if links.get('shards/'+prefix)!=shard['head']:raise ValueError('Meta gitlink mismatch')
        scope_raw=read_bounded(scope_path)
        if sha(scope_raw)!=manifest['scope_sha256']:raise ValueError('Scope hash mismatch')
        self.names=json.loads(scope_raw)['records']
    @staticmethod
    def git(repo,*args):
        # Spool output into a temporary file so malicious/oversized blobs cannot
        # force unbounded PIPE buffering. No source-tree files are created.
        import tempfile
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            process=subprocess.Popen(['git','--no-replace-objects','-C',str(repo),*args],stdout=output,stderr=errors,start_new_session=True)
            try:
                code=process.wait(timeout=30)
                if code:
                    errors.seek(0)
                    raise subprocess.CalledProcessError(code,process.args,stderr=errors.read(65536))
            except BaseException:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                process.wait()
                raise
            output.seek(0);raw=output.read(MAX_BLOB+1)
        if len(raw)>MAX_BLOB:raise ValueError('Git result exceeds 32 MiB bound')
        return raw
    def families(self):return [{'setid':sid,'title':self.names[sid]['title']} for sid in sorted(self.names)]
    def versions(self,sid):
        sid=canonical_sid(sid)
        if sid not in self.names:return []
        if sid[0] in self.uninstalled_shards:
            raise ValueError('Required Git shard '+sid[0]+' is uninstalled; evidence is unavailable, not a negative result')
        if sid in self.cache:
            self.cache.move_to_end(sid);return [dict(r) for r in self.cache[sid]]
        p=sid[0];repo=self.root/'shards'/p
        if p not in self.checked_shards:
            if self.git(repo,'rev-parse','--is-shallow-repository').strip()!=b'false':raise ValueError('Shallow shard history is unsupported')
            self.checked_shards.add(p)
        raw=self.git(repo,'log',self.shards[p]['head'],'--format=%H%x00%B%x00','--',f'labels/{sid[:2]}/{sid}/metadata.json')
        parts=raw.decode().split('\0');rows=[]
        for i in range(0,len(parts)-1,2):
            commit,body=parts[i].strip(),parts[i+1]
            t=dict(line.split(': ',1) for line in body.splitlines() if ': ' in line)
            if t.get('Setid')!=sid:raise ValueError('Git history identity mismatch')
            rows.append({'version':int(t['Version']),'raw_sha256':t['Raw-SHA256'],
                         'effective_date':None if t['SPL-Effective-Date']=='not recorded' else t['SPL-Effective-Date'],
                         'publication_dates':[] if t['DailyMed-Publication-Date']=='not recorded' else t['DailyMed-Publication-Date'].split(', '),
                         'commit':commit})
        if len(rows)>10000 or len({r['version'] for r in rows})!=len(rows):raise ValueError('Ambiguous/oversized Git family history')
        rows.sort(key=lambda r:r['version']);self.cache[sid]=rows
        while len(self.cache)>self.cache_families or sum(len(json.dumps(v)) for v in self.cache.values())>4*1024*1024:self.cache.popitem(last=False)
        return [dict(r) for r in rows]
    def read(self,sid,version):
        sid=canonical_sid(sid)
        row=next((r for r in self.versions(sid) if r['version']==version),None)
        if row is None:raise ValueError('Version outside selected release')
        repo=self.root/'shards'/sid[0];base=f"{row['commit']}:labels/{sid[:2]}/{sid}/"
        key=(sid,version)
        if key in self.blobs:
            self.blobs.move_to_end(key);metadata,raw=self.blobs[key]
        else:
            metadata=json.loads(self.git(repo,'show',base+'metadata.json'))
            raw=self.git(repo,'show',base+'sections.json')
            if len(raw)<=8*1024*1024:
                self.blobs[key]=(metadata,raw);self.blob_bytes+=len(raw)
                while self.blob_bytes>8*1024*1024:
                    _,(_,old)=self.blobs.popitem(last=False);self.blob_bytes-=len(old)
        if (metadata['setid'],metadata['version'],metadata['raw_sha256'])!=(sid,version,row['raw_sha256']):raise ValueError('Git metadata identity mismatch')
        sections=json.loads(raw)
        return sections,self.evidence_citation(sid,version,{'snapshot':self.snapshot,'raw_sha256':row['raw_sha256'],'meta_commit':self.meta_commit,'label_commit':row['commit']})
    def close(self):self.cache.clear();self.blobs.clear();self.blob_bytes=0
    def __enter__(self):return self
    def __exit__(self,*args):self.close()
