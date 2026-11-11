"""Polite, resumable crawler and offline gradebook builder for the public FDA SrLC site.

Crawl: ``python -I -B srlc.py crawl --root DIR``; parse: ``... build --root DIR``.
Raw bodies are kept write-once and content-addressed; every request is in ledger.sqlite.
The gradebook is rebuilt deterministically from raw bodies only (no network).
"""
from __future__ import annotations

import argparse
import csv as _csv  # noqa: F401  (kept for callers; CSV here is malformed so a regex parser is used)
import datetime as dt
import email.utils
import gzip
import hashlib
import http.cookiejar
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
import time
import urllib.error
import urllib.parse
import urllib.request

from lxml import html as lhtml

HOST = "www.accessdata.fda.gov"
BASE = "https://www.accessdata.fda.gov/scripts/cder/safetylabelingchanges/index.cfm"
USER_AGENT = "GitRx-research/0.1 (+public SrLC validation)"
MIN_INTERVAL = 3.2
HARD_CAP = 5000
MAX_CONSECUTIVE_FAILURES = 10
MAX_ATTEMPTS_PER_ITEM = 5
BACKOFF_BASE = 15.0
SPLIT_ROW_THRESHOLD = 1000
SECTIONS = ["BW", "C", "WP", "AR", "DI", "USP", "MG"]
DEFAULT_ROOT = "/Volumes/Untitled/GitRx/srlc"


# ----------------------------------------------------------------------------- raw store
def sha256_hex(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def raw_path(root: str, sha: str) -> str:
    return os.path.join(root, "raw", "sha256", sha[:2], sha)


def store_raw(root: str, body: bytes) -> str:
    """Write-once content-addressed store: temp file + fsync + link without replace."""
    sha = sha256_hex(body)
    final = raw_path(root, sha)
    if os.path.exists(final):
        return sha
    d = os.path.dirname(final)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(tmp, final)
        except FileExistsError:
            pass
    finally:
        os.unlink(tmp)
    return sha


def read_raw(root: str, sha: str) -> bytes:
    with open(raw_path(root, sha), "rb") as f:
        return f.read()


# ----------------------------------------------------------------------------- ledger
LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS requests(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  item_key TEXT NOT NULL, kind TEXT NOT NULL,
  url TEXT NOT NULL, method TEXT NOT NULL, params TEXT,
  requested_at TEXT NOT NULL, status INTEGER, headers TEXT,
  body_sha256 TEXT, error TEXT, ok INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS requests_key ON requests(item_key, ok);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
"""


def open_ledger(root: str) -> sqlite3.Connection:
    os.makedirs(root, exist_ok=True)
    con = sqlite3.connect(os.path.join(root, "ledger.sqlite"), check_same_thread=False)
    con.executescript(LEDGER_SCHEMA)
    return con


def utcnow() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------------------- HTTP
def real_fetch(method, url, data, headers, cj=None, timeout=60):
    """Return (status, headers_dict, body). Raises OSError/URLError on transport failure."""
    cj = cj if cj is not None else http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        resp = opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        resp = e
    body = resp.read()
    hdrs = {k.lower(): v for k, v in resp.headers.items() if k.lower() != "set-cookie"}
    if hdrs.get("content-encoding", "").lower() == "gzip":
        body = gzip.decompress(body)
    return resp.status if hasattr(resp, "status") else resp.code, hdrs, body


def enum_params(d_from: dt.date, d_to: dt.date):
    p = [("date_range_from", d_from.strftime("%m/%d/%Y")), ("date_range_to", d_to.strftime("%m/%d/%Y"))]
    p += [("section", s) for s in SECTIONS]
    p.append(("Datesearch", ""))
    return p


def enum_key(d_from, d_to):
    return f"enum:{d_from.isoformat()}:{d_to.isoformat()}"


def detail_url(i):
    return f"{BASE}?event=searchdetail.page&DrugNameID={i}"


def csv_url(i):
    return f"{BASE}?event=downloadDetails.page&DrugNameID={i}"


def validate_body(kind: str, body: bytes):
    """Return an error string if the body is not the expected page type."""
    if kind == "csv":
        return None if body.lstrip(b"\xef\xbb\xbf\r\n ").startswith(b'"Section Title"') else "csv header missing"
    t = body.decode("utf-8", "replace")
    if kind == "enum":
        return None if 'id="example"' in t or "There are no results for your search" in t else "enum table missing"
    if kind == "detail":
        return None if "accordion" in t or "Safety-related Labeling Changes Approved" in t else "detail marker missing"
    return None


class StopCrawl(Exception):
    pass


class Crawler:
    def __init__(self, root, fetch=real_fetch, sleep=time.sleep, clock=time.monotonic,
                 log=None, hard_cap=HARD_CAP, min_interval=MIN_INTERVAL, lanes=1):
        self.root, self.fetch, self.sleep, self.clock = root, fetch, sleep, clock
        self.lanes = max(1, int(lanes))
        self._rate = threading.Lock()   # request-start spacing, cap reservation, global pause
        self._db = threading.RLock()    # ledger access
        self.next_slot = None           # earliest start time of the next request (multi-lane)
        self.reserved = None            # requests started or recorded (multi-lane cap accounting)
        self.stopped = None
        self.log = log or (lambda m: print(m, flush=True))
        self.hard_cap, self.min_interval = hard_cap, min_interval
        self.con = open_ledger(root)
        self.last_req = None
        self.consecutive_failures = 0
        self.session_requests = 0
        self.cj = http.cookiejar.CookieJar()

    # -- state
    def total_requests(self):
        with self._db:
            return self.con.execute("SELECT COUNT(*) FROM requests").fetchone()[0]

    def done_sha(self, key):
        with self._db:
            r = self.con.execute("SELECT body_sha256 FROM requests WHERE item_key=? AND ok=1 ORDER BY id DESC LIMIT 1", (key,)).fetchone()
        return r[0] if r else None

    def stop(self, reason):
        with self._rate:
            if self.stopped is not None:
                raise StopCrawl(self.stopped)
            self.stopped = reason
        with open(os.path.join(self.root, "STOP.txt"), "a") as f:
            f.write(f"{utcnow()} {reason}\n")
        self.log(f"STOP: {reason}")
        raise StopCrawl(reason)

    def _reserve(self, extra=0.0):
        """Multi-lane: check the cap and claim the next start slot (start-to-start spacing)."""
        with self._rate:
            if self.stopped is not None:
                raise StopCrawl(self.stopped)
            if self.reserved is None:
                self.reserved = self.total_requests()
            if self.reserved >= self.hard_cap:
                cap_hit = True
            else:
                cap_hit = False
                now = self.clock()
                start = max(now, self.next_slot or now)
                self.next_slot = start + self.min_interval
                self.reserved += 1
        if cap_hit:
            self.stop(f"hard request cap {self.hard_cap} reached")
        wait = start - self.clock()
        if wait > 0:
            self.sleep(wait)
        if extra > 0:
            self.sleep(extra)

    def _pause_all(self, seconds):
        with self._rate:
            self.next_slot = max(self.next_slot or self.clock(), self.clock() + seconds)

    def _throttle(self, extra=0.0):
        if self.last_req is not None:
            wait = self.min_interval + extra - (self.clock() - self.last_req)
            if wait > 0:
                self.sleep(wait)

    # -- one item with retries
    def get(self, key, kind, method, url, params=None):
        sha = self.done_sha(key)
        if sha:
            return sha
        if urllib.parse.urlparse(url).hostname != HOST:
            raise ValueError("host not allowed: " + url)
        extra = 0.0
        multi = self.lanes > 1
        for attempt in range(MAX_ATTEMPTS_PER_ITEM):
            if multi:
                self._reserve(extra)
            else:
                if self.total_requests() >= self.hard_cap:
                    self.stop(f"hard request cap {self.hard_cap} reached")
                self._throttle(extra)
            data = urllib.parse.urlencode(params).encode() if params else None
            headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity", "Accept": "*/*"}
            if data:
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            at = utcnow()
            self.last_req = self.clock()
            self.session_requests += 1
            status = hdrs = body = err = None
            try:
                status, hdrs, body = self.fetch(method, url, data, headers, self.cj) if self.fetch is real_fetch else self.fetch(method, url, data, headers)
            except Exception as e:  # transport failure/timeouts
                err = f"{type(e).__name__}: {e}"
            self.last_req = self.clock()
            sha = None
            ok = 0
            if err is None:
                sha = store_raw(self.root, body)
                if status == 200:
                    err = validate_body(kind, body)
                    ok = 1 if err is None else 0
                else:
                    err = f"http {status}"
            with self._db:
              self.con.execute(
                "INSERT INTO requests(item_key,kind,url,method,params,requested_at,status,headers,body_sha256,error,ok)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (key, kind, url, method, json.dumps(params) if params else None, at, status,
                 json.dumps(hdrs) if hdrs is not None else None, sha, err, ok))
              self.con.commit()
            if ok:
                self.consecutive_failures = 0
                self.log(f"ok {kind} {key} status={status} bytes={len(body)} total={self.total_requests()}")
                return sha
            self.consecutive_failures += 1
            self.log(f"FAIL {kind} {key}: {err} (consecutive={self.consecutive_failures})")
            if self.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                self.stop(f"{MAX_CONSECUTIVE_FAILURES} consecutive failures; last: {key}: {err}")
            if status is not None and 400 <= status < 500 and status not in (408, 429):
                return None  # permanent for this item
            ra = parse_retry_after((hdrs or {}).get("retry-after"))
            extra = max(ra or 0.0, min(600.0, BACKOFF_BASE * 2 ** attempt))
            if multi and status in (429, 503):
                self._pause_all(extra)  # server asked to slow down: pause every lane
            self.log(f"backoff {extra:.0f}s")
        return None


def parse_retry_after(v):
    if not v:
        return None
    v = v.strip()
    if v.isdigit():
        return float(v)
    try:
        d = email.utils.parsedate_to_datetime(v)
        return max(0.0, (d - dt.datetime.now(dt.timezone.utc)).total_seconds())
    except Exception:
        return None


# ----------------------------------------------------------------------------- crawl plan
def month_windows(start=(2016, 1), end=None):
    end = end or (dt.date.today().year, dt.date.today().month)
    y, m = start
    out = []
    while (y, m) <= end:
        first = dt.date(y, m, 1)
        nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
        out.append((first, nxt - dt.timedelta(days=1)))
        y, m = nxt.year, nxt.month
    return out


def crawl_window(c: Crawler, d_from, d_to, split=True):
    """Fetch an enumeration window; split in halves if it looks truncated."""
    key = enum_key(d_from, d_to)
    sha = c.get(key, "enum", "POST", BASE + "?event=searchResult.page", enum_params(d_from, d_to))
    if not sha:
        return
    rows = parse_results(read_raw(c.root, sha).decode("utf-8", "replace"))
    if split and len(rows) >= SPLIT_ROW_THRESHOLD and d_from < d_to:
        mid = d_from + (d_to - d_from) // 2
        c.log(f"split {key}: {len(rows)} rows")
        crawl_window(c, d_from, mid)
        crawl_window(c, mid + dt.timedelta(days=1), d_to)


def known_ids(root):
    con = open_ledger(root)
    ids = set()
    for (sha,) in con.execute("SELECT body_sha256 FROM requests WHERE kind='enum' AND ok=1"):
        for r in parse_results(read_raw(root, sha).decode("utf-8", "replace")):
            ids.add(r["drug_name_id"])
    return sorted(ids)


def run_crawl(c: Crawler, start=(2016, 1), end=None, max_ids=None, only_windows=None, validation=None):
    try:
        for a, b in (only_windows or month_windows(start, end)):
            crawl_window(c, a, b)
        for x, y in (validation if validation is not None else []):
            crawl_window(c, x, y, split=False)  # truncation cross-check: wide windows vs monthly union
        ids = known_ids(c.root)
        c.log(f"{len(ids)} DrugNameIDs known")
        # Detail pages first (authoritative, dated); per-drug CSVs second, used for reconciliation,
        # so a hard-cap stop sacrifices the redundant CSVs rather than detail pages.
        if max_ids is not None:
            ids = ids[:max_ids]
        for kind, url_of in (("detail", detail_url), ("csv", csv_url)):
            todo = [i for i in ids if not c.done_sha(f"{kind}:{i}")]
            if c.lanes == 1:
                for i in todo:
                    c.get(f"{kind}:{i}", kind, "GET", url_of(i))
            else:
                with ThreadPoolExecutor(max_workers=c.lanes) as ex:
                    futs = [ex.submit(c.get, f"{kind}:{i}", kind, "GET", url_of(i)) for i in todo]
                    for f in futs:
                        f.result()  # re-raises StopCrawl; remaining lanes see c.stopped and exit
        c.log("crawl complete")
        return True
    except StopCrawl:
        return False


def validation_windows():
    return [(dt.date(2023, 1, 1), dt.date(2023, 12, 31)), (dt.date(2016, 1, 1), dt.date.today())]


# ----------------------------------------------------------------------------- parsing
WS = re.compile(r"\s+")


def norm(s: str) -> str:
    return WS.sub(" ", (s or "").replace("\xa0", " ")).strip()


def iso_date(s):
    try:
        return dt.datetime.strptime(s.strip(), "%m/%d/%Y").date().isoformat()
    except Exception:
        return None


def parse_results(text: str):
    d = lhtml.fromstring(text)
    out = []
    for tr in d.xpath('//table[@id="example"]/tbody/tr'):
        tds = tr.xpath("./td")
        if len(tds) < 6:
            continue
        a = tds[0].xpath(".//a")
        href = a[0].get("href") if a else (norm(tds[6].text_content()) if len(tds) > 6 else "")
        m = re.search(r"DrugNameID=(\d+)", href or "")
        if not m:
            continue
        out.append({
            "drug_name_id": int(m.group(1)), "drug_name": norm(tds[0].text_content()),
            "active_ingredient": norm(tds[1].text_content()), "appl_no": norm(tds[2].text_content()),
            "appl_type": norm(tds[3].text_content()), "suppl_date_raw": norm(tds[4].text_content()),
            "suppl_date_iso": iso_date(tds[4].text_content()), "database_updated_raw": norm(tds[5].text_content()),
            "database_updated_iso": iso_date(tds[5].text_content()),
        })
    return out


def group_of(heading: str):
    h = (heading or "").lower()
    if "box" in h:
        return "BW"
    if "contraind" in h:
        return "C"
    if "pci/pi/mg" in h or "mg/pci" in h or "medication guide" in h or "patient counsel" in h or "patient information" in h:
        return "MG"
    if "specific population" in h or "pregnan" in h or "lactation" in h or "pediatric" in h or "geriatric" in h or "nursing" in h:
        return "USP"
    if "adverse" in h:
        return "AR"
    if "interaction" in h:
        return "DI"
    if "warning" in h or "precaution" in h:
        return "WP"
    return "UNMAPPED"


def desc_features(html_fragment: str):
    """Features of a description HTML fragment (from detail HTML or CSV)."""
    root = lhtml.fragment_fromstring(html_fragment or "<div></div>", create_parent="div")
    text = norm(root.text_content())

    def outer(tag):
        return [norm(e.text_content()) for e in root.iter(tag)
                if not any(a.tag == tag for a in e.iterancestors()) and norm(e.text_content())]
    return {
        "text": text, "underlined": outer("u"), "comments": outer("i"),
        "has_elision": "…" in text or "&#8230;" in html_fragment,
        "refers_to_table": bool(re.search(r"refer to (the )?label to view table", text, re.I)),
    }


def inner_html(el) -> str:
    parts = [el.text or ""]
    for ch in el:
        parts.append(lhtml.tostring(ch, encoding="unicode", method="html"))
    return "".join(parts)


def split_number_title(s: str):
    s = norm(s)
    m = re.match(r"^(\d+(?:\.\d+)*)\s+(.*)$", s)
    return (m.group(1), m.group(2)) if m else ("", s)


def parse_detail(text: str):
    d = lhtml.fromstring(text)
    head = d.xpath('//div[contains(@class,"filter-REMS")]')
    h3 = d.xpath('//div[contains(@class,"filter-REMS")]/h3')
    name = appl_type = appl_no = ingredient = ""
    if h3:
        name = norm(h3[0].xpath("string(text())"))
        sm = norm(h3[0].xpath("string(.//small)"))
        m = re.match(r"\(?([A-Za-z]+)-?\s*(\w+)\)?", sm)
        if m:
            appl_type, appl_no = m.group(1), m.group(2)
        name = norm(re.sub(r"\(.*?\)\s*$", "", norm(h3[0].text_content())))
    h5 = d.xpath('//div[contains(@class,"filter-REMS")]/h5')
    if h5:
        ingredient = norm(h5[0].text_content()).strip("()")
    supps = []
    acc = d.xpath('//div[@id="accordion"]')
    if not acc:
        return {"drug_name": name, "appl_type": appl_type, "appl_no": appl_no, "active_ingredient": ingredient, "supplements": supps}
    kids = list(acc[0])
    for idx, h in enumerate(kids):
        if h.tag != "h3":
            continue
        htxt = norm(h.text_content())
        m = re.match(r"^(\d{2}/\d{2}/\d{4})\s*(?:\((.*)\))?$", htxt)
        date_raw, ref = (m.group(1), m.group(2) or "") if m else (htxt, "")
        body = kids[idx + 1] if idx + 1 < len(kids) and kids[idx + 1].tag == "div" else None
        sup = {"date_raw": date_raw, "date_iso": iso_date(date_raw), "suppl_ref": ref, "label_pdf_url": None, "changes": []}
        if body is not None:
            a = body.xpath('./p/a[@href]')
            if a:
                sup["label_pdf_url"] = a[0].get("href")
            grp_head, grp, pending = "", "UNMAPPED", None
            for el in body:
                if el.tag == "h4":
                    grp_head = norm(el.text_content())
                    grp = group_of(grp_head)
                    pending = None
                elif el.tag == "strong":
                    pending = norm(el.text_content())
                elif el.tag == "div" and "margin-left" in (el.get("style") or ""):
                    num, title = split_number_title(pending) if pending is not None else ("", "")
                    dh = inner_html(el)
                    f = desc_features(dh)
                    sup["changes"].append({
                        "section_group": grp, "group_heading": grp_head, "subheading_raw": pending,
                        "section_number": num, "section_title": title,
                        "description_html": dh, "description_text": f["text"],
                        "underlined": f["underlined"], "comments": f["comments"],
                        "has_elision": f["has_elision"], "refers_to_table": f["refers_to_table"],
                    })
                    pending = None
        supps.append(sup)
    del head
    return {"drug_name": name, "appl_type": appl_type, "appl_no": appl_no, "active_ingredient": ingredient, "supplements": supps}


CSV_REC = re.compile(
    r'(?:^|(?<=\n))"([^"\r\n]*)","([^"\r\n]*)","(.*?)","(\d*)","([A-Za-z]*)\s*","([^"\r\n]*)","([^"\r\n]*)","(\d*)","([A-Za-z]*)\s*","([^"\r\n]*)","([^"\r\n]*)"[ \t\r]*(?=\n|\Z)',
    re.S)


def parse_download_csv(body: bytes):
    """Regex record parser: the FDA CSV has unescaped quotes inside descriptions, so csv.reader mis-splits it.
    Returns (rows, unparsed_chars)."""
    text = body.decode("utf-8-sig", "replace")
    hm = re.match(r'\s*"Section Title","Title","Description"[^\n]*\n', text)
    text = text[hm.end():] if hm else text
    rows, residual, pos = [], [], 0
    for m in CSV_REC.finditer(text):
        residual.append(text[pos:m.start()])
        pos = m.end()
        rows.append({
            "section_title_raw": m.group(1), "title": m.group(2), "description_html": m.group(3),
            "appl_no": m.group(4), "appl_type": m.group(5), "drug_name": m.group(6),
            "active_ingredient": m.group(7), "sub_no": m.group(8), "sub_type": m.group(9),
            "url": m.group(10), "url_text": m.group(11), "section_group": group_of(m.group(1)),
        })
    residual.append(text[pos:])
    return rows, len("".join(residual).strip())


def suppl_numbers(ref: str):
    return sorted({int(n) for n in re.findall(r"-\s*(\d+)", ref or "")}) or sorted({int(n) for n in re.findall(r"(\d+)", ref or "")})


# ----------------------------------------------------------------------------- gradebook
GRADE_SCHEMA = """
CREATE TABLE drugs(drug_name_id INTEGER PRIMARY KEY, drug_name TEXT, active_ingredient TEXT, appl_type TEXT, appl_no TEXT, detail_sha256 TEXT, csv_sha256 TEXT);
CREATE TABLE search_hits(window_key TEXT, drug_name_id INTEGER, drug_name TEXT, active_ingredient TEXT, appl_no TEXT, appl_type TEXT,
  suppl_date_raw TEXT, suppl_date_iso TEXT, database_updated_raw TEXT, database_updated_iso TEXT, source_sha256 TEXT);
CREATE TABLE supplements(supplement_id INTEGER PRIMARY KEY, drug_name_id INTEGER, approval_date_raw TEXT, approval_date_iso TEXT,
  suppl_ref TEXT, suppl_no TEXT, label_pdf_url TEXT, database_updated TEXT, source_sha256 TEXT);
CREATE TABLE changes(id INTEGER PRIMARY KEY, drug_name_id INTEGER, supplement_id INTEGER, suppl_ref TEXT, section_group TEXT,
  group_heading TEXT, subheading_raw TEXT, section_number TEXT, section_title TEXT, description_html TEXT, description_text TEXT,
  underlined_spans_json TEXT, comment_spans_json TEXT, has_elision INTEGER, refers_to_table INTEGER, source_sha256 TEXT);
CREATE TABLE csv_rows(id INTEGER PRIMARY KEY, drug_name_id INTEGER, section_group TEXT, section_title_raw TEXT, title TEXT,
  description_html TEXT, description_text TEXT, appl_no TEXT, appl_type TEXT, sub_no TEXT, sub_type TEXT, url TEXT, source_sha256 TEXT);
CREATE TABLE reconcile(drug_name_id INTEGER, sub_no TEXT, section_group TEXT, html_n INTEGER, csv_n INTEGER, text_match_n INTEGER, status TEXT);
CREATE TABLE build_info(k TEXT PRIMARY KEY, v TEXT);
"""


def _latest_ok(con):
    """Latest successful request per item_key, ordered deterministically."""
    return con.execute(
        "SELECT item_key, kind, body_sha256 FROM requests WHERE ok=1 AND id IN "
        "(SELECT MAX(id) FROM requests WHERE ok=1 GROUP BY item_key) ORDER BY item_key").fetchall()


def build_gradebook(root: str, out_path: str | None = None):
    out_path = out_path or os.path.join(root, "gradebook.sqlite")
    tmp = out_path + ".building"
    if os.path.exists(tmp):
        os.unlink(tmp)
    led = open_ledger(root)
    gb = sqlite3.connect(tmp)
    gb.executescript(GRADE_SCHEMA)
    items = _latest_ok(led)
    hits = {}  # drug_id -> list of search hits
    details, csvs = {}, {}
    for key, kind, sha in items:
        if kind == "enum":
            for r in parse_results(read_raw(root, sha).decode("utf-8", "replace")):
                gb.execute("INSERT INTO search_hits VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                    key, r["drug_name_id"], r["drug_name"], r["active_ingredient"], r["appl_no"], r["appl_type"],
                    r["suppl_date_raw"], r["suppl_date_iso"], r["database_updated_raw"], r["database_updated_iso"], sha))
                hits.setdefault(r["drug_name_id"], []).append(r)
        elif kind == "detail":
            details[int(key.split(":")[1])] = sha
        elif kind == "csv":
            csvs[int(key.split(":")[1])] = sha
    unmapped = 0
    csv_unparsed = {}
    cid = sid = chid = 0
    for did in sorted(set(details) | set(csvs) | set(hits)):
        det = parse_detail(read_raw(root, details[did]).decode("utf-8", "replace")) if did in details else None
        h0 = hits.get(did, [{}])[0]
        gb.execute("INSERT INTO drugs VALUES(?,?,?,?,?,?,?)", (
            did, (det or {}).get("drug_name") or h0.get("drug_name"), (det or {}).get("active_ingredient") or h0.get("active_ingredient"),
            (det or {}).get("appl_type") or h0.get("appl_type"), (det or {}).get("appl_no") or h0.get("appl_no"),
            details.get(did), csvs.get(did)))
        html_counts = {}
        html_texts = {}
        if det:
            for s in det["supplements"]:
                sid += 1
                dbu = next((r["database_updated_iso"] for r in hits.get(did, [])
                            if r["suppl_date_iso"] == s["date_iso"] and r["database_updated_iso"]), None)
                nums = suppl_numbers(s["suppl_ref"])
                gb.execute("INSERT INTO supplements VALUES(?,?,?,?,?,?,?,?,?)", (
                    sid, did, s["date_raw"], s["date_iso"], s["suppl_ref"], ",".join(map(str, nums)),
                    s["label_pdf_url"], dbu, details[did]))
                for ch in s["changes"]:
                    chid += 1
                    if ch["section_group"] == "UNMAPPED":
                        unmapped += 1
                    gb.execute("INSERT INTO changes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                        chid, did, sid, s["suppl_ref"], ch["section_group"], ch["group_heading"], ch["subheading_raw"],
                        ch["section_number"], ch["section_title"], ch["description_html"], ch["description_text"],
                        json.dumps(ch["underlined"], ensure_ascii=False), json.dumps(ch["comments"], ensure_ascii=False),
                        int(ch["has_elision"]), int(ch["refers_to_table"]), details[did]))
                    for n in (nums or [None]):
                        k = (str(n) if n is not None else "", ch["section_group"])
                        html_counts[k] = html_counts.get(k, 0) + 1
                        html_texts.setdefault(k, []).append(ch["description_text"])
        csv_counts, csv_texts = {}, {}
        if did in csvs:
            rows, unp = parse_download_csv(read_raw(root, csvs[did]))
            csv_unparsed[did] = unp
            for r in rows:
                cid += 1
                f = desc_features(r["description_html"])
                gb.execute("INSERT INTO csv_rows VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    cid, did, r["section_group"], r["section_title_raw"], r["title"], r["description_html"], f["text"],
                    r["appl_no"], r["appl_type"], r["sub_no"], r["sub_type"], r["url"], csvs[did]))
                k = (str(int(r["sub_no"])) if r["sub_no"].isdigit() else r["sub_no"], r["section_group"])
                csv_counts[k] = csv_counts.get(k, 0) + 1
                csv_texts.setdefault(k, []).append(f["text"])
        if did in details and did in csvs:
            for k in sorted(set(html_counts) | set(csv_counts)):
                hn, cn = html_counts.get(k, 0), csv_counts.get(k, 0)
                ht, ct = list(html_texts.get(k, [])), list(csv_texts.get(k, []))
                tm = 0
                for t in ht:
                    if t in ct:
                        ct.remove(t)
                        tm += 1
                gb.execute("INSERT INTO reconcile VALUES(?,?,?,?,?,?,?)", (
                    did, k[0], k[1], hn, cn, tm, "match" if hn == cn else "count_mismatch"))
    gb.execute("INSERT INTO build_info VALUES('unmapped_group_changes',?)", (str(unmapped),))
    gb.execute("INSERT INTO build_info VALUES('csv_unparsed_chars_total',?)", (str(sum(csv_unparsed.values())),))
    gb.execute("INSERT INTO build_info VALUES('csv_files_with_unparsed',?)", (json.dumps(sorted(d for d, u in csv_unparsed.items() if u > 0)),))
    gb.commit()
    gb.close()
    if os.path.exists(out_path):
        os.unlink(out_path)
    os.replace(tmp, out_path)
    return out_path


def gradebook_stats(root):
    led = sqlite3.connect(os.path.join(root, "ledger.sqlite"))
    g = sqlite3.connect(os.path.join(root, "gradebook.sqlite"))
    q = lambda c, sql: c.execute(sql).fetchall()
    return {
        "requests_total": q(led, "SELECT COUNT(*) FROM requests")[0][0],
        "requests_by_kind_ok": q(led, "SELECT kind, ok, COUNT(*) FROM requests GROUP BY kind, ok"),
        "failed_requests": q(led, "SELECT item_key, status, error, requested_at FROM requests WHERE ok=0"),
        "first_last_request": q(led, "SELECT MIN(requested_at), MAX(requested_at) FROM requests"),
        "drugs": q(g, "SELECT COUNT(*) FROM drugs")[0][0],
        "drugs_with_detail": q(g, "SELECT COUNT(*) FROM drugs WHERE detail_sha256 IS NOT NULL")[0][0],
        "drugs_with_csv": q(g, "SELECT COUNT(*) FROM drugs WHERE csv_sha256 IS NOT NULL")[0][0],
        "supplements": q(g, "SELECT COUNT(*) FROM supplements")[0][0],
        "supplement_date_range": q(g, "SELECT MIN(approval_date_iso), MAX(approval_date_iso) FROM supplements"),
        "supplements_missing_date": q(g, "SELECT COUNT(*) FROM supplements WHERE approval_date_iso IS NULL")[0][0],
        "search_hit_date_range": q(g, "SELECT MIN(suppl_date_iso), MAX(suppl_date_iso) FROM search_hits"),
        "changes_by_group": q(g, "SELECT section_group, COUNT(*) FROM changes GROUP BY 1 ORDER BY 1"),
        "changes_total": q(g, "SELECT COUNT(*) FROM changes")[0][0],
        "underlined_spans": q(g, "SELECT SUM(json_array_length(underlined_spans_json)) FROM changes")[0][0],
        "changes_with_underline": q(g, "SELECT COUNT(*) FROM changes WHERE json_array_length(underlined_spans_json)>0")[0][0],
        "changes_without_subheading": q(g, "SELECT COUNT(*) FROM changes WHERE section_number=''")[0][0],
        "elision_changes": q(g, "SELECT COUNT(*) FROM changes WHERE has_elision=1")[0][0],
        "table_ref_changes": q(g, "SELECT COUNT(*) FROM changes WHERE refers_to_table=1")[0][0],
        "csv_rows": q(g, "SELECT COUNT(*) FROM csv_rows")[0][0],
        "reconcile_status": q(g, "SELECT status, COUNT(*), SUM(html_n), SUM(csv_n), SUM(text_match_n) FROM reconcile GROUP BY 1"),
        "reconcile_mismatch_drugs": q(g, "SELECT COUNT(DISTINCT drug_name_id) FROM reconcile WHERE status!='match'")[0][0],
        "reconcile_mismatch_examples": q(g, "SELECT * FROM reconcile WHERE status!='match' LIMIT 15"),
        "build_info": q(g, "SELECT k, v FROM build_info"),
        "detail_pages_without_supplements": q(g, "SELECT COUNT(*) FROM drugs d WHERE detail_sha256 IS NOT NULL AND NOT EXISTS (SELECT 1 FROM supplements s WHERE s.drug_name_id=d.drug_name_id)")[0][0],
        "group_headings_unmapped": q(g, "SELECT group_heading, COUNT(*) FROM changes WHERE section_group='UNMAPPED' GROUP BY 1 LIMIT 20"),
    }


# ----------------------------------------------------------------------------- CLI
def _logger(root):
    path = os.path.join(root, "crawl.log")

    def log(m):
        line = f"{utcnow()} {m}"
        with open(path, "a") as f:
            f.write(line + "\n")
        print(line, flush=True)
    return log


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cmd", choices=["crawl", "dry-run", "validate-enum", "build", "stats"])
    ap.add_argument("--root", default=DEFAULT_ROOT)
    ap.add_argument("--min-interval", type=float, default=MIN_INTERVAL,
                    help="seconds between request starts (multi-lane) / between requests (1 lane)")
    ap.add_argument("--lanes", type=int, default=1, help="parallel request lanes")
    a = ap.parse_args(argv)
    os.makedirs(a.root, exist_ok=True)
    log = _logger(a.root)
    if a.cmd == "build":
        print(build_gradebook(a.root))
        return 0
    if a.cmd == "stats":
        print(json.dumps(gradebook_stats(a.root), indent=1, default=str))
        return 0
    with open(os.path.join(a.root, "crawl.pid"), "w") as f:
        f.write(str(os.getpid()))
    c = Crawler(a.root, log=log, min_interval=a.min_interval, lanes=a.lanes)
    log(f"crawler start: lanes={c.lanes} min_interval={c.min_interval}s cap={c.hard_cap}")
    if a.cmd == "dry-run":
        # month 2024-01 plus two half-month windows (truncation check), then two details+CSVs
        w = [(dt.date(2024, 1, 1), dt.date(2024, 1, 31)), (dt.date(2024, 1, 1), dt.date(2024, 1, 15)),
             (dt.date(2024, 1, 16), dt.date(2024, 1, 31))]
        for x, y in w:
            crawl_window(c, x, y)
        ids = known_ids(a.root)[:2]
        for i in ids:
            c.get(f"detail:{i}", "detail", "GET", detail_url(i))
            c.get(f"csv:{i}", "csv", "GET", csv_url(i))
        log(f"dry run done; requests={c.total_requests()}")
        return 0
    if a.cmd == "validate-enum":
        for x, y in validation_windows():
            crawl_window(c, x, y, split=False)
        return 0
    ok = run_crawl(c, validation=validation_windows())
    if ok:
        with open(os.path.join(a.root, "COMPLETE.txt"), "w") as f:
            f.write(utcnow() + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
