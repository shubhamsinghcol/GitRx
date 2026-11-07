"""Step B coverage, Step C diagnostics, and frozen evidence checkpoint."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from .archive import LOINC, now
from .db import connect, dumps
from .parser import CONFIG, PARSER_VERSION, section_state
from .source_audit import audit_section_codes, verify_parsed_structure
from .availability import family_statuses,route_failure


def coverage(conn):
    run_id = conn.execute('SELECT MAX(id) FROM ingest_runs WHERE histories_complete=1').fetchone()[0]
    result = {'run_id':run_id,'families':[],'date_anomalies':[]}
    for family in conn.execute('SELECT * FROM families ORDER BY name'):
        versions = [dict(r) for r in conn.execute('''SELECT DISTINCT v.*,h.published_raw,h.published_date
          FROM versions v JOIN history_entries h ON h.version_id=v.id
          WHERE v.setid=? AND h.run_id=? ORDER BY v.version''',(family['setid'],run_id))]
        counts = Counter(v['state'] for v in versions)
        unavailable = []
        for v in versions:
            if v['state'] != 'unavailable':
                continue
            attempts = [dict(r) for r in conn.execute('SELECT * FROM retrievals WHERE version_id=? ORDER BY id',(v['id'],))]
            for a in attempts:
                a['headers'] = json.loads(a.pop('headers_json'))
                a['validation'] = json.loads(a['validation_json']) if a['validation_json'] else None
            position = 'oldest and newest' if len(versions)==1 else 'oldest' if v['version']==versions[0]['version'] else 'newest' if v['version']==versions[-1]['version'] else 'middle'
            unavailable.append({'version':v['version'],'position':position,'attempts':attempts})
        doc_codes = [dict(r) for r in conn.execute('''SELECT DISTINCT d.code,d.code_system FROM document_type_observations d
          JOIN retrievals r ON r.id=d.retrieval_id JOIN versions v ON v.id=r.version_id WHERE v.setid=? AND d.allowed=0''',(family['setid'],))]
        section_codes = [dict(r) for r in conn.execute('''SELECT DISTINCT s.code,s.code_system FROM section_code_observations s
          JOIN retrievals r ON r.id=s.retrieval_id JOIN versions v ON v.id=r.version_id WHERE v.setid=? AND s.expected=0''',(family['setid'],))]
        result['families'].append({'name':family['name'],'setid':family['setid'],'listed':len(versions),
           'downloaded':counts['downloaded'],'unavailable':counts['unavailable'],
           'identity_mismatch':counts['identity_mismatch'],'byte_conflict':counts['byte_conflict'],
           'pending':counts['pending'],'date_anomaly_versions':[v['version'] for v in versions if v['date_anomaly']],
           'unexpected_document_codes':doc_codes,'unexpected_section_codes':section_codes,
           'family_label_status':family_statuses(conn)[family['setid']],
           'unavailable_versions':unavailable,
           'retrieval_failure_meaning':'not retrievable by the recorded route(s); untested or unadmitted routes remain unknown'})
    for row in conn.execute('''SELECT f.name,l.setid,l.version AS lower_version,h.version AS higher_version,
      d.lower_published,d.higher_published FROM date_anomalies d
      JOIN versions l ON l.id=d.lower_version_id JOIN versions h ON h.id=d.higher_version_id
      JOIN families f ON f.setid=l.setid WHERE d.run_id=? ORDER BY l.setid,l.version,h.version''',(run_id,)):
        result['date_anomalies'].append(dict(row))
    result['totals']={field:sum(f[field] for f in result['families']) for field in
                      ('listed','downloaded','unavailable','identity_mismatch','byte_conflict','pending')}
    return result


def write_step_b(conn, output):
    report=coverage(conn)
    (output/'STEP-B.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    lines=['# Step B: full ingest of the accepted 20 families','',
      'A download counts only when ZIP/XML identity matches. Route failures mean not retrievable by recorded route(s), with no inferred text. All counts use source-listed versions, never maximum version numbers.','',
      '| Family | Listed | Downloaded | Not retrievable by recorded route(s) | Identity mismatch | Byte conflict | Date-anomaly versions | Unexpected document / section codes |',
      '|---|---:|---:|---:|---:|---:|---|---|']
    for f in report['families']:
        codes=' / '.join(', '.join('`'+c['code']+'`' for c in f[k]) or 'none' for k in ('unexpected_document_codes','unexpected_section_codes'))
        lines.append(f"| {f['name']} | {f['listed']} | {f['downloaded']} | {f['unavailable']} | {f['identity_mismatch']} | {f['byte_conflict']} | {', '.join(map(str,f['date_anomaly_versions'])) or 'none'} | {codes} |")
    t=report['totals']
    lines+=['',f"**Totals:** {t['listed']} listed; {t['downloaded']} downloaded; {t['unavailable']} not retrievable by recorded route(s); {t['identity_mismatch']} identity mismatch; {t['byte_conflict']} byte conflict; {t['pending']} pending.",
      '', '## Publication-date anomalies','', 'Numeric order is authoritative for previous/next. Every participant below is flagged in SQLite; publication dates are retained as observed.','']
    for a in report['date_anomalies']:
        lines.append(f"- {a['name']}: v{a['lower_version']} published {a['lower_published']}, followed numerically by v{a['higher_version']} published {a['higher_published']} — **date anomaly**.")
    lines+=['','## Every version not retrievable by recorded route(s)','',
      'Positions refer to the minimum/maximum listed numeric versions. Unlisted integer gaps are not listed downloads and are not counted as failures. The JSON report retains every attempt’s status, headers, timestamp, error, response hash and next retry time.','',
      '| Family | Version | Position | Latest HTTP status | Retrieved at (UTC) | Next retry eligible (UTC) |',
      '|---|---:|---|---|---|---|']
    for f in report['families']:
        for u in f['unavailable_versions']:
            a=u['attempts'][-1]
            lines.append(f"| {f['name']} | {u['version']} | {u['position']} | {a['status']} | {a['retrieved_at']} | {a['next_retry_at']} |")
    lines+=['','## Code observations','',
      'Document-type allowlist: `34391-3`, `45129-4`. Every observed document code is in `document_type_observations`; every section occurrence inspected is in `section_code_observations`. Section expectations use the 109-code FDA registry captured in research/section-code-registry.json. An unexpected code is a review flag, not a dropped document or section.','',
      'Full machine-readable evidence: [STEP-B.json](STEP-B.json). Raw bodies are stored under artifacts/sha256/ and referenced by SQLite and the report. Step A successes were reused only after rehashing and validating XML identity; fresh full histories and remaining versions were fetched in Step B.','']
    (output/'STEP-B.md').write_text('\n'.join(lines))
    return report


def freeze(conn, output):
    run_id=conn.execute('SELECT MAX(id) FROM ingest_runs WHERE histories_complete=1').fetchone()[0]
    versions=[]
    for v in conn.execute('SELECT DISTINCT v.* FROM versions v JOIN history_entries h ON h.version_id=v.id WHERE h.run_id=? ORDER BY setid,version',(run_id,)):
        item=dict(v)
        item['publication_observations']=[dict(r) for r in conn.execute('SELECT * FROM history_entries WHERE run_id=? AND version_id=? ORDER BY retrieval_id',(run_id,v['id']))]
        item['retrievals']=[dict(r) for r in conn.execute('SELECT * FROM retrievals WHERE version_id=? ORDER BY id',(v['id'],))]
        item['parse_runs']=[dict(r) for r in conn.execute('SELECT * FROM parse_runs WHERE version_id=? ORDER BY id',(v['id'],))]
        versions.append(item)
    manifest={'format':'gitrx-evidence-snapshot-v1','ingest_run_id':run_id,'versions':versions,
              'archive_route_sources':[dict(r) for r in conn.execute('SELECT * FROM archive_route_sources ORDER BY retrieval_id')],
              'parser':{'version':PARSER_VERSION,'config':CONFIG},
              'history_pages':[dict(r) for r in conn.execute('SELECT * FROM history_pages WHERE run_id=? ORDER BY setid,page',(run_id,))],
              'document_type_allowlist':[dict(r) for r in conn.execute('SELECT * FROM document_type_allowlist ORDER BY code')],
              'section_code_registry':[dict(r) for r in conn.execute('SELECT * FROM section_code_registry ORDER BY code')],
              'date_anomalies':[dict(r) for r in conn.execute('SELECT * FROM date_anomalies WHERE run_id=? ORDER BY lower_version_id,higher_version_id',(run_id,))]}
    registry_path=output.parent/'research/section-code-registry.json'
    manifest['section_registry_source']=json.loads(registry_path.read_text())
    # Pin metadata response bodies too, not just successful label ZIPs.
    ids={p['retrieval_id'] for p in manifest['history_pages']}
    manifest['history_retrievals']=[dict(r) for r in conn.execute('SELECT * FROM retrievals ORDER BY id') if r['id'] in ids]
    text=dumps(manifest)
    digest=hashlib.sha256(text.encode()).hexdigest()
    conn.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?,?)',(digest,now(),text))
    conn.commit()
    (output/f'snapshot-{digest}.json').write_text(text+'\n')
    (output/'SNAPSHOT-ID.txt').write_text(digest+'\n')
    return digest


def write_step_c(conn, root, output, snapshot):
    diagnostics=Counter()
    for row in conn.execute('SELECT diagnostics_json FROM parse_runs WHERE parser_version=?',(PARSER_VERSION,)):
        diagnostics.update(d['code'] for d in json.loads(row[0]))
    audit=verify_parsed_structure(conn,root,PARSER_VERSION)
    counts={table:conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] for table in
            ('parse_runs','sections','section_excerpts','narrative_blocks','narrative_tables')}
    report={'parser_version':PARSER_VERSION,'configuration':CONFIG,'snapshot':snapshot,'counts':counts,
       'document_statuses':dict(conn.execute('SELECT status,COUNT(*) FROM parse_runs WHERE parser_version=? GROUP BY status',(PARSER_VERSION,))),
       'section_statuses':dict(conn.execute('SELECT status,COUNT(*) FROM sections GROUP BY status')),
       'diagnostics':dict(diagnostics),'structural_count_mismatches':audit,
       'sqlite_integrity_check':conn.execute('PRAGMA integrity_check').fetchone()[0],
       'foreign_key_violations':[list(r) for r in conn.execute('PRAGMA foreign_key_check')]}
    (output/'STEP-C.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    lines=['# Step C: SQLite extraction and manual audit','',
      f"Parser: `{PARSER_VERSION}`. Archive snapshot: `{snapshot}`.",'',
      f"Document states: `{report['document_statuses']}`. Section-instance states: `{report['section_statuses']}`.",'',
      '| Stored representation | Count |','|---|---:|']
    lines += [f'| {name} | {count} |' for name,count in counts.items()]
    lines += ['', '## Diagnostics','', '| Diagnostic | Occurrences |','|---|---:|']
    lines += [f'| {name} | {count} |' for name,count in diagnostics.most_common()]
    lines += ['', f"Every parsed document was compared with the raw XML for section-instance and table counts. Mismatches: **{len(audit)}**. This structural check does not replace human semantic/layout audit.",
      '', f"SQLite integrity check: `{report['sqlite_integrity_check']}`; foreign-key violations: {len(report['foreign_key_violations'])}.",
      '', 'Five side-by-side source/extraction samples: [audit/index.html](audit/index.html). This is a local static audit artifact, not a product web UI. Each sample includes its exact raw ZIP hash, parser identity, instance ordinal/ancestry, source locator, direct narrative, ordered descendant narratives, separate highlights, and structured/flattened table data.',
      '', 'Full diagnostics and counts: [STEP-C.json](STEP-C.json). No phrase-history CLI, Git exporter, or MCP server was implemented in this run.']
    (output/'STEP-C.md').write_text('\n'.join(lines)+'\n')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path.cwd())
    parser.add_argument('--stage',choices=('b','c'),required=True)
    opts=parser.parse_args()
    root=opts.root.resolve();output=root/'reports';output.mkdir(exist_ok=True)
    conn=connect(root/'data/gitrx.sqlite')
    audit_section_codes(conn,root)
    result=write_step_b(conn,output)
    print(dumps(result['totals']),flush=True)
    if opts.stage=='c':
        snapshot=freeze(conn,output)
        result=write_step_c(conn,root,output,snapshot)
        print(dumps(result),flush=True)


if __name__=='__main__':
    main()
