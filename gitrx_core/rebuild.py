"""Step E: full, deterministic rebuild of every captured DailyMed version from raw evidence.

Frozen code (Freeze 2, commit 34ff8d0): spl_parser (spl-sections-0.2.3), section_groups (srlc-groups-0.3.0),
changes (srlc-changes-0.3.2), normalize.  This module only orchestrates them; it never modifies them and refuses
to run when any of the four files differs from FROZEN_SHA256.

OUTPUT LAYOUT (<out>/):
  build-config.json        inputs/code identity this directory was started with (resume refuses a mismatch)
  shards/XX.sqlite         one SQLite file per setid prefix (first two hex chars of the setid, up to 256 shards)
  shards/XX.done.json      sidecar written atomically AFTER the shard file is final; a shard without it is not done
  runlog.jsonl             run timestamps / progress (the only place wall-clock time appears)
  COMPLETE.json            build manifest, written last and atomically

SHARD TABLES (all WITHOUT ROWID, rows inserted in fixed order: setid, version, ordinal/idx):
  meta      key/value constants (format, code hashes, shard id)
  versions  one row per inventory (setid, version): raw sha256, dates, parser status, diagnostics, errors
  sections  one row per parsed section: identity, SrLC group, alt group, basis/flags, 32-byte hashes of its channels
  texts     channel text stored once per distinct sha256 (zlib level 6 of the UTF-8 text); sections reference it
  pairs     one row per consecutive pair of PARSED captured versions of a family (record_z = slim change record)
  changes   one row per change of a pair, with the verbatim added/removed/moved spans (zlib canonical JSON)
  families  one row per setid
The full record of a pair = pairs.record_z with each change's added_spans/removed_spans/moved_spans restored from
`changes` (see full_record); record_hash of that equals pairs.record_sha256 (build-failing check at write time).

Determinism: a shard is built in a single pass in sorted order from pure functions of (inventory, raw bytes,
frozen code); no clock, no randomness and no paths inside the data tables.  Resume granularity is the shard.
"""
import argparse
import collections
import hashlib
import io
import json
import multiprocessing
import os
import platform
import random
import re
import shutil
import sqlite3
import sys
import time
import zipfile
import zlib

FORMAT = "gitrx-rebuild-1"
FROZEN_SHA256 = {
    "spl_parser.py": "2bffee02825edf82e97a6373a45e8fdab40c7d3f39f8ed95edcdeccde6468853",
    "section_groups.py": "f1a2684d4d1aead3d2c42ff8b89b90495513cb1877ae9dd0af2381774a538b80",
    "changes.py": "9b0bc06db5adaffd38d5a1099db50a4fd4ddfddca4d9fb3a1cf1fbac02e11c95",
    "normalize.py": "4a613040e6f1660d917f955ebea194b6084765a121e28e5caef3d45be886d12b",
}
EXPECTED_VERSIONS = {"parser": "spl-sections-0.2.3", "groups": "srlc-groups-0.3.0", "changes": "srlc-changes-0.3.2"}
OUT_NAME = "spl-sections-0.2.3__srlc-changes-0.3.2"
DEFAULT_OUT = "/Volumes/Untitled/GitRx/rebuild/" + OUT_NAME
DEFAULT_EVIDENCE = "/Volumes/Untitled/GitRx/evidence/evidence-v1.sqlite"
DEFAULT_MANIFEST = ("/private/tmp/claude-502/-Users-shubham-Desktop-GitRx/b81b1d09-9a13-41a3-9d0c-882a73ea8595/"
                    "scratchpad/phase1/raw-manifest.sqlite")
DEFAULT_BODIES = "/Volumes/Untitled/GitRx/backfill-20261107/network/bodies"
SSD_ROOT = "/Volumes/Untitled/GitRx"
ZLEVEL = 6
PAIR_SPAN_KEYS = ("added_spans", "removed_spans", "moved_spans")
PARSED_STATUSES = {"complete", "complete_with_source_gaps", "identification_incomplete", "no_label_body",
                   "extraction_defect"}
# statuses of versions with no parsed document (rebuild-level, never produced by the parser)
NO_DOC = {"no_raw", "raw_not_found", "raw_hash_mismatch", "raw_unreadable", "parse_exception"}

SCHEMA = """
CREATE TABLE meta(k TEXT PRIMARY KEY, v TEXT) WITHOUT ROWID;
CREATE TABLE families(setid TEXT PRIMARY KEY, n_inventory INTEGER, n_raw INTEGER, n_parsed INTEGER,
  min_version INTEGER, max_version INTEGER, first_parsed_version INTEGER, versions_before_first_parsed INTEGER,
  n_pairs INTEGER, n_nonconsecutive_pairs INTEGER) WITHOUT ROWID;
CREATE TABLE versions(setid TEXT, version INTEGER, inventory_states TEXT, raw_sha256 TEXT, xml_member TEXT,
  xml_sha256 TEXT, pub_date TEXT, effective_date TEXT, doc_effective_time TEXT, parsed_setid TEXT,
  parsed_version INTEGER, status TEXT, status_reasons TEXT, parser_output_sha256 TEXT, n_sections INTEGER,
  buckets TEXT, error TEXT, warnings TEXT, diagnostics_z BLOB, PRIMARY KEY(setid, version)) WITHOUT ROWID;
CREATE TABLE sections(setid TEXT, version INTEGER, ordinal INTEGER, parent_ordinal INTEGER, section_number TEXT,
  title TEXT, code TEXT, section_key TEXT, section_id_root TEXT, section_id_ext TEXT, kind TEXT, srlc_group TEXT,
  alt_srlc_group TEXT, topic_group TEXT, group_basis TEXT, group_flags TEXT, text_h BLOB, table_h BLOB,
  excerpt_h BLOB, PRIMARY KEY(setid, version, ordinal)) WITHOUT ROWID;
CREATE TABLE texts(h BLOB PRIMARY KEY, z BLOB) WITHOUT ROWID;
CREATE TABLE pairs(setid TEXT, from_version INTEGER, to_version INTEGER, consecutive INTEGER, gap_versions INTEGER,
  known_missing TEXT, comparison TEXT, comparison_reasons TEXT, from_raw_sha256 TEXT, to_raw_sha256 TEXT,
  from_pub_date TEXT, to_pub_date TEXT, from_effective_date TEXT, to_effective_date TEXT, n_changes INTEGER,
  n_formatting_changes INTEGER, record_sha256 TEXT, record_z BLOB, PRIMARY KEY(setid, from_version, to_version))
  WITHOUT ROWID;
CREATE TABLE changes(setid TEXT, from_version INTEGER, to_version INTEGER, idx INTEGER, kind TEXT, srlc_group TEXT,
  alt_srlc_group TEXT, topic_group TEXT, group_basis TEXT, section_number TEXT, section_title TEXT,
  section_key TEXT, ordinal_from INTEGER, ordinal_to INTEGER, alignment_method TEXT, alignment_confidence REAL,
  table_changed INTEGER, reference_only INTEGER, formatting_token_diffs INTEGER, n_added INTEGER, n_removed INTEGER,
  n_moved INTEGER, added_chars INTEGER, removed_chars INTEGER, added_spans_z BLOB, removed_spans_z BLOB,
  moved_spans_z BLOB, PRIMARY KEY(setid, from_version, to_version, idx)) WITHOUT ROWID;
"""
TABLE_COLS = {}
for _stmt in SCHEMA.split(";"):
    _stmt = _stmt.strip()
    if _stmt.startswith("CREATE TABLE"):
        _name = _stmt.split("(", 1)[0].split()[-1]
        _body = re.sub(r"PRIMARY KEY\([^)]*\)", "", _stmt.split("(", 1)[1])
        _cols = []
        for _part in _body.replace("\n", " ").split(","):
            _w = _part.strip().split()
            if _w and _w[0].strip("()") and _w[0] not in ("PRIMARY", "WITHOUT"):
                _cols.append(_w[0].strip("()"))
        TABLE_COLS[_name] = _cols


class FrozenCodeError(RuntimeError):
    pass


class SsdGone(RuntimeError):
    pass


def sha256_file(path, chunk=1 << 22):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def code_hashes():
    here = os.path.dirname(os.path.abspath(__file__))
    return {n: sha256_file(os.path.join(here, n)) for n in sorted(FROZEN_SHA256)}


def check_frozen(expected=None):
    """Raise FrozenCodeError unless every frozen module file has exactly the recorded sha256."""
    expected = FROZEN_SHA256 if expected is None else expected
    actual = code_hashes()
    bad = {n: (expected[n], actual.get(n)) for n in expected if actual.get(n) != expected[n]}
    if bad:
        raise FrozenCodeError("frozen code differs from Freeze 2: " + json.dumps(bad, sort_keys=True))
    from . import changes, section_groups, spl_parser
    got = {"parser": spl_parser.PARSER_VERSION, "groups": section_groups.GROUPS_VERSION,
           "changes": changes.CHANGES_VERSION}
    if got != EXPECTED_VERSIONS:
        raise FrozenCodeError("frozen version strings differ: %r" % (got,))
    return actual


class ShardAnomaly(RuntimeError):
    """Raw reads failed in a way that looks like an unavailable store rather than genuinely missing evidence."""


def check_ssd(require_dir):
    if require_dir:
        try:
            if not os.path.isdir(require_dir):
                raise SsdGone("required directory vanished: %s" % require_dir)
            os.listdir(require_dir)
        except OSError as exc:
            raise SsdGone("required directory unreadable: %s (%s)" % (require_dir, exc))


def check_shard_reads(shard, stats, max_raw_failures):
    """A shard whose raw reads failed with I/O errors, or with more than max_raw_failures missing/unreadable raw
    ZIPs, is not finalized (the store may have been unavailable); it is rebuilt on resume."""
    bad = stats["status:raw_not_found"] + stats["status:raw_unreadable"]
    if stats["io_errors"] or bad > max_raw_failures:
        raise ShardAnomaly("shard %s: %d raw read I/O errors, %d raw_not_found/raw_unreadable of %d versions with raw "
                           "(tolerance %d); not finalized" % (
                               shard, stats["io_errors"], bad, sum(
                                   v for k, v in stats.items() if k.startswith("status:") and k != "status:no_raw"),
                               max_raw_failures))


def jdump(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def z(data):
    if isinstance(data, str):
        data = data.encode("utf-8", "surrogatepass")
    return zlib.compress(data, ZLEVEL)


def unz(blob):
    return zlib.decompress(blob).decode("utf-8", "surrogatepass")


def shard_of(setid):
    p = (setid or "")[:2].lower()
    return p if len(p) == 2 and all(c in "0123456789abcdef" for c in p) else "__"


# ------------------------------------------------------------------------------------------------- raw access
def read_xml_bytes(zdata, member):
    """Same member rule as srlc_grade.read_xml_from_zip, on in-memory ZIP bytes."""
    with zipfile.ZipFile(io.BytesIO(zdata)) as zf:
        names = zf.namelist()
        if member and member in names:
            return zf.read(member)
        xmls = [n for n in names if n.lower().endswith(".xml")]
        if len(xmls) == 1:
            return zf.read(xmls[0])
        if member is None and xmls:
            return zf.read(sorted(xmls)[0])
        raise KeyError("xml member %r not in zip (%d xml files)" % (member, len(xmls)))


def load_raw(sha, member, candidates):
    """Re-hash the ZIP when read.  -> (xml_bytes|None, status|None, error|None, warnings[]).
    A copy whose hash differs is a warning if another copy verifies, otherwise a raw_hash_mismatch error."""
    warnings, mismatches, existing = [], [], 0
    for p in candidates:
        try:
            with open(p, "rb") as f:
                data = f.read()
        except FileNotFoundError:
            continue
        except OSError as exc:
            warnings.append("copy_unreadable:%s:%s" % (p, type(exc).__name__))
            existing += 1
            continue
        existing += 1
        got = hashlib.sha256(data).hexdigest()
        if got != sha:
            mismatches.append("%s has sha256 %s" % (p, got))
            continue
        try:
            xml = read_xml_bytes(data, member)
        except Exception as exc:  # noqa: BLE001 - recorded, not hidden
            return None, "raw_unreadable", "%s: %s" % (type(exc).__name__, exc), warnings + \
                ["copy_hash_mismatch:" + m for m in mismatches]
        return xml, None, None, warnings + ["copy_hash_mismatch:" + m for m in mismatches]
    if mismatches:
        return None, "raw_hash_mismatch", "; ".join(mismatches), warnings
    if existing:
        return None, "raw_unreadable", "no readable copy", warnings
    return None, "raw_not_found", "no copy found for %s" % sha, warnings


# ------------------------------------------------------------------------------------------------- family worker
def _init_worker(expected):
    check_frozen(expected)


def _text_h(text, texts):
    if not text:
        return None
    raw = text.encode("utf-8", "surrogatepass")
    h = hashlib.sha256(raw).digest()
    if h not in texts:
        texts[h] = zlib.compress(raw, ZLEVEL)
    return h


def process_family(task):
    """task = (setid, [version records]) -> dict of rows for the writer.  Pure given the raw bytes."""
    from . import changes as C
    from . import section_groups as SG
    from . import spl_parser as P
    t0 = time.time()
    setid, vers = task
    vers = sorted(vers, key=lambda r: r["version"])
    texts = {}
    out = {"setid": setid, "versions": [], "sections": [], "pairs": [], "changes": [], "stats": collections.Counter()}
    st = out["stats"]
    parsed = []  # (inventory record, doc)
    for r in vers:
        v = r["version"]
        sha, member = r.get("sha"), r.get("member")
        row = dict.fromkeys(TABLE_COLS["versions"])
        row.update(setid=setid, version=v, inventory_states=r.get("states") or "", raw_sha256=sha, xml_member=member,
                   pub_date=r.get("pub"), effective_date=r.get("eff"))
        doc, warnings = None, []
        if not sha:
            row["status"] = "no_raw"
            row["error"] = "no raw artifact recorded in the evidence inventory (states: %s)" % (r.get("states") or "")
        else:
            xml, bad, err, warnings = load_raw(sha, member, r.get("candidates") or [])
            if bad:
                row["status"], row["error"] = bad, err
            else:
                row["xml_sha256"] = hashlib.sha256(xml).hexdigest()
                try:
                    doc = P.parse(xml)
                except Exception as exc:  # noqa: BLE001 - recorded, not hidden
                    row["status"], row["error"] = "parse_exception", "%s: %s" % (type(exc).__name__, exc)
        if doc is not None:
            row["status"] = doc["status"]
            row["status_reasons"] = jdump(doc["status_reasons"])
            row["doc_effective_time"] = doc.get("effective_time")
            row["parsed_setid"], row["parsed_version"] = doc.get("setid"), doc.get("version")
            row["parser_output_sha256"] = doc["content_sha256"]
            row["n_sections"] = len(doc["sections"])
            row["buckets"] = jdump(doc["buckets"])
            if doc["diagnostics"]:
                row["diagnostics_z"] = z(jdump(doc["diagnostics"]))
            if doc.get("setid") != setid:
                warnings.append("identity_mismatch:setid:%s" % doc.get("setid"))
            if doc.get("version") != v:
                warnings.append("identity_mismatch:version:%s" % doc.get("version_raw"))
            try:
                groups = SG.classify_doc(doc)
                for s in doc["sections"]:
                    g = groups[s["ordinal"]]
                    ch = C.channels(s)
                    sid = s.get("section_id") or {}
                    out["sections"].append(tuple([
                        setid, v, s["ordinal"], s.get("parent_ordinal"), s.get("section_number"),
                        s.get("title_normalized"), s.get("code"), s.get("section_key"), sid.get("root"),
                        sid.get("extension"), s.get("kind"), g["group"], g["alt_group"], g["topic_group"], g["basis"],
                        jdump(g["flags"]), _text_h(ch["text"], texts), _text_h(ch["table"], texts),
                        _text_h(ch["excerpt"], texts)]))
            except Exception as exc:  # noqa: BLE001
                row["status"], row["error"] = "parse_exception", "classify: %s: %s" % (type(exc).__name__, exc)
                doc = None
                out["sections"] = [x for x in out["sections"] if not (x[0] == setid and x[1] == v)]
        if warnings:
            row["warnings"] = jdump(sorted(set(warnings)))
        st["status:" + row["status"]] += 1
        if row["error"] and row["status"] != "no_raw":
            st["errors"] += 1  # no_raw is an inventory fact (version listed without a raw artifact), counted by status
        if warnings:
            st["warnings"] += 1
            st["io_errors"] += sum(1 for w in warnings if w.startswith("copy_unreadable:"))
        out["versions"].append(tuple(row[c] for c in TABLE_COLS["versions"]))
        if doc is not None:
            parsed.append((r, doc))
    # consecutive pairs of parsed versions; versions in between that were not parsed are listed, never invented
    status_of = {o[1]: o[11] for o in out["versions"]}
    n_pairs = n_nonc = 0
    for (ra, da), (rb, db) in zip(parsed, parsed[1:]):
        va, vb = ra["version"], rb["version"]
        missing = [[v, status_of[v]] for v in sorted(status_of) if va < v < vb]
        gap = vb - va - 1
        rec, reasons, rsha, rz = None, [], None, None
        try:
            rec = C.compare_docs(da, db, ra.get("sha"), rb.get("sha"))
            comparison, reasons = rec["comparison"], rec["comparison_reasons"]
            rsha = rec["record_sha256"]
        except Exception as exc:  # noqa: BLE001 - recorded, not hidden
            comparison, reasons = "compare_exception", ["%s: %s" % (type(exc).__name__, exc)]
            st["errors"] += 1
        nch = nf = 0
        if rec is not None:
            nch, nf = len(rec["changes"]), len(rec["formatting_changes"])
            slim_changes = []
            for i, c in enumerate(rec["changes"]):
                sp = {k: c[k] for k in PAIR_SPAN_KEYS}
                slim_changes.append({k: v for k, v in c.items() if k not in PAIR_SPAN_KEYS})
                ad, rm, mv = sp["added_spans"], sp["removed_spans"], sp["moved_spans"]
                out["changes"].append((
                    setid, va, vb, i, c["kind"], c["srlc_group"], c.get("alt_srlc_group"), c.get("topic_group"),
                    c.get("group_basis"), c.get("section_number"), c.get("section_title"), c.get("section_key"),
                    c.get("ordinal_from"), c.get("ordinal_to"), c.get("alignment_method"),
                    c.get("alignment_confidence"), int(bool(c.get("table_changed"))),
                    int(bool(c.get("reference_only"))), c.get("formatting_token_diffs"), len(ad), len(rm), len(mv),
                    sum(len(s["text"]) for s in ad), sum(len(s["text"]) for s in rm),
                    z(jdump(ad)) if ad else None, z(jdump(rm)) if rm else None, z(jdump(mv)) if mv else None))
            slim = dict(rec)
            slim["changes"] = slim_changes
            rz = z(jdump(slim))
            # build-failing check: the slim record plus stored spans must reproduce record_sha256
            full = dict(slim)
            full["changes"] = [{**sc, **{k: rec["changes"][i][k] for k in PAIR_SPAN_KEYS}}
                               for i, sc in enumerate(slim_changes)]
            if C.record_hash(full) != rsha:
                raise RuntimeError("record_sha256 not reproducible for %s %s->%s" % (setid, va, vb))
            st["changes"] += nch
            st["formatting_changes"] += nf
        n_pairs += 1
        n_nonc += 0 if (gap == 0 and not missing) else 1
        st["comparison:" + comparison] += 1
        st["pairs"] += 1
        if gap or missing:
            st["pairs_nonconsecutive"] += 1
        out["pairs"].append((setid, va, vb, int(gap == 0 and not missing), gap, jdump(missing), comparison,
                             jdump(reasons), ra.get("sha"), rb.get("sha"), ra.get("pub"), rb.get("pub"),
                             ra.get("eff"), rb.get("eff"), nch, nf, rsha, rz))
    nums = [r["version"] for r in vers]
    first = parsed[0][0]["version"] if parsed else None
    out["family"] = (setid, len(vers), sum(1 for r in vers if r.get("sha")), len(parsed), min(nums), max(nums),
                     first, (first - 1) if first is not None else None, n_pairs, n_nonc)
    out["texts"] = texts
    st["families"] += 1
    out["ms"] = int((time.time() - t0) * 1000)
    return out


# ------------------------------------------------------------------------------------------------- shard writer
def _insert(con, table, rows):
    cols = TABLE_COLS[table]
    con.executemany("INSERT INTO %s(%s) VALUES (%s)" % (table, ",".join(cols), ",".join("?" * len(cols))), rows)


def write_shard(path, shard, results, meta, commit_every=100, hook=None):
    """results: iterable of process_family outputs in setid order.  Returns Counter of stats."""
    with open(path, "wb"):
        pass  # truncate (never unlink)
    con = sqlite3.connect(path, isolation_level=None)
    try:
        con.executescript("PRAGMA page_size=4096; PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; "
                          "PRAGMA locking_mode=EXCLUSIVE;")
        con.execute("BEGIN")
        for stmt in SCHEMA.split(";"):
            if stmt.strip():
                con.execute(stmt)
        for k, v in sorted({**meta, "shard": shard}.items()):
            con.execute("INSERT INTO meta VALUES (?,?)", (k, v if isinstance(v, str) else jdump(v)))
        stats, n = collections.Counter(), 0
        for res in results:
            _insert(con, "families", [res["family"]])
            _insert(con, "versions", res["versions"])
            _insert(con, "sections", res["sections"])
            if res["texts"]:
                con.executemany("INSERT OR IGNORE INTO texts(h, z) VALUES (?,?)", sorted(res["texts"].items()))
            _insert(con, "pairs", res["pairs"])
            _insert(con, "changes", res["changes"])
            stats.update(res["stats"])
            n += 1
            if hook:
                hook(n, res)
            if n % commit_every == 0:
                con.execute("COMMIT")
                con.execute("BEGIN")
        con.execute("COMMIT")
        stats["texts_distinct"] = con.execute("SELECT count(*) FROM texts").fetchone()[0]
        con.execute("PRAGMA user_version=1")
    finally:
        con.close()
    return stats


def full_record(con, setid, from_version, to_version):
    """Reconstruct the complete change record of a pair from a shard connection (None when no record)."""
    row = con.execute("SELECT record_z, record_sha256 FROM pairs WHERE setid=? AND from_version=? AND to_version=?",
                      (setid, from_version, to_version)).fetchone()
    if not row or row[0] is None:
        return None
    rec = json.loads(unz(row[0]))
    spans = {r[0]: r[1:] for r in con.execute(
        "SELECT idx, added_spans_z, removed_spans_z, moved_spans_z FROM changes "
        "WHERE setid=? AND from_version=? AND to_version=?", (setid, from_version, to_version))}
    for i, c in enumerate(rec["changes"]):
        for k, blob in zip(PAIR_SPAN_KEYS, spans[i]):
            c[k] = json.loads(unz(blob)) if blob else []
    return rec


def section_text(con, h):
    if h is None:
        return ""
    return unz(con.execute("SELECT z FROM texts WHERE h=?", (h,)).fetchone()[0])


# ------------------------------------------------------------------------------------------------- orchestration
def atomic_write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def copy_verified(src, dst, expect_sha):
    tmp = dst + ".tmp"
    with open(src, "rb") as a, open(tmp, "wb") as b:
        shutil.copyfileobj(a, b, 1 << 22)
        b.flush()
        os.fsync(b.fileno())
    if sha256_file(tmp) != expect_sha:
        raise RuntimeError("copy of %s to %s did not verify" % (src, dst))
    os.replace(tmp, dst)


def done_sidecar(shards_dir, shard):
    return os.path.join(shards_dir, shard + ".done.json")


def shard_done(shards_dir, shard):
    sc = done_sidecar(shards_dir, shard)
    fp = os.path.join(shards_dir, shard + ".sqlite")
    if not (os.path.exists(sc) and os.path.exists(fp)):
        return None
    try:
        meta = json.load(open(sc, encoding="utf-8"))
    except ValueError:
        return None
    return meta if meta.get("bytes") == os.path.getsize(fp) else None


def select_setids(inventory, pilot=None):
    ids = sorted(inventory)
    if pilot:
        frac, seed = pilot
        ids = sorted(random.Random(seed).sample(ids, max(1, round(frac * len(ids)))))
    return ids


def run(out_dir, inventory, resolve, *, scratch_dir, inputs=None, workers=4, pilot=None, pause=0.0,
        require_dir=None, max_raw_failures=3, max_shards=None, expected_frozen=None, log=print, family_hook=None, config_extra=None):
    """inventory: {setid: [{"version","sha","member","states","pub","eff"}]}; resolve(shas)->{sha:[paths]}."""
    code = check_frozen(expected_frozen)
    check_ssd(require_dir)
    shards_dir = os.path.join(out_dir, "shards")
    os.makedirs(shards_dir, exist_ok=True)
    os.makedirs(scratch_dir, exist_ok=True)
    setids = select_setids(inventory, pilot)
    by_shard = collections.defaultdict(list)
    for s in setids:
        by_shard[shard_of(s)].append(s)
    shard_list = sorted(by_shard)
    rebuild_sha = sha256_file(os.path.abspath(__file__))
    config = {"format": FORMAT, "frozen_code_sha256": code, "versions": EXPECTED_VERSIONS,
              "pilot": list(pilot) if pilot else None, "n_setids": len(setids), "shards": shard_list,
              "inventory_fingerprint": hashlib.sha256(jdump(
                  [[s, [(r["version"], r.get("sha")) for r in sorted(inventory[s], key=lambda r: r["version"])]]
                   for s in setids]).encode()).hexdigest(),
              "inputs": inputs or {}, "rebuild_py_sha256": rebuild_sha, **(config_extra or {})}
    cfg_path = os.path.join(out_dir, "build-config.json")
    if os.path.exists(cfg_path):
        old = json.load(open(cfg_path, encoding="utf-8"))
        new = json.loads(jdump(config))
        # rebuild.py itself may be hardened between resumes; every shard sidecar records the module hash it used
        old.pop("rebuild_py_sha256", None)
        new.pop("rebuild_py_sha256", None)
        if old != new:
            raise RuntimeError("build-config.json differs from this run's configuration; refusing to resume "
                               "into %s (use a fresh output directory)" % out_dir)
    else:
        atomic_write(cfg_path, json.dumps(config, indent=1, sort_keys=True) + "\n")
    try:
        runlog = open(os.path.join(out_dir, "runlog.jsonl"), "a", encoding="utf-8")
    except OSError:
        runlog = io.StringIO()  # the log is advisory; losing it must not stop the build

    def event(**kw):
        nonlocal runlog
        line = json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **kw}, sort_keys=True) + "\n"
        for attempt in (1, 2):
            try:
                runlog.write(line)
                runlog.flush()
                return
            except (OSError, ValueError):
                try:
                    runlog = open(os.path.join(out_dir, "runlog.jsonl"), "a", encoding="utf-8")
                except OSError:
                    break
        log("runlog write failed (continuing): " + line.strip())  # a log failure must never abort or unfinalize a shard
    event(ev="start", workers=workers, shards=len(shard_list), setids=len(setids), pid=os.getpid())
    meta = {"format": FORMAT, "parser_version": EXPECTED_VERSIONS["parser"],
            "groups_version": EXPECTED_VERSIONS["groups"], "changes_version": EXPECTED_VERSIONS["changes"],
            "frozen_code_sha256": code, "zlib_level": str(ZLEVEL)}
    pool = None
    if workers and workers > 0:
        pool = multiprocessing.get_context("spawn").Pool(workers, _init_worker, (expected_frozen,))
    t_start = time.time()
    done_n = built = 0
    try:
        for shard in shard_list:
            if shard_done(shards_dir, shard):
                done_n += 1
                continue
            if max_shards is not None and built >= max_shards:
                break
            check_ssd(require_dir)
            t0 = time.time()
            sids = by_shard[shard]
            shas = sorted({r["sha"] for s in sids for r in inventory[s] if r.get("sha")})
            cand = resolve(shas)
            tasks = [(s, [{**r, "candidates": cand.get(r.get("sha"), [])} for r in inventory[s]]) for s in sids]
            if pool:
                results = pool.imap(process_family, tasks, chunksize=1)
            else:
                results = map(process_family, tasks)
            scratch = os.path.join(scratch_dir, "shard-work.sqlite")
            slow = []

            def hook(n, res, slow=slow):
                if res["ms"] > 60000:
                    slow.append([res["setid"], res["ms"]])
                if family_hook:
                    family_hook(n, res)
            stats = write_shard(scratch, shard, results, meta, hook=hook)
            check_ssd(require_dir)  # the SSD must still be there; otherwise this shard is not counted
            try:
                check_shard_reads(shard, stats, max_raw_failures)
            except ShardAnomaly as exc:
                event(ev="shard_anomaly", shard=shard, message=str(exc))
                raise
            sha, size = sha256_file(scratch), os.path.getsize(scratch)
            final = os.path.join(shards_dir, shard + ".sqlite")
            copy_verified(scratch, final, sha)
            check_ssd(require_dir)
            atomic_write(done_sidecar(shards_dir, shard), json.dumps(
                {"shard": shard, "sha256": sha, "bytes": size, "setids": len(sids), "rebuild_py_sha256": rebuild_sha,
                 "stats": {k: stats[k] for k in sorted(stats)}}, indent=1, sort_keys=True) + "\n")
            built += 1
            done_n += 1
            event(ev="shard_done", shard=shard, setids=len(sids), seconds=round(time.time() - t0, 1), bytes=size,
                  versions=sum(v for k, v in stats.items() if k.startswith("status:")), slow_families=slow)
            log("shard %s done (%d/%d) %d families %.0fs %.1f MB; elapsed %.0fs" % (
                shard, done_n, len(shard_list), len(sids), time.time() - t0, size / 1e6, time.time() - t_start))
            if pause:
                time.sleep(pause)
    finally:
        if pool:
            pool.terminate()
            pool.join()
    remaining = [s for s in shard_list if not shard_done(shards_dir, s)]
    if remaining:
        event(ev="stopped_incomplete", remaining=len(remaining))
        return None
    manifest = finalize(out_dir, shard_list, config, setids)
    event(ev="complete", shards=len(shard_list))
    try:
        runlog.close()
    except OSError:
        pass
    return manifest


def finalize(out_dir, shard_list, config, setids):
    """Re-hash every shard, compare with its sidecar, aggregate counts, write COMPLETE.json last and atomically."""
    shards_dir = os.path.join(out_dir, "shards")
    totals, per = collections.Counter(), {}
    for s in shard_list:
        side = json.load(open(done_sidecar(shards_dir, s), encoding="utf-8"))
        fp = os.path.join(shards_dir, s + ".sqlite")
        sha = sha256_file(fp)
        if sha != side["sha256"] or os.path.getsize(fp) != side["bytes"]:
            raise RuntimeError("shard %s does not match its sidecar" % s)
        per[s] = {"sha256": sha, "bytes": side["bytes"], "rebuild_py_sha256": side.get("rebuild_py_sha256")}
        totals.update(side["stats"])
        totals["bytes"] += side["bytes"]
    totals["setids"] = len(setids)
    manifest = {
        "format": FORMAT, "config": config, "counts": {k: totals[k] for k in sorted(totals)},
        "status_counts": {k[7:]: v for k, v in sorted(totals.items()) if k.startswith("status:")},
        "comparison_counts": {k[11:]: v for k, v in sorted(totals.items()) if k.startswith("comparison:")},
        "environment": {"python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
                        "zlib": zlib.ZLIB_VERSION},
        "shards": per,
    }
    atomic_write(os.path.join(out_dir, "COMPLETE.json"), json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    return manifest


# ------------------------------------------------------------------------------------------------- inputs
def open_ro(path):
    return sqlite3.connect("file:%s?mode=ro&immutable=1" % path, uri=True)


def load_inventory(evidence_path):
    """Every (setid, version) in evidence versions; the downloaded row (with raw sha256) wins over other states."""
    con = open_ro(evidence_path)
    pub = {}
    for s, v, d in con.execute("SELECT setid, version, published_date FROM history_entries ORDER BY id"):
        pub[(s, int(v))] = d
    inv = {}
    for s, v, state, sha, mem, eff in con.execute(
            "SELECT setid, version, state, selected_sha256, xml_member, effective_date FROM versions ORDER BY id"):
        e = inv.setdefault(s, {}).setdefault(int(v), {"version": int(v), "states": set(), "sha": None, "member": None,
                                                      "eff": None, "pub": pub.get((s, int(v)))})
        e["states"].add(state)
        if sha and not e["sha"]:
            e["sha"], e["member"] = sha, mem
        if eff and not e["eff"]:
            e["eff"] = eff
    con.close()
    out = {}
    for s, vs in inv.items():
        out[s] = [{**e, "states": "+".join(sorted(e["states"]))} for _, e in sorted(vs.items())]
    return out


def manifest_resolver(manifest_path, bodies_dir=DEFAULT_BODIES):
    """sha256 -> candidate ZIP paths (flat bodies dir first, then raw-manifest copies, sorted)."""
    def resolve(shas):
        res = {}
        con = open_ro(manifest_path)
        for i in range(0, len(shas), 500):
            chunk = shas[i:i + 500]
            for sha, p in con.execute("SELECT sha256, path FROM copies WHERE sha256 IN (%s)" % ",".join("?" * len(chunk)),
                                      chunk):
                res.setdefault(sha, []).append(p)
        con.close()
        out = {}
        for sha in shas:
            paths = sorted(set(res.get(sha, [])), key=lambda p: (not p.endswith("/" + sha), p))
            out[sha] = [os.path.join(bodies_dir, sha)] + [p for p in paths if p != os.path.join(bodies_dir, sha)]
        return out
    return resolve


def evidence_expected_sha(evidence_path):
    vj = os.path.join(os.path.dirname(evidence_path), "VERIFY.json")
    try:
        d = json.load(open(vj, encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for k in ("sha256", "db_sha256", "output_sha256"):
        if isinstance(d.get(k), str):
            return d[k]
    for v in d.values():
        if isinstance(v, dict) and isinstance(v.get("sha256"), str):
            return v["sha256"]
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--evidence", default=DEFAULT_EVIDENCE)
    ap.add_argument("--evidence-sha256", default=None, help="expected full sha256 (default: from VERIFY.json)")
    ap.add_argument("--raw-manifest", default=DEFAULT_MANIFEST)
    ap.add_argument("--scratch", required=True)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--pilot-fraction", type=float, default=None)
    ap.add_argument("--seed", type=int, default=20261111)
    ap.add_argument("--pause", type=float, default=3.0)
    ap.add_argument("--max-shards", type=int, default=None)
    ap.add_argument("--no-ssd-check", action="store_true")
    ap.add_argument("--skip-evidence-hash", action="store_true")
    a = ap.parse_args(argv)
    if a.workers > 4:
        sys.exit("at most 4 workers (SSD care)")
    os.nice(10)
    require = None if a.no_ssd_check else SSD_ROOT
    check_frozen()
    check_ssd(require)
    inputs = {"evidence_path": a.evidence, "raw_manifest_path": a.raw_manifest}
    if not a.skip_evidence_hash:
        expect = a.evidence_sha256 or evidence_expected_sha(a.evidence)
        t = time.time()
        got = sha256_file(a.evidence)
        print("evidence sha256 %s (%.0fs)" % (got, time.time() - t), flush=True)
        if expect and got != expect:
            sys.exit("evidence database sha256 %s != expected %s" % (got, expect))
        if not expect and not a.evidence_sha256:
            print("warning: no expected evidence sha256 available", flush=True)
        inputs["evidence_sha256"] = got
    inputs["raw_manifest_bytes"] = os.path.getsize(a.raw_manifest)
    inv = load_inventory(a.evidence)
    print("inventory: %d families, %d versions" % (len(inv), sum(len(v) for v in inv.values())), flush=True)
    os.makedirs(a.out, exist_ok=True)
    manifest = run(a.out, inv, manifest_resolver(a.raw_manifest), scratch_dir=a.scratch, inputs=inputs,
                   workers=a.workers, pilot=(a.pilot_fraction, a.seed) if a.pilot_fraction else None, pause=a.pause,
                   require_dir=require, max_shards=a.max_shards, log=lambda m: print(m, flush=True))
    if not a.skip_evidence_hash and manifest is not None:
        after = sha256_file(a.evidence)
        print("evidence sha256 after run %s %s" % (after, "UNCHANGED" if after == inputs["evidence_sha256"] else "CHANGED"),
              flush=True)
        if after != inputs["evidence_sha256"]:
            sys.exit(2)
    print("COMPLETE" if manifest else "INCOMPLETE", flush=True)


if __name__ == "__main__":
    main()
