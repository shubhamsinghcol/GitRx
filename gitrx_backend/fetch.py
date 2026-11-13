"""`get`: explicit, verified download of released GitRx files from the public Hugging Face dataset.

This is a download command, not a read: it is the only place in the native CLI that uses the network, and only when
you run it. Queries never fetch. Every file is checked against a pinned SHA-256 (the pinned configuration, COMPLETE.json
and the per-shard .done.json), written to a temporary name and renamed into place only when it verifies, and an
interrupted download resumes from the partial file on the next run.
"""
import hashlib
import http.client
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE = 'https://huggingface.co/datasets/shubhams20/GitRx/resolve/main'
CONFIG = 'native-config-rebuild-0.2.4.json'
SEARCH_CONFIG = 'native-config-rebuild-0.2.4-search.json'
PREFIX = '${GITRX_DATA_ROOT}/'
CHUNK = 1 << 20
ATTEMPTS = 4


class FetchError(RuntimeError):
    """A download problem the user can act on."""


class NotYetAvailable(FetchError):
    pass


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(CHUNK), b''):
            h.update(chunk)
    return h.hexdigest()


def human(n):
    if n is None:
        return '? B'
    n = float(n)
    for unit in ('B', 'KiB', 'MiB', 'GiB'):
        if n < 1024 or unit == 'GiB':
            return ('%d %s' % (n, unit)) if unit == 'B' else ('%.1f %s' % (n, unit))
        n /= 1024


def resolve_root(dest=None, environ=None):
    """(path, how). --dest wins when given; otherwise GITRX_DATA_ROOT, runtime/local.json, then ~/GitRx-data."""
    from gitrx_core.dataroot import ENV_ROOT, data_root
    environ = os.environ if environ is None else environ
    if dest:
        return Path(dest).expanduser().resolve(), '--dest'
    if environ.get(ENV_ROOT):
        return Path(environ[ENV_ROOT]).expanduser().resolve(), ENV_ROOT
    configured = data_root()
    if configured:
        return Path(configured).expanduser().resolve(), 'data_root in runtime/local.json'
    return Path('~/GitRx-data').expanduser().resolve(), 'default (~/GitRx-data)'


# -- network -----------------------------------------------------------------
class Remote:
    def __init__(self, base=DEFAULT_BASE, timeout=60):
        self.base = base.rstrip('/')
        self.timeout = timeout

    def url(self, path):
        return self.base + '/' + path

    def _open(self, path, start=0, method='GET'):
        headers = {'User-Agent': 'gitrx-get'}
        if start:
            headers['Range'] = 'bytes=%d-' % start
        try:
            return urllib.request.urlopen(urllib.request.Request(self.url(path), headers=headers, method=method),
                                          timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise NotYetAvailable('%s is not yet available on Hugging Face (HTTP 404). The release files may still be '
                                      'uploading; try again later.' % path) from None
            if exc.code == 416:
                return exc
            raise FetchError('Hugging Face returned HTTP %d for %s' % (exc.code, path)) from None
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            raise FetchError('Cannot reach %s (%s: %s)' % (self.base, type(exc).__name__, exc)) from None

    def bytes(self, path):
        with self._open(path) as r:
            return r.read()

    def size(self, path):
        """Content-Length of a file, or None when the server does not say."""
        with self._open(path, method='HEAD') as r:
            value = r.headers.get('Content-Length')
        return int(value) if value and value.isdigit() else None


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.part')
    tmp.write_bytes(data)
    os.replace(tmp, path)


def download(remote, rel, dest, sha, size=None, progress=None):
    """Fetch rel into dest unless dest already verifies. Returns 'present' or 'downloaded'."""
    dest = Path(dest)
    if dest.is_file() and (size is None or dest.stat().st_size == size) and sha256_file(dest) == sha:
        return 'present'
    part = dest.with_name(dest.name + '.part')
    dest.parent.mkdir(parents=True, exist_ok=True)
    last_error = None
    complete = False
    for attempt in range(ATTEMPTS):
        have = part.stat().st_size if part.exists() else 0
        if size is not None and have > size:
            part.unlink()
            have = 0
        if size is not None and have == size:
            complete = True
            break
        try:
            resp = remote._open(rel, have)
            if resp.code == 416:                      # range not satisfiable: the partial file is bad
                part.unlink(missing_ok=True)
                continue
            if have and resp.status != 206:           # server ignored Range: restart from zero
                have = 0
            with resp, part.open('ab' if have else 'wb') as out:
                total = size or (have + int(resp.headers.get('Content-Length') or 0)) or None
                got = have
                while True:
                    chunk = resp.read(CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    got += len(chunk)
                    if progress:
                        progress(rel, got, total)
            complete = size is None or part.stat().st_size == size
            if complete:
                break
        except NotYetAvailable:
            raise
        except (FetchError, OSError, http.client.HTTPException) as exc:
            last_error = exc
            time.sleep(min(2 ** attempt, 5) * 0.1)
    if not complete:
        raise FetchError('Download of %s was interrupted (%s); run the same command again to resume.'
                         % (rel, last_error or 'incomplete'))
    if progress:
        progress(rel, None, None)
    if (size is not None and part.stat().st_size != size) or sha256_file(part) != sha:
        part.unlink(missing_ok=True)
        raise FetchError('%s failed verification (size or SHA-256 differs from the pinned value); the file was rejected '
                         'and removed.' % rel)
    os.replace(part, dest)
    return 'downloaded'


class Progress:
    def __init__(self, stream):
        self.stream = stream
        self.last = 0
        self.tty = hasattr(stream, 'isatty') and stream.isatty()

    def __call__(self, rel, got, total):
        now = time.monotonic()
        if got is None:
            if self.tty:
                self.stream.write('\r%s\r' % (' ' * 78))
            return
        if now - self.last < (0.2 if self.tty else 5):
            return
        self.last = now
        text = '  %s  %s' % (rel.rsplit('/', 1)[-1], human(got) + (' / ' + human(total) if total else ''))
        self.stream.write(('\r' + text.ljust(78)) if self.tty else text + '\n')
        self.stream.flush()


# -- plan --------------------------------------------------------------------
def _under_root(value):
    if not value.startswith(PREFIX):
        raise FetchError('Unexpected path in the published configuration: ' + value)
    return value[len(PREFIX):]


def _checkout_pins(name, config, checkout_runtime):
    """Cross-check the downloaded config's pins against this checkout's copy when one exists."""
    if checkout_runtime is None:
        return
    local = Path(checkout_runtime) / name
    if not local.is_file():
        return
    mine = json.loads(local.read_bytes())
    for key in ('rebuild_complete_sha256', 'names_index_sha256', 'search_index_sha256', 'retrieval_routes_sha256'):
        if mine.get(key) != config.get(key):
            raise FetchError('configs/%s on Hugging Face pins a different %s than this checkout (%s). Update the '
                             'checkout or run get with the matching version; nothing was downloaded.' % (name, key, name))


def _fetch_config(remote, root, name, checkout_runtime):
    data = remote.bytes('configs/' + name)
    config = json.loads(data)
    _checkout_pins(name, config, checkout_runtime)
    target = root / 'configs' / name
    if not target.is_file() or target.read_bytes() != data:
        atomic_write(target, data)
    return config


def _candidates_text(candidates, shown=25):
    lines = ['  %s  [shard %s]  %s' % (c['setid'], c['setid'][:2], c['title']) for c in candidates[:shown]]
    if len(candidates) > shown:
        lines.append('  ... and %d more' % (len(candidates) - shown))
    return '\n'.join(lines)


def get(names=(), all_shards=False, search=False, routes=False, dry_run=False, dest=None, base_url=DEFAULT_BASE,
        out=None, err=None, checkout_runtime='default'):
    out = out or sys.stdout
    err = err or sys.stderr
    if checkout_runtime == 'default':
        checkout_runtime = Path(__file__).resolve().parents[1] / 'runtime'
    if not (names or all_shards or search or routes):
        raise FetchError('Give a drug name or setid (./label-history get MOVIPREP), or --all, --search or --routes.')
    # The fast-search configuration pins both the search index and the download-records lookup,
    # so --search brings both (about 3 GB); --routes alone adds only the lookup.
    routes = routes or search
    root, how = resolve_root(dest)
    remote = Remote(base_url)
    say = lambda text='': print(text, file=out)
    say('Data root: %s  (from %s)' % (root, how))

    config = _fetch_config(remote, root, CONFIG, checkout_runtime)
    release = _under_root(config['rebuild_root'])
    complete_sha = config['rebuild_complete_sha256']
    names_rel = _under_root(config['names_index'])
    progress = Progress(err)

    # Minimal release files: always needed to open the release and resolve names.
    download(remote, release + '/COMPLETE.json', root / release / 'COMPLETE.json', complete_sha, progress=progress)
    build = root / release / 'build-config.json'
    if not build.is_file():
        atomic_write(build, remote.bytes(release + '/build-config.json'))
    names_dest = root / names_rel
    if download(remote, names_rel, names_dest, config['names_index_sha256'], progress=progress) == 'downloaded':
        say('Downloaded name index (%s).' % human(names_dest.stat().st_size))
    complete = json.loads((root / release / 'COMPLETE.json').read_bytes())
    shard_info = complete['shards']

    # Resolve names with the shared resolver (same as `resolve`); ambiguity is preserved, never merged.
    wanted = {}
    if names:
        from .query import Query
        from .rebuild_reader import RebuildReader
        reader = RebuildReader(root / release, complete_sha, names_dest, config['names_index_sha256'], 'size')
        try:
            q = Query(reader)
            for name in names:
                res = q.resolve(name)
                if not res['total_candidates']:
                    raise FetchError('No family matches %r. Try ./label-history families --search TEXT once a release is '
                                     'installed, or check the spelling.' % name)
                if res['selected'] is None and not all_shards:
                    say('%r matches %d families; choose one and run get again with its setid:' % (name, res['total_candidates']))
                    say(_candidates_text(res['candidates']))
                    raise FetchError('Ambiguous name %r: pick a setid from the list above (or use --all).' % name)
                for c in ([res['selected']] if res['selected'] else []):
                    wanted[c['setid']] = c['title']
        finally:
            reader.close()
    prefixes = sorted(shard_info) if all_shards else sorted({sid[:2] for sid in wanted})
    for sid in wanted:
        if sid[:2] not in shard_info:
            raise FetchError('Family %s has no shard in this release.' % sid)

    # Plan: (relative path, expected sha256, expected bytes or None, label)
    plan = []
    for p in prefixes:
        plan.append((release + '/shards/%s.sqlite' % p, shard_info[p]['sha256'], shard_info[p]['bytes'], 'shard %s' % p))
    search_config = None
    if search or routes:
        search_config = _fetch_config(remote, root, SEARCH_CONFIG, checkout_runtime)
    if search:
        manifest_rel = _under_root(search_config['search_index'])
        manifest_dest = root / manifest_rel
        download(remote, manifest_rel, manifest_dest, search_config['search_index_sha256'], progress=progress)
        parts = json.loads(manifest_dest.read_bytes())['parts']
        folder = manifest_rel.rsplit('/', 1)[0]
        for p in sorted(parts):
            plan.append(('%s/%s.sqlite' % (folder, p), parts[p]['sha256'], parts[p]['bytes'], 'search part %s' % p))
    if routes:
        routes_rel = _under_root(search_config['retrieval_routes'])
        plan.append((routes_rel, search_config['retrieval_routes_sha256'], remote.size(routes_rel), 'download-records lookup'))

    def present(item):
        path = root / item[0]
        return path.is_file() and (item[2] is None or path.stat().st_size == item[2])

    todo = [i for i in plan if not present(i)]
    total = sum(i[2] or 0 for i in todo)
    say('')
    if wanted:
        for sid, title in wanted.items():
            say('Family: %s  %s  (shard %s)' % (sid, title, sid[:2]))
    say('%s %d file(s), %s%s' % ('Would download' if dry_run else 'To download:', len(todo), human(total),
                                  '' if todo else ' - everything requested is already present'))
    for item in plan:
        say('  %-24s %10s  %s%s' % (item[3], human(item[2]), item[0], '' if item not in todo else '  (needed)') if item[2] is not None
            else '  %-24s %10s  %s  (needed)' % (item[3], '?', item[0]))
    if len(todo) > 8 and not dry_run:
        say('(Large download; it resumes if interrupted - run the same command again.)')
    if dry_run:
        say('Dry run: only the small release metadata and name index were fetched; no shards were downloaded.')
        return 0
    if todo:
        probe = root
        while not probe.exists():
            probe = probe.parent
        if total and shutil.disk_usage(probe).free < total * 1.02:
            raise FetchError('Not enough free disk space under %s: need about %s, have %s.'
                             % (probe, human(total), human(shutil.disk_usage(probe).free)))

    for rel, sha, size, label in plan:
        if label.startswith('shard '):
            prefix = label.split()[1]
            done_rel = release + '/shards/%s.done.json' % prefix
            done = json.loads(remote.bytes(done_rel))
            if done.get('sha256') != sha or done.get('bytes') != size:
                raise FetchError('Shard %s .done.json disagrees with COMPLETE.json; nothing from it was kept.' % prefix)
            if not (root / done_rel).is_file():
                atomic_write(root / done_rel, json.dumps(done, indent=1, sort_keys=True).encode() + b'\n')
        if (rel, sha, size, label) in todo:
            say('Downloading %s (%s)...' % (label, human(size)))
        state = download(remote, rel, root / rel, sha, size, progress=progress)
        if state == 'downloaded':
            say('  verified SHA-256 and installed %s' % rel)

    # Next step
    say('')
    if how != 'GITRX_DATA_ROOT':
        say('To use this data root next time:')
        say('  export GITRX_DATA_ROOT=%s' % root)
    has_fast = False
    if (root / 'configs' / SEARCH_CONFIG).is_file():
        sc = json.loads((root / 'configs' / SEARCH_CONFIG).read_bytes())
        has_fast = (root / _under_root(sc['search_index'])).is_file() and (root / _under_root(sc['retrieval_routes'])).is_file()
    if not has_fast:
        say('label-history will read this with a full scan (same answers; whole-collection text search takes minutes).')
        say('For sub-second search add the fast-search pack: ./label-history get --search  (about 3 GB)')
    sid = next(iter(wanted), None)
    if sid:
        say('Next:  ./label-history versions %s' % sid)
    else:
        say('Next:  ./label-history describe-release')
    return 0
