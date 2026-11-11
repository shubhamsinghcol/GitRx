"""Step F gap repair: measure missing versions and probe their recoverability (collection only).

Nothing here parses, classifies, rebuilds or promotes. The immutable evidence database and the
rebuild shards are opened read-only. New observations go to a separate append-only database
(updates/gap-repair/gap-repair.sqlite); response bodies are written by the maintained
gitrx_backend.jobs.HTTP (nlm.nih.gov only, ledgered, write-once, sha256-named).

    python -m gitrx_core.gap_repair measure            # no network
    python -m gitrx_core.gap_repair probe              # retries + controls + seeded sample, capped

"Missing" is defined against captured integers: a version is *implied missing* when it lies below the
family's earliest captured version (but >= 1) or between its earliest and latest captured versions.
An implied-missing integer is NOT evidence that DailyMed ever published it (labelers may skip numbers
or start numbering high). Versions above PLAUSIBLE_MAX are treated as source version-number anomalies
and are excluded from gap arithmetic (reported separately).
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import glob
import hashlib
import json
import math
import os
import random
import re
import sqlite3
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlencode

from . import update_common as uc

ROOT = uc.UPDATES + "/gap-repair"
EVIDENCE = uc.GITRX + "/evidence/evidence-v1.sqlite"
REBUILD = uc.GITRX + "/rebuild/spl-sections-0.2.3__srlc-changes-0.3.2"
GETFILE = "https://dailymed.nlm.nih.gov/dailymed/getFile.cfm"
PLAUSIBLE_MAX = 200
HARD_CAP = 1500
SYNTHETIC_PREFIX = "a0000000-0000-0000-0000-"      # test fixture family present in evidence-v1
MAX_CONSECUTIVE_FAILURES = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
  seed INTEGER, requests INTEGER DEFAULT 0, bytes INTEGER DEFAULT 0, error TEXT, params_json TEXT);
CREATE TABLE IF NOT EXISTS targets(id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, setid TEXT NOT NULL, version INTEGER NOT NULL,
  kind TEXT NOT NULL, stratum TEXT, draw_index INTEGER);
CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, setid TEXT NOT NULL, version INTEGER NOT NULL,
  kind TEXT NOT NULL, stratum TEXT, url TEXT, retrieved_at TEXT, status INTEGER, sha256 TEXT, bytes INTEGER,
  zip_ok INTEGER, identity_ok INTEGER, duplicate_of_known_sha INTEGER DEFAULT 0, outcome TEXT NOT NULL, note TEXT);
CREATE INDEX IF NOT EXISTS attempts_sv ON attempts(setid, version);
"""
APPEND_ONLY = ("targets", "attempts")
TRIGGERS = "".join(
    f"CREATE TRIGGER IF NOT EXISTS {t}_no_update BEFORE UPDATE ON {t} BEGIN SELECT RAISE(ABORT,'append-only'); END;"
    f"CREATE TRIGGER IF NOT EXISTS {t}_no_delete BEFORE DELETE ON {t} BEGIN SELECT RAISE(ABORT,'append-only'); END;"
    for t in APPEND_ONLY)

SETID_RE = re.compile(rb'<setId[^>]*root="([0-9a-fA-F-]{36})"')
VERSION_RE = re.compile(rb'<versionNumber[^>]*value="(\d+)"')


class Blocked(Exception):
    pass


class CapReached(Exception):
    pass


def wilson(k, n, z=1.959964):
    """Wilson score interval for k successes of n (95% by default). Returns (lo, hi); (0, 1) when n == 0."""
    if n <= 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - h) / d), min(1.0, (c + h) / d))


# ----------------------------------------------------------------------------- inventory / measurement

def load_inventory(rebuild=REBUILD, evidence=EVIDENCE):
    """Read-only. Returns dict: captured{setid:set(v)}, no_raw[(setid,v)], history{setid:set(v)},
    listing_only{(setid,v)}, pairs_total, pairs_nonconsec, pairs_gap_sum_plausible."""
    captured = collections.defaultdict(set)
    no_raw, pairs_total, nonconsec, gap_plausible, bogus_pairs = [], 0, 0, 0, 0
    for f in sorted(glob.glob(os.path.join(rebuild, "shards", "*.sqlite"))):
        c = sqlite3.connect(f"file:{f}?mode=ro", uri=True)
        try:
            for sid, v, st in c.execute("SELECT setid, version, status FROM versions"):
                captured[sid].add(int(v))
                if st == "no_raw":
                    no_raw.append((sid, int(v)))
            for fv, tv, cons, gap in c.execute("SELECT from_version, to_version, consecutive, gap_versions FROM pairs"):
                pairs_total += 1
                if not cons:
                    nonconsec += 1
                    if fv <= PLAUSIBLE_MAX and tv <= PLAUSIBLE_MAX:
                        gap_plausible += int(gap)
                    else:
                        bogus_pairs += 1
        finally:
            c.close()
    e = sqlite3.connect(f"file:{evidence}?mode=ro&immutable=1", uri=True)
    try:
        history = collections.defaultdict(set)
        for sid, v in e.execute("SELECT setid, version FROM history_entries"):
            try:
                history[sid].add(int(v))
            except (TypeError, ValueError):
                pass
        listing_only = {(s, int(v)) for s, v in e.execute("SELECT setid, version FROM release1_listing_only_targets")}
        known_shas = {r[0] for r in e.execute("SELECT selected_sha256 FROM versions WHERE selected_sha256 IS NOT NULL")}
    finally:
        e.close()
    return dict(captured=dict(captured), no_raw=sorted(no_raw), history=dict(history), listing_only=listing_only,
                known_shas=known_shas, pairs_total=pairs_total, pairs_nonconsec=nonconsec,
                pairs_gap_sum_plausible=gap_plausible, pairs_bogus=bogus_pairs)


def missing_versions(captured):
    """(below, internal): lists of (setid, version) implied missing among plausible captured versions."""
    below, internal = [], []
    for sid in sorted(captured):
        cv = {x for x in captured[sid] if x <= PLAUSIBLE_MAX}
        if not cv:
            continue
        mn, mx = min(cv), max(cv)
        below.extend((sid, x) for x in range(1, mn))
        internal.extend((sid, x) for x in range(mn, mx + 1) if x not in cv)
    return below, internal


def stratum_of(sid, version, captured):
    cv = {x for x in captured[sid] if x <= PLAUSIBLE_MAX}
    mn = min(cv)
    if version > mn:
        return "internal_gap"
    return "below_v1_start100" if mn == 100 else "below_v1_other"


def measure(inv):
    cap = inv["captured"]
    below, internal = missing_versions(cap)
    hist = inv["history"]
    fam_below = collections.Counter(s for s, _ in below)
    start100 = [s for s in fam_below if min(x for x in cap[s] if x <= PLAUSIBLE_MAX) == 100]
    return dict(
        families=len(cap),
        families_with_history=sum(1 for s in cap if s in hist),
        families_with_anomalous_versions=sum(1 for s in cap if any(x > PLAUSIBLE_MAX for x in cap[s])),
        no_raw_versions=len(inv["no_raw"]),
        no_raw_synthetic_fixture=sum(1 for s, _ in inv["no_raw"] if s.startswith(SYNTHETIC_PREFIX)),
        no_raw_families=len({s for s, _ in inv["no_raw"]}),
        no_raw_in_listing_only=sum(1 for t in inv["no_raw"] if t in inv["listing_only"]),
        no_raw_in_history=sum(1 for s, v in inv["no_raw"] if v in hist.get(s, ())),
        families_start_after_v1=len(fam_below),
        families_start_at_100=len(start100),
        implied_missing_below=len(below),
        implied_missing_below_start100=sum(fam_below[s] for s in start100),
        implied_missing_below_other=sum(n for s, n in fam_below.items() if s not in set(start100)),
        families_with_internal_gaps=len({s for s, _ in internal}),
        implied_missing_internal=len(internal),
        pairs_total=inv["pairs_total"], pairs_nonconsecutive=inv["pairs_nonconsec"],
        pairs_nonconsecutive_anomalous_numbers=inv["pairs_bogus"],
        pairs_gap_versions_plausible=inv["pairs_gap_sum_plausible"],
        implied_missing_in_history_inventory=sum(1 for s, v in below + internal if v in hist.get(s, ())),
    )


def draw_sample(inv, seed, per_stratum):
    """Seeded stratified sample of implied-missing versions: {stratum: [(setid, version), ...]}."""
    below, internal = missing_versions(inv["captured"])
    cap = inv["captured"]
    pools = collections.defaultdict(list)
    for s, v in below + internal:
        if s.startswith(SYNTHETIC_PREFIX):
            continue
        pools[stratum_of(s, v, cap)].append((s, v))
    out = {}
    for i, (name, n) in enumerate(sorted(per_stratum.items())):
        pool = sorted(pools[name])
        rng = random.Random(f"{seed}:{name}")
        out[name] = rng.sample(pool, min(n, len(pool)))
    return out, {k: len(v) for k, v in pools.items()}


def draw_controls(inv, seed, n):
    """Captured (setid, version) pairs with a held raw body, to prove the request form works."""
    rng = random.Random(f"{seed}:control")
    sids = sorted(s for s in inv["captured"] if not s.startswith(SYNTHETIC_PREFIX))
    out = []
    for sid in rng.sample(sids, min(n, len(sids))):
        v = rng.choice(sorted(x for x in inv["captured"][sid] if x <= PLAUSIBLE_MAX) or [1])
        out.append((sid, v))
    return out


# ----------------------------------------------------------------------------- probing

def open_db(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(root / "gap-repair.sqlite")
    c.row_factory = sqlite3.Row
    c.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;")
    c.executescript(SCHEMA + TRIGGERS)
    return c


def sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def inspect_zip(path):
    """(zip_ok, setid, version) from the first XML member. Label text is data; only two attributes are read."""
    try:
        with zipfile.ZipFile(path) as z:
            xmls = [n for n in z.namelist() if n.lower().endswith(".xml")]
            if not xmls:
                return False, None, None
            with z.open(xmls[0]) as f:
                head = f.read(2 * 1024 * 1024)
    except (zipfile.BadZipFile, OSError):
        return False, None, None
    s, v = SETID_RE.search(head), VERSION_RE.search(head)
    return True, (s.group(1).decode().lower() if s else None), (int(v.group(1)) if v else None)


def _blocked(exc):
    m = str(exc)
    return "HTTP 403" in m or "HTTP 429" in m or "Retry deferred" in m


def probe(*, root=ROOT, targets, seed, known_shas=frozenset(), http_factory=None, max_requests=HARD_CAP,
          guard=None, log=print, stop_check=None, spacing=2, prior_requests=0):
    """targets: list of (setid, version, kind, stratum). One GET each, in order. Stops on 403/429/cap/STOP."""
    root = Path(root)
    db = open_db(root)
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with db:
        db.execute("INSERT INTO runs(run_id,started_at,status,seed,params_json) VALUES(?,?,?,?,?)",
                   (run_id, uc.utcnow(), "running", seed,
                    json.dumps({"max_requests": max_requests, "prior_requests": prior_requests, "n_targets": len(targets)})))
        for i, (s, v, k, st) in enumerate(targets):
            db.execute("INSERT INTO targets(run_id,setid,version,kind,stratum,draw_index) VALUES(?,?,?,?,?,?)", (run_id, s, v, k, st, i))
    if http_factory is None:
        from gitrx_backend.jobs import HTTP

        def http_factory(r, cap, g):
            return HTTP(r, cap=cap, spacing=spacing, guard=g, retain_completed_partials=False, max_bytes=2 * 1024**3)
    http = http_factory(root / "network", max_requests, guard)
    start = getattr(http, "request_count", 0)
    if hasattr(http, "cap"):
        http.cap = start + max(0, max_requests - prior_requests)
    status, error, consecutive, done = "complete", None, 0, 0
    try:
        for s, v, kind, stratum in targets:
            if stop_check and stop_check():
                raise KeyboardInterrupt("stop requested")
            url = f"{GETFILE}?{urlencode({'type': 'zip', 'setid': s, 'version': v})}"
            at = uc.utcnow()
            try:
                p = Path(http.get(url))
                consecutive = 0
            except Exception as exc:
                if _blocked(exc):
                    raise Blocked(str(exc))
                if "request cap reached" in str(exc):
                    raise CapReached(str(exc))
                code = int(str(exc).split()[1].rstrip(":")) if str(exc).startswith("HTTP ") else None
                # a 404 is a definite, expected answer for a missing version; only other failures count toward a stop
                consecutive = 0 if code == 404 else consecutive + 1
                if consecutive >= MAX_CONSECUTIVE_FAILURES:
                    raise Blocked(f"{MAX_CONSECUTIVE_FAILURES} consecutive failures: {exc}")
                outcome = "not_found" if code == 404 else "error"
                with db:
                    db.execute("INSERT INTO attempts(run_id,setid,version,kind,stratum,url,retrieved_at,status,outcome,note) VALUES(?,?,?,?,?,?,?,?,?,?)",
                               (run_id, s, v, kind, stratum, url, at, code, outcome, str(exc)[:200]))
                done += 1
                continue
            digest = sha_file(p)
            if digest != p.name:
                raise RuntimeError(f"stored body hash mismatch {p.name} != {digest}")
            ok, rs, rv = inspect_zip(p)
            ident = int(ok and rs == s.lower() and rv == v)
            dup = int(digest in known_shas)
            outcome = ("recovered_new" if ident and not dup else "recovered_duplicate_of_held" if ident
                       else "invalid_body")
            with db:
                db.execute("INSERT INTO attempts(run_id,setid,version,kind,stratum,url,retrieved_at,status,sha256,bytes,zip_ok,identity_ok,duplicate_of_known_sha,outcome,note) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                           (run_id, s, v, kind, stratum, url, at, 200, digest, p.stat().st_size, int(ok), ident, dup, outcome,
                            None if ident else f"xml identity {rs}/{rv}" if ok else "not a zip with an xml member"))
            done += 1
    except Blocked as exc:
        status, error = "blocked", str(exc)[:300]
        uc.write_backoff(Path(root) / "BACKOFF-UNTIL")
        log(f"STOP: blocked/throttled ({error}); backoff written")
    except CapReached as exc:
        status, error = "partial", "max_requests reached"
    except KeyboardInterrupt as exc:
        status, error = "interrupted", str(exc)
    except Exception as exc:
        status, error = "error", f"{type(exc).__name__}: {exc}"[:300]
        log(f"ERROR: {error}")
    n_req = getattr(http, "request_count", 0) - start
    with db:
        db.execute("UPDATE runs SET finished_at=?,status=?,requests=?,bytes=?,error=? WHERE run_id=?",
                   (uc.utcnow(), status, n_req, getattr(http, "bytes_received", 0), error, run_id))
    row = dict(db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone())
    row["attempted"] = done
    db.close()
    log("run " + json.dumps({k: row[k] for k in ("run_id", "status", "requests", "bytes", "attempted", "error")}))
    return row


def summarize(db_path):
    """Per (kind, stratum): n, recovered (identity-verified), not_found, other, Wilson CI of recovery."""
    c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    rows = c.execute("SELECT kind, COALESCE(stratum,''), outcome, COUNT(*) FROM attempts GROUP BY 1,2,3").fetchall()
    c.close()
    agg = collections.defaultdict(lambda: collections.Counter())
    for k, st, o, n in rows:
        agg[(k, st)][o] += n
    out = {}
    for key, cnt in sorted(agg.items()):
        n = sum(cnt.values())
        rec = cnt["recovered_new"] + cnt["recovered_duplicate_of_held"]
        lo, hi = wilson(rec, n)
        out["/".join(x for x in key if x)] = dict(n=n, recovered=rec, outcomes=dict(cnt), rate=rec / n if n else None,
                                                  wilson95=[round(lo, 4), round(hi, 4)])
    return out


# ----------------------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["measure", "probe", "summary"])
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--seed", type=int, default=20261111)
    ap.add_argument("--sample", default="below_v1_other=70,internal_gap=70,below_v1_start100=60")
    ap.add_argument("--controls", type=int, default=20)
    ap.add_argument("--max-requests", type=int, default=HARD_CAP)
    ap.add_argument("--prior-requests", type=int, default=0, help="requests already spent today against the cap")
    a = ap.parse_args(argv)
    inv = load_inventory()
    if a.command == "measure":
        print(json.dumps(measure(inv), indent=1, sort_keys=True))
        return 0
    if a.command == "summary":
        print(json.dumps(summarize(Path(a.root) / "gap-repair.sqlite"), indent=1))
        return 0
    try:
        os.nice(10)
    except OSError:
        pass
    per = {k: int(v) for k, v in (x.split("=") for x in a.sample.split(","))}
    sample, pools = draw_sample(inv, a.seed, per)
    targets = [(s, v, "retry_unavailable", "no_raw") for s, v in inv["no_raw"] if not s.startswith(SYNTHETIC_PREFIX)]
    targets += [(s, v, "control_held", "held") for s, v in draw_controls(inv, a.seed, a.controls)]
    for name in sorted(sample):
        targets += [(s, v, "missing_sample", name) for s, v in sample[name]]
    root = Path(a.root)
    done_before = set()
    if (root / "gap-repair.sqlite").exists():     # resumable: never re-request an already attempted (setid, version, kind)
        d = sqlite3.connect(f"file:{root / 'gap-repair.sqlite'}?mode=ro", uri=True)
        done_before = {(s, v, k) for s, v, k in d.execute("SELECT setid, version, kind FROM attempts")}
        d.close()
    targets = [t for t in targets if (t[0], t[1], t[2]) not in done_before]
    try:
        uc.preflight(heavy_lock=uc.HEAVY_LOCK, backoff_file=root / "BACKOFF-UNTIL")
        with uc.JobLock(root / "job.lock"):
            log = uc.Logger(root / "logs" / f"run-{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}.log")
            log(f"pools={pools} targets={len(targets)} seed={a.seed}")
            row = probe(root=root, targets=targets, seed=a.seed, known_shas=inv["known_shas"], max_requests=a.max_requests,
                        prior_requests=a.prior_requests, guard=uc.require_volume, log=log,
                        stop_check=lambda: (Path(uc.UPDATES) / uc.STOP_NAME).exists() or uc.heavy_lock_held() is not None)
    except uc.Skip as s:
        print(uc.utcnow(), str(s), flush=True)
        return 0
    return 0 if row["status"] in ("complete", "partial", "blocked", "interrupted") else 1


if __name__ == "__main__":
    sys.exit(main())
