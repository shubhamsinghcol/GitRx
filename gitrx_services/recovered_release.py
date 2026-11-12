"""Offline bulk-member admission and additive release selection; no source writes."""
from collections import Counter
from pathlib import Path
import argparse
import hashlib
import io
import json
import os
import sqlite3
import zipfile

from gitrx.current.archive import NS, LOINC, put_bytes, read_xml, now
from gitrx.current.db import connect, dumps
from gitrx.current.content_storage import migrate
from gitrx.current.parser import load_registry, persist_document, CONFIG, PARSER_VERSION
from gitrx_services.io import readonly, ensure_space
from .federation import One


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def verify_zip(body, expected):
    if hashlib.sha256(body).hexdigest() != expected['ZIP_sha256']:
        raise ValueError('Candidate ZIP hash mismatch')
    with zipfile.ZipFile(io.BytesIO(body)) as z:
        candidates = []
        for member in z.infolist():
            if not member.filename.lower().endswith('.xml'):
                continue
            if member.file_size > 100 * 1024**2:
                raise ValueError('XML size bound')
            xml = z.read(member)
            d = read_xml(xml)
            sid, number = d.find('s:setId', NS), d.find('s:versionNumber', NS)
            if sid is not None and number is not None:
                candidates.append((member.filename, xml, d, sid.get('root', '').lower(), int(number.get('value'))))
    if len(candidates) != 1:
        raise ValueError('Ambiguous XML identity')
    name, xml, doc, sid, version = candidates[0]
    effective = doc.find('s:effectiveTime', NS)
    effective = effective.get('value') if effective is not None else None
    if (sid, version, effective) != (expected['setid'], int(expected['version']), expected['effective_time']):
        raise ValueError('Candidate XML setid/version/effectiveTime mismatch')
    if name != expected['xml_member'] or hashlib.sha256(xml).hexdigest() != expected['XML_sha256']:
        raise ValueError('Candidate XML member/hash mismatch')
    code = doc.find('s:code', NS)
    if code is None or code.get('codeSystem') != LOINC or code.get('code') not in {'34391-3', '45129-4'}:
        raise ValueError('Unconfirmed human-Rx document type')
    return name, effective, doc


def link_evidence(c, source, root, expected):
    source, root = Path(source), Path(root)
    if sha(source) != expected:
        raise ValueError('Retained response hash mismatch')
    dest = root / 'artifacts/sha256' / expected[:2] / expected
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        os.link(source, dest)
    if sha(dest) != expected:
        raise ValueError('Linked evidence hash mismatch')
    c.execute('INSERT OR IGNORE INTO artifacts(sha256,bytes,path,artifact_class) VALUES(?,?,?,?)',
              (expected, source.stat().st_size, str(dest.relative_to(root)), 'retrieved-evidence'))


def admit(project, root):
    project, root = Path(project), Path(root)
    ensure_space(root.parent, 1024**3)
    report = root / 'reports/ADMISSION.json'
    if report.exists():
        result = json.loads(report.read_bytes())
        if sha(root / 'data/gitrx.sqlite') != result['database_sha256']:
            raise RuntimeError('Completed supplemental database changed')
        return result
    source = project / 'reports/recovery-attempt-20261103'
    candidates = json.loads((source / 'VERIFIED-CANDIDATES.json').read_bytes())
    if len(candidates) != 15 or len({(x['setid'], int(x['version'])) for x in candidates}) != 15:
        raise ValueError('Expected exactly 15 distinct retained targets')
    # Validate all candidates before admitting any.
    verified = {x['setid']: verify_zip((project / x['ZIP_path']).read_bytes(), x) for x in candidates}
    ledger_path = source / 'evidence/requests.jsonl'
    ledger = [json.loads(x) for x in ledger_path.read_text().splitlines()]
    root.mkdir(parents=True, exist_ok=True)
    (root / 'reports').mkdir(exist_ok=True)
    if not (root / 'research').exists():
        (root / 'research').symlink_to(project / 'research', target_is_directory=True)
    c = connect(root / 'data/gitrx.sqlite')
    migrate(c)
    c.execute('CREATE TABLE IF NOT EXISTS bulk_member_sources(retrieval_id INTEGER PRIMARY KEY REFERENCES retrievals(id),provenance_json TEXT NOT NULL)')
    c.execute('CREATE TABLE IF NOT EXISTS release1_parse_selection(version_id INTEGER PRIMARY KEY,parse_id INTEGER NOT NULL)')
    scope = json.loads((project / 'cohorts/release1/scope.json').read_bytes())
    names = scope['records'] if isinstance(scope['records'], dict) else {r['setid']: r for r in scope['records']}
    registry = load_registry(c, root)
    imported, versions = {}, []
    for x in sorted(candidates, key=lambda r: (r['setid'], int(r['version']))):
        ensure_space(root, 128 * 1024**2)
        sid, version = x['setid'], int(x['version'])
        body = (project / x['ZIP_path']).read_bytes()
        member, effective, doc = verify_zip(body, x)
        metadata = names.get(sid, {})
        title = next((metadata[k] for k in ('title', 'name', 'drug_name') if metadata.get(k)), sid)
        c.execute('INSERT OR IGNORE INTO families VALUES(?,?,?)', (sid, title, dumps(metadata)))
        c.execute('INSERT OR IGNORE INTO family_label_status(setid) VALUES(?)', (sid,))
        c.execute("INSERT OR IGNORE INTO versions(setid,version) VALUES(?,?)", (sid, version))
        v = c.execute('SELECT * FROM versions WHERE setid=? AND version=?', (sid, version)).fetchone()
        if v['selected_sha256'] and v['selected_sha256'] != x['ZIP_sha256']:
            raise ValueError('Supplemental selection byte conflict')
        response_ids = []
        for observation in ledger:
            if observation['url'] != x['url'] or observation['HTTP_status'] != 206:
                continue
            key = hashlib.sha256(dumps(observation).encode()).hexdigest()
            if key not in imported:
                link_evidence(c, observation['path'], root, observation['sha256'])
                c.execute('''INSERT OR IGNORE INTO retrievals(observation_key,kind,url,final_url,retrieved_at,status,headers_json,state,artifact_sha256,error,attempt)
                             VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                          (key, 'bulk_range', observation['url'], observation['url'], observation['UTC'],
                           206, dumps(observation['headers']), 'available', observation['sha256'], observation.get('error'), 1))
                imported[key] = c.execute('SELECT id FROM retrievals WHERE observation_key=?', (key,)).fetchone()[0]
            response_ids.append(imported[key])
        if not response_ids:
            raise ValueError('No retained HTTP range provenance for member')
        digest = put_bytes(c, root, body, artifact_class='retrieved-evidence')
        provenance = {**x, 'ingested': True, 'source_ledger_sha256': sha(ledger_path),
                      'bulk_range_retrieval_ids': sorted(set(response_ids)),
                      'transport': 'Exact nested ZIP extracted offline from retained HTTP206 ranges; no direct member HTTP response',
                      'publication_date': None, 'publication_date_reason': 'Not supplied by frozen target; no history date fabricated'}
        key = 'bulk-member:' + hashlib.sha256(dumps(x).encode()).hexdigest()
        observed = max(r['UTC'] for r in ledger if r['url'] == x['url'] and r['HTTP_status'] == 206)
        c.execute('''INSERT OR IGNORE INTO retrievals(observation_key,version_id,kind,url,final_url,retrieved_at,status,headers_json,state,artifact_sha256,attempt,validation_json)
                     VALUES(?,?,?,?,?,?,NULL,?,?,?,?,?)''',
                  (key, v['id'], 'bulk_member', x['url'], x['url'], observed, '[]', 'available', digest, 1,
                   dumps({'state': 'downloaded', 'identity_verified': True, 'xml_member': member,
                          'effective_date': effective, 'transport_status': 'not a direct HTTP response'})))
        rid = c.execute('SELECT id FROM retrievals WHERE observation_key=?', (key,)).fetchone()[0]
        c.execute('INSERT OR IGNORE INTO bulk_member_sources VALUES(?,?)', (rid, dumps(provenance)))
        c.execute("UPDATE versions SET state='downloaded',selected_sha256=?,xml_member=?,effective_date=? WHERE id=?",
                  (digest, member, effective, v['id']))
        c.commit()
        stored = c.execute('SELECT * FROM versions WHERE id=?', (v['id'],)).fetchone()
        parse = persist_document(c, root, stored, registry)
        c.execute('INSERT OR IGNORE INTO release1_parse_selection VALUES(?,?)', (v['id'], parse))
        c.commit()
        parsed = dict(c.execute('SELECT * FROM parse_runs WHERE id=?', (parse,)).fetchone())
        if parsed['status'] == 'extraction_failed':
            raise ValueError('Recovered document parse failed')
        versions.append({**dict(stored), 'publication_observations': [],
                         'retrievals': [dict(r) for r in c.execute('SELECT * FROM retrievals WHERE version_id=? ORDER BY id', (v['id'],))],
                         'parse_runs': [parsed], 'target_origin': 'listing-only' if sid != '6de5637f-d527-4228-af1a-3aae71155cb5' else 'bulk-only; upstream history unresolved',
                         'bulk_member_provenance': provenance})
        print(json.dumps({'admitted': len(versions), 'setid': sid, 'version': version, 'parse_status': parsed['status']}), flush=True)
    manifest = {'format': 'gitrx-bulk-recovery-evidence-1', 'versions': versions,
                'candidate_manifest_sha256': sha(source / 'VERIFIED-CANDIDATES.json'),
                'source_ledger_sha256': sha(ledger_path), 'parser': {'version': PARSER_VERSION, 'config': CONFIG},
                'history_complete': False, 'fabricated_publication_dates': 0, 'classifier_invoked': False}
    raw = dumps(manifest)
    snapshot = hashlib.sha256(raw.encode()).hexdigest()
    c.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?,?)', (snapshot, now(), raw))
    c.commit()
    if c.execute('PRAGMA foreign_key_check').fetchall():
        raise ValueError('Supplemental foreign-key failure')
    c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    c.close()
    (root / 'reports/SNAPSHOT-ID.txt').write_text(snapshot + '\n')
    (root / 'reports' / ('snapshot-' + snapshot + '.json')).write_text(raw + '\n')
    result = {'root': str(root.resolve()), 'snapshot': snapshot, 'admitted': len(versions),
              'parse_statuses': dict(Counter(p['parse_runs'][0]['status'] for p in versions)),
              'database_sha256': sha(root / 'data/gitrx.sqlite'), 'network_requests': 0,
              'history_entries_invented': 0, 'publication_dates_invented': 0}
    report.write_text(json.dumps(result, indent=2) + '\n')
    return result


class ReleaseFederation:
    """Exporter adapter selecting the last pinned family source plus additions."""
    def __init__(self, project, supplemental):
        self.project, supplemental = Path(project), Path(supplemental)
        parent_path = self.project / 'cohorts/release1/recovery-manifest-round-3.json'
        parent = json.loads(parent_path.read_bytes())
        roots = {}
        for family in parent['family_sources']:
            roots.setdefault(family['source_root'], set()).add(family['setid'])
        self.sources, self.raw_roots, self.raw_lookup, self.parse_versions = {}, {}, {}, {}
        selected, identities, source_pins = [], set(), {}
        sources = [(Path(root), allowed) for root, allowed in sorted(roots.items())]
        sources.append((supplemental, {r['setid'] for r in parent['family_sources']}))
        from .federation import checkpoint_pins
        pins = {str(root.resolve()): (snapshot, digest)
                for root, snapshot, digest in checkpoint_pins(self.project / 'cohorts/release1')}
        for number, (root, allowed) in enumerate(sources):
            prefix = 's' + str(number)
            c = readonly(root / 'data/gitrx.sqlite')
            # Bounded per-source page caches (at most ~3 GiB across the 46
            # retained stores) and read-only memory mapping avoid repeated
            # small overflow-page reads from the deduplicated TEXT views.
            # These connection settings do not change database bytes/indexes.
            c.execute('PRAGMA cache_size=-65536')
            c.execute('PRAGMA mmap_size=1073741824')
            snapshot = (pins[str(root.resolve())][0] if str(root.resolve()) in pins
                        else (root / 'reports/SNAPSHOT-ID.txt').read_text().strip())
            row = c.execute('SELECT manifest_json FROM snapshots WHERE id=?', (snapshot,)).fetchone()
            if not row or hashlib.sha256(row[0].encode()).hexdigest() != snapshot:
                raise ValueError('Source snapshot missing or changed')
            self.sources[prefix] = (c, root)
            source_pins[prefix] = {'root': str(root.resolve()), 'snapshot': snapshot,
                                   'database_sha256': pins[str(root.resolve())][1] if str(root.resolve()) in pins else sha(root / 'data/gitrx.sqlite')}
            for version in json.loads(row[0])['versions']:
                if version['setid'] not in allowed or version['state'] != 'downloaded':
                    continue
                key = (version['setid'], version['version'])
                if key in identities:
                    raise ValueError('Duplicate selected release identity')
                identities.add(key)
                v = dict(version, id=prefix + ':' + str(version['id']))
                v['parse_runs'] = [dict(p, id=prefix + ':' + str(p['id'])) for p in version['parse_runs']]
                if len(v['parse_runs']) != 1:
                    raise ValueError('Ambiguous selected parse')
                selected.append(v)
                for parse in version['parse_runs']:
                    if int(parse['version_id']) != int(version['id']) or parse['artifact_sha256'] != version['selected_sha256']:
                        raise ValueError('Pinned parse/version/raw identity mismatch')
                    self.parse_versions[(prefix, int(parse['id']))] = int(version['id'])
                self.raw_roots[v['selected_sha256']] = prefix
        if len(selected) != 51357 or len({v['setid'] for v in selected}) != 7239:
            raise ValueError('Expanded release target accounting does not reconcile')
        self.manifest = {'format': 'gitrx-expanded-release-1',
                         'growth_policy': 'immutable-separately-tagged-expanded-snapshots',
                         'parent_selection_sha256': sha(parent_path),
                         'scope_sha256': sha(self.project / 'cohorts/release1/scope.json'),
                         'sources': source_pins, 'versions': sorted(selected, key=lambda v: (v['setid'], v['version'])),
                         'upstream_history_unknown_families': ['6de5637f-d527-4228-af1a-3aae71155cb5'],
                         'known_target_failures': 0, 'recovered_targets': 15,
                         'historical_completeness_claim': False, 'classification_exported': False}
        self.raw = dumps(self.manifest)
        self.snapshot = hashlib.sha256(self.raw.encode()).hexdigest()

    def execute(self, sql, parameters=()):
        if 'SELECT manifest_json FROM snapshots' in sql:
            return One((self.raw,)) if parameters[0] == self.snapshot else One(None)
        if 'SELECT path,artifact_class FROM artifacts' in sql:
            digest = parameters[0]
            if digest not in self.raw_lookup:
                prefix = self.raw_roots[digest]
                c, root = self.sources[prefix]
                row = c.execute(sql, parameters).fetchone()
                self.raw_lookup[digest] = {'path': str(root / row['path']), 'artifact_class': row['artifact_class']}
            return One(self.raw_lookup[digest])
        if 'parse_run_id=?' in sql:
            if getattr(self, 'payload_cache', None) is not None:
                table = 'tables' if 'FROM narrative_tables' in sql else 'sections'
                return self.payload_cache.read(str(parameters[0]), table)
            prefix, number = str(parameters[0]).split(':', 1)
            number = int(number)
            version = self.parse_versions[(prefix, number)]
            # The retained section_lookup index starts with version_id. This
            # logically redundant pinned-ID restriction avoids full native
            # instance scans without modifying any source index or payload.
            if 'WHERE s.parse_run_id=?' in sql:
                sql = sql.replace('WHERE s.parse_run_id=?', 'WHERE s.parse_run_id=? AND s.version_id=?')
            elif 'WHERE parse_run_id=?' in sql:
                sql = sql.replace('WHERE parse_run_id=?', 'WHERE parse_run_id=? AND version_id=?')
            else:
                raise ValueError('Unrecognized exporter parse predicate')
            return self.sources[prefix][0].execute(sql, (number, version, *parameters[1:]))
        raise ValueError('Unsupported release query: ' + sql[:80])

    def close(self):
        for c, _ in self.sources.values():
            c.close()


RETIRED = 'This command built the 2026-11-06 release (cohorts/release1), which was deleted on 2026-11-11. The library functions remain; the build command is recoverable at git tag codex-final (docs/CLEANUP-2026-11-11.md).'


def main():
    raise SystemExit(RETIRED)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project', type=Path, default=Path(__file__).resolve().parents[1])
    a = p.parse_args()
    root = a.project / 'cohorts/release1-recovered-20261106'
    admitted = admit(a.project, root)
    f = ReleaseFederation(a.project, root)
    try:
        (root / 'reports/EXPANDED-SELECTION.json').write_text(f.raw + '\n')
        summary = {**admitted, 'expanded_snapshot': f.snapshot,
                   'families': 7239, 'known_targets': 51357, 'retrieved': 51357,
                   'known_target_failures': 0, 'upstream_empty_history_families': 1,
                   'old_controller_preserved': True}
        (root / 'reports/CURRENT-RELEASE.json').write_text(json.dumps(summary, indent=2) + '\n')
        print(json.dumps(summary))
    finally:
        f.close()


if __name__ == '__main__':
    main()
