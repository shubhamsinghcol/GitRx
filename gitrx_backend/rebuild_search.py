"""Pinned literal-search projection for a rebuild release (proposes candidates only).

One SQLite part per setid prefix (same split as the rebuild shards) holds an FTS5
contentless trigram index of the casefolded own_text of every section of each
family's greatest captured version, in the order RebuildReader.read returns them.
The index only proposes (family, section index) candidates: Query.search re-reads
the rebuild's own text and confirms every hit with the same literal casefold test,
so results equal an exhaustive scan.  Python performs all case folding (the
tokenizer runs with case_sensitive 1), so the index does not depend on SQLite's
fold tables; it does depend on the Python Unicode database, which is pinned.
Reads never build or repair a projection; a mismatch is refused, not rebuilt.

Build (explicit, resumable; completed parts are kept):
  .venv/bin/python -B -m gitrx_backend.rebuild_search --config CONFIG --output DIR [--workers N]
"""
import argparse
import bisect
import hashlib
import json
import os
import sqlite3
import sys
import time
import unicodedata
from pathlib import Path

FORMAT = 'gitrx-rebuild-search-1'
TOKENIZER = "trigram case_sensitive 1"
MANIFEST = 'SEARCH-INDEX.json'
MAX_QUERY_TRIGRAMS = 16


def file_sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 22), b''):
            h.update(chunk)
    return h.hexdigest()


def index_text(text):
    """Casefold exactly like the literal test; lone surrogates cannot be stored, so they become U+FFFD."""
    folded = (text or '').casefold()
    try:
        folded.encode('utf-8')
    except UnicodeEncodeError:
        folded = folded.encode('utf-8', 'replace').decode('utf-8')
    return folded.replace('\x00', '�')


def query_trigrams(phrase):
    """Distinct trigrams (evenly sampled when long) of the casefolded phrase, or None when unusable."""
    folded = phrase.casefold()
    if len(folded) < 3 or '\x00' in folded:
        return None
    try:
        folded.encode('utf-8')
    except UnicodeEncodeError:
        return None
    grams = list(dict.fromkeys(folded[i:i + 3] for i in range(len(folded) - 2)))
    if len(grams) > MAX_QUERY_TRIGRAMS:
        step = (len(grams) - 1) / (MAX_QUERY_TRIGRAMS - 1)
        grams = [grams[round(i * step)] for i in range(MAX_QUERY_TRIGRAMS)]
    return grams


def match_expression(grams):
    return ' AND '.join('"%s"' % g.replace('"', '""') for g in grams)


# -- reading -----------------------------------------------------------------
class SearchProjection:
    def __init__(self, manifest_path, manifest_sha256, snapshot, verify='sha256'):
        manifest_path = Path(manifest_path)
        try:
            raw = manifest_path.read_bytes()
        except OSError as exc:
            raise OSError('Search projection manifest unavailable (%s); it is never rebuilt on read' % type(exc).__name__) from None
        if hashlib.sha256(raw).hexdigest() != manifest_sha256:
            raise ValueError('Search projection manifest hash does not match the pinned configuration')
        self.manifest = json.loads(raw)
        m = self.manifest
        if m.get('format') != FORMAT or m.get('tokenizer') != TOKENIZER:
            raise ValueError('Unsupported search projection format')
        if m.get('snapshot') != snapshot:
            raise ValueError('Search projection belongs to a different release snapshot')
        if m.get('unicode') != unicodedata.unidata_version:
            raise ValueError('Search projection was built with Unicode %s, this Python has %s; rebuild explicitly'
                             % (m.get('unicode'), unicodedata.unidata_version))
        self.dir = manifest_path.parent
        self.sha256 = manifest_sha256
        self.verify = verify
        self.snapshot = snapshot
        self.parts = m['parts']
        self._conns = {}
        self._docs = {}
        self._verified = set()

    def _part(self, prefix):
        if prefix not in self._conns:
            info = self.parts[prefix]
            path = self.dir / (prefix + '.sqlite')
            if prefix not in self._verified:
                if path.stat().st_size != info['bytes']:
                    raise ValueError('Search projection part %s size differs from its manifest' % prefix)
                if self.verify == 'sha256' and file_sha256(path) != info['sha256']:
                    raise ValueError('Search projection part %s SHA-256 differs from its manifest' % prefix)
                self._verified.add(prefix)
            conn = sqlite3.connect('file:%s?mode=ro&immutable=1' % path, uri=True)
            conn.execute('PRAGMA query_only=ON')
            meta = dict(conn.execute('SELECT k,v FROM meta'))
            if meta.get('snapshot') != self.snapshot or meta.get('format') != FORMAT or meta.get('prefix') != prefix:
                conn.close()
                raise ValueError('Search projection part %s belongs to a different release' % prefix)
            self._conns[prefix] = conn
            rows = conn.execute('SELECT first_rowid,setid,version,n FROM docs ORDER BY first_rowid').fetchall()
            self._docs[prefix] = ([r[0] for r in rows], rows)
        return self._conns[prefix], self._docs[prefix]

    def plan(self, phrase, family_index):
        grams = query_trigrams(phrase)
        if grams is None:
            return None
        return _Plan(self, match_expression(grams), family_index)

    def close(self):
        for c in self._conns.values():
            c.close()
        self._conns.clear()


class _Plan:
    def __init__(self, projection, expression, family_index):
        self.projection, self.expression, self.family_index = projection, expression, family_index

    def stream(self, family_offset, section_offset, families):
        """Yield ('part', prefix) before each part, then ('doc', family_index, setid, version, n_sections,
        [candidate section indexes]) for each family with candidates, in register order."""
        if family_offset >= len(families):
            return
        first_sid = families[family_offset]['setid']
        for prefix in sorted(self.projection.parts):
            if prefix < first_sid[:2]:
                continue
            yield ('part', prefix)
            conn, (starts, docs) = self.projection._part(prefix)
            lo = 0
            if prefix == first_sid[:2]:
                at = bisect.bisect_left([d[1] for d in docs], first_sid)
                if at >= len(docs):
                    continue
                lo = docs[at][0]
            current, positions = None, []
            for (rowid,) in conn.execute('SELECT rowid FROM t WHERE t MATCH ? AND rowid>=? ORDER BY rowid',
                                         (self.expression, lo)):
                d = docs[bisect.bisect_right(starts, rowid) - 1]
                if d is not current:
                    if current is not None and positions:
                        yield self._out(current, positions, family_offset, section_offset)
                    current, positions = d, []
                positions.append(rowid - d[0])
            if current is not None and positions:
                yield self._out(current, positions, family_offset, section_offset)

    def first_family(self, prefix, families):
        """Register index of the first family whose setid is not before this prefix."""
        return bisect.bisect_left([f['setid'] for f in families], prefix)

    def _out(self, doc, positions, family_offset, section_offset):
        fi = self.family_index[doc[1]]
        if fi == family_offset:
            positions = [p for p in positions if p >= section_offset]
        return ('doc', fi, doc[1], doc[2], doc[3], positions)


# -- building ----------------------------------------------------------------
def build_part(reader, prefix, out_dir):
    """Build one part from the pinned rebuild reader (no projection). Returns its manifest entry."""
    final = Path(out_dir) / (prefix + '.sqlite')
    tmp = Path(out_dir) / (prefix + '.sqlite.partial')
    if tmp.exists():
        tmp.unlink()
    conn = sqlite3.connect(tmp)
    try:
        conn.executescript(
            "PRAGMA page_size=4096; PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;"
            "CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT) WITHOUT ROWID;"
            "CREATE TABLE docs(first_rowid INTEGER PRIMARY KEY, setid TEXT, version INTEGER, n INTEGER);"
            "CREATE VIRTUAL TABLE t USING fts5(x, content='', detail=none, tokenize='%s');" % TOKENIZER)
        rowid = 0
        families = 0
        for fam in reader.families():
            sid = fam['setid']
            if not sid.startswith(prefix):
                continue
            versions = reader.versions(sid)
            if not versions or not versions[-1]['parsed']:
                continue
            sections, _ = reader.read(sid, versions[-1]['version'])
            if not sections:
                continue            # no section text: nothing to propose
            families += 1
            conn.execute('INSERT INTO docs VALUES(?,?,?,?)', (rowid + 1, sid, versions[-1]['version'], len(sections)))
            for s in sections:
                rowid += 1
                conn.execute('INSERT INTO t(rowid,x) VALUES(?,?)', (rowid, index_text(s['own_text'])))
        conn.executemany('INSERT INTO meta VALUES(?,?)', [
            ('format', FORMAT), ('snapshot', reader.snapshot), ('prefix', prefix), ('tokenizer', TOKENIZER),
            ('unicode', unicodedata.unidata_version), ('families', str(families)), ('sections', str(rowid))])
        conn.execute("INSERT INTO t(t) VALUES('optimize')")
        conn.commit()
        if conn.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Projection part integrity failure')
    finally:
        conn.close()
    os.replace(tmp, final)
    entry = {'bytes': final.stat().st_size, 'sha256': file_sha256(final), 'families': families, 'sections': rowid}
    (Path(out_dir) / (prefix + '.done.json')).write_text(json.dumps(entry, sort_keys=True) + '\n')
    return entry


_WORKER = {}


def _init(config, out_dir):
    os.nice(10)
    from .config import open_reader
    _WORKER['reader'] = open_reader(config)
    _WORKER['out'] = out_dir


def _work(prefix):
    t = time.time()
    entry = build_part(_WORKER['reader'], prefix, _WORKER['out'])
    return prefix, entry, time.time() - t


def build(config, out_dir, workers=2):
    """Build every missing part, then write the pinned manifest. Completed parts are reused (resumable)."""
    from .config import open_reader
    workers = max(1, min(int(workers), 4))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with open_reader(config) as r:
        if r.kind != 'rebuild':
            raise ValueError('Search projection builds only from a rebuild release')
        snapshot = r.snapshot
        prefixes = sorted(r.shard_info)
    entries, todo = {}, []
    for p in prefixes:
        done = out / (p + '.done.json')
        part = out / (p + '.sqlite')
        if done.is_file() and part.is_file():
            e = json.loads(done.read_text())
            if part.stat().st_size == e['bytes']:
                entries[p] = e
                continue
        todo.append(p)
    started = time.time()
    if todo:
        import multiprocessing
        ctx = multiprocessing.get_context('spawn')
        with ctx.Pool(workers, _init, (str(config), str(out))) as pool:
            for prefix, entry, seconds in pool.imap_unordered(_work, todo):
                entries[prefix] = entry
                print('part %s: %d families, %d sections, %d bytes, %.1fs' % (
                    prefix, entry['families'], entry['sections'], entry['bytes'], seconds), file=sys.stderr, flush=True)
    manifest = {'format': FORMAT, 'snapshot': snapshot, 'tokenizer': TOKENIZER, 'unicode': unicodedata.unidata_version,
                'sqlite_version_built': sqlite3.sqlite_version,
                'semantics': 'Candidate proposal only: trigram AND-filter over casefolded section text; every hit is '
                             'confirmed against the rebuild text, so results equal an exhaustive scan.',
                'scope': 'Greatest captured version per family; own_text (text plus table channel) per section index.',
                'parts': {p: entries[p] for p in prefixes},
                'totals': {'families': sum(e['families'] for e in entries.values()),
                           'sections': sum(e['sections'] for e in entries.values()),
                           'bytes': sum(e['bytes'] for e in entries.values())}}
    body = (json.dumps(manifest, indent=2, sort_keys=True) + '\n').encode()
    (out / MANIFEST).write_bytes(body)
    (out / 'BUILD-LOG.json').write_text(json.dumps({'build_seconds_this_run': round(time.time() - started, 1),
                                                    'parts_built_this_run': len(todo), 'workers': workers}) + '\n')
    return {'manifest_sha256': hashlib.sha256(body).hexdigest(), **manifest['totals'],
            'parts_built_this_run': len(todo), 'seconds': round(time.time() - started, 1)}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--config', required=True, help='rebuild configuration WITHOUT a search_index (the source to project)')
    p.add_argument('--output', required=True)
    p.add_argument('--workers', type=int, default=2)
    a = p.parse_args()
    print(json.dumps(build(a.config, a.output, a.workers), indent=2))


if __name__ == '__main__':
    main()
