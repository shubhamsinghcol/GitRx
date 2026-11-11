"""Weekly DailyMed update: discover recently published SPLs, refresh history, fetch new versions.

Collection only. Nothing is parsed, classified or promoted; the immutable evidence
database is opened read-only (immutable=1) solely to learn which (setid, version) and
sha256 values are already held. New observations go to a separate append-only delta
database (updates/dailymed/updates.sqlite). HTTP reuses the maintained ledgered,
write-once, hash-named, nlm.nih.gov-restricted HTTP class from gitrx_backend.jobs.

Discovery: the official v2 listing (spls.json) is served newest-published first and its
published_date filters are ignored by the server, so pages are walked until two
consecutive pages contain only publications older than the cutoff
(cutoff = watermark - 7 days).

    python -m gitrx_core.dailymed_update [--smoke] [--since-days N] [--max-downloads N]
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
import zipfile
from pathlib import Path
from urllib.parse import urlencode

from . import update_common as uc

JOB = "dailymed"
ROOT = uc.UPDATES + "/dailymed"
EVIDENCE = uc.GITRX + "/evidence/evidence-v1.sqlite"
LISTING = "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json"
HISTORY = "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/{setid}/history.json"
GETFILE = "https://dailymed.nlm.nih.gov/dailymed/getFile.cfm"
OVERLAP_DAYS = 7
PAGESIZE = 100
DEFAULT_MAX_DOWNLOADS = 3000
DEFAULT_MAX_REQUESTS = 6000
MAX_LISTING_PAGES = 400
MAX_CONSECUTIVE_FAILURES = 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
  status TEXT NOT NULL, mode TEXT, watermark_in TEXT, cutoff TEXT, watermark_out TEXT,
  listing_pages INTEGER DEFAULT 0, listing_rows INTEGER DEFAULT 0, candidate_setids INTEGER DEFAULT 0,
  changed_setids INTEGER DEFAULT 0, histories_fetched INTEGER DEFAULT 0, new_versions INTEGER DEFAULT 0,
  downloads INTEGER DEFAULT 0, duplicate_sha INTEGER DEFAULT 0, unavailable INTEGER DEFAULT 0,
  requests INTEGER DEFAULT 0, bytes INTEGER DEFAULT 0, error TEXT, params_json TEXT);
CREATE TABLE IF NOT EXISTS listing_observations(id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, setid TEXT NOT NULL,
  listed_version INTEGER, published_raw TEXT, published_date TEXT, title TEXT, page INTEGER, page_sha256 TEXT);
CREATE TABLE IF NOT EXISTS history_entries(id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, setid TEXT NOT NULL,
  version INTEGER NOT NULL, published_raw TEXT, published_date TEXT, history_sha256 TEXT);
CREATE TABLE IF NOT EXISTS version_files(id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, setid TEXT NOT NULL,
  version INTEGER NOT NULL, url TEXT, retrieved_at TEXT, status INTEGER, sha256 TEXT, bytes INTEGER,
  zip_ok INTEGER, duplicate_of_known_sha INTEGER DEFAULT 0, note TEXT);
CREATE INDEX IF NOT EXISTS version_files_sv ON version_files(setid, version);
CREATE INDEX IF NOT EXISTS version_files_sha ON version_files(sha256);
"""
APPEND_ONLY = ("listing_observations", "history_entries", "version_files")
TRIGGERS = "".join(
    f"CREATE TRIGGER IF NOT EXISTS {t}_no_update BEFORE UPDATE ON {t} BEGIN SELECT RAISE(ABORT,'append-only'); END;"
    f"CREATE TRIGGER IF NOT EXISTS {t}_no_delete BEFORE DELETE ON {t} BEGIN SELECT RAISE(ABORT,'append-only'); END;"
    for t in APPEND_ONLY)


class Blocked(Exception):
    pass


class CapReached(Exception):
    pass


def parse_pub(raw):
    """'Nov 10, 2026' -> '2026-11-10' (None if unparseable)."""
    try:
        return dt.datetime.strptime((raw or "").strip(), "%b %d, %Y").date().isoformat()
    except ValueError:
        return None


def sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def open_delta(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(root / "updates.sqlite")
    c.row_factory = sqlite3.Row
    c.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;")
    c.executescript(SCHEMA + TRIGGERS)
    return c


def read_watermark(root):
    p = Path(root) / "WATERMARK.json"
    if not p.exists():
        return None
    return json.loads(p.read_text()).get("watermark")


def known_versions(evidence, delta, setids):
    """({(setid, version): sha}, set(all known shas of those setids)) from the immutable evidence DB
    (read-only, one pass) plus the delta database."""
    known, shas = {}, set()
    wanted = set(setids)
    if evidence and Path(evidence).exists() and wanted:
        e = sqlite3.connect(f"file:{evidence}?mode=ro&immutable=1", uri=True)
        try:
            for sid, ver, sha in e.execute("SELECT setid, version, selected_sha256 FROM versions"):
                if sid in wanted and sha:
                    known[(sid, int(ver))] = sha
                    shas.add(sha)
        finally:
            e.close()
    for r in delta.execute("SELECT setid, version, sha256 FROM version_files WHERE sha256 IS NOT NULL AND zip_ok=1"):
        if r["setid"] in wanted:
            known[(r["setid"], r["version"])] = r["sha256"]
        shas.add(r["sha256"])
    return known, shas


def zip_ok(path):
    try:
        with zipfile.ZipFile(path) as z:
            return any(n.lower().endswith(".xml") for n in z.namelist())
    except (zipfile.BadZipFile, OSError):
        return False


def _blocked(exc):
    m = str(exc)
    return "HTTP 403" in m or "HTTP 429" in m or "Retry deferred" in m


def run(*, root=ROOT, evidence=EVIDENCE, http_factory=None, now=None, since_days=None, smoke=False,
        max_downloads=DEFAULT_MAX_DOWNLOADS, max_requests=DEFAULT_MAX_REQUESTS, guard=None,
        log=print, stop_check=None, spacing=2):
    """Execute one update run. Returns the runs-row dict. Raises Blocked never; records it."""
    root = Path(root)
    now = now or dt.datetime.now(dt.timezone.utc)
    today = now.date()
    delta = open_delta(root)
    wm_in = read_watermark(root)
    if since_days is not None:
        cutoff = today - dt.timedelta(days=since_days)
        mode = "smoke" if smoke else "since-days"
    elif wm_in:
        cutoff = dt.date.fromisoformat(wm_in) - dt.timedelta(days=OVERLAP_DAYS)
        mode = "watermark"
    else:
        raise SystemExit("no watermark: pass --since-days N (or --initial-since) for the first run")
    run_id = now.strftime("%Y%m%dT%H%M%SZ")
    params = {"overlap_days": OVERLAP_DAYS, "max_downloads": max_downloads, "max_requests": max_requests,
              "since_days": since_days, "smoke": smoke}
    with delta:
        delta.execute("INSERT INTO runs(run_id,started_at,status,mode,watermark_in,cutoff,params_json) VALUES(?,?,?,?,?,?,?)",
                      (run_id, uc.utcnow(), "running", mode, wm_in, cutoff.isoformat(), json.dumps(params)))
    st = dict(listing_pages=0, listing_rows=0, candidate_setids=0, changed_setids=0, histories_fetched=0,
              new_versions=0, downloads=0, duplicate_sha=0, unavailable=0)
    status, error, wm_out = "complete", None, None
    base = Path(root) / "network"
    if http_factory is None:
        from gitrx_backend.jobs import HTTP

        def http_factory(r, cap, g):
            return HTTP(r, cap=cap, spacing=spacing, guard=g, retain_completed_partials=False,
                        max_bytes=20 * 1024**3)
    http = http_factory(base, max_requests, guard)
    start_requests = getattr(http, "request_count", 0)
    # the maintained HTTP class counts lifetime requests against its cap; make the cap per-run
    if hasattr(http, "cap"):
        http.cap = start_requests + max_requests
    consecutive = [0]

    def fetch(url):
        if stop_check and stop_check():
            raise KeyboardInterrupt("stop requested")
        try:
            p = Path(http.get(url))
            consecutive[0] = 0
            return p
        except Exception as exc:
            if _blocked(exc):
                raise Blocked(str(exc))
            if "request cap reached" in str(exc):
                raise CapReached(str(exc))
            consecutive[0] += 1
            if consecutive[0] >= MAX_CONSECUTIVE_FAILURES:
                raise Blocked(f"{MAX_CONSECUTIVE_FAILURES} consecutive failures: {exc}")
            raise

    try:
        # ---- 1. discovery
        candidates = {}   # setid -> (listed_version, published_date, published_raw, title, page, sha)
        older_pages, page, total_pages = 0, 1, None
        db_date = None
        while page <= MAX_LISTING_PAGES:
            url = f"{LISTING}?{urlencode({'pagesize': PAGESIZE, 'page': page})}"
            try:
                p = fetch(url)
            except (Blocked, CapReached):
                raise
            except Exception as exc:
                raise RuntimeError(f"listing page {page} failed: {exc}")
            body = json.loads(p.read_bytes())
            from gitrx_backend.census import validate_page
            validate_page(body, page, None if total_pages is None else total_pages)
            total_pages = (int(body["metadata"]["total_elements"]), max(1, int(body["metadata"]["total_pages"])))
            if page == 1:
                db_date = parse_pub((body["metadata"].get("db_published_date") or "")[:12].strip().rstrip(" "))
            st["listing_pages"] += 1
            rows = body["data"]
            st["listing_rows"] += len(rows)
            page_new = 0
            sha = p.name
            with delta:
                for r in rows:
                    d = parse_pub(r.get("published_date"))
                    if d is not None and d >= cutoff.isoformat():
                        page_new += 1
                        sid = r["setid"].lower()
                        candidates.setdefault(sid, (int(r["spl_version"]), d, r.get("published_date"), r.get("title"), page, sha))
                        delta.execute("INSERT INTO listing_observations(run_id,setid,listed_version,published_raw,published_date,title,page,page_sha256) VALUES(?,?,?,?,?,?,?,?)",
                                      (run_id, sid, int(r["spl_version"]), r.get("published_date"), d, r.get("title"), page, sha))
            older_pages = older_pages + 1 if page_new == 0 else 0
            if older_pages >= 2 or page >= total_pages[1]:
                break
            page += 1
        else:
            status, error = "partial", f"listing page limit {MAX_LISTING_PAGES} reached"
        st["candidate_setids"] = len(candidates)
        log(f"listing: pages={st['listing_pages']} rows={st['listing_rows']} candidates={len(candidates)} cutoff={cutoff}")

        # ---- 2. which setids are changed (listed version not yet held)
        known, shas = known_versions(evidence, delta, candidates)
        changed = sorted(s for s, v in candidates.items() if (s, v[0]) not in known)
        st["changed_setids"] = len(changed)
        log(f"changed setids (listed version not held): {len(changed)}")

        # ---- 3. history refresh + new versions
        for sid in changed:
            if st["downloads"] >= max_downloads:
                status, error = "partial", "max_downloads reached"
                break
            hist, hist_sha, page_h, pages_h = [], None, 1, 1
            while page_h <= pages_h:
                p = fetch(f"{HISTORY.format(setid=sid)}?{urlencode({'pagesize': 100, 'page': page_h})}")
                doc = json.loads(p.read_bytes())
                if doc["data"]["spl"]["setid"].lower() != sid:
                    raise RuntimeError(f"history identity mismatch for {sid}")
                pages_h = max(1, int(doc["metadata"]["total_pages"]))
                hist.extend(doc["data"]["history"])
                hist_sha = p.name
                page_h += 1
                if page_h > 1000:
                    break
            st["histories_fetched"] += 1
            versions = {}
            with delta:
                for h in hist:
                    v = int(h["spl_version"])
                    d = parse_pub(h.get("published_date"))
                    versions.setdefault(v, d)
                    delta.execute("INSERT INTO history_entries(run_id,setid,version,published_raw,published_date,history_sha256) VALUES(?,?,?,?,?,?)",
                                  (run_id, sid, v, h.get("published_date"), d, hist_sha))
            for v in sorted(versions):
                if (sid, v) in known:
                    continue
                if st["downloads"] >= max_downloads:
                    status, error = "partial", "max_downloads reached"
                    break
                st["new_versions"] += 1
                url = f"{GETFILE}?{urlencode({'type': 'zip', 'setid': sid, 'version': v})}"
                at = uc.utcnow()
                try:
                    p = fetch(url)
                except (Blocked, CapReached):
                    raise
                except Exception as exc:
                    code = None
                    if str(exc).startswith("HTTP "):
                        code = int(str(exc).split()[1].rstrip(":"))
                    st["unavailable"] += 1
                    with delta:
                        delta.execute("INSERT INTO version_files(run_id,setid,version,url,retrieved_at,status,note) VALUES(?,?,?,?,?,?,?)",
                                      (run_id, sid, v, url, at, code, f"unavailable: {str(exc)[:200]}"))
                    continue
                st["downloads"] += 1
                digest = sha_file(p)          # re-verify the stored write-once body
                if digest != p.name:
                    raise RuntimeError(f"stored body hash mismatch {p.name} != {digest}")
                dup = 1 if digest in shas else 0
                st["duplicate_sha"] += dup
                shas.add(digest)
                ok = zip_ok(p)
                with delta:
                    delta.execute("INSERT INTO version_files(run_id,setid,version,url,retrieved_at,status,sha256,bytes,zip_ok,duplicate_of_known_sha,note) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                  (run_id, sid, v, url, at, 200, digest, p.stat().st_size, int(ok), dup,
                                   None if ok else "not a zip with an xml member"))
                if ok:
                    known[(sid, v)] = digest
            if status == "partial":
                break
        if status == "complete":
            wm_out = db_date or (today - dt.timedelta(days=1)).isoformat()
            if wm_in and wm_out < wm_in:
                wm_out = wm_in
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
    n_req = getattr(http, "request_count", 0) - start_requests
    n_bytes = getattr(http, "bytes_received", 0)
    if smoke and status == "complete":
        status, wm_out = "smoke-complete", None   # never advances the production watermark
    with delta:
        delta.execute("UPDATE runs SET finished_at=?,status=?,watermark_out=?,error=?,requests=?,bytes=?,"
                      "listing_pages=?,listing_rows=?,candidate_setids=?,changed_setids=?,histories_fetched=?,"
                      "new_versions=?,downloads=?,duplicate_sha=?,unavailable=? WHERE run_id=?",
                      (uc.utcnow(), status, wm_out, error, n_req, n_bytes, st["listing_pages"], st["listing_rows"],
                       st["candidate_setids"], st["changed_setids"], st["histories_fetched"], st["new_versions"],
                       st["downloads"], st["duplicate_sha"], st["unavailable"], run_id))
    if status == "complete" and wm_out:       # atomic watermark update only after success
        uc.atomic_json(Path(root) / "WATERMARK.json", {"watermark": wm_out, "run_id": run_id, "updated_at": uc.utcnow()})
    row = dict(delta.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone())
    delta.close()
    log("run " + json.dumps({k: row[k] for k in ("run_id", "status", "watermark_in", "cutoff", "watermark_out",
                                                 "listing_pages", "candidate_setids", "changed_setids",
                                                 "histories_fetched", "new_versions", "downloads",
                                                 "duplicate_sha", "unavailable", "requests", "error")}))
    return row


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--since-days", type=int, help="override watermark: publications in the last N days (first run / smoke)")
    ap.add_argument("--smoke", action="store_true", help="never advance the production watermark")
    ap.add_argument("--max-downloads", type=int, default=DEFAULT_MAX_DOWNLOADS)
    ap.add_argument("--max-requests", type=int, default=DEFAULT_MAX_REQUESTS)
    ap.add_argument("--ignore-heavy", action="store_true", help="smoke/testing only: do not skip for a running heavy job")
    a = ap.parse_args(argv)
    try:
        os.nice(10)
    except OSError:
        pass
    root = Path(a.root)
    try:
        uc.preflight(check_processes=not a.ignore_heavy, heavy_lock=uc.HEAVY_LOCK if not a.ignore_heavy else "/nonexistent",
                     backoff_file=root / "BACKOFF-UNTIL")
        with uc.JobLock(root / "job.lock"):
            log = uc.Logger(root / "logs" / f"run-{dt.datetime.now(dt.timezone.utc):%Y%m%dT%H%M%SZ}.log")
            row = run(root=root, since_days=a.since_days, smoke=a.smoke, max_downloads=a.max_downloads,
                      max_requests=a.max_requests, guard=uc.require_volume,
                      log=log, stop_check=lambda: (Path(uc.UPDATES) / uc.STOP_NAME).exists() or
                      (not a.ignore_heavy and uc.heavy_lock_held() is not None))
    except uc.Skip as s:
        print(uc.utcnow(), str(s), flush=True)
        return 0
    return 0 if row["status"] in ("complete", "smoke-complete", "partial", "blocked", "interrupted") else 1


if __name__ == "__main__":
    sys.exit(main())
