#!/usr/bin/env python3
"""Researcher verification kit for GitRx (read-only; see VERIFY.md).

    tools/verify_gitrx.py quick [--out DIR] [--seed N] [--records N] [--skip-big-hashes]
    tools/verify_gitrx.py full  --out DIR   [--workers N] [--rebuild-pilot]

quick (minutes): sha256 of the kept evidence/gradebook/split/index/rebuild artifacts and all rebuild shards,
sealed-split fingerprints (including the seeded re-draw), frozen code hashes against the Freeze 2 declaration (spl_parser against Freeze 2.1),
N seeded change records re-derived from raw ZIPs, one SrLC fragment traced end to end, and the summary metrics and
precision recomputed from the kept grading jsonl.

full (hours): re-runs the open-split grading in both group modes with the frozen harness into a NEW --out
directory, diffs it against the kept summaries, recomputes precision from freshly rebuilt pair digests, and
optionally re-runs a rebuild pilot twice for determinism.

Guarantees: reads only; the only place written is --out (a directory that must not exist or be empty); the
sealed split is never graded (the harness is only ever called with --split open and never with --sealed-final);
exit status is 0 only if no check failed.  Skipped checks are listed, never counted as passes.
"""
import argparse
import collections
import hashlib
import html
import json
import os
import random
import re
import sqlite3
import subprocess
import sys
import time

sys.dont_write_bytecode = True  # never write .pyc files into the repository
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from gitrx_core import dataroot  # noqa: E402  (GITRX_DATA_ROOT or runtime/local.json)
SSD = dataroot.DATA_ROOT
SEED = 20261111
PLAN_DOC = os.path.join(REPO, "docs", "AUTHORITATIVE-PARSER-PLAN.md")

EXPECTED_FILES = {   # name -> (path under SSD, full sha256 from docs/AUTHORITATIVE-PARSER-PLAN.md and docs/DELETION-PROPOSAL-2026-11-11.md)
    "evidence-v1.sqlite": ("evidence/evidence-v1.sqlite", "823a5bc6c384a5f4529ec76c738ba9120b4010a7cf212462662a641ff72ac3c6"),
    "gradebook.sqlite": ("srlc/gradebook.sqlite", "863aea6712ed024fb49456d4a155337bb503d12cad4bf347c3ed46e04b896db4"),
    "split.json": ("srlc/split.json", "45725c78830a4a5a657bd92bf3f01d6d85580a3f8f4abd23d56ae82b664750a9"),
    "index-allversions.sqlite": ("srlc/grading/cache/index-allversions.sqlite",
                                 "a5739f4d8a9e484901d201311f83a64f3a6eafc734ebfb4eddb5ae54ab3243e7"),
}
# Rebuild releases that can be verified (default: the newest).  `parser` is the parser version the build was made with.
BUILDS = {
    "0.2.4": {"dir": "rebuild/spl-sections-0.2.4__srlc-changes-0.3.2", "parser": "spl-sections-0.2.4",
              "complete_sha256": "8dc88e7a55831af605dbedd2a2b3bccdbd6575d51eaacb62c75c399975ce644b"},
    "0.2.3-delta": {"dir": "rebuild/spl-sections-0.2.3__srlc-changes-0.3.2__delta-20261111", "parser": "spl-sections-0.2.3",
                    "complete_sha256": "69db2791bad3ca2dca4d211bc738603700fe3b5d7dda49da71f85988e78c4c2a"},
    "0.2.3": {"dir": "rebuild/spl-sections-0.2.3__srlc-changes-0.3.2", "parser": "spl-sections-0.2.3",
              "complete_sha256": "bbe9223a91eb3a663501d00dea854a5f81a2192c930f015597a5b3b2b9a6869c"},
}
DEFAULT_BUILD = "0.2.4"
EXPECTED_SPLIT = {   # fingerprints declared in the plan before any grading
    "population_sha256": "b065c433be0b393db3b734bdbfe61ea5b319d86cf0eee46f1681b85f9c2f03f0",
    "sealed_sha256": "354025929110b2c565129d565b2ddcdc6ab7e5bc7a9d889f0ba4a241e897d894",
    "open_sha256": "f494923ef173088e164810c46a312cd964a4e3ab78d12a63be4d7cb4646ed333",
    "population_n": 3278, "sealed_n": 983, "open_n": 2295, "seed": SEED,
}
# kept grading dirs under SSD/srlc/grading: name -> expected summary.json sha256 (only declared for sealed runs)
GRADING_DIRS = {
    "open-spl-sections-0.2.2-srlc-changes-0.2.2-h0.3.2-either": None,
    "open-spl-sections-0.2.2-srlc-changes-0.2.2-h0.3.2-primary": None,
    "open-spl-sections-0.2.3-srlc-changes-0.3.2-h0.3.2-either": None,
    "open-spl-sections-0.2.3-srlc-changes-0.3.2-h0.3.2-primary": None,
    "SEALED-spl-sections-0.2.2-h0.3.2-either": "e80b56405eae6112c1422fbd6cf804e51873eb441384ee0611a87d174af33bdb",
    "SEALED-spl-sections-0.2.2-h0.3.2-primary": "e4619a0a6e667132fd34e0150ddb78a623736b1a8c0bb67227fdca8e9588d08d",
    "SEALED-spl-sections-0.2.3-srlc-changes-0.3.2-h0.3.2-either": "423e668bbe38367cf9faa78bdd2867994fcaaa4385023d202dea2a527333c235",
    "SEALED-spl-sections-0.2.3-srlc-changes-0.3.2-h0.3.2-primary": "8162fa9914f312fcd73414e3e65c924bdbed19d0631cce6fb33f29f50ff5c825",
}
OPEN_F2 = "open-spl-sections-0.2.3-srlc-changes-0.3.2-h0.3.2-%s"
# published precision (docs/VALIDATION-REASONING.md section d), open split
EXPECTED_PRECISION = {
    "open-spl-sections-0.2.3-srlc-changes-0.3.2-h0.3.2-either": {"crude_counted": 79.75, "crude_excluded": 80.54,
                                                                  "timing_counted": 86.56, "timing_excluded": 87.19},
    "open-spl-sections-0.2.2-srlc-changes-0.2.2-h0.3.2-either": {"crude_counted": 77.62},
}
FROZEN_MODULES = ("spl_parser.py", "normalize.py", "section_groups.py", "changes.py", "srlc_grade.py", "srlc.py",
                  "srlc_split.py")


# ----------------------------------------------------------------------------- reporting
class Report:
    def __init__(self, echo=True):
        self.checks, self.echo = [], echo

    def add(self, name, status, detail=""):
        assert status in ("pass", "fail", "skipped", "info")
        self.checks.append({"name": name, "status": status, "detail": detail})
        if self.echo:
            print("[%-7s] %s%s" % (status.upper(), name, (" -- " + detail) if detail else ""), flush=True)

    def ok(self, name, cond, detail=""):
        self.add(name, "pass" if cond else "fail", detail)
        return bool(cond)

    @property
    def failed(self):
        return [c for c in self.checks if c["status"] == "fail"]

    def summary(self):
        c = collections.Counter(x["status"] for x in self.checks)
        return {"pass": c["pass"], "fail": c["fail"], "skipped": c["skipped"], "info": c["info"]}


def sha256_file(path, chunk=1 << 23):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def ro(path):
    """Read-only, immutable SQLite connection (creates no -wal/-shm/-journal files)."""
    return sqlite3.connect("file:%s?mode=ro&immutable=1" % path, uri=True)


def spl_parser_version():
    from gitrx_core import spl_parser
    return spl_parser.PARSER_VERSION


def check_ssd(ssd):
    return os.path.isdir(ssd)


def prepare_out(out):
    """--out must not exist or be an empty directory; it is the only place anything is written."""
    if out is None:
        return None
    if os.path.exists(out) and (not os.path.isdir(out) or os.listdir(out)):
        raise SystemExit("refusing: --out %s exists and is not an empty directory" % out)
    os.makedirs(out, exist_ok=True)
    return out


# ----------------------------------------------------------------------------- 1. artifact hashes
def check_file_hashes(rep, ssd, files=None, skip_big=False):
    for name, (rel, want) in (files or EXPECTED_FILES).items():
        p = os.path.join(ssd, rel)
        if not os.path.isfile(p):
            rep.add("sha256 " + name, "fail", "missing: " + p)
            continue
        if skip_big and os.path.getsize(p) > (1 << 30):
            rep.add("sha256 " + name, "skipped", "--skip-big-hashes")
            continue
        t = time.time()
        got = sha256_file(p)
        rep.ok("sha256 " + name, got == want, "%s%s (%.0fs)" % (got[:12], "" if got == want else " != " + want[:12], time.time() - t))


# ----------------------------------------------------------------------------- 2. split fingerprints
def fingerprint(ids):
    return hashlib.sha256("\n".join(str(i) for i in sorted(ids)).encode()).hexdigest()


def check_split(rep, split_path, expected=None, redraw_root=None):
    expected = expected or EXPECTED_SPLIT
    sp = json.load(open(split_path))
    sealed, opn = sp["sealed"], sp["open"]
    pop = sorted(set(sealed) | set(opn))
    got = {"population_sha256": fingerprint(pop), "sealed_sha256": fingerprint(sealed), "open_sha256": fingerprint(opn),
           "population_n": len(pop), "sealed_n": len(sealed), "open_n": len(opn), "seed": sp["seed"]}
    for k, v in expected.items():
        rep.ok("split " + k, got[k] == v, "recomputed %s" % (str(got[k])[:16]))
    rep.ok("split lists disjoint", not (set(sealed) & set(opn)))
    rep.ok("split.json internal fingerprints equal recomputed",
           all(sp[k] == got[k] for k in ("population_sha256", "sealed_sha256", "open_sha256")))
    if redraw_root:
        try:
            from gitrx_core import srlc_split
            ids = srlc_split.population(redraw_root)
            s2, o2 = srlc_split.draw(ids, seed=sp["seed"], fraction=sp["fraction"])
            rep.ok("split re-draw from SrLC enumeration pages (seed %d)" % sp["seed"],
                   s2 == sorted(sealed) and o2 == sorted(opn) and fingerprint(ids) == expected["population_sha256"],
                   "population %d, sealed %d" % (len(ids), len(s2)))
        except Exception as exc:  # noqa: BLE001
            rep.add("split re-draw", "fail", "%s: %s" % (type(exc).__name__, exc))
    return sp


# ----------------------------------------------------------------------------- 3. frozen code
def parse_freeze(plan_text, heading_prefix):
    """-> ({module: sha256}, {version labels}) from the section of the plan starting at a '## <heading_prefix>' line."""
    m = re.search(r"^##+ %s.*?$(.*?)(?=^##+ |\Z)" % re.escape(heading_prefix), plan_text, re.S | re.M)
    if not m:
        return {}
    return {a: b for a, b in re.findall(r"^- (\w+\.py) ([0-9a-f]{64})\s*$", m.group(1), re.M)}


def check_frozen_code(rep, repo=REPO, plan_path=PLAN_DOC, heading="Freeze 2 — sealed rerun",
                      amend_heading="Freeze 2.1 — parser spl-sections-0.2.4"):
    """Freeze 2 declares every module; Freeze 2.1 (if the plan has it) replaces only the hashes it lists (spl_parser)."""
    text = open(plan_path, encoding="utf-8").read()
    decl = parse_freeze(text, heading)
    rep.ok("Freeze 2 declaration parsed from plan", len(decl) == len(FROZEN_MODULES) and set(decl) == set(FROZEN_MODULES),
           "%d module hashes" % len(decl))
    amend = parse_freeze(text, amend_heading) if amend_heading else {}
    if amend:
        rep.ok("Freeze 2.1 declaration parsed from plan", set(amend) <= set(FROZEN_MODULES) and "spl_parser.py" in amend,
               "%d module hashes (%s)" % (len(amend), ", ".join(sorted(amend))))
    decl = {**decl, **amend}
    for name in FROZEN_MODULES:
        p = os.path.join(repo, "gitrx_core", name)
        got = sha256_file(p) if os.path.exists(p) else None
        rep.ok("frozen code %s" % name, got is not None and got == decl.get(name),
               "%s" % ((got or "missing")[:12]))
    return decl


def check_versions(rep):
    from gitrx_core import changes, section_groups, spl_parser
    from gitrx_core import srlc_grade
    got = (spl_parser.PARSER_VERSION, section_groups.GROUPS_VERSION, changes.CHANGES_VERSION, srlc_grade.HARNESS_VERSION)
    rep.ok("code versions", got == ("spl-sections-0.2.4", "srlc-groups-0.3.0", "srlc-changes-0.3.2", "srlc-grade-0.3.2"),
           " / ".join(got))


# ----------------------------------------------------------------------------- 4. rebuild shards
def check_shards(rep, rebuild_dir, skip=False):
    cpath = os.path.join(rebuild_dir, "COMPLETE.json")
    comp = json.load(open(cpath))
    shards = comp["config"]["shards"]
    rep.ok("rebuild COMPLETE.json lists 256 shards", len(shards) == 256, "%d" % len(shards))
    if skip:
        rep.add("rebuild shards vs sidecars", "skipped", "--skip-big-hashes")
        return comp
    bad, tot = [], collections.Counter()
    t = time.time()
    for sh in shards:
        fp = os.path.join(rebuild_dir, "shards", sh + ".sqlite")
        sc = os.path.join(rebuild_dir, "shards", sh + ".done.json")
        try:
            side = json.load(open(sc))
            size = os.path.getsize(fp)
            ok = size == side["bytes"] and sha256_file(fp) == side["sha256"]
        except (OSError, ValueError, KeyError):
            ok, side = False, {}
        if not ok:
            bad.append(sh)
        for k in ("setids", "bytes"):
            tot[k] += side.get(k, 0)
        for k in ("families", "pairs", "changes", "formatting_changes"):
            tot[k] += side.get("stats", {}).get(k, 0)
    rep.ok("rebuild shards vs sidecars (sha256, bytes)", not bad,
           "%d shards, bad: %s (%.0fs)" % (len(shards), bad or "none", time.time() - t))
    c = comp["counts"]
    rep.ok("rebuild sidecar totals equal COMPLETE.json counts",
           tot["setids"] == c["setids"] == comp["config"]["n_setids"] and tot["pairs"] == c["pairs"]
           and tot["changes"] == c["changes"] and tot["families"] == c["families"],
           "setids %d pairs %d changes %d" % (tot["setids"], tot["pairs"], tot["changes"]))
    from gitrx_core import rebuild as R
    recorded = comp["config"]["frozen_code_sha256"]
    mine = R.code_hashes()
    if comp["config"]["versions"]["parser"] == R.EXPECTED_VERSIONS["parser"]:
        rep.ok("COMPLETE.json frozen_code_sha256 equals this repo's frozen modules",
               all(recorded.get(n) == h for n, h in mine.items()))
    else:   # an older build made with the previous parser: every other frozen module must still be the same
        rep.ok("COMPLETE.json (older parser %s) frozen modules other than spl_parser.py equal this repo's" %
               comp["config"]["versions"]["parser"],
               all(recorded.get(n) == h for n, h in mine.items() if n != "spl_parser.py")
               and recorded.get("spl_parser.py") != mine["spl_parser.py"])
    return comp


# ----------------------------------------------------------------------------- 5. re-derive change records
def make_resolver(ssd, index_path):
    """sha256 -> candidate ZIP paths: flat network/bodies, the SSD copy of the former internal-disk objects
    (raw-internal-20261111/sha256/xx/sha), then the path recorded in the version index."""
    bodies = os.path.join(ssd, "backfill-20261107", "network", "bodies")
    internal = os.path.join(ssd, "raw-internal-20261111", "sha256")
    con = ro(index_path) if index_path and os.path.exists(index_path) else None

    def resolve(shas):
        out = {}
        for s in shas:
            c = [os.path.join(bodies, s), os.path.join(internal, s[:2], s)]
            if con is not None:
                r = con.execute("SELECT path FROM paths WHERE sha256=?", (s,)).fetchone()
                if r and r[0] not in c:
                    c.append(r[0])
            out[s] = c
        return out
    return resolve


def sample_pairs(rebuild_dir, n, seed):
    import glob
    pairs = []
    for fp in sorted(glob.glob(os.path.join(rebuild_dir, "shards", "*.sqlite"))):
        con = ro(fp)
        pairs += list(con.execute("SELECT setid, from_version, to_version FROM pairs WHERE record_sha256 IS NOT NULL"))
        con.close()
    pairs.sort()
    return pairs, random.Random(seed).sample(pairs, min(n, len(pairs)))


def rederive_records(rep, rebuild_dir, resolve, n, seed, sample=None):
    from gitrx_core import changes as C
    from gitrx_core import rebuild as R
    from gitrx_core import rebuild_validate as V
    from gitrx_core import spl_parser as P
    if sample is None:
        allp, sample = sample_pairs(rebuild_dir, n, seed)
        rep.add("record sample", "info", "%d pairs with records in rebuild; seed %d; sampled %d" % (len(allp), seed, len(sample)))
    good = 0
    for s, a, b in sample:
        con = V.shard_con(rebuild_dir, s)
        rows = {v: (sha, mem) for v, sha, mem in con.execute(
            "SELECT version, raw_sha256, xml_member FROM versions WHERE setid=? AND version IN (?,?)", (s, a, b))}
        stored = con.execute("SELECT record_sha256, n_changes FROM pairs WHERE setid=? AND from_version=? AND to_version=?",
                             (s, a, b)).fetchone()
        docs, err = [], None
        for v in (a, b):
            sha, mem = rows[v]
            xml, bad, e, _w = R.load_raw(sha, mem, resolve([sha])[sha])
            if bad:
                err = "%s %s" % (bad, e)
                break
            docs.append(P.parse(xml))
        if err:
            rep.add("re-derive %s v%d->v%d" % (s[:8], a, b), "fail", err)
            continue
        rec = C.compare_docs(docs[0], docs[1], rows[a][0], rows[b][0])
        full = R.full_record(con, s, a, b)
        ok = rec["record_sha256"] == stored[0] and C.record_hash(full) == stored[0]
        good += ok
        rep.ok("re-derive %s v%d->v%d" % (s[:8], a, b), ok,
               "%d changes, record_sha256 %s" % (len(rec["changes"]), rec["record_sha256"][:16]))
        con.close()
    return good, len(sample)


# ----------------------------------------------------------------------------- 6. trace one SrLC fragment
_TAG = re.compile(r"<[^>]+>")


def page_squash(raw_html):
    """Tags removed, entities unescaped, all whitespace dropped, casefolded (page-vs-fragment comparison)."""
    t = html.unescape(_TAG.sub(" ", raw_html.decode("utf-8", "replace")))
    return "".join(t.split()).casefold()


def pick_fragment(grading_dir, seed):
    """Seeded pick among open-split main fragments located by the parser and found (a full end-to-end chain)."""
    cands = []
    with open(os.path.join(grading_dir, "fragments.jsonl")) as f:
        for line in f:
            d = json.loads(line)
            p = d.get("parser") or {}
            if d["class"] == "main" and p.get("located") and p.get("found_render"):
                cands.append((d["supplement_id"], d["change_id"], d["fragment"], d["drug_name_id"], p))
    cands.sort(key=lambda x: (x[1], x[2]))
    return random.Random(seed).choice(cands), len(cands)


def trace_fragment(rep, ssd, grading_dir, seed, rebuild_dir=None):
    from gitrx_core import srlc, srlc_grade as G
    from gitrx_core import rebuild as R
    (sup_id, change_id, frag, drug_id, par), ncand = pick_fragment(grading_dir, seed)
    rep.add("trace: candidate fragments", "info", "%d open-split located+found; picked change_id %d (seed %d)" % (ncand, change_id, seed))
    root = os.path.join(ssd, "srlc")
    gb = ro(os.path.join(root, "gradebook.sqlite"))
    drug = gb.execute("SELECT drug_name, appl_type, appl_no, detail_sha256 FROM drugs WHERE drug_name_id=?", (drug_id,)).fetchone()
    sup = gb.execute("SELECT suppl_ref, approval_date_iso, source_sha256, database_updated FROM supplements WHERE supplement_id=?",
                     (sup_id,)).fetchone()
    ch = gb.execute("SELECT section_group, section_number, section_title, source_sha256, underlined_spans_json "
                    "FROM changes WHERE id=?", (change_id,)).fetchone()
    print("  SrLC entry: %s (%s%s) %s approved %s, group %s section %r %r" % (
        drug[0], drug[1], drug[2], sup[0], sup[1], ch[0], ch[1], ch[2]))
    print("  fragment: %r" % frag[:200])
    # (1) raw SrLC page
    sha = ch[3]
    page_path = srlc.raw_path(root, sha)
    page = srlc.read_raw(root, sha)
    rep.ok("trace 1: SrLC raw page sha256 equals its name", hashlib.sha256(page).hexdigest() == sha, "%s %s" % (sha[:16], page_path))
    rep.ok("trace 1: gradebook drug.detail_sha256 == change.source_sha256 == supplement.source_sha256",
           drug[3] == sha == sup[2])
    led = ro(os.path.join(root, "ledger.sqlite")).execute(
        "SELECT url, requested_at, status FROM requests WHERE body_sha256=? AND ok=1", (sha,)).fetchone()
    print("  fetched: %s at %s (HTTP %s)" % (led[0] if led else "?", led[1] if led else "?", led[2] if led else "?"))
    pieces = [p for s in json.loads(ch[4] or "[]") for p in re.split("…", s or "")]
    psq = page_squash(page)
    in_page = [p for p in pieces if p.strip() and "".join(p.split()).casefold() in psq]
    frag_ok = any("".join(G.normalize(frag).split()).casefold() in "".join(G.normalize(p).split()).casefold() for p in in_page)
    rep.ok("trace 1: an underlined span of the gradebook row occurs in the raw page and contains the fragment", frag_ok,
           "%d of %d spans found in page text" % (len(in_page), len(pieces)))
    # (2) candidate pair and raw label ZIPs
    ix = ro(os.path.join(root, "grading", "cache", "index-allversions.sqlite"))
    setid, va, vb = par["setid"], par["from_version"], par["to_version"]
    vrows = {v: (sha_, mem, pub) for v, sha_, mem, pub in ix.execute(
        "SELECT version, sha256, xml_member, pub_date FROM versions WHERE setid=? AND version IN (?,?)", (setid, va, vb))}
    appl = (drug[1] or "") + (drug[2] or "")
    has_appl = ix.execute("SELECT 1 FROM approvals WHERE setid=? AND appl=?", (setid, appl.upper())).fetchone() is not None
    rep.ok("trace 2: candidate setid carries the SrLC application number %s" % appl, has_appl, setid)
    print("  GitRx pair: setid %s v%d (%s) -> v%d (%s)" % (setid, va, vrows[va][2], vb, vrows[vb][2]))
    resolve = make_resolver(ssd, os.path.join(root, "grading", "cache", "index-allversions.sqlite"))
    xmls = []
    for v in (va, vb):
        sha_, mem, _pub = vrows[v]
        xml, bad, e, _w = R.load_raw(sha_, mem, resolve([sha_])[sha_])
        rep.ok("trace 3: raw ZIP v%d re-hashed" % v, not bad, "sha256 %s" % sha_[:16] + (" " + str(e) if bad else ""))
        xmls.append(xml)
    if None in xmls:
        return
    # (3) reparse with the frozen code and find the fragment
    d = G.make_digest(xmls[0], xmls[1], vrows[va][0], vrows[vb][0])
    view = G.DigestView(d)
    st = view.fragment_status(G.normalize(frag), ch[0])
    rep.ok("trace 4: frozen parser + compare_docs re-derivation: fragment inside a changed same-group section's added tokens",
           bool(st["found_render"]), "pair record_sha256 %s" % d["record_sha256"][:16])
    for c in d["changes"]:
        if G._accepts(ch[0], c["group"], c.get("alt")):
            print("  GitRx change: %s %s %r %r (alt %s)" % (c["kind"], c["group"], c["section_number"], c["section_title"], c.get("alt")))
    if rebuild_dir and os.path.isdir(rebuild_dir):
        from gitrx_core import rebuild_validate as V
        row = V.shard_con(rebuild_dir, setid).execute(
            "SELECT record_sha256 FROM pairs WHERE setid=? AND from_version=? AND to_version=?", (setid, va, vb)).fetchone()
        rep.ok("trace 5: rebuild shard stores the same record_sha256", bool(row) and row[0] == d["record_sha256"],
               "shard %s" % (row[0][:16] if row else "no row"))


# ----------------------------------------------------------------------------- 7. metrics from kept jsonl
def _eq(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_eq(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_eq(x, y) for x, y in zip(a, b))
    if isinstance(a, float) or isinstance(b, float):
        return abs(a - b) < 1e-12
    return a == b


def recompute_metrics(grading_dir):
    """Recompute the headline metrics of a harness output from rows.jsonl / fragments.jsonl / gitrx_only.jsonl."""
    from gitrx_core import srlc_grade as G
    groups = G.SRLC_GROUPS
    rows = [json.loads(l) for l in open(os.path.join(grading_dir, "rows.jsonl"))]
    sec = [r for r in rows if r["status"] == "graded"]
    out = {"section_recall": G.rate(sum(r["section_found"] for r in sec), len(sec)),
           "section_recall_by_group": {g: G.rate(sum(1 for r in sec if r["group"] == g and r["section_found"]),
                                                  sum(1 for r in sec if r["group"] == g)) for g in groups}}
    main_e2e, located, pmain = [], [], []
    for l in open(os.path.join(grading_dir, "fragments.jsonl")):
        f = json.loads(l)
        if f["class"] == "main" and f["e2e"]:
            main_e2e.append(f)
        if f["class"] == "main" and "parser" in f:
            pmain.append(f)
            if f["parser"]["located"]:
                located.append(f)
    out["text_recall_main"] = {n: G.rate(sum(1 for f in main_e2e if f[k]), len(main_e2e))
                               for n, k in (("render", "found"), ("strict", "found_strict"), ("ws", "found_ws"))}
    prow = [r for r in rows if r.get("parser_located_fragments")]
    pa = {"main_fragments_in_parser_gradable_entries": len(pmain), "located": len(located),
          "not_locatable": len(pmain) - len(located),
          "not_locatable_reasons": dict(collections.Counter(f["parser"]["not_locatable_reason"] for f in pmain
                                                              if not f["parser"]["located"])),
          "text_same_group": {n: G.rate(sum(1 for f in located if f["parser"][p + n]), len(located))
                              for n in ("strict", "ws", "render") for p in ("found_",)},
          "text_any_group": {n: G.rate(sum(1 for f in located if f["parser"]["any_group_" + n]), len(located))
                             for n in ("strict", "ws", "render")},
          "text_same_group_by_group": {g: {n: G.rate(sum(1 for f in located if f["group"] == g and f["parser"]["found_" + n]),
                                                      sum(1 for f in located if f["group"] == g))
                                           for n in ("strict", "ws", "render")} for g in groups},
          "section_accuracy": G.rate(sum(1 for r in prow if r["parser_section_found"]), len(prow)),
          "section_accuracy_by_group": {g: G.rate(sum(1 for r in prow if r["group"] == g and r["parser_section_found"]),
                                                  sum(1 for r in prow if r["group"] == g)) for g in groups}}
    out["parser_accuracy"] = pa
    go = collections.Counter()
    for l in open(os.path.join(grading_dir, "gitrx_only.jsonl")):
        go[json.loads(l)["category"]] += 1
    out["gitrx_only_counts"] = dict(go)
    return out


def compare_metrics(rep, name, grading_dir):
    s = json.load(open(os.path.join(grading_dir, "summary.json")))
    m = recompute_metrics(grading_dir)
    ee = s["end_to_end"]
    pairs = [("section_recall", m["section_recall"], ee["section_recall"]),
             ("section_recall_by_group", m["section_recall_by_group"], ee["section_recall_by_group"]),
             ("text_recall_main (render/strict/ws)", m["text_recall_main"],
              {"render": ee["text_recall_main"]["render"], "strict": ee["text_recall_main"]["strict"],
               "ws": ee["text_recall_main"]["ws"]}),
             ("parser located / not_locatable",
              {k: m["parser_accuracy"][k] for k in ("main_fragments_in_parser_gradable_entries", "located", "not_locatable",
                                                    "not_locatable_reasons")},
              {k: s["parser_accuracy"][k] for k in ("main_fragments_in_parser_gradable_entries", "located", "not_locatable",
                                                    "not_locatable_reasons")}),
             ("parser text same-group", m["parser_accuracy"]["text_same_group"], s["parser_accuracy"]["text_same_group"]),
             ("parser text any-group", m["parser_accuracy"]["text_any_group"], s["parser_accuracy"]["text_any_group"]),
             ("parser text same-group by group", m["parser_accuracy"]["text_same_group_by_group"],
              s["parser_accuracy"]["text_same_group_by_group"]),
             ("parser section accuracy", m["parser_accuracy"]["section_accuracy"], s["parser_accuracy"]["section_accuracy"]),
             ("parser section accuracy by group", m["parser_accuracy"]["section_accuracy_by_group"],
              s["parser_accuracy"]["section_accuracy_by_group"]),
             ("gitrx_only counts", m["gitrx_only_counts"], {k: v for k, v in s["gitrx_only"].items() if k != "matched_by_srlc"})]
    allok = True
    for label, mine, kept in pairs:
        ok = _eq(mine, kept)
        allok &= ok
        if not ok:
            rep.add("%s: %s" % (name, label), "fail", "recomputed %s != summary %s" % (json.dumps(mine)[:150], json.dumps(kept)[:150]))
    pa = m["parser_accuracy"]
    rep.ok("%s: summary metrics recomputed from jsonl" % name, allok,
           "section recall %.2f%%, text(render) %.2f%%, parser text %.2f%% (%d/%d), parser section %.2f%% (%d/%d)" % (
               100 * m["section_recall"]["recall"], 100 * m["text_recall_main"]["render"]["recall"],
               100 * pa["text_same_group"]["render"]["recall"], pa["text_same_group"]["render"]["k"],
               pa["text_same_group"]["render"]["n"], 100 * pa["section_accuracy"]["recall"], pa["section_accuracy"]["k"],
               pa["section_accuracy"]["n"]))
    return m, s


def check_kept_precision(rep, name, grading_dir):
    from gitrx_core import srlc_precision as P
    k = P.kept_precision(grading_dir)
    want = EXPECTED_PRECISION.get(name, {}).get("crude_counted")
    detail = "matched %d, unmatched SrLC-group %d, non-SrLC-section %d -> crude precision %.2f%%" % (
        k["matched_by_srlc"], k["gitrx_only_srlc_group"], k["gitrx_only_non_srlc_section"], k["crude_precision_pct"])
    rep.ok("%s: precision from kept summary + gitrx_only.jsonl" % name,
           k["jsonl_consistent_with_summary"] and (want is None or k["crude_precision_pct"] == want),
           detail + ("" if want is None else " (published %.2f%%)" % want))
    return k


# ----------------------------------------------------------------------------- quick
def quick(args):
    ssd = args.ssd
    if not check_ssd(ssd):
        print("refusing: SSD %s is not present" % ssd, file=sys.stderr)
        return 2
    out = prepare_out(args.out)
    rep = Report()
    t0 = time.time()
    gdir = os.path.join(ssd, "srlc", "grading")
    build = BUILDS[args.build]
    rebuild_dir = os.path.join(ssd, build["dir"])
    print("== 1. frozen code and versions")
    check_frozen_code(rep)
    check_versions(rep)
    print("== 2. artifact hashes")
    check_file_hashes(rep, ssd, skip_big=args.skip_big_hashes)
    check_file_hashes(rep, ssd, {"rebuild COMPLETE.json (%s)" % args.build: (build["dir"] + "/COMPLETE.json",
                                                                              build["complete_sha256"])})
    print("== 3. sealed split fingerprints")
    check_split(rep, os.path.join(ssd, "srlc", "split.json"), redraw_root=os.path.join(ssd, "srlc"))
    print("== 4. rebuild shards")
    check_shards(rep, rebuild_dir, skip=args.skip_big_hashes)
    older = build["parser"] != spl_parser_version()
    if older:   # records embed the parser version; re-deriving needs that parser, which is no longer the checked-out code
        rep.add("re-derive records", "skipped", "build made with %s; this checkout has %s" % (build["parser"], spl_parser_version()))
        rep.add("trace 5", "skipped", "same reason")
    else:
        print("== 5. re-derive %d seeded change records from raw ZIPs" % args.records)
        resolve = make_resolver(ssd, os.path.join(gdir, "cache", "index-allversions.sqlite"))
        good, n = rederive_records(rep, rebuild_dir, resolve, args.records, args.seed)
        rep.ok("re-derived records identical", good == n, "%d of %d" % (good, n))
    print("== 6. trace one SrLC fragment end to end (open split)")
    try:
        trace_fragment(rep, ssd, os.path.join(gdir, OPEN_F2 % "either"), args.seed, None if older else rebuild_dir)
    except Exception as exc:  # noqa: BLE001
        rep.add("trace", "fail", "%s: %s" % (type(exc).__name__, exc))
    print("== 7. kept grading outputs: declared summary hashes, metrics and precision recomputed from jsonl")
    for name, want in GRADING_DIRS.items():
        d = os.path.join(gdir, name)
        if not os.path.isdir(d):
            rep.add(name, "fail", "missing grading dir")
            continue
        if want:
            rep.ok("%s: summary.json sha256 equals the value declared in the plan" % name,
                   sha256_file(os.path.join(d, "summary.json")) == want)
        compare_metrics(rep, name, d)
        if name.startswith("open-"):
            check_kept_precision(rep, name, d)
    s = rep.summary()
    print("\nSUMMARY %s  (%.0fs)" % (s, time.time() - t0))
    if rep.failed:
        print("FAILED: " + "; ".join(c["name"] for c in rep.failed))
    if out:
        json.dump({"mode": "quick", "summary": s, "checks": rep.checks}, open(os.path.join(out, "verify-quick.json"), "w"), indent=1)
    return 1 if rep.failed else 0


# ----------------------------------------------------------------------------- full
def grade_cmd(mode, out_dir, cache_dir, index_path, srlc_root, workers):
    """The harness command line.  Open split only: no --sealed-final, and --split is pinned to open."""
    return [sys.executable, "-B", "-m", "gitrx_core.srlc_grade", "grade", "--split", "open", "--group-mode", mode,
            "--workers", str(workers), "--out-dir", out_dir, "--cache-dir", cache_dir, "--index", index_path,
            "--srlc-root", srlc_root]


VOLATILE = ("generated_at", "runtime_seconds", "digest_build")


def diff_summaries(new, kept, path=""):
    """-> list of differing leaf paths between two summary.json dicts (volatile keys ignored)."""
    diffs = []
    if isinstance(new, dict) and isinstance(kept, dict):
        for k in sorted(set(new) | set(kept)):
            if k in VOLATILE and not path:
                continue
            if k not in new or k not in kept:
                diffs.append(path + "/" + k + " (missing on one side)")
            else:
                diffs += diff_summaries(new[k], kept[k], path + "/" + k)
    elif not _eq(new, kept):
        diffs.append(path)
    return diffs


def jsonl_digest(path):
    """Order-insensitive digest of a jsonl file (sha256 over sorted canonical lines)."""
    h = hashlib.sha256()
    lines = sorted(json.dumps(json.loads(l), sort_keys=True) for l in open(path))
    for l in lines:
        h.update(l.encode())
        h.update(b"\n")
    return h.hexdigest(), len(lines)


def full(args):
    ssd = args.ssd
    if not check_ssd(ssd):
        print("refusing: SSD %s is not present" % ssd, file=sys.stderr)
        return 2
    if not args.out:
        print("refusing: full mode needs --out DIR (a new directory)", file=sys.stderr)
        return 2
    out = prepare_out(args.out)
    rep = Report()
    t0 = time.time()
    gdir = os.path.join(ssd, "srlc", "grading")
    print("== preflight: frozen code")
    check_frozen_code(rep)
    check_versions(rep)
    if rep.failed:
        print("refusing to re-run grading with code that is not the Freeze 2 code")
        return 1
    index_path = os.path.join(gdir, "cache", "index-allversions.sqlite")
    cache = os.path.join(out, "cache", "pairs")
    from gitrx_core import srlc_precision as P
    for mode in ("either", "primary"):
        od = os.path.join(out, "open-" + mode)
        cmd = grade_cmd(mode, od, cache, index_path, os.path.join(ssd, "srlc"), args.workers)
        print("== re-running open-split grading (%s): %s" % (mode, " ".join(cmd)), flush=True)
        r = subprocess.run(["nice", "-n", "10"] + cmd, cwd=REPO)
        rep.ok("harness run (%s) exit status" % mode, r.returncode == 0, "exit %d" % r.returncode)
        kept = os.path.join(gdir, OPEN_F2 % mode)
        if r.returncode:
            continue
        new_s = json.load(open(os.path.join(od, "summary.json")))
        kept_s = json.load(open(os.path.join(kept, "summary.json")))
        d = diff_summaries(new_s, kept_s)
        rep.ok("summary.json (%s) equals the kept summary, volatile keys ignored" % mode, not d,
               "differences: %s" % (d[:10] or "none"))
        for fn in ("rows.jsonl", "fragments.jsonl", "gitrx_only.jsonl"):
            a, na = jsonl_digest(os.path.join(od, fn))
            b, nb = jsonl_digest(os.path.join(kept, fn))
            rep.ok("%s (%s) identical to kept (order-insensitive, %d lines)" % (fn, mode, na), a == b and na == nb)
        compare_metrics(rep, "rerun " + mode, od)
    print("== precision recomputed from the freshly rebuilt digests", flush=True)
    kept = os.path.join(gdir, OPEN_F2 % "either")
    res, recs = P.run(os.path.join(out, "open-either"), cache_dirs=[cache], build_dir=cache, workers=args.workers)
    s = res["summary"]["all_kinds"]["ALL"]
    ro_ = res["summary"]["reference_only"]["ALL"]
    want = EXPECTED_PRECISION[OPEN_F2 % "either"]
    got = {"crude_counted": s["crude_pct"], "timing_counted": s["timing_robust_pct"],
           "crude_excluded": ro_["excluded"]["crude_pct"], "timing_excluded": ro_["excluded"]["timing_robust_pct"]}
    rep.ok("precision equals published numbers", got == want, "recomputed %s published %s" % (got, want))
    cc = res.get("kept_crosscheck") or {}
    rep.ok("precision units equal kept gitrx_only.jsonl", bool(cc.get("unmatched_identical_to_kept_gitrx_only_jsonl")), str(cc))
    json.dump(res, open(os.path.join(out, "precision.json"), "w"), indent=1, sort_keys=True)
    if args.rebuild_pilot:
        pilot_check(rep, args, out)
    sm = rep.summary()
    print("\nSUMMARY %s  (%.0fs)" % (sm, time.time() - t0))
    if rep.failed:
        print("FAILED: " + "; ".join(c["name"] for c in rep.failed))
    json.dump({"mode": "full", "summary": sm, "checks": rep.checks}, open(os.path.join(out, "verify-full.json"), "w"), indent=1)
    return 1 if rep.failed else 0


def pilot_check(rep, args, out):
    """Build the same seeded 0.2% pilot of the rebuild twice into --out and compare shard hashes."""
    import glob
    from gitrx_core import rebuild as R
    ssd = args.ssd
    evidence = os.path.join(ssd, EXPECTED_FILES["evidence-v1.sqlite"][0])
    inv = R.load_inventory(evidence)
    resolve = make_resolver(ssd, os.path.join(ssd, "srlc", "grading", "cache", "index-allversions.sqlite"))
    hashes = []
    for i in (1, 2):
        od = os.path.join(out, "pilot-%d" % i)
        os.makedirs(od)
        R.run(od, inv, resolve, scratch_dir=os.path.join(out, "pilot-scratch-%d" % i), workers=args.workers,
              pilot=(0.002, SEED), require_dir=ssd, log=lambda m: None)
        hashes.append({os.path.basename(p): sha256_file(p) for p in sorted(glob.glob(os.path.join(od, "shards", "*.sqlite")))})
    rep.ok("rebuild pilot twice: byte-identical shards", hashes[0] == hashes[1] and bool(hashes[0]),
           "%d shards" % len(hashes[0]))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    for name in ("quick", "full"):
        sp = sub.add_parser(name)
        sp.add_argument("--ssd", default=SSD, help="evidence root (default %s)" % SSD)
        sp.add_argument("--out", default=None, help="the only directory this tool may write to (must be new/empty)")
        sp.add_argument("--seed", type=int, default=SEED)
        sp.add_argument("--workers", type=int, default=3)
        if name == "quick":
            sp.add_argument("--records", type=int, default=20, help="seeded change records to re-derive from raw")
            sp.add_argument("--build", choices=sorted(BUILDS), default=DEFAULT_BUILD,
                            help="rebuild release to verify (default %s)" % DEFAULT_BUILD)
            sp.add_argument("--skip-big-hashes", action="store_true", help="skip files > 1 GiB and shard hashing")
        else:
            sp.add_argument("--rebuild-pilot", action="store_true")
    a = ap.parse_args(argv)
    os.nice(10)
    return quick(a) if a.mode == "quick" else full(a)


if __name__ == "__main__":
    sys.exit(main())
