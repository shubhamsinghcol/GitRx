"""describe_release freshness: read-only report of recorded update-job state; unknown is explicit, never inferred."""
import json
import os
import sqlite3

from gitrx_backend import freshness
from gitrx_core import dailymed_update, srlc_update

A = '11111111-1111-4111-8111-111111111111'   # in the release at version 3
B = '22222222-2222-4222-8222-222222222222'   # in the release at version 1
C = '33333333-3333-4333-8333-333333333333'   # not in the release


class Reader:
    snapshot = 's' * 64
    held = {A: [1, 2, 3], B: [1]}

    def family(self, sid):
        return {'setid': sid} if sid in self.held else None

    def versions(self, sid):
        return [{'version': v} for v in self.held[sid]]


def snapshot_tree(root):
    return sorted((str(p.relative_to(root)), p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file())


def make_state(root, runs=True):
    base = root / 'updates' / 'dailymed'
    base.mkdir(parents=True)
    con = dailymed_update.open_delta(base)
    if runs:
        for rid, started, status, mode in (('r1', '2026-11-01T00:00:00Z', 'complete', None),
                                           ('r2', '2026-11-08T00:00:00Z', 'smoke-complete', 'smoke'),
                                           ('r3', '2026-11-09T00:00:00Z', 'partial', None)):
            con.execute('INSERT INTO runs(run_id,started_at,finished_at,status,mode,error) VALUES(?,?,?,?,?,?)',
                        (rid, started, started.replace('T00', 'T01'), status, mode, 'cap' if status == 'partial' else None))
        con.executemany('INSERT INTO listing_observations(run_id,setid,listed_version,published_date) VALUES(?,?,?,?)',
                        [('r1', A, 3, '2026-11-01'), ('r3', A, 4, '2026-11-09'), ('r3', B, 1, '2026-11-09'), ('r3', C, 1, '2026-11-09')])
        con.execute('INSERT INTO version_files(run_id,setid,version,status,zip_ok) VALUES(?,?,?,?,?)', ('r3', A, 4, 200, 1))
        con.execute('INSERT INTO version_files(run_id,setid,version,status,zip_ok) VALUES(?,?,?,?,?)', ('r3', B, 1, 200, 1))
        con.commit()
    con.close()
    (base / 'WATERMARK.json').write_text(json.dumps({'watermark': '2026-11-08'}))
    state = root / 'srlc' / 'updates'
    state.mkdir(parents=True)
    s = srlc_update.open_state(state)
    s.execute("INSERT INTO runs(run_id,started_at,finished_at,status,drugs_changed,windows_json) VALUES('s1','2026-11-02T04:00:00Z','2026-11-02T04:05:00Z','complete',4,'[[\"2026-10-01\",\"2026-10-31\"]]')")
    s.commit()
    s.close()


def test_unknown_when_no_data_root_or_no_state(tmp_path):
    for root in (tmp_path / 'absent', tmp_path):
        block = freshness.freshness(Reader(), root=root)
        assert block['source_checked_at'] is None and block['source_checked_state'] == 'unknown'
        for name in ('dailymed', 'srlc'):
            assert block['sources'][name]['state'] == 'unknown' and block['sources'][name]['reason']
            assert block['sources'][name]['last_attempt'] == 'unknown'
        assert block['release_snapshot']['identifier'] == 's' * 64


def test_last_attempt_success_watermark_and_counts_against_release(tmp_path):
    make_state(tmp_path)
    before = snapshot_tree(tmp_path)
    block = freshness.freshness(Reader(), root=tmp_path)
    assert snapshot_tree(tmp_path) == before                       # read-only: no journal, shm or wal files appear
    d = block['sources']['dailymed']
    assert d['last_attempt']['run_id'] == 'r3' and d['last_attempt']['status'] == 'partial'
    assert d['last_success']['run_id'] == 'r1'                     # smoke-complete and partial runs do not count
    assert d['watermark'] == '2026-11-08' and d['runs_recorded'] == 3
    assert block['source_checked_at'] == '2026-11-01T01:00:00Z'
    new = d['new_since_release']
    assert new['observed_setids_absent_from_release'] == 1         # C
    assert new['observed_setids_with_newer_listed_version_than_release'] == 1   # A listed v4, release has v3
    assert new['retrieved_versions_not_in_release'] == 1           # A v4; B v1 is held
    assert new['version_comparison']['complete'] is True
    s = block['sources']['srlc']
    assert s['last_success']['run_id'] == 's1' and s['last_success']['windows'] == [['2026-10-01', '2026-10-31']]
    assert s['watermark'] == 'unknown' and 'holdout' not in json.dumps(s)


def test_no_complete_run_is_explicit_unknown(tmp_path):
    make_state(tmp_path)
    con = sqlite3.connect(tmp_path / 'updates' / 'dailymed' / 'updates.sqlite')
    con.execute("UPDATE runs SET status='partial' WHERE run_id='r1'")
    con.commit()
    con.close()
    block = freshness.freshness(Reader(), root=tmp_path)
    d = block['sources']['dailymed']
    assert d['last_success'] == 'unknown' and 'smoke or partial' in d['last_success_note']
    assert block['source_checked_at'] is None and 'watermark_note' in d


def test_unreadable_state_reports_unknown_not_error(tmp_path):
    make_state(tmp_path)
    (tmp_path / 'updates' / 'dailymed' / 'updates.sqlite').write_bytes(b'not a database' * 100)
    block = freshness.freshness(Reader(), root=tmp_path)
    assert block['sources']['dailymed']['state'] == 'unknown' and 'unreadable' in block['sources']['dailymed']['reason']
    assert block['sources']['srlc']['state'] == 'recorded'


def test_describe_release_carries_freshness(tmp_path, monkeypatch):
    from gitrx_backend import discovery
    make_state(tmp_path)
    monkeypatch.setenv('GITRX_DATA_ROOT', str(tmp_path))
    info = discovery.describe(Reader_with_families())
    assert info['freshness']['sources']['dailymed']['last_attempt']['run_id'] == 'r3'
    assert info['freshness']['mode'] == 'snapshot'


class Reader_with_families(Reader):
    def families(self):
        return [{'setid': A, 'title': 'a'}]
