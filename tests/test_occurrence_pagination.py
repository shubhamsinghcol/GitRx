"""Bounded record pages preserve filtering, text offsets and release identity."""
import json
import pytest
from test_backend_portable import release, SID
from gitrx_backend.query import Query
from gitrx_backend.config import open_reader
from gitrx_backend.cli import main
from test_release_cycle import job


class ManyRecords:
    snapshot = 'synthetic-many-records'

    def families(self):
        return [{'setid': SID, 'title': 'Many records'}]

    def versions(self, sid):
        return [dict(version=i, publication_dates=['2026-01-01'], effective_date=None)
                for i in range(1, 452)]

    def read(self, sid, version):
        return [dict(ordinal=i, loinc_code='a' if i % 2 else 'b', code_system='test',
                     title='Section', parent_ordinal=None, ancestry_path='/', own_text='abcdef')
                for i in range(1, 1002)], {'snapshot': self.snapshot, 'raw_sha256': '0' * 64}


def test_complete_version_pages_and_input_bounds():
    q = Query(ManyRecords())
    numbers, offset = [], 0
    while True:
        page = q.versions(SID, offset=offset)
        assert len(page['versions']) <= 200
        assert page['total_captured_versions'] == 451
        assert page['snapshot'] == q.reader.snapshot
        numbers.extend(v['version'] for v in page['versions'])
        offset = page['next_offset']
        if offset is None:
            break
    assert numbers == list(range(1, 452))
    assert q.versions(SID, offset=999)['versions'] == []
    for limit, offset in [(True, 0), (201, 0), (1, -1), (1, False)]:
        with pytest.raises(ValueError):
            q.versions(SID, limit=limit, offset=offset)


def test_filtered_occurrence_pages_and_independent_text_offsets():
    q = Query(ManyRecords())
    numbers, offset = [], 0
    while True:
        page = q.show(SID, 1, code='a', section_offset=offset, max_chars=1)
        assert len(page['sections']) <= 100
        assert sum(len(s['text']) for s in page['sections']) <= 1
        assert page['matching_section_instances'] == 501
        assert page['total_section_instances'] == 1001
        numbers.extend(s['ordinal'] for s in page['sections'])
        offset = page['next_section_offset']
        if offset is None:
            break
    assert numbers == list(range(1, 1002, 2))
    first = q.show(SID, 1, ordinal=3, max_chars=2)
    assert first['sections'][0]['next_offset'] == 2
    second = q.show(SID, 1, ordinal=3, max_chars=2, offset=2)
    assert second['sections'][0]['text'] == 'cd'
    assert second['next_section_offset'] is None
    dated = q.show_date(SID, '2026-01-01', code='a', section_limit=2, section_offset=2)
    assert [s['ordinal'] for s in dated['sections']] == [5, 7]
    for limit, offset in [(True, 0), (201, 0), (1, -1), (1, False)]:
        with pytest.raises(ValueError):
            q.show(SID, 1, section_limit=limit, section_offset=offset)
        with pytest.raises(ValueError):
            q.show_date(SID, '1900-01-01', section_limit=limit, section_offset=offset)


def test_cli_pages_and_storage_adapter_agreement(release, capsys):
    assert main(['--config', str(release), 'versions', SID, '--limit', '1', '--offset', '1']) == 0
    assert [v['version'] for v in json.loads(capsys.readouterr().out)['versions']] == [2]
    assert main(['--config', str(release), 'show', SID, '--date', '2026-01-01',
                 '--section-limit', '1', '--section-offset', '1']) == 0
    assert [s['ordinal'] for s in json.loads(capsys.readouterr().out)['sections']] == [2]
    answers = []
    for backend in ('sqlite', 'git'):
        with open_reader(release, backend) as reader:
            q = Query(reader)
            answer = q.show(SID, 2, section_limit=1, section_offset=1)
            assert answer['next_section_offset'] is None
            answers.append(answer['sections'])
    assert answers[0] == answers[1]


def test_browser_consumes_every_version_page(job, tmp_path, monkeypatch):
    from gitrx_backend.web import App
    from test_release_cycle import SID as JOB_SID
    original = Query.versions
    offsets = []

    def small_pages(self, sid, limit=200, offset=0, **kwargs):
        offsets.append(offset)
        return original(self, sid, limit=1, offset=offset, **kwargs)

    monkeypatch.setattr(Query, 'versions', small_pages)
    app = App(tmp_path/'parent/backend.json', tmp_path/'registry')
    page = app.page({'family': JOB_SID, 'version': '2'}, 'http://127.0.0.1:8766')
    assert offsets == [0, 1]
    assert '>v1 · Published' in page and '>v2 · Published' in page
