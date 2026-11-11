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
import functools
import time
import unicodedata
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gitrx_core.normalize import normalize  # noqa: E402

HARNESS_VERSION = "srlc-grade-0.3.2"
# Amendment 4: "either" accepts srlc_group or alt_group as same-group; "primary" accepts srlc_group only.
GROUP_MODE = "either"


def _accepts(group, primary, alt):
    return group == primary or (GROUP_MODE == "either" and alt is not None and group == alt)
WINDOW_BEFORE_DAYS = 30
WINDOW_AFTER_DAYS = 365
# Amendment 3 item 2: transition search range around the SrLC approval date
LOCATE_BEFORE_DAYS = 730
LOCATE_AFTER_DAYS = 365
MIN_FRAGMENT_TOKENS = 3
SRLC_GROUPS = ("BW", "C", "WP", "AR", "DI", "USP", "MG")
CHANNELS = ("text", "table", "excerpt")
DEFAULT_EVIDENCE = "/Volumes/Untitled/GitRx/evidence/evidence-v1.sqlite"
DEFAULT_SRLC_ROOT = "/Volumes/Untitled/GitRx/srlc"
BODIES_DIR = "/Volumes/Untitled/GitRx/backfill-20261107/network/bodies"
# document types that can carry SrLC applications (Rx human + biologics); unknown/missing type is kept
RX_DOC_CODES = {"34391-3", "45129-4", "60683-0", "53404-0", "53405-7", "60684-8", "60682-2", "53408-1",
                "78745-7", "53406-5"}
# Amendment 2: descriptive, mechanical miss labels (not attributions).  Lower rank = closer to a match.
L_OUT = "in changed same-group section but outside GitRx added tokens"
L_FROM_IN = "present in from-version (published inside window)"
L_FROM_BEFORE = "present in a version published >30 d before approval"
L_TO_NOCHG = "present in to-version but not in a changed same-group section"
L_XML = "present in raw XML but not parsed sections"
L_INWIN_OTHER = "absent from compared pairs; present in another in-window version"
L_AFTER = "absent from window versions; present only in versions after window"
L_BEFORE = "absent from window versions; present only in versions before window"
L_BOTH = "absent from window versions; present only in versions before and after window"
L_NONE = "absent from every captured version (including all window versions)"
L_NOPAIR = "no comparable in-window pair"
L_NOTCOMP = "candidate pair not comparable"
TEXT_LABEL_ORDER = (L_OUT, L_FROM_IN, L_FROM_BEFORE, L_TO_NOCHG, L_XML, L_INWIN_OTHER, L_AFTER, L_BEFORE, L_BOTH,
                    L_NONE, L_NOPAIR, L_NOTCOMP)
S_OTHER_SEC = "in-window pair(s) change other groups only; to-version has an unchanged section of this group"
S_OTHER_NOSEC = "in-window pair(s) change other groups only; to-version has no section of this group"
S_NOCHG = "in-window pair(s) have no substantive change"
S_NOTCOMP = L_NOTCOMP
S_NOPAIR = L_NOPAIR
SECTION_LABEL_ORDER = (S_OTHER_SEC, S_OTHER_NOSEC, S_NOCHG, S_NOTCOMP, S_NOPAIR)
# Amendment 3 item 2: reasons a fragment is "not locatable" and labels for located-but-missed fragments
NL_FIRST = "present from first captured version (no absent->present transition)"
NL_NEVER = "never present in any captured version"
NL_RANGE = "absent->present transitions only outside [approval-730 d, approval+365 d]"
NL_UNREADABLE = "no readable captured version"
NOT_LOCATABLE_ORDER = (NL_FIRST, NL_NEVER, NL_RANGE, NL_UNREADABLE)
LL_NOTCOMP = "located pair not comparable"
LL_OTHER_GROUP = "in pair, inside added tokens but different group (alignment/grouping)"
LL_SAME_NOTADDED = "in to-version same-group changed section, not within added tokens"
LL_SAME_UNCHANGED = "in to-version same-group section not marked changed"
LL_OTHER_NOADDED = "in to-version other-group section only, not within added tokens"
LL_XML = "in raw XML of to-version but not in parsed sections (parser)"
LL_UNEXPLAINED = "located by full text but not found in the pair's digest"
_RANK = {l: i for i, l in enumerate(TEXT_LABEL_ORDER)}
_SRANK = {l: i for i, l in enumerate(SECTION_LABEL_ORDER)}


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


def ws_squash(text):
    """Remove all whitespace (Amendment 2 primary metric)."""
    return "".join(text.split())


def _norm_tokens(raw, added_intervals=()):
    """[(normalized_token, overlaps_added_interval)] for the non-empty tokens of the raw channel text."""
    out = []
    for m in _TOK.finditer(raw):
        t = normalize(m.group())
        if not t:
            continue
        added = any(m.start() < e and m.end() > s for s, e in added_intervals)
        out.append((t, added))
    return out


def norm_with_tokens(raw, added_intervals=()):
    """Normalized (literal-v1) text of a channel as space-joined normalized tokens, with, per token, its
    [start, end) position in that string and whether it overlaps a GitRx added char interval of the raw text."""
    parts, toks, pos = [], [], 0
    for t, added in _norm_tokens(raw, added_intervals):
        if parts:
            pos += 1
        toks.append((pos, pos + len(t), added))
        parts.append(t)
        pos += len(t)
    return " ".join(parts), toks


def norm_ws_with_flags(raw, added_intervals=()):
    """Amendment 2: literal-v1 text with ALL whitespace removed, plus a per-character bytearray that is 1 where the
    character belongs to a token overlapping a GitRx added interval (so the overlap rule is evaluated on the same
    token mapping as the strict metric)."""
    parts, flags = [], bytearray()
    for t, added in _norm_tokens(raw, added_intervals):
        t = ws_squash(t)
        if not t:
            continue
        parts.append(t)
        flags.extend((b"\x01" if added else b"\x00") * len(t))
    return "".join(parts), flags


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


def find_fragment_ws(fragment_ws, ws_text, flags):
    """-> (verbatim, overlaps_added_token) for a whitespace-free fragment in a whitespace-free channel text."""
    verbatim = overlap = False
    start = 0
    while fragment_ws:
        i = ws_text.find(fragment_ws, start)
        if i < 0:
            break
        verbatim = True
        j = i + len(fragment_ws)
        if flags.find(1, i, j) >= 0:
            overlap = True
            break
        start = i + 1
    return verbatim, overlap


# ---- Amendment 3 item 1: rendering normalization (on top of the Amendment 2 whitespace-free match)

_LIST_MARKERS = ("\u2022", "\u25e6", "\u25aa", "-", "*")


def _drop_cf(t):
    """Remove Unicode format characters (category Cf) from t."""
    if t.isascii():
        return t
    drop = {ord(c): None for c in set(t) if unicodedata.category(c) == "Cf"}
    return t.translate(drop) if drop else t


def render_squash(text):
    """Rendering-normalized, whitespace-free text: literal-v1, casefold, Cf characters removed, whitespace removed."""
    return "".join(_drop_cf(normalize(text).casefold()).split())


def strip_list_markers(text):
    """Remove leading list markers (bullet, white bullet, small square, '-', '*', or 'o' + space), repeatedly."""
    s = normalize(text)
    while True:
        if s[:1] in _LIST_MARKERS:
            s = s[1:].lstrip(" ")
        elif s.startswith("o "):
            s = s[2:].lstrip(" ")
        else:
            return s


def render_key(fragment):
    """Rendering-normalized, whitespace-free key of an SrLC fragment (list markers stripped first)."""
    return render_squash(strip_list_markers(fragment))


@functools.lru_cache(maxsize=200000)
def compile_key(key):
    """-> compiled regex when the key holds SrLC '?' placeholders (each matches one non-ASCII label char, or a
    literal '?'), else None (plain substring search)."""
    if "?" not in key:
        return None
    return re.compile("".join(r"(?:[^\x00-\x7f]|\?)" if c == "?" else re.escape(c) for c in key))


def norm_render_with_flags(raw, added_intervals=()):
    """Amendment 3 rendering-normalized, whitespace-free label text plus per-character added flags (1 where the
    character belongs to a token overlapping a GitRx added interval; same token mapping as the strict metric)."""
    parts, flags = [], bytearray()
    for m in _TOK.finditer(raw):
        t = render_squash(m.group())
        if not t:
            continue
        added = any(m.start() < e and m.end() > s for s, e in added_intervals)
        parts.append(t)
        flags.extend((b"\x01" if added else b"\x00") * len(t))
    return "".join(parts), flags


def find_render(key, text, flags=None):
    """-> (verbatim, overlaps_added_token) for a render key in rendering-normalized text.  `flags` None = presence."""
    verbatim = overlap = False
    if not key:
        return False, False
    rx = compile_key(key)
    start = 0
    while True:
        if rx is None:
            i = text.find(key, start)
            j = i + len(key)
        else:
            m = rx.search(text, start)
            i, j = (m.start(), m.end()) if m else (-1, -1)
        if i < 0:
            break
        verbatim = True
        if flags is None:
            break
        if flags.find(1, i, j) >= 0:
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
    from gitrx_core.section_groups import classify_doc
    out = []
    groups = classify_doc(doc)
    for sec in doc.get("sections", []):
        ch = {k: v for k, v in channels(sec).items() if v.strip()}
        out.append({"o": sec["ordinal"], "group": groups[sec["ordinal"]]["group"],
                    "alt": groups[sec["ordinal"]]["alt_group"],
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
        ch.append({"kind": c["kind"], "group": c["srlc_group"], "alt": c.get("alt_srlc_group"), "ordinal_to": c.get("ordinal_to"),
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
        self.to_groups = {x for sec in digest.get("to_sections", []) for x in ((sec["group"], sec.get("alt")) if GROUP_MODE == "either" else (sec["group"],))}

    def _added(self, sec):
        return (self.changed.get(sec["o"]) or {}).get("added", {})

    def to_norm(self, sec, ch):
        key = ("to", sec["o"], ch)
        if key not in self._n:
            self._n[key] = norm_with_tokens(sec["ch"].get(ch, ""), self._added(sec).get(ch, []))
        return self._n[key]

    def to_ws(self, sec, ch):
        key = ("tows", sec["o"], ch)
        if key not in self._n:
            self._n[key] = norm_ws_with_flags(sec["ch"].get(ch, ""), self._added(sec).get(ch, []))
        return self._n[key]

    def to_render(self, sec, ch):
        key = ("torender", sec["o"], ch)
        if key not in self._n:
            self._n[key] = norm_render_with_flags(sec["ch"].get(ch, ""), self._added(sec).get(ch, []))
        return self._n[key]

    def from_render(self, sec, ch):
        key = ("fromrender", sec["o"], ch)
        if key not in self._n:
            self._n[key] = norm_render_with_flags(sec["ch"].get(ch, ""))
        return self._n[key]

    def xml_render(self):
        if "xmlrender" not in self._n:
            self._n["xmlrender"] = render_squash(self.d.get("xml_text_to", ""))
        return self._n["xmlrender"]

    def has_group_change(self, group):
        return any(_accepts(group, c["group"], c.get("alt")) for c in self.d["changes"])

    def fragment_status(self, frag, group):
        """Amendment 1 text criterion with Amendment 2/3 matching, plus diagnostics for labelling.

        found / found_render (primary): rendering-normalized fragment (Amendment 3) occurs in a changed same-group
        to-section AND overlaps an added token.  found_ws: Amendment 2 whitespace-free match; found_strict: literal-v1.
        any_*: same criteria but in a substantive change of ANY group.  Diagnostic keys are render-based."""
        fws = ws_squash(frag)
        rkey = render_key(frag)
        res = {"found": False, "found_render": False, "found_ws": False, "found_strict": False,
               "any_render": False, "any_ws": False, "any_strict": False,
               "verbatim_changed_same_group": False, "in_unchanged_same_group": False, "in_other_group": False,
               "in_from_same_group": False, "in_from_any": False, "in_xml_text": False}
        for sec in self.d["to_sections"]:
            same = _accepts(group, sec["group"], sec.get("alt"))
            chg = sec["o"] in self.changed
            for ch in sec["ch"]:
                rt, rflags = self.to_render(sec, ch)
                v, ov = find_render(rkey, rt, rflags)
                if not v:
                    continue
                if same:
                    if chg:
                        res["verbatim_changed_same_group"] = True
                    else:
                        res["in_unchanged_same_group"] = True
                else:
                    res["in_other_group"] = True
                if not (chg and ov):
                    continue
                res["any_render"] = True
                if same:
                    res["found_render"] = res["found"] = True
                wt, flags = self.to_ws(sec, ch)
                if find_fragment_ws(fws, wt, flags)[1]:
                    res["any_ws"] = True
                    res["found_ws"] = res["found_ws"] or same
                    nt, toks = self.to_norm(sec, ch)
                    if find_fragment(frag, nt, toks)[1]:
                        res["any_strict"] = True
                        res["found_strict"] = res["found_strict"] or same
        if res["found"]:
            return res
        for sec in self.d["from_sections"]:
            for ch in sec["ch"]:
                wt, _ = self.from_render(sec, ch)
                if find_render(rkey, wt)[0]:
                    res["in_from_any"] = True
                    if _accepts(group, sec["group"], sec.get("alt")):
                        res["in_from_same_group"] = True
        res["in_xml_text"] = find_render(rkey, self.xml_render())[0]
        return res


def _pre_existing(from_pub_date, approval_date):
    """Fragment already in the from-version of the compared pair."""
    p, a = parse_date(from_pub_date), parse_date(approval_date)
    if p and a and p < a - dt.timedelta(days=WINDOW_BEFORE_DAYS):
        return L_FROM_BEFORE, "text already in a version published >30 d before the SrLC approval"
    return L_FROM_IN, "text already in the from-version, which is itself inside the window"


def label_stat(st, from_pub_date, approval_date):
    """Descriptive label for one unfound per-pair fragment status; None when the fragment is absent from the
    pair's to/from label text and raw XML (caller resolves with the all-version check)."""
    if st["verbatim_changed_same_group"] and st["in_from_same_group"]:
        return _pre_existing(from_pub_date, approval_date)
    if st["verbatim_changed_same_group"]:
        return L_OUT, "fragment in changed same-group to-section but not within GitRx added tokens"
    if st["in_unchanged_same_group"] and st["in_from_any"]:
        return _pre_existing(from_pub_date, approval_date)
    if st["in_unchanged_same_group"]:
        return L_TO_NOCHG, "fragment in same-group to-section not marked changed; not in from-version"
    if st["in_other_group"]:
        return L_TO_NOCHG, "fragment present only in to-section(s) of a different srlc_group"
    if st["in_from_any"]:
        lab = _pre_existing(from_pub_date, approval_date)
        return lab[0], lab[1] + " (not in to-version)"
    if st["in_xml_text"]:
        return L_XML, "fragment in raw XML text of to-version but in no parsed section"
    return None


def candidate_label(stats):
    """stats: [(fragment_status, from_pub_date)] for ONE candidate's comparable in-window pairs.
    -> (label, detail) with the lowest-rank label over pairs, or (None, None) if absent from all."""
    best = None
    for st, fpub, apr in stats:
        r = label_stat(st, fpub, apr)
        if r and (best is None or _RANK[r[0]] < _RANK[best[0]]):
            best = r
    return best if best else (None, None)


def classify_presence(pubs, approval_date, present):
    """pubs: {version: pub_date}; present: set(version).  Position of a fragment among ALL captured versions
    of one candidate relative to the window.  -> label."""
    before = after = inwin = False
    for v in present:
        pd = parse_date(pubs.get(v))
        a = parse_date(approval_date)
        if pd is None or a is None:
            continue
        if pd < a - dt.timedelta(days=WINDOW_BEFORE_DAYS):
            before = True
        elif pd > a + dt.timedelta(days=WINDOW_AFTER_DAYS):
            after = True
        else:
            inwin = True
    if inwin:
        return L_INWIN_OTHER
    if before and after:
        return L_BOTH
    if after:
        return L_AFTER
    if before:
        return L_BEFORE
    return L_NONE


# ----------------------------------------------------------------------------- product names / all-version presence

_MP_NAME = re.compile(rb"<manufacturedProduct\b[^>]*>(?:(?!<name\b)(?!</manufacturedProduct>).){0,600}?"
                      rb"<name\b[^>]*>(.*?)</name>", re.S)
_TITLE = re.compile(rb"<title\b[^>]*>(.*?)</title>", re.S)


def product_names_in_xml(xml_bytes):
    """SPL product names (manufacturedProduct/name) plus the document title, tags stripped, de-duplicated."""
    out = []
    items = [m.group(1) for m in _MP_NAME.finditer(xml_bytes)]
    t = _TITLE.search(xml_bytes)
    if t:
        items.append(t.group(1))
    for raw in items:
        s = normalize(html.unescape(re.sub(r"<[^>]+>", " ", raw.decode("utf-8", "replace"))))
        if s and s not in out:
            out.append(s)
    return out


def is_sibling_or_repackager(drug_name, names):
    """True when none of the candidate's SPL product names contains the SrLC drug_name token (case-insensitive,
    whitespace-insensitive).  Token = drug_name without parenthetical, or its first word."""
    if not names:
        return False
    clean = normalize(re.sub(r"\(.*?\)", " ", drug_name or ""), True) or normalize(drug_name or "", True)
    toks = {ws_squash(clean)}
    if clean.split():
        toks.add(ws_squash(clean.split()[0]))
    toks.discard("")
    if not toks:
        return False
    hay = ws_squash(normalize(" | ".join(names), True))
    return not any(t in hay for t in toks)


def _names_task(task):
    setid, member, path = task
    try:
        return setid, product_names_in_xml(read_xml_from_zip(path, member)), None
    except Exception as exc:  # noqa: BLE001
        return setid, [], "%s: %s" % (type(exc).__name__, exc)


def fulltext_path(ft_dir, sha):
    return os.path.join(ft_dir, sha[:2], sha + ".txt.gz")


def load_or_build_fulltext(ft_dir, sha, member, path):
    """Rendering-normalized whitespace-free full label text of one raw artifact, cached per raw sha256
    (cache/fulltext-render/<sha[:2]>/<sha>.txt.gz; existing files are never overwritten)."""
    fp = fulltext_path(ft_dir, sha)
    if os.path.exists(fp):
        with gzip.open(fp, "rt", encoding="utf-8") as f:
            return f.read()
    text = render_squash(_strip_xml_text(read_xml_from_zip(path, member)))
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    tmp = "%s.%d.tmp" % (fp, os.getpid())
    with gzip.open(tmp, "wt", encoding="utf-8", compresslevel=3) as f:
        f.write(text)
    if os.path.exists(fp):
        os.remove(tmp)
    else:
        os.replace(tmp, fp)
    return text


def _presence_task(task):
    """task = (setid, [(version, sha, member, path)], [render_key, ...], ft_dir)
    -> (setid, {version: [indices of keys present] | None})."""
    setid, versions, keys, ft_dir = task
    out = {}
    for ver, sha, member, path in versions:
        try:
            text = load_or_build_fulltext(ft_dir, sha, member, path)
        except Exception:  # noqa: BLE001 - recorded as unchecked
            out[ver] = None
            continue
        out[ver] = [i for i, k in enumerate(keys) if find_render(k, text)[0]]
    return setid, out


def locate_transition(cands, approval_date):
    """Amendment 3 item 2.  cands: {setid: [(version, pub_date, present)]} for the captured versions of each candidate
    in version order; present is True/False, or None when the version could not be read.

    A transition is two consecutive readable captured versions with the fragment absent in the first and present in
    the next.  Of transitions whose to-version publication date lies in [approval-730 d, approval+365 d] the one
    nearest the approval is chosen (within a candidate: earlier date, then lower version on ties; across candidates:
    lowest setid on ties).  -> (location dict | None, not_locatable_reason | None)."""
    a = parse_date(approval_date)
    lo = a - dt.timedelta(days=LOCATE_BEFORE_DAYS) if a else None
    hi = a + dt.timedelta(days=LOCATE_AFTER_DAYS) if a else None
    best = None
    any_transition = any_present = any_readable = False
    for setid in sorted(cands):
        rd = [(v, pd, pr) for v, pd, pr in cands[setid] if pr is not None]
        if rd:
            any_readable = True
        any_present = any_present or any(pr for _, _, pr in rd)
        for (va, pda, pra), (vb, pdb, prb) in zip(rd, rd[1:]):
            if pra or not prb:
                continue
            any_transition = True
            d = parse_date(pdb)
            if d is None or a is None or not (lo <= d <= hi):
                continue
            key = (abs((d - a).days), setid, d, vb)
            if best is None or key < best[0]:
                best = (key, {"setid": setid, "from_version": va, "to_version": vb, "to_pub_date": pdb,
                              "delta_days": (d - a).days})
    if best:
        return best[1], None
    if any_transition:
        return None, NL_RANGE
    if any_present:
        return None, NL_FIRST
    if any_readable:
        return None, NL_NEVER
    return None, NL_UNREADABLE


def located_label(view, st):
    """Descriptive label for a located fragment that GitRx did not report in the pair's same-group added tokens."""
    if view is None or view.d["comparison"] != "ok":
        return LL_NOTCOMP
    if st["any_render"]:
        return LL_OTHER_GROUP
    if st["verbatim_changed_same_group"]:
        return LL_SAME_NOTADDED
    if st["in_unchanged_same_group"]:
        return LL_SAME_UNCHANGED
    if st["in_other_group"]:
        return LL_OTHER_NOADDED
    if st["in_xml_text"]:
        return LL_XML
    return LL_UNEXPLAINED


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


VARIANTS = (("strict", "found_strict"), ("ws", "found_ws"), ("render", "found"))


def _ssd_guard(*paths):
    """Stop (rather than create stray directories) if the external SSD vanished mid-run."""
    for p in paths:
        if p and p.startswith("/Volumes/"):
            root = os.sep.join(p.split(os.sep)[:4])
            if not os.path.isdir(root):
                raise SystemExit("external volume %s is gone; stopping" % root)


def run_grade(args):
    global GROUP_MODE
    GROUP_MODE = getattr(args, "group_mode", "either") or "either"
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
    cache_dir = args.cache_dir or os.path.join(root, "grading", "cache", "pairs")
    ft_dir = getattr(args, "fulltext_dir", None) or os.path.join(os.path.dirname(cache_dir.rstrip(os.sep)), "fulltext-render")
    index_path = args.index or os.path.join(root, "grading", "cache", "index.sqlite")
    _ssd_guard(out_dir, cache_dir, ft_dir, index_path)
    os.makedirs(out_dir, exist_ok=True)
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

    vcache = {}

    def vers(setid):
        if setid not in vcache:
            vcache[setid] = candidate_versions(ix, setid)
        return vcache[setid]

    # ---- window pairs per candidate setid (end-to-end metric, Amendment 1 bracketing)
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
    unresolved = set()
    build_status = collections.Counter()

    def build_digests(need_map, what):
        tasks = []
        for s, d in need_map.items():
            chain = []
            for (av, bv), (a, b) in sorted(d.items()):
                pa, pb = resolve(a[2]), resolve(b[2])
                if pa is None or pb is None:
                    unresolved.add((s, av, bv))
                    continue
                chain.append((a[2], a[3], pa, b[2], b[3], pb))
            if chain:
                tasks.append((cache_dir, chain))
        log("%s pairs needed: %d across %d setids (%d unresolved raw so far)" %
            (what, sum(len(t[1]) for t in tasks), len(tasks), len(unresolved)))
        _ssd_guard(cache_dir)
        done = 0
        with cf.ProcessPoolExecutor(args.workers) as ex:
            for res in ex.map(_digest_task, tasks, chunksize=1):
                for key, st in res:
                    build_status[st.split(":")[0]] += 1
                done += 1
                if done % 100 == 0:
                    log("%s digest tasks %d/%d %s" % (what, done, len(tasks), dict(build_status)))
        log("%s digests: %s" % (what, dict(build_status)))
    build_digests(need, "window")

    # ---- candidate product names (sibling / repackager flag)
    cand_names, name_err = {}, 0
    all_cands = sorted({c for p in plans if p["approval_date"] and p["candidates"]
                        and any(p["window_versions"][x] for x in p["candidates"]) for c in p["candidates"]})
    ntasks = []
    for c in all_cands:
        vs = [v for v in vers(c) if v[2]]
        if vs:
            r = vs[-1]
            path = resolve(r[2])
            if path:
                ntasks.append((c, r[3], path))
    with cf.ProcessPoolExecutor(args.workers) as ex:
        for c, names, err in ex.map(_names_task, ntasks, chunksize=16):
            cand_names[c] = names
            name_err += 1 if err else 0
    log("product names read for %d/%d candidate setids (%d errors)" % (len(cand_names), len(all_cands), name_err))

    # ---- Amendment 3: fragments of every parser-gradable entry, full-text presence per captured version
    need_keys = collections.defaultdict(set)  # setid -> {render key}
    for p in plans:
        p["rows"], p["frs"] = [], {}
        if not p["approval_date"] or not p["candidates"]:
            continue
        p["rows"] = gb.execute(
            "SELECT id, section_group, section_number, section_title, underlined_spans_json, refers_to_table, has_elision "
            "FROM changes WHERE supplement_id=? ORDER BY id", (p["supplement_id"],)).fetchall()
        for r in p["rows"]:
            frs = split_fragments(json.loads(r[4] or "[]"))
            p["frs"][r[0]] = frs
            if r[1] in SRLC_GROUPS and not r[5]:
                for fr in frs:
                    k = render_key(fr["text"])
                    if fr["tokens"] >= MIN_FRAGMENT_TOKENS and k:
                        for s in p["candidates"]:
                            need_keys[s].add(k)
    ptasks = []
    for s, ks in sorted(need_keys.items()):
        vl = []
        for v in vers(s):
            if v[2]:
                pth = resolve(v[2])
                if pth:
                    vl.append((v[0], v[2], v[3], pth))
        ptasks.append((s, vl, sorted(ks), ft_dir))
    nver = sum(len(t[1]) for t in ptasks)
    log("full-text presence: %d setids, %d version reads, %d (setid,fragment) pairs" %
        (len(ptasks), nver, sum(len(t[2]) for t in ptasks)))
    _ssd_guard(ft_dir)
    pres = {}  # setid -> {version: set(render keys) | None}
    done = 0
    with cf.ProcessPoolExecutor(args.workers) as ex:
        for (s, res), t in zip(ex.map(_presence_task, ptasks, chunksize=1), ptasks):
            ks = t[2]
            pres[s] = {v: (None if hit is None else {ks[i] for i in hit}) for v, hit in res.items()}
            done += 1
            if done % 200 == 0:
                log("presence %d/%d" % (done, len(ptasks)))
                _ssd_guard(ft_dir)
    log("full-text presence done")

    # ---- locate the transition pair of every main fragment
    loc_memo = {}

    def locate(p, key):
        mk = (tuple(p["candidates"]), p["approval_date"], key)
        if mk not in loc_memo:
            cands = {}
            for s in p["candidates"]:
                ps = pres.get(s, {})
                cands[s] = [(v[0], v[1], (key in ps[v[0]]) if ps.get(v[0]) is not None else None)
                            for v in vers(s) if v[2]]
            loc_memo[mk] = locate_transition(cands, p["approval_date"])
        return loc_memo[mk]
    need_loc = collections.defaultdict(dict)
    for p in plans:
        for r in p["rows"]:
            if r[1] in SRLC_GROUPS and not r[5]:
                for fr in p["frs"][r[0]]:
                    k = render_key(fr["text"])
                    if fr["tokens"] >= MIN_FRAGMENT_TOKENS and k:
                        loc, _ = locate(p, k)
                        if loc:
                            s = loc["setid"]
                            rowmap = {v[0]: v for v in vers(s)}
                            need_loc[s][(loc["from_version"], loc["to_version"])] = (
                                rowmap[loc["from_version"]], rowmap[loc["to_version"]])
    log("located %d distinct (entry,fragment) memo keys; %d distinct located pairs" %
        (len(loc_memo), sum(len(d) for d in need_loc.values())))
    build_digests(need_loc, "located")

    # ---- grade
    dig_views = collections.OrderedDict()

    def view(a_sha, b_sha):
        key = digest_key(a_sha, b_sha, parser_version, changes.CHANGES_VERSION)
        if key in dig_views:
            dig_views.move_to_end(key)
            return dig_views[key]
        d = load_digest(cache_dir, key)
        dig_views[key] = DigestView(d) if d else None
        if len(dig_views) > 300:
            dig_views.popitem(last=False)
        return dig_views[key]

    def sha_of(s, ver):
        return next(v[2] for v in vers(s) if v[0] == ver)

    rows_out, frags_out = [], []
    cov = collections.Counter()
    graded_pair_keys = {}  # (setid, to_ver) -> {"shas": (a_sha, b_sha), "srlc_groups": set(), "from": ver, "drugs": set()}
    pending = []           # e2e fragment records awaiting final labels: (fr_rec, plan, rowrec, cand_stats, labs)
    for p in plans:
        did, sid = p["drug_name_id"], p["supplement_id"]
        rows = p["rows"] if p["approval_date"] and p["candidates"] else gb.execute(
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
        if p["status"] == "excluded_no_candidate_setid":
            continue
        e2e = p["status"] == "graded"
        sib = {s: is_sibling_or_repackager(p["drug_name"], cand_names.get(s, [])) for s in p["candidates"]}
        p["sibling"] = sib
        if e2e:
            for s in p["candidates"]:
                for a, b, gap in p["pairs"][s]:
                    v = view(a[2], b[2])
                    if v is None:
                        continue
                    g = graded_pair_keys.setdefault((s, b[0]), {"shas": (a[2], b[2]), "srlc_groups": set(),
                                                                "from": a[0], "drugs": set()})
                    g["drugs"].add(did)
                    g["srlc_groups"].update(r[1] for r in rows)
        status_memo = {}

        def status(a_sha, b_sha, text, group):
            k = (a_sha, b_sha, text, group)
            if k not in status_memo:
                v = view(a_sha, b_sha)
                status_memo[k] = (v, v.fragment_status(text, group) if v and v.d["comparison"] == "ok" else None)
            return status_memo[k]
        by_group = collections.OrderedDict()
        for r in rows:
            by_group.setdefault(r[1], []).append(r)
        for group, grows in by_group.items():
            rowrec = {"drug_name_id": did, "drug_name": p["drug_name"], "appl": p["appl"], "supplement_id": sid,
                      "suppl_ref": p["suppl_ref"], "approval_date": p["approval_date"], "group": group,
                      "n_change_rows": len(grows), "candidates": {}, "section_found": False, "e2e": e2e,
                      "candidate_product_names": {s: cand_names.get(s) for s in p["candidates"]},
                      "sibling_or_repackager": sorted(s for s, f in sib.items() if f)}
            if group not in SRLC_GROUPS:
                rowrec["status"] = "unmapped_group"
                cov["rows_unmapped_group"] += 1
                rows_out.append(rowrec)
                continue
            rowrec["status"] = "graded" if e2e else "parser_only"
            if e2e:
                cov["triples_graded"] += 1
            for s in p["candidates"]:
                cres = {"window_versions": p["window_versions"][s], "pairs": [], "found": False}
                for a, b, gap in p["pairs"][s]:
                    v = view(a[2], b[2])
                    ent = {"from_version": a[0], "to_version": b[0], "to_pub_date": b[1], "version_gap": gap,
                           "comparison": (v.d["comparison"] if v else "no_digest")}
                    if v:
                        ent["n_substantive_changes"] = len(v.d["changes"])
                        ent["to_version_has_group_section"] = group in v.to_groups
                    if v and v.d["comparison"] == "ok" and v.has_group_change(group):
                        ent["group_changed"] = True
                        cres["found"] = True
                    else:
                        ent["group_changed"] = False
                    cres["pairs"].append(ent)
                rowrec["candidates"][s] = cres
                rowrec["section_found"] = rowrec["section_found"] or cres["found"]
            rowrec["section_found_nonsibling"] = any(rowrec["candidates"][s]["found"] for s in p["candidates"] if not sib[s])
            rowrec["has_nonsibling_candidate"] = any(not sib[s] for s in p["candidates"])
            located_pairs = []
            for r in grows:
                for fi, fr in enumerate(p["frs"][r[0]]):
                    big = fr["tokens"] >= MIN_FRAGMENT_TOKENS
                    fclass = "table_referring" if r[5] else ("main" if big else "short")
                    fr_rec = {"drug_name_id": did, "supplement_id": sid, "change_id": r[0], "group": group,
                              "section_number": r[2], "section_title": r[3], "fragment": fr["text"],
                              "tokens": fr["tokens"], "class": fclass, "approval_date": p["approval_date"], "e2e": e2e,
                              "per_candidate": {}, "per_candidate_ws": {}, "per_candidate_strict": {},
                              "found": False, "found_ws": False, "found_strict": False,
                              "sibling_or_repackager": sorted(s for s, f in sib.items() if f)}
                    cand_stats = {}
                    if e2e:
                        for s in p["candidates"]:
                            c_r = c_w = c_s = False
                            cand_stats[s] = []
                            for a, b, gap in p["pairs"][s]:
                                v, st = status(a[2], b[2], fr["text"], group)
                                if st is None:
                                    continue
                                cand_stats[s].append((st, a[1], p["approval_date"]))
                                c_r, c_w, c_s = c_r or st["found"], c_w or st["found_ws"], c_s or st["found_strict"]
                            fr_rec["per_candidate"][s], fr_rec["per_candidate_ws"][s], fr_rec["per_candidate_strict"][s] = c_r, c_w, c_s
                            fr_rec["found"] = fr_rec["found"] or c_r
                            fr_rec["found_ws"] = fr_rec["found_ws"] or c_w
                            fr_rec["found_strict"] = fr_rec["found_strict"] or c_s
                        if fr_rec["found_strict"] and not fr_rec["found_ws"]:
                            cov["INVARIANT_strict_found_but_ws_not"] += 1
                        if fr_rec["found_ws"] and not fr_rec["found"]:
                            cov["INVARIANT_ws_found_but_render_not"] += 1
                        if not fr_rec["found"]:
                            labs = {s: (candidate_label(sts) if sts else (None, None)) for s, sts in cand_stats.items()}
                            pending.append((fr_rec, p, rowrec, cand_stats, labs))
                    if fclass == "main":
                        key = render_key(fr["text"])
                        loc, reason = locate(p, key) if key else (None, NL_NEVER)
                        pr = {"located": loc is not None}
                        if loc is None:
                            pr["not_locatable_reason"] = reason
                        else:
                            pr.update(loc)
                            a_sha, b_sha = sha_of(loc["setid"], loc["from_version"]), sha_of(loc["setid"], loc["to_version"])
                            v, st = status(a_sha, b_sha, fr["text"], group)
                            located_pairs.append((v, loc))
                            pr["comparable"] = st is not None
                            for name, kk in VARIANTS:
                                pr["found_" + name] = bool(st and st[kk])
                            for name, kk in (("strict", "any_strict"), ("ws", "any_ws"), ("render", "any_render")):
                                pr["any_group_" + name] = bool(st and st[kk])
                            if not pr["found_render"]:
                                pr["miss_label"] = located_label(v, st) if st else LL_NOTCOMP
                        fr_rec["parser"] = pr
                    frags_out.append(fr_rec)
            rowrec["parser_located_fragments"] = len(located_pairs)
            if located_pairs:
                rowrec["parser_section_found"] = any(v is not None and v.d["comparison"] == "ok" and v.has_group_change(group)
                                                    for v, _ in located_pairs)
                if not rowrec["parser_section_found"]:
                    best = None
                    for v, _ in located_pairs:
                        if v is None or v.d["comparison"] != "ok":
                            lab = S_NOTCOMP
                        elif not v.d["changes"]:
                            lab = S_NOCHG
                        elif group in v.to_groups:
                            lab = S_OTHER_SEC
                        else:
                            lab = S_OTHER_NOSEC
                        if best is None or _SRANK[lab] < _SRANK[best]:
                            best = lab
                    rowrec["parser_section_miss_label"] = best
            rows_out.append(rowrec)
            if e2e and not rowrec["section_found"]:
                pending.append((None, p, rowrec, None, None))
    log("graded entries; finalizing labels")

    # ---- finalize end-to-end miss labels (presence among all captured versions comes from the full-text scan)
    for fr_rec, p, rowrec, cand_stats, labs in pending:
        if fr_rec is None:
            rowrec["miss_label"], rowrec["miss_detail"] = _section_label(rowrec)
            continue
        key = render_key(fr_rec["fragment"])
        cands = []
        for s, (lab, det) in labs.items():
            if lab is not None:
                cands.append((_RANK[lab], lab, "%s [%s]" % (det, s)))
            elif cand_stats[s]:
                ps = pres.get(s, {})
                vs = {v[0]: v[1] for v in vers(s) if v[2]}
                unchecked = [v for v in vs if ps.get(v) is None]
                present = {v for v, hit in ps.items() if hit is not None and key in hit}
                lab2 = classify_presence(vs, p["approval_date"], present)
                det2 = "present in versions %s of %d captured; %d unreadable [%s]" % (sorted(present), len(vs), len(unchecked), s)
                cands.append((_RANK[lab2], lab2, det2))
            else:
                comp = any(e["comparison"] in ("not_comparable", "error", "no_digest")
                           for e in rowrec["candidates"][s]["pairs"])
                lab2 = L_NOTCOMP if comp else L_NOPAIR
                cands.append((_RANK[lab2], lab2, "%s [%s]" % (lab2, s)))
        if not cands:
            cands.append((_RANK[L_NOPAIR], L_NOPAIR, "no candidate"))
        cands.sort(key=lambda x: x[0])
        fr_rec["miss_label"], fr_rec["miss_detail"] = cands[0][1], cands[0][2]
        fr_rec["miss_label_all_candidates"] = sorted({c[1] for c in cands})

    # ---- GitRx-only changes (pairs in a graded entry's window)
    go = collections.Counter()
    go_rows = []
    for (s, to_ver), g in sorted(graded_pair_keys.items()):
        v = view(*g["shas"])
        if v is None or v.d["comparison"] != "ok":
            continue
        for c in v.d["changes"]:
            grp = c["group"]
            matched = grp in g["srlc_groups"] or (GROUP_MODE == "either" and c.get("alt") in g["srlc_groups"])
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
    e2e_frs = [f for f in frags_out if f["e2e"]]
    main = [f for f in e2e_frs if f["class"] == "main"]
    short = [f for f in e2e_frs if f["class"] == "short"]
    tblf = [f for f in e2e_frs if f["class"] == "table_referring"]
    miss_sec = collections.Counter(r.get("miss_label") for r in sec_rows if not r["section_found"])
    miss_txt = collections.Counter(f.get("miss_label") for f in main if not f["found"])

    def trate(fs, key="found"):
        return rate(sum(1 for f in fs if f[key]), len(fs))

    def tvariants(fs):
        return {name: trate(fs, kk) for name, kk in VARIANTS}

    def by_group(fs, key="found"):
        return {g: trate([f for f in fs if f["group"] == g], key) for g in SRLC_GROUPS}

    def sib_diag_text(fs):
        nn = [f for f in fs if len(f["per_candidate"]) > len(f["sibling_or_repackager"])]
        out = {}
        for name, kk in VARIANTS:
            pc = {"strict": "per_candidate_strict", "ws": "per_candidate_ws", "render": "per_candidate"}[name]
            out[name] = {"all_candidates": trate(fs, kk),
                         "excluding_sibling_candidates": rate(
                             sum(1 for f in nn if any(v for c, v in f[pc].items() if c not in f["sibling_or_repackager"])),
                             len(nn))}
        out["fragments_with_only_sibling_candidates"] = len(fs) - len(nn)
        return out
    nn_rows = [r for r in sec_rows if r["has_nonsibling_candidate"]]
    sib_all = {s for r in sec_rows for s in r["sibling_or_repackager"]}

    # parser accuracy (Amendment 3 item 2)
    pf = [f for f in frags_out if f["class"] == "main" and "parser" in f]
    locf = [f for f in pf if f["parser"]["located"]]

    def prate(fs, key):
        return rate(sum(1 for f in fs if f["parser"][key]), len(fs))

    def pvariants(fs, prefix):
        return {name: prate(fs, prefix + name) for name in ("strict", "ws", "render")}
    prow = [r for r in rows_out if r.get("parser_located_fragments")]
    parser_accuracy = {
        "definition": "Amendment 3 item 2: located = nearest absent->present transition (render matcher on full label "
                      "text) with to-version date in [approval-730 d, approval+365 d]; accuracy = located fragments GitRx "
                      "reports inside added tokens of a substantive change in that pair",
        "main_fragments_in_parser_gradable_entries": len(pf),
        "located": len(locf), "not_locatable": len(pf) - len(locf),
        "not_locatable_reasons": dict(collections.Counter(f["parser"]["not_locatable_reason"] for f in pf if not f["parser"]["located"])),
        "located_pair_comparable": sum(1 for f in locf if f["parser"]["comparable"]),
        "located_delta_days_buckets": dict(collections.Counter(
            "<-365" if f["parser"]["delta_days"] < -365 else ("-365..-31" if f["parser"]["delta_days"] < -30 else
                                                              ("-30..365")) for f in locf)),
        "text_same_group": pvariants(locf, "found_"),
        "text_any_group": pvariants(locf, "any_group_"),
        "text_same_group_by_group": {g: pvariants([f for f in locf if f["group"] == g], "found_") for g in SRLC_GROUPS},
        "text_any_group_by_group": {g: pvariants([f for f in locf if f["group"] == g], "any_group_") for g in SRLC_GROUPS},
        "located_miss_labels": dict(collections.Counter(f["parser"]["miss_label"] for f in locf
                                                        if not f["parser"]["found_render"])),
        "section_accuracy": rate(sum(1 for r in prow if r["parser_section_found"]), len(prow)),
        "section_accuracy_by_group": {g: rate(sum(1 for r in prow if r["group"] == g and r["parser_section_found"]),
                                              sum(1 for r in prow if r["group"] == g)) for g in SRLC_GROUPS},
        "section_miss_labels": dict(collections.Counter(r.get("parser_section_miss_label") for r in prow
                                                        if not r["parser_section_found"])),
        "rows_without_located_fragment": sum(1 for r in rows_out if r["status"] in ("graded", "parser_only")
                                             and not r.get("parser_located_fragments")),
    }
    end_to_end = {
        "definition": "Amendment 1 window [-30 d, +365 d]; strict = literal-v1, ws = Amendment 2, render = Amendment 3 "
                      "(primary for end-to-end)",
        "section_recall": rate(sec_found, len(sec_rows)),
        "section_recall_by_group": {g: rate(sum(1 for r in sec_rows if r["group"] == g and r["section_found"]),
                                            sum(1 for r in sec_rows if r["group"] == g)) for g in SRLC_GROUPS},
        "text_recall_main": tvariants(main),
        "text_recall_main_by_group": {name: by_group(main, kk) for name, kk in VARIANTS},
        "text_short_fragments_lt3_tokens": tvariants(short),
        "text_table_referring_rows": tvariants(tblf),
        "section_miss_labels": dict(miss_sec),
        "text_miss_labels": dict(miss_txt),
    }
    summary = {
        "harness_version": HARNESS_VERSION, "split": mode, "parser_version": parser_version, "group_mode": GROUP_MODE,
        "changes_version": changes.CHANGES_VERSION, "normalization": spl_parser.NORMALIZATION,
        "text_match_primary": "render (Amendment 3: literal-v1 + casefold + Cf removed + leading list markers stripped "
                              "+ SrLC '?' = one non-ASCII char + whitespace removed); ws = Amendment 2; strict = literal-v1",
        "window_days": [-WINDOW_BEFORE_DAYS, WINDOW_AFTER_DAYS], "min_fragment_tokens": MIN_FRAGMENT_TOKENS,
        "locate_range_days": [-LOCATE_BEFORE_DAYS, LOCATE_AFTER_DAYS],
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "coverage": dict(cov),
        "mapping": {"drugs_in_split_with_detail": len({p["drug_name_id"] for p in plans}),
                    "drugs_with_candidate_setid": len({p["drug_name_id"] for p in plans if p["candidates"]}),
                    "setids_unresolved_raw_pairs": len(unresolved)},
        "parser_accuracy": parser_accuracy,
        "end_to_end": end_to_end,
        # legacy top-level keys (render is the primary text variant)
        "section_recall": end_to_end["section_recall"],
        "section_recall_by_group": end_to_end["section_recall_by_group"],
        "text_recall_main": end_to_end["text_recall_main"]["render"],
        "text_recall_main_strict": end_to_end["text_recall_main"]["strict"],
        "text_recall_main_ws": end_to_end["text_recall_main"]["ws"],
        "section_miss_labels": end_to_end["section_miss_labels"],
        "text_miss_labels": end_to_end["text_miss_labels"],
        "text_short_fragments_lt3_tokens": end_to_end["text_short_fragments_lt3_tokens"]["render"],
        "text_table_referring_rows": end_to_end["text_table_referring_rows"]["render"],
        "sibling_or_repackager_diagnostic": {
            "definition": "candidate setid none of whose SPL product names/title contains the SrLC drug_name "
                          "(parenthetical removed) or its first word, case/whitespace-insensitive; diagnostic only, "
                          "primary metrics count any candidate (Amendment 1)",
            "candidate_setids_flagged": len(sib_all), "candidate_setids_total": len(all_cands),
            "candidate_setids_without_names": len([c for c in all_cands if not cand_names.get(c)]),
            "section_rows": {"all_candidates": rate(sec_found, len(sec_rows)),
                             "excluding_sibling_candidates": rate(sum(1 for r in nn_rows if r["section_found_nonsibling"]), len(nn_rows)),
                             "rows_with_only_sibling_candidates": len(sec_rows) - len(nn_rows)},
            "text_main": sib_diag_text(main)},
        "gitrx_only": dict(go),
        "digest_build": dict(build_status),
        "full_text_presence": {"setids": len(ptasks), "version_reads": nver,
                               "unreadable_versions": sum(1 for r in pres.values() for h in r.values() if h is None) +
                               sum(1 for s in need_keys for v in vers(s) if v[2] and v[0] not in pres.get(s, {}))},
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
    _ssd_guard(out_dir)
    for name, recs in (("rows.jsonl", rows_out), ("fragments.jsonl", frags_out), ("gitrx_only.jsonl", go_rows)):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            for r in recs:
                f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    with open(os.path.join(out_dir, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1, sort_keys=True, ensure_ascii=False)
    log("wrote %s" % out_dir)
    return summary


def _section_label(rowrec):
    """Descriptive label for a section row no candidate's compared pair marked as changed in the group."""
    best = None
    pairs_any = False
    for c in rowrec["candidates"].values():
        for e in c["pairs"]:
            pairs_any = True
            if e["comparison"] != "ok":
                lab = S_NOTCOMP
            elif e.get("n_substantive_changes", 0) == 0:
                lab = S_NOCHG
            elif e.get("to_version_has_group_section"):
                lab = S_OTHER_SEC
            else:
                lab = S_OTHER_NOSEC
            if best is None or _SRANK[lab] < _SRANK[best]:
                best = lab
    if not pairs_any:
        best = S_NOPAIR
    gap = any(e["version_gap"] for c in rowrec["candidates"].values() for e in c["pairs"])
    return best, "%d candidate(s), pairs span a version gap: %s" % (len(rowrec["candidates"]), gap)


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
            sp.add_argument("--group-mode", default="either", choices=["either", "primary"],
                            help="Amendment 4: accept alt_group as same-group (either) or srlc_group only (primary)")
    a = ap.parse_args(argv)
    if a.cmd == "index":
        ip = a.index or os.path.join(a.srlc_root, "grading", "cache", "index.sqlite")
        build_index(a.evidence_db, ip, a.workers, a.raw_manifest, limit=a.limit, scan_all_versions=a.scan_all_versions)
        return 0
    a.gradebook = a.gradebook or os.path.join(a.srlc_root, "gradebook.sqlite")
    if a.out_dir and os.path.exists(a.out_dir) and os.listdir(a.out_dir):
        raise SystemExit("refusing: --out-dir %s already exists and is not empty" % a.out_dir)
    s = run_grade(a)
    print(json.dumps({k: s[k] for k in ("split", "parser_version", "coverage", "mapping", "parser_accuracy",
                                        "end_to_end", "gitrx_only")}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
