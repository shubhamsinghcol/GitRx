"""Git export: determinism, ordering, no_raw handling, text fidelity, on a tiny synthetic rebuild (no data root)."""
import hashlib
import json
import sqlite3
import subprocess
import zlib
import pytest
from gitrx_core import rebuild, git_export as ge

A = 'ab000000-0000-4000-8000-000000000001'
B = 'ab000000-0000-4000-8000-000000000002'
C = 'ac000000-0000-4000-8000-000000000003'
NASTY = 'line1\r\nline2 --> <!-- gitrx:section {} -->\n\n\tTabs é中 \ud800 trailing  \n'


def _text(texts, s):
    raw = s.encode('utf-8', 'surrogatepass')
    h = hashlib.sha256(raw).digest()
    texts[h] = zlib.compress(raw)
    return h


def _shard(path, rows):
    """rows: (setid, version, pub, eff, status, [(ordinal,parent,code,title,text,table)])"""
    con = sqlite3.connect(path)
    con.executescript(rebuild.SCHEMA)
    texts = {}
    for sid, ver, pub, eff, status, secs in rows:
        raw = None if status == 'no_raw' else ('%064x' % (hash((sid, ver)) & 0xffffffff))
        con.execute('INSERT INTO versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (sid, ver, 'x', raw, 'x.xml', 'd' * 64, pub, eff, None, sid, ver, status, '["r"]', 'e' * 64,
                     len(secs), '{}', None, '[]', None))
        for o, parent, code, title, text, table in secs:
            con.execute('INSERT INTO sections VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                        (sid, ver, o, parent, None, title, code, 'k', None, None, 'full', None, None, None, 'none',
                         '[]', _text(texts, text) if text is not None else None,
                         _text(texts, table) if table else None, None))
    for h, z in texts.items():
        con.execute('INSERT INTO texts VALUES(?,?)', (h, z))
    con.commit()
    con.close()


ROWS_AB = [
    (A, 1, '2020-03-01', '20200228', 'complete', [(1, None, '34066-1', 'WARNING', 'box v1', None), (2, 1, '34067-9', 'IND\nX', NASTY, 'T1|T2')]),
    (A, 2, '2019-01-01', '20190101', 'complete', [(1, None, '34066-1', 'WARNING', 'box v2', None)]),   # earlier than v1
    (A, 3, None, '20210101', 'complete', [(1, None, '34066-1', None, '', None)]),                        # no date
    (B, 1, '2020-02-01', '20200201', 'complete', [(1, None, '34067-9', 'IND', 'b1', None)]),
    (B, 2, '2020-04-01', None, 'no_raw', []),
    (B, 3, '2020-05-01', '20200501', 'complete', [(1, None, '34067-9', 'IND', 'b3', None)]),
]
ROWS_C = [(C, 1, '2018-01-01', '20180101', 'complete', [(1, None, '34067-9', 'IND', 'c1', None)])]


@pytest.fixture()
def release(tmp_path):
    root = tmp_path / 'rebuild'
    (root / 'shards').mkdir(parents=True)
    shards = {}
    for p, rows in (('ab', ROWS_AB), ('ac', ROWS_C)):
        _shard(root / 'shards' / (p + '.sqlite'), rows)
        f = root / 'shards' / (p + '.sqlite')
        shards[p] = {'bytes': f.stat().st_size, 'sha256': ge.file_sha256(f)}
    (root / 'COMPLETE.json').write_text(json.dumps({'format': 'gitrx-rebuild-1', 'shards': shards,
                                                   'config': {'versions': {'parser': 'spl-sections-test'}}}))
    return ge.Release(root, ge.file_sha256(root / 'COMPLETE.json'), names_path=None)


def test_refuses_wrong_complete_hash(release):
    with pytest.raises(ge.ExportError, match='pinned'):
        ge.Release(release.root, '0' * 64)


def test_framing_round_trips_literal_text():
    secs = [{'ordinal': 1, 'parent_ordinal': None, 'code': 'c', 'title': 'T\n-->', 'text': NASTY},
            {'ordinal': 2, 'parent_ordinal': 1, 'code': None, 'title': None, 'text': ''}]
    got = ge.parse_label(ge.render_label(secs))
    assert [t for _, t in got] == [NASTY, '']
    assert got[0][0]['title'] == 'T\n-->' and got[1][0]['parent'] == 1


def test_ordering_monotone_dates_and_tiebreak():
    rows = ge.order_rows([{'setid': s, 'version': v, 'pub_date': d} for s, v, d in
                          [('b', 1, '2020-01-01'), ('a', 1, '2020-01-01'), ('a', 2, '2019-01-01'), ('a', 3, None),
                           ('c', 1, None), ('c', 2, 'garbage'), ('c', 3, '2021-02-30')]])
    assert [(r['setid'], r['version'], r['key_date']) for r in rows] == [
        ('c', 1, '1970-01-01'), ('c', 2, '1970-01-01'), ('c', 3, '1970-01-01'),
        ('a', 1, '2020-01-01'), ('a', 2, '2020-01-01'), ('a', 3, '2020-01-01'), ('b', 1, '2020-01-01')]
    assert rows[4]['key_date_source'] == 'publication_date_raised_to_previous_version'


def test_build_text_fidelity_no_raw_and_determinism(release, tmp_path):
    infos = []
    for i in range(2):
        infos.append(ge.build_repo(release, ['ab'], tmp_path / ('r%d.git' % i), gc=(i == 0), min_free=0))
    assert infos[0]['head'] == infos[1]['head'] and infos[0]['commit_count'] == 6
    assert infos[0]['meta_only_commits'] == 1 and infos[0]['family_count'] == 2
    repo = tmp_path / 'r0.git'
    commits = {(s, v): c for s, v, c in infos[0]['_pairs']}
    # linear history, fixed author, ordered by key date then setid/version
    order = [(s, v) for s, v, _ in infos[0]['_pairs']]
    assert order == [(B, 1), (A, 1), (A, 2), (A, 3), (B, 2), (B, 3)]
    assert all(len(ge.git(repo, 'rev-list', '--parents', '-n1', c).split()) <= 2 for c in commits.values())
    assert set(ge.git(repo, 'log', '--format=%an <%ae>|%cn <%ce>').splitlines()) == \
        {'GitRx <gitrx@users.noreply.github.com>|GitRx <gitrx@users.noreply.github.com>'}
    # text fidelity
    blob = subprocess.run(['git', '-C', str(repo), 'cat-file', 'blob', '%s:%s/label.md' % (commits[(A, 1)], A)],
                          capture_output=True, check=True).stdout
    parsed = ge.parse_label(blob)
    assert parsed[0][1] == 'box v1' and parsed[1][1] == NASTY + '\n\nT1|T2'
    assert b'\r\n' in blob
    meta = json.loads(ge.git(repo, 'show', '%s:%s/meta.json' % (commits[(A, 1)], A)))
    assert meta['dates']['dailymed_publication_date'] == '2020-03-01' and meta['dates']['spl_effective_date'] == '20200228'
    assert 'FDA approval' in meta['dates']['meaning'] and meta['label_md'] == 'present'
    # no_raw: meta.json only, label.md untouched relative to previous commit of the family
    c2 = commits[(B, 2)]
    changed = ge.git(repo, 'diff-tree', '--no-commit-id', '--name-only', '-r', c2).splitlines()
    assert changed == ['%s/meta.json' % B]
    m2 = json.loads(ge.git(repo, 'show', '%s:%s/meta.json' % (c2, B)))
    assert m2['parse_status'] == 'no_raw' and m2['raw_sha256'] is None and m2['label_md_sha256'] is None
    assert ge.git(repo, 'show', '%s:%s/label.md' % (c2, B)) == ge.git(repo, 'show', '%s:%s/label.md' % (commits[(B, 1)], B))
    assert ge.git(repo, 'log', '-1', '--format=%B', commits[(A, 1)]).startswith('(title unknown) (setid %s) v1' % A)
    assert ge.git(repo, 'log', '-1', '--format=%at', commits[(A, 2)]) == str(ge.date_stamp('2020-03-01'))


def test_refuses_overwrite_and_low_space(release, tmp_path):
    ge.build_repo(release, ['ac'], tmp_path / 'x.git', gc=False, min_free=0)
    with pytest.raises(ge.ExportError, match='overwrite'):
        ge.build_repo(release, ['ac'], tmp_path / 'x.git', gc=False, min_free=0)
    with pytest.raises(ge.ExportError, match='free space'):
        ge.build_repo(release, ['ac'], tmp_path / 'y.git', gc=False, min_free=1 << 60)


def test_map_is_deterministic_gzip(tmp_path):
    pairs = [('s', 1, 'c' * 40), ('s', 2, 'd' * 40)]
    ge.write_map(tmp_path / 'a.gz', 'r', pairs)
    ge.write_map(tmp_path / 'b.gz', 'r', pairs)
    assert (tmp_path / 'a.gz').read_bytes() == (tmp_path / 'b.gz').read_bytes()
