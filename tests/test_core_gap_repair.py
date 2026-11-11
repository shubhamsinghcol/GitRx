"""No-network tests for gitrx_core.gap_repair (mock HTTP)."""
import hashlib
import io
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from gitrx_core import gap_repair as gr

A, B = "aaaaaaaa-0000-0000-0000-000000000001", "bbbbbbbb-0000-0000-0000-000000000002"


def zbytes(setid, version):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("x.xml", f'<document><setId root="{setid}"/><versionNumber value="{version}"/></document>')
    return b.getvalue()


class FakeHTTP:
    def __init__(self, root, routes, fail=None):
        self.root = Path(root)
        (self.root / "bodies").mkdir(parents=True, exist_ok=True)
        self.routes, self.fail = routes, fail or {}
        self.request_count, self.cap, self.bytes_received, self.urls = 0, 10**6, 0, []

    def get(self, url):
        if self.request_count >= self.cap:
            raise RuntimeError("Workspace request cap reached")
        self.request_count += 1
        self.urls.append(url)
        for k, exc in self.fail.items():
            if k in url:
                raise exc
        for k, body in self.routes.items():
            if k in url:
                p = self.root / "bodies" / hashlib.sha256(body).hexdigest()
                p.write_bytes(body)
                return p
        raise ValueError("HTTP 404: " + url)


class T(unittest.TestCase):
    def test_wilson(self):
        lo, hi = gr.wilson(0, 200)
        self.assertEqual(lo, 0.0)
        self.assertAlmostEqual(hi, 0.0188, places=3)
        self.assertEqual(gr.wilson(0, 0), (0.0, 1.0))
        lo, hi = gr.wilson(5, 10)
        self.assertTrue(0.23 < lo < 0.24 and 0.76 < hi < 0.77)

    def test_missing_and_sample(self):
        cap = {A: {3, 4, 7}, B: {100, 101}, "x": {1, 2}, "y": {1, 5000}}
        below, internal = gr.missing_versions(cap)
        self.assertEqual(sorted(s for s, _ in below if s == A), [A, A])
        self.assertEqual([v for s, v in internal if s == A], [5, 6])
        self.assertEqual(len([1 for s, _ in below if s == B]), 99)
        self.assertFalse(any(s == "x" for s, _ in below + internal))
        self.assertFalse(any(s == "y" for s, _ in below + internal))  # anomalous number excluded
        inv = {"captured": cap}
        s1, pools = gr.draw_sample(inv, 7, {"internal_gap": 1, "below_v1_other": 2, "below_v1_start100": 5})
        s2, _ = gr.draw_sample(inv, 7, {"internal_gap": 1, "below_v1_other": 2, "below_v1_start100": 5})
        self.assertEqual(s1, s2)
        self.assertEqual(pools["below_v1_start100"], 99)
        self.assertEqual(len(s1["below_v1_start100"]), 5)

    def test_probe_outcomes_and_append_only(self):
        with tempfile.TemporaryDirectory() as d:
            routes = {f"setid={A}&version=3": zbytes(A, 3), f"setid={A}&version=4": zbytes(B, 4)}
            made = []

            def fac(r, cap, g):
                made.append(FakeHTTP(r, routes))
                return made[0]
            known = {hashlib.sha256(zbytes(A, 3)).hexdigest()}
            row = gr.probe(root=d, targets=[(A, 3, "control_held", "held"), (A, 4, "retry_unavailable", "no_raw"),
                                            (A, 5, "missing_sample", "internal_gap")],
                           seed=1, known_shas=known, http_factory=fac, log=lambda *_: None)
            self.assertEqual(row["status"], "complete")
            self.assertEqual(row["requests"], 3)
            s = gr.summarize(Path(d) / "gap-repair.sqlite")
            self.assertEqual(s["control_held/held"]["outcomes"], {"recovered_duplicate_of_held": 1})
            self.assertEqual(s["retry_unavailable/no_raw"]["outcomes"], {"invalid_body": 1})  # identity mismatch
            self.assertEqual(s["missing_sample/internal_gap"]["outcomes"], {"not_found": 1})
            c = sqlite3.connect(Path(d) / "gap-repair.sqlite")
            with self.assertRaises(sqlite3.DatabaseError):
                c.execute("UPDATE attempts SET outcome='x'")
            with self.assertRaises(sqlite3.DatabaseError):
                c.execute("DELETE FROM targets")

    def test_404s_do_not_trigger_stop(self):
        with tempfile.TemporaryDirectory() as d:
            h = FakeHTTP(d + "/n", {})
            row = gr.probe(root=d, targets=[(A, v, "missing_sample", "internal_gap") for v in range(1, 12)], seed=1,
                           http_factory=lambda r, c, g: h, log=lambda *_: None)
            self.assertEqual(row["status"], "complete")
            self.assertEqual(h.request_count, 11)

    def test_stops_on_429_and_cap(self):
        with tempfile.TemporaryDirectory() as d:
            h = FakeHTTP(d + "/n", {}, fail={"version=2": RuntimeError("HTTP 429: x")})
            row = gr.probe(root=d, targets=[(A, v, "missing_sample", "internal_gap") for v in (1, 2, 3)], seed=1,
                           http_factory=lambda r, c, g: h, log=lambda *_: None)
            self.assertEqual(row["status"], "blocked")
            self.assertEqual(h.request_count, 2)
            self.assertTrue((Path(d) / "BACKOFF-UNTIL").exists())
        with tempfile.TemporaryDirectory() as d:
            h = FakeHTTP(d + "/n", {})
            row = gr.probe(root=d, targets=[(A, v, "missing_sample", "internal_gap") for v in (1, 2, 3)], seed=1,
                           http_factory=lambda r, c, g: h, max_requests=1000, prior_requests=998, log=lambda *_: None)
            self.assertEqual(row["status"], "partial")
            self.assertEqual(h.request_count, 2)


if __name__ == "__main__":
    unittest.main()
