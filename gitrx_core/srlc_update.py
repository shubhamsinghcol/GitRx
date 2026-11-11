"""Monthly SrLC update with a sealed prospective holdout.

Re-enumerates the last N months of the public FDA SrLC database (single lane, >=1 s
between requests, per-run request cap, stop on any 403/429), fetches detail pages and
per-drug CSVs only for DrugNameIDs that are new or whose (supplement date, Database
Updated) pairs differ from what is already stored, and builds a NEW dated gradebook
snapshot from the update ledger via gitrx_core.srlc.build_gradebook. The frozen
gradebook.sqlite and original ledger.sqlite are only read (immutable, read-only).

HOLDOUT SEALING: entries whose approval/supplement date OR Database Updated date is
after HOLDOUT_AFTER (2026-11-11) are the prospective holdout. They are never graded,
printed or summarized: logs carry counts and fingerprints only; the snapshot omits
their supplements/changes; a per-run manifest lists IDs and fingerprints (no text).

    python -m gitrx_core.srlc_update [--window FROM:TO] [--months 6] [--run-cap 500]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

from . import srlc, update_common as uc

JOB = "srlc"
ROOT = uc.GITRX + "/srlc/updates"
OLD_GRADEBOOK = uc.GITRX + "/srlc/gradebook.sqlite"
HOLDOUT_AFTER = "2026-11-11"
DEFAULT_MONTHS = 6
DEFAULT_RUN_CAP = 500
DEFAULT_INTERVAL = 2.0
MIN_INTERVAL_FLOOR = 1.0

STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
  windows_json TEXT, enum_rows INTEGER, drugs_seen INTEGER, drugs_changed INTEGER, details_fetched INTEGER, csvs_fetched INTEGER,
  requests INTEGER, holdout_entries INTEGER, holdout_fingerprint TEXT, gradebook TEXT, error TEXT, params_json TEXT);
CREATE TABLE IF NOT EXISTS pair_seen(drug_name_id INTEGER, suppl_date_iso TEXT, database_updated_iso TEXT, run_id TEXT,
  PRIMARY KEY(drug_name_id, suppl_date_iso, database_updated_iso));
CREATE TABLE IF NOT EXISTS drug_fetch(drug_name_id INTEGER, run_id TEXT, detail_sha256 TEXT, csv_sha256 TEXT,
  PRIMARY KEY(drug_name_id, run_id));
"""


def open_state(root):
    Path(root).mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(Path(root) / "state.sqlite")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(STATE_SCHEMA)
    return c


def fp(*parts):
    return hashlib.sha256("|".join("" if p is None else str(p) for p in parts).encode()).hexdigest()


def ids_fingerprint(ids):
    return hashlib.sha256("\n".join(str(i) for i in sorted(set(ids))).encode()).hexdigest()


def is_holdout(suppl_iso, dbu_iso, after=HOLDOUT_AFTER):
    return bool((suppl_iso and suppl_iso > after) or (dbu_iso and dbu_iso > after))


class UpdateCrawler(srlc.Crawler):
    """Crawler with a per-run request cap, immediate stop on 403/429 and a volume guard."""

    def __init__(self, root, *, run_cap, inner_fetch=srlc.real_fetch, guard=None, **kw):
        kw.setdefault("hard_cap", 10**9)
        self.run_cap = run_cap
        self.blocked = None
        self.guard = guard or (lambda: None)
        self._inner = inner_fetch
        super().__init__(root, fetch=self._wrapped, **kw)

    def _wrapped(self, method, url, data, headers):
        status, hdrs, body = self._inner(method, url, data, headers, self.cj) if self._inner is srlc.real_fetch \
            else self._inner(method, url, data, headers)
        if status in (403, 429):
            self.blocked = status
        return status, hdrs, body

    def stop(self, reason):          # no STOP.txt side effect: a cap or block is a normal outcome here
        if self.stopped is None:
            self.stopped = reason
            self.log(f"STOP: {reason}")
        raise srlc.StopCrawl(self.stopped)

    def _throttle(self, extra=0.0):
        self.guard()
        if self.blocked:
            self.stop(f"HTTP {self.blocked} received")
        if self.session_requests >= self.run_cap:
            self.stop(f"per-run request cap {self.run_cap} reached")
        super()._throttle(extra)

    def fetch_key(self, key, kind, method, url, params=None):
        sha = self.get(key, kind, method, url, params)
        if self.blocked:
            self.stop(f"HTTP {self.blocked} received")
        return sha


def windows_for(today, months):
    y, m = today.year, today.month - (months - 1)
    while m < 1:
        y, m = y - 1, m + 12
    return srlc.month_windows((y, m), (today.year, today.month))


def enumerate_windows(c, run_id, windows, log):
    hits, seen = [], set()

    def one(a, b, split=True):
        sha = c.fetch_key(f"enum:{run_id}:{a.isoformat()}:{b.isoformat()}", "enum", "POST",
                          srlc.BASE + "?event=searchResult.page", srlc.enum_params(a, b))
        if not sha:
            return
        rows = srlc.parse_results(srlc.read_raw(c.root, sha).decode("utf-8", "replace"))
        for r in rows:
            k = (r["drug_name_id"], r["suppl_date_iso"], r["database_updated_iso"])
            if k not in seen:
                seen.add(k)
                hits.append(r)
        if split and len(rows) >= srlc.SPLIT_ROW_THRESHOLD and a < b:
            mid = a + (b - a) // 2
            log(f"split window {a}..{b}")
            one(a, mid)
            one(mid + dt.timedelta(days=1), b)

    for a, b in windows:
        one(a, b)
    return hits


def known_pairs(old_gradebook, state):
    pairs, drugs = set(), set()
    if old_gradebook and Path(old_gradebook).exists():
        g = sqlite3.connect(f"file:{old_gradebook}?mode=ro&immutable=1", uri=True)
        try:
            for did, s, d in g.execute("SELECT drug_name_id, suppl_date_iso, database_updated_iso FROM search_hits"):
                pairs.add((did, s, d))
            drugs.update(r[0] for r in g.execute("SELECT drug_name_id FROM drugs"))
        finally:
            g.close()
    for did, s, d in state.execute("SELECT drug_name_id, suppl_date_iso, database_updated_iso FROM pair_seen"):
        pairs.add((did, s, d))
        drugs.add(did)
    return pairs, drugs


def write_manifest(root, run_id, hits, extra_supps=None, after=HOLDOUT_AFTER):
    entries = []
    for r in hits:
        by_a = bool(r["suppl_date_iso"] and r["suppl_date_iso"] > after)
        by_d = bool(r["database_updated_iso"] and r["database_updated_iso"] > after)
        if by_a or by_d:
            entries.append({"drug_name_id": r["drug_name_id"], "approval_date": r["suppl_date_iso"],
                            "database_updated": r["database_updated_iso"], "by_approval_date": by_a,
                            "by_database_updated": by_d,
                            "fingerprint": fp(r["drug_name_id"], r["suppl_date_iso"], r["database_updated_iso"])})
    entries.sort(key=lambda e: (e["drug_name_id"], e["approval_date"] or "", e["database_updated"] or ""))
    doc = {"run_id": run_id, "generated_at": uc.utcnow(), "holdout_after": after,
           "criteria": "approval/supplement date > cutoff OR Database Updated date > cutoff (both recorded)",
           "content": "ids, dates and fingerprints only; no label text",
           "n_entries": len(entries), "n_drug_ids": len({e["drug_name_id"] for e in entries}),
           "drug_ids_fingerprint": ids_fingerprint(e["drug_name_id"] for e in entries),
           "entries_fingerprint": ids_fingerprint(e["fingerprint"] for e in entries),
           "entries": entries, "supplements": extra_supps or []}
    out = Path(root) / "holdout" / f"manifest-{run_id}.json"
    uc.atomic_json(out, doc)
    return out, doc


def seal_snapshot(pre, final_dir, run_id, base_name, after=HOLDOUT_AFTER, parent_sha=None):
    """Remove holdout supplements/changes (and per-drug CSV/reconcile for drugs holding any) from the
    freshly built snapshot, add provenance, and move it to a never-overwritten dated name.
    Returns (final_path, sealed_supplement_fingerprints)."""
    g = sqlite3.connect(pre)
    sealed = g.execute("SELECT supplement_id, drug_name_id, approval_date_iso, suppl_ref, database_updated FROM supplements "
                       "WHERE approval_date_iso > ? OR database_updated > ?", (after, after)).fetchall()
    sids = [s[0] for s in sealed]
    dids = sorted({s[1] for s in sealed})
    marks = lambda n: ",".join("?" * n)
    removed = {"supplements": len(sids), "changes": 0, "csv_rows": 0, "reconcile": 0, "search_hits": 0}
    for i in range(0, len(sids), 500):
        chunk = sids[i:i + 500]
        removed["changes"] += g.execute(f"DELETE FROM changes WHERE supplement_id IN ({marks(len(chunk))})", chunk).rowcount
        g.execute(f"DELETE FROM supplements WHERE supplement_id IN ({marks(len(chunk))})", chunk)
    for i in range(0, len(dids), 500):
        chunk = dids[i:i + 500]
        removed["csv_rows"] += g.execute(f"DELETE FROM csv_rows WHERE drug_name_id IN ({marks(len(chunk))})", chunk).rowcount
        removed["reconcile"] += g.execute(f"DELETE FROM reconcile WHERE drug_name_id IN ({marks(len(chunk))})", chunk).rowcount
    removed["search_hits"] = g.execute("DELETE FROM search_hits WHERE suppl_date_iso > ? OR database_updated_iso > ?",
                                       (after, after)).rowcount
    g.execute("CREATE TABLE provenance(k TEXT PRIMARY KEY, v TEXT)")
    prov = {"kind": "gitrx srlc update snapshot (delta, update ledger only)", "run_id": run_id, "built_at": uc.utcnow(),
            "holdout_after": after, "holdout_rule": "supplement date > cutoff OR database_updated > cutoff removed",
            "holdout_removed_counts": json.dumps(removed), "contains_holdout_text": "0",
            "drugs_with_csv_and_reconcile_dropped_for_holdout": str(len(dids)),
            "parent_frozen_gradebook_sha256": parent_sha or "", "source_ledger": "srlc/updates/ledger.sqlite",
            "schema": "same as gitrx_core.srlc.GRADE_SCHEMA plus provenance"}
    g.executemany("INSERT INTO provenance VALUES(?,?)", prov.items())
    g.commit()
    g.close()
    final = Path(final_dir) / f"{base_name}.sqlite"
    if final.exists():
        final = Path(final_dir) / f"{base_name}T{dt.datetime.now(dt.timezone.utc):%H%M%S}.sqlite"
    os.replace(pre, final)
    supps = [{"drug_name_id": s[1], "approval_date": s[2], "suppl_ref": s[3], "database_updated": s[4],
              "fingerprint": fp(s[1], s[2], s[3])} for s in sealed]
    return final, supps


def run(*, root=ROOT, old_gradebook=OLD_GRADEBOOK, fetch=srlc.real_fetch, sleep=time.sleep, clock=time.monotonic,
        today=None, months=DEFAULT_MONTHS, window=None, run_cap=DEFAULT_RUN_CAP, min_interval=DEFAULT_INTERVAL,
        guard=None, log=print, stop_check=None, parent_sha=None, build=True):
    root = Path(root)
    today = today or dt.date.today()
    now = dt.datetime.now(dt.timezone.utc)
    run_id = now.strftime("%Y%m%dT%H%M%S%fZ")
    windows = [window] if window else windows_for(today, months)
    min_interval = max(MIN_INTERVAL_FLOOR, min_interval)
    state = open_state(root)
    with state:
        state.execute("INSERT INTO runs(run_id,started_at,status,windows_json,params_json) VALUES(?,?,?,?,?)",
                      (run_id, uc.utcnow(), "running", json.dumps([[a.isoformat(), b.isoformat()] for a, b in windows]),
                       json.dumps({"run_cap": run_cap, "min_interval": min_interval, "holdout_after": HOLDOUT_AFTER})))
    c = UpdateCrawler(str(root), run_cap=run_cap, inner_fetch=fetch, guard=guard, sleep=sleep, clock=clock,
                      log=log, min_interval=min_interval)
    stats = dict(enum_rows=0, drugs_seen=0, drugs_changed=0, details=0, csvs=0)
    status, error, gradebook = "complete", None, None
    hits, hold_doc = [], None
    try:
        hits = enumerate_windows(c, run_id, windows, log)
        stats["enum_rows"] = len(hits)
        # manifest first, from enumeration rows only (ids/dates/fingerprints)
        _, hold_doc = write_manifest(root, run_id, hits)
        pairs, drugs = known_pairs(old_gradebook, state)
        by_drug = {}
        for r in hits:
            by_drug.setdefault(r["drug_name_id"], []).append(r)
        stats["drugs_seen"] = len(by_drug)
        changed = sorted(d for d, rs in by_drug.items()
                         if d not in drugs or any((d, r["suppl_date_iso"], r["database_updated_iso"]) not in pairs for r in rs))
        stats["drugs_changed"] = len(changed)
        log(f"enumerated rows={len(hits)} drugs={len(by_drug)} changed_or_new={len(changed)}")
        pending_csv = [r[0] for r in state.execute("SELECT drug_name_id FROM drug_fetch WHERE csv_sha256 IS NULL AND detail_sha256 IS NOT NULL")]
        for did in changed:
            if stop_check and stop_check():
                raise KeyboardInterrupt("stop requested")
            sha = c.fetch_key(f"detail:{did}:{run_id}", "detail", "GET", srlc.detail_url(did))
            if not sha:
                continue
            stats["details"] += 1
            with state:
                state.execute("INSERT OR REPLACE INTO drug_fetch(drug_name_id,run_id,detail_sha256) VALUES(?,?,?)", (did, run_id, sha))
                for r in by_drug[did]:
                    state.execute("INSERT OR IGNORE INTO pair_seen VALUES(?,?,?,?)",
                                  (did, r["suppl_date_iso"], r["database_updated_iso"], run_id))
        for did in sorted(set(changed) | set(pending_csv)):
            if stop_check and stop_check():
                raise KeyboardInterrupt("stop requested")
            row = state.execute("SELECT run_id FROM drug_fetch WHERE drug_name_id=? AND detail_sha256 IS NOT NULL AND csv_sha256 IS NULL "
                                "ORDER BY run_id DESC LIMIT 1", (did,)).fetchone()
            if not row:
                continue
            sha = c.fetch_key(f"csv:{did}:{run_id}", "csv", "GET", srlc.csv_url(did))
            if sha:
                stats["csvs"] += 1
                with state:
                    state.execute("UPDATE drug_fetch SET csv_sha256=? WHERE drug_name_id=? AND run_id=?", (sha, did, row[0]))
    except srlc.StopCrawl as exc:
        reason = str(exc)
        status = "blocked" if "HTTP 4" in reason else ("partial" if "cap" in reason else "stopped")
        error = reason
        if status == "blocked":
            uc.write_backoff(root / "BACKOFF-UNTIL")
    except KeyboardInterrupt as exc:
        status, error = "interrupted", str(exc)
    except Exception as exc:
        status, error = "error", f"{type(exc).__name__}: {exc}"[:300]
        log(f"ERROR: {error}")
    # snapshot (offline; parses stored raw only) + final manifest with supplement fingerprints
    if build and status in ("complete", "partial", "stopped", "interrupted") and stats["details"]:
        try:
            pre = root / "tmp" / f"pre-{run_id}.sqlite"
            pre.parent.mkdir(parents=True, exist_ok=True)
            srlc.build_gradebook(str(root), str(pre))
            final, supps = seal_snapshot(str(pre), root, run_id, f"gradebook-{today:%Y%m%d}", parent_sha=parent_sha)
            gradebook = str(final)
            _, hold_doc = write_manifest(root, run_id, hits, supps)
        except Exception as exc:
            status, error = "error", f"snapshot: {type(exc).__name__}: {exc}"[:300]
            log(f"ERROR: {error}")
    n_hold = hold_doc["n_entries"] if hold_doc else 0
    fprint = hold_doc["drug_ids_fingerprint"] if hold_doc else None
    with state:
        state.execute("UPDATE runs SET finished_at=?,status=?,enum_rows=?,drugs_seen=?,drugs_changed=?,details_fetched=?,"
                      "csvs_fetched=?,requests=?,holdout_entries=?,holdout_fingerprint=?,gradebook=?,error=? WHERE run_id=?",
                      (uc.utcnow(), status, stats["enum_rows"], stats["drugs_seen"], stats["drugs_changed"], stats["details"],
                       stats["csvs"], c.session_requests, n_hold, fprint, gradebook, error, run_id))
    row = dict(state.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone())
    state.close()
    log("run " + json.dumps({k: row[k] for k in ("run_id", "status", "enum_rows", "drugs_seen", "drugs_changed",
                                                 "details_fetched", "csvs_fetched", "requests", "holdout_entries",
                                                 "holdout_fingerprint", "gradebook", "error")}))
    return row


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--months", type=int, default=DEFAULT_MONTHS)
    ap.add_argument("--window", help="FROM:TO (YYYY-MM-DD:YYYY-MM-DD) single window (smoke)")
    ap.add_argument("--run-cap", type=int, default=DEFAULT_RUN_CAP)
    ap.add_argument("--min-interval", type=float, default=DEFAULT_INTERVAL)
    ap.add_argument("--old-gradebook", default=OLD_GRADEBOOK, help="read-only baseline of already-held (drug, dates)")
    ap.add_argument("--ignore-heavy", action="store_true", help="smoke/testing only")
    a = ap.parse_args(argv)
    try:
        os.nice(10)
    except OSError:
        pass
    window = None
    if a.window:
        f, t = a.window.split(":")
        window = (dt.date.fromisoformat(f), dt.date.fromisoformat(t))
    root = Path(a.root)
    try:
        uc.preflight(check_processes=not a.ignore_heavy, heavy_lock=uc.HEAVY_LOCK if not a.ignore_heavy else "/nonexistent",
                     backoff_file=root / "BACKOFF-UNTIL")
        with uc.JobLock(root / "job.lock"):
            log = uc.Logger(root / "logs" / f"run-{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}.log")
            row = run(root=root, old_gradebook=a.old_gradebook, months=a.months, window=window, run_cap=a.run_cap, min_interval=a.min_interval,
                      guard=uc.require_volume, log=log,
                      parent_sha="863aea6712ed024fb49456d4a155337bb503d12cad4bf347c3ed46e04b896db4",
                      stop_check=lambda: (Path(uc.UPDATES) / uc.STOP_NAME).exists() or
                      (not a.ignore_heavy and uc.heavy_lock_held() is not None))
    except uc.Skip as s:
        print(uc.utcnow(), str(s), flush=True)
        return 0
    return 0 if row["status"] in ("complete", "partial", "blocked", "interrupted", "stopped") else 1


if __name__ == "__main__":
    sys.exit(main())
