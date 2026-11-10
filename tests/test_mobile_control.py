import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from gitrx_backend import mobile_control as m
from gitrx_backend.jobs import sha


def ready(tmp_path):
    root=tmp_path/'local/mobile-workspace';root.mkdir(parents=True)
    job={'workspace':str(root),'volume':str(tmp_path/'external'), 'volume_marker':'marker','volume_token':'token',
        'mobile_archive':{'archive_workspace':str(tmp_path/'external/job')}}
    (root/'config.json').write_text(json.dumps(job));(root/'MOBILE-READY.json').write_text(json.dumps({'config_sha256':sha(root/'config.json')}))
    support=tmp_path/'support';support.mkdir()
    return {'mobile_workspace':str(root),'workspace':str(root),'state_directory':str(support)},root


def test_eject_pauses_then_asks_macos_without_force_and_resumes_locally(tmp_path,monkeypatch):
    config,root=ready(tmp_path);events=[]
    monkeypatch.setattr(m,'pause_wait',lambda c:events.append('paused'))
    monkeypatch.setattr(m.control,'start',lambda c:events.append('resumed'))
    monkeypatch.setattr(m.MobileArchive,'available',lambda *a,**kw:True)
    def disk(*args):
        assert (root/'SSD-DISABLED').exists();events.append(args)
        return SimpleNamespace(returncode=0,stderr='',stdout='ejected')
    monkeypatch.setattr(m,'diskutil',disk)
    assert 'safely ejected' in m.eject(config)
    assert events==['paused',('eject',str(tmp_path/'external')),'resumed']


def test_refused_eject_never_reports_safe_and_still_resumes_local_work(tmp_path,monkeypatch):
    config,root=ready(tmp_path);resumed=[]
    monkeypatch.setattr(m,'pause_wait',lambda c:None)
    monkeypatch.setattr(m.control,'start',lambda c:resumed.append(True))
    monkeypatch.setattr(m.MobileArchive,'available',lambda *a,**kw:True)
    monkeypatch.setattr(m,'diskutil',lambda *a:SimpleNamespace(returncode=1,stderr='disk busy',stdout=''))
    with pytest.raises(RuntimeError,match='Do not unplug'):m.eject(config)
    assert resumed and (root/'SSD-DISABLED').exists()


def test_eject_requires_verified_local_configuration_before_touching_drive(tmp_path,monkeypatch):
    config,root=ready(tmp_path);(root/'config.json').write_text('{}')
    monkeypatch.setattr(m,'diskutil',lambda *a:pytest.fail('must not eject'))
    monkeypatch.setattr(m,'pause_wait',lambda *a:pytest.fail('must not pause'))
    with pytest.raises(RuntimeError,match='not prepared'):m.eject(config)


def test_reconnect_without_expected_ssd_leaves_local_work_running(tmp_path,monkeypatch):
    config,root=ready(tmp_path)
    monkeypatch.setattr(m.MobileArchive,'available',lambda *a,**kw:False)
    monkeypatch.setattr(m,'pause_wait',lambda c:pytest.fail('do not stop healthy local work'))
    with pytest.raises(RuntimeError,match='local work can continue'):m.reconnect(config)


def test_reconnect_verifies_and_syncs_before_resuming(tmp_path,monkeypatch):
    config,root=ready(tmp_path);(root/'SSD-DISABLED').write_text('disabled');events=[]
    monkeypatch.setattr(m.MobileArchive,'available',lambda *a,**kw:True)
    monkeypatch.setattr(m,'pause_wait',lambda c:events.append('paused'))
    monkeypatch.setattr(m.MobileArchive,'sync_pending',lambda a:events.append('verified transfer'))
    monkeypatch.setattr(m.control,'start',lambda c:events.append('resumed'))
    assert 'verified and enabled' in m.reconnect(config)
    assert events==['paused','verified transfer','resumed'] and not (root/'SSD-DISABLED').exists()


def test_reconnect_can_release_outbox_even_when_working_budget_is_full(tmp_path,monkeypatch):
    config,root=ready(tmp_path)
    monkeypatch.setattr(m.MobileArchive,'guard',lambda self:(_ for _ in ()).throw(RuntimeError('local cap reached')))
    monkeypatch.setattr(m.MobileArchive,'available',lambda *a,**kw:True)
    monkeypatch.setattr(m.MobileArchive,'sync_pending',lambda self:None)
    monkeypatch.setattr(m,'pause_wait',lambda c:None)
    monkeypatch.setattr(m.control,'start',lambda c:None)
    assert 'verified and enabled' in m.reconnect(config)


def test_storage_button_does_not_clear_existing_integrity_review(tmp_path,monkeypatch):
    config,root=ready(tmp_path)
    support=Path(config['state_directory']);(support/'ATTENTION').write_text('unreviewed integrity failure')
    monkeypatch.setattr(m,'pause_wait',lambda c:None)
    monkeypatch.setattr(m.MobileArchive,'available',lambda *a,**kw:True)
    monkeypatch.setattr(m,'diskutil',lambda *a:SimpleNamespace(returncode=0,stderr='',stdout=''))
    monkeypatch.setattr(m.control,'start',lambda c:pytest.fail('must not acknowledge integrity stop'))
    assert 'existing integrity review' in m.eject(config)
    assert (support/'ATTENTION').read_text()=='unreviewed integrity failure'


def test_failed_archive_transfer_preserves_data_and_stops_for_review(tmp_path,monkeypatch):
    config,root=ready(tmp_path)
    monkeypatch.setattr(m.MobileArchive,'available',lambda *a,**kw:True)
    monkeypatch.setattr(m,'pause_wait',lambda c:None)
    def failed(self):raise RuntimeError('immutable SSD batch conflict')
    monkeypatch.setattr(m.MobileArchive,'sync_pending',failed)
    monkeypatch.setattr(m.control,'start',lambda c:pytest.fail('do not resume after integrity failure'))
    with pytest.raises(RuntimeError,match='batch conflict'):m.reconnect(config)
    assert (root/'SSD-DISABLED').exists()
    assert 'batch conflict' in (Path(config['state_directory'])/'ATTENTION').read_text()


def test_reconnect_prepares_shared_network_store_once_for_multiple_batches(tmp_path,monkeypatch):
    from gitrx_backend.mobile_archive import MobileArchive
    archive=object.__new__(MobileArchive);archive.root=tmp_path/'local';archive.root.mkdir()
    for n in range(3):
        marker=archive.root/'batches'/str(n)/'BATCH.json';marker.parent.mkdir(parents=True);marker.write_text('{}')
    prepared=[];batches=[]
    monkeypatch.setattr(archive,'external_guard',lambda:None)
    monkeypatch.setattr(archive,'_sync_network',lambda:prepared.append(True))
    monkeypatch.setattr(archive,'receipt',lambda batch:None)
    monkeypatch.setattr(archive,'sync_batch',lambda batch,network_prepared=False:batches.append(network_prepared))
    archive.sync_pending()
    assert prepared==[True] and batches==[True,True,True]
