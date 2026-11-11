"""Build the deterministic family-name index pinned beside a rebuild release.

Explicit, offline build step; reads never run it. Titles come from the retained
evidence database (families table); setids come from the rebuild shards. The
output holds no clock, path or random content, so rebuilding yields identical bytes
for identical inputs. Titles are selection aids, never merged drug identities.
"""
import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path

from .rebuild_reader import NAMES_FORMAT, file_sha256

FIXTURE_NAME = 'Automatic parity fixture'


def choose(candidates):
    """candidates: [(name, listing_title_or_None)] -> (title, source, alternatives)."""
    listing = sorted({t for _, t in candidates if t})
    names = sorted({n for n, _ in candidates if n and n != FIXTURE_NAME})
    if listing:
        title, source = listing[0], 'dailymed_listing_title'
    elif names:
        title, source = names[0], 'evidence_family_name'
    else:
        names = sorted({n for n, _ in candidates if n})
        title, source = (names[0], 'fixture_name') if names else (None, 'none')
    alternatives = sorted({x for x in listing + names + [n for n, _ in candidates if n] if x != title})
    return title, source, alternatives


def build(rebuild_root, evidence_path, output):
    root = Path(rebuild_root)
    complete = root / 'COMPLETE.json'
    complete_sha = hashlib.sha256(complete.read_bytes()).hexdigest()
    manifest = json.loads(complete.read_bytes())
    evidence_sha = manifest['config']['inputs']['evidence_sha256']
    if file_sha256(evidence_path) != evidence_sha:
        raise ValueError('Evidence database hash differs from the hash recorded in the rebuild')
    setids = []
    for prefix in sorted(manifest['shards']):
        c = sqlite3.connect('file:%s?mode=ro&immutable=1' % (root / 'shards' / (prefix + '.sqlite')), uri=True)
        try:
            setids += [r[0] for r in c.execute('SELECT setid FROM families ORDER BY setid')]
        finally:
            c.close()
    wanted = set(setids)
    if len(wanted) != manifest['counts']['families']:
        raise ValueError('Shard family count differs from COMPLETE.json')
    found = {}
    e = sqlite3.connect('file:%s?mode=ro&immutable=1' % evidence_path, uri=True)
    try:
        for sid, name, meta in e.execute('SELECT setid,name,metadata_json FROM families ORDER BY id'):
            if sid not in wanted:
                continue
            try:
                listing = (json.loads(meta) or {}).get('listing', {}).get('title') if meta else None
            except (ValueError, AttributeError):
                listing = None
            found.setdefault(sid, []).append((name, listing))
    finally:
        e.close()
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix='names-building-', dir=output.parent)
    os.close(fd)
    os.unlink(tmp)
    c = sqlite3.connect(tmp)
    sources = {}
    try:
        c.executescript('PRAGMA page_size=4096; PRAGMA journal_mode=OFF;'
                        'CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT) WITHOUT ROWID;'
                        'CREATE TABLE families(setid TEXT PRIMARY KEY, title TEXT, title_source TEXT, alt_titles TEXT) WITHOUT ROWID;')
        with c:
            for sid in sorted(wanted):
                title, source, alt = choose(found.get(sid, []))
                sources[source] = sources.get(source, 0) + 1
                c.execute('INSERT INTO families VALUES(?,?,?,?)', (sid, title, source, json.dumps(alt, ensure_ascii=False)))
            for k, v in sorted({'format': NAMES_FORMAT, 'complete_sha256': complete_sha, 'evidence_sha256': evidence_sha,
                                'families': str(len(wanted)), 'title_sources': json.dumps(sources, sort_keys=True)}.items()):
                c.execute('INSERT INTO meta VALUES(?,?)', (k, v))
        if c.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Name index integrity failure')
    finally:
        c.close()
    os.replace(tmp, output)
    return {'path': str(output), 'sha256': file_sha256(output), 'families': len(wanted), 'title_sources': sources,
            'complete_sha256': complete_sha, 'evidence_sha256': evidence_sha}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--rebuild-root', required=True)
    p.add_argument('--evidence', required=True)
    p.add_argument('--output', required=True)
    a = p.parse_args(argv)
    if Path(a.output).exists():
        p.error('Output exists; refusing to overwrite a pinned derived file')
    print(json.dumps(build(a.rebuild_root, a.evidence, a.output), indent=2))


if __name__ == '__main__':
    main()
