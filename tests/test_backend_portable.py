"""Self-contained portable release fixture: no workspace data or frozen imports."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import pytest
from gitrx_backend.readers import GitReader, SQLiteReader
from gitrx_backend.config import open_reader
from gitrx_backend.query import Query
from gitrx_backend.cli import main

SID='aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
def sha(raw):return hashlib.sha256(raw).hexdigest()
def git(repo,*args):return subprocess.check_output(['git','-C',str(repo),*args],stderr=subprocess.DEVNULL).decode().strip()
def commit(repo,message):
    git(repo,'add','.')
    git(repo,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm',message)
    return git(repo,'rev-parse','HEAD')
@pytest.fixture
def release(tmp_path):
    root=tmp_path/'relocated release';root.mkdir()
    (root/'reports').mkdir();(root/'export-payloads').mkdir()
    scope=json.dumps({'records':{SID:{'title':'Synthetic label'}}}).encode();(root/'scope.json').write_bytes(scope)
    c=sqlite3.connect(root/'native-query.sqlite')
    c.executescript('CREATE TABLE meta(key,value);CREATE TABLE families(setid,title);CREATE TABLE versions(setid,version,raw_sha256,effective_date,publication_dates,payload_path,payload_sha256,parse_key);')
    snapshot='f'*64;c.execute('INSERT INTO meta VALUES(?,?)',('expanded_snapshot',snapshot));c.execute('INSERT INTO families VALUES(?,?)',(SID,'Synthetic label'))
    shards={};export=root/'export'
    for p in '0123456789abcdef':
        repo=export/'shards'/p;repo.mkdir(parents=True);git(repo,'init','-q')
        (repo/'README').write_text('Synthetic shard')
        commit(repo,'initialize fixture')
        if p=='a':
            for v,text in [(1,'renal warning'),(2,'renal warning changed')]:
                sections=[dict(ordinal=n,parent_ordinal=None,ancestry_path='/',loinc_code='34066-1',code_system='loinc',title='Warning',own_text=text) for n in (1,2)]
                raw=json.dumps({'sections':sections}).encode();(root/f'export-payloads/{v}.gz').write_bytes(gzip.compress(raw))
                source=sha(str(v).encode());c.execute('INSERT INTO versions VALUES(?,?,?,?,?,?,?,?)',(SID,v,source,None,json.dumps(['2026-01-01']),f'{v}.gz',sha(raw),str(v)))
                base=repo/'labels'/SID[:2]/SID;base.mkdir(parents=True,exist_ok=True)
                (base/'sections.json').write_text(json.dumps(sections));(base/'metadata.json').write_text(json.dumps({'setid':SID,'version':v,'raw_sha256':source}))
                commit(repo,f'label\n\nSetid: {SID}\nVersion: {v}\nRaw-SHA256: {source}\nSPL-Effective-Date: not recorded\nDailyMed-Publication-Date: 2026-01-01\n')
        shards[p]={'head':git(repo,'rev-parse','HEAD')}
    c.commit();c.close();(root/'reports/NATIVE-INDEX.json').write_text(json.dumps({'database_sha256':sha((root/'native-query.sqlite').read_bytes()),'expanded_snapshot':snapshot}))
    meta=export/'meta';meta.mkdir();git(meta,'init','-q')
    (meta/'manifest.json').write_text(json.dumps({'expanded_snapshot':snapshot,'scope_sha256':sha(scope),'shards':shards}))
    git(meta,'add','manifest.json')
    for p,shard in shards.items():git(meta,'update-index','--add','--cacheinfo','160000,'+shard['head']+',shards/'+p)
    # commit()'s git add . keeps the pre-staged gitlinks.
    git(meta,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture release')
    head=git(meta,'rev-parse','HEAD')
    config=root/'backend.json';config.write_text(json.dumps({'format':'gitrx-backend-config-1','snapshot':snapshot,'release_root':'.','export_root':'export','scope_path':'scope.json','meta_commit':head}))
    return config

def test_adapters_exact_records_citations_filters_diff_and_resumable_search(release):
    outputs=[]
    for backend in ('sqlite','git'):
        with open_reader(release,backend) as reader:
            q=Query(reader);a=q.show(SID,2,code='34066-1',ordinal=1,max_chars=5)
            assert a['sections'][0]['text']=='renal' and a['sections'][0]['next_offset']==5
            assert q.show(SID,2,ordinal=1,max_chars=100,offset=5)['sections'][0]['text']==' warning changed'
            assert len(q.show(SID,2)['sections'])==2
            assert q.show(SID,2,code='absent')['sections']==[]
            assert 'changed' in q.diff(SID,1,2,1)['diff']
            first=q.search('warning',limit=1);assert first['partial']
            second=q.search('warning',limit=1,family_offset=first['next_family_offset'],section_offset=first['next_section_offset'])
            assert [first['hits'][0]['ordinal'],second['hits'][0]['ordinal']]==[1,2]
            with pytest.raises(ValueError):q.show(SID,99)
            outputs.append(a['sections'])
    assert outputs[0]==outputs[1]

def test_pin_corruption_configuration_and_path_boundaries(release):
    config=json.loads(release.read_text());config['snapshot']='0'*64
    bad=release.with_name('bad.json');bad.write_text(json.dumps(config))
    for backend in ('git','sqlite'):
        with pytest.raises(ValueError,match='snapshot'):open_reader(bad,backend)
    with (release.parent/'native-query.sqlite').open('ab') as f:f.write(b'tamper')
    with pytest.raises(ValueError,match='hash'):open_reader(release,'sqlite')
    (release.parent/'scope.json').write_text('{}')
    with pytest.raises(ValueError,match='Scope hash'):open_reader(release,'git')

def test_cli_explicit_config_from_other_directory(release,tmp_path,monkeypatch,capsys):
    monkeypatch.chdir(tmp_path);assert main(['--config',str(release),'--backend','git','show',SID,'2','--ordinal','1'])==0
    assert json.loads(capsys.readouterr().out)['version']==2
    assert main(['--config',str(release),'show',SID,'99'])==2
    assert json.loads(capsys.readouterr().err)['evidence_answer'] is False

def test_git_reads_pinned_commit_when_branch_moves_and_keeps_cache_bounded(release):
    config=json.loads(release.read_text());repo=release.parent/'export/shards/a'
    base=repo/'labels'/SID[:2]/SID
    (base/'sections.json').write_text('[]');commit(repo,'unrelated later branch mutation')
    with open_reader(release,'git') as reader:
        assert len(reader.read(SID,2)[0])==2
        assert reader.read(SID,2)[0][0]['own_text']=='renal warning changed'
        assert reader.blob_bytes<=8*1024*1024 and len(reader.cache)<=reader.cache_families
    assert reader.blob_bytes==0

def test_shallow_git_history_is_refused(release):
    repo=release.parent/'export/shards/a'
    (repo/'.git/shallow').write_text(git(repo,'rev-parse','HEAD')+'\n')
    with open_reader(release,'git') as reader:
        with pytest.raises(ValueError,match='Shallow'):reader.versions(SID)

def test_sql_payload_path_escape_is_refused(release):
    root=release.parent
    with sqlite3.connect(root/'native-query.sqlite') as c:c.execute("UPDATE versions SET payload_path='../outside.gz'")
    pin=json.loads((root/'reports/NATIVE-INDEX.json').read_text());pin['database_sha256']=sha((root/'native-query.sqlite').read_bytes())
    (root/'reports/NATIVE-INDEX.json').write_text(json.dumps(pin))
    with open_reader(release,'sqlite') as reader:
        with pytest.raises(ValueError,match='escapes'):reader.read(SID,1)
