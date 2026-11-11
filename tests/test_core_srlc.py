"""No-network tests for gitrx_core.srlc (mock HTTP, saved FDA fixtures)."""
import datetime as dt
import json
import os
import sqlite3
import tempfile
import unittest
from collections import Counter

from gitrx_core import srlc

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "core_srlc")


def fx(name, mode="rb"):
    with open(os.path.join(FIX, name), mode) as f:
        return f.read()


class ParseTests(unittest.TestCase):
    def test_result_rows(self):
        rows = srlc.parse_results(fx("r_date2.html").decode("utf-8", "replace"))
        self.assertEqual(len(rows), 16)
        self.assertEqual(rows[0]["drug_name_id"], 1358)
        self.assertEqual(rows[0]["drug_name"], "AURYXIA")
        self.assertEqual((rows[0]["appl_no"], rows[0]["appl_type"]), ("205874", "NDA"))
        self.assertEqual(rows[0]["suppl_date_iso"], "2024-01-19")
        self.assertEqual(rows[0]["database_updated_iso"], "2024-01-23")
        by_name = srlc.parse_results(fx("r_name.html").decode("utf-8", "replace"))
        self.assertEqual(by_name[0]["drug_name_id"], 312)
        self.assertEqual(by_name[0]["suppl_date_raw"], "04/17/2025")

    def test_empty_result_page(self):
        body = fx("r_empty.html")
        self.assertEqual(srlc.parse_results(body.decode("utf-8")), [])
        self.assertIsNone(srlc.validate_body("enum", body))
        self.assertIsNotNone(srlc.validate_body("enum", b"<html>Akamai challenge</html>"))

    def test_detail_312(self):
        d = srlc.parse_detail(fx("detail_312.html").decode("utf-8"))
        self.assertEqual((d["drug_name"], d["appl_type"], d["appl_no"], d["active_ingredient"]),
                         ("ELIQUIS", "NDA", "202155", "APIXABAN"))
        self.assertEqual([s["suppl_ref"] for s in d["supplements"]],
                         ["SUPPL-39", "SUPPL-34", "SUPPL-32", "SUPPL-24", "SUPPL-21", "SUPPL-20", "SUPPL-18", "SUPPL-12"])
        s39 = d["supplements"][0]
        self.assertEqual(s39["date_iso"], "2025-04-17")
        self.assertTrue(s39["label_pdf_url"].endswith("202155Orig1s039,s040lbl.pdf"))
        self.assertEqual(Counter(c["section_group"] for c in s39["changes"]),
                         Counter({"USP": 3, "WP": 2, "AR": 1, "DI": 1, "MG": 1}))
        c = s39["changes"][0]
        self.assertEqual((c["section_number"], c["section_title"]), ("5.2", "Bleeding"))
        self.assertEqual(c["underlined"][0], "A specific reversal")
        self.assertIn("(andexanet alfa) antagonizing", c["underlined"])
        self.assertTrue(c["has_elision"])
        self.assertIn("Additions and/or revisions underlined:", c["comments"])
        self.assertIn("<u>", c["description_html"])
        ar = [x for x in s39["changes"] if x["section_group"] == "AR"][0]
        self.assertTrue(ar["refers_to_table"])
        self.assertEqual(sum(len(x["underlined"]) for s in d["supplements"] for x in s["changes"]), 59)
        self.assertEqual(sum(len(s["changes"]) for s in d["supplements"]), 24)
        # subsection without a <strong> heading keeps an empty number/title rather than inventing one
        di = [x for x in s39["changes"] if x["section_group"] == "DI"][0]
        self.assertEqual(di["section_number"], "")

    def test_csv_embedded_crlf_and_unescaped_quotes(self):
        rows, residual = srlc.parse_download_csv(fx("dl_312.bin"))
        self.assertEqual(len(rows), 24)
        self.assertEqual(residual, 0)
        self.assertEqual(rows[0]["section_group"], "DI")
        self.assertEqual(rows[0]["title"], "7.3 Anticoagulants and Antiplatelet Agents")
        self.assertIn("\r\n", rows[0]["description_html"])
        self.assertEqual(Counter(r["section_group"] for r in rows)["MG"], 5)
        self.assertEqual({r["appl_no"] for r in rows}, {"202155"})

    def test_csv_synthetic(self):
        body = (b'\n"Section Title","Title","Description","Appl No","Appl Type","Drug Name","Active Ingredient","Sub No","Sub Type","URL","URL Text"\n'
                b'"Boxed Warning","","a\r\nb <a href="x">q</a>","1","NDA     ","X","Y","2","SUPPL     ","http://u","T"\n'
                b'"Contraindications","4","c","1","NDA","X","Y","3","SUPPL","http://u","T"\n\n')
        rows, residual = srlc.parse_download_csv(body)
        self.assertEqual([r["section_group"] for r in rows], ["BW", "C"])
        self.assertEqual(rows[0]["description_html"], 'a\r\nb <a href="x">q</a>')
        self.assertEqual(residual, 0)

    def test_group_mapping(self):
        g = srlc.group_of
        self.assertEqual(g("5 Warnings and Precautions"), "WP")
        self.assertEqual(g("Boxed Warning"), "BW")
        self.assertEqual(g("17 PCI/PI/MG (Patient Counseling Information/Patient Information/Medication Guide)"), "MG")
        self.assertEqual(g("8 Use in Specific Populations"), "USP")
        self.assertEqual(g("Something else"), "UNMAPPED")


class FakeHTTP:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def __call__(self, method, url, data, headers):
        self.calls.append((method, url, data, headers))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


ENUM_BODY = fx("r_date2.html")
DETAIL = fx("detail_312.html")
CSV = fx("dl_312.bin")


class CrawlTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = self.td.name
        self.sleeps = []
        self.t = 0.0

    def tearDown(self):
        self.td.cleanup()

    def crawler(self, http, **kw):
        def sleep(s):
            self.sleeps.append(s)
            self.t += s
        return srlc.Crawler(self.root, fetch=http, sleep=sleep, clock=lambda: self.t, log=lambda m: None, **kw)

    def test_raw_store_write_once(self):
        a = srlc.store_raw(self.root, b"hello")
        b = srlc.store_raw(self.root, b"hello")
        self.assertEqual(a, b)
        self.assertEqual(srlc.read_raw(self.root, a), b"hello")
        self.assertEqual(os.listdir(os.path.dirname(srlc.raw_path(self.root, a))), [a])

    def test_enum_params_and_resume(self):
        http = FakeHTTP([(200, {}, ENUM_BODY)] * 3)
        c = self.crawler(http)
        a, b = dt.date(2024, 1, 1), dt.date(2024, 1, 31)
        srlc.crawl_window(c, a, b)
        srlc.crawl_window(c, a, b)  # resume: no second request
        self.assertEqual(len(http.calls), 1)
        m, url, data, hdr = http.calls[0]
        self.assertEqual(m, "POST")
        self.assertIn(b"date_range_from=01%2F01%2F2024", data)
        self.assertIn(b"section=BW&section=C&section=WP&section=AR&section=DI&section=USP&section=MG&Datesearch=", data)
        self.assertEqual(hdr["User-Agent"], srlc.USER_AGENT)
        c2 = self.crawler(FakeHTTP([]))
        srlc.crawl_window(c2, a, b)  # new process, same ledger: still no request
        row = c2.con.execute("SELECT status, ok, body_sha256, params FROM requests").fetchone()
        self.assertEqual((row[0], row[1]), (200, 1))
        self.assertEqual(srlc.read_raw(self.root, row[2]), ENUM_BODY)

    def test_spacing_retry_after_and_backoff(self):
        http = FakeHTTP([(429, {"retry-after": "120"}, b"slow"), (503, {}, b""), TimeoutError("t"), (200, {}, ENUM_BODY)])
        c = self.crawler(http)
        sha = c.get("k", "enum", "POST", srlc.BASE, [("a", "b")])
        self.assertTrue(sha)
        self.assertEqual(len(http.calls), 4)
        self.assertTrue(all(s >= 3.0 for s in self.sleeps))
        self.assertGreaterEqual(self.sleeps[0], 120 - 1)
        self.assertGreater(self.sleeps[2], self.sleeps[1])  # exponential
        errs = [r[0] for r in c.con.execute("SELECT error FROM requests ORDER BY id")]
        self.assertEqual(errs[0], "http 429")
        self.assertTrue(errs[2].startswith("TimeoutError"))

    def test_stop_after_10_consecutive_failures(self):
        http = FakeHTTP([(500, {}, b"")] * 50)
        c = self.crawler(http)
        c.get("a", "csv", "GET", srlc.csv_url(1))
        with self.assertRaises(srlc.StopCrawl):
            for i in range(2, 5):
                c.get(f"k{i}", "csv", "GET", srlc.csv_url(i))
        self.assertEqual(len(http.calls), 10)
        with open(os.path.join(self.root, "STOP.txt")) as f:
            self.assertIn("10 consecutive failures", f.read())

    def test_hard_cap(self):
        c = self.crawler(FakeHTTP([(200, {}, CSV)] * 5), hard_cap=2)
        c.get("a", "csv", "GET", srlc.csv_url(1))
        c.get("b", "csv", "GET", srlc.csv_url(2))
        with self.assertRaises(srlc.StopCrawl):
            c.get("c", "csv", "GET", srlc.csv_url(3))

    def test_host_restriction(self):
        c = self.crawler(FakeHTTP([]))
        with self.assertRaises(ValueError):
            c.get("x", "csv", "GET", "https://www.accessdata.fda.gov.evil.example/a")
        with self.assertRaises(ValueError):
            c.get("x", "csv", "GET", "https://www.accessdata.fda.gov/drugsatfda_docs/label/2025/x.pdf".replace("fda.gov", "fda.org"))

    def test_bad_200_body_is_failure(self):
        c = self.crawler(FakeHTTP([(200, {}, b"<html>challenge</html>")] * 5))
        self.assertIsNone(c.done_sha("x"))
        c.get("x", "csv", "GET", srlc.csv_url(1))
        self.assertIsNone(c.done_sha("x"))

    def test_month_windows(self):
        w = srlc.month_windows((2016, 1), (2016, 3))
        self.assertEqual(w[0], (dt.date(2016, 1, 1), dt.date(2016, 1, 31)))
        self.assertEqual(w[1][1], dt.date(2016, 2, 29))
        self.assertEqual(len(srlc.month_windows((2016, 1), (2026, 11))), 131)

    def test_end_to_end_gradebook_offline(self):
        http = FakeHTTP([(200, {}, ENUM_BODY), (200, {}, DETAIL), (200, {}, CSV)])
        c = self.crawler(http)
        # use the by-name page so the id list is [312]
        c.get("enum:x", "enum", "POST", srlc.BASE, [("a", "b")])
        c.con.execute("UPDATE requests SET body_sha256=?", (srlc.store_raw(self.root, fx("r_name.html")),))
        c.con.commit()
        c.get("detail:312", "detail", "GET", srlc.detail_url(312))
        c.get("csv:312", "csv", "GET", srlc.csv_url(312))
        out = srlc.build_gradebook(self.root)
        g = sqlite3.connect(out)
        self.assertEqual(g.execute("SELECT drug_name, appl_type, appl_no FROM drugs WHERE drug_name_id=312").fetchone(),
                         ("ELIQUIS", "NDA", "202155"))
        self.assertEqual(g.execute("SELECT COUNT(*) FROM supplements").fetchone()[0], 8)
        self.assertEqual(g.execute("SELECT database_updated FROM supplements WHERE suppl_ref='SUPPL-39'").fetchone()[0], "2025-05-27")
        self.assertEqual(g.execute("SELECT COUNT(*) FROM changes").fetchone()[0], 24)
        self.assertEqual(g.execute("SELECT COUNT(*) FROM csv_rows").fetchone()[0], 24)
        self.assertEqual(g.execute("SELECT COUNT(*) FROM reconcile WHERE status!='match'").fetchone()[0], 0)
        sha = g.execute("SELECT DISTINCT source_sha256 FROM changes").fetchone()[0]
        self.assertEqual(srlc.read_raw(self.root, sha), DETAIL)
        spans = json.loads(g.execute("SELECT underlined_spans_json FROM changes WHERE id=1").fetchone()[0])
        self.assertEqual(spans[0], "A specific reversal")
        # deterministic rebuild
        dump1 = list(g.iterdump())
        g.close()
        g2 = sqlite3.connect(srlc.build_gradebook(self.root))
        self.assertEqual(dump1, list(g2.iterdump()))


if __name__ == "__main__":
    unittest.main()
