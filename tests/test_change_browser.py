"""Browser change inspection stays thin, pinned, paged and explicit about unknowns."""
import html
from test_release_cycle import job, SID
from gitrx_backend.web import App
from gitrx_backend.query import Query


def test_version_changes_preserve_comparison_selection_and_escape_sources(job,tmp_path,monkeypatch):
    app=App(tmp_path/'parent/backend.json',tmp_path/'registry')
    seen=[]
    def details(self,sid,version,**kwargs):
        seen.append((sid,version,kwargs))
        return {'version':2,'previous_version':1,'baseline':False,'summary':{'total_comparisons':52,'unmatched_or_ambiguous':2},
                'numeric_gap':{'unobserved_integer_count':1},'next_offset':50,
                'changes':[{'before':{'ordinal':1,'title':'<script>title</script>'},'after':None,
                            'unmatched_or_ambiguous':True,'word_diff':[{'operation':'delete','before':'<script>text</script>','after':''}],
                            'truncated':True}]}
    monkeypatch.setattr(Query,'version_changes',details)
    result=app.page({'family':SID,'view':'changes','version':'2','previous':'1','code':'34066-1','change_offset':'3'},'http://127.0.0.1:8766')
    assert seen[0][1]==2 and seen[0][2]['previous']==1 and seen[0][2]['offset']==3
    assert seen[0][2]['code']=='34066-1'
    assert 'could not be paired confidently' in result and 'not proof that a section was added or removed' in result
    assert '&lt;script&gt;text&lt;/script&gt;' in result and '<script>text</script>' not in result
    assert 'Equal wording does not establish' in result and 'unobserved version positions' in result
    decoded=html.unescape(result)
    assert 'change_offset=50' in decoded and 'previous=1' in decoded and 'version=2' in decoded
    assert 'release='+app.current in decoded and 'code=34066-1' in decoded
    assert 'Source SHA-256:' in result


def test_baseline_view_does_not_invent_introduction(job,tmp_path):
    app=App(tmp_path/'parent/backend.json',tmp_path/'registry')
    page=app.page({'family':SID,'view':'changes','version':'1'},'http://127.0.0.1:8766')
    assert 'no captured predecessor' in page and 'not proof that this label or its sections were introduced' in page
    assert 'Source SHA-256:' in page


def test_changed_search_preserves_date_filter_continuation_and_negative_limits(job,tmp_path,monkeypatch):
    app=App(tmp_path/'parent/backend.json',tmp_path/'registry')
    seen=[]
    def search(self,sid,phrase,**kwargs):
        seen.append((sid,phrase,kwargs))
        return {'hits':[],'transitions_scanned':3,'date_unknown_transitions_excluded':1,
                'date_outside_transitions_excluded':2,'changed_text_coverage_complete':False,'unexamined_occurrences':1,
                'partial':True,'continuation':'next-query-pin'}
    monkeypatch.setattr(Query,'search_changes',search)
    page=app.page({'family':SID,'view':'change-search','phrase':'risk','code':'34066-1',
                   'changed_from':'2020-01-01','changed_to':'2021-01-01','cursor':'prior-query-pin'},'http://127.0.0.1:8766')
    kwargs=seen[0][2]
    assert kwargs['start']=='2020-01-01' and kwargs['end']=='2021-01-01' and kwargs['cursor']=='prior-query-pin'
    assert 'no usable publication date' in page and 'A negative result is incomplete' in page
    assert 'Continue even when this page has no hits' in page
    decoded=html.unescape(page)
    for expected in ('cursor=next-query-pin','changed_from=2020-01-01','changed_to=2021-01-01','code=34066-1','release='+app.current):assert expected in decoded


def test_changed_search_safe_matches_and_real_query_integration(job,tmp_path,monkeypatch):
    app=App(tmp_path/'parent/backend.json',tmp_path/'registry')
    # Actual shared endpoint comparison and bounded search work through the view.
    details=app.page({'family':SID,'view':'changes','version':'2'},'http://127.0.0.1:8766')
    assert 'Selected comparison summary' in details and 'Read v1' in details and 'Read v2' in details
    search=app.page({'family':SID,'view':'change-search','phrase':'2'},'http://127.0.0.1:8766')
    assert 'Changed-text matches' in search and 'Source SHA-256:' in search
    def matched(self,*args,**kwargs):
        return {'transitions_scanned':1,'hits':[{'previous_version':1,'version':2,'publication_dates':[],
                'before':None,'after':{'ordinal':1,'title':'<script>title</script>'},'unmatched_or_ambiguous':True,
                'gap':True,'unobserved_integer_count':1,'matches':[{'side':'after','text_excerpt':'<script>risk</script>'}]}],
                'partial':False,'continuation':None,'changed_text_coverage_complete':True}
    monkeypatch.setattr(Query,'search_changes',matched)
    page=app.page({'family':SID,'view':'change-search','phrase':'risk'},'http://127.0.0.1:8766')
    assert '&lt;script&gt;risk&lt;/script&gt;' in page and '<script>risk</script>' not in page
    assert 'precise change date is unknown' in page and 'one-sided wording is not proof' in page
