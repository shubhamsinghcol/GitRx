"""Precision of GitRx change records against the FDA SrLC database (open split only).

Definition (all of it is mechanical; nothing here is tuned):

* Unit: one GitRx change record entry, i.e. one element of ``changes`` in the comparison of two
  consecutive captured versions (from-version, to-version) of one DailyMed setid.  A section that
  is added, removed or modified is one unit.  Only units whose SrLC group (``srlc_group``) is one
  of the seven SrLC groups (BW, C, WP, AR, DI, USP, MG) are in the denominator; units in
  sections SrLC does not cover (indications, dosage, how supplied, ...) are counted separately as
  ``non_srlc_section`` and are never errors.
* Population: the *graded pairs* of the open split.  For every SrLC supplement of an open drug
  (DrugNameID) that has candidate setids (setids whose SPL approvals contain the SrLC application
  number) and at least one captured candidate version published in [approval - 30 d,
  approval + 365 d] (Amendment 1), every consecutive captured pair of a candidate whose
  to-version is in that window is a graded pair.  A pair graded for several supplements is
  counted once, and a unit "matches" if *any* of those supplements has a change row in the same
  SrLC group.
* Crude match (``matched``): the unit's SrLC group (or, in group-mode ``either``, its alternative
  group; Amendment 4) equals the SrLC group of at least one SrLC change row of a supplement that
  graded the pair.  This is exactly the harness ``matched_by_srlc`` and is cross-checked against
  the kept ``gitrx_only.jsonl`` / ``summary.json`` of the grading run.
  Crude precision = matched / (matched + unmatched), SrLC-group units only.
* Timing-robust match (``timing``): crude match, or an SrLC supplement of the same drug with the
  same SrLC group whose approval date lies in [to-version date - 365 d, to-version date + 730 d].
  This is the window of the parser-accuracy locator (Amendment 3: to-version in
  [approval - 730 d, approval + 365 d]) and credits labels that changed before or long after the
  FDA approval date because DailyMed publication and FDA approval dates are different things.
* Text-level match (``text_hit``, reported only as an extra column): the unit's added tokens
  contain a >= 3-token SrLC underlined fragment of the same drug and SrLC group.
* GitRx-only is not "false positive".  SrLC lists only safety-related labeling changes, only for
  NDA/BLA holders and (in this crawl) only from 2016.  A change GitRx reports and SrLC lacks may be
  a real non-safety edit, an edit SrLC did not record (SrLC recall is unknown), a timing
  difference, or noise.  The categories below are descriptive heuristics, not adjudications.
* ``reference_only`` units (only cross-reference numbers renumbered) are kept as changes because
  SrLC itself underlines such edits (owner decision, srlc-changes-0.3.2); the report gives
  precision with them counted and with them excluded.
* Units are clustered (many pairs per label, many labels per drug) so the Wilson intervals
  printed here are descriptive lower bounds on uncertainty, not independent-sample intervals.

Read-only on all inputs.  Writes only to ``--out``.

    python -m gitrx_core.srlc_precision --grading-dir <open grading dir> --out <new dir>
"""
import argparse
import collections
import concurrent.futures as cf
import datetime as dt
import difflib
import json
import os
import re
import sqlite3
import sys

from . import changes as CH
from . import dataroot
from . import spl_parser as SP
from . import srlc_grade as G

PRECISION_VERSION = "srlc-precision-0.1.0"
SRG = ("BW", "C", "WP", "AR", "DI", "USP", "MG")
DEFAULT_ROOT = dataroot.DATA_ROOT + "/srlc"
DEFAULT_GRADEBOOK = DEFAULT_ROOT + "/gradebook.sqlite"
DEFAULT_SPLIT = DEFAULT_ROOT + "/split.json"
DEFAULT_INDEX = DEFAULT_ROOT + "/grading/cache/index-allversions.sqlite"
DEFAULT_CACHE = DEFAULT_ROOT + "/grading/cache/pairs"
RAW_INTERNAL = dataroot.DATA_ROOT + "/raw-internal-20261111/sha256"
TIMING_BEFORE_DAYS = 365   # SrLC approval may be up to 365 d before the to-version publication ...
TIMING_AFTER_DAYS = 730    # ... or up to 730 d after it

# ----------------------------------------------------------------------------- formatting-noise canonicalization
# (analysis-only; independent of the frozen changes.py noise rules).  A unmatched unit is "formatting"
# if its added and removed tokens are equal after these rules, or the whole sections are equal after
# removing cross-references, dates and list numbering.
_TOK = re.compile(r"\S+")
_XREF = re.compile(r"^[\(\[]\d+(\.\d+)*(,\s*\d+(\.\d+)*)*[\)\]][.,;:]*$")
_DATE = re.compile(r"^(\d{1,2}/\d{4}|\d{1,2}/\d{1,2}/\d{2,4}|\d{4}-\d{2}-\d{2})[.,;]*$")
_LISTM = re.compile(r"^[\(\[]?([a-zA-Z]|\d{1,2}|[ivxIVX]+)[\)\].]$")
_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|"
          r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_CAN_RES = [
    re.compile(r"\[\s*see\b[^\]]*\]", re.I),
    re.compile(r"\(\s*\d+(?:\.\d+)*(?:\s*[,;]\s*\d+(?:\.\d+)*)*\s*\)"),
    re.compile(r"\[FOOTNOTE:[^\]]*\]"),
    re.compile(r"\b(?:revised|rev\.?|issued|updated)\s*:?\s*(?:" + _MONTH + r"\s+)?\d{0,2}/?\d{2,4}\b", re.I),
    re.compile(r"\b" + _MONTH + r"\s+\d{4}\b", re.I),
    re.compile(r"\b\d{1,2}/\d{4}\b"),
    re.compile(r"(?m)^\s*(?:\d{1,2}|[a-z])[.)]\s+"),
]


def canon(tok):
    t = tok.replace("®", "").replace("™", "").replace("©", "").replace("℠", "")
    if _XREF.match(t) or _DATE.match(t) or _LISTM.match(t) and not t.isalpha():
        return ""
    return re.sub(r"[\W_]+", "", t.casefold())


def canon_full(txt):
    t = G.normalize(txt or "")
    for rx in _CAN_RES:
        t = rx.sub(" ", t)
    t = t.replace("®", "").replace("™", "").replace("©", "")
    return re.sub(r"[\W_]+", "", t.casefold())


def chan_tokens(txt):
    return [G.normalize(m.group()) for m in _TOK.finditer(txt or "")]


def diff_channel_tokens(a, b):
    """Token-level added/removed tokens between two channel texts (prefix/suffix trimmed, then difflib).
    Returns None when the middle part is too large to diff (> 4M cells and > 3000 tokens each)."""
    ta = [t for t in chan_tokens(a) if t]
    tb = [t for t in chan_tokens(b) if t]
    i = 0
    while i < len(ta) and i < len(tb) and ta[i] == tb[i]:
        i += 1
    j = 0
    while j < len(ta) - i and j < len(tb) - i and ta[-1 - j] == tb[-1 - j]:
        j += 1
    ma, mb = ta[i:len(ta) - j], tb[i:len(tb) - j]
    if len(ma) * len(mb) > 4_000_000 and len(ma) > 3000 and len(mb) > 3000:
        return None
    sm = difflib.SequenceMatcher(None, ma, mb, autojunk=False)
    add, rem = [], []
    for op, a1, a2, b1, b2 in sm.get_opcodes():
        if op in ("replace", "delete"):
            rem += ma[a1:a2]
        if op in ("replace", "insert"):
            add += mb[b1:b2]
    return add, rem, len(ta), len(tb)


# ----------------------------------------------------------------------------- per-pair unit extraction
def _accepts(group_mode, change_group, change_alt, srlc_group):
    return srlc_group == change_group or (group_mode == "either" and change_alt is not None and srlc_group == change_alt)


def _refonly(c, fr, to):
    """True when every substantive channel diff of a modified SrLC-group section is cross-reference-only.
    Uses the installed changes.diff_channel, so it is only meaningful for digests built by that version."""
    if c["kind"] != "modified" or c.get("ordinal_to") is None or c.get("ordinal_from") is None:
        return False
    fs, ts = fr.get(c["ordinal_from"]), to.get(c["ordinal_to"])
    if not (fs and ts):
        return False
    flags = []
    for ch in G.CHANNELS:
        d = CH.diff_channel(fs["ch"].get(ch, ""), ts["ch"].get(ch, ""), 0, 0, ch)
        if d["kind"] == "substantive":
            flags.append(d["reference_only"])
    return bool(flags) and all(flags)


def load_digest_multi(cache_dirs, key):
    for cd in cache_dirs:
        d = G.load_digest(cd, key)
        if d is not None:
            return d
    return None


def process_pair(task):
    """task = (cache_dirs, key, groups, group_mode, with_refonly).  -> (key, [unit records] | None)."""
    cache_dirs, key, groups, group_mode, with_refonly = task
    d = load_digest_multi(cache_dirs, key)
    if d is None or d["comparison"] != "ok":
        return key, None
    to = {s["o"]: s for s in d["to_sections"]}
    fr = {s["o"]: s for s in d["from_sections"]}
    out = []
    for c in d["changes"]:
        grp, alt = c["group"], c.get("alt")
        matched = any(_accepts(group_mode, grp, alt, g) for g in groups)
        rec = {"kind": c["kind"], "group": grp, "alt": alt, "num": c["section_number"], "title": c["section_title"],
               "matched": matched, "srg": grp in SRG, "refonly": False}
        if rec["srg"] and with_refonly:
            rec["refonly"] = _refonly(c, fr, to)
        if rec["srg"] and not matched:
            ts = to.get(c["ordinal_to"]) if c.get("ordinal_to") is not None else None
            fs = fr.get(c["ordinal_from"]) if c.get("ordinal_from") is not None else None
            add, rem, nto, nfr, big, chans = [], [], 0, 0, False, []
            for ch in G.CHANNELS:
                a = fs["ch"].get(ch, "") if fs else ""
                b = ts["ch"].get(ch, "") if ts else ""
                if a == b:
                    continue
                r = diff_channel_tokens(a, b)
                if r is None:
                    big = True
                    continue
                ad, rm, na, nb = r
                if ad or rm:
                    chans.append(ch)
                add += [(ch, x) for x in ad]
                rem += [(ch, x) for x in rm]
                nto += nb
                nfr += na
            ja = "".join(canon(t) for _, t in add)
            jr = "".join(canon(t) for _, t in rem)
            fa = "".join(canon_full(fs["ch"].get(ch, "")) for ch in G.CHANNELS) if fs else ""
            fb = "".join(canon_full(ts["ch"].get(ch, "")) for ch in G.CHANNELS) if ts else ""
            rec.update({"nadd": len(add), "nrem": len(rem), "chans": chans, "big": big,
                        "fmt_tok": ja == jr, "fmt2": bool(fs and ts and fa == fb),
                        "add": " ".join(t for _, t in add)[:6000], "rem": " ".join(t for _, t in rem)[:6000],
                        "nto": nto, "nfr": nfr})
            rec["fmt"] = bool(rec["fmt_tok"] or rec["fmt2"])
        out.append(rec)
    return key, out


# ----------------------------------------------------------------------------- graded-pair plan
def open_ids(split_path):
    return set(json.load(open(split_path))["open"])


def graded_pairs(gb, ix, ids, parser_version, changes_version):
    """The pairs the harness grades for the given drug ids (mirrors srlc_grade.run_grade planning)."""
    cache = {}

    def vers(s):
        if s not in cache:
            cache[s] = G.candidate_versions(ix, s)
        return cache[s]
    graded = {}
    for p in G.plan_entries(gb, ix, sorted(ids)):
        if not p["approval_date"] or not p["candidates"]:
            continue
        if not any(any(v[2] and G.in_window(v[1], p["approval_date"]) for v in vers(s)) for s in p["candidates"]):
            continue
        rows = gb.execute("SELECT section_group FROM changes WHERE supplement_id=?", (p["supplement_id"],)).fetchall()
        for s in p["candidates"]:
            for a, b, _gap in G.captured_pairs(vers(s)):
                if not G.in_window(b[1], p["approval_date"]):
                    continue
                k = G.digest_key(a[2], b[2], parser_version, changes_version)
                g = graded.setdefault((s, b[0]), {"key": k, "setid": s, "from": a[0], "to": b[0], "to_pub": b[1],
                                                  "rows": (a, b), "groups": set(), "drugs": set(), "entries": set()})
                g["drugs"].add(p["drug_name_id"])
                g["groups"].update(r[0] for r in rows)
                g["entries"].add(p["supplement_id"])
    return graded


def ensure_digests(graded, cache_dirs, build_dir, index_path, parser_version, changes_version, workers=3, log=None):
    """Build (into build_dir only) the pair digests that no cache dir has, from the raw ZIPs, with the installed
    parser/changes code.  Refuses when the installed code is not the version the grading run used."""
    missing = {k: g for k, g in ((g["key"], g) for g in graded.values()) if load_digest_multi(cache_dirs, k) is None}
    if not missing:
        return {"built": 0, "present": len(graded), "unresolved_raw": 0}
    if (SP.PARSER_VERSION, CH.CHANGES_VERSION) != (parser_version, changes_version):
        raise SystemExit("cannot rebuild %d missing digests: installed code is %s/%s but the grading run used %s/%s; "
                         "run this module from a checkout of the frozen commit (see VERIFY.md)"
                         % (len(missing), SP.PARSER_VERSION, CH.CHANGES_VERSION, parser_version, changes_version))
    ix = sqlite3.connect("file:%s?mode=ro" % index_path, uri=True)
    base = G.Resolver(dict(ix.execute("SELECT sha256, path FROM paths")), None)

    def resolve(sha):
        # flat network/bodies, the index's recorded path, then the SSD copy of the former internal-disk objects
        return base(sha) or next((p for p in (os.path.join(RAW_INTERNAL, sha[:2], sha),) if os.path.exists(p)), None)
    chains, unresolved = collections.defaultdict(list), 0
    for g in sorted(missing.values(), key=lambda g: (g["setid"], g["to"])):
        a, b = g["rows"]
        pa, pb = resolve(a[2]), resolve(b[2])
        if pa is None or pb is None:
            unresolved += 1
            continue
        chains[g["setid"]].append((a[2], a[3], pa, b[2], b[3], pb))
    tasks = [(build_dir, ch) for ch in chains.values()]
    st = collections.Counter()
    with cf.ProcessPoolExecutor(workers) as ex:
        for n, res in enumerate(ex.map(G._digest_task, tasks, chunksize=1), 1):
            for _k, s_ in res:
                st[s_.split(":")[0]] += 1
            if log and n % 200 == 0:
                log("digest tasks %d/%d %s" % (n, len(tasks), dict(st)))
    return {"built": st["built"], "cached": st["cached"], "errors": st["error"], "unresolved_raw": unresolved,
            "present": len(graded) - len(missing)}


def collect(graded, cache_dirs, group_mode="either", with_refonly=True, workers=3, progress=None):
    """Run process_pair over all graded pairs.  Returns (records, counters).  Records are SrLC-group units."""
    tasks = [(tuple(cache_dirs), g["key"], sorted(x for x in g["groups"] if x), group_mode, with_refonly)
             for g in graded.values()]
    by_key = {}
    if workers and workers > 1:
        with cf.ProcessPoolExecutor(workers) as ex:
            for n, (k, out) in enumerate(ex.map(process_pair, tasks, chunksize=8), 1):
                by_key[k] = out
                if progress and n % 1000 == 0:
                    progress(n, len(tasks))
    else:
        for n, t in enumerate(tasks, 1):
            k, out = process_pair(t)
            by_key[k] = out
            if progress and n % 1000 == 0:
                progress(n, len(tasks))
    cnt = collections.Counter()
    recs = []
    for g in graded.values():
        out = by_key.get(g["key"])
        if out is None:
            cnt["pair_not_ok_or_missing_digest"] += 1
            continue
        cnt["pairs_ok"] += 1
        for r in out:
            cnt["matched" if r["matched"] else ("unmatched_srlc_group" if r["srg"] else "non_srlc_section")] += 1
            if r["srg"]:
                r2 = dict(r)
                r2.update({"setid": g["setid"], "from": g["from"], "to": g["to"], "to_pub": g["to_pub"],
                           "drugs": sorted(g["drugs"]), "groups": sorted(x for x in g["groups"] if x)})
                recs.append(r2)
    return recs, cnt


# ----------------------------------------------------------------------------- timing and text-level matches
_TOKR = re.compile(r"[0-9a-z]+")


def _grams(toks, n=5):
    return {tuple(toks[i:i + n]) for i in range(len(toks) - n + 1)}


class SrlcIndex:
    """SrLC supplements and underlined fragments of the given drugs, for timing / text-level matching."""

    def __init__(self, gb, ids):
        ids = set(ids)
        self.sup = collections.defaultdict(list)    # drug -> [date, supplement_id, set(groups)]
        self.frag = collections.defaultdict(list)   # drug -> [(group, tokens, supplement_id)]
        for did, sid, adate in gb.execute("SELECT drug_name_id, supplement_id, approval_date_iso FROM supplements"):
            if did in ids:
                self.sup[did].append([G.parse_date(adate), sid, set()])
        idx = {(d, s[1]): s for d, lst in self.sup.items() for s in lst}
        for did, sid, grp, spans in gb.execute(
                "SELECT drug_name_id, supplement_id, section_group, underlined_spans_json FROM changes"):
            if did not in ids:
                continue
            idx[(did, sid)][2].add(grp)
            for fr in G.split_fragments(json.loads(spans or "[]")):
                toks = _TOKR.findall(fr["text"].casefold())
                if len(toks) >= 3:
                    self.frag[did].append((grp, toks, sid))
        self.gset, self.short = {}, {}
        for d, lst in self.frag.items():
            gd, sh = collections.defaultdict(set), []
            for grp, toks, sid in lst:
                if len(toks) >= 5:
                    for g in _grams(toks):
                        gd[g].add((grp, sid))
                else:
                    sh.append((grp, " ".join(toks), sid))
            self.gset[d], self.short[d] = gd, sh

    def timing_match(self, r, group_mode):
        to = G.parse_date(r["to_pub"])
        lo, hi = to - dt.timedelta(days=TIMING_BEFORE_DAYS), to + dt.timedelta(days=TIMING_AFTER_DAYS)
        for d in r["drugs"]:
            for date, _sid, groups in self.sup.get(d, ()):
                if date and lo <= date <= hi and any(_accepts(group_mode, r["group"], r["alt"], g) for g in groups):
                    return True
        return False

    def text_match(self, r, group_mode, anygroup=False):
        toks = _TOKR.findall(r.get("add", "").casefold())
        txt = " " + " ".join(toks) + " "
        hits = []

        def ok(grp):
            return anygroup or _accepts(group_mode, r["group"], r["alt"], grp)
        for d in r["drugs"]:
            gd = self.gset.get(d, {})
            for g in _grams(toks):
                for grp, sid in gd.get(g, ()):
                    if ok(grp):
                        hits.append((d, sid, grp))
            for grp, j, sid in self.short.get(d, ()):
                if ok(grp) and (" " + j + " ") in txt:
                    hits.append((d, sid, grp))
            if 3 <= len(toks) < 5:
                j = " ".join(toks)
                for grp, ftoks, sid in self.frag.get(d, ()):
                    if ok(grp) and j in " ".join(ftoks):
                        hits.append((d, sid, grp))
        return hits


def attach_matches(recs, sidx, group_mode="either"):
    for r in recs:
        r["timing"] = bool(r["matched"]) or sidx.timing_match(r, group_mode)
        if not r["matched"] and r.get("add"):
            h = sidx.text_match(r, group_mode)
            r["text_hit"] = bool(h)
            r["text_hit_any"] = bool(h) or bool(sidx.text_match(r, group_mode, True))
        else:
            r["text_hit"] = r["text_hit_any"] = False
    return recs


# ----------------------------------------------------------------------------- categorization of unmatched units
_SAFE = re.compile(r"risk|warning|contraindicat|fatal|death|died|mortality|hepatotox|liver|serious|life-threatening|"
                   r"severe|anaphyla|hypersensitiv|toxic|suicid|overdos|boxed|discontinu|monitor|\bQT|cardiac|cardio|"
                   r"hemorrhag|bleed|abuse|dependence|embryo|fetal|teratogen|malignan|cancer|lactic|angioedema|stevens|"
                   r"seizure|hepatic failure|renal failure|infection", re.I)
_NONSAFE = re.compile(r"indicat|dose|dosage|\bmg\b|trial|study|studies|efficacy|response|pharmacokinetic|population|"
                      r"pediatric|years of age|patients with", re.I)
CATEGORIES = ("a_formatting_noise", "b_timing_other_supplement_text", "b_timing_group_level", "c1_table_data",
              "c2_editorial_rewording", "c3_non_safety_content", "c4_possible_srlc_omission_safety_kw",
              "c5_other_text", "d_pure_deletion", "d_too_large_to_diff", "d_no_text_delta")


def categorize(r):
    """Descriptive, ordered heuristic for an unmatched SrLC-group unit.  Not an adjudication: c4 means only that a
    safety keyword occurs in the added text; c5 is the unexplained residual."""
    if r.get("fmt"):
        return "a_formatting_noise"
    if r["text_hit"]:
        return "b_timing_other_supplement_text"
    if r["timing"]:
        return "b_timing_group_level"
    add, rem = r.get("add", ""), r.get("rem", "")
    if r.get("nadd", 0) == 0 and r.get("nrem", 0) > 0:
        return "d_pure_deletion"
    if r.get("big"):
        return "d_too_large_to_diff"
    if r.get("nadd", 0) == 0:
        return "d_no_text_delta"
    if r["chans"] and set(r["chans"]) == {"table"}:
        return "c1_table_data"
    if r["nrem"] > 0:
        ta, tr = add.split(), rem.split()
        if len(ta) <= 4000 and len(tr) <= 4000 and difflib.SequenceMatcher(None, ta, tr, autojunk=False).ratio() >= 0.6:
            return "c2_editorial_rewording"
    if _SAFE.search(add):
        return "c4_possible_srlc_omission_safety_kw"
    if _NONSAFE.search(add):
        return "c3_non_safety_content"
    return "c5_other_text"


def fmt_kind(r):
    """Sub-type of a formatting-noise unit (case / spacing / punctuation / ...)."""
    a, b = r.get("add", ""), r.get("rem", "")
    if r.get("fmt2") and not r.get("fmt_tok"):
        return "section_level_canon_equal"
    if a.casefold() == b.casefold() and a != b:
        return "case_only"
    if re.sub(r"\s+", "", a) == re.sub(r"\s+", "", b):
        return "space_only"
    if re.sub(r"[^\w]", "", a) == re.sub(r"[^\w]", "", b):
        return "punct_only"
    if re.sub(r"[\W_]+", "", a.casefold()) == re.sub(r"[\W_]+", "", b.casefold()):
        return "case_punct"
    return "other"


# ----------------------------------------------------------------------------- summaries
def _pct(k, n):
    return round(100.0 * k / n, 2) if n else None


def _row(rs):
    n = len(rs)
    m = sum(r["matched"] for r in rs)
    t = sum(r["timing"] for r in rs)
    x = sum(r["matched"] or r["text_hit"] for r in rs)
    y = sum(r["timing"] or r["text_hit"] for r in rs)
    lo, hi = G.wilson(m, n) if n else (None, None)
    return {"n": n, "matched": m, "unmatched": n - m, "crude_pct": _pct(m, n),
            "crude_wilson95_pct": [round(100 * lo, 2), round(100 * hi, 2)] if n else None,
            "timing_robust_pct": _pct(t, n), "text_level_pct": _pct(x, n), "timing_or_text_pct": _pct(y, n)}


def table(recs, flt=lambda r: True):
    return {g: _row([r for r in recs if (g == "ALL" or r["group"] == g) and flt(r)]) for g in list(SRG) + ["ALL"]}


def summarize(recs):
    un = [r for r in recs if not r["matched"]]
    cat_all = collections.Counter()
    cat_g = collections.defaultdict(collections.Counter)
    fk = collections.Counter()
    for r in un:
        c = categorize(r)
        cat_all[c] += 1
        cat_g[r["group"]][c] += 1
        if c == "a_formatting_noise":
            fk[fmt_kind(r)] += 1
    tot = len(recs)
    m = sum(r["matched"] for r in recs)
    a = cat_all["a_formatting_noise"]
    d = cat_all["d_pure_deletion"] + cat_all["d_no_text_delta"] + cat_all["d_too_large_to_diff"]
    c5 = cat_all["c5_other_text"]
    bounds = {"n": tot, "matched": m, "crude_pct": _pct(m, tot),
              "only_formatting_noise_is_error_pct": _pct(tot - a, tot),
              "formatting_plus_deletions_are_errors_pct": _pct(tot - a - d, tot),
              "formatting_deletions_unexplained_residual_are_errors_pct": _pct(tot - a - d - c5, tot)}
    by_group_bounds = {}
    for g in SRG:
        n = sum(1 for r in recs if r["group"] == g)
        mm = sum(1 for r in recs if r["group"] == g and r["matched"])
        ag = cat_g[g]["a_formatting_noise"]
        dg = sum(cat_g[g][k] for k in ("d_pure_deletion", "d_no_text_delta", "d_too_large_to_diff"))
        cg = cat_g[g]["c5_other_text"]
        by_group_bounds[g] = {"n": n, "crude_pct": _pct(mm, n), "formatting_only_pct": _pct(n - ag, n),
                              "formatting_deletions_pct": _pct(n - ag - dg, n),
                              "formatting_deletions_residual_pct": _pct(n - ag - dg - cg, n)}
    ro = {}
    for g in list(SRG) + ["ALL"]:
        rs = [r for r in recs if g == "ALL" or r["group"] == g]
        refs = [r for r in rs if r["refonly"]]
        ro[g] = {"counted": _row(rs), "reference_only_units": len(refs),
                 "reference_only_matched": sum(r["matched"] for r in refs),
                 "excluded": _row([r for r in rs if not r["refonly"]])}
    return {
        "all_kinds": table(recs),
        "modified": table(recs, lambda r: r["kind"] == "modified"),
        "added_section": table(recs, lambda r: r["kind"] == "added_section"),
        "removed_section": table(recs, lambda r: r["kind"] == "removed_section"),
        "excluding_formatting_noise_from_denominator": table(recs, lambda r: r["matched"] or not r.get("fmt")),
        "reference_only": ro,
        "formatting": {"unmatched_total": len(un), "formatting_noise": a, "share_of_unmatched_pct": _pct(a, len(un)),
                       "by_kind": dict(fk)},
        "unmatched_categories": {"n": len(un), "counts": dict(cat_all),
                                 "share_pct": {k: _pct(v, len(un)) for k, v in cat_all.items()},
                                 "by_group": {g: dict(cat_g[g]) for g in SRG}},
        "bounds": bounds,
        "bounds_by_group": by_group_bounds,
    }


# ----------------------------------------------------------------------------- kept grading outputs
def kept_precision(grading_dir):
    """Crude precision recomputed from the kept harness outputs alone (summary.json + gitrx_only.jsonl)."""
    s = json.load(open(os.path.join(grading_dir, "summary.json")))
    go = s["gitrx_only"]
    by_cat = collections.Counter()
    unm_g = collections.Counter()
    with open(os.path.join(grading_dir, "gitrx_only.jsonl")) as f:
        for line in f:
            d = json.loads(line)
            by_cat[d["category"]] += 1
            if d["category"] == "gitrx_only_srlc_group":
                unm_g[d["group"]] += 1
    m, u = go["matched_by_srlc"], by_cat["gitrx_only_srlc_group"]
    consistent = (by_cat["gitrx_only_srlc_group"] == go["gitrx_only_srlc_group"]
                  and by_cat["gitrx_only_non_srlc_section"] == go["gitrx_only_non_srlc_section"])
    return {"matched_by_srlc": m, "gitrx_only_srlc_group": u,
            "gitrx_only_non_srlc_section": by_cat["gitrx_only_non_srlc_section"],
            "crude_precision_pct": _pct(m, m + u), "jsonl_consistent_with_summary": consistent,
            "unmatched_by_group": dict(unm_g), "group_mode": s.get("group_mode"),
            "parser_version": s.get("parser_version"), "changes_version": s.get("changes_version")}


def compare_with_kept(recs, grading_dir):
    """The recomputed unmatched SrLC-group units must equal the kept gitrx_only.jsonl rows (multiset)."""
    mine = collections.Counter((r["setid"], r["from"], r["to"], r["group"], r["num"], r["title"], r["kind"])
                               for r in recs if not r["matched"])
    kept = collections.Counter()
    with open(os.path.join(grading_dir, "gitrx_only.jsonl")) as f:
        for line in f:
            d = json.loads(line)
            if d["category"] == "gitrx_only_srlc_group":
                kept[(d["setid"], d["from_version"], d["to_version"], d["group"], d["section_number"],
                      d["section_title"], d["kind"])] += 1
    s = json.load(open(os.path.join(grading_dir, "summary.json")))["gitrx_only"]
    return {"unmatched_identical_to_kept_gitrx_only_jsonl": mine == kept,
            "unmatched_recomputed": sum(mine.values()), "unmatched_kept": sum(kept.values()),
            "matched_recomputed": sum(1 for r in recs if r["matched"]), "matched_kept": s["matched_by_srlc"]}


# ----------------------------------------------------------------------------- driver
def run(grading_dir, *, split_path=DEFAULT_SPLIT, gradebook=DEFAULT_GRADEBOOK, index=DEFAULT_INDEX,
        cache_dirs=(DEFAULT_CACHE,), build_dir=None, workers=3, progress=None, ids=None, log=None):
    """Full recomputation for one *open* grading run.  Refuses sealed runs/ids."""
    summ = json.load(open(os.path.join(grading_dir, "summary.json")))
    if summ.get("split") != "open":
        raise SystemExit("refusing: precision analysis is defined for the open split only (summary split=%r)" % summ.get("split"))
    group_mode = summ["group_mode"]
    pv, cv = summ["parser_version"], summ["changes_version"]
    sp = json.load(open(split_path))
    if ids is None:
        ids = set(sp["open"])
    if ids & set(sp["sealed"]):
        raise SystemExit("refusing: sealed DrugNameIDs requested")
    ix = sqlite3.connect("file:%s?mode=ro" % index, uri=True)
    gb = sqlite3.connect("file:%s?mode=ro" % gradebook, uri=True)
    graded = graded_pairs(gb, ix, ids, pv, cv)
    cache_dirs = [d for d in cache_dirs if d and os.path.isdir(d)]
    if build_dir:
        os.makedirs(build_dir, exist_ok=True)
        cache_dirs.append(build_dir)
    built = ensure_digests(graded, cache_dirs, build_dir, index, pv, cv, workers, log) if build_dir else None
    recs, cnt = collect(graded, cache_dirs, group_mode, with_refonly=(cv == CH.CHANGES_VERSION and cv >= "srlc-changes-0.3.2"),
                        workers=workers, progress=progress)
    attach_matches(recs, SrlcIndex(gb, ids), group_mode)
    res = {"precision_version": PRECISION_VERSION, "grading_dir": grading_dir, "parser_version": pv,
           "changes_version": cv, "group_mode": group_mode, "graded_pairs": len(graded), "digest_build": built,
           "counters": dict(cnt),
           "reference_only_available": cv == CH.CHANGES_VERSION and cv >= "srlc-changes-0.3.2",
           "kept": kept_precision(grading_dir),
           "kept_crosscheck": compare_with_kept(recs, grading_dir) if len(ids) == len(sp["open"]) else None,
           "summary": summarize(recs)}
    return res, recs


def format_report(res):
    s = res["summary"]
    L = ["srlc-precision %s  %s / %s  group-mode %s  graded pairs %d" % (
        res["precision_version"], res["parser_version"], res["changes_version"], res["group_mode"], res["graded_pairs"])]
    k = res["kept"]
    L.append("kept harness outputs: matched %d, unmatched SrLC-group %d -> crude %.2f%%  (non-SrLC-section units: %d, not scored)" % (
        k["matched_by_srlc"], k["gitrx_only_srlc_group"], k["crude_precision_pct"], k["gitrx_only_non_srlc_section"]))
    hdr = "%-5s %7s %7s %7s | %7s | %7s | %7s | %7s"
    L.append(hdr % ("group", "n", "match", "unmatch", "crude", "timing", "text", "tim|txt"))
    for g, r in s["all_kinds"].items():
        L.append(hdr % (g, r["n"], r["matched"], r["unmatched"], r["crude_pct"], r["timing_robust_pct"],
                        r["text_level_pct"], r["timing_or_text_pct"]))
    if res["reference_only_available"]:
        a = s["reference_only"]["ALL"]
        L.append("reference_only: %d units (%d matched).  counted: crude %.2f%% timing %.2f%%  |  excluded: n=%d crude %.2f%% timing %.2f%%" % (
            a["reference_only_units"], a["reference_only_matched"], a["counted"]["crude_pct"], a["counted"]["timing_robust_pct"],
            a["excluded"]["n"], a["excluded"]["crude_pct"], a["excluded"]["timing_robust_pct"]))
    f = s["formatting"]
    L.append("formatting-noise units: %d of %d unmatched (%.1f%%)  kinds %s" % (
        f["formatting_noise"], f["unmatched_total"], f["share_of_unmatched_pct"], f["by_kind"]))
    L.append("unmatched categories: %s" % s["unmatched_categories"]["counts"])
    L.append("bounds: %s" % s["bounds"])
    if res.get("kept_crosscheck"):
        L.append("cross-check vs kept gitrx_only.jsonl: %s" % res["kept_crosscheck"])
    return "\n".join(L)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--grading-dir", required=True, help="an open-split grading output dir (summary.json, gitrx_only.jsonl)")
    ap.add_argument("--out", required=True, help="new directory for precision.json and records.jsonl (must not exist)")
    ap.add_argument("--split", default=DEFAULT_SPLIT)
    ap.add_argument("--gradebook", default=DEFAULT_GRADEBOOK)
    ap.add_argument("--index", default=DEFAULT_INDEX)
    ap.add_argument("--cache", action="append", default=None,
                    help="read-only pair-digest cache dir(s) to try first (default: the harness cache, if it still exists)")
    ap.add_argument("--workers", type=int, default=3)
    a = ap.parse_args(argv)
    if os.path.exists(a.out):
        raise SystemExit("refusing: --out %s already exists" % a.out)
    os.nice(10)
    os.makedirs(a.out)
    res, recs = run(a.grading_dir, split_path=a.split, gradebook=a.gradebook, index=a.index,
                    cache_dirs=a.cache or [DEFAULT_CACHE], build_dir=os.path.join(a.out, "cache", "pairs"),
                    workers=a.workers, progress=lambda n, t: print("pairs %d/%d" % (n, t), file=sys.stderr, flush=True),
                    log=lambda m: print(m, file=sys.stderr, flush=True))
    with open(os.path.join(a.out, "precision.json"), "w") as f:
        json.dump(res, f, indent=1, sort_keys=True)
    with open(os.path.join(a.out, "records.jsonl"), "w") as f:
        for r in recs:
            f.write(json.dumps(r, sort_keys=True) + "\n")
    txt = format_report(res)
    with open(os.path.join(a.out, "report.txt"), "w") as f:
        f.write(txt + "\n")
    print(txt)
    return res


if __name__ == "__main__":
    main()
