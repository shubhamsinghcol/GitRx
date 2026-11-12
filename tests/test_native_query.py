from pathlib import Path
import gzip
import hashlib
import json
import pytest
from gitrx_services.native_query import build, show, verify_index


@pytest.fixture
def native_root(tmp_path):
    root = tmp_path / 'release'
    (root / 'reports').mkdir(parents=True)
    (root / 'export-payloads/s0').mkdir(parents=True)
    scope = tmp_path / 'scope.json'
    scope.write_text(json.dumps({'records': {'abc': {'title': 'Synthetic family'}}}))
    payload = {'sections': [dict(ordinal=1, loinc_code='34066-1', code_system='loinc',
                                title='Source-coded section', parent_ordinal=None,
                                ancestry_path='/', own_text='one two three four')], 'tables': []}
    raw = json.dumps(payload).encode()
    (root / 'export-payloads/s0/1.json.gz').write_bytes(gzip.compress(raw, mtime=0))
    manifest = {'scope_sha256': hashlib.sha256(scope.read_bytes()).hexdigest(), 'versions': [
        {'setid': 'abc', 'version': 1, 'selected_sha256': 'raw-source-hash', 'effective_date': '202601',
         'publication_observations': [], 'parse_runs': [{'id': 's0:1'}]}]}
    selection = json.dumps(manifest).encode()
    snapshot = hashlib.sha256(selection).hexdigest()
    (root / 'reports/EXPANDED-SELECTION.json').write_bytes(selection + b'\n')
    (root / 'export-payloads/INDEX.json').write_text(json.dumps({'expanded_snapshot': snapshot,
        'entries': {'s0:1': {'path': 's0/1.json.gz', 'sha256': hashlib.sha256(raw).hexdigest()}}}))
    build(root, scope)
    return root


def test_native_output_is_bounded_cited_unclassified_and_pageable(native_root):
    a = show(native_root, 'ABC', 1, code='34066-1', ordinal=1, max_chars=7)
    assert a['context'] == 'unclassified native source'
    assert a['raw_sha256'] == 'raw-source-hash'
    assert a['DailyMed_publication_dates'] == [] and a['SPL_effective_date'] == '202601'
    assert a['sections'][0]['text'] == 'one two'
    assert a['sections'][0]['next_offset'] == 7
    b = show(native_root, 'abc', 1, ordinal=1, max_chars=20, offset=7)
    assert b['sections'][0]['text'] == ' three four'
    assert not b['sections'][0]['truncated']
    assert show(native_root, 'abc', 1, code='different')['selected_instances'] == 0


def test_unknown_versions_and_tampered_payloads_decline(native_root):
    with pytest.raises(ValueError, match='outside'):
        show(native_root, 'abc', 2)
    (native_root / 'export-payloads/s0/1.json.gz').write_bytes(gzip.compress(b'{}'))
    with pytest.raises(ValueError, match='payload hash'):
        show(native_root, 'abc', 1)


def test_tampered_metadata_index_declines_and_offset_requires_instance(native_root):
    with pytest.raises(ValueError, match='ordinal'):
        show(native_root, 'abc', 1, offset=3)
    with (native_root / 'native-query.sqlite').open('ab') as f:
        f.write(b'changed')
    with pytest.raises(ValueError, match='index hash'):
        verify_index(native_root)
