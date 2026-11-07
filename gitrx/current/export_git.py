"""Deterministic context-free DailyMed evidence export via Git fast-import."""
import hashlib,json,subprocess
from datetime import datetime,timezone
from pathlib import Path
from .archive import artifact_bytes
from .db import dumps

EXPORT_VERSION='label-export-1'
AUTHOR='GitRx Archive <archive@gitrx.invalid>'

def ordered_versions(versions):
    groups={}
    for v in versions:groups.setdefault(v['setid'],[]).append(v)
    for rows in groups.values():rows.sort(key=lambda v:v['version'])
    result=[]
    while groups:
        def key(sid):
            v=groups[sid][0]
            dates=sorted(x['published_date'] for x in v['publication_observations'] if x['published_date'])
            return (dates[0] if dates else '9999-99-99',sid,v['version'])
        sid=min(groups,key=key);result.append(groups[sid].pop(0))
        if not groups[sid]:del groups[sid]
    return result

def export_git(c,root,repo,snapshot,release_tag,export_version=EXPORT_VERSION):
    root=Path(root);repo=Path(repo)
    if not release_tag or any(ch not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_./' for ch in release_tag):raise ValueError('Unsafe release tag')
    row=c.execute('SELECT manifest_json FROM snapshots WHERE id=?',(snapshot,)).fetchone()
    if row is None or hashlib.sha256(row[0].encode()).hexdigest()!=snapshot:raise ValueError('Evidence snapshot integrity')
    manifest=json.loads(row[0])
    if 'query_engine' in manifest:raise ValueError('Supply source evidence snapshot, never a derived context query snapshot')
    versions=ordered_versions(manifest['versions'])
    if not repo.exists():
        repo.mkdir(parents=True)
        subprocess.run(['git','init','--quiet','--object-format=sha1',str(repo)],check=True)
    def git(*args):return subprocess.run(['git','-C',str(repo),*args],capture_output=True,check=True).stdout.decode().strip()
    tag='refs/tags/'+release_tag;branch='refs/heads/build/'+release_tag
    refs=git('for-each-ref','--format=%(refname)').splitlines()
    if tag in refs or branch in refs:raise ValueError('Refusing to overwrite existing release/build refs')
    process=subprocess.Popen(['git','-C',str(repo),'fast-import','--quiet'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    stream=process.stdin
    def write(text):stream.write(text.encode())
    def data(body):
        if isinstance(body,str):body=body.encode()
        write('data '+str(len(body))+'\n');stream.write(body);write('\n')
    try:
        for mark,v in enumerate(versions,1):
            if v['state']!='downloaded' or not v['selected_sha256']:raise ValueError('Export requires explicitly retrieved versions')
            blob=artifact_bytes(c,root,v['selected_sha256'])
            if hashlib.sha256(blob).hexdigest()!=v['selected_sha256']:raise RuntimeError('STOP_RAW_HASH')
            parses=v['parse_runs']
            if len(parses)!=1:raise ValueError('One snapshot-pinned parse required')
            parse=parses[0];pubs=sorted({x['published_date'] for x in v['publication_observations'] if x['published_date']})
            valid=[]
            for date in pubs:
                try:valid.append(int(datetime.strptime(date,'%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp()))
                except ValueError:pass
            stamp=min(valid) if valid else 0
            message=(f"DailyMed {v['setid']} v{v['version']}\n\nSetid: {v['setid']}\nVersion: {v['version']}\n"
                f"DailyMed-Publication-Date: {', '.join(pubs) or 'not recorded'}\nSPL-Effective-Date: {v['effective_date'] or 'not recorded'}\n"
                f"Raw-SHA256: {v['selected_sha256']}\nParser-Version: {parse['parser_version']}\nExport-Version: {export_version}\n"
                f"Evidence-Snapshot: {snapshot}\nCommit-Date-Source: {'earliest observed DailyMed publication date' if valid else 'fixed epoch; source date missing'}\n")
            write(f'commit {branch}\nmark :{mark}\nauthor {AUTHOR} {stamp} +0000\ncommitter {AUTHOR} {stamp} +0000\n');data(message)
            if mark>1:write(f'from :{mark-1}\n')
            sections=[dict(r) for r in c.execute('''SELECT ordinal,parent_ordinal,ancestry_path,
                loinc_code,code_system,title,kind,own_text,source_xml,narrative_xml FROM sections
                WHERE parse_run_id=? ORDER BY ordinal''',(parse['id'],))]
            tables=[{'section_ordinal':r['section_ordinal'],'table_ordinal':r['table_ordinal'],
                     'scope':r['scope'],'structure':json.loads(r['structured_json']),
                     'flattened_text':r['flattened_text']} for r in c.execute('''SELECT s.ordinal section_ordinal,
                t.ordinal table_ordinal,t.scope,t.structured_json,t.flattened_text FROM narrative_tables t
                JOIN sections s ON s.id=t.section_id WHERE s.parse_run_id=? ORDER BY s.ordinal,t.ordinal''',(parse['id'],))]
            metadata={'setid':v['setid'],'version':v['version'],'DailyMed_publication_dates':pubs,
                'SPL_effective_date':v['effective_date'],'raw_sha256':v['selected_sha256'],
                'parser_version':parse['parser_version'],'export_version':export_version}
            base=f"labels/{v['setid'][:2]}/{v['setid']}/"
            for name,payload in (('metadata.json',metadata),('sections.json',sections),('tables.json',tables)):
                write('M 100644 inline '+base+name+'\n');data(dumps(payload)+'\n')
            write('\n')
        write('done\n');stream.close();process.stdin=None
        _,error=process.communicate()
        if process.returncode:raise RuntimeError('Git fast-import failed: '+error.decode()[:200])
    except BaseException:
        process.kill();process.wait();raise
    head=git('rev-parse',branch)
    git('update-ref',tag,head,'0'*40)
    # Checkout is intentionally unnecessary; object content is platform independent.
    return {'snapshot':snapshot,'export_version':export_version,'release_tag':release_tag,
            'commits':len(versions),'families':len({v['setid'] for v in versions}),
            'head':head,'tree':git('rev-parse',head+'^{tree}'),'repo':str(repo),
            'derived_context_fields_exported':0,
            'ordering':'numeric within family; next-version priority by publication date/setid/version; anomalies retained'}
