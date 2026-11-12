"""Observed retrieval routes for cited raw artifacts: pinned derived lookup, explicit degradation, no evidence-DB reads."""
import json
import sqlite3

import pytest

from gitrx_backend import rebuild_routes
from gitrx_backend.config import open_reader
from gitrx_backend.query import Query
from test_rebuild_reader import fixture, SID, SID2, RAW  # noqa: F401  (pytest fixture)

URL = 'https://dailymed.nlm.nih.gov/dailymed/getFile.cfm?type=zip&setid=%s&version=%d'


def make_evidence(path):
    c = sqlite3.connect(path)
    c.executescript(
        'CREATE TABLE retrievals(id INTEGER PRIMARY KEY, observation_key, kind, url, final_url, retrieved_at, status, headers_json, state,'
        ' artifact_sha256, error, attempt, next_retry_at, validation_json, setid, version);'
        'CREATE TABLE http_requests(id INTEGER PRIMARY KEY, url, origin_url, started_epoch, started_at, completed_epoch, status, sha256, bytes,'
        ' error, headers_json, final_url, archive_request, complete);'
        'CREATE TABLE bulk_member_sources(id INTEGER PRIMARY KEY, retrieval_key, provenance_json);')
    # version 1: direct 200 response observed twice (retrievals + http ledger); version 2: only a 404 and a wrong body
    c.execute("INSERT INTO retrievals(id,kind,url,final_url,retrieved_at,status,state,artifact_sha256,setid,version) VALUES(1,'getFile_zip',?,?,?,200,'available',?,?,1)",
              (URL % (SID, 1), URL % (SID, 1), '2026-11-08T11:38:54-06:00', RAW[1], SID))
    c.execute("INSERT INTO http_requests(id,url,started_epoch,started_at,status,sha256,bytes) VALUES(968,?,1794159534.29,'2026-11-08T17:38:54Z',200,?,71205)",
              (URL % (SID, 1), RAW[1]))
    c.execute("INSERT INTO retrievals(id,kind,url,final_url,retrieved_at,status,state,artifact_sha256,setid,version) VALUES(2,'getFile_zip',?,?,?,404,'unavailable',?,?,2)",
              (URL % (SID, 2), URL % (SID, 2), '2026-11-08T11:40:00-06:00', 'f' * 64, SID))
    # version 3: recovered from a monthly bulk archive member (no direct response)
    c.execute("INSERT INTO retrievals(id,kind,url,final_url,retrieved_at,status,state,artifact_sha256) VALUES(9,'bulk_range','https://dailymed-data.nlm.nih.gov/m.zip','https://dailymed-data.nlm.nih.gov/m.zip','2026-11-03T18:30:18Z',206,'available','r')")
    c.execute('INSERT INTO bulk_member_sources VALUES(1,?,?)', ('k', json.dumps({
        'ZIP_sha256': RAW[3], 'XML_sha256': 'x' * 64, 'url': 'https://dailymed-data.nlm.nih.gov/m.zip', 'member': 'prescription/a.zip',
        'xml_member': 'x.xml', 'transport': 'Exact nested ZIP extracted offline from retained HTTP206 ranges',
        'source_ledger_sha256': 's' * 64, 'bulk_range_retrieval_ids': [9]})))
    c.commit()
    c.close()


@pytest.fixture()
def routed(fixture, tmp_path, monkeypatch):
    config, csha, root = fixture
    evidence = tmp_path / 'evidence.sqlite'
    make_evidence(evidence)
    real = rebuild_routes.file_sha256
    monkeypatch.setattr(rebuild_routes, 'file_sha256', lambda p: 'e' if str(p) == str(evidence.resolve()) else real(p))
    out = root / 'index' / 'routes.sqlite'
    summary = rebuild_routes.build(config, evidence, out)
    data = json.loads(config.read_text())
    data['retrieval_routes'] = 'rebuild/index/routes.sqlite'
    data['retrieval_routes_sha256'] = summary['sha256']
    routed_config = tmp_path / 'config-routes.json'
    routed_config.write_text(json.dumps(data))
    return config, routed_config, out, summary


def test_observed_routes_in_citations(routed):
    _, routed_config, _, summary = routed
    assert summary['raw_hashes_in_release'] == 3 and summary['raw_hashes_with_routes'] == 2
    with open_reader(routed_config) as reader:
        q = Query(reader)
        one = q.show(SID, 1)['citation']['evidence']
        assert one['retrieval_routes_status'] == 'observed'
        sources = {r['source']: r for r in one['retrieval_routes']}
        assert set(sources) == {'evidence.retrievals', 'evidence.http_requests'}
        for r in sources.values():
            assert r['url'] == URL % (SID, 1) and r['http_status'] == 200 and r['body_matches_raw'] is True
            assert r['body_sha256'] == RAW[1]
        assert one['publisher_references'][0]['observed'] is True and one['publisher_references'][0]['status'] == 200
        two = q.show(SID, 2)['citation']['evidence']          # only a 404 for a different body was attempted: nothing observed
        assert two['retrieval_routes'] == [] and two['retrieval_routes_status'] == 'none_recorded'
        assert two['publisher_references'][0]['observed'] is False
        three = q.show(SID, 3)['citation']['evidence']
        route = three['retrieval_routes'][0]
        assert route['kind'] == 'bulk-member' and route['http_status'] is None
        assert route['detail']['member'] == 'prescription/a.zip' and route['detail']['range_statuses'] == [206]
        assert three['publisher_references'][0]['kind'] == 'bulk-member' and three['publisher_references'][0]['observed'] is True
        # the version list carries the same evidence block
        assert q.versions(SID)['versions'][0]['evidence']['retrieval_routes_status'] == 'observed'


def test_without_a_lookup_routes_are_explicitly_unavailable(fixture):
    config, _, _ = fixture
    with open_reader(config) as reader:
        e = Query(reader).show(SID, 1)['citation']['evidence']
        assert e['retrieval_routes'] == [] and e['retrieval_routes_status'] == 'unavailable'
        assert 'pinned' in e['retrieval_routes_note'] and e['publisher_references'][0]['observed'] is False


def test_missing_or_tampered_lookup_degrades_to_unavailable_never_serves_content(routed):
    _, routed_config, out, _ = routed
    blob = bytearray(out.read_bytes())
    blob[-5] ^= 0xFF
    out.write_bytes(bytes(blob))
    with open_reader(routed_config) as reader:
        e = Query(reader).show(SID, 1)['citation']['evidence']
        assert e['retrieval_routes'] == [] and e['retrieval_routes_status'] == 'unavailable'
        assert 'SHA-256' in e['retrieval_routes_note']
    out.unlink()
    with open_reader(routed_config) as reader:
        e = Query(reader).show(SID, 1)['citation']['evidence']
        assert e['retrieval_routes_status'] == 'unavailable' and 'absent' in e['retrieval_routes_note']


def test_build_never_overwrites_and_refuses_unpinned_evidence(routed, fixture, tmp_path):
    config, routed_config, out, summary = routed
    with pytest.raises(ValueError, match='builds never overwrite'):
        rebuild_routes.build(config, tmp_path / 'evidence.sqlite', out)


def test_build_refuses_evidence_that_is_not_the_pinned_input(fixture, tmp_path):
    config, _, root = fixture
    other = tmp_path / 'other-evidence.sqlite'
    make_evidence(other)
    with pytest.raises(ValueError, match='Evidence database hash differs'):
        rebuild_routes.build(config, other, root / 'index' / 'r.sqlite')


def test_pin_pairs_and_backend_are_validated(fixture, tmp_path):
    config, _, _ = fixture
    data = json.loads(config.read_text())
    data['retrieval_routes'] = 'x.sqlite'
    bad = tmp_path / 'bad.json'
    bad.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='both path and SHA-256'):
        open_reader(bad)
