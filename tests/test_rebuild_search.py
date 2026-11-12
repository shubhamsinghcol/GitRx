"""Pinned literal-search projection for rebuild releases: candidates only, results equal an exhaustive scan."""
import json
import shutil
import sqlite3

import pytest

from gitrx_backend import rebuild_search
from gitrx_backend.config import open_reader
from gitrx_backend.query import Query
from gitrx_backend.rebuild_reader import file_sha256
from test_rebuild_reader import fixture, SID, SID2  # noqa: F401  (pytest fixture)


def with_projection(fixture_value, tmp_path, name='search'):
    config, csha, root = fixture_value
    out = root / 'index' / name
    summary = rebuild_search.build(config, out, workers=1)
    data = json.loads(config.read_text())
    data['search_index'] = 'rebuild/index/%s/SEARCH-INDEX.json' % name
    data['search_index_sha256'] = summary['manifest_sha256']
    projected = tmp_path / 'config-search.json'
    projected.write_text(json.dumps(data))
    return projected, out, summary


def traverse(q, phrase, **kw):
    hits, cursor = [], None
    while True:
        page = q.search(phrase, limit=1, time_budget_seconds=5, cursor=cursor, **kw)
        hits += page['hits']
        cursor = page['continuation']
        if not page['partial']:
            return hits, page


PHRASES = ['warning', 'TEXT V3', 'Boxed Warning text', 'things', 'for things', 'zzz-absent', 'ab', 'ß', 'e\nr', 'v3']


def test_projection_equals_exhaustive_scan(fixture, tmp_path):
    projected, out, summary = with_projection(fixture, tmp_path)
    with open_reader(fixture[0]) as plain, open_reader(projected) as fast:
        assert getattr(plain, 'search_projection', None) is None and fast.search_projection is not None
        for phrase in PHRASES:
            slow_hits, slow_last = traverse(Query(plain), phrase)
            fast_hits, fast_last = traverse(Query(fast), phrase)
            assert slow_hits == fast_hits, phrase                       # full hit dicts, citations included
            assert slow_last['candidate_projection']['used'] is False
            assert fast_last['candidate_projection']['used'] is (len(phrase.casefold()) >= 3)
        assert [h['setid'] for h in traverse(Query(fast), 'warning')[0]] == [SID]
    assert summary['families'] == 1       # only the family whose greatest version has text is indexed


def test_family_with_unreadable_latest_version_is_reported_not_fatal(fixture, tmp_path):
    projected, _, _ = with_projection(fixture, tmp_path)
    with open_reader(fixture[0]) as reader:           # exhaustive scan visits it and says so
        page = Query(reader).search('warning', limit=25, time_budget_seconds=5)
        assert page['families_with_unreadable_latest_version_count'] == 1
        assert page['families_with_unreadable_latest_version'] == [SID2]
        assert not page['partial'] and len(page['hits']) == 1
    with open_reader(projected) as reader:            # the projection never proposes it; same hits, no error
        page = Query(reader).search('warning', limit=25, time_budget_seconds=5)
        assert not page['partial'] and [h['setid'] for h in page['hits']] == [SID]


def test_cursor_interoperates_between_scan_and_projection(fixture, tmp_path):
    projected, _, _ = with_projection(fixture, tmp_path)
    with open_reader(fixture[0]) as plain, open_reader(projected) as fast:
        first = Query(plain).search('warning', max_families=1, time_budget_seconds=5)
        assert first['partial'] and first['continuation'] and len(first['hits']) == 1
        rest = Query(fast).search('warning', cursor=first['continuation'], time_budget_seconds=5)
        assert rest['hits'] == [] and not rest['partial'] and rest['candidate_projection']['used']


def test_build_is_deterministic_and_resumable(fixture, tmp_path):
    config, csha, root = fixture
    first = rebuild_search.build(config, root / 'index' / 'one', workers=1)
    second = rebuild_search.build(config, root / 'index' / 'two', workers=1)
    assert first['manifest_sha256'] == second['manifest_sha256']
    again = rebuild_search.build(config, root / 'index' / 'one', workers=1)     # completed parts are reused
    assert again['parts_built_this_run'] == 0 and again['manifest_sha256'] == first['manifest_sha256']


def test_refuses_wrong_pin_tampered_part_and_other_snapshot(fixture, tmp_path):
    projected, out, summary = with_projection(fixture, tmp_path)
    data = json.loads(projected.read_text())
    bad = dict(data, search_index_sha256='0' * 64)
    bad_path = tmp_path / 'bad.json'
    bad_path.write_text(json.dumps(bad))
    with pytest.raises(ValueError, match='manifest hash'):
        open_reader(bad_path)
    # a manifest for another snapshot is refused even when its own pin matches
    manifest = json.loads((out / 'SEARCH-INDEX.json').read_text())
    manifest['snapshot'] = 'f' * 64
    other = out.parent / 'other'
    shutil.copytree(out, other)
    (other / 'SEARCH-INDEX.json').write_text(json.dumps(manifest))
    data['search_index'] = 'rebuild/index/other/SEARCH-INDEX.json'
    data['search_index_sha256'] = file_sha256(other / 'SEARCH-INDEX.json')
    other_cfg = tmp_path / 'other.json'
    other_cfg.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='different release snapshot'):
        open_reader(other_cfg)
    # a modified part fails its size/hash check at first use, never silently
    part = out / 'ab.sqlite'
    blob = bytearray(part.read_bytes())
    blob[-1] ^= 0xFF
    part.write_bytes(bytes(blob))
    with open_reader(projected) as reader:
        with pytest.raises(ValueError, match='SHA-256 differs'):
            Query(reader).search('warning', time_budget_seconds=5)


def test_trigram_filter_never_loses_a_casefold_match():
    # Candidates are a superset of literal casefold matches, including multi-character folds and punctuation.
    conn = sqlite3.connect(':memory:')
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(x, content='', detail=none, tokenize='%s')" % rebuild_search.TOKENIZER)
    texts = ['Straße nach µg/mL', 'MICRO μG/ML dose', 'say "hi"  there\nnow', 'QT prolongation', 'ABC abc', '\ud800 lone']
    for i, text in enumerate(texts, 1):
        conn.execute('INSERT INTO t(rowid,x) VALUES(?,?)', (i, rebuild_search.index_text(text)))
    for phrase in ['strasse', 'STRAẞE', 'µg/ml', 'μg/ml', '"hi"  there\nnow', 'qt prolong', 'abc', 'say "hi"', 'lone', 'e nach']:
        grams = rebuild_search.query_trigrams(phrase)
        got = {r[0] for r in conn.execute('SELECT rowid FROM t WHERE t MATCH ?', (rebuild_search.match_expression(grams),))}
        truth = {i for i, text in enumerate(texts, 1) if phrase.casefold() in text.casefold()}
        assert truth <= got, phrase
    assert rebuild_search.query_trigrams('ab') is None and rebuild_search.query_trigrams('\ud800abc') is None
