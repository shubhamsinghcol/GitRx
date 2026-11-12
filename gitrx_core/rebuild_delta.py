"""Delta evidence database for admitting already-held raw bodies, plus delta-build proofs (read-only on inputs).

    python -m gitrx_core.rebuild_delta create --out <delta.sqlite> [--admitted-at ISO]
    python -m gitrx_core.rebuild_delta verify --delta <delta.sqlite> --expect-sha256 <hex>

The delta database is APPEND-ONLY (triggers refuse UPDATE/DELETE) and never rewrites evidence-v1.  Each row admits one
(setid, version) whose raw body was already retained byte-for-byte but sat as `pending` in evidence-v1.  Nothing is
downloaded here.  The rebuild consumes it through `rebuild.load_inventory(evidence, delta_path)`.
"""
import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import sys
import zipfile

from . import rebuild as R

FORMAT = R.DELTA_FORMAT
REASON = "byte-identical body ledgered but not admitted"
GITRX = R.SSD_ROOT
EVIDENCE = R.DEFAULT_EVIDENCE
GAP_DB = GITRX + "/updates/gap-repair/gap-repair.sqlite"
GAP_LEDGER = GITRX + "/updates/gap-repair/network/ledger.sqlite"
BACKFILL_LEDGER = GITRX + "/backfill-20261107/network/ledger.sqlite"
BODIES = R.DEFAULT_BODIES
GETFILE = "https://dailymed.nlm.nih.gov/dailymed/getFile.cfm?type=zip&setid=%s&version=%d"

SCHEMA = """
CREATE TABLE delta_meta(k TEXT PRIMARY KEY, v TEXT) WITHOUT ROWID;
CREATE TABLE admissions(
  id INTEGER PRIMARY KEY, setid TEXT NOT NULL, version INTEGER NOT NULL, raw_sha256 TEXT NOT NULL, bytes INTEGER NOT NULL,
  xml_member TEXT NOT NULL, effective_date TEXT, source_body_path TEXT NOT NULL, admission_reason TEXT NOT NULL,
  admitted_at TEXT NOT NULL, evidence_v1_version_row_id INTEGER, evidence_v1_state TEXT, evidence_v1_published_date TEXT,
  ledger_requests_json TEXT NOT NULL, backfill_ledger_requests_json TEXT NOT NULL, gap_repair_json TEXT NOT NULL,
  UNIQUE(setid, version));
CREATE TRIGGER admissions_no_update BEFORE UPDATE ON admissions BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER admissions_no_delete BEFORE DELETE ON admissions BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER meta_no_update BEFORE UPDATE ON delta_meta BEGIN SELECT RAISE(ABORT,'append-only'); END;
CREATE TRIGGER meta_no_delete BEFORE DELETE ON delta_meta BEGIN SELECT RAISE(ABORT,'append-only'); END;
"""
EFF_RE = re.compile(rb'<effectiveTime[^>]*value="(\d{8})')


def iso(epoch):
    return dt.datetime.fromtimestamp(float(epoch), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def jd(obj):
    return R.jdump(obj)


def inspect_body(path, setid, version):
    """(xml_member, effective_date) after checking the ZIP's XML setId/versionNumber equal the target."""
    from .gap_repair import SETID_RE, VERSION_RE
    with zipfile.ZipFile(path) as zf:
        xmls = [n for n in zf.namelist() if n.lower().endswith(".xml")]
        if len(xmls) != 1:
            raise ValueError("%s v%s: %d xml members" % (setid, version, len(xmls)))
        xml = zf.read(xmls[0])
    s, v = SETID_RE.search(xml), VERSION_RE.search(xml)
    if not s or not v or s.group(1).decode().lower() != setid or int(v.group(1)) != version:
        raise ValueError("%s v%s: xml identity does not match the target" % (setid, version))
    m = EFF_RE.search(xml)
    return xmls[0], (m.group(1).decode() if m else None)


def collect(evidence=EVIDENCE, gap_db=GAP_DB, gap_ledger=GAP_LEDGER, backfill_ledger=BACKFILL_LEDGER, bodies=BODIES,
            admitted_at=None):
    ev = R.open_ro(evidence)
    gap = sqlite3.connect("file:%s?mode=ro" % gap_db, uri=True)
    gl = sqlite3.connect("file:%s?mode=ro" % gap_ledger, uri=True)
    bl = sqlite3.connect("file:%s?mode=ro" % backfill_ledger, uri=True)
    rows = gap.execute("SELECT id, run_id, setid, version, url, retrieved_at, status, sha256, bytes FROM attempts "
                       "WHERE kind='retry_unavailable' AND outcome='recovered_new' AND identity_ok=1 "
                       "ORDER BY setid, version").fetchall()
    out = []
    for aid, run_id, s, v, url, got_at, status, sha, nbytes in rows:
        path = os.path.join(bodies, sha)
        if R.sha256_file(path) != sha or os.path.getsize(path) != nbytes:
            raise ValueError("%s v%s: held body differs from the re-fetched sha256/bytes" % (s, v))
        member, eff = inspect_body(path, s, v)
        vr = ev.execute("SELECT id, state, selected_sha256 FROM versions WHERE setid=? AND version=?", (s, v)).fetchall()
        if len(vr) != 1 or vr[0][2] is not None or vr[0][1] != "pending":
            raise ValueError("%s v%s: not a single pending evidence-v1 row without a raw artifact: %r" % (s, v, vr))
        pub = ev.execute("SELECT published_date FROM history_entries WHERE setid=? AND version=?", (s, v)).fetchone()
        reqs = [{"evidence_v1_http_request_id": i, "url": u, "started_at": iso(t), "status": st, "sha256": h, "bytes": b,
                 "same_url_as_target": u == GETFILE % (s, v)}
                for i, u, t, st, h, b in ev.execute(
                    "SELECT id, url, started_epoch, status, sha256, bytes FROM http_requests WHERE sha256=? ORDER BY id",
                    (sha,))]
        if not any(r["same_url_as_target"] and r["status"] == 200 for r in reqs):
            raise ValueError("%s v%s: no evidence-v1 getFile request with this sha256" % (s, v))
        breqs = [{"backfill_ledger_request_id": i, "url": u, "started_at": iso(t), "status": st, "sha256": h}
                 for i, u, t, st, h in bl.execute("SELECT id, url, started, status, sha256 FROM requests WHERE sha256=?",
                                                  (sha,))]
        g = gl.execute("SELECT id, url, started, status, sha256, bytes FROM requests WHERE url=? AND sha256=?",
                       (url, sha)).fetchone()
        out.append(dict(
            setid=s, version=v, raw_sha256=sha, bytes=nbytes, xml_member=member, effective_date=eff, source_body_path=path,
            admission_reason=REASON, admitted_at=admitted_at, evidence_v1_version_row_id=vr[0][0],
            evidence_v1_state=vr[0][1], evidence_v1_published_date=pub[0] if pub else None,
            ledger_requests_json=jd(reqs), backfill_ledger_requests_json=jd(breqs),
            gap_repair_json=jd({"gap_repair_attempt_id": aid, "run_id": run_id, "url": url, "retrieved_at": got_at,
                                "http_status": status, "sha256": sha, "bytes": nbytes, "identity_ok": 1,
                                "gap_ledger_request_id": g[0] if g else None,
                                "gap_ledger_started_at": iso(g[2]) if g else None,
                                "confirms_sha256_equal_to_held_body": True})))
    for c in (ev, gap, gl, bl):
        c.close()
    return out


def create(out, *, admitted_at, evidence=EVIDENCE, **kw):
    if os.path.exists(out):
        raise FileExistsError("refusing to overwrite %s" % out)
    adm = collect(evidence=evidence, admitted_at=admitted_at, **kw)
    ev_sha = R.sha256_file(evidence)
    con = sqlite3.connect(out)
    try:
        con.executescript("PRAGMA page_size=4096; PRAGMA journal_mode=DELETE;")
        con.executescript(SCHEMA)
        cols = list(adm[0])
        with con:
            for k, v in sorted({"format": FORMAT, "created_at": admitted_at, "evidence_path": evidence,
                                "evidence_sha256": ev_sha, "n_admissions": str(len(adm)),
                                "note": "Append-only. evidence-v1 is not modified; rows admit already-held raw bodies."}.items()):
                con.execute("INSERT INTO delta_meta VALUES(?,?)", (k, v))
            for a in adm:
                con.execute("INSERT INTO admissions(%s) VALUES (%s)" % (",".join(cols), ",".join("?" * len(cols))),
                            [a[c] for c in cols])
    finally:
        con.close()
    return adm, ev_sha


def verify(delta, expect_sha256=None):
    """Re-derive every admission fact from the retained bodies; returns a dict (raises on a mismatch)."""
    sha = R.sha256_file(delta)
    if expect_sha256 and sha != expect_sha256:
        raise ValueError("delta sha256 %s != expected %s" % (sha, expect_sha256))
    con = R.open_ro(delta)
    n = 0
    states = collections.Counter()
    for s, v, rsha, nbytes, member, eff, path in con.execute(
            "SELECT setid, version, raw_sha256, bytes, xml_member, effective_date, source_body_path FROM admissions"):
        if R.sha256_file(path) != rsha or os.path.getsize(path) != nbytes:
            raise ValueError("%s v%s body hash/size mismatch" % (s, v))
        if inspect_body(path, s, v) != (member, eff):
            raise ValueError("%s v%s member/effective date mismatch" % (s, v))
        n += 1
    for (st,) in con.execute("SELECT evidence_v1_state FROM admissions"):
        states[st] += 1
    meta = dict(con.execute("SELECT k, v FROM delta_meta"))
    con.close()
    return {"delta_sha256": sha, "admissions_verified": n, "evidence_v1_states": dict(states),
            "evidence_sha256_recorded": meta["evidence_sha256"]}


ROW_TABLES = ("families", "versions", "sections", "pairs", "changes")


def prove(old, new, delta, determinism_dir=None):
    """Read-only proof that NEW (a delta build) differs from OLD only where the delta admissions say it must.
    Re-hashes every shard of both builds.  determinism_dir: optional dir of a second build of the affected shards."""
    oc = json.load(open(os.path.join(old, "COMPLETE.json"), encoding="utf-8"))
    nc = json.load(open(os.path.join(new, "COMPLETE.json"), encoding="utf-8"))
    adm = R.delta_admissions(delta)
    affected_setids = sorted({s for s, *_ in adm})
    affected_shards = sorted({R.shard_of(s) for s in affected_setids})
    res = {"old_complete_sha256": R.sha256_file(os.path.join(old, "COMPLETE.json")),
           "new_complete_sha256": R.sha256_file(os.path.join(new, "COMPLETE.json")),
           "affected_setids": len(affected_setids), "affected_shards": affected_shards, "problems": []}
    bad = res["problems"]
    if oc["config"]["frozen_code_sha256"] != nc["config"]["frozen_code_sha256"] or oc["config"]["versions"] != nc["config"]["versions"]:
        bad.append("frozen code / version strings differ between builds")
    if sorted(oc["shards"]) != sorted(nc["shards"]):
        bad.append("shard sets differ")
    identical = 0
    for sh in sorted(nc["shards"]):
        fn = os.path.join(new, "shards", sh + ".sqlite")
        h = R.sha256_file(fn)
        if h != nc["shards"][sh]["sha256"] or os.path.getsize(fn) != nc["shards"][sh]["bytes"]:
            bad.append("new shard %s does not match its COMPLETE.json entry" % sh)
        if sh not in affected_shards:
            if h != R.sha256_file(os.path.join(old, "shards", sh + ".sqlite")) or h != oc["shards"][sh]["sha256"]:
                bad.append("unaffected shard %s differs from the old build" % sh)
            else:
                identical += 1
        elif h == oc["shards"][sh]["sha256"]:
            bad.append("affected shard %s is unexpectedly identical" % sh)
    res["unaffected_shards_byte_identical"] = identical
    res["unaffected_shards_expected"] = len(nc["shards"]) - len(affected_shards)
    diffs, touched = {}, set()
    for sh in affected_shards:
        con = sqlite3.connect("file:%s?mode=ro&immutable=1" % os.path.join(new, "shards", sh + ".sqlite"), uri=True)
        con.execute("ATTACH 'file:%s?mode=ro&immutable=1' AS o" % os.path.join(old, "shards", sh + ".sqlite"))
        if dict(con.execute("SELECT k, v FROM main.meta")) != dict(con.execute("SELECT k, v FROM o.meta")):
            bad.append("meta differs in shard %s" % sh)
        for t in ROW_TABLES:
            only_new = con.execute("SELECT setid, count(*) FROM (SELECT * FROM main.%s EXCEPT SELECT * FROM o.%s) GROUP BY 1" % (t, t)).fetchall()
            only_old = con.execute("SELECT setid, count(*) FROM (SELECT * FROM o.%s EXCEPT SELECT * FROM main.%s) GROUP BY 1" % (t, t)).fetchall()
            for sid, n in only_new:
                diffs.setdefault(sid, {}).setdefault(t, {"new_or_changed": 0, "old_or_changed": 0})["new_or_changed"] += n
            for sid, n in only_old:
                diffs.setdefault(sid, {}).setdefault(t, {"new_or_changed": 0, "old_or_changed": 0})["old_or_changed"] += n
        lost = con.execute("SELECT count(*) FROM o.texts WHERE h NOT IN (SELECT h FROM main.texts)").fetchone()[0]
        if lost:
            bad.append("shard %s lost %d texts" % (sh, lost))
        res.setdefault("texts_added", 0)
        res["texts_added"] += con.execute("SELECT count(*) FROM main.texts WHERE h NOT IN (SELECT h FROM o.texts)").fetchone()[0]
        con.close()
    outside = sorted(set(diffs) - set(affected_setids))
    if outside:
        bad.append("rows differ for non-affected families: %s" % outside[:5])
    res["families_with_row_differences"] = len(diffs)
    res["row_differences_by_family"] = {k: diffs[k] for k in sorted(diffs)}
    # the admitted versions now carry parser rows
    status = collections.Counter()
    for s, v, sha, mem, eff in adm:
        con = sqlite3.connect("file:%s?mode=ro&immutable=1" % os.path.join(new, "shards", R.shard_of(s) + ".sqlite"), uri=True)
        row = con.execute("SELECT status, raw_sha256, inventory_states, n_sections, parsed_setid, parsed_version FROM versions "
                          "WHERE setid=? AND version=?", (s, v)).fetchone()
        old_row = sqlite3.connect("file:%s?mode=ro&immutable=1" % os.path.join(old, "shards", R.shard_of(s) + ".sqlite"),
                                  uri=True).execute("SELECT status FROM versions WHERE setid=? AND version=?", (s, v)).fetchone()
        if old_row[0] != "no_raw":
            bad.append("%s v%s was %s in the old build, expected no_raw" % (s, v, old_row[0]))
        if row[0] in R.NO_DOC or row[1] != sha or row[2] != "delta-admitted+pending" or (
                row[0] != "extraction_defect" and (row[4] != s or row[5] != v)):
            bad.append("%s v%s row unexpected: %r" % (s, v, row))
        status[row[0]] += 1
        con.close()
    res["admitted_version_new_status"] = dict(status)
    res["counts_old"] = {k: oc["counts"][k] for k in ("families", "pairs", "changes", "pairs_nonconsecutive")}
    res["counts_new"] = {k: nc["counts"][k] for k in ("families", "pairs", "changes", "pairs_nonconsecutive")}
    res["status_counts_old"], res["status_counts_new"] = oc["status_counts"], nc["status_counts"]
    res["comparison_counts_old"], res["comparison_counts_new"] = oc["comparison_counts"], nc["comparison_counts"]
    if determinism_dir:
        same = {}
        for sh in affected_shards:
            a = R.sha256_file(os.path.join(determinism_dir, "shards", sh + ".sqlite"))
            same[sh] = a == nc["shards"][sh]["sha256"]
        res["determinism_second_build_identical"] = same
        if not all(same.values()) or sorted(same) != affected_shards:
            bad.append("second build differs")
    res["ok"] = not bad
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["create", "verify", "prove"])
    ap.add_argument("--old")
    ap.add_argument("--new")
    ap.add_argument("--second-build")
    ap.add_argument("--out")
    ap.add_argument("--delta")
    ap.add_argument("--expect-sha256")
    ap.add_argument("--admitted-at", default=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    a = ap.parse_args(argv)
    os.nice(10)
    R.check_ssd(R.SSD_ROOT)
    if a.command == "create":
        adm, ev_sha = create(a.out, admitted_at=a.admitted_at)
        print(json.dumps({"admissions": len(adm), "evidence_sha256": ev_sha, "out": a.out}, indent=1))
    elif a.command == "prove":
        r = prove(a.old, a.new, a.delta, a.second_build)
        print(json.dumps(r, indent=1, sort_keys=True))
        return 0 if r["ok"] else 1
    else:
        print(json.dumps(verify(a.delta or a.out, a.expect_sha256), indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
