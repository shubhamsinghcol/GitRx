"""Change observations preserve ambiguity, literal edits and resumable scans."""
import pytest
from test_backend_portable import SID
from gitrx_backend.query import Query
from gitrx_backend.changes import details,search


def section(text,ordinal=1,title='Warning',code='a',xml=None):
    value=dict(ordinal=ordinal,loinc_code=code,code_system='loinc',title=title,parent_ordinal=None,ancestry_path='/',own_text=text)
    if xml is not None:value['source_xml']=xml
    return value


class Reader:
    snapshot='test';evidence_metadata_sha256='metadata'
    def __init__(self,rows=None):self.rows=rows or {1:[section('risk low')],2:[section('risk high')],3:[section('risk low')]}
    def families(self):return [{'setid':SID,'title':'Example'}]
    def versions(self,sid):return [dict(version=n,publication_dates=[] if n==3 else ['2026-01-0'+str(n)],effective_date=None) for n in sorted(self.rows)]
    def read(self,sid,version):return self.rows[version],{'snapshot':self.snapshot,'raw_sha256':str(version),'evidence':{'extraction':{'status':'complete'}}}


def test_baseline_default_predecessor_aba_endpoint_and_events():
    q=Query(Reader())
    assert details(q,SID,1)['baseline']
    assert details(q,SID,3)['previous_version']==2
    endpoint=details(q,SID,3,previous=1)
    assert endpoint['summary'].get('content_changed',0)==0
    assert endpoint['numeric_gap'] is None and endpoint['intermediate_captured_version_count']==1
    hits=search(q,SID,'high')['hits']
    assert [(h['previous_version'],h['version']) for h in hits]==[(1,2),(2,3)]
    assert {m['side'] for h in hits for m in h['matches']}=={'before','after'}
    with pytest.raises(ValueError):details(q,SID,2,previous=3)


def test_precise_literal_changed_spans_and_crossing_context():
    q=Query(Reader({1:[section('risk low and dose 10 mg')],2:[section('risk high and dose 20 mg')]}))
    assert not search(q,SID,'risk')['hits']
    assert search(q,SID,'risk high')['hits'][0]['matches'][0]['text_excerpt']=='risk high and dose 20 mg'
    assert search(q,SID,'20 mg')['hits']
    assert not search(q,SID,'dose')['hits']
    q=Query(Reader({1:[section('dose')],2:[section('doses')]}))
    assert not search(q,SID,'dose')['hits']
    assert search(q,SID,'doses')['hits']


def test_whitespace_case_units_structure_and_source_flags():
    q=Query(Reader({1:[section('risk low',xml='<text>risk low</text>')],2:[section('risk  low',ordinal=2,title='Safety',xml='<text>risk  low</text>')]}))
    row=details(q,SID,2)['changes'][0]
    assert row['raw_text_changed'] and not row['content_changed']
    assert row['heading_changed'] and row['position_changed'] and row['source_representation_changed']
    assert row['before_source_xml_sha256']!=row['after_source_xml_sha256']
    assert search(q,SID,'risk  low')['hits']
    assert search(q,SID,'risk low')['hits'] # insertion inside the literal breaks its former presence
    q=Query(Reader({1:[section('10 mg risk')],2:[section('10 mcg RISK')]}))
    assert search(q,SID,'10 mcg')['hits'] and search(q,SID,'risk')['hits']
    assert not search(q,SID,'Risk',ignore_case=False)['hits']


def test_repeated_uncoded_ambiguity_and_paged_details():
    r=Reader({1:[section('one',1,'Same'),section('two',2,'Same')],2:[section('three',3,'Same'),section('four',4,'Same')]})
    q=Query(r);a=details(q,SID,2,limit=1)
    assert a['summary']['unmatched_or_ambiguous']==4 and a['next_offset']==1
    assert a['changes'][0]['unmatched_or_ambiguous']
    hits=search(q,SID,'three')['hits'];assert hits and hits[0]['unmatched_or_ambiguous']
    r.rows={1:[section('one',code=None)],2:[section('two',code=None)]}
    assert details(q,SID,2)['changes'][0]['match_rule']=='unique-heading'


def test_search_pages_no_skip_filter_unknown_dates_gaps_and_binding():
    q=Query(Reader({1:[section('a')],2:[section('b')],3:[section('a')],5:[section('b')]}))
    first=search(q,SID,'b',limit=1,max_transitions=1)
    assert first['partial'] and len(first['hits'])==1
    rows=list(first['hits']);page=first
    while page['continuation']:
        page=search(q,SID,'b',limit=1,cursor=page['continuation'],max_transitions=1)
        rows.extend(page['hits'])
    assert [(r['previous_version'],r['version']) for r in rows]==[(1,2),(2,3),(3,5)]
    assert rows[-1]['gap'] and rows[-1]['unobserved_integer_count']==1
    assert not page['changed_text_coverage_complete']
    filtered=search(q,SID,'b',start='2026-01-02',end='2026-01-02')
    assert len(filtered['hits'])==1 and filtered['date_unknown_transitions_excluded']==1
    for changed in ({'phrase':'a'},{'code':'a'},{'ignore_case':False},{'start':'2026-01-01'}):
        args=dict(sid=SID,phrase='b',cursor=first['continuation']);args.update(changed)
        with pytest.raises(ValueError,match='cursor'):search(q,**args)
    q.reader.evidence_metadata_sha256='new'
    with pytest.raises(ValueError,match='cursor'):search(q,SID,'b',cursor=first['continuation'])
    with pytest.raises(ValueError):search(q,SID,'b',start='2026-02-30')


def test_scan_bound_zero_hit_continuation_and_large_unresolved():
    q=Query(Reader())
    result=search(q,SID,'absent',max_transitions=1)
    assert not result['hits'] and result['partial'] and result['continuation']
    q.reader.rows={1:[section('a '*600)],2:[section('b '*600)]}
    result=search(q,SID,'b')
    assert result['unexamined_occurrences']==1 and not result['changed_text_coverage_complete']
    assert not result['hits']


def test_casefold_source_offsets_and_invalid_cursor_values():
    q=Query(Reader({1:[section('prefix no')],2:[section('ß RISK')]}))
    match=search(q,SID,'ss risk')['hits'][0]['matches'][0]
    assert match['match_start']==0 and match['match_end']==6
    for token in ('!', 'W10=', 'bnVsbA=='):
        with pytest.raises(ValueError,match='cursor'):search(q,SID,'risk',cursor=token)


def test_long_unchanged_surroundings_do_not_hide_a_small_edit():
    prefix='unchanged '*800
    q=Query(Reader({1:[section(prefix+'10 mg'+prefix)],2:[section(prefix+'20 mg'+prefix)]}))
    result=search(q,SID,'20 mg')
    assert result['hits'] and result['unexamined_occurrences']==0
    assert not search(q,SID,'unchanged')['hits']


def test_huge_unmatched_text_and_uncertain_extraction_are_not_complete_evidence():
    q=Query(Reader({1:[],2:[section('a'*200001)]}))
    result=search(q,SID,'a')
    assert result['unexamined_occurrences']==1 and not result['changed_text_coverage_complete']
    class Partial(Reader):
        def read(self,sid,version):
            rows,citation=super().read(sid,version)
            citation['evidence']['extraction']['status']='partial'
            return rows,citation
    result=search(Query(Partial()),SID,'absent')
    assert result['extraction_uncertain_transitions']==2
    assert not result['changed_text_coverage_complete'] and result['selected_extracted_text_scan_complete']
