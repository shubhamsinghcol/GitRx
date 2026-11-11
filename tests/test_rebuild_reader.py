"""RebuildReader over a tiny synthetic Step E release: pinned hashes, shared Query answers, change records."""
import hashlib
import json
import sqlite3
import zlib
import pytest
from gitrx_core import rebuild, changes
from gitrx_backend.config import open_reader
from gitrx_backend.query import Query
from gitrx_backend.mcp import Server
from gitrx_backend.rebuild_reader import ReleaseUnavailable, NAMES_FORMAT, file_sha256

SID = 'ab000000-0000-4000-8000-000000000001'
SID2 = 'ab000000-0000-4000-8000-000000000002'
RAW = {1: 'a' * 64, 2: 'b' * 64, 3: 'c' * 64}
Z = rebuild.z


def th(texts, s):
    raw = s.encode()
    h = hashlib.sha256(raw).digest()
    texts[h] = zlib.compress(raw)
    return h


def make_shard(path):
    con = sqlite3.connect(path)
    con.executescript(rebuild.SCHEMA)
    texts = {}
    con.execute('INSERT INTO families VALUES(?,?,?,?,?,?,?,?,?,?)', (SID, 3, 3, 3, 1, 3, 1, 0, 2, 0))
    con.execute('INSERT INTO families VALUES(?,?,?,?,?,?,?,?,?,?)', (SID2, 1, 0, 0, 1, 1, None, 1, 0, 0))
    for v, date in ((1, '2020-01-02'), (2, '2020-06-01'), (3, '2021-01-01')):
        con.execute('INSERT INTO versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (SID, v, 'downloaded', RAW[v], 'x.xml', 'd' * 64, date, '2019010%d' % v, None, SID, v, 'complete',
                     '[]', 'e' * 64, 2, '{}', None, '[]', Z('[]')))
        body = 'Boxed warning text v%d' % v
        con.execute('INSERT INTO sections VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (SID, v, 1, None, None, 'BOXED WARNING', '34066-1', '34066-1#k', None, None, 'full', 'BW', None,
                     'BW', 'code', '[]', th(texts, body), None, None))
        con.execute('INSERT INTO sections VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (SID, v, 2, 1, '1.1', 'Child', '34067-9', '34067-9#k', None, None, 'full', None, None,
                     None, 'none', '[]', th(texts, 'indicated for things'), None, None))
    con.execute('INSERT INTO versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (SID2, 1, 'listed', None, None, None, None, None, None, None, None, 'no_raw', '[]', None, None, None,
                 None, '[]', None))
    for f, t in ((1, 2), (2, 3)):
        rec = {'changes_version': 'srlc-changes-0.3.2', 'parser_version': 'spl-sections-0.2.3', 'comparison': 'ok',
               'comparison_reasons': [], 'formatting_changes': [], 'alignment': [],
               'changes': [{'kind': 'modified', 'srlc_group': 'BW', 'alt_srlc_group': None, 'topic_group': 'BW',
                            'group_basis': 'code', 'section_number': None, 'section_title': 'BOXED WARNING',
                            'section_key': '34066-1#k', 'ordinal_from': 1, 'ordinal_to': 1, 'alignment_method': 'match_key',
                            'alignment_confidence': 0.98, 'table_changed': False, 'reference_only': False,
                            'formatting_token_diffs': 0, 'section_kind': 'full', 'group_flags': []}]}
        spans = {'added_spans': [{'channel': 'text', 'text': 'v%d' % t, 'context': 'Boxed warning text v%d' % t}],
                 'removed_spans': [{'channel': 'text', 'text': 'v%d' % f}], 'moved_spans': []}
        stored = json.loads(json.dumps(rec))  # the shard keeps spans in the changes table, not in record_z
        rec['changes'][0].update(spans)
        rec['record_sha256'] = changes.record_hash(rec)
        stored['record_sha256'] = rec['record_sha256']
        con.execute('INSERT INTO pairs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (SID, f, t, 1, 0, '[]', 'ok', '[]', RAW[f], RAW[t], None, None, None, None, 1, 0,
                     rec['record_sha256'], Z(json.dumps(stored))))
        con.execute('INSERT INTO changes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (SID, f, t, 0, 'modified', 'BW', None, 'BW', 'code', None, 'BOXED WARNING', '34066-1#k', 1, 1,
                     'match_key', 0.98, 0, 0, 0, 1, 1, 0, 2, 2, Z(json.dumps(spans['added_spans'])),
                     Z(json.dumps(spans['removed_spans'])), None))
    for h, z in texts.items():
        con.execute('INSERT INTO texts VALUES(?,?)', (h, z))
    con.commit()
    con.close()


@pytest.fixture()
def fixture(tmp_path):
    root = tmp_path / 'rebuild'
    (root / 'shards').mkdir(parents=True)
    make_shard(root / 'shards' / 'ab.sqlite')
    shard = root / 'shards' / 'ab.sqlite'
    complete = {'format': 'gitrx-rebuild-1', 'shards': {'ab': {'bytes': shard.stat().st_size, 'sha256': file_sha256(shard)}},
                'counts': {'families': 2, 'status:complete': 3, 'status:no_raw': 1, 'pairs': 2, 'pairs_nonconsecutive': 0,
                           'changes': 2, 'formatting_changes': 0},
                'comparison_counts': {'ok': 2},
                'config': {'versions': {'parser': 'spl-sections-0.2.3', 'groups': 'g', 'changes': 'srlc-changes-0.3.2'},
                           'frozen_code_sha256': {}, 'inventory_fingerprint': 'f', 'inputs': {'evidence_sha256': 'e'}}}
    (root / 'COMPLETE.json').write_text(json.dumps(complete))
    csha = file_sha256(root / 'COMPLETE.json')
    names = tmp_path / 'names.sqlite'
    c = sqlite3.connect(names)
    c.executescript('CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT); CREATE TABLE families(setid TEXT PRIMARY KEY, title TEXT, title_source TEXT, alt_titles TEXT);')
    c.executemany('INSERT INTO meta VALUES(?,?)', [('format', NAMES_FORMAT), ('complete_sha256', csha)])
    c.executemany('INSERT INTO families VALUES(?,?,?,?)', [(SID, 'DRUG A [X]', 'dailymed_listing_title', '[]'),
                                                           (SID2, 'DRUG A [Y]', 'dailymed_listing_title', '[]')])
    c.commit()
    c.close()
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'format': 'gitrx-backend-config-1', 'backend': 'rebuild', 'snapshot': csha,
                                  'rebuild_root': 'rebuild', 'rebuild_complete_sha256': csha, 'names_index': 'names.sqlite',
                                  'names_index_sha256': file_sha256(names)}))
    return config, csha, root


def test_queries_carry_release_and_raw_hashes(fixture):
    config, csha, _ = fixture
    with open_reader(config) as reader:
        q = Query(reader)
        resolved = q.resolve('DRUG A')
        assert resolved['total_candidates'] == 2 and resolved['selected'] is None  # ambiguity preserved
        assert q.resolve(SID)['selected']['title'] == 'DRUG A [X]'
        versions = q.versions(SID)
        assert versions['snapshot'] == csha and [v['raw_sha256'] for v in versions['versions']] == [RAW[1], RAW[2], RAW[3]]
        shown = q.show(SID, 2)
        assert shown['citation']['raw_sha256'] == RAW[2] and shown['citation']['snapshot'] == csha
        assert shown['sections'][0]['srlc_group'] == 'BW' and shown['sections'][1]['section_number'] == '1.1'
        assert q.show_date(SID, '2020-07-01')['date_selection']['selected_version'] == 2
        assert q.first_observed(SID, 'indicated')['first_observed']['version'] == 1
        assert q.coverage(SID)['inventory']['versions_with_raw'] == 3
        with pytest.raises(ValueError, match='no parsed document'):
            q.show(SID2, 1)
        desc = q.describe_release()
        assert desc['release']['versions'] == 4 and desc['release']['versions_without_raw'] == 1
        assert 'FDA approval' in desc['release']['date_semantics']


def test_change_records_pairs_and_verbatim_spans(fixture):
    config, csha, _ = fixture
    with open_reader(config) as reader:
        q = Query(reader)
        pairs = q.change_records(SID)
        assert [(p['from_version'], p['to_version']) for p in pairs['pairs']] == [(1, 2), (2, 3)]
        assert pairs['pairs'][0]['groups']['BW']['changes'] == 1
        one = q.change_records(SID, to_version=3)
        assert one['pair']['from_version'] == 2 and one['record_hash_reproduced'] is True
        assert one['changes'][0]['added_spans'][0]['text'] == 'v3'
        assert q.change_records(SID, group='C')['total_pairs'] == 0
        tiny = q.change_records(SID, to_version=3, max_chars=1)
        assert tiny['text_budget_exhausted'] and tiny['changes'][0]['added_spans'][0]['text_truncated']
        with pytest.raises(ValueError):
            q.change_records(SID, to_version=1)


def test_mcp_matches_query(fixture):
    config, _, _ = fixture
    with open_reader(config) as reader:
        s = Server(reader)
        s.dispatch({'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-11-25'}})
        s.dispatch({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        r = s.dispatch({'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call',
                        'params': {'name': 'change_records', 'arguments': {'sid': SID, 'to_version': 2}}})
        assert r['result']['structuredContent'] == Query(reader).change_records(SID, to_version=2)


def test_pin_and_corruption_checks(fixture, tmp_path):
    config, csha, root = fixture
    cfg = json.loads(config.read_text())
    bad = tmp_path / 'bad.json'
    bad.write_text(json.dumps(dict(cfg, snapshot='0' * 64, rebuild_complete_sha256='0' * 64)))
    with pytest.raises(ValueError, match='COMPLETE.json hash'):
        open_reader(bad)
    with pytest.raises(ValueError, match='backend override'):
        open_reader(config, 'sqlite')
    with open((root / 'shards' / 'ab.sqlite'), 'r+b') as f:
        f.seek(5000)
        f.write(b'\x00\x01')
    with open_reader(config) as reader, pytest.raises(ValueError, match='SHA-256 differs'):
        Query(reader).versions(SID)


def test_absent_release_is_a_clear_error(fixture, tmp_path):
    config, _, _ = fixture
    cfg = json.loads(config.read_text())
    gone = tmp_path / 'gone.json'
    gone.write_text(json.dumps(dict(cfg, rebuild_root='/nonexistent-ssd/rebuild')))
    with pytest.raises(ReleaseUnavailable, match='No fallback'):
        open_reader(gone)
