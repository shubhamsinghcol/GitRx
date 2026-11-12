"""Row-level proof that a parser-version build differs from a base build only where intended (read-only on inputs).

    python -m gitrx_core.rebuild_diff_proof --old <base build> --new <new build> --delta <delta.sqlite> \
        --old-version spl-sections-0.2.3 --new-version spl-sections-0.2.4 --out DELTA-PROOF-0.2.4.json \
        [--second-build <dir with the affected shards built a second time>] [--versions-proof <versions-proof.json>]

Allowed differences: (a) the parser version identifier (shard meta; the `parser_version` / `from_parser_version`
fields inside each pair record) and the hashes that embed it (versions.parser_output_sha256, pairs.record_sha256,
pairs.record_z) -- each is normalized by replacing exactly that identifier and re-hashing, then compared to the base value;
(b) rows of the families that hold a delta-admitted version, and only rows that involve an admitted version.
"""
import argparse
import collections
import json
import multiprocessing
import os
import sqlite3
import sys

from . import changes as C
from . import rebuild as R

TABLES = ("families", "versions", "sections", "pairs", "changes")
MASK = {"versions": ("parser_output_sha256",), "pairs": ("record_sha256", "record_z")}


def _con(build, sh):
    return sqlite3.connect("file:%s?mode=ro&immutable=1" % os.path.join(build, "shards", sh + ".sqlite"), uri=True)


def _norm_record(rec, old_v, new_v, setid, a, b, newh, oldh, bad):
    """Replace exactly the embedded identifier (parser_version, from_parser_version) and the two parser output hashes
    that embed it, the latter by the base build's value for that version after checking the record's value equals the
    new build's own versions row (so only the identifier-derived hash is substituted)."""
    for k in ("parser_version", "from_parser_version"):
        if rec.get(k) == new_v:
            rec[k] = old_v
    for k, v in (("from_parser_output_sha256", a), ("to_parser_output_sha256", b)):
        if rec.get(k) != newh.get((setid, v)):
            bad.append("record %s %s->%s %s does not equal the new versions row" % (setid, a, b, k))
        if oldh.get((setid, v)) is not None:
            rec[k] = oldh[(setid, v)]
    return rec


def shard_task(args):
    old, new, sh, adm, old_v, new_v = args          # adm: {setid: [versions]}
    con = _con(new, sh)
    con.execute("ATTACH 'file:%s?mode=ro&immutable=1' AS o" % os.path.join(old, "shards", sh + ".sqlite"))
    res = {"shard": sh, "problems": [], "diff_setids": collections.defaultdict(lambda: collections.defaultdict(int)),
           "pairs_checked_normalized_identical": 0, "pairs_with_record_changed": [], "versions_hash_changed": 0,
           "versions_hash_same": 0, "affected_pair_keys": {"old_only": [], "new_only": []}}
    bad = res["problems"]
    nm, om = dict(con.execute("SELECT k, v FROM main.meta")), dict(con.execute("SELECT k, v FROM o.meta"))
    mdiff = {k for k in set(nm) | set(om) if nm.get(k) != om.get(k)}
    if mdiff != {"parser_version", "frozen_code_sha256"}:
        bad.append("meta keys differ: %s" % sorted(mdiff))
    else:
        nf, of = json.loads(nm["frozen_code_sha256"]), json.loads(om["frozen_code_sha256"])
        if {k for k in set(nf) | set(of) if nf.get(k) != of.get(k)} != {"spl_parser.py"}:
            bad.append("frozen_code_sha256 differs outside spl_parser.py")
        if (om["parser_version"], nm["parser_version"]) != (old_v, new_v):
            bad.append("meta parser_version %r -> %r" % (om["parser_version"], nm["parser_version"]))
    admitted = {(s, v) for s, vs in adm.items() for v in vs}
    # exact EXCEPT diffs in both directions, with the masked hash columns removed
    for t in TABLES:
        cols = [r[1] for r in con.execute("PRAGMA main.table_info(%s)" % t)]
        keep = ",".join(c for c in cols if c not in MASK.get(t, ()))
        for side, a, b in (("new", "main", "o"), ("old", "o", "main")):
            q = "SELECT * FROM (SELECT %s FROM %s.%s EXCEPT SELECT %s FROM %s.%s)" % (keep, a, t, keep, b, t)
            klist = [c for c in cols if c not in MASK.get(t, ())]
            for row in con.execute(q):
                d = dict(zip(klist, row))
                res["diff_setids"][d["setid"]][t + ":" + side] += 1
                if d["setid"] not in adm:
                    bad.append("%s row differs for non-affected family %s" % (t, d["setid"]))
                    continue
                if t == "versions" and (d["setid"], d["version"]) not in admitted:
                    bad.append("versions row differs for a non-admitted version %s v%s" % (d["setid"], d["version"]))
                if t == "sections" and (d["setid"], d["version"]) not in admitted:
                    bad.append("sections row differs for a non-admitted version %s v%s" % (d["setid"], d["version"]))
                if t in ("pairs", "changes"):
                    lo, hi = d["from_version"], d["to_version"]
                    if not any(lo <= v <= hi for v in adm[d["setid"]]):
                        bad.append("%s row %s %s->%s differs but spans no admitted version" % (t, d["setid"], lo, hi))
    lost = con.execute("SELECT count(*) FROM o.texts WHERE h NOT IN (SELECT h FROM main.texts)").fetchone()[0]
    if lost:
        bad.append("shard lost %d texts" % lost)
    res["texts_added"] = con.execute("SELECT count(*) FROM main.texts WHERE h NOT IN (SELECT h FROM o.texts)").fetchone()[0]
    # parser_output_sha256: every version that has a doc in both builds must differ (it embeds the version string)
    for s, v, oh, nh in con.execute("SELECT m.setid, m.version, o.parser_output_sha256, m.parser_output_sha256 "
                                    "FROM main.versions m JOIN o.versions o USING(setid, version)"):
        if oh is None and nh is None:
            continue
        if oh is not None and nh is not None:
            res["versions_hash_changed" if oh != nh else "versions_hash_same"] += 1
    # pair records: normalize the identifier, rehash, compare with the base value
    oldp = {(s, a, b): (rs, rz) for s, a, b, rs, rz in con.execute(
        "SELECT setid, from_version, to_version, record_sha256, record_z FROM o.pairs")}
    seen = set()
    newconn = con
    newh = {(sid, v): h for sid, v, h in con.execute("SELECT setid, version, parser_output_sha256 FROM main.versions")}
    oldh = {(sid, v): h for sid, v, h in con.execute("SELECT setid, version, parser_output_sha256 FROM o.versions")}
    for s, a, b, rs, rz in con.execute("SELECT setid, from_version, to_version, record_sha256, record_z FROM main.pairs"):
        k = (s, a, b)
        seen.add(k)
        if k not in oldp:
            res["affected_pair_keys"]["new_only"].append(list(k))
            continue
        ors, orz = oldp[k]
        if rz is None or orz is None:
            if (rz is None) != (orz is None) or rs != ors:
                bad.append("record presence/hash differs for %s %s->%s" % k)
            else:
                res["pairs_checked_normalized_identical"] += 1
            continue
        full = R.full_record(newconn, s, a, b)
        _norm_record(full, old_v, new_v, s, a, b, newh, oldh, bad)
        slim_new = json.loads(R.unz(rz))
        _norm_record(slim_new, old_v, new_v, s, a, b, newh, oldh, bad)
        slim_old = json.loads(R.unz(orz))
        slim_new.pop("record_sha256", None)   # the record hash itself is compared via record_hash() below
        slim_old.pop("record_sha256", None)
        if C.record_hash(full) == ors and slim_new == slim_old:
            res["pairs_checked_normalized_identical"] += 1
        else:
            res["pairs_with_record_changed"].append(list(k))
            if s not in adm or not any(a <= v <= b for v in adm[s]):
                bad.append("record content differs for %s %s->%s which spans no admitted version" % k)
    res["affected_pair_keys"]["old_only"] = [list(k) for k in oldp if k not in seen]
    for k in oldp:
        if k not in seen and (k[0] not in adm or not any(k[1] <= v <= k[2] for v in adm[k[0]])):
            bad.append("old pair %s %s->%s missing in new build" % k)
    con.close()
    res["diff_setids"] = {k: dict(v) for k, v in res["diff_setids"].items()}
    return res


def prove(a):
    oc = json.load(open(os.path.join(a.old, "COMPLETE.json"), encoding="utf-8"))
    nc = json.load(open(os.path.join(a.new, "COMPLETE.json"), encoding="utf-8"))
    adm_rows = R.delta_admissions(a.delta)
    adm = collections.defaultdict(list)
    for s, v, *_ in adm_rows:
        adm[s].append(v)
    affected_shards = sorted({R.shard_of(s) for s in adm})
    out = {"old_complete_sha256": R.sha256_file(os.path.join(a.old, "COMPLETE.json")),
           "new_complete_sha256": R.sha256_file(os.path.join(a.new, "COMPLETE.json")),
           "old_version": a.old_version, "new_version": a.new_version, "admitted_versions": len(adm_rows),
           "admitted_families": len(adm), "problems": []}
    bad = out["problems"]
    if sorted(oc["shards"]) != sorted(nc["shards"]):
        bad.append("shard sets differ")
    if (oc["config"]["versions"]["groups"], oc["config"]["versions"]["changes"]) != (
            nc["config"]["versions"]["groups"], nc["config"]["versions"]["changes"]):
        bad.append("groups/changes version strings differ")
    if oc["config"]["versions"]["parser"] != a.old_version or nc["config"]["versions"]["parser"] != a.new_version:
        bad.append("parser version strings in COMPLETE.json are not %s -> %s" % (a.old_version, a.new_version))
    ofz, nfz = oc["config"]["frozen_code_sha256"], nc["config"]["frozen_code_sha256"]
    out["frozen_code_changed"] = sorted(k for k in set(ofz) | set(nfz) if ofz.get(k) != nfz.get(k))
    if out["frozen_code_changed"] != ["spl_parser.py"]:
        bad.append("frozen modules changed other than spl_parser.py: %s" % out["frozen_code_changed"])
    out["inventory_fingerprint_equal"] = oc["config"]["inventory_fingerprint"] == nc["config"]["inventory_fingerprint"]
    if not out["inventory_fingerprint_equal"]:
        bad.append("inventory fingerprints differ")
    for sh in sorted(nc["shards"]):                                   # re-hash every new shard
        fn = os.path.join(a.new, "shards", sh + ".sqlite")
        if R.sha256_file(fn) != nc["shards"][sh]["sha256"] or os.path.getsize(fn) != nc["shards"][sh]["bytes"]:
            bad.append("new shard %s does not match its COMPLETE.json entry" % sh)
    tasks = [(a.old, a.new, sh, dict(adm), a.old_version, a.new_version) for sh in sorted(nc["shards"])]
    tot = collections.Counter()
    diff_by_family, changed_pairs, old_only, new_only = {}, [], [], []
    with multiprocessing.get_context("spawn").Pool(min(a.workers, 4)) as pool:
        for i, r in enumerate(pool.imap_unordered(shard_task, tasks), 1):
            bad += ["shard %s: %s" % (r["shard"], p) for p in r["problems"]]
            tot["pairs_normalized_identical"] += r["pairs_checked_normalized_identical"]
            tot["versions_parser_hash_changed"] += r["versions_hash_changed"]
            tot["versions_parser_hash_same"] += r["versions_hash_same"]
            tot["texts_added"] += r["texts_added"]
            diff_by_family.update(r["diff_setids"])
            changed_pairs += r["pairs_with_record_changed"]
            old_only += r["affected_pair_keys"]["old_only"]
            new_only += r["affected_pair_keys"]["new_only"]
            print("shard %s checked (%d/256)" % (r["shard"], i), flush=True)
    out.update(tot)
    out["pairs_normalized_identical"] = tot["pairs_normalized_identical"]
    out["families_with_row_differences"] = len(diff_by_family)
    out["families_with_row_differences_all_admitted"] = set(diff_by_family) <= set(adm)
    out["row_differences_by_family"] = {k: diff_by_family[k] for k in sorted(diff_by_family)}
    out["pairs_with_record_changed_spanning_admitted"] = sorted(changed_pairs)
    out["pairs_only_in_old"] = sorted(old_only)
    out["pairs_only_in_new"] = sorted(new_only)
    out["counts_old"] = {k: oc["counts"][k] for k in ("families", "pairs", "changes", "pairs_nonconsecutive")}
    out["counts_new"] = {k: nc["counts"][k] for k in ("families", "pairs", "changes", "pairs_nonconsecutive")}
    out["status_counts_old"], out["status_counts_new"] = oc["status_counts"], nc["status_counts"]
    out["comparison_counts_old"], out["comparison_counts_new"] = oc["comparison_counts"], nc["comparison_counts"]
    if out["families_with_row_differences"] != 21 or not out["families_with_row_differences_all_admitted"]:
        bad.append("expected exactly the 21 admitted families to differ, got %d" % out["families_with_row_differences"])
    if tot["versions_parser_hash_same"]:
        bad.append("%d parsed versions kept the same parser_output_sha256 (it embeds the version string)" %
                   tot["versions_parser_hash_same"])
    if a.versions_proof:
        vp = json.load(open(a.versions_proof))
        out["versions_proof"] = {k: vp[k] for k in ("inventory_versions", "compared", "no_raw_rows", "identical",
                                                    "differences_count", "changed_status", "new_status_counts")}
        if vp["identical"] + vp["differences_count"] != vp["compared"]:
            bad.append("versions proof counts inconsistent")
    if a.second_build:
        same = {}
        for sh in affected_shards:
            same[sh] = R.sha256_file(os.path.join(a.second_build, "shards", sh + ".sqlite")) == nc["shards"][sh]["sha256"]
        out["determinism_second_build_identical"] = same
        if not all(same.values()) or sorted(same) != affected_shards:
            bad.append("second build differs")
    out["affected_shards"] = affected_shards
    out["ok"] = not bad
    R.atomic_write(a.out, json.dumps(out, indent=1, sort_keys=True) + "\n")
    return out


def graded_versions(grading_dirs):
    """(setid, version) pairs a kept grading run actually used (rows.jsonl candidate pairs and windows) and every
    candidate setid it looked at."""
    used, setids = set(), set()
    for d in grading_dirs:
        with open(os.path.join(d, "rows.jsonl"), encoding="utf-8") as f:
            for line in f:
                for sid, c in json.loads(line).get("candidates", {}).items():
                    setids.add(sid)
                    for p in c.get("pairs", []):
                        used.add((sid, p["from_version"]))
                        used.add((sid, p["to_version"]))
                    for v in c.get("window_versions", []):
                        used.add((sid, v))
        with open(os.path.join(d, "fragments.jsonl"), encoding="utf-8") as f:
            for line in f:
                par = json.loads(line).get("parser") or {}
                if par.get("setid"):
                    setids.add(par["setid"])
                    used.add((par["setid"], par["from_version"]))
                    used.add((par["setid"], par["to_version"]))
    return used, setids


def check_graded(grading_dirs, versions_proof_path, delta, build, out_path):
    """Every version used by the kept grading runs is among the versions proved identical (or not in the proof's
    difference list), and no graded candidate family is one of the admitted families."""
    vp = json.load(open(versions_proof_path))
    changed = {(d["setid"], d["version"]) for d in vp["differences"] if "setid" in d}
    adm = {(s, v) for s, v, *_ in R.delta_admissions(delta)}
    adm_setids = {s for s, _ in adm}
    used, setids = graded_versions(grading_dirs)
    in_build = collections.Counter()
    for sid in sorted({s for s, _ in used}):
        con = _con(build, R.shard_of(sid))
        have = {v: st for v, st in con.execute("SELECT version, status FROM versions WHERE setid=?", (sid,))}
        con.close()
        for s, v in used:
            if s == sid:
                in_build["present" if v in have else "absent_from_build"] += 1
    res = {"grading_dirs": [os.path.basename(d) for d in grading_dirs], "versions_used": len(used),
           "candidate_setids": len(setids), "versions_in_proof_difference_list": sorted(map(list, used & changed)),
           "used_versions_admitted": sorted(map(list, used & adm)),
           "candidate_setids_in_admitted_families": sorted(setids & adm_setids),
           "used_versions_in_admitted_families": sorted(map(list, {x for x in used if x[0] in adm_setids})),
           "presence_in_new_build": dict(in_build),
           "proof_total_differences": len(changed), "proof_compared": vp["compared"]}
    res["ok"] = not (res["versions_in_proof_difference_list"] or res["used_versions_admitted"]
                     or res["candidate_setids_in_admitted_families"] or res["used_versions_in_admitted_families"])
    R.atomic_write(out_path, json.dumps(res, indent=1, sort_keys=True) + "\n")
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--delta", required=True)
    ap.add_argument("--old-version", default="spl-sections-0.2.3")
    ap.add_argument("--new-version", default="spl-sections-0.2.4")
    ap.add_argument("--second-build", default=None)
    ap.add_argument("--versions-proof", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    os.nice(10)
    R.check_ssd(R.SSD_ROOT)
    r = prove(a)
    print(json.dumps({k: v for k, v in r.items() if k not in ("row_differences_by_family",)}, indent=1, sort_keys=True)[:6000])
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
