"""No-network tests for gitrx_core.dailymed_update and gitrx_core.update_common (mock HTTP)."""
import datetime as dt
import hashlib
import io
import json
import os
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from gitrx_core import dailymed_update as du
from gitrx_core import update_common as uc

NOW = dt.datetime(2026, 11, 11, 12, 0, tzinfo=dt.timezone.utc)
A, B, C = ("aaaaaaaa-0000-0000-0000-000000000001", "bbbbbbbb-0000-0000-0000-000000000002",
           "cccccccc-0000-0000-0000-000000000003")


def zbytes(tag):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("label.xml", f"<document>{tag}</document>")
    return b.getvalue()


def lst(page, total_pages, rows, total=None):
    return json.dumps({"data": [{"spl_version": v, "published_date": d, "title": "T", "setid": s} for s, v, d in rows],
                       "metadata": {"db_published_date": "Nov 10, 2026 07:19:40PM EST", "total_elements": total or 100,
                                    "total_pages": total_pages, "current_page": page}}).encode()


def hist(setid, versions):
    return json.dumps({"data": {"spl": {"setid": setid}, "history": [
        {"spl_version": v, "published_date": d} for v, d in versions]},
        "metadata": {"total_pages": 1, "current_page": 1, "total_elements": len(versions)}}).encode()


class FakeHTTP:
    """Mimics gitrx_backend.jobs.HTTP: get(url)->Path of write-once hash-named body, request cap, counters."""

    def __init__(self, root, routes, mislabel=False, fail=None):
        self.root = Path(root)
        (self.root / "bodies").mkdir(parents=True, exist_ok=True)
        self.routes, self.mislabel, self.fail = routes, mislabel, fail or {}
        self.request_count, self.cap, self.bytes_received, self.urls = 0, 10**6, 0, []

    def get(self, url):
        if self.request_count >= self.cap:
            raise RuntimeError("Workspace request cap reached")
        self.request_count += 1
        self.urls.append(url)
        for k, exc in self.fail.items():
            if k in url:
                raise exc
        body = None
        for k, v in self.routes.items():
            if k in url:
                body = v
        if body is None:
            raise ValueError("HTTP 404: " + url)
        digest = hashlib.sha256(body).hexdigest()
        p = self.root / "bodies" / (("0" * 64) if self.mislabel else digest)
        p.write_bytes(body)
        self.bytes_received += len(body)
        return p


def evidence(path, rows):
    Path(path).unlink(missing_ok=True)
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE versions(id INTEGER PRIMARY KEY, setid, version, state, selected_sha256)")
    for i, (s, v, sha) in enumerate(rows):
        c.execute("INSERT INTO versions(setid,version,state,selected_sha256) VALUES(?,?,?,?)", (s, v, "downloaded", sha))
    c.commit()
    c.close()


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name) / "dailymed"
        self.ev = Path(self.td.name) / "ev.sqlite"
        evidence(self.ev, [])
        self.logs = []

    def tearDown(self):
        self.td.cleanup()

    def set_wm(self, d):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "WATERMARK.json").write_text(json.dumps({"watermark": d}))

    def go(self, routes, **kw):
        holder = {}
        mislabel, fail, now = kw.pop("mislabel", False), kw.pop("fail", None), kw.pop("now", NOW)

        def factory(r, cap, g):
            holder["h"] = FakeHTTP(r, routes, mislabel=mislabel, fail=fail)
            return holder["h"]
        row = du.run(root=self.root, evidence=self.ev, http_factory=factory, now=now, log=self.logs.append, **kw)
        return row, holder["h"]

    def runs(self):
        c = sqlite3.connect(self.root / "updates.sqlite")
        c.row_factory = sqlite3.Row
        return c

    def wm(self):
        p = self.root / "WATERMARK.json"
        return json.loads(p.read_text())["watermark"] if p.exists() else None


class WatermarkTests(Base):
    def routes(self):
        return {
            "page=1": lst(1, 5, [(A, 2, "Nov 10, 2026"), (B, 1, "Nov 05, 2026"), (C, 3, "Nov 01, 2026")]),
            "page=2": lst(2, 5, [("dddddddd-0000-0000-0000-000000000004", 1, "Oct 20, 2026")]),
            "page=3": lst(3, 5, [("eeeeeeee-0000-0000-0000-000000000005", 1, "Oct 19, 2026")]),
            "page=4": lst(4, 5, []),  # must never be requested
            f"{A}/history": hist(A, [(1, "Jan 01, 2026"), (2, "Nov 10, 2026")]),
            f"{B}/history": hist(B, [(1, "Nov 05, 2026")]),
            f"setid={A}&version=1": zbytes("a1"), f"setid={A}&version=2": zbytes("a2"),
            f"setid={B}&version=1": zbytes("b1"),
        }

    def test_overlap_cutoff_and_watermark_advance(self):
        self.set_wm("2026-11-10")            # cutoff = 11-10 minus 7 = 11-03
        row, h = self.go(self.routes())
        self.assertEqual(row["cutoff"], "2026-11-03")
        self.assertEqual(row["status"], "complete")
        self.assertEqual(row["candidate_setids"], 2)        # C (Nov 01) is before the cutoff
        self.assertEqual(row["watermark_in"], "2026-11-10")
        self.assertEqual(row["watermark_out"], "2026-11-10")  # server db_published_date
        self.assertEqual(self.wm(), "2026-11-10")
        self.assertFalse(any("page=4" in u for u in h.urls))  # stopped after two all-older pages
        self.assertEqual(row["downloads"], 3)
        self.assertTrue(all("nlm.nih.gov" in u for u in h.urls))

    def test_partial_run_does_not_advance_watermark(self):
        self.set_wm("2026-11-01")
        row, _ = self.go(self.routes(), max_downloads=1)
        self.assertEqual(row["status"], "partial")
        self.assertEqual(self.wm(), "2026-11-01")
        self.assertIsNone(row["watermark_out"])

    def test_first_run_needs_since_days_and_smoke_never_sets_watermark(self):
        with self.assertRaises(SystemExit):
            self.go(self.routes())
        row, _ = self.go(self.routes(), since_days=2, smoke=True)
        self.assertEqual(row["status"], "smoke-complete")
        self.assertEqual(row["cutoff"], "2026-11-09")
        self.assertIsNone(self.wm())


class DedupeTests(Base):
    def test_known_versions_not_downloaded_and_duplicate_sha_flagged(self):
        z1 = zbytes("a1")
        sha1 = hashlib.sha256(z1).hexdigest()
        evidence(self.ev, [(A, 1, sha1)])
        routes = {"page=1": lst(1, 1, [(A, 2, "Nov 10, 2026")]),
                  f"{A}/history": hist(A, [(1, "Jan 01, 2026"), (2, "Nov 10, 2026")]),
                  f"setid={A}&version=2": z1,        # new version whose bytes equal an already-held sha
                  f"setid={A}&version=1": zbytes("never")}
        row, h = self.go(routes, since_days=3)
        self.assertEqual(row["new_versions"], 1)
        self.assertEqual(row["downloads"], 1)
        self.assertEqual(row["duplicate_sha"], 1)
        self.assertFalse(any("version=1" in u for u in h.urls))
        r = self.runs().execute("SELECT * FROM version_files").fetchall()
        self.assertEqual([(x["version"], x["zip_ok"], x["duplicate_of_known_sha"]) for x in r], [(2, 1, 1)])

    def test_listed_version_already_held_means_no_history_request(self):
        evidence(self.ev, [(A, 2, "f" * 64)])
        routes = {"page=1": lst(1, 1, [(A, 2, "Nov 10, 2026")])}
        row, h = self.go(routes, since_days=3)
        self.assertEqual(row["changed_setids"], 0)
        self.assertEqual(len(h.urls), 1)

    def test_second_run_skips_what_delta_db_holds(self):
        routes = {"page=1": lst(1, 1, [(B, 1, "Nov 05, 2026")]), f"{B}/history": hist(B, [(1, "Nov 05, 2026")]),
                  f"setid={B}&version=1": zbytes("b1")}
        r1, _ = self.go(routes, since_days=10)
        self.assertEqual(r1["downloads"], 1)
        r2, h2 = self.go(routes, since_days=10, now=NOW + dt.timedelta(minutes=1))
        self.assertEqual(r2["changed_setids"], 0)
        self.assertEqual(r2["downloads"], 0)

    def test_unavailable_version_recorded_and_retried_later(self):
        routes = {"page=1": lst(1, 1, [(B, 1, "Nov 05, 2026")]), f"{B}/history": hist(B, [(1, "Nov 05, 2026")])}
        r, _ = self.go(routes, since_days=10)
        self.assertEqual(r["unavailable"], 1)
        rows = self.runs().execute("SELECT status, sha256 FROM version_files").fetchall()
        self.assertEqual((rows[0][0], rows[0][1]), (404, None))

    def test_hash_verification_failure_is_an_error_not_a_record(self):
        routes = {"page=1": lst(1, 1, [(B, 1, "Nov 05, 2026")]), f"{B}/history": hist(B, [(1, "Nov 05, 2026")]),
                  f"setid={B}&version=1": zbytes("b1")}
        # listing/history also get mislabelled, so the very first fetched body already fails verification
        row, _ = self.go(routes, since_days=10, mislabel=True)
        self.assertIn(row["status"], ("error",))
        self.assertEqual(self.runs().execute("SELECT COUNT(*) FROM version_files WHERE sha256 IS NOT NULL").fetchone()[0], 0)

    def test_append_only_triggers(self):
        routes = {"page=1": lst(1, 1, [(B, 1, "Nov 05, 2026")]), f"{B}/history": hist(B, [(1, "Nov 05, 2026")]),
                  f"setid={B}&version=1": zbytes("b1")}
        self.go(routes, since_days=10)
        c = self.runs()
        for t in du.APPEND_ONLY:
            with self.assertRaises(sqlite3.DatabaseError):
                c.execute(f"UPDATE {t} SET run_id='x'")
            with self.assertRaises(sqlite3.DatabaseError):
                c.execute(f"DELETE FROM {t}")


class BlockedTests(Base):
    def test_403_stops_and_backs_off(self):
        self.set_wm("2026-11-01")
        routes = {"page=1": lst(1, 1, [(A, 2, "Nov 10, 2026"), (B, 1, "Nov 05, 2026")]),
                  f"{A}/history": hist(A, [(2, "Nov 10, 2026")])}
        row, h = self.go(routes, fail={f"{A}/history": ValueError("HTTP 403: forbidden")})
        self.assertEqual(row["status"], "blocked")
        self.assertEqual(self.wm(), "2026-11-01")
        self.assertFalse(any(B in u for u in h.urls))      # no further requests after the 403
        self.assertIsNotNone(uc.backoff_active(self.root / "BACKOFF-UNTIL"))
        with self.assertRaises(uc.Skip):
            uc.preflight(volume=self.td.name, check_processes=False)  # SSD check first
        with mock.patch.object(uc, "volume_ok", return_value=True):
            with self.assertRaises(uc.Skip) as cm:
                uc.preflight(gitrx=self.td.name, updates=self.td.name, heavy_lock=self.td.name + "/none",
                             check_processes=False, backoff_file=self.root / "BACKOFF-UNTIL")
            self.assertIn("backoff", str(cm.exception))

    def test_429_stops(self):
        routes = {"page=1": lst(1, 1, [(A, 2, "Nov 10, 2026")])}
        row, _ = self.go(routes, since_days=3, fail={"spls.json": ValueError("HTTP 429: slow down")})
        self.assertEqual(row["status"], "blocked")

    def test_request_cap(self):
        self.set_wm("2026-11-01")
        routes = {"page=1": lst(1, 1, [(A, 2, "Nov 10, 2026"), (B, 1, "Nov 05, 2026")]),
                  f"{A}/history": hist(A, [(2, "Nov 10, 2026")]), f"setid={A}&version=2": zbytes("a2"),
                  f"{B}/history": hist(B, [(1, "Nov 05, 2026")]), f"setid={B}&version=1": zbytes("b1")}
        row, h = self.go(routes, max_requests=3)
        self.assertEqual(row["status"], "partial")
        self.assertEqual(row["requests"], 3)
        self.assertEqual(self.wm(), "2026-11-01")


class SafetyTests(unittest.TestCase):
    def test_ssd_absent_skip(self):
        with self.assertRaises(uc.Skip) as cm:
            uc.preflight(gitrx="/nonexistent/GitRx", volume="/nonexistent")
        self.assertEqual(str(cm.exception), "skipped: SSD absent")

    def test_wrong_volume_token(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "GitRx").mkdir()
            (Path(d) / "GitRx/.volume-identity").write_text("other")
            self.assertFalse(uc.volume_ok(d, "GitRx/.volume-identity", "expected"))

    def test_stop_file_and_heavy_lock(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(uc, "volume_ok", return_value=True):
            kw = dict(gitrx=d, updates=d, heavy_lock=d + "/HEAVY-JOB.lock", check_processes=False)
            uc.preflight(**kw)                                    # clean: no raise
            (Path(d) / "STOP").write_text("")
            with self.assertRaisesRegex(uc.Skip, "STOP"):
                uc.preflight(**kw)
            (Path(d) / "STOP").unlink()
            Path(kw["heavy_lock"]).write_text(f"{os.getpid()}\nrebuild\n")
            with self.assertRaisesRegex(uc.Skip, "heavy"):
                uc.preflight(**kw)
            Path(kw["heavy_lock"]).write_text("999999999\n")      # stale pid: ignored
            uc.preflight(**kw)
            Path(kw["heavy_lock"]).write_text("not-a-pid\n")      # unparseable: honored
            with self.assertRaisesRegex(uc.Skip, "heavy"):
                uc.preflight(**kw)

    def test_heavy_process_detection(self):
        fake = mock.Mock(stdout="  11 /usr/bin/python -m gitrx_core.rebuild --out x\n  12 /bin/zsh\n")
        with mock.patch.object(uc.subprocess, "run", return_value=fake):
            self.assertTrue(uc.heavy_process_running())
        fake = mock.Mock(stdout="  12 /bin/zsh\n  13 python -m gitrx_core.dailymed_update\n")
        with mock.patch.object(uc.subprocess, "run", return_value=fake):
            self.assertFalse(uc.heavy_process_running())

    def test_single_instance_flock(self):
        with tempfile.TemporaryDirectory() as d:
            with uc.JobLock(Path(d) / "job.lock"):
                with self.assertRaisesRegex(uc.Skip, "another instance"):
                    with uc.JobLock(Path(d) / "job.lock"):
                        pass
            with uc.JobLock(Path(d) / "job.lock"):   # released
                pass

    def test_parse_pub(self):
        self.assertEqual(du.parse_pub("Nov 10, 2026"), "2026-11-10")
        self.assertIsNone(du.parse_pub("garbage"))


if __name__ == "__main__":
    unittest.main()
