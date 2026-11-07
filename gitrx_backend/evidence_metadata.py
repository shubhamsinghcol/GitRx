"""Additive, release-pinned extraction, inventory and publisher metadata.

Build explicitly from retained selection observations; reads never collect or build.
This sidecar augments an evidence release without rewriting its source or Git refs.
"""
import argparse
from collections import defaultdict
import hashlib
import json
import os
import tempfile
from pathlib import Path
import sqlite3
from urllib.parse import urlencode, urlsplit

FORMAT = 'gitrx-evidence-metadata-1'


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()


def dumps(value): return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'))


def unknown_evidence(sid, version):
    return {'metadata_status': 'unavailable',
            'extraction': {'status': 'unknown', 'diagnostics': [],
                           'meaning': 'This configuration has no pinned extraction metadata; no completeness inference.'},
            'publisher_references': [{'url': 'https://dailymed.nlm.nih.gov/dailymed/getFile.cfm?' + urlencode({'type':'zip','setid':sid,'version':version}),
                                      'kind': 'exact-version-route', 'observed': False,
                                      'meaning': 'Constructed publisher route; availability and returned identity are not verified by this read.'}],
            'retrieval_routes': []}


def unknown_inventory(numbers):
    return {'metadata_status': 'unavailable', 'status': 'unknown',
            'captured_versions': len(numbers), 'known_target_versions': None,
            'known_target_count': None, 'unknown_upstream_history': None,
            'historical_completeness_claim': False,
            'basis': 'Captured reader records only; upstream target inventory metadata unavailable.'}


def _json(value, default):
    return json.loads(value) if isinstance(value, str) else value if value is not None else default


def _official(url):
    try:
        u = urlsplit(url)
        return u.scheme == 'https' and (u.hostname == 'nlm.nih.gov' or (u.hostname or '').endswith('.nlm.nih.gov')) and not u.username and not u.password
    except (ValueError, TypeError): return False


def _version_record(row):
    parses = row.get('parse_runs', [])
    statuses = [p.get('status', 'unknown') for p in parses]
    status = statuses[0] if len(statuses) == 1 and statuses[0] in ('complete','partial','failed') else 'unknown'
    diagnostics = [dict(d, parse_key=p.get('id')) if isinstance(d, dict) else {'detail':d, 'parse_key':p.get('id')}
                   for p in parses for d in _json(p.get('diagnostics_json'), [])]
    extraction = {'status': status, 'diagnostics': diagnostics,
                  'parse_keys': [p.get('id') for p in parses],
                  'parser_versions': sorted({p['parser_version'] for p in parses if p.get('parser_version')}),
                  'meaning': 'Parser diagnostic status, not independent proof of complete source fidelity or clinical meaning.'}
    routes, refs = [], []
    bulk = row.get('bulk_member_provenance')
    for r in row.get('retrievals', []):
        # Keep failed/conflicting route observations visibly separate from references
        # that actually supplied selected bytes. Headers and source text remain in evidence stores.
        route = {k:r.get(k) for k in ('id','kind','url','final_url','retrieved_at','status','state','artifact_sha256','error')}
        route['selected_bytes'] = r.get('artifact_sha256') == row['selected_sha256']
        routes.append(route)
        url = r.get('final_url') or r.get('url')
        if not route['selected_bytes'] or r.get('error') or r.get('status') != 200 or not _official(url): continue
        kind = 'archive-version' if 'getArchivalFile' in url else 'exact-version' if 'getFile.cfm' in url else 'publisher-response'
        refs.append({'url':url,'kind':kind,'observed':True,'retrieved_at':r.get('retrieved_at'),
                     'status':r.get('status'),'artifact_sha256':r.get('artifact_sha256')})
    if bulk:
        # An offline nested member has no invented successful direct HTTP response.
        refs.append({k:bulk.get(k) for k in ('url','member','xml_member','transport','source_ledger_sha256','bulk_range_retrieval_ids')}
                    | {'kind':'bulk-member','observed':True,'status':None,'artifact_sha256':row['selected_sha256']})
    if not refs:
        refs = unknown_evidence(row['setid'], row['version'])['publisher_references']
    return {'metadata_status':'pinned','raw_sha256':row['selected_sha256'],
            'extraction':extraction,'publisher_references':refs,'retrieval_routes':routes}


def _write_projection(manifest, target, snapshot):
    target = Path(target).resolve()
    if target.exists():
        pin = json.loads((target/'PIN.json').read_bytes())
        if pin.get('format') != FORMAT or pin.get('snapshot') != snapshot:
            raise ValueError('Existing evidence metadata belongs to different inputs')
        projection = Projection(target/'evidence.sqlite',pin['sha256'],snapshot)
        projection.close()
        return pin
    target.parent.mkdir(parents=True,exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=target.name+'-building-',dir=target.parent))
    database = staging/'evidence.sqlite'
    c = sqlite3.connect(database)
    try:
        c.executescript('CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);'
                       'CREATE TABLE versions(setid TEXT,version INTEGER,raw_sha256 TEXT NOT NULL,record TEXT NOT NULL,PRIMARY KEY(setid,version));'
                       'CREATE TABLE families(setid TEXT PRIMARY KEY,record TEXT NOT NULL);')
        families = defaultdict(list)
        with c:
            c.executemany('INSERT INTO meta VALUES(?,?)', [('format',FORMAT),('snapshot',snapshot)])
            for row in manifest['versions']:
                record = _version_record(row); sid = row['setid']; version = row['version']
                c.execute('INSERT INTO versions VALUES(?,?,?,?)',(sid,version,row['selected_sha256'],dumps(record)))
                families[sid].append(version)
            unknown = set(manifest.get('upstream_history_unknown_families', []))
            # The expanded selection can assert all known targets recovered, not all
            # historical versions discovered. Future manifests without that assertion
            # retain unknown target totals rather than deriving them from max(version).
            targets_known = manifest.get('known_target_failures') == 0
            for sid,numbers in sorted(families.items()):
                inventory = {'metadata_status':'pinned',
                    'status':'upstream-history-unknown' if sid in unknown else 'captured-target-selection',
                    'captured_versions':len(numbers),
                    'known_target_versions':sorted(numbers) if targets_known else None,
                    'known_target_count':len(numbers) if targets_known else None,
                    'unknown_upstream_history':True if sid in unknown else None,
                    'historical_completeness_claim':False,
                    'basis':'Pinned expanded selection membership and declared known-target accounting; not every integer or complete historical inventory.',
                    'selection_snapshot':snapshot,
                    'publication_observation_basis':'Retained DailyMed publication observations; no live verification timestamp is inferred.'}
                c.execute('INSERT INTO families VALUES(?,?)',(sid,dumps(inventory)))
        if c.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':raise ValueError('Evidence projection integrity failure')
    finally: c.close()
    pin = {'format':FORMAT,'snapshot':snapshot,'sha256':file_hash(database),'database':str(target/'evidence.sqlite'),
           'versions':len(manifest['versions']),'families':len(families)}
    (staging/'PIN.json').write_text(json.dumps(pin,indent=2)+'\n')
    os.rename(staging,target)
    return pin


def build(release_root, target, expected_snapshot=None):
    root=Path(release_root).resolve()
    path=root/'reports/EXPANDED-SELECTION.json'
    # Hash exact selected bytes, retaining the established one-newline boundary.
    raw=path.read_bytes().removesuffix(b'\n'); snapshot=hashlib.sha256(raw).hexdigest()
    pin=json.loads((root/'reports/NATIVE-INDEX.json').read_bytes())
    if snapshot != pin['expanded_snapshot'] or expected_snapshot is not None and snapshot != expected_snapshot:
        raise ValueError('Evidence metadata selection snapshot mismatch')
    manifest=json.loads(raw)
    del raw
    return _write_projection(manifest,target,snapshot)


class Projection:
    def __init__(self,path,expected_hash,snapshot):
        path=Path(path).resolve()
        if file_hash(path) != expected_hash:raise ValueError('Evidence metadata hash mismatch')
        self.conn=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
        self.conn.execute('PRAGMA query_only=ON')
        if dict(self.conn.execute('SELECT key,value FROM meta')) != {'format':FORMAT,'snapshot':snapshot}:
            self.close();raise ValueError('Evidence metadata snapshot/format mismatch')
    def evidence(self,sid,version,raw_sha256=None):
        row=self.conn.execute('SELECT raw_sha256,record FROM versions WHERE setid=? AND version=?',(sid,version)).fetchone()
        if row is None:raise ValueError('Version absent from pinned evidence metadata')
        if raw_sha256 is not None and row[0] != raw_sha256:raise ValueError('Evidence metadata source hash mismatch')
        return json.loads(row[1])
    def inventory(self,sid):
        row=self.conn.execute('SELECT record FROM families WHERE setid=?',(sid,)).fetchone()
        if row is None:raise ValueError('Family absent from pinned evidence metadata')
        return json.loads(row[0])
    def close(self): self.conn.close()


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();path=Path(a.config).resolve();config=json.loads(path.read_bytes())
    if config.get('format')!='gitrx-backend-config-1':raise ValueError('Unsupported configuration')
    root=(path.parent/config['release_root']).resolve()
    pin=build(root,a.output,config['snapshot'])
    for key in ('release_root','export_root','scope_path','search_index','evidence_metadata'):
        if key in config:config[key]=str((path.parent/config[key]).resolve())
    config.update(evidence_metadata=pin['database'],evidence_metadata_sha256=pin['sha256'])
    (Path(a.output)/'backend.json').write_text(json.dumps(config,indent=2)+'\n')
    print(json.dumps(pin,indent=2))

if __name__=='__main__':main()
