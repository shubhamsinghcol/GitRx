"""No-network tests for gitrx_core.srlc_update (mock FDA HTTP, saved fixtures, sealed holdout)."""
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from gitrx_core import srlc, srlc_update as su, update_common as uc

FIX = Path(__file__).parent / "fixtures" / "core_srlc"
DETAIL = (FIX / "detail_312.html").read_text()
CSV = (FIX / "dl_312.bin").read_bytes()
TODAY = dt.date(2026, 11, 11)


def row(did, name, sup, dbu):
    f = lambda iso: "" if not iso else dt.date.fromisoformat(iso).strftime("%m/%d/%Y")
    return (f'<tr><td><a href="/x?event=searchdetail.page&DrugNameID={did}" title="{name}"><strong>{name}</strong></a></td>'
            f'<td>ING</td><td>1</td><td>NDA</td><td>{f(sup)}</td><td>{f(dbu)}</td><td>u</td></tr>')


def enum_page(rows):
    return ('<html><table id="example"><thead><tr><th>a</th></tr></thead><tbody>' + "".join(rows) + "</tbody></table></html>").encode()


def detail_with_date(date_raw):
    return DETAIL.replace("04/17/2025", date_raw)


class FakeFDA:
    def __init__(self, enum_rows, details=None, status_for=None):
        self.enum_rows, self.details, self.status_for = enum_rows, details or {}, status_for or {}
        self.calls, self.times = [], []
        self.t = 0.0

    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += s

    def __call__(self, method, url, data, headers):
        self.calls.append((method, url))
        self.times.append(self.t)
        assert headers["User-Agent"] == "GitRx-research/0.1 (+public SrLC validation)"
        m = re.search(r"DrugNameID=(\d+)", url)
        if m and int(m.group(1)) in self.status_for:
            return self.status_for[int(m.group(1))], {}, b"denied"
        if "searchResult" in url:
            return 200, {}, enum_page(self.enum_rows)
        did = int(m.group(1))
        if "downloadDetails" in url:
            return 200, {}, CSV
        return 200, {}, self.details.get(did, DETAIL).encode()


def make_old_gradebook(path, hits):
    Path(path).unlink(missing_ok=True)
    g = sqlite3.connect(path)
    g.execute("CREATE TABLE drugs(drug_name_id INTEGER)")
    g.execute("CREATE TABLE search_hits(drug_name_id INTEGER, suppl_date_iso TEXT, database_updated_iso TEXT)")
    for h in hits:
        g.execute("INSERT INTO search_hits VALUES(?,?,?)", h)
        g.execute("INSERT OR IGNORE INTO drugs VALUES(?)", (h[0],))
    g.commit()
    g.close()


def sha_of(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name) / "updates"
        self.old = Path(self.td.name) / "gradebook.sqlite"
        self.logs = []
        self.win = (dt.date(2026, 10, 1), dt.date(2026, 10, 31))

    def tearDown(self):
        self.td.cleanup()

    def go(self, fda, hits_old=(), build=True, **kw):
        make_old_gradebook(self.old, list(hits_old))
        kw.setdefault("window", self.win)
        return su.run(root=self.root, old_gradebook=str(self.old), fetch=fda, sleep=fda.sleep, clock=fda.clock,
                      today=TODAY, log=self.logs.append, build=build, **kw)


class ChangeDetectionTests(Base):
    def test_only_new_or_changed_drugs_are_fetched(self):
        fda = FakeFDA([row(10, "SAME", "2026-09-01", "2026-09-05"),
                       row(11, "CHANGED", "2026-09-02", "2026-11-03"),
                       row(12, "NEWDRUG", "2026-09-03", "2026-09-04")])
        r = self.go(fda, hits_old=[(10, "2026-09-01", "2026-09-05"), (11, "2026-09-02", "2026-09-06")], build=False)
        self.assertEqual((r["drugs_seen"], r["drugs_changed"], r["details_fetched"], r["csvs_fetched"]), (3, 2, 2, 2))
        urls = [u for _, u in fda.calls]
        self.assertFalse(any("DrugNameID=10" in u for u in urls))
        self.assertEqual(r["requests"], 1 + 2 + 2)
        # a second run now knows the pairs it recorded
        fda2 = FakeFDA(fda.enum_rows)
        self.root.joinpath("holdout").mkdir(exist_ok=True)
        r2 = su.run(root=self.root, old_gradebook=str(self.old), fetch=fda2, sleep=fda2.sleep, clock=fda2.clock,
                    today=TODAY, window=self.win, log=self.logs.append, build=False)
        self.assertEqual(r2["drugs_changed"], 0)
        self.assertEqual(r2["requests"], 1)

    def test_old_ledger_never_touched_and_new_ledger_used(self):
        fda = FakeFDA([row(12, "NEWDRUG", "2026-09-03", "2026-09-04")])
        self.go(fda, build=False)
        self.assertTrue((self.root / "ledger.sqlite").exists())
        con = sqlite3.connect(self.root / "ledger.sqlite")
        kinds = dict(con.execute("SELECT kind, COUNT(*) FROM requests WHERE ok=1 GROUP BY kind"))
        self.assertEqual(kinds, {"enum": 1, "detail": 1, "csv": 1})


class PoliteTests(Base):
    def test_spacing_at_least_one_second_even_if_configured_lower(self):
        fda = FakeFDA([row(12, "A", "2026-09-03", "2026-09-04"), row(13, "B", "2026-09-03", "2026-09-04")])
        self.go(fda, build=False, min_interval=0.01)
        gaps = [b - a for a, b in zip(fda.times, fda.times[1:])]
        self.assertTrue(gaps and all(g >= 1.0 - 1e-9 for g in gaps), gaps)

    def test_run_cap(self):
        fda = FakeFDA([row(i, f"D{i}", "2026-09-03", "2026-09-04") for i in range(20, 30)])
        r = self.go(fda, build=False, run_cap=4)
        self.assertEqual(len(fda.calls), 4)
        self.assertEqual(r["status"], "partial")
        self.assertIn("cap", r["error"])

    def test_403_stops_immediately_and_backs_off(self):
        fda = FakeFDA([row(i, f"D{i}", "2026-09-03", "2026-09-04") for i in (20, 21, 22)], status_for={20: 403})
        r = self.go(fda, build=False)
        self.assertEqual(r["status"], "blocked")
        self.assertEqual(len(fda.calls), 2)          # enum + the 403 detail; nothing after
        self.assertIsNotNone(uc.backoff_active(self.root / "BACKOFF-UNTIL"))

    def test_429_stops(self):
        fda = FakeFDA([row(i, f"D{i}", "2026-09-03", "2026-09-04") for i in (20, 21)], status_for={20: 429})
        r = self.go(fda, build=False)
        self.assertEqual(r["status"], "blocked")
        self.assertEqual(len(fda.calls), 2)

    def test_six_month_windows(self):
        w = su.windows_for(dt.date(2026, 11, 11), 6)
        self.assertEqual((w[0][0], w[-1][0], len(w)), (dt.date(2026, 6, 1), dt.date(2026, 11, 1), 6))
        w = su.windows_for(dt.date(2026, 2, 3), 6)
        self.assertEqual(w[0][0], dt.date(2025, 9, 1))


class HoldoutTests(Base):
    def test_manifest_snapshot_and_logs_expose_no_content(self):
        make_old_gradebook(self.old, [])
        old_sha = sha_of(self.old)
        rows = [row(312, "ELIQUIS", "2026-12-01", "2026-12-05"),      # holdout by both dates
                row(313, "LATEDB", "2026-09-01", "2026-11-20"),       # holdout by Database Updated only
                row(314, "PRE", "2026-09-01", "2026-09-02")]          # not holdout
        fda = FakeFDA(rows, details={312: detail_with_date("12/01/2026"), 313: DETAIL.replace("04/17/2025", "09/01/2026"),
                                     314: DETAIL.replace("04/17/2025", "09/01/2026")})
        r = self.go(fda, window=(dt.date(2026, 9, 1), dt.date(2026, 12, 31)))
        self.assertEqual(r["status"], "complete")
        man = json.loads(next((self.root / "holdout").glob("manifest-*.json")).read_text())
        ent = {e["drug_name_id"]: e for e in man["entries"]}
        self.assertEqual(sorted(ent), [312, 313])
        self.assertTrue(ent[312]["by_approval_date"] and ent[312]["by_database_updated"])
        self.assertFalse(ent[313]["by_approval_date"])
        self.assertTrue(ent[313]["by_database_updated"])
        self.assertEqual(man["drug_ids_fingerprint"], su.ids_fingerprint([312, 313]))
        self.assertEqual(r["holdout_fingerprint"], man["drug_ids_fingerprint"])
        self.assertTrue(man["supplements"])                       # supplement fingerprints present
        text = (self.root / "holdout").joinpath(next((self.root / "holdout").glob("manifest-*.json")).name).read_text()
        for needle in ("Bleeding", "antagonizing", "reversal", "ELIQUIS", "LATEDB"):   # no label text or names
            self.assertNotIn(needle, text)
            self.assertNotIn(needle, "\n".join(self.logs))
        # snapshot: sealed rows omitted, pre-cutoff rows retained, provenance present
        gb = sqlite3.connect(r["gradebook"])
        self.assertEqual(gb.execute("SELECT COUNT(*) FROM supplements WHERE approval_date_iso>'2026-11-11' OR database_updated>'2026-11-11'").fetchone()[0], 0)
        self.assertEqual(gb.execute("SELECT COUNT(*) FROM changes WHERE drug_name_id=312 AND supplement_id IN (SELECT supplement_id FROM supplements WHERE approval_date_iso='2026-12-01')").fetchone()[0], 0)
        self.assertGreater(gb.execute("SELECT COUNT(*) FROM changes WHERE drug_name_id=314").fetchone()[0], 0)
        self.assertEqual(gb.execute("SELECT COUNT(*) FROM csv_rows WHERE drug_name_id IN (312,313)").fetchone()[0], 0)
        prov = dict(gb.execute("SELECT k, v FROM provenance"))
        self.assertEqual(prov["contains_holdout_text"], "0")
        self.assertEqual(prov["holdout_after"], "2026-11-11")
        tables = {x[0] for x in gb.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"drugs", "search_hits", "supplements", "changes", "csv_rows", "reconcile", "build_info", "provenance"} <= tables)
        # frozen inputs untouched; snapshot has a dated name
        self.assertEqual(sha_of(self.old), old_sha)
        self.assertRegex(Path(r["gradebook"]).name, r"^gradebook-20261111.*\.sqlite$")

    def test_snapshot_never_overwrites(self):
        fda = FakeFDA([row(314, "PRE", "2026-09-01", "2026-09-02")], details={314: DETAIL.replace("04/17/2025", "09/01/2026")})
        r1 = self.go(fda)
        fda2 = FakeFDA([row(315, "PRE2", "2026-09-01", "2026-09-02")], details={315: DETAIL.replace("04/17/2025", "09/01/2026")})
        import time as _t
        _t.sleep(1.1)
        r2 = su.run(root=self.root, old_gradebook=str(self.old), fetch=fda2, sleep=fda2.sleep, clock=fda2.clock,
                    today=TODAY, window=self.win, log=self.logs.append)
        self.assertNotEqual(r1["gradebook"], r2["gradebook"])
        self.assertTrue(Path(r1["gradebook"]).exists())

    def test_is_holdout(self):
        self.assertTrue(su.is_holdout("2026-11-12", None))
        self.assertTrue(su.is_holdout("2026-01-01", "2026-11-12"))
        self.assertFalse(su.is_holdout("2026-11-11", "2026-11-11"))


class SafetyTests(unittest.TestCase):
    def test_skip_conditions_apply_to_srlc_paths(self):
        with self.assertRaisesRegex(uc.Skip, "SSD absent"):
            uc.preflight(gitrx="/nonexistent/GitRx", volume="/nonexistent")
        with tempfile.TemporaryDirectory() as d:
            from unittest import mock
            with mock.patch.object(uc, "volume_ok", return_value=True):
                bo = Path(d) / "BACKOFF-UNTIL"
                uc.write_backoff(bo)
                with self.assertRaisesRegex(uc.Skip, "backoff"):
                    uc.preflight(gitrx=d, updates=d, heavy_lock=d + "/x", check_processes=False, backoff_file=bo)
                bo.write_text("2020-01-01T00:00:00Z\n")
                uc.preflight(gitrx=d, updates=d, heavy_lock=d + "/x", check_processes=False, backoff_file=bo)


if __name__ == "__main__":
    unittest.main()
