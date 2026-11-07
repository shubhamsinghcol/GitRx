"""Phrase histories preserve observed states and refuse invented continuity."""
import base64
import json
import pytest
from test_backend_portable import SID, release
from gitrx_backend.query import Query
from gitrx_backend.config import open_reader
from gitrx_backend.phrase_history import history


class Reader:
    snapshot = 'synthetic'
    evidence_metadata_sha256 = 'metadata-a'
    rows = [(1, 'none', 'complete'), (2, 'risk', 'complete'), (3, '', 'partial'),
            (4, 'none', 'complete'), (5, 'risk', 'partial'), (7, 'risk', 'complete'),
            (8, 'none', 'complete'), (9, 'risk', 'complete')]

    def families(self): return [{'setid': SID, 'title': 'Example'}]
    def versions(self, sid):
        return [{'version': n, 'publication_dates': ['2026-01-01'], 'effective_date': None} for n, _, _ in self.rows]
    def read(self, sid, version):
        _, text, _ = next(r for r in self.rows if r[0] == version)
        return [dict(ordinal=1, own_text=text, loinc_code='a', code_system='loinc', title='one', ancestry_path='/'),
                dict(ordinal=2, own_text=text, loinc_code='a', code_system='loinc', title='two', ancestry_path='/')], {'raw_sha256': str(version), 'snapshot': self.snapshot}
    def evidence(self, sid, version):
        return {'extraction': {'status': next(r[2] for r in self.rows if r[0] == version), 'diagnostics': []}}


def test_unknowns_gaps_and_returns_are_observations_not_introduction_dates():
    result = history(Query(Reader()), SID, 'risk')
    rows = result['observations']
    assert [r['evidence_state'] for r in rows] == ['checked_nonmatch', 'present', 'unknown', 'checked_nonmatch', 'present', 'present', 'checked_nonmatch', 'present']
    assert [r['transition'] for r in rows] == ['nonmatch_at_record_start', 'observed_nonmatch_to_match', 'continuity_unknown', 'continuity_unknown', 'observed_return', 'present_after_unknown', 'observed_match_to_nonmatch', 'observed_return']
    assert rows[5]['gap_from_previous']['unobserved_integer_count'] == 1
    assert rows[4]['extraction']['status'] == 'partial' and rows[4]['matching_occurrences'] == 2
    assert [m['ordinal'] for m in rows[4]['matches']] == [1, 2]
    assert result['historical_completeness_claim'] is False


def test_pages_reconstruct_identical_observations_and_bind_all_selection():
    q = Query(Reader()); full = history(q, SID, 'risk')['observations']
    page = history(q, SID, 'risk', limit=2); rows = list(page['observations'])
    cursor = page['continuation']
    for changed in ({'phrase':'other'}, {'code':'a'}, {'ignore_case':False}):
        arguments = dict(sid=SID, phrase='risk', cursor=cursor); arguments.update(changed)
        with pytest.raises(ValueError, match='cursor'): history(q, **arguments)
    q.reader.evidence_metadata_sha256 = 'metadata-b'
    with pytest.raises(ValueError, match='cursor'): history(q, SID, 'risk', cursor=cursor)
    q.reader.evidence_metadata_sha256 = 'metadata-a'
    while page['continuation']:
        page = history(q, SID, 'risk', cursor=page['continuation'], limit=2)
        rows.extend(page['observations'])
    assert rows == full
    assert not page['partial']
    assert history(q, SID, 'risk', offset=100)['observations'] == []
    with pytest.raises(ValueError): history(q, SID, 'risk', limit=True)
    with pytest.raises(ValueError): history(q, SID, 'risk', offset=1, cursor=cursor)


def test_literal_casefold_offsets_whitespace_and_first_record():
    reader = Reader(); reader.rows = [(1, 'ß RISK\nword', 'complete'), (2, 'risk word', 'complete')]
    q = Query(reader)
    a = history(q, SID, 'ss risk')['observations'][0]
    assert a['transition'] == 'present_at_record_start' and a['matches'][0]['match_start'] == 0
    a = history(q, SID, 'risk')['observations'][0]
    assert a['matches'][0]['match_start'] == 2
    assert history(q, SID, 'risk word')['observations'][0]['literal_presence'] == 'not_observed_in_extracted_text'
    assert history(q, SID, 'risk', ignore_case=False)['observations'][0]['literal_presence'] == 'not_observed_in_extracted_text'
    assert history(q, SID, 'RISK\nword', ignore_case=False)['observations'][0]['literal_presence'] == 'present'


def test_old_adapters_have_unknown_negative_extraction_and_preserve_citations(release):
    answers = []
    for backend in ('sqlite', 'git'):
        with open_reader(release, backend) as reader:
            q = Query(reader)
            answer = history(q, SID, 'changed')
            assert answer['observations'][0]['evidence_state'] == 'unknown'
            assert answer['observations'][1]['literal_presence'] == 'present'
            assert answer['observations'][1]['transition'] == 'present_after_unknown'
            assert 'raw_sha256' in answer['observations'][1]['citation']
            answers.append([(r['version'], r['literal_presence']) for r in answer['observations']])
    assert answers[0] == answers[1]


def test_malformed_cursor_values_are_input_errors():
    q = Query(Reader())
    for value in (None, True, 4, 'string', [], {}):
        token = base64.urlsafe_b64encode(json.dumps(value).encode()).decode()
        with pytest.raises(ValueError, match='cursor'):
            history(q, SID, 'risk', cursor=token)


def test_query_history_deadline_returns_no_partial_answer(monkeypatch):
    import time
    from gitrx_backend.limits import QueryTimeout
    reader = Reader(); original = reader.read
    def slow(*args):
        time.sleep(.05)
        return original(*args)
    monkeypatch.setattr(reader, 'read', slow)
    with pytest.raises(QueryTimeout):
        Query(reader).phrase_history(SID, 'risk', timeout_seconds=.01)
    monkeypatch.setattr(reader, 'read', original)
    assert Query(reader).phrase_history(SID, 'risk')['observations']


def test_long_unicode_selection_has_small_resumable_cursor():
    q=Query(Reader())
    phrase='😀'*500;code='漢'*500
    page=q.phrase_history(SID,phrase,code=code,limit=1)
    assert len(page['continuation'])<4000
    next_page=q.phrase_history(SID,phrase,code=code,limit=1,cursor=page['continuation'])
    assert next_page['observations'][0]['version']==2
