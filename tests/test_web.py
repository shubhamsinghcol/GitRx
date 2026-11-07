import html,json,re
from pathlib import Path
import pytest
from test_release_cycle import job,inbox,SID
from gitrx_backend.web import App,table_html,label_html

def test_plain_journey_and_retained_references(job,tmp_path):
    registry=tmp_path/'registry';old=tmp_path/'parent/backend.json';app=App(old,registry)
    snapshot=app.current
    search=app.page({'q':'Synthetic'},'http://127.0.0.1:8766')
    assert '<table>' in search and 'Label families' in search
    read=app.page({'family':SID,'version':'2'},'http://127.0.0.1:8766')
    assert 'SPL effective' in read and 'Version timeline' in read and 'Source SHA-256:' in read
    assert 'schema' not in read and 'max_chars' not in read and 'parse_key' not in read
    assert f'release={snapshot}' in html.unescape(read)
    comparison=app.page({'family':SID,'view':'compare','before':'1','after':'2'},'http://127.0.0.1:8766')
    assert 'Compare' in comparison and '<ins>' in comparison
    phrase=app.page({'family':SID,'view':'phrase','phrase':'warning'},'http://127.0.0.1:8766')
    assert 'First observed wording' in phrase and 'not a proven addition date' in phrase
    absent=app.page({'family':SID,'on_date':'1900-01-01'},'http://127.0.0.1:8766')
    assert 'No captured version' in absent
    result=job.cycle(inbox(tmp_path))
    new=App(result['config'],registry)
    assert new.current!=snapshot
    assert new.page({'release':snapshot,'family':SID,'version':'2'},'http://127.0.0.1:8766')==read
    with pytest.raises(ValueError):new.page({'release':'../bad'},'http://127.0.0.1:8766')

def test_tables_keep_cells_spans_and_escape_source_content():
    t={'flattened_text':'<ROW>\n<CELL>x</CELL>\n</ROW>','structure':{'rows':[{'cells':[{'tag':'th','text':'<script>bad()</script>','attributes':{'colspan':'2','onclick':'evil()'}}]},{'cells':[{'tag':'td','text':'10 mg','attributes':{}}]}]}}
    rendered=label_html('Before\n'+t['flattened_text']+'\nAfter',[t])
    assert '<th colspan="2">' in rendered and '<td>10 mg</td>' in rendered
    assert '<script>' not in rendered and 'onclick' not in rendered and '&lt;script&gt;' in rendered
    assert '&lt;ROW&gt;' not in rendered and rendered.index('Before')<rendered.index('<table')<rendered.index('After')
    fallback=table_html({'flattened_text':'a | b'})
    assert 'layout is unavailable' in fallback and 'a | b' in fallback

def test_local_http_view_security_and_saved_release_errors(job,tmp_path):
    import subprocess,sys,urllib.request,urllib.error
    from gitrx_backend.limits import deadline
    cmd=[sys.executable,'-B','-m','gitrx_backend.web','--config',str(tmp_path/'parent/backend.json'),'--registry',str(tmp_path/'registry'),'--port','0']
    p=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
    try:
        with deadline(10):line=p.stdout.readline()
        address=line.strip().split()[-1]
        with urllib.request.urlopen(address,timeout=5) as r:
            assert r.status==200 and "frame-ancestors 'none'" in r.headers['Content-Security-Policy']
            assert b'Find a label family' in r.read()
        request=urllib.request.Request(address,headers={'Host':'untrusted.example'})
        with pytest.raises(urllib.error.HTTPError) as exc:urllib.request.urlopen(request,timeout=5)
        assert exc.value.code==403
        with pytest.raises(urllib.error.HTTPError) as exc:urllib.request.urlopen(address+'/?release=bad',timeout=5)
        assert b'Could not open this view' in exc.value.read()
    finally:
        p.terminate();p.wait(timeout=5)
