"""Locate the external GitRx data root (raw evidence, evidence database, rebuilds, SrLC records).

The data root is deliberately outside the source checkout. Resolution order:

1. the ``GITRX_DATA_ROOT`` environment variable;
2. ``data_root`` in ``runtime/local.json`` of the checkout (a local, git-ignored file);
3. unset: ``DATA_ROOT`` is a clearly invalid placeholder path and ``require()`` raises.

``runtime/local.json`` may also hold ``volume_token`` (or set ``GITRX_VOLUME_TOKEN``): when
present, the recurring update jobs additionally require ``<data_root>/.volume-identity`` to
contain that token, so a different disk mounted at the same path is refused.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

ENV_ROOT = "GITRX_DATA_ROOT"
ENV_TOKEN = "GITRX_VOLUME_TOKEN"
UNSET_PLACEHOLDER = "/nonexistent/GITRX_DATA_ROOT-not-set"


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _local() -> dict:
    try:
        return json.loads((repo_root() / "runtime" / "local.json").read_text())
    except (OSError, ValueError):
        return {}


def data_root() -> str | None:
    value = os.environ.get(ENV_ROOT) or _local().get("data_root")
    return str(value).rstrip("/") if value else None


def volume_token() -> str | None:
    return os.environ.get(ENV_TOKEN) or _local().get("volume_token") or None


def require() -> str:
    root = data_root()
    if not root:
        raise RuntimeError(
            f"{ENV_ROOT} is not set (and runtime/local.json has no data_root): this operation needs the "
            "external GitRx data root; see README.md (Configuration).")
    return root


DATA_ROOT = data_root() or UNSET_PLACEHOLDER


def data_path(*parts: str) -> str:
    return "/".join([DATA_ROOT, *parts])


def available() -> bool:
    """True when a data root is configured and exists (used by tests to skip cleanly)."""
    root = data_root()
    return bool(root) and Path(root).is_dir()
