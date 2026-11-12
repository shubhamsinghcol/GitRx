"""Source-first browsing of the expanded native archive; no context inference."""
from pathlib import Path
import argparse
import gzip
import hashlib
import json
import sqlite3

from .io import ensure_space


def build(root, scope_path):
    root = Path(root).resolve()
    target = root / 'native-query.sqlite'
    selection_raw = (root / 'reports/EXPANDED-SELECTION.json').read_bytes().removesuffix(b'\n')
    snapshot = hashlib.sha256(selection_raw).hexdigest()
    cache_raw = (root / 'export-payloads/INDEX.json').read_bytes()
    cache_hash = hashlib.sha256(cache_raw).hexdigest()
    if target.exists():
        verify_index(root)
        with readonly(target) as c:
            meta = dict(c.execute('SELECT key,value FROM meta'))
        if meta != {'expanded_snapshot': snapshot, 'cache_index_sha256': cache_hash}:
            raise ValueError('Existing native index belongs to other inputs; retain it and choose a new release root')
        return target
    manifest, cache = json.loads(selection_raw), json.loads(cache_raw)
    if cache['expanded_snapshot'] != snapshot:
        raise ValueError('Prepared payload selection mismatch')
    names = json.loads(Path(scope_path).read_bytes())['records']
    if hashlib.sha256(Path(scope_path).read_bytes()).hexdigest() != manifest['scope_sha256']:
        raise ValueError('Family-name scope differs from pinned selection')
    ensure_space(root, 256 * 1024**2)
    c = sqlite3.connect(target)
    try:
        c.executescript('''PRAGMA foreign_keys=ON;
          CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE families(setid TEXT PRIMARY KEY,title TEXT NOT NULL);
          CREATE TABLE versions(setid TEXT REFERENCES families(setid),version INTEGER,
            raw_sha256 TEXT NOT NULL,effective_date TEXT,publication_dates TEXT NOT NULL,
            parse_key TEXT NOT NULL,payload_path TEXT NOT NULL,payload_sha256 TEXT NOT NULL,
            PRIMARY KEY(setid,version));''')
        with c:
            c.executemany('INSERT INTO meta VALUES(?,?)', [('expanded_snapshot', snapshot), ('cache_index_sha256', cache_hash)])
            for sid in sorted({v['setid'] for v in manifest['versions']}):
                c.execute('INSERT INTO families VALUES(?,?)', (sid, names[sid]['title']))
            for v in manifest['versions']:
                key = v['parse_runs'][0]['id']
                payload = cache['entries'][key]
                dates = sorted({p['published_date'] for p in v['publication_observations'] if p['published_date']})
                c.execute('INSERT INTO versions VALUES(?,?,?,?,?,?,?,?)',
                          (v['setid'], v['version'], v['selected_sha256'], v['effective_date'],
                           json.dumps(dates), key, payload['path'], payload['sha256']))
        if c.execute('PRAGMA foreign_key_check').fetchall():
            raise ValueError('Native query index foreign-key failure')
    finally:
        c.close()
    pin = {'database_sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
           'expanded_snapshot': snapshot, 'cache_index_sha256': cache_hash,
           'versions': len(manifest['versions']), 'families': len(names)}
    (root / 'reports/NATIVE-INDEX.json').write_text(json.dumps(pin, indent=2) + '\n')
    return target


def readonly(path):
    c = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA query_only=ON')
    return c


def verify_index(root):
    root = Path(root).resolve()
    pin = json.loads((root / 'reports/NATIVE-INDEX.json').read_bytes())
    if hashlib.sha256((root / 'native-query.sqlite').read_bytes()).hexdigest() != pin['database_sha256']:
        raise ValueError('Derived native index hash changed; no evidence answer')
    return pin


def show(root, sid, version, code=None, ordinal=None, max_chars=16000, offset=0):
    if not 1 <= max_chars <= 200000 or offset < 0 or offset and ordinal is None:
        raise ValueError('Use 1..200000 max characters; a nonnegative offset requires one exact --ordinal')
    root = Path(root).resolve()
    verify_index(root)
    with readonly(root / 'native-query.sqlite') as c:
        v = c.execute('SELECT v.*,f.title FROM versions v JOIN families f USING(setid) WHERE setid=? AND version=?',
                      (sid.lower(), version)).fetchone()
        if v is None:
            raise ValueError('Version is outside this selected native snapshot')
        snapshot = c.execute("SELECT value FROM meta WHERE key='expanded_snapshot'").fetchone()[0]
    path = (root / 'export-payloads' / v['payload_path']).resolve()
    if not path.is_relative_to(root / 'export-payloads'):
        raise ValueError('Payload path escapes derived release cache')
    raw = gzip.decompress(path.read_bytes())
    if hashlib.sha256(raw).hexdigest() != v['payload_sha256']:
        raise ValueError('Native export payload hash changed')
    sections = json.loads(raw)['sections']
    selected = [s for s in sections if (code is None or s['loinc_code'] == code)
                and (ordinal is None or s['ordinal'] == ordinal)]
    output, budget = [], max_chars
    for s in selected:
        text = s['own_text'] or ''
        start = offset if ordinal is not None else 0
        excerpt = text[start:start + budget]
        output.append({'ordinal': s['ordinal'], 'source_code': s['loinc_code'],
                       'code_system': s['code_system'], 'title': s['title'],
                       'parent_ordinal': s['parent_ordinal'], 'ancestry': s['ancestry_path'],
                       'text': excerpt, 'text_characters': len(text), 'offset': start,
                       'truncated': start + len(excerpt) < len(text),
                       'next_offset': start + len(excerpt) if start + len(excerpt) < len(text) else None})
        budget -= len(excerpt)
    return {'family': v['title'], 'setid': sid.lower(), 'version': version,
            'expanded_snapshot': snapshot, 'raw_sha256': v['raw_sha256'],
            'DailyMed_publication_dates': json.loads(v['publication_dates']),
            'SPL_effective_date': v['effective_date'], 'context': 'unclassified native source',
            'scope': 'Source-coded/native occurrences; no product-section or absence/addition inference',
            'selected_instances': len(output), 'total_section_instances': len(sections), 'sections': output,
            'historical_completeness_claim': False}


RETIRED = 'This command built the 2026-11-06 release (cohorts/release1), which was deleted on 2026-11-11. The library functions remain; the build command is recoverable at git tag codex-final (docs/CLEANUP-2026-11-11.md).'


def main():
    raise SystemExit(RETIRED)
    project = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=project / 'runtime/release1-current')
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('build-index')
    families = sub.add_parser('families')
    families.add_argument('--search', default='')
    families.add_argument('--limit', type=int, default=25)
    versions = sub.add_parser('versions')
    versions.add_argument('setid')
    display = sub.add_parser('show')
    display.add_argument('setid')
    display.add_argument('version', type=int)
    display.add_argument('--code')
    display.add_argument('--ordinal', type=int)
    display.add_argument('--max-chars', type=int, default=16000)
    display.add_argument('--offset', type=int, default=0)
    a = p.parse_args()
    if a.command == 'build-index':
        print(build(a.root, project / 'cohorts/release1/scope.json'))
    elif a.command == 'show':
        print(json.dumps(show(a.root, a.setid, a.version, a.code, a.ordinal, a.max_chars, a.offset), ensure_ascii=False, indent=2))
    else:
        pin = verify_index(a.root)
        with readonly(a.root / 'native-query.sqlite') as c:
            if a.command == 'families':
                if not 1 <= a.limit <= 200:
                    raise ValueError('Family result limit must be 1..200')
                term = a.search.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
                rows = c.execute("SELECT setid,title FROM families WHERE title LIKE ? ESCAPE '\\' OR setid LIKE ? ESCAPE '\\' ORDER BY title,setid LIMIT ?",
                                 ('%' + term + '%', '%' + term + '%', a.limit))
                print(json.dumps({'expanded_snapshot': pin['expanded_snapshot'], 'selection_aid_only': True,
                                  'families': [dict(r) for r in rows]}, ensure_ascii=False, indent=2))
            else:
                rows = c.execute('SELECT version,raw_sha256,effective_date,publication_dates FROM versions WHERE setid=? ORDER BY version', (a.setid.lower(),))
                versions = [{'version': r['version'], 'raw_sha256': r['raw_sha256'],
                             'SPL_effective_date': r['effective_date'],
                             'DailyMed_publication_dates': json.loads(r['publication_dates'])} for r in rows]
                print(json.dumps({'setid': a.setid, 'expanded_snapshot': pin['expanded_snapshot'],
                                  'context': 'unclassified native source', 'historical_completeness_claim': False,
                                  'versions': versions}, indent=2))


if __name__ == '__main__':
    main()
