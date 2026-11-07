"""Browser metadata consistency, source escaping and complete-history continuation."""
import html
import json
from pathlib import Path
import pytest
from test_release_cycle import job, SID
from gitrx_backend.web import App, publisher_html
from gitrx_backend.query import Query


def test_publisher_references_are_labeled_and_safe():
    evidence={'publisher_references':[
        {'url':'https://dailymed-data.nlm.nih.gov/release.zip','kind':'bulk-member','observed':True,'member':'nested/<script>.zip'},
        {'url':'https://dailymed.nlm.nih.gov/version','kind':'exact-version','observed':False},
        {'url':'javascript:alert(1)','kind':'exact-version','observed':True},
        {'url':'https://not-nlm.example/source','kind':'exact-version','observed':True},
    ]}
    output=publisher_html(evidence)
    assert 'Publisher bulk archive' in output and 'Bulk member nested/&lt;script&gt;.zip' in output
    assert 'link not verified by retained retrieval' in output
    assert 'javascript:' not in output and 'not-nlm.example' not in output and '<script>' not in output


def test_browser_prefers_shared_pinned_evidence_and_copies_provenance(job,tmp_path):
    app=App(tmp_path/'parent/backend.json',tmp_path/'registry')
    reader,_=app.release(app.current)
    reader.evidence=lambda sid,v:{'metadata_status':'pinned','extraction':{'status':'partial'},
        'publisher_references':[{'kind':'bulk-member','url':'https://dailymed-data.nlm.nih.gov/month.zip','observed':True,'member':'member.zip'}]}
    # Shared statuses override old browser-only status caches.
    app.status[app.current][(SID,2)]='complete'
    output=app.page({'family':SID,'version':'2'},'http://127.0.0.1:8766')
    assert 'Incomplete text' in output and 'Publisher bulk archive' in output
    assert 'Bulk member: member.zip' in html.unescape(output)
    assert 'Source SHA-256:' in output and 'Release: '+app.current in output
    assert '>Copy reference</button>' in output


def test_history_view_preserves_scope_and_pagination_and_escapes_source(job,tmp_path,monkeypatch):
    app=App(tmp_path/'parent/backend.json',tmp_path/'registry')
    called=[]
    def history(self,sid,phrase,**kwargs):
        called.append((sid,phrase,kwargs))
        return {'observations':[{'version':2,'publication_dates':['2026-01-01'],'effective_date':None,
            'evidence_state':'present','transition':'present_after_unknown',
            'gap_from_previous':{'after_version':0,'before_version':2,'unobserved_integer_count':1},
            'matches':[{'ordinal':1,'excerpt':'<script>text</script>'}]}],
            'continuation':'release-query-bound-token','partial':True}
    monkeypatch.setattr(Query,'phrase_history',history,raising=False)
    output=app.page({'family':SID,'view':'history','phrase':'warning','code':'34066-1','cursor':'prior-token'},'http://127.0.0.1:8766')
    assert called[0][2]['cursor']=='prior-token' and called[0][2]['code']=='34066-1'
    assert 'Match after an unknown interval' in output
    assert '&lt;script&gt;text&lt;/script&gt;' in output and '<script>text</script>' not in output
    assert 'not proven addition or removal dates' in output and 'Continuity across this interval is unknown' in output
    assert 'More wording history' in output and 'cursor=release-query-bound-token' in output
    assert 'release='+app.current in output and 'code=34066-1' in output


def test_registry_adds_verified_metadata_without_downgrade(job,tmp_path,monkeypatch):
    import gitrx_backend.web as web
    config=tmp_path/'parent/backend.json';registry=tmp_path/'registry'
    first=App(config,registry);saved=registry/(first.current+'.json');before=saved.read_bytes()
    richer=json.loads(config.read_text());richer.update(evidence_metadata='metadata.sqlite',evidence_metadata_sha256='a'*64)
    extended=config.with_name('extended.json');extended.write_text(json.dumps(richer))
    original=web.open_reader
    checks=[]
    def reader(path,backend=None):
        checks.append(Path(path))
        # This test isolates registry behavior; projection validation has its own integration tests.
        return original(config,backend)
    monkeypatch.setattr(web,'open_reader',reader)
    App(extended,registry)
    assert checks==[extended]
    upgraded=json.loads(saved.read_text())
    assert upgraded['evidence_metadata']==str((config.parent/'metadata.sqlite').resolve())
    assert (registry/(first.current+'.legacy.json')).read_bytes()==before
    App(config,registry)
    assert json.loads(saved.read_text())==upgraded
    richer['evidence_metadata_sha256']='b'*64;extended.write_text(json.dumps(richer))
    with pytest.raises(ValueError,match='different evidence metadata pin'):App(extended,registry)
