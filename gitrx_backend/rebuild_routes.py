"""Pinned, derived lookup of the retrieval routes actually observed for each raw artifact of a rebuild release.

Built explicitly from the retained evidence database (evidence-v1.sqlite, plus the append-only delta that admitted
26 byte-identical bodies); reads never build it, never open the evidence database and never touch the network.
Each row says: this raw_sha256 was seen arriving by this URL, at this time, with this HTTP status, and the response
body hashed to body_sha256 (body_matches_raw).  Rows of a bulk/monthly-archive member state the member path and the
retained HTTP-range requests instead, because no direct HTTP response exists for such a member.

A lookup row is an observation of the retained evidence, not a statement that the route is still live, that the
publisher currently serves identical bytes, or that the version was published at that time.

Build (explicit; the evidence databases are opened read-only/immutable and never modified):
  .venv/bin/python -B -m gitrx_backend.rebuild_routes --config CONFIG --evidence EVIDENCE.sqlite \\
      [--delta DELTA.sqlite] --output DIR/routes.sqlite
"""
import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlsplit

FORMAT = 'gitrx-rebuild-routes-1'
FIELDS = ('source', 'kind', 'url', 'final_url', 'retrieved_at', 'http_status', 'state', 'body_sha256',
          'body_matches_raw', 'bytes', 'evidence_ref', 'detail')


def file_sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 22), b''):
            h.update(chunk)
    return h.hexdigest()


def _ro(path):
    c = sqlite3.connect('file:%s?mode=ro&immutable=1' % path, uri=True)
    c.execute('PRAGMA query_only=ON')
    return c


def _iso(epoch):
    import datetime as dt
    return dt.datetime.fromtimestamp(float(epoch), dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') if epoch not in (None, '') else None


def release_raw_hashes(reader):
    """raw_sha256 -> sorted [(setid, version)] for every version of the pinned release that has a raw body."""
    out = {}
    for prefix in sorted(reader.shard_info):
        conn = reader._shard(prefix + '0' * 6)       # any setid with this prefix selects the shard
        for sid, version, raw in conn.execute('SELECT setid,version,raw_sha256 FROM versions WHERE raw_sha256 IS NOT NULL ORDER BY setid,version'):
            out.setdefault(raw, []).append((sid, version))
    return out


def build(config, evidence_path, output, delta_path=None, log=lambda *a: None):
    from .config import open_reader
    evidence_path = Path(evidence_path).resolve()
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Routes lookup output exists; builds never overwrite')
    with open_reader(config) as reader:
        if reader.kind != 'rebuild':
            raise ValueError('Routes lookup builds only for a rebuild release')
        snapshot = reader.snapshot
        want_evidence = reader.complete['config']['inputs']['evidence_sha256']
        raws = release_raw_hashes(reader)
    log('release raw hashes: %d' % len(raws))
    ev_sha = file_sha256(evidence_path)
    if ev_sha != want_evidence:
        raise ValueError('Evidence database hash differs from the release inputs (%s != %s)' % (ev_sha, want_evidence))
    delta_sha = file_sha256(delta_path) if delta_path else None
    rows = []

    def add(raw, source, kind, url, final_url, retrieved_at, status, state, body_sha, nbytes, ref, detail=None):
        for sid, version in raws[raw]:
            rows.append((raw, sid, version, source, kind, url, final_url, retrieved_at, status, state, body_sha,
                         int(body_sha == raw) if body_sha else None, nbytes, ref,
                         json.dumps(detail, sort_keys=True, separators=(',', ':')) if detail else None))

    ev = _ro(evidence_path)
    try:
        log('scan retrievals')
        for (i, kind, url, final_url, at, status, state, sha, err, setid, version) in ev.execute(
                'SELECT id,kind,url,final_url,retrieved_at,status,state,artifact_sha256,error,setid,version FROM retrievals '
                'WHERE artifact_sha256 IS NOT NULL'):
            if sha in raws:
                detail = {k: v for k, v in (('error', err), ('local_body_import', url.startswith('file:') or None)) if v}
                add(sha, 'evidence.retrievals', kind, url, final_url, at, status, state, sha, None, 'retrievals:%d' % i, detail)
        log('scan http_requests')
        for (i, url, final_url, epoch, started, status, sha, nbytes, err, archive) in ev.execute(
                'SELECT id,url,final_url,started_epoch,started_at,status,sha256,bytes,error,archive_request FROM http_requests '
                'WHERE sha256 IS NOT NULL'):
            if sha in raws:
                detail = {k: v for k, v in (('error', err), ('archive_request', archive)) if v}
                add(sha, 'evidence.http_requests', 'archive-version' if 'getArchivalFile' in (url or '') else 'http-request',
                    url, final_url, started or _iso(epoch), status, None, sha, nbytes, 'http_requests:%d' % i, detail)
        log('bulk members')
        for (i, prov) in ev.execute('SELECT id,provenance_json FROM bulk_member_sources'):
            p = json.loads(prov)
            hit = [h for h in (p.get('ZIP_sha256'), p.get('XML_sha256')) if h in raws]
            ids = p.get('bulk_range_retrieval_ids') or []
            ranges = ev.execute('SELECT status,retrieved_at FROM retrievals WHERE id IN (%s)' % ','.join('?' * len(ids)), ids).fetchall() if ids else []
            for h in dict.fromkeys(hit):
                add(h, 'evidence.bulk_member_sources', 'bulk-member', p.get('url'), None,
                    min((r[1] for r in ranges), default=None), None, 'offline-member', h, None, 'bulk_member_sources:%d' % i,
                    {'member': p.get('member'), 'xml_member': p.get('xml_member'), 'transport': p.get('transport'),
                     'source_ledger_sha256': p.get('source_ledger_sha256'),
                     'range_requests': len(ranges), 'range_statuses': sorted({r[0] for r in ranges if r[0] is not None}),
                     'range_retrieved_first': min((r[1] for r in ranges), default=None),
                     'range_retrieved_last': max((r[1] for r in ranges), default=None)})
    finally:
        ev.close()
    if delta_path:
        log('delta admissions')
        d = _ro(Path(delta_path).resolve())
        try:
            for (i, raw, led, back, gap) in d.execute('SELECT id,raw_sha256,ledger_requests_json,backfill_ledger_requests_json,gap_repair_json FROM admissions'):
                if raw not in raws:
                    continue
                for lane, blob in (('ledger', led), ('backfill-ledger', back)):
                    for r in json.loads(blob or '[]'):
                        add(raw, 'evidence-delta.admissions', lane, r.get('url'), None, r.get('started_at'), r.get('status'), None,
                            r.get('sha256'), r.get('bytes'), 'admissions:%d' % i,
                            {'evidence_v1_http_request_id': r.get('evidence_v1_http_request_id'), 'same_url_as_target': r.get('same_url_as_target')})
                g = json.loads(gap or '{}')
                if g:
                    add(raw, 'evidence-delta.admissions', 'gap-repair', g.get('url'), None, g.get('gap_ledger_started_at'), g.get('http_status'),
                        None, raw if g.get('confirms_sha256_equal_to_held_body') else None, g.get('bytes'), 'admissions:%d' % i,
                        {k: g.get(k) for k in ('gap_ledger_request_id', 'gap_repair_attempt_id', 'identity_ok') if k in g})
        finally:
            d.close()
    rows.sort(key=lambda r: (r[0], r[1], r[2], r[3], r[13], r[7] or '', r[5] or ''))
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_name(output.name + '.partial')
    if tmp.exists():
        tmp.unlink()
    c = sqlite3.connect(tmp)
    try:
        c.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;'
                        'CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT) WITHOUT ROWID;'
                        'CREATE TABLE routes(raw_sha256 TEXT NOT NULL, setid TEXT NOT NULL, version INTEGER NOT NULL, source TEXT, kind TEXT,'
                        ' url TEXT, final_url TEXT, retrieved_at TEXT, http_status INTEGER, state TEXT, body_sha256 TEXT, body_matches_raw INTEGER,'
                        ' bytes INTEGER, evidence_ref TEXT, detail TEXT);')
        c.executemany('INSERT INTO routes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', rows)
        c.execute('CREATE INDEX routes_raw ON routes(raw_sha256, setid, version)')
        covered = len({r[0] for r in rows})
        c.executemany('INSERT INTO meta VALUES(?,?)', [
            ('format', FORMAT), ('snapshot', snapshot), ('evidence_sha256', ev_sha), ('delta_sha256', delta_sha or ''),
            ('raw_hashes_in_release', str(len(raws))), ('raw_hashes_with_routes', str(covered)), ('rows', str(len(rows)))])
        c.commit()
        if c.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Routes lookup integrity failure')
    finally:
        c.close()
    tmp.replace(output)
    return {'sha256': file_sha256(output), 'bytes': output.stat().st_size, 'rows': len(rows), 'raw_hashes_in_release': len(raws),
            'raw_hashes_with_routes': covered, 'evidence_sha256': ev_sha, 'delta_sha256': delta_sha}


# -- reading ------------------------------------------------------------------
class RoutesLookup:
    """Read-only, lazily verified. Unusable lookups report why; their content is never served."""

    def __init__(self, path, expected_sha256, snapshot, verify='sha256'):
        self.path, self.expected, self.snapshot, self.verify = Path(path), expected_sha256, snapshot, verify
        self.conn = None
        self.unavailable = None
        self.sha256 = expected_sha256
        self.meta = {}

    def _open(self):
        if self.conn is not None or self.unavailable:
            return
        try:
            size_ok = self.path.is_file()
            if not size_ok:
                raise OSError('routes lookup file absent')
            if self.verify == 'sha256' and file_sha256(self.path) != self.expected:
                self.unavailable = 'pinned routes lookup failed SHA-256 verification; its content is not used'
                return
            conn = sqlite3.connect('file:%s?mode=ro&immutable=1' % self.path, uri=True)
            conn.execute('PRAGMA query_only=ON')
            self.meta = dict(conn.execute('SELECT k,v FROM meta'))
            if self.meta.get('format') != FORMAT or self.meta.get('snapshot') != self.snapshot:
                conn.close()
                self.unavailable = 'routes lookup belongs to a different release or format; its content is not used'
                return
            self.conn = conn
        except (OSError, sqlite3.Error) as exc:
            self.unavailable = 'routes lookup unavailable (%s: %s)' % (type(exc).__name__, str(exc)[:80])

    def routes(self, raw_sha256, setid, version):
        """(status, reason, [route dict]); status is observed | none_recorded | unavailable."""
        self._open()
        if self.conn is None:
            return 'unavailable', self.unavailable, []
        cur = self.conn.execute('SELECT %s FROM routes WHERE raw_sha256=? AND setid=? AND version=? ORDER BY rowid'
                                % ','.join(FIELDS), (raw_sha256, setid, version))
        out = []
        for row in cur:
            r = dict(zip(FIELDS, row))
            r['detail'] = json.loads(r['detail']) if r['detail'] else None
            r['body_matches_raw'] = None if r['body_matches_raw'] is None else bool(r['body_matches_raw'])
            out.append(r)
        return ('observed' if out else 'none_recorded'), None, out

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None


def _official(url):
    try:
        u = urlsplit(url or '')
        return u.scheme == 'https' and (u.hostname or '').endswith('nlm.nih.gov') and not u.username and not u.password
    except ValueError:
        return False


def _instant(value):
    """UTC second of an ISO timestamp (the same request is logged by two ledgers in different notations)."""
    import datetime as dt
    try:
        d = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if d.tzinfo is None:
            return str(value)
        return d.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')
    except ValueError:
        return str(value)


def publisher_references(routes, constructed):
    """Observed references (200 responses whose body hashed to the raw body, official hosts) replace the constructed route."""
    refs = []
    seen = set()
    for r in routes:
        if r['kind'] == 'bulk-member':
            key = ('bulk', r['url'], (r['detail'] or {}).get('member'))
            if key not in seen:
                seen.add(key)
                d = r['detail'] or {}
                refs.append({'url': r['url'], 'kind': 'bulk-member', 'observed': True, 'member': d.get('member'), 'status': None,
                             'transport': d.get('transport'), 'artifact_sha256': r['body_sha256']})
            continue
        url = r['final_url'] or r['url']
        if r['http_status'] != 200 or not r['body_matches_raw'] or not _official(url) or (r['detail'] or {}).get('error'):
            continue
        kind = 'archive-version' if 'getArchivalFile' in url else 'exact-version' if 'getFile.cfm' in url else 'publisher-response'
        key = (url, r['http_status'], _instant(r['retrieved_at']))
        if key in seen:
            continue
        seen.add(key)
        refs.append({'url': url, 'kind': kind, 'observed': True, 'retrieved_at': r['retrieved_at'], 'status': r['http_status'],
                     'artifact_sha256': r['body_sha256']})
    return refs or constructed


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--config', required=True, help='rebuild configuration (without routes pin)')
    p.add_argument('--evidence', required=True)
    p.add_argument('--delta')
    p.add_argument('--output', required=True)
    a = p.parse_args(argv)
    print(json.dumps(build(a.config, a.evidence, a.output, a.delta, lambda m: print(m, file=sys.stderr, flush=True)), indent=2))


if __name__ == '__main__':
    main()
