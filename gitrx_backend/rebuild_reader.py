"""Read-only adapter for the Step E rebuild (per-setid-prefix SQLite shards).

Implements the reader interface the shared Query layer uses (families, versions,
read, evidence, inventory) plus the change-record layer (change_pairs,
change_record). Reads never collect, parse, classify or promote anything: the
release is the pinned COMPLETE.json (its SHA-256 is the snapshot identifier) and
each shard is checked against the size and SHA-256 recorded there before first use.
"""
from collections import OrderedDict
from pathlib import Path
import hashlib
import json
import sqlite3
import zlib

from .readers import EvidenceReader, canonical_sid, read_bounded

FORMAT = 'gitrx-rebuild-1'
NAMES_FORMAT = 'gitrx-rebuild-names-1'
SHARD_CACHE = 6
FAMILY_CACHE = 64
LABEL_CACHE = 8
NO_DOCUMENT = {'no_raw', 'raw_not_found', 'raw_hash_mismatch', 'raw_unreadable', 'parse_exception'}


class ReleaseUnavailable(OSError):
    """The pinned rebuild (or its external volume) cannot be read; never silently replaced."""


def file_sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 22), b''):
            h.update(chunk)
    return h.hexdigest()


def _unz(blob):
    return zlib.decompress(blob).decode('utf-8', 'surrogatepass')


def _json(value, default=None):
    return json.loads(value) if value else default


class RebuildReader(EvidenceReader):
    kind = 'rebuild'
    uninstalled_shards = ()

    def __init__(self, root, complete_sha256, names_path, names_sha256, verify_shards='sha256', description=None,
                 search_index=None, search_index_sha256=None, retrieval_routes=None, retrieval_routes_sha256=None):
        if verify_shards not in ('sha256', 'size'):
            raise ValueError('verify_shards must be sha256 or size')
        self.root = Path(root)
        self.description = description or {}
        self.verify_shards = verify_shards
        self.shard_conns = OrderedDict()
        self.verified = set()
        self.family_cache = OrderedDict()
        self.label_cache = OrderedDict()
        self._families = None
        self.conn = None
        self.names = None
        complete = self.root / 'COMPLETE.json'
        try:
            if not self.root.is_dir():
                raise FileNotFoundError(str(self.root))
            raw = read_bounded(complete)
        except OSError as exc:
            raise ReleaseUnavailable(
                'Pinned rebuild release is unavailable (%s: %s). Is the external SSD mounted? '
                'To download from Hugging Face instead run: ./label-history get NAME. '
                'No fallback to another release was attempted.' % (type(exc).__name__, self.root)) from None
        if hashlib.sha256(raw).hexdigest() != complete_sha256:
            raise ValueError('Rebuild COMPLETE.json hash does not match the pinned configuration')
        self.complete = json.loads(raw)
        if self.complete.get('format') != FORMAT:
            raise ValueError('Unsupported rebuild format')
        self.snapshot = complete_sha256
        self.shard_info = self.complete['shards']
        self.versions_info = self.complete['config']['versions']
        try:
            if file_sha256(names_path) != names_sha256:
                raise ValueError('Name index hash does not match the pinned configuration')
            self.names = sqlite3.connect(Path(names_path).resolve().as_uri() + '?mode=ro', uri=True)
        except (OSError, sqlite3.Error) as exc:
            raise ReleaseUnavailable('Name index unavailable: %s' % type(exc).__name__) from None
        self.names.execute('PRAGMA query_only=ON')
        meta = dict(self.names.execute('SELECT k,v FROM meta'))
        if meta.get('format') != NAMES_FORMAT or meta.get('complete_sha256') != complete_sha256:
            self.close()
            raise ValueError('Name index belongs to a different rebuild release')
        self.names_sha256 = names_sha256
        self.routes = None
        self.retrieval_routes_sha256 = retrieval_routes_sha256
        if retrieval_routes is not None:
            from .rebuild_routes import RoutesLookup
            self.routes = RoutesLookup(retrieval_routes, retrieval_routes_sha256, complete_sha256, verify_shards)
        self.search_projection = None
        self.search_index_sha256 = search_index_sha256
        self._family_index = None
        if search_index is not None:
            from .rebuild_search import SearchProjection
            try:
                self.search_projection = SearchProjection(search_index, search_index_sha256, self.snapshot, verify_shards)
            except BaseException:
                self.close()
                raise

    # -- shards
    def _shard(self, sid):
        prefix = sid[:2]
        if prefix not in self.shard_info:
            return None
        if prefix in self.shard_conns:
            self.shard_conns.move_to_end(prefix)
            return self.shard_conns[prefix]
        path = self.root / 'shards' / (prefix + '.sqlite')
        try:
            size = path.stat().st_size
        except OSError as exc:
            hint = ''
            if isinstance(exc, FileNotFoundError):
                # Whole-collection scans address a shard by a placeholder setid; suggest --all there.
                real = len(sid) == 36 and sid[2:].strip('0-') != ''
                hint = ('; to download it run: ./label-history get %s' % sid if real else
                        '; a whole-collection scan needs every shard: ./label-history get --all (about 12 GB), '
                        'or ./label-history get --search for sub-second search')
            raise ReleaseUnavailable('Rebuild shard %s is unavailable (%s); evidence is unavailable, not a negative result%s'
                                     % (prefix, type(exc).__name__, hint)) from None
        info = self.shard_info[prefix]
        if prefix not in self.verified:
            if size != info['bytes']:
                raise ValueError('Rebuild shard %s size differs from COMPLETE.json' % prefix)
            if self.verify_shards == 'sha256' and file_sha256(path) != info['sha256']:
                raise ValueError('Rebuild shard %s SHA-256 differs from COMPLETE.json' % prefix)
            self.verified.add(prefix)
        conn = sqlite3.connect('file:%s?mode=ro&immutable=1' % path, uri=True)
        conn.execute('PRAGMA query_only=ON')
        self.shard_conns[prefix] = conn
        while len(self.shard_conns) > SHARD_CACHE:
            self.shard_conns.popitem(last=False)[1].close()
        return conn

    # -- family register (names are selection aids only)
    def families(self):
        if self._families is None:
            self._families = [{'setid': s, 'title': t or ''}
                              for s, t in self.names.execute('SELECT setid,title FROM families ORDER BY setid')]
        return self._families

    def search_plan(self, phrase):
        """Candidate proposer for a literal phrase, or None (no projection, or phrase unusable): callers must scan."""
        if self.search_projection is None:
            return None
        if self._family_index is None:
            self._family_index = {f['setid']: i for i, f in enumerate(self.families())}
        return self.search_projection.plan(phrase, self._family_index)

    def family(self, sid):
        row = self.names.execute('SELECT setid,title FROM families WHERE setid=?', (sid,)).fetchone()
        return None if row is None else {'setid': row[0], 'title': row[1] or ''}

    def _family_rows(self, sid):
        sid = canonical_sid(sid)
        if sid in self.family_cache:
            self.family_cache.move_to_end(sid)
            return self.family_cache[sid]
        conn = self._shard(sid)
        if conn is None:
            return []
        cols = ('version,inventory_states,raw_sha256,xml_member,xml_sha256,pub_date,effective_date,status,'
                'status_reasons,parser_output_sha256,n_sections,buckets,error,warnings,diagnostics_z')
        names = [c.strip() for c in cols.split(',')]
        rows = [dict(zip(names, r)) for r in conn.execute(
            'SELECT %s FROM versions WHERE setid=? ORDER BY version' % cols, (sid,))]
        self.family_cache[sid] = rows
        while len(self.family_cache) > FAMILY_CACHE:
            self.family_cache.popitem(last=False)
        return rows

    def versions(self, sid):
        return [{'version': r['version'], 'raw_sha256': r['raw_sha256'], 'effective_date': r['effective_date'],
                 'publication_dates': [r['pub_date']] if r['pub_date'] else [],
                 'status': r['status'], 'parsed': r['status'] not in NO_DOCUMENT,
                 'inventory_states': r['inventory_states'], 'xml_member': r['xml_member'],
                 'n_sections': r['n_sections'], 'parser_output_sha256': r['parser_output_sha256']}
                for r in self._family_rows(sid)]

    def _row(self, sid, version):
        row = next((r for r in self._family_rows(sid) if r['version'] == version), None)
        if row is None:
            raise ValueError('Version outside selected release')
        return row

    def evidence(self, sid, version):
        sid = canonical_sid(sid)
        row = self._row(sid, version)
        from .evidence_metadata import unknown_evidence
        constructed = unknown_evidence(sid, version)['publisher_references']
        if self.routes is None:
            status, reason, routes = 'unavailable', 'No retrieval-routes lookup is pinned in this configuration.', []
        elif row['raw_sha256'] is None:
            status, reason, routes = 'none_recorded', 'This version has no retained raw artifact.', []
        else:
            status, reason, routes = self.routes.routes(row['raw_sha256'], sid, version)
        if routes:
            from .rebuild_routes import publisher_references
            references = publisher_references(routes, constructed)
        else:
            references = constructed
        diagnostics = _json(_unz(row['diagnostics_z']), []) if row['diagnostics_z'] else []
        return {'metadata_status': 'pinned', 'raw_sha256': row['raw_sha256'],
                'extraction': {'status': row['status'], 'status_reasons': _json(row['status_reasons'], []),
                               'diagnostics': diagnostics, 'warnings': _json(row['warnings'], []),
                               'error': row['error'], 'buckets': _json(row['buckets'], None),
                               'parser_versions': [self.versions_info['parser']],
                               'meaning': 'Parser status (spl-sections-0.2.x status model), not independent proof of '
                                          'complete source fidelity or clinical meaning.'},
                'publisher_references': references,
                'retrieval_routes': routes, 'retrieval_routes_status': status,
                'retrieval_routes_note': reason or (
                    'Routes actually observed for this raw artifact in the retained evidence (URL, time, HTTP status, whether the '
                    'response body hashed to this raw SHA-256); not proof the route still serves it. publisher_references with '
                    'observed true are drawn from them; observed false is a constructed route that was not verified.'
                    if routes else 'No retrieval route for this raw artifact is recorded in the pinned lookup.')}

    def inventory(self, sid):
        sid = canonical_sid(sid)
        conn = self._shard(sid)
        fam = conn.execute('SELECT n_inventory,n_raw,n_parsed,min_version,max_version,first_parsed_version,'
                           'versions_before_first_parsed FROM families WHERE setid=?', (sid,)).fetchone() if conn else None
        if fam is None:
            raise ValueError('Family absent from the rebuild inventory')
        numbers = [r['version'] for r in self._family_rows(sid)]
        return {'metadata_status': 'pinned', 'status': 'rebuild-inventory',
                'captured_versions': fam[0], 'known_target_versions': numbers, 'known_target_count': fam[0],
                'versions_with_raw': fam[1], 'versions_parsed': fam[2],
                'first_parsed_version': fam[5], 'versions_before_first_parsed': fam[6],
                'unknown_upstream_history': None, 'historical_completeness_claim': False,
                'basis': 'Evidence inventory of listed versions at build time; versions without raw are listed with '
                         'status no_raw, never invented. Upstream history beyond the inventory is unknown.'}

    # -- section occurrences in the shape the shared Query layer reads
    def read(self, sid, version):
        sid = canonical_sid(sid)
        key = (sid, version)
        if key in self.label_cache:
            self.label_cache.move_to_end(key)
            sections, citation = self.label_cache[key]
            return [dict(s) for s in sections], dict(citation)
        row = self._row(sid, version)
        if row['status'] in NO_DOCUMENT or row['raw_sha256'] is None:
            raise ValueError('Version %d has no parsed document (status %s); there is no text to read'
                             % (version, row['status']))
        conn = self._shard(sid)
        texts = {}

        def text(h):
            if h is None:
                return ''
            if h not in texts:
                texts[h] = _unz(conn.execute('SELECT z FROM texts WHERE h=?', (h,)).fetchone()[0])
            return texts[h]
        raw = conn.execute('SELECT ordinal,parent_ordinal,section_number,title,code,section_key,kind,srlc_group,'
                           'alt_srlc_group,topic_group,group_basis,text_h,table_h,excerpt_h FROM sections '
                           'WHERE setid=? AND version=? ORDER BY ordinal', (sid, version)).fetchall()
        codes = {r[0]: (r[4], r[1]) for r in raw}
        sections = []
        for (ordinal, parent, number, title, code, skey, kind, group, alt, topic, basis, th, tbh, eh) in raw:
            chain, seen, p = [], set(), parent
            while p is not None and p not in seen and p in codes:
                seen.add(p)
                chain.append(codes[p][0] or '')
                p = codes[p][1]
            body, table = text(th), text(tbh)
            sections.append({
                'ordinal': ordinal, 'parent_ordinal': parent, 'loinc_code': code, 'code_system': None,
                'title': title, 'ancestry_path': '/' + '/'.join(reversed(chain)) if chain else '/',
                'own_text': body + ('\n\n' + table if table else ''),
                'extra': {'section_number': number, 'section_key': skey, 'section_kind': kind,
                          'srlc_group': group, 'alt_srlc_group': alt, 'topic_group': topic, 'group_basis': basis,
                          'channel_characters': {'text': len(body), 'table': len(table), 'excerpt': len(text(eh))}}})
        citation = self.evidence_citation(sid, version, {
            'snapshot': self.snapshot, 'raw_sha256': row['raw_sha256'], 'xml_member': row['xml_member'],
            'xml_sha256': row['xml_sha256'], 'parser_version': self.versions_info['parser'],
            'parser_output_sha256': row['parser_output_sha256'], 'parse_status': row['status'],
            'shard': sid[:2]})
        self.label_cache[key] = (sections, citation)
        while len(self.label_cache) > LABEL_CACHE:
            self.label_cache.popitem(last=False)
        return [dict(s) for s in sections], dict(citation)

    # -- change-record layer
    def change_pairs(self, sid):
        sid = canonical_sid(sid)
        conn = self._shard(sid)
        if conn is None:
            return []
        groups = {}
        for f, t, g, n, ref in conn.execute(
                'SELECT from_version,to_version,srlc_group,count(*),sum(reference_only) FROM changes '
                'WHERE setid=? GROUP BY 1,2,3', (sid,)):
            groups.setdefault((f, t), {})[g or 'unclassified'] = {'changes': n, 'reference_only': ref or 0}
        out = []
        for r in conn.execute('SELECT from_version,to_version,consecutive,gap_versions,known_missing,comparison,'
                              'comparison_reasons,from_raw_sha256,to_raw_sha256,from_pub_date,to_pub_date,'
                              'from_effective_date,to_effective_date,n_changes,n_formatting_changes,record_sha256 '
                              'FROM pairs WHERE setid=? ORDER BY from_version,to_version', (sid,)):
            out.append({'from_version': r[0], 'to_version': r[1], 'consecutive': bool(r[2]), 'gap_versions': r[3],
                        'known_missing': _json(r[4], []), 'comparison': r[5], 'comparison_reasons': _json(r[6], []),
                        'from_raw_sha256': r[7], 'to_raw_sha256': r[8], 'from_publication_date': r[9],
                        'to_publication_date': r[10], 'from_effective_date': r[11], 'to_effective_date': r[12],
                        'n_changes': r[13], 'n_formatting_changes': r[14], 'record_sha256': r[15],
                        'groups': groups.get((r[0], r[1]), {})})
        return out

    def change_record(self, sid, from_version, to_version):
        """Full stored record (verbatim spans restored) and whether it reproduces its pinned record_sha256."""
        from gitrx_core.rebuild import full_record
        from gitrx_core import changes as frozen_changes
        sid = canonical_sid(sid)
        conn = self._shard(sid)
        record = full_record(conn, sid, from_version, to_version) if conn else None
        if record is None:
            return None, None
        stored = conn.execute('SELECT record_sha256 FROM pairs WHERE setid=? AND from_version=? AND to_version=?',
                              (sid, from_version, to_version)).fetchone()[0]
        return record, frozen_changes.record_hash(record) == stored

    # -- discovery
    def release_description(self):
        c = self.complete['counts']
        statuses = {k.split(':', 1)[1]: v for k, v in c.items() if k.startswith('status:')}
        cfg = self.complete['config']
        return dict(self.description, **{
            'kind': 'rebuild', 'complete_json_sha256': self.snapshot, 'format': self.complete['format'],
            'families': c['families'], 'versions': sum(statuses.values()), 'version_status_counts': statuses,
            'versions_without_raw': statuses.get('no_raw', 0),
            'change_pairs': c['pairs'], 'nonconsecutive_pairs': c['pairs_nonconsecutive'],
            'change_records': c['changes'], 'formatting_changes': c['formatting_changes'],
            'comparison_counts': self.complete['comparison_counts'],
            'parser_version': self.versions_info['parser'], 'groups_version': self.versions_info['groups'],
            'changes_version': self.versions_info['changes'],
            'frozen_code_sha256': cfg['frozen_code_sha256'], 'inventory_fingerprint': cfg['inventory_fingerprint'],
            'evidence_sha256': cfg['inputs']['evidence_sha256'], 'names_index_sha256': self.names_sha256,
            'shard_verification': self.verify_shards,
            'date_semantics': 'DailyMed publication and SPL effective dates are not FDA approval dates; '
                              'retrieval times are separate again.',
            'family_titles': 'Latest DailyMed listing title (selection aid only), from the pinned name index.'})

    def installation_plan(self, selected):
        prefixes = sorted({s[:2] for s in selected})
        return [{'prefix': p, 'file': 'shards/%s.sqlite' % p, 'bytes': self.shard_info[p]['bytes'],
                 'sha256': self.shard_info[p]['sha256'], 'installed': (self.root / 'shards' / (p + '.sqlite')).is_file()}
                for p in prefixes if p in self.shard_info], \
            [p for p in sorted(self.shard_info) if p not in prefixes]

    def close(self):
        for conn in self.shard_conns.values():
            conn.close()
        self.shard_conns.clear()
        self.family_cache.clear()
        self.label_cache.clear()
        if self.routes is not None:
            self.routes.close()
        if self.search_projection is not None:
            self.search_projection.close()
            self.search_projection = None
        if self.names is not None:
            self.names.close()
            self.names = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
