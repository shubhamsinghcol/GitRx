"""GITRX_DATA_ROOT resolution and configuration expansion (fixtures only)."""
import json

import pytest

from gitrx_backend.config import open_reader
from gitrx_backend.selftest import run
from gitrx_core import dataroot


def test_env_wins_and_unset_requires_error(monkeypatch, tmp_path):
    monkeypatch.setenv("GITRX_DATA_ROOT", str(tmp_path) + "/")
    assert dataroot.data_root() == str(tmp_path)
    assert dataroot.available()
    monkeypatch.delenv("GITRX_DATA_ROOT")
    monkeypatch.setattr(dataroot, "_local", lambda: {})
    assert dataroot.data_root() is None and not dataroot.available()
    with pytest.raises(RuntimeError, match="GITRX_DATA_ROOT"):
        dataroot.require()


def test_config_expands_data_root_placeholder(monkeypatch, tmp_path):
    parent = tmp_path / "parent"
    run(parent)
    cfg = json.loads((parent / "backend.json").read_text())
    for key in ("release_root", "export_root", "scope_path"):
        cfg[key] = "${GITRX_DATA_ROOT}/" + cfg[key]
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "c.json").write_text(json.dumps(cfg))
    monkeypatch.setenv("GITRX_DATA_ROOT", str(parent))
    with open_reader(elsewhere / "c.json", "sqlite") as reader:
        assert reader.snapshot == cfg["snapshot"]
