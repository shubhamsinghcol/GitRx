"""Shared safety for the recurring update jobs (DailyMed weekly, SrLC monthly).

Checks, in order, before any write: SSD present and identity-verified (same
marker/token mechanism as the backfill), STOP file, heavy-job lock, backoff
file; then a per-job flock. Every refusal is a *skip* (caller exits 0).
Reads/collection here never parse, classify or promote anything.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import subprocess
import tempfile
from pathlib import Path

from . import dataroot

GITRX = dataroot.DATA_ROOT                   # GITRX_DATA_ROOT or runtime/local.json (see dataroot.py)
VOLUME = str(Path(GITRX).parent)             # the mount that holds the data root
MARKER = Path(GITRX).name + "/.volume-identity"
TOKEN = dataroot.volume_token()              # None: the identity-token check is skipped
UPDATES = GITRX + "/updates"
HEAVY_LOCK = GITRX + "/HEAVY-JOB.lock"
STOP_NAME = "STOP"
HEAVY_PROCESS_MARKERS = ("gitrx_core.rebuild", "gitrx_core/rebuild.py")
BACKOFF_SECONDS = 24 * 3600


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Skip(Exception):
    """Job must not run now; exit 0 with the message logged."""


def volume_ok(volume=VOLUME, marker=MARKER, token=TOKEN) -> bool:
    v = Path(volume)
    try:
        if token is None:       # no identity token configured: the data root merely has to exist
            return v.is_dir()
        return (v.is_mount() and os.stat(v).st_dev != os.stat("/").st_dev
                and (v / marker).read_text().strip() == token)
    except OSError:
        return False


def require_volume():
    """HTTP/write guard: abort (raise) if the SSD vanished or is the wrong volume mid-run."""
    if not volume_ok():
        raise RuntimeError("External volume unavailable")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def heavy_process_running(markers=HEAVY_PROCESS_MARKERS) -> bool:
    try:
        out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        return False
    me = os.getpid()
    for line in out.splitlines():
        pid_s, _, cmd = line.strip().partition(" ")
        if pid_s.isdigit() and int(pid_s) != me and any(m in cmd for m in markers):
            return True
    return False


def heavy_lock_held(path=HEAVY_LOCK) -> str | None:
    """Return a reason if a heavy job holds the lock file.

    Convention: a heavy job creates HEAVY-JOB.lock (first line = its PID, optional
    second line = description) and removes it when done. A lock whose PID is dead
    is stale and ignored (logged); a lock without a parseable PID is honored.
    """
    p = Path(path)
    if not p.exists():
        return None
    try:
        first = p.read_text().split("\n", 1)[0].strip()
    except OSError:
        return "heavy-job lock unreadable"
    if first.isdigit():
        return f"heavy job lock held by pid {first}" if _pid_alive(int(first)) else None
    return "heavy job lock present (no pid)"


def backoff_active(path: Path, now=None) -> str | None:
    if not path.exists():
        return None
    try:
        until = dt.datetime.fromisoformat(path.read_text().strip().replace("Z", "+00:00"))
    except Exception:
        return "backoff file unreadable"
    now = now or dt.datetime.now(dt.timezone.utc)
    return f"backoff until {until.isoformat()}" if now < until else None


def write_backoff(path: Path, seconds=BACKOFF_SECONDS):
    until = dt.datetime.now(dt.timezone.utc) + dt.timedelta(seconds=seconds)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(until.strftime("%Y-%m-%dT%H:%M:%SZ") + "\n")


def preflight(*, volume=VOLUME, marker=MARKER, token=TOKEN, gitrx=GITRX, updates=UPDATES,
              heavy_lock=HEAVY_LOCK, check_processes=True, backoff_file: Path | None = None):
    """Raise Skip with the logged message if the job must not run."""
    if not Path(gitrx).is_dir() or not volume_ok(volume, marker, token):
        raise Skip("skipped: SSD absent")
    if (Path(updates) / STOP_NAME).exists():
        raise Skip("skipped: STOP file present")
    why = heavy_lock_held(heavy_lock)
    if why:
        raise Skip(f"skipped: {why}")
    if check_processes and heavy_process_running():
        raise Skip("skipped: heavy rebuild process running")
    if backoff_file is not None:
        why = backoff_active(backoff_file)
        if why:
            raise Skip(f"skipped: {why}")


class JobLock:
    """Single-instance flock per job."""

    def __init__(self, path):
        self.path = Path(path)
        self.f = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = self.path.open("a+")
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.f.close()
            raise Skip("skipped: another instance holds the job lock")
        self.f.seek(0)
        self.f.truncate()
        self.f.write(f"{os.getpid()}\n")
        self.f.flush()
        return self

    def __exit__(self, *a):
        if self.f:
            fcntl.flock(self.f, fcntl.LOCK_UN)
            self.f.close()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "w") as f:
        json.dump(value, f, indent=1, sort_keys=True)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class Logger:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, msg):
        line = f"{utcnow()} {msg}"
        print(line, flush=True)
        if self.path:
            with self.path.open("a") as f:
                f.write(line + "\n")
