"""MCP server and CLI must open the same configuration and give the same answers (fixtures only, no data root)."""
import json
import sys

import pytest

from gitrx_backend import interface_check, selftest


@pytest.fixture(scope='module')
def synthetic(tmp_path_factory):
    out = tmp_path_factory.mktemp('selftest') / 'release'
    return selftest.run(out)['config']


@pytest.fixture(autouse=True)
def no_data_root(monkeypatch, tmp_path):
    # Hermetic: the synthetic release needs no data root, and update state must read as unavailable.
    monkeypatch.setenv('GITRX_DATA_ROOT', str(tmp_path / 'absent-data-root'))


def test_mcp_starts_on_synthetic_release_lists_tools_and_agrees_with_cli(synthetic):
    checks = interface_check.run([sys.executable, '-B', '-m', 'gitrx_backend.mcp', '--config', synthetic],
                                 [sys.executable, '-B', '-m', 'gitrx_backend.cli', '--config', synthetic])
    failed = [(n, d) for n, ok, d in checks if not ok]
    assert not failed, failed
    assert len(checks) >= 9


def test_mcp_refuses_unopenable_config_with_explicit_error(tmp_path):
    bad = tmp_path / 'bad.json'
    bad.write_text(json.dumps({'format': 'gitrx-backend-config-1', 'backend': 'rebuild', 'snapshot': 'a' * 64,
                               'rebuild_root': '${GITRX_DATA_ROOT}/nowhere', 'rebuild_complete_sha256': 'a' * 64,
                               'names_index': '${GITRX_DATA_ROOT}/nowhere/n.sqlite', 'names_index_sha256': 'b' * 64}))
    with pytest.raises(RuntimeError, match='did not initialize'):
        interface_check.mcp_session([sys.executable, '-B', '-m', 'gitrx_backend.mcp', '--config', str(bad)],
                                    [('describe_release', {})])


def test_disagreement_is_detected(synthetic, monkeypatch):
    real = interface_check.cli_call

    def tampered(command, name, arguments, timeout=180):
        answer = real(command, name, arguments, timeout)
        if name == 'families':
            answer['families'] = answer['families'][:-1]
        return answer
    monkeypatch.setattr(interface_check, 'cli_call', tampered)
    checks = interface_check.run([sys.executable, '-B', '-m', 'gitrx_backend.mcp', '--config', synthetic],
                                 [sys.executable, '-B', '-m', 'gitrx_backend.cli', '--config', synthetic])
    assert [n for n, ok, _ in checks if not ok] == ['MCP == CLI: families']
