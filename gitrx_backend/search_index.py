"""Optional immutable, release-pinned literal text projection; no source edits."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import unicodedata
from .config import open_reader

def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def build(config,target):
    target=Path(target).resolve();target.mkdir(parents=True,exist_ok=False)
    db=target/'search.sqlite';count=0
    with open_reader(config,'sqlite') as r:
        c=sqlite3.connect(db)
        try:
            c.executescript('CREATE TABLE meta(key PRIMARY KEY,value);CREATE TABLE sections(setid,version,position INTEGER,text TEXT,PRIMARY KEY(setid,position));')
            c.executemany('INSERT INTO meta VALUES(?,?)',[('snapshot',r.snapshot),('unicode',unicodedata.unidata_version),('format','gitrx-literal-projection-1')])
            for f in r.families():
                versions=r.versions(f['setid'])
                if not versions:continue
                version=versions[-1]['version'];sections,_=r.read(f['setid'],version)
                c.executemany('INSERT INTO sections VALUES(?,?,?,?)',[(f['setid'],version,i,(s['own_text'] or '').casefold()) for i,s in enumerate(sections)])
                count+=len(sections)
            c.commit()
            if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Projection integrity failure')
        finally:c.close()
        pin={'format':'gitrx-literal-projection-1','snapshot':r.snapshot,'unicode':unicodedata.unidata_version,'sha256':file_hash(db),'sections':count}
        (target/'PIN.json').write_text(json.dumps(pin,indent=2)+'\n')
    return pin

class Projection:
    def __init__(self,path,expected_hash,snapshot):
        path=Path(path).resolve()
        if file_hash(path)!=expected_hash:raise ValueError('Search projection hash mismatch')
        self.conn=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
        self.conn.execute('PRAGMA query_only=ON')
        meta=dict(self.conn.execute('SELECT key,value FROM meta'))
        if meta!={'snapshot':snapshot,'unicode':unicodedata.unidata_version,'format':'gitrx-literal-projection-1'}:
            self.conn.close();raise ValueError('Search projection release/normalization mismatch')
    def candidates(self,sid,version,phrase,offset):
        return [r[0] for r in self.conn.execute('SELECT position FROM sections WHERE setid=? AND version=? AND position>=? AND instr(text,?)>0 ORDER BY position',(sid,version,offset,phrase.casefold()))]
    def close(self):self.conn.close()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();print(json.dumps(build(a.config,a.output),indent=2))
if __name__=='__main__':main()
