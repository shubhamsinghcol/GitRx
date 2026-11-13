"""`get`: verified selective download from a local mock of the Hugging Face dataset (no real network)."""
import hashlib
import io
import json
import shutil
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from gitrx_backend import cli, fetch
from gitrx_backend.config import open_reader
from gitrx_backend.query import Query
from gitrx_backend.rebuild_reader import NAMES_FORMAT, file_sha256
from test_rebuild_reader import make_shard, SID, SID2

SID3 = 'cd000000-0000-4000-8000-000000000003'
REL = 'rebuild/spl-sections-0.2.4__srlc-changes-0.3.2'
P = '${GITRX_DATA_ROOT}/' + REL


def sha(b):
    return hashlib.sha256(b).hexdigest()


def build_origin(top):
    """A tiny synthetic release laid out like the Hugging Face dataset."""
    root = top / 'origin'
    rel = root / REL
    (rel / 'shards').mkdir(parents=True)
    (rel / 'index/search-literal-1').mkdir(parents=True)
    (rel / 'index/routes-1').mkdir(parents=True)
    (root / 'configs').mkdir()
    make_shard(rel / 'shards/ab.sqlite')
    shutil.copy(rel / 'shards/ab.sqlite', rel / 'shards/cd.sqlite')
    shards = {}
    for p in ('ab', 'cd'):
        f = rel / 'shards' / (p + '.sqlite')
        shards[p] = {'bytes': f.stat().st_size, 'sha256': file_sha256(f)}
        (rel / 'shards' / (p + '.done.json')).write_text(json.dumps(dict(shards[p], shard=p)))
    complete = {'format': 'gitrx-rebuild-1', 'shards': shards, 'counts': {'families': 3},
                'config': {'versions': {'parser': 'p'}}}
    (rel / 'COMPLETE.json').write_text(json.dumps(complete))
    (rel / 'build-config.json').write_text('{}')
    csha = file_sha256(rel / 'COMPLETE.json')
    names = rel / 'index/names.sqlite'
    c = sqlite3.connect(names)
    c.executescript('CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT); CREATE TABLE families(setid TEXT PRIMARY KEY, title TEXT, title_source TEXT, alt_titles TEXT);')
    c.executemany('INSERT INTO meta VALUES(?,?)', [('format', NAMES_FORMAT), ('complete_sha256', csha)])
    c.executemany('INSERT INTO families VALUES(?,?,?,?)', [(SID, 'DRUG A [X]', 'x', '[]'), (SID2, 'DRUG A [Y]', 'x', '[]'),
                                                           (SID3, 'OTHER DRUG', 'x', '[]')])
    c.commit()
    c.close()
    parts = {}
    for p in ('ab', 'cd'):
        data = (p * 5000).encode()
        (rel / 'index/search-literal-1' / (p + '.sqlite')).write_bytes(data)
        parts[p] = {'bytes': len(data), 'sha256': sha(data)}
    manifest = json.dumps({'format': 'gitrx-rebuild-search-1', 'parts': parts}).encode()
    (rel / 'index/search-literal-1/SEARCH-INDEX.json').write_bytes(manifest)
    routes = b'routes-lookup' * 1000
    (rel / 'index/routes-1/routes.sqlite').write_bytes(routes)
    base = {'format': 'gitrx-backend-config-1', 'backend': 'rebuild', 'snapshot': csha, 'rebuild_root': P,
            'rebuild_complete_sha256': csha, 'names_index': P + '/index/names.sqlite', 'names_index_sha256': file_sha256(names),
            'verify_shards': 'sha256'}
    (root / 'configs/native-config-rebuild-0.2.4.json').write_text(json.dumps(base))
    search = dict(base, search_index=P + '/index/search-literal-1/SEARCH-INDEX.json', search_index_sha256=sha(manifest),
                  retrieval_routes=P + '/index/routes-1/routes.sqlite', retrieval_routes_sha256=sha(routes))
    (root / 'configs/native-config-rebuild-0.2.4-search.json').write_text(json.dumps(search))
    return root


class Server:
    def __init__(self, root):
        outer = self
        self.root, self.log, self.cut, self.hidden = root, [], {}, set()

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_HEAD(self):
                self.serve(False)

            def do_GET(self):
                self.serve(True)

            def serve(self, body):
                path = self.path.lstrip('/')
                outer.log.append((self.command, path, self.headers.get('Range')))
                f = outer.root / path
                if path in outer.hidden or not f.is_file():
                    self.send_response(404)
                    self.send_header('Content-Length', '0')
                    self.end_headers()
                    return
                data = f.read_bytes()
                start = 0
                rng = self.headers.get('Range')
                if rng:
                    start = int(rng.split('=')[1].rstrip('-'))
                    self.send_response(206)
                    self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, len(data) - 1, len(data)))
                else:
                    self.send_response(200)
                chunk = data[start:]
                self.send_header('Content-Length', str(len(chunk)))
                self.end_headers()
                if body:
                    n = outer.cut.pop(path, None)
                    self.wfile.write(chunk if n is None else chunk[:n])
                    if n is not None:
                        self.wfile.flush()
                        self.close_connection = True

        self.httpd = ThreadingHTTPServer(('127.0.0.1', 0), H)
        self.url = 'http://127.0.0.1:%d' % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def downloads(self, suffix):
        return [e for e in self.log if e[0] == 'GET' and e[1].endswith(suffix)]


@pytest.fixture()
def hf(tmp_path, monkeypatch):
    monkeypatch.delenv('GITRX_DATA_ROOT', raising=False)
    server = Server(build_origin(tmp_path))
    yield server
    server.close()


def get(hf, tmp_path, names=(), **kw):
    out, err = io.StringIO(), io.StringIO()
    dest = tmp_path / 'dest'
    code = fetch.get(names, dest=str(dest), base_url=hf.url, out=out, err=err, checkout_runtime=None, **kw)
    return code, out.getvalue(), dest


def test_name_downloads_only_its_shard_and_opens(hf, tmp_path, monkeypatch):
    code, text, dest = get(hf, tmp_path, ['OTHER DRUG'])
    rel = dest / REL
    assert code == 0 and (rel / 'shards/cd.sqlite').is_file() and (rel / 'shards/cd.done.json').is_file()
    assert not (rel / 'shards/ab.sqlite').exists()
    assert (rel / 'COMPLETE.json').is_file() and (rel / 'index/names.sqlite').is_file() and (dest / 'configs/native-config-rebuild-0.2.4.json').is_file()
    assert not list(dest.rglob('*.part'))
    assert 'export GITRX_DATA_ROOT=%s' % dest in text and 'get --search' in text and 'GITRX_CONFIG' not in text
    assert './label-history versions ' + SID3 in text
    # The unindexed config opens the downloaded release; a family in a shard that was not downloaded says how to get it.
    monkeypatch.setenv('GITRX_DATA_ROOT', str(dest))
    with open_reader(dest / 'configs/native-config-rebuild-0.2.4.json') as r:
        q = Query(r)
        assert q.resolve('OTHER')['selected']['setid'] == SID3
        assert q.versions(SID3)['total_captured_versions'] == 0     # shard cd is a synthetic copy; SID3 has no rows
        with pytest.raises(OSError, match='get ' + SID):
            q.versions(SID)


def test_rerun_downloads_nothing_and_setid_works(hf, tmp_path):
    get(hf, tmp_path, [SID])
    first = len(hf.downloads('ab.sqlite'))
    code, text, dest = get(hf, tmp_path, [SID])
    assert code == 0 and len(hf.downloads('ab.sqlite')) == first == 1
    assert 'already present' in text


def test_hash_mismatch_is_rejected_and_removed(hf, tmp_path):
    shard = hf.root / REL / 'shards/ab.sqlite'
    good = shard.read_bytes()
    shard.write_bytes(good[:-1] + bytes([good[-1] ^ 1]))          # same size, different content
    with pytest.raises(fetch.FetchError, match='failed verification'):
        get(hf, tmp_path, [SID])
    dest = tmp_path / 'dest'
    assert not (dest / REL / 'shards/ab.sqlite').exists() and not list(dest.rglob('*.part'))
    shard.write_bytes(good)
    assert get(hf, tmp_path, [SID])[0] == 0


def test_corrupt_local_file_is_replaced(hf, tmp_path):
    _, _, dest = get(hf, tmp_path, [SID])
    local = dest / REL / 'shards/ab.sqlite'
    good = local.read_bytes()
    local.write_bytes(b'x' * len(good))
    get(hf, tmp_path, [SID])
    assert local.read_bytes() == good


def test_interrupted_download_resumes_with_range(hf, tmp_path):
    key = REL + '/shards/ab.sqlite'
    size = (hf.root / key).stat().st_size
    hf.cut[key] = size // 3
    code, _, dest = get(hf, tmp_path, [SID])                       # retries inside one run and resumes
    assert code == 0 and file_sha256(dest / key) == file_sha256(hf.root / key)
    ranges = [e[2] for e in hf.downloads('ab.sqlite')]
    assert ranges[0] is None and ranges[1] == 'bytes=%d-' % (size // 3)


def test_resume_from_existing_partial_file(hf, tmp_path):
    key = REL + '/shards/ab.sqlite'
    data = (hf.root / key).read_bytes()
    dest = tmp_path / 'dest'
    (dest / REL / 'shards').mkdir(parents=True)
    (dest / key).with_name('ab.sqlite.part').write_bytes(data[:1000])
    get(hf, tmp_path, [SID])
    assert (dest / key).read_bytes() == data
    assert hf.downloads('ab.sqlite')[0][2] == 'bytes=1000-'


def test_ambiguous_name_lists_candidates_and_downloads_no_shard(hf, tmp_path):
    out = io.StringIO()
    with pytest.raises(fetch.FetchError, match='Ambiguous'):
        fetch.get(['DRUG A'], dest=str(tmp_path / 'dest'), base_url=hf.url, out=out, err=io.StringIO(), checkout_runtime=None)
    assert SID in out.getvalue() and SID2 in out.getvalue() and 'DRUG A [X]' in out.getvalue()
    assert not hf.downloads('.sqlite') or all('names' in e[1] for e in hf.downloads('.sqlite'))


def test_unknown_name(hf, tmp_path):
    with pytest.raises(fetch.FetchError, match='No family matches'):
        get(hf, tmp_path, ['NOSUCHDRUG'])


def test_missing_release_file_says_not_yet_available(hf, tmp_path):
    hf.hidden.add(REL + '/shards/ab.sqlite')
    with pytest.raises(fetch.NotYetAvailable, match='not yet available on Hugging Face'):
        get(hf, tmp_path, [SID])
    assert not list((tmp_path / 'dest').rglob('*.part'))
    hf.hidden.add('configs/native-config-rebuild-0.2.4.json')
    with pytest.raises(fetch.NotYetAvailable, match='not yet available'):
        get(hf, tmp_path, [SID])


def test_dry_run_reports_sizes_and_downloads_no_shards(hf, tmp_path):
    code, text, dest = get(hf, tmp_path, [SID], dry_run=True)
    size = (hf.root / REL / 'shards/ab.sqlite').stat().st_size
    assert code == 0 and 'Would download 1 file(s), %s' % fetch.human(size) in text
    assert not (dest / REL / 'shards/ab.sqlite').exists() and not hf.downloads('ab.sqlite')


def test_all_prints_total_and_gets_every_shard(hf, tmp_path):
    code, text, dest = get(hf, tmp_path, all_shards=True)
    assert code == 0 and all((dest / REL / 'shards' / (p + '.sqlite')).is_file() for p in ('ab', 'cd'))
    total = sum((hf.root / REL / 'shards' / (p + '.sqlite')).stat().st_size for p in ('ab', 'cd'))
    assert 'To download: 2 file(s), %s' % fetch.human(total) in text


def test_search_and_routes(hf, tmp_path):
    code, text, dest = get(hf, tmp_path, [SID], search=True)   # --search brings the routes lookup too
    rel = dest / REL
    assert code == 0 and (rel / 'index/search-literal-1/SEARCH-INDEX.json').is_file()
    assert (rel / 'index/search-literal-1/ab.sqlite').is_file() and (rel / 'index/search-literal-1/cd.sqlite').is_file()
    assert (rel / 'index/routes-1/routes.sqlite').is_file() and (dest / 'configs/native-config-rebuild-0.2.4-search.json').is_file()
    assert 'GITRX_CONFIG' not in text and 'get --search' not in text   # the fast configuration now works


def test_search_index_error_names_the_remedy(hf, tmp_path, monkeypatch):
    _, _, dest = get(hf, tmp_path, [SID], routes=True)
    monkeypatch.setenv('GITRX_DATA_ROOT', str(dest))
    with pytest.raises(OSError, match='get --search'):
        open_reader(dest / 'configs/native-config-rebuild-0.2.4-search.json')


def test_pins_must_match_checkout_config(hf, tmp_path):
    runtime = tmp_path / 'runtime'
    runtime.mkdir()
    cfg = json.loads((hf.root / 'configs/native-config-rebuild-0.2.4.json').read_text())
    cfg['names_index_sha256'] = '0' * 64
    (runtime / 'native-config-rebuild-0.2.4.json').write_text(json.dumps(cfg))
    with pytest.raises(fetch.FetchError, match='pins a different'):
        fetch.get([SID], dest=str(tmp_path / 'dest'), base_url=hf.url, out=io.StringIO(), err=io.StringIO(), checkout_runtime=runtime)


def test_data_root_resolution(tmp_path, monkeypatch):
    monkeypatch.setenv('GITRX_DATA_ROOT', str(tmp_path / 'env'))
    assert fetch.resolve_root(None) == (tmp_path / 'env', 'GITRX_DATA_ROOT')
    assert fetch.resolve_root(str(tmp_path / 'd')) == (tmp_path / 'd', '--dest')
    monkeypatch.delenv('GITRX_DATA_ROOT')
    monkeypatch.setattr('gitrx_core.dataroot._local', lambda: {})
    path, how = fetch.resolve_root(None)
    assert path == Path('~/GitRx-data').expanduser() and 'default' in how


def test_cli_get_is_wired_and_needs_a_target(hf, tmp_path, capsys, monkeypatch):
    monkeypatch.delenv('GITRX_DATA_ROOT', raising=False)
    assert cli.main(['get', '--dest', str(tmp_path / 'd'), '--base-url', hf.url, '--dry-run', SID]) in (0, 2)
    assert cli.main(['get', '--dest', str(tmp_path / 'd2')]) == 2
    assert 'Give a drug name' in capsys.readouterr().err
