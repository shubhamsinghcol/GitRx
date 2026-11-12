"""Deterministic global-law-style Git export of a pinned rebuild release ("gitrx-git-export-1").

One bare repo per group of consecutive rebuild shards (setid hex-prefix ranges), one linear history per
repo, one commit per captured version, ordered by (key date, setid, version). Each commit sets
<setid>/label.md (all sections of that version, literal text inside deterministic Markdown framing) and
<setid>/meta.json. Versions without raw (no_raw etc.) commit meta.json only; label.md is left as it was.
A meta repo carries the README, MANIFEST.json, the setid/version -> (repo, commit) map and verification.

Dates: the commit date is the key date at UTC midnight. The key date is the DailyMed publication date
when valid, raised to the previous version's key date when earlier (so history never runs backwards within a
family), carried forward when missing, else the next known date of the family, else 1970-01-01. Neither
date is an FDA approval date. Nothing is pushed or published here; reads of the rebuild never modify it.
"""
import argparse
import calendar
import gzip
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import zlib
from datetime import datetime
from pathlib import Path

EXPORT_FORMAT = 'gitrx-git-export-1'
REBUILD_FORMAT = 'gitrx-rebuild-1'
PINNED_COMPLETE_SHA256 = '8dc88e7a55831af605dbedd2a2b3bccdbd6575d51eaacb62c75c399975ce644b'
PINNED_NAMES_SHA256 = '1b54fe9ff866252ef2e1af528ff608349d17993d358632b4d73f74fb7802e3e4'
AUTHOR_NAME = 'GitRx'
AUTHOR_EMAIL = 'gitrx@users.noreply.github.com'
AUTHOR = '%s <%s>' % (AUTHOR_NAME, AUTHOR_EMAIL)
NO_DOCUMENT = {'no_raw', 'raw_not_found', 'raw_hash_mismatch', 'raw_unreadable', 'parse_exception'}
MAX_FILE_BYTES = 50 * 1000 * 1000
MIN_FREE_BYTES = 60 * (1 << 30)
LABEL_MAGIC = '<!-- gitrx-label-md-1 -->'
DATE_MEANING = ('dailymed_publication_date is the date DailyMed published this version; spl_effective_date is the '
                'effective date inside the SPL document. Neither is an FDA approval date. First observed is not '
                'actual introduction.')
DEFAULT_ROOT = '/Volumes/Untitled/GitRx/rebuild/spl-sections-0.2.4__srlc-changes-0.3.2'
DEFAULT_OUT = '/Volumes/Untitled/GitRx/git-export/gitrx-git-export-1__spl-sections-0.2.4'


class ExportError(RuntimeError):
    pass


def module_sha256():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def file_sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 22), b''):
            h.update(chunk)
    return h.hexdigest()


def canon(obj):
    """Canonical JSON: sorted keys, ASCII only, fixed separators."""
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def enc(s):
    return s.encode('utf-8', 'surrogatepass')


# ---------------------------------------------------------------- Markdown framing

def _one_line(s):
    return ' '.join((s or '').split())


def render_label(sections):
    """sections: list of dicts ordinal,parent_ordinal,code,title,text in document order -> bytes."""
    by_ord = {s['ordinal']: s for s in sections}
    parts = [LABEL_MAGIC + '\n']
    for s in sections:
        depth, seen, p = 2, set(), s['parent_ordinal']
        while p is not None and p in by_ord and p not in seen and depth < 6:
            seen.add(p)
            depth += 1
            p = by_ord[p]['parent_ordinal']
        header = canon({'chars': len(s['text']), 'code': s['code'], 'ordinal': s['ordinal'],
                        'parent': s['parent_ordinal'], 'title': s['title']}).replace('>', '\\u003e')
        heading = '%s %s | %s' % ('#' * depth, s['code'] or 'uncoded', _one_line(s['title']) or '(no title)')
        parts.append('<!-- gitrx:section %s -->\n%s\n\n%s\n\n' % (header, heading, s['text']))
    return enc(''.join(parts))


def parse_label(blob):
    """Inverse of the framing: returns [(header dict, text)]; text is byte-exact as stored."""
    text = blob.decode('utf-8', 'surrogatepass')
    if not text.startswith(LABEL_MAGIC + '\n'):
        raise ValueError('not a gitrx label.md')
    pos, out = len(LABEL_MAGIC) + 1, []
    while pos < len(text):
        eol = text.index('\n', pos)
        line = text[pos:eol]
        if not (line.startswith('<!-- gitrx:section ') and line.endswith(' -->')):
            raise ValueError('malformed section header')
        header = json.loads(line[len('<!-- gitrx:section '):-len(' -->')])
        pos = eol + 1
        pos = text.index('\n', pos) + 1          # heading line
        if text[pos] != '\n':
            raise ValueError('malformed framing')
        pos += 1
        body = text[pos:pos + header['chars']]
        pos += header['chars']
        if text[pos:pos + 2] != '\n\n':
            raise ValueError('malformed section end')
        pos += 2
        out.append((header, body))
    return out


# ---------------------------------------------------------------- dates / ordering

def valid_date(s):
    if not s or len(s) != 10:
        return None
    try:
        datetime.strptime(s, '%Y-%m-%d')
        return s
    except ValueError:
        return None


def date_stamp(day):
    return calendar.timegm(datetime.strptime(day, '%Y-%m-%d').timetuple())


def assign_key_dates(rows):
    """rows: dicts with setid, version, pub_date. Adds key_date and key_date_source in place."""
    by_family = {}
    for r in rows:
        by_family.setdefault(r['setid'], []).append(r)
    for fam in by_family.values():
        fam.sort(key=lambda r: r['version'])
        prev = None
        for r in fam:
            own = valid_date(r['pub_date'])
            if own is None:
                r['key_date'], r['key_date_source'] = (prev, 'carried_forward') if prev else (None, None)
            elif prev and own < prev:
                r['key_date'], r['key_date_source'] = prev, 'publication_date_raised_to_previous_version'
            else:
                r['key_date'], r['key_date_source'] = own, 'publication_date'
            prev = r['key_date'] or prev
        nxt = next((r['key_date'] for r in fam if r['key_date']), None)
        for r in fam:
            if r['key_date'] is None:
                r['key_date'], r['key_date_source'] = (nxt, 'next_known_date') if nxt else ('1970-01-01', 'epoch_no_date')
    return rows


def order_rows(rows):
    return sorted(assign_key_dates(rows), key=lambda r: (r['key_date'], r['setid'], r['version']))


# ---------------------------------------------------------------- release access

def group_prefixes(shards, groups):
    shards = sorted(shards)
    if groups < 1 or len(shards) % groups:
        raise ValueError('groups must divide the shard count')
    n = len(shards) // groups
    return [shards[i * n:(i + 1) * n] for i in range(groups)]


def repo_name(prefixes):
    return 'gitrx-labels-%s-%s' % (prefixes[0], prefixes[-1])


class Release:
    def __init__(self, root, complete_sha256=PINNED_COMPLETE_SHA256, names_path=None, names_sha256=PINNED_NAMES_SHA256):
        self.root = Path(root)
        try:
            raw = (self.root / 'COMPLETE.json').read_bytes()
        except OSError as exc:
            raise ExportError('rebuild unavailable (%s); is the SSD mounted?' % type(exc).__name__)
        if hashlib.sha256(raw).hexdigest() != complete_sha256:
            raise ExportError('COMPLETE.json sha256 differs from the pinned value; refusing')
        self.complete = json.loads(raw)
        if self.complete.get('format') != REBUILD_FORMAT:
            raise ExportError('unsupported rebuild format')
        self.complete_sha256 = complete_sha256
        self.shards = self.complete['shards']
        self.parser = self.complete['config']['versions']['parser']
        self.conns = {}
        self.names = None
        if names_path:
            if names_sha256 and file_sha256(names_path) != names_sha256:
                raise ExportError('names index sha256 differs from the pinned value')
            self.names = sqlite3.connect(Path(names_path).resolve().as_uri() + '?mode=ro', uri=True)
        self.names_sha256 = names_sha256 if names_path else None
        self.title_cache = {}

    def shard(self, prefix):
        if prefix not in self.conns:
            path = self.root / 'shards' / (prefix + '.sqlite')
            info = self.shards[prefix]
            if path.stat().st_size != info['bytes'] or file_sha256(path) != info['sha256']:
                raise ExportError('shard %s differs from COMPLETE.json' % prefix)
            conn = sqlite3.connect('file:%s?mode=ro&immutable=1' % path, uri=True)
            conn.execute('PRAGMA query_only=ON')
            self.conns[prefix] = conn
        return self.conns[prefix]

    def version_rows(self, prefixes):
        rows = []
        for p in prefixes:
            for r in self.shard(p).execute(
                    'SELECT setid,version,pub_date,effective_date,status,raw_sha256,xml_sha256,parser_output_sha256,'
                    'n_sections,status_reasons FROM versions ORDER BY setid,version'):
                rows.append(dict(zip(('setid', 'version', 'pub_date', 'effective_date', 'status', 'raw_sha256',
                                      'xml_sha256', 'parser_output_sha256', 'n_sections', 'status_reasons'), r)))
        return rows

    def title(self, setid):
        if setid not in self.title_cache:
            t = None
            if self.names is not None:
                row = self.names.execute('SELECT title FROM families WHERE setid=?', (setid,)).fetchone()
                t = row[0] if row else None
            self.title_cache[setid] = t
        return self.title_cache[setid]

    def sections(self, setid, version):
        conn = self.shard(setid[:2])
        out = []
        for ordinal, parent, title, code, z1, z2 in conn.execute(
                'SELECT s.ordinal,s.parent_ordinal,s.title,s.code,t1.z,t2.z FROM sections s '
                'LEFT JOIN texts t1 ON t1.h=s.text_h LEFT JOIN texts t2 ON t2.h=s.table_h '
                'WHERE s.setid=? AND s.version=? ORDER BY s.ordinal', (setid, version)):
            body = zlib.decompress(z1).decode('utf-8', 'surrogatepass') if z1 is not None else ''
            table = zlib.decompress(z2).decode('utf-8', 'surrogatepass') if z2 is not None else ''
            out.append({'ordinal': ordinal, 'parent_ordinal': parent, 'code': code, 'title': title,
                        'text': body + ('\n\n' + table if table else '')})
        return out

    def close(self):
        for c in self.conns.values():
            c.close()
        self.conns.clear()
        if self.names is not None:
            self.names.close()
            self.names = None


# ---------------------------------------------------------------- commit content

def meta_dict(release, row, label_state, label_sha, title):
    return {
        'dates': {'dailymed_publication_date': row['pub_date'], 'spl_effective_date': row['effective_date'],
                  'meaning': DATE_MEANING},
        'export_format': EXPORT_FORMAT,
        'key_date': row['key_date'], 'key_date_source': row['key_date_source'],
        'label_md': label_state, 'label_md_sha256': label_sha,
        'n_sections': row['n_sections'],
        'parser_output_sha256': row['parser_output_sha256'],
        'parser_version': release.parser,
        'parse_status': row['status'],
        'parse_status_reasons': json.loads(row['status_reasons']) if row['status_reasons'] else [],
        'raw_sha256': row['raw_sha256'],
        'setid': row['setid'],
        'snapshot': release.complete_sha256,
        'title': title,
        'title_meaning': 'Latest DailyMed listing title of the family (selection aid), not necessarily the title at this version.',
        'version': row['version'],
        'xml_sha256': row['xml_sha256'],
    }


def commit_message(release, row, title):
    t = _one_line(title) or '(title unknown)'
    return ('%s (setid %s) v%d\n\nDrug-Title: %s\nSetid: %s\nVersion: %d\nRaw-SHA256: %s\nParse-Status: %s\n'
            'DailyMed-Publication-Date: %s\nSPL-Effective-Date: %s\nSnapshot: %s\nExport-Format: %s\n'
            % (t, row['setid'], row['version'], t, row['setid'], row['version'], row['raw_sha256'] or 'none',
               row['status'], row['pub_date'] or 'not recorded', row['effective_date'] or 'not recorded',
               release.complete_sha256, EXPORT_FORMAT))


def git(repo, *args, check=True):
    return subprocess.run(['git', '-C', str(repo), *args], capture_output=True, check=check).stdout.decode().strip()


def free_bytes(path):
    p = Path(path)
    while not p.exists():
        p = p.parent
    return shutil.disk_usage(p).free


def build_repo(release, prefixes, repo_path, gc=True, log=None, min_free=MIN_FREE_BYTES):
    """Build one bare repo from the given rebuild shard prefixes. Returns an info dict."""
    repo_path = Path(repo_path)
    if repo_path.exists():
        raise ExportError('refusing to overwrite %s' % repo_path)
    if free_bytes(repo_path.parent) < min_free:
        raise ExportError('free space below the %d GiB floor' % (min_free >> 30))
    t0 = time.time()
    rows = order_rows(release.version_rows(prefixes))
    repo_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['git', 'init', '--quiet', '--bare', '--object-format=sha1', '--initial-branch=main', str(repo_path)],
                   check=True)
    proc = subprocess.Popen(['git', '-C', str(repo_path), 'fast-import', '--quiet'], stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stream = proc.stdin
    max_file = 0
    n_nodoc = 0
    try:
        def data(body):
            if isinstance(body, str):
                body = enc(body)
            stream.write(b'data %d\n' % len(body))
            stream.write(body)
            stream.write(b'\n')
        for i, row in enumerate(rows):
            title = release.title(row['setid'])
            nodoc = row['status'] in NO_DOCUMENT or row['raw_sha256'] is None
            label = None
            if nodoc:
                n_nodoc += 1
                state, label_sha = 'not_available_unchanged_from_previous_commit_if_any', None
            else:
                label = render_label(release.sections(row['setid'], row['version']))
                state, label_sha = 'present', hashlib.sha256(label).hexdigest()
                max_file = max(max_file, len(label))
            meta = (canon(meta_dict(release, row, state, label_sha, title)) + '\n').encode()
            stamp = date_stamp(row['key_date'])
            stream.write(('commit refs/heads/main\nauthor %s %d +0000\ncommitter %s %d +0000\n'
                          % (AUTHOR, stamp, AUTHOR, stamp)).encode())
            data(commit_message(release, row, title))
            if label is not None:
                stream.write(('M 100644 inline %s/label.md\n' % row['setid']).encode())
                data(label)
            stream.write(('M 100644 inline %s/meta.json\n' % row['setid']).encode())
            data(meta)
            stream.write(b'\n')
            if log and i % 5000 == 0:
                log('  %s %d/%d' % (repo_path.name, i, len(rows)))
        stream.write(b'done\n')
        stream.close()
        _, err = proc.communicate()
        if proc.returncode:
            raise ExportError('git fast-import failed: ' + err.decode()[:300])
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    if max_file > MAX_FILE_BYTES:
        raise ExportError('a label.md exceeds 50 MB (%d)' % max_file)
    head = git(repo_path, 'rev-parse', 'refs/heads/main')
    commits = git(repo_path, 'rev-list', '--reverse', 'refs/heads/main').split('\n')
    if len(commits) != len(rows):
        raise ExportError('commit count differs from version count')
    info = {'head': head, 'commit_count': len(rows), 'family_count': len({r['setid'] for r in rows}),
            'meta_only_commits': n_nodoc, 'max_label_md_bytes': max_file,
            'prefixes': [prefixes[0], prefixes[-1]], 'build_seconds_pre_gc': round(time.time() - t0, 1)}
    info['_pairs'] = [(r['setid'], r['version'], c) for r, c in zip(rows, commits)]
    if gc:
        t1 = time.time()
        git(repo_path, '-c', 'pack.threads=4', '-c', 'pack.windowMemory=512m', 'gc', '--aggressive', '--prune=now', '--quiet')
        info['gc_seconds'] = round(time.time() - t1, 1)
    info['packed_bytes'] = pack_bytes(repo_path)
    return info


def pack_bytes(repo_path):
    return sum(f.stat().st_size for f in (Path(repo_path) / 'objects' / 'pack').glob('*') if f.is_file())


def write_map(path, name, pairs):
    """Deterministic gzip jsonl: setid, version, repo, commit in commit order."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode='wb', mtime=0, compresslevel=9) as g:
        for sid, ver, commit in pairs:
            g.write((canon({'commit': commit, 'repo': name, 'setid': sid, 'version': ver}) + '\n').encode())
    Path(path).write_bytes(buf.getvalue())
    return len(buf.getvalue())


# ---------------------------------------------------------------- orchestration

def state_dir(out):
    return Path(out) / '.state'


def build_all(release, out, groups, log=print):
    out = Path(out)
    plan = group_prefixes(release.shards, groups)
    for prefixes in plan:
        name = repo_name(prefixes)
        done = state_dir(out) / (name + '.done.json')
        if done.exists():
            log('skip %s (done)' % name)
            continue
        if not Path(release.root).is_dir():
            raise ExportError('rebuild volume disappeared; stopping')
        tmp = out / 'repos' / (name + '.git.partial')
        if tmp.exists():
            shutil.rmtree(tmp)               # own partial output only
        log('build %s' % name)
        info = build_repo(release, prefixes, tmp, log=log)
        pairs = info.pop('_pairs')
        (out / 'map').mkdir(parents=True, exist_ok=True)
        info['map_bytes'] = write_map(out / 'map' / (name + '.jsonl.gz'), name, pairs)
        info['name'] = name
        final = out / 'repos' / (name + '.git')
        os.rename(tmp, final)
        state_dir(out).mkdir(parents=True, exist_ok=True)
        done.write_text(canon(info) + '\n')
        log('done %s %s' % (name, canon({k: info[k] for k in ('commit_count', 'packed_bytes', 'build_seconds_pre_gc')})))


def determinism_check(release, out, groups, names, log=print):
    """Rebuild the named repos into scratch dirs and compare HEAD ids with the real build."""
    out = Path(out)
    scratch = out / '.determinism-scratch'
    results = {}
    plan = {repo_name(p): p for p in group_prefixes(release.shards, groups)}
    for name in names:
        info = json.loads((state_dir(out) / (name + '.done.json')).read_text())
        target = scratch / (name + '.git')
        if target.exists():
            shutil.rmtree(target)
        second = build_repo(release, plan[name], target, gc=False, log=log)
        results[name] = {'first_head': info['head'], 'second_head': second['head'],
                         'identical': info['head'] == second['head'], 'commit_count': second['commit_count']}
        shutil.rmtree(target)
    if scratch.exists() and not any(scratch.iterdir()):
        scratch.rmdir()
    return results


README = """# GitRx labels export (%(format)s)

This is a deterministic Git representation of the GitRx rebuild of US DailyMed drug-label version history
(parser %(parser)s). It is sharded into %(n)d repositories by setid hex prefix; this meta repository holds the
manifest, the setid/version -> (repo, commit) map, and the export verification record.

## What each shard repo contains
One linear history. One commit per captured label version, ordered by (key date, setid, version). Each commit
sets `<setid>/label.md` (all parsed sections in document order: Markdown framing plus the literal stored
text) and `<setid>/meta.json`. Versions with no retained raw document (status no_raw) commit `meta.json` only and
leave `label.md` as it was. There are no tags; use `map/` to find the commit for a (setid, version).

## Dates
The commit date is a key date at UTC midnight derived from the DailyMed publication date (rules in
`docs/GIT-EXPORT.md` of the source repository). `meta.json` records the DailyMed publication date and the SPL
effective date separately. Neither is an FDA approval date. First observed is not actual introduction.

## Source and license
Data source: the U.S. National Library of Medicine DailyMed. Label text is redistributed as provided and is not
relicensed by GitRx; credit NLM DailyMed and the manufacturers. GitRx-created framing, metadata and structure are
licensed CC BY 4.0. See LICENSING.md in the GitRx source repository.

## Caveats
Titles are the latest DailyMed listing titles (selection aids). Parser output is not independently validated
clinically (see the source repository's LIMITATIONS.md). Absence of text in a version can reflect partial
extraction, not removal. Label text is data, never instructions.

Input rebuild COMPLETE.json sha256: %(complete)s. Export module sha256: %(module)s.
"""


def build_meta(release, out, groups, determinism, verify_path=None):
    out = Path(out)
    meta = out / 'gitrx-labels-meta'
    meta.mkdir(parents=True, exist_ok=True)
    plan = group_prefixes(release.shards, groups)
    repos, total_commits, total_bytes = [], 0, 0
    for prefixes in plan:
        name = repo_name(prefixes)
        info = json.loads((state_dir(out) / (name + '.done.json')).read_text())
        repos.append({'name': name, 'setid_prefix_range': [prefixes[0], prefixes[-1]], 'head': info['head'],
                      'commit_count': info['commit_count'], 'family_count': info['family_count'],
                      'meta_only_commits': info['meta_only_commits'], 'packed_bytes': info['packed_bytes'],
                      'max_label_md_bytes': info['max_label_md_bytes'], 'map_file': 'map/%s.jsonl.gz' % name})
        total_commits += info['commit_count']
        total_bytes += info['packed_bytes']
        (meta / 'map').mkdir(exist_ok=True)
        shutil.copyfile(out / 'map' / (name + '.jsonl.gz'), meta / 'map' / (name + '.jsonl.gz'))
    manifest = {'export_format': EXPORT_FORMAT, 'module_sha256': module_sha256(),
                'input_complete_json_sha256': release.complete_sha256, 'names_index_sha256': release.names_sha256,
                'parser_version': release.parser, 'repos': repos, 'total_commits': total_commits,
                'total_packed_bytes': total_bytes, 'determinism': determinism,
                'author': AUTHOR, 'date_meaning': DATE_MEANING,
                'map_format': 'gzip jsonl, one {commit,repo,setid,version} per line in commit order'}
    (meta / 'MANIFEST.json').write_text(json.dumps(manifest, indent=1, sort_keys=True) + '\n')
    (meta / 'README.md').write_text(README % {'format': EXPORT_FORMAT, 'parser': release.parser, 'n': len(plan),
                                              'complete': release.complete_sha256, 'module': module_sha256()})
    if verify_path:
        shutil.copyfile(verify_path, meta / 'VERIFY-EXPORT.json')
    return manifest


def commit_meta(out):
    meta = Path(out) / 'gitrx-labels-meta'
    if not (meta / '.git').exists():
        subprocess.run(['git', 'init', '--quiet', '--initial-branch=main', str(meta)], check=True)
    env = dict(os.environ, GIT_AUTHOR_NAME=AUTHOR_NAME, GIT_AUTHOR_EMAIL=AUTHOR_EMAIL, GIT_COMMITTER_NAME=AUTHOR_NAME,
               GIT_COMMITTER_EMAIL=AUTHOR_EMAIL)
    subprocess.run(['git', '-C', str(meta), 'add', '-A'], check=True, env=env)
    subprocess.run(['git', '-C', str(meta), 'commit', '--quiet', '-m', 'GitRx labels export meta (%s)' % EXPORT_FORMAT],
                   check=True, env=env)


# ---------------------------------------------------------------- verification

def load_map(out, name):
    with gzip.open(Path(out) / 'map' / (name + '.jsonl.gz'), 'rt') as f:
        return {(r['setid'], r['version']): r['commit'] for r in map(json.loads, f)}


def verify_sample(out, reader, groups, seed=20261111, sample=200):
    """reader: a RebuildReader (independent path). Samples (setid, version), checks label.md and meta.json."""
    import random
    out = Path(out)
    plan = group_prefixes(reader.shard_info, groups)
    repo_of = {p: repo_name(prefixes) for prefixes in plan for p in prefixes}
    maps = {}
    for prefixes in plan:
        maps[repo_name(prefixes)] = load_map(out, repo_name(prefixes))
    all_keys = sorted((k, n) for n, m in maps.items() for k in m)
    rng = random.Random(seed)
    chosen = rng.sample(all_keys, sample)
    nodoc = [(k, n) for k, n in all_keys if k[0][:2] in reader.shard_info and _is_nodoc(reader, k)]
    results, failures = [], []
    for (sid, ver), name in chosen + nodoc:
        repo = out / 'repos' / (name + '.git')
        commit = maps[name][(sid, ver)]
        rec = {'setid': sid, 'version': ver, 'repo': name, 'commit': commit, 'ok': True, 'problems': []}
        row = reader._row(sid, ver)
        meta = json.loads(git(repo, 'show', '%s:%s/meta.json' % (commit, sid)))
        expect = {'setid': sid, 'version': ver, 'raw_sha256': row['raw_sha256'], 'parse_status': row['status'],
                  'snapshot': reader.snapshot}
        for k, v in expect.items():
            if meta.get(k) != v:
                rec['problems'].append('meta.%s' % k)
        if meta['dates']['dailymed_publication_date'] != row['pub_date'] or \
                meta['dates']['spl_effective_date'] != row['effective_date']:
            rec['problems'].append('meta.dates')
        if row['status'] in NO_DOCUMENT or row['raw_sha256'] is None:
            rec['kind'] = 'no_document'
            if meta['label_md'] == 'present' or meta['label_md_sha256'] is not None:
                rec['problems'].append('no_document label state')
        else:
            rec['kind'] = 'document'
            blob = subprocess.run(['git', '-C', str(repo), 'cat-file', 'blob', '%s:%s/label.md' % (commit, sid)],
                                  capture_output=True, check=True).stdout
            if hashlib.sha256(blob).hexdigest() != meta['label_md_sha256']:
                rec['problems'].append('label_md_sha256')
            got = parse_label(blob)
            sections, _ = reader.read(sid, ver)
            if len(got) != len(sections):
                rec['problems'].append('section count %d != %d' % (len(got), len(sections)))
            else:
                for (h, text), s in zip(got, sections):
                    if (text != s['own_text'] or h['ordinal'] != s['ordinal'] or h['title'] != s['title']
                            or h['code'] != s['loinc_code'] or h['parent'] != s['parent_ordinal']):
                        rec['problems'].append('section %s' % s['ordinal'])
                        break
            rec['n_sections'] = len(sections)
        rec['ok'] = not rec['problems']
        results.append(rec)
        if not rec['ok']:
            failures.append(rec)
    return {'export_format': EXPORT_FORMAT, 'seed': seed, 'random_sample_size': sample,
            'extra_no_document_versions_checked': len(nodoc), 'checked': len(results),
            'passed': len(results) - len(failures), 'failed': len(failures),
            'failures': failures[:50], 'sample': [{k: r[k] for k in ('setid', 'version', 'repo', 'commit', 'kind', 'ok')}
                                                  for r in results[:sample]],
            'method': 'label.md parsed back from the git blob and compared to RebuildReader.read own_text/title/'
                      'code/ordinal/parent; meta.json compared to the versions row'}


def _is_nodoc(reader, key):
    sid, ver = key
    row = reader._row(sid, ver)
    return row['status'] in NO_DOCUMENT or row['raw_sha256'] is None


# ---------------------------------------------------------------- CLI

def _check_volume(root):
    if not Path(root).is_dir():
        raise SystemExit('rebuild volume not available; stopping')


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m gitrx_core.git_export')
    ap.add_argument('--root', default=DEFAULT_ROOT)
    ap.add_argument('--out', default=DEFAULT_OUT)
    ap.add_argument('--names', default=None, help='names.sqlite (default: <root>/index/names.sqlite)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    p = sub.add_parser('pilot', help='export the given rebuild shard prefixes into <out>/pilot and measure')
    p.add_argument('prefixes', nargs='+')
    p = sub.add_parser('build', help='build all repos (resumable per repo)')
    p.add_argument('--groups', type=int, required=True)
    p = sub.add_parser('determinism')
    p.add_argument('--groups', type=int, required=True)
    p.add_argument('repos', nargs='+')
    p = sub.add_parser('verify')
    p.add_argument('--groups', type=int, required=True)
    p.add_argument('--config', default=None, help='native backend config JSON (default: runtime config for 0.2.4)')
    p = sub.add_parser('meta')
    p.add_argument('--groups', type=int, required=True)
    p.add_argument('--determinism-json', default=None)
    p.add_argument('--verify-json', default=None)
    sub.add_parser('complete')
    args = ap.parse_args(argv)
    try:
        os.nice(10)
    except OSError:
        pass
    _check_volume(args.root)
    names = args.names or str(Path(args.root) / 'index' / 'names.sqlite')
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.cmd == 'complete':
        meta = json.loads((out / 'gitrx-labels-meta' / 'MANIFEST.json').read_text())
        complete = {'export_format': EXPORT_FORMAT, 'module_sha256': module_sha256(),
                    'input_complete_json_sha256': meta['input_complete_json_sha256'],
                    'manifest_sha256': file_sha256(out / 'gitrx-labels-meta' / 'MANIFEST.json'),
                    'repos': [{k: r[k] for k in ('name', 'head', 'commit_count', 'packed_bytes')} for r in meta['repos']],
                    'total_commits': meta['total_commits'], 'total_packed_bytes': meta['total_packed_bytes']}
        (out / 'COMPLETE.json').write_text(json.dumps(complete, indent=1, sort_keys=True) + '\n')
        return 0
    if args.cmd == 'verify':
        from gitrx_backend.config import open_reader
        reader = open_reader(args.config or 'runtime/native-config-rebuild-0.2.4.json')
        result = verify_sample(out, reader, args.groups)
        (out / 'VERIFY-EXPORT.json').write_text(json.dumps(result, indent=1, sort_keys=True) + '\n')
        print(canon({k: result[k] for k in ('checked', 'passed', 'failed')}))
        return 0 if not result['failed'] else 1
    release = Release(args.root, names_path=names)
    try:
        if args.cmd == 'pilot':
            t = time.time()
            target = out / 'pilot' / ('pilot-' + '-'.join(args.prefixes) + '.git')
            info = build_repo(release, sorted(args.prefixes), target, log=print)
            info.pop('_pairs')
            info['seconds_total'] = round(time.time() - t, 1)
            info['bytes_per_version'] = round(info['packed_bytes'] / info['commit_count'], 1)
            info['bytes_per_family'] = round(info['packed_bytes'] / info['family_count'], 1)
            print(json.dumps(info, indent=1, sort_keys=True))
        elif args.cmd == 'build':
            build_all(release, out, args.groups)
        elif args.cmd == 'determinism':
            res = determinism_check(release, out, args.groups, args.repos)
            print(json.dumps(res, indent=1, sort_keys=True))
        elif args.cmd == 'meta':
            det = json.loads(Path(args.determinism_json).read_text()) if args.determinism_json else None
            build_meta(release, out, args.groups, det, args.verify_json)
            commit_meta(out)
    finally:
        release.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
