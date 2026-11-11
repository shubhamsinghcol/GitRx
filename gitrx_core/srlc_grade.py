"""SrLC -> DailyMed mapping and grading harness (Step D of docs/AUTHORITATIVE-PARSER-PLAN.md).

Metric definitions are FIXED by "Pre-registered validation" and "Amendment 1" of that plan; this module only
operationalizes them.  Reads are read-only: the evidence DB is opened ?mode=ro, the SrLC crawl root is never
written, and nothing here triggers collection or release promotion.

Pipeline
  index   evidence DB -> cache/index.sqlite : versions (sha, publication date) + application numbers found in
          the SPL XML (manufacturedProduct .. approval/id@extension, e.g. NDA021234, BLA125057) of the latest
          captured version of every prescription/biologic setid.
  grade   gradebook (open split only) x index -> bracketing pairs -> parse both versions (spl_parser) and
          compare (changes.compare_docs), cached by raw sha256 + parser version -> section / text recall,
          miss categories, coverage exclusions, GitRx-only changes.

Split safety: `--split open` is the default and the only mode unless `--sealed-final --parser-version X
--frozen-parser-marker FILE` is given; sealed mode refuses without a marker naming exactly that frozen
parser version.  Sealed drugs are never read in open mode.
"""
import argparse
import collections
import concurrent.futures as cf
import datetime as dt
import gzip
import hashlib
import html
import json
import math
import os
import re
import sqlite3
import sys
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gitrx_core.normalize import normalize  # noqa: E402

HARNESS_VERSION = "srlc-grade-0.1.0"
WINDOW_BEFORE_DAYS = 30
WINDOW_AFTER_DAYS = 365
MIN_FRAGMENT_TOKENS = 3
SRLC_GROUPS = ("BW", "C", "WP", "AR", "DI", "USP", "MG")
CHANNELS = ("text", "table", "excerpt")
DEFAULT_EVIDENCE = "/Volumes/Untitled/GitRx/evidence/evidence-v1.sqlite"
DEFAULT_SRLC_ROOT = "/Volumes/Untitled/GitRx/srlc"
BODIES_DIR = "/Volumes/Untitled/GitRx/backfill-20261107/network/bodies"
# document types that can carry SrLC applications (Rx human + biologics); unknown/missing type is kept
RX_DOC_CODES = {"34391-3", "45129-4", "60683-0", "53404-0", "53405-7", "60684-8", "60682-2", "53408-1",
                "78745-7", "53406-5"}
MISS_CATEGORIES = ("parser", "alignment", "mapping", "version bracketing", "SrLC-side inconsistency",
                   "missing DailyMed version", "uncategorized")


# ----------------------------------------------------------------------------- pure helpers

def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def wilson(k, n, z=1.959963984540054):
    """Wilson score 95% interval for k successes of n; (None, None) when n == 0."""
    if n == 0:
        return (None, None)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def rate(k, n):
    lo, hi = wilson(k, n)
    return {"k": k, "n": n, "recall": (k / n if n else None), "wilson95": [lo, hi]}


def parse_date(s):
    if not s:
        return None
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def in_window(pub_date, approval_date):
    """Amendment 1 bracketing: pub date in [approval - 30 d, approval + 365 d], both ends inclusive."""
    p, a = parse_date(pub_date), parse_date(approval_date)
    if p is None or a is None:
        return False
    return a - dt.timedelta(days=WINDOW_BEFORE_DAYS) <= p <= a + dt.timedelta(days=WINDOW_AFTER_DAYS)


def split_fragments(underlined_spans):
    """Amendment 1: split each underlined span at the ellipsis, literal-v1 normalize, keep (text, n_tokens).

    Returns every non-empty piece; callers keep pieces with >= MIN_FRAGMENT_TOKENS tokens in the 95% bar."""
    out = []
    for span in underlined_spans or []:
        for piece in re.split("…", span or ""):
            n = normalize(piece)
            if n:
                out.append({"text": n, "tokens": len(n.split(" "))})
    return out


_TOK = re.compile(r"\S+")


def norm_with_tokens(raw, added_intervals=()):
    """Normalized (literal-v1) text of a channel as space-joined normalized tokens, with, per token, its
    [start, end) position in that string and whether it overlaps a GitRx added char interval of the raw text."""
    parts, toks, pos = [], [], 0
    for m in _TOK.finditer(raw):
        t = normalize(m.group())
        if not t:
            continue
        if parts:
            pos += 1
        added = any(m.start() < e and m.end() > s for s, e in added_intervals)
        toks.append((pos, pos + len(t), added))
        parts.append(t)
        pos += len(t)
    return " ".join(parts), toks


def find_fragment(fragment, norm_text, toks):
    """-> (verbatim, overlaps_added_token) for a normalized fragment in a normalized channel text."""
    verbatim = overlap = False
    start = 0
    while True:
        i = norm_text.find(fragment, start)
        if i < 0:
            break
        verbatim = True
        j = i + len(fragment)
        if any(a and s < j and e > i for s, e, a in toks):
            overlap = True
            break
        start = i + 1
    return verbatim, overlap


def check_split_mode(args):
    """`--split open` default; sealed only with --sealed-final, a matching --parser-version and a frozen marker."""
    if not args.sealed_final:
        if args.split != "open":
            raise SystemExit("refusing: only --split open is allowed without --sealed-final")
        return "open"
    if not args.parser_version or not args.frozen_parser_marker:
        raise SystemExit("refusing sealed grading: --sealed-final needs --parser-version and --frozen-parser-marker")
    if not os.path.isfile(args.frozen_parser_marker):
        raise SystemExit("refusing sealed grading: frozen-parser marker file not found")
    try:
        marker = json.load(open(args.frozen_parser_marker))
    except (OSError, ValueError):
        raise SystemExit("refusing sealed grading: frozen-parser marker is not valid JSON")
    if marker.get("frozen_parser_version") != args.parser_version:
        raise SystemExit("refusing sealed grading: marker does not freeze the requested parser version")
    from gitrx_core import spl_parser
    if spl_parser.PARSER_VERSION != args.parser_version:
        raise SystemExit("refusing sealed grading: installed parser is %s, not %s"
                         % (spl_parser.PARSER_VERSION, args.parser_version))
    return "sealed"


# ----------------------------------------------------------------------------- raw artifact locations

class Resolver:
    """sha256 -> path of the raw ZIP.  Order: flat network/bodies, recorded parent raw_path, raw manifest copies."""

    def __init__(self, extra_paths=None, manifest=None, bodies=BODIES_DIR):
        self.extra = extra_paths or {}
        self.bodies = bodies
        self.manifest = manifest

    def __call__(self, sha):
        p = os.path.join(self.bodies, sha)
        if os.path.exists(p):
            return p
        p = self.extra.get(sha)
        if p and os.path.exists(p):
            return p
        if self.manifest:
            con = sqlite3.connect("file:%s?mode=ro" % self.manifest, uri=True)
            try:
                for (q,) in con.execute("SELECT path FROM copies WHERE sha256=?", (sha,)):
                    if os.path.exists(q):
                        return q
            finally:
                con.close()
        return None


def read_xml_from_zip(path, member=None):
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if member and member in names:
            return z.read(member)
        xmls = [n for n in names if n.lower().endswith(".xml")]
        if len(xmls) == 1:
            return z.read(xmls[0])
        if member is None and xmls:
            return z.read(sorted(xmls)[0])
        raise KeyError("xml member %r not in zip (%d xml files)" % (member, len(xmls)))


_APPROVAL = re.compile(rb"<approval\b[^>]*>\s*<id\b([^>]*)>", re.S)
_ATTR = re.compile(rb'(\w+)\s*=\s*"([^"]*)"')


def approvals_in_xml(xml_bytes):
    """Application numbers (e.g. NDA021234, BLA125057, ANDA065222) from product approval ids."""
    out = set()
    for m in _APPROVAL.finditer(xml_bytes):
        attrs = {k.decode(): v.decode("utf-8", "replace") for k, v in _ATTR.findall(m.group(1))}
        ext = re.sub(r"\s+", "", attrs.get("extension", "")).upper()
        if ext:
            out.add(ext)
    return out


def _scan_task(task):
    setid, version, sha, member, path = task
    try:
        return setid, version, sorted(approvals_in_xml(read_xml_from_zip(path, member))), None
    except Exception as exc:  # noqa: BLE001 - recorded, not hidden
        return setid, version, [], "%s: %s" % (type(exc).__name__, exc)


# ----------------------------------------------------------------------------- index (evidence -> appl -> setids)

INDEX_SCHEMA = """
CREATE TABLE versions(setid TEXT, version INTEGER, sha256 TEXT, xml_member TEXT, pub_date TEXT, effective_date TEXT,
  rx INTEGER, PRIMARY KEY(setid, version)) WITHOUT ROWID;
CREATE TABLE paths(sha256 TEXT PRIMARY KEY, path TEXT) WITHOUT ROWID;
CREATE TABLE approvals(setid TEXT, version INTEGER, appl TEXT, PRIMARY KEY(setid, version, appl)) WITHOUT ROWID;
CREATE TABLE scanned(setid TEXT, version INTEGER, error TEXT, PRIMARY KEY(setid, version)) WITHOUT ROWID;
CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT) WITHOUT ROWID;
CREATE INDEX approvals_appl ON approvals(appl);
"""


def _evidence_ro(path):
    return sqlite3.connect("file:%s?mode=ro" % path, uri=True)


def build_index(evidence_db, index_path, workers=6, raw_manifest=None, log=print, limit=None, scan_all_versions=False):
    """Build/extend the appl->setid index.  Resumable: versions already scanned are skipped."""
    fresh = not os.path.exists(index_path)
    os.makedirs(os.path.dirname(index_path), exist_ok=True)
    ix = sqlite3.connect(index_path)
    if fresh:
        ix.executescript(INDEX_SCHEMA)
        ev = _evidence_ro(evidence_db)
        t0 = time.time()
        # an (setid, version) is rx if any observation says so; recompute precisely
        rxset = {(str(a).lower(), str(b)) for a, b in ev.execute(
            "SELECT xml_setid, xml_version FROM document_type_observations WHERE code IN (%s)"
            % ",".join("'%s'" % c for c in sorted(RX_DOC_CODES)))}
        anyobs = {(str(a).lower(), str(b)) for a, b in ev.execute(
            "SELECT DISTINCT xml_setid, xml_version FROM document_type_observations")}
        pubs = {}
        for s, v, d in ev.execute("SELECT setid, version, published_date FROM history_entries"):
            pubs[(s, int(v))] = d
        rows = {}
        for s, v, sha, mem, eff in ev.execute(
                "SELECT setid, version, selected_sha256, xml_member, effective_date FROM versions"):
            k = (s, int(v))
            old = rows.get(k)
            if old is None or (not old[0] and sha):
                rows[k] = (sha, mem, eff)
        pubs_only = set(pubs) - set(rows)
        for k, (sha, mem, eff) in rows.items():
            kk = (k[0].lower(), str(k[1]))
            rx = 1 if (kk in rxset or kk not in anyobs) else 0
            ix.execute("INSERT INTO versions VALUES(?,?,?,?,?,?,?)", (k[0], k[1], sha or None, mem, pubs.get(k), eff, rx))
        for k in pubs_only:
            ix.execute("INSERT INTO versions VALUES(?,?,?,?,?,?,?)", (k[0], k[1], None, None, pubs[k], None, 1))
        extra = 0
        for (rj,) in ev.execute("SELECT reference_json_without_parse_runs FROM backfill_parent_refs"):
            try:
                j = json.loads(rj)
            except ValueError:
                continue
            rp = j.get("raw_path")
            if rp:
                ix.execute("INSERT OR IGNORE INTO paths VALUES(?,?)", (os.path.basename(rp), rp))
                extra += 1
        ev.close()
        vj = os.path.join(os.path.dirname(evidence_db), "VERIFY.json")
        ix.execute("INSERT INTO meta VALUES('evidence_db', ?)", (evidence_db,))
        ix.execute("INSERT INTO meta VALUES('evidence_db_bytes', ?)", (str(os.path.getsize(evidence_db)),))
        if os.path.exists(vj):
            ix.execute("INSERT INTO meta VALUES('evidence_verify_json_sha256', ?)", (sha256_file(vj),))
            try:
                ix.execute("INSERT INTO meta VALUES('evidence_db_sha256_per_verify_json', ?)",
                           (str(json.load(open(vj)).get("sha256") or json.load(open(vj)).get("db_sha256")),))
            except Exception:  # noqa: BLE001
                pass
        ix.execute("INSERT INTO meta VALUES('built_at', ?)", (dt.datetime.now(dt.timezone.utc).isoformat(),))
        ix.execute("INSERT INTO meta VALUES('scope', ?)", ("all_versions" if scan_all_versions else "latest_captured_version_per_setid",))
        ix.commit()
        log("index: %d versions (%.0fs), %d parent paths" % (len(rows) + len(pubs_only), time.time() - t0, extra))
    # scan targets
    if scan_all_versions:
        q = "SELECT setid, version, sha256, xml_member FROM versions WHERE sha256 IS NOT NULL AND rx=1"
    else:
        q = ("SELECT v.setid, v.version, v.sha256, v.xml_member FROM versions v JOIN "
             "(SELECT setid, MAX(version) mv FROM versions WHERE sha256 IS NOT NULL GROUP BY setid) m "
             "ON m.setid=v.setid AND m.mv=v.version WHERE v.rx=1")
    done = {(s, v) for s, v in ix.execute("SELECT setid, version FROM scanned")}
    extra_paths = dict(ix.execute("SELECT sha256, path FROM paths"))
    resolve = Resolver(extra_paths, raw_manifest)
    targets = [r for r in ix.execute(q).fetchall() if (r[0], r[1]) not in done]
    if limit:
        targets = targets[:limit]
    log("index: scanning %d versions with %d workers" % (len(targets), workers))
    tasks, unresolved = [], 0
    for s, v, sha, mem in targets:
        p = resolve(sha)
        if p is None:
            unresolved += 1
            ix.execute("INSERT OR REPLACE INTO scanned VALUES(?,?,?)", (s, v, "raw_not_found"))
        else:
            tasks.append((s, v, sha, mem, p))
    ix.commit()
    log("index: %d tasks, %d raw ZIPs not found" % (len(tasks), unresolved))
    t0 = time.time()
    with cf.ProcessPoolExecutor(workers) as ex:
        for i, (s, v, apps, err) in enumerate(ex.map(_scan_task, tasks, chunksize=64), 1):
            for a in apps:
                ix.execute("INSERT OR IGNORE INTO approvals VALUES(?,?,?)", (s, v, a))
            ix.execute("INSERT OR REPLACE INTO scanned VALUES(?,?,?)", (s, v, err))
            if i % 5000 == 0:
                ix.commit()
                log("index: scanned %d/%d (%.0fs)" % (i, len(tasks), time.time() - t0))
    ix.commit()
    ix.close()
    return index_path


# ----------------------------------------------------------------------------- pair digests (parse + compare)

def _channel_intervals(change):
    iv = collections.defaultdict(list)
    for sp in change.get("added_spans", []):
        iv[sp["channel"]].append([sp["char_start"], sp["char_end"]])
    return dict(iv)


def _strip_xml_text(xml_bytes):
    s = xml_bytes.decode("utf-8", "replace")
    s = re.sub(r"<[^>]+>", " ", s)
    return normalize(html.unescape(s))


def _sections_digest(doc):
    from gitrx_core.changes import channels
    from gitrx_core.section_groups import classify
    out = []
    for sec in doc.get("sections", []):
        ch = {k: v for k, v in channels(sec).items() if v.strip()}
        out.append({"o": sec["ordinal"], "group": classify(sec)["group"],
                    "number": sec.get("section_number"), "title": sec.get("title_normalized"), "ch": ch})
    return out


def make_digest(a_xml, b_xml, a_sha, b_sha):
    """Parse two versions with the installed parser and compare; returns the cacheable digest dict."""
    from gitrx_core import changes, spl_parser
    base = {"harness": HARNESS_VERSION, "parser_version": spl_parser.PARSER_VERSION,
            "changes_version": changes.CHANGES_VERSION, "from_sha256": a_sha, "to_sha256": b_sha}
    try:
        a, b = spl_parser.parse(a_xml), spl_parser.parse(b_xml)
        rec = changes.compare_docs(a, b, a_sha, b_sha)
    except Exception as exc:  # noqa: BLE001
        return {**base, "comparison": "error", "reasons": ["%s: %s" % (type(exc).__name__, exc)], "changes": [],
                "to_sections": [], "from_sections": [], "xml_text_to": _strip_xml_text(b_xml)}
    ch = []
    for c in rec["changes"]:
        ch.append({"kind": c["kind"], "group": c["srlc_group"], "ordinal_to": c.get("ordinal_to"),
                   "ordinal_from": c.get("ordinal_from"), "section_number": c.get("section_number"),
                   "section_title": c.get("section_title"), "table_changed": c.get("table_changed"),
                   "added": _channel_intervals(c)})
    fmt = [{"group": c["srlc_group"], "ordinal_to": c.get("ordinal_to"), "type": c.get("type")}
           for c in rec["formatting_changes"]]
    return {**base, "comparison": rec["comparison"], "reasons": rec["comparison_reasons"], "changes": ch,
            "formatting_changes": fmt, "record_sha256": rec["record_sha256"],
            "to_status": b.get("status"), "from_status": a.get("status"),
            "to_sections": _sections_digest(b), "from_sections": _sections_digest(a),
            "xml_text_to": _strip_xml_text(b_xml)}


def digest_key(a_sha, b_sha, parser_version, changes_version):
    return hashlib.sha256(("%s|%s|%s|%s" % (a_sha, b_sha, parser_version, changes_version)).encode()).hexdigest()


def _digest_task(task):
    """task = (cache_dir, [(a_sha, a_member, a_path, b_sha, b_member, b_path), ...])  one setid chain."""
    from gitrx_core import changes, spl_parser
    cache_dir, pairs = task
    xml_cache, results = {}, []

    def xml(sha, member, path):
        if sha not in xml_cache:
            xml_cache.clear() if len(xml_cache) > 3 else None
            xml_cache[sha] = read_xml_from_zip(path, member)
        return xml_cache[sha]
    for a_sha, a_mem, a_path, b_sha, b_mem, b_path in pairs:
        key = digest_key(a_sha, b_sha, spl_parser.PARSER_VERSION, changes.CHANGES_VERSION)
        fp = os.path.join(cache_dir, key[:2], key + ".json.gz")
        if os.path.exists(fp):
            results.append((key, "cached"))
            continue
        try:
            d = make_digest(xml(a_sha, a_mem, a_path), xml(b_sha, b_mem, b_path), a_sha, b_sha)
        except Exception as exc:  # noqa: BLE001 - e.g. unreadable zip; recorded, not cached
            results.append((key, "error: %s: %s" % (type(exc).__name__, exc)))
            continue
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with gzip.open(fp + ".tmp", "wt", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(fp + ".tmp", fp)
        results.append((key, "built"))
    return results


def load_digest(cache_dir, key):
    fp = os.path.join(cache_dir, key[:2], key + ".json.gz")
    if not os.path.exists(fp):
        return None
    with gzip.open(fp, "rt", encoding="utf-8") as f:
        return json.load(f)


# ----------------------------------------------------------------------------- matching on digests

class DigestView:
    """Memoized normalized channel texts of one digest side."""

    def __init__(self, digest):
        self.d = digest
        self._n = {}
        self.changed = {}
        for c in digest["changes"]:
            if c["kind"] in ("modified", "added_section") and c.get("ordinal_to") is not None:
                self.changed[c["ordinal_to"]] = c

    def to_norm(self, sec, ch):
        key = ("to", sec["o"], ch)
        if key not in self._n:
            c = self.changed.get(sec["o"])
            iv = (c or {}).get("added", {}).get(ch, [])
            self._n[key] = norm_with_tokens(sec["ch"].get(ch, ""), iv)
        return self._n[key]

    def from_norm(self, sec, ch):
        key = ("from", sec["o"], ch)
        if key not in self._n:
            self._n[key] = norm_with_tokens(sec["ch"].get(ch, ""))
        return self._n[key]

    def has_group_change(self, group):
        return any(c["group"] == group for c in self.d["changes"])

    def fragment_status(self, frag, group):
        """Amendment 1 text criterion plus diagnostics for categorization.

        found: verbatim in a changed same-group to-section AND overlapping an added token.
        Other keys describe where the fragment was seen when not found."""
        res = {"found": False, "verbatim_changed_same_group": False, "in_unchanged_same_group": False,
               "in_other_group": False, "in_from_same_group": False, "in_from_any": False, "in_xml_text": False}
        for sec in self.d["to_sections"]:
            for ch in sec["ch"]:
                nt, toks = self.to_norm(sec, ch)
                v, ov = find_fragment(frag, nt, toks)
                if not v:
                    continue
                if sec["group"] == group:
                    if sec["o"] in self.changed:
                        res["verbatim_changed_same_group"] = True
                        if ov:
                            res["found"] = True
                            return res
                    else:
                        res["in_unchanged_same_group"] = True
                else:
                    res["in_other_group"] = True
        for sec in self.d["from_sections"]:
            for ch in sec["ch"]:
                nt, toks = self.from_norm(sec, ch)
                if find_fragment(frag, nt, toks)[0]:
                    res["in_from_any"] = True
                    if sec["group"] == group:
                        res["in_from_same_group"] = True
        res["in_xml_text"] = frag in self.d.get("xml_text_to", "")
        return res


def categorize_fragment(stats, from_pub_date, approval_date):
    """stats: list of fragment_status dicts, one per in-window pair of the best candidate.  Returns
    (category, detail).  Heuristics are conservative and recorded; anything else is 'uncategorized'."""
    if any(s["found"] for s in stats):
        return None, None
    if not stats:
        return "missing DailyMed version", "no comparable in-window pair"
    # prefer the most informative status across pairs
    for s in stats:
        if s["verbatim_changed_same_group"] and s["in_from_same_group"]:
            return _pre_existing(from_pub_date, approval_date)
    for s in stats:
        if s["verbatim_changed_same_group"]:
            return "alignment", "fragment in changed same-group section but not within GitRx added tokens"
    for s in stats:
        if s["in_unchanged_same_group"] and s["in_from_any"]:
            return _pre_existing(from_pub_date, approval_date)
    for s in stats:
        if s["in_unchanged_same_group"]:
            return "alignment", "fragment in same-group to-section not marked changed, absent from from-version"
    for s in stats:
        if s["in_other_group"]:
            return "alignment", "fragment present only in to-section(s) of a different srlc_group"
    for s in stats:
        if s["in_xml_text"]:
            return "parser", "fragment in raw XML text of to-version but in no parsed section"
    return "SrLC-side inconsistency", "fragment absent from to-version label text (parsed and raw XML)"


def _pre_existing(from_pub_date, approval_date):
    """Fragment already in the from-version: label carried it before this pair."""
    p, a = parse_date(from_pub_date), parse_date(approval_date)
    if p and a and p < a - dt.timedelta(days=WINDOW_BEFORE_DAYS):
        return "SrLC-side inconsistency", "text already in a version published >30 d before the SrLC approval"
    return "version bracketing", "text already in the from-version, which is itself inside the window"


# ----------------------------------------------------------------------------- grading

def load_split(root):
    sp = json.load(open(os.path.join(root, "split.json")))
    return sp


def candidate_versions(ix, setid):
    """All known versions of a setid (captured or not): [(version, pub_date, sha, member)] sorted by version."""
    return ix.execute("SELECT version, pub_date, sha256, xml_member FROM versions WHERE setid=? ORDER BY version",
                      (setid,)).fetchall()


def captured_pairs(vers):
    """Consecutive captured versions: [(from_row, to_row, gap_flag)].  gap_flag = version numbers not adjacent or
    an uncaptured version lies between."""
    cap = [v for v in vers if v[2]]
    out = []
    for a, b in zip(cap, cap[1:]):
        out.append((a, b, b[0] - a[0] != 1))
    return out


def plan_entries(gb, ix, drug_ids):
    """Build entry plans: one per (drug, supplement) with candidates, window versions and pairs."""
    appl_cache = {}
    plans = []
    for did, dname, atype, ano in gb.execute(
            "SELECT drug_name_id, drug_name, appl_type, appl_no FROM drugs WHERE drug_name_id IN (%s) AND detail_sha256 IS NOT NULL "
            "ORDER BY drug_name_id" % ",".join(str(int(i)) for i in drug_ids)):
        appl = ("%s%s" % (atype or "", ano or "")).upper()
        if appl not in appl_cache:
            appl_cache[appl] = [r[0] for r in ix.execute(
                "SELECT DISTINCT setid FROM approvals WHERE appl=? ORDER BY setid", (appl,))]
        cands = appl_cache[appl]
        for sid, adate, ref in gb.execute(
                "SELECT supplement_id, approval_date_iso, suppl_ref FROM supplements WHERE drug_name_id=? ORDER BY supplement_id", (did,)):
            plans.append({"drug_name_id": did, "drug_name": dname, "appl": appl, "supplement_id": sid,
                          "approval_date": adate, "suppl_ref": ref, "candidates": cands})
    return plans


def run_grade(args):
    mode = check_split_mode(args)
    from gitrx_core import changes, section_groups, spl_parser
    parser_version = spl_parser.PARSER_VERSION
    root = args.srlc_root
    split = load_split(root)
    ids = split["open"] if mode == "open" else split["sealed"]
    for k, name in (("open", "open_sha256"), ("sealed", "sealed_sha256")):
        got = hashlib.sha256("\n".join(str(i) for i in sorted(split[k])).encode()).hexdigest()
        if got != split[name]:
            raise SystemExit("split.json %s list does not match its recorded fingerprint" % k)
    out_dir = args.out_dir or os.path.join(root, "grading", "%s-%s" % (mode, parser_version))
    if mode == "sealed" and os.path.exists(os.path.join(out_dir, "summary.json")):
        raise SystemExit("refusing: sealed grading already run for this frozen parser version")
    os.makedirs(out_dir, exist_ok=True)
    cache_dir = args.cache_dir or os.path.join(root, "grading", "cache", "pairs")
    index_path = args.index or os.path.join(root, "grading", "cache", "index.sqlite")
    if not os.path.exists(index_path):
        build_index(args.evidence_db, index_path, args.workers, args.raw_manifest)
    ix = sqlite3.connect("file:%s?mode=ro" % index_path, uri=True)
    gb = sqlite3.connect("file:%s?mode=ro" % args.gradebook, uri=True)
    extra_paths = dict(ix.execute("SELECT sha256, path FROM paths"))
    resolve = Resolver(extra_paths, args.raw_manifest)
    t0 = time.time()
    log = lambda m: print("[%6.0fs] %s" % (time.time() - t0, m), flush=True)  # noqa: E731

    plans = plan_entries(gb, ix, ids)
    log("entries: %d (drug,supplement) in %s split with detail pages" % (len(plans), mode))

    # ---- decide pairs per candidate setid
    vcache = {}

    def vers(setid):
        if setid not in vcache:
            vcache[setid] = candidate_versions(ix, setid)
        return vcache[setid]
    need = collections.defaultdict(dict)  # setid -> {(a_ver,b_ver): row tuple}
    for p in plans:
        p["window_versions"] = {}
        p["pairs"] = {}
        for s in p["candidates"]:
            vs = vers(s)
            p["window_versions"][s] = [v[0] for v in vs if v[2] and in_window(v[1], p["approval_date"])]
            pr = [(a, b, gap) for a, b, gap in captured_pairs(vs) if in_window(b[1], p["approval_date"])]
            p["pairs"][s] = pr
            for a, b, gap in pr:
                need[s][(a[0], b[0])] = (a, b)
    tasks, unresolved = [], set()
    for s, d in need.items():
        chain = []
        for (av, bv), (a, b) in sorted(d.items()):
            pa, pb = resolve(a[2]), resolve(b[2])
            if pa is None or pb is None:
                unresolved.add((s, av, bv))
                continue
            chain.append((a[2], a[3], pa, b[2], b[3], pb))
        if chain:
            tasks.append((cache_dir, chain))
    npairs = sum(len(t[1]) for t in tasks)
    log("pairs needed: %d across %d setids (%d unresolved raw)" % (npairs, len(tasks), len(unresolved)))
    build_status = collections.Counter()
    done = 0
    with cf.ProcessPoolExecutor(args.workers) as ex:
        for res in ex.map(_digest_task, tasks, chunksize=1):
            for key, st in res:
                build_status[st.split(":")[0]] += 1
            done += 1
            if done % 100 == 0:
                log("digest tasks %d/%d %s" % (done, len(tasks), dict(build_status)))
    log("digests: %s" % dict(build_status))

    # ---- grade
    dig_views = {}

    def view(a_sha, b_sha):
        key = digest_key(a_sha, b_sha, parser_version, changes.CHANGES_VERSION)
        if key not in dig_views:
            d = load_digest(cache_dir, key)
            dig_views[key] = DigestView(d) if d else None
        return dig_views[key]

    rows_out, frags_out = [], []
    cov = collections.Counter()
    gitrx_only = {}
    graded_pair_keys = {}  # (setid, to_ver) -> {"view":..., "groups_srlc": set(), "drugs": set()}
    for p in plans:
        did, sid = p["drug_name_id"], p["supplement_id"]
        rows = gb.execute(
            "SELECT id, section_group, section_number, section_title, underlined_spans_json, refers_to_table, has_elision "
            "FROM changes WHERE supplement_id=? ORDER BY id", (sid,)).fetchall()
        cov["entries_total"] += 1
        if not p["approval_date"]:
            cov["entries_no_approval_date"] += 1
            continue
        if not p["candidates"]:
            cov["entries_excluded_no_candidate_setid"] += 1
            p["status"] = "excluded_no_candidate_setid"
        elif not any(p["window_versions"][s] for s in p["candidates"]):
            cov["entries_excluded_no_version_in_window"] += 1
            p["status"] = "excluded_no_version_in_window"
        else:
            cov["entries_graded"] += 1
            p["status"] = "graded"
        for r in rows:
            cov["rows_total_changes_table"] += 1
            if p["status"] != "graded":
                cov["rows_excluded_" + p["status"]] += 1
        if p["status"] != "graded":
            continue
        # mark pairs for gitrx-only accounting
        for s in p["candidates"]:
            for a, b, gap in p["pairs"][s]:
                v = view(a[2], b[2])
                if v is None:
                    continue
                g = graded_pair_keys.setdefault((s, b[0]), {"view": v, "srlc_groups": set(), "from": a[0], "drugs": set()})
                g["drugs"].add(did)
                g["srlc_groups"].update(r[1] for r in rows)
        # triples
        by_group = collections.OrderedDict()
        for r in rows:
            by_group.setdefault(r[1], []).append(r)
        for group, grows in by_group.items():
            rowrec = {"drug_name_id": did, "drug_name": p["drug_name"], "appl": p["appl"], "supplement_id": sid,
                      "suppl_ref": p["suppl_ref"], "approval_date": p["approval_date"], "group": group,
                      "n_change_rows": len(grows), "candidates": {}, "section_found": False}
            if group not in SRLC_GROUPS:
                rowrec["status"] = "unmapped_group"
                cov["rows_unmapped_group"] += 1
                rows_out.append(rowrec)
                continue
            rowrec["status"] = "graded"
            cov["triples_graded"] += 1
            stats_for_row = []  # (setid, pair a/b, [fragment stats])
            for s in p["candidates"]:
                cres = {"window_versions": p["window_versions"][s], "pairs": [], "found": False}
                for a, b, gap in p["pairs"][s]:
                    v = view(a[2], b[2])
                    ent = {"from_version": a[0], "to_version": b[0], "to_pub_date": b[1], "version_gap": gap,
                           "comparison": (v.d["comparison"] if v else "no_digest")}
                    if v and v.d["comparison"] == "ok" and v.has_group_change(group):
                        ent["group_changed"] = True
                        cres["found"] = True
                    else:
                        ent["group_changed"] = False
                    cres["pairs"].append(ent)
                rowrec["candidates"][s] = cres
                rowrec["section_found"] = rowrec["section_found"] or cres["found"]
            # fragments
            for r in grows:
                spans = json.loads(r[4] or "[]")
                frs = split_fragments(spans)
                for fi, fr in enumerate(frs):
                    big = fr["tokens"] >= MIN_FRAGMENT_TOKENS
                    table_row = bool(r[5])
                    fclass = "table_referring" if table_row else ("main" if big else "short")
                    fr_rec = {"drug_name_id": did, "supplement_id": sid, "change_id": r[0], "group": group,
                              "section_number": r[2], "section_title": r[3], "fragment": fr["text"],
                              "tokens": fr["tokens"], "class": fclass, "approval_date": p["approval_date"],
                              "per_candidate": {}, "found": False}
                    pair_stats = []
                    for s in p["candidates"]:
                        cf_found = False
                        for a, b, gap in p["pairs"][s]:
                            v = view(a[2], b[2])
                            if v is None or v.d["comparison"] != "ok":
                                continue
                            st = v.fragment_status(fr["text"], group)
                            pair_stats.append((s, a, b, st))
                            if st["found"]:
                                cf_found = True
                        fr_rec["per_candidate"][s] = cf_found
                        fr_rec["found"] = fr_rec["found"] or cf_found
                    if not fr_rec["found"]:
                        # categorize using the best-scoring pair: the earliest pair that carries any signal
                        cat, detail = categorize_fragment([x[3] for x in pair_stats],
                                                          pair_stats[0][1][1] if pair_stats else None, p["approval_date"])
                        if not pair_stats:
                            cat, detail = _no_pair_category(rowrec)
                        fr_rec["miss_category"], fr_rec["miss_detail"] = cat, detail
                    frags_out.append(fr_rec)
                    stats_for_row.append(fr_rec)
            # section miss category
            if not rowrec["section_found"]:
                rowrec["miss_category"], rowrec["miss_detail"] = _section_category(rowrec, stats_for_row, group)
            rows_out.append(rowrec)

    # ---- GitRx-only changes (pairs in a graded entry's window)
    go = collections.Counter()
    go_rows = []
    for (s, to_ver), g in sorted(graded_pair_keys.items()):
        v = g["view"]
        if v.d["comparison"] != "ok":
            continue
        for c in v.d["changes"]:
            grp = c["group"]
            matched = grp in g["srlc_groups"]
            kind = "srlc_group" if grp in SRLC_GROUPS else "non_srlc_section"
            key = ("matched_by_srlc" if matched else ("gitrx_only_" + kind))
            go[key] += 1
            if not matched:
                go_rows.append({"setid": s, "from_version": g["from"], "to_version": to_ver, "group": grp,
                                "section_number": c["section_number"], "section_title": c["section_title"],
                                "kind": c["kind"], "category": key, "drugs": sorted(g["drugs"])})
    # ---- summary
    sec_rows = [r for r in rows_out if r["status"] == "graded"]
    sec_found = sum(1 for r in sec_rows if r["section_found"])
    main = [f for f in frags_out if f["class"] == "main"]
    short = [f for f in frags_out if f["class"] == "short"]
    tblf = [f for f in frags_out if f["class"] == "table_referring"]
    miss_sec = collections.Counter(r.get("miss_category") for r in sec_rows if not r["section_found"])
    miss_txt = collections.Counter(f.get("miss_category") for f in main if not f["found"])
    summary = {
        "harness_version": HARNESS_VERSION, "split": mode, "parser_version": parser_version,
        "changes_version": changes.CHANGES_VERSION, "normalization": spl_parser.NORMALIZATION,
        "window_days": [-WINDOW_BEFORE_DAYS, WINDOW_AFTER_DAYS], "min_fragment_tokens": MIN_FRAGMENT_TOKENS,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "coverage": dict(cov),
        "mapping": {"drugs_in_split_with_detail": len({p["drug_name_id"] for p in plans}),
                    "drugs_with_candidate_setid": len({p["drug_name_id"] for p in plans if p["candidates"]}),
                    "setids_unresolved_raw_pairs": len(unresolved)},
        "section_recall": rate(sec_found, len(sec_rows)),
        "section_recall_by_group": {g: rate(sum(1 for r in sec_rows if r["group"] == g and r["section_found"]),
                                            sum(1 for r in sec_rows if r["group"] == g)) for g in SRLC_GROUPS},
        "text_recall_main": rate(sum(1 for f in main if f["found"]), len(main)),
        "text_recall_main_by_group": {g: rate(sum(1 for f in main if f["group"] == g and f["found"]),
                                              sum(1 for f in main if f["group"] == g)) for g in SRLC_GROUPS},
        "text_short_fragments_lt3_tokens": rate(sum(1 for f in short if f["found"]), len(short)),
        "text_table_referring_rows": rate(sum(1 for f in tblf if f["found"]), len(tblf)),
        "section_miss_categories": dict(miss_sec),
        "text_miss_categories": dict(miss_txt),
        "gitrx_only": dict(go),
        "digest_build": dict(build_status),
        "inputs": {
            "gradebook": args.gradebook, "gradebook_sha256": sha256_file(args.gradebook),
            "gradebook_drugs_with_detail": gb.execute("SELECT COUNT(*) FROM drugs WHERE detail_sha256 IS NOT NULL").fetchone()[0],
            "split_json_sha256": sha256_file(os.path.join(root, "split.json")),
            "split_ids_used_sha256": split["open_sha256" if mode == "open" else "sealed_sha256"],
            "index": index_path, "index_sha256": sha256_file(index_path),
            "index_meta": dict(ix.execute("SELECT k, v FROM meta")),
            "code_sha256": {n: sha256_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), n))
                            for n in ("srlc_grade.py", "spl_parser.py", "changes.py", "section_groups.py", "normalize.py")},
        },
        "runtime_seconds": round(time.time() - t0, 1),
    }
    for name, recs in (("rows.jsonl", rows_out), ("fragments.jsonl", frags_out), ("gitrx_only.jsonl", go_rows)):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1, sort_keys=True, ensure_ascii=False)
    log("wrote %s" % out_dir)
    return summary


def _no_pair_category(rowrec):
    """No comparable in-window pair existed for any candidate."""
    ok = False
    for c in rowrec["candidates"].values():
        for e in c["pairs"]:
            if e["comparison"] in ("not_comparable", "error"):
                ok = True
    if ok:
        return "parser", "in-window pair not comparable (extraction defect / parse error)"
    return "missing DailyMed version", "window version has no captured predecessor or pair"


def _section_category(rowrec, frags, group):
    pairs = [e for c in rowrec["candidates"].values() for e in c["pairs"]]
    if not pairs:
        return "missing DailyMed version", "in-window version(s) have no captured predecessor"
    if all(e["comparison"] in ("not_comparable", "error", "no_digest") for e in pairs):
        return "parser", "all in-window pairs not comparable (%s)" % ",".join(sorted({e["comparison"] for e in pairs}))
    cats = collections.Counter(f.get("miss_category") for f in frags
                               if f["class"] != "short" and not f["found"] and f.get("miss_category"))
    if cats:
        cat = cats.most_common(1)[0][0]
        return cat, "from fragment categories %s" % dict(cats)
    if any(e["version_gap"] for e in pairs):
        return "missing DailyMed version", "in-window pair spans a version gap"
    return "uncategorized", "no >=3-token fragment to test"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("index", "grade"):
        sp = sub.add_parser(name)
        sp.add_argument("--evidence-db", default=DEFAULT_EVIDENCE)
        sp.add_argument("--srlc-root", default=DEFAULT_SRLC_ROOT)
        sp.add_argument("--index", default=None, help="index.sqlite path (default <srlc-root>/grading/cache/index.sqlite)")
        sp.add_argument("--raw-manifest", default=None, help="optional raw-manifest.sqlite with a copies(sha256,path) table")
        sp.add_argument("--workers", type=int, default=6)
        if name == "index":
            sp.add_argument("--limit", type=int, default=None)
            sp.add_argument("--scan-all-versions", action="store_true")
        else:
            sp.add_argument("--gradebook", default=None, help="gradebook.sqlite copy (default <srlc-root>/gradebook.sqlite)")
            sp.add_argument("--split", default="open", choices=["open", "sealed"])
            sp.add_argument("--sealed-final", action="store_true")
            sp.add_argument("--parser-version", default=None)
            sp.add_argument("--frozen-parser-marker", default=None)
            sp.add_argument("--out-dir", default=None)
            sp.add_argument("--cache-dir", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "index":
        ip = a.index or os.path.join(a.srlc_root, "grading", "cache", "index.sqlite")
        build_index(a.evidence_db, ip, a.workers, a.raw_manifest, limit=a.limit, scan_all_versions=a.scan_all_versions)
        return 0
    a.gradebook = a.gradebook or os.path.join(a.srlc_root, "gradebook.sqlite")
    s = run_grade(a)
    print(json.dumps({k: s[k] for k in ("split", "parser_version", "coverage", "mapping", "section_recall",
                                        "text_recall_main", "section_miss_categories", "text_miss_categories",
                                        "gitrx_only")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
