"""Validation of a finished Step E rebuild (read-only).

  python -m gitrx_core.rebuild_validate --out <rebuild dir> --sweep <results-0.2.3.jsonl> [--sweep-index index.jsonl]
        [--rows <open rows.jsonl> --cache <grading/cache/pairs>] [--seed 20261111]

(a) inventory coverage + status counts vs the 0.2.3 sweep, (b) re-derive N seeded change records straight from raw
ZIPs with changes.compare_docs and compare record_sha256, (c) compare M open-split SrLC grader digests to the rebuild.
"""
import argparse
import collections
import glob
import gzip
import json
import os
import random
import sqlite3
import sys

from . import changes as C
from . import rebuild as R
from . import spl_parser as P


def shard_con(out, setid):
    return sqlite3.connect("file:%s?mode=ro&immutable=1" % os.path.join(out, "shards", R.shard_of(setid) + ".sqlite"),
                           uri=True)


def all_shards(out):
    return sorted(glob.glob(os.path.join(out, "shards", "*.sqlite")))


def check_a(out, evidence, sweep, sweep_index, delta=None):
    inv = R.load_inventory(evidence, delta)
    want = {(s, r["version"]) for s, rs in inv.items() for r in rs}
    have, status, by_sha = set(), collections.Counter(), {}
    nostatus = 0
    for fp in all_shards(out):
        con = sqlite3.connect("file:%s?mode=ro&immutable=1" % fp, uri=True)
        for s, v, st, sha, pout in con.execute("SELECT setid, version, status, raw_sha256, parser_output_sha256 FROM versions"):
            have.add((s, v))
            status[st] += 1
            if st is None:
                nostatus += 1
            if sha:
                by_sha[sha] = (s, v, st, pout)
        con.close()
    res = {"inventory_versions": len(want), "rebuild_rows": len(have), "missing_rows": len(want - have),
           "extra_rows": len(have - want), "rows_without_status": nostatus, "status_counts": dict(sorted(status.items()))}
    sw = collections.Counter()
    diff_status = diff_hash = overlap = 0
    sw_shas = set()
    examples = []
    for line in open(sweep):
        r = json.loads(line)
        sw[r["status"]] += 1
        sw_shas.add(r["sha"])
        b = by_sha.get(r["sha"])
        if b is None:
            continue
        overlap += 1
        if b[2] != r["status"]:
            diff_status += 1
            examples.append([r["sha"], r["status"], b[2]])
        elif r.get("hash") and r["hash"] != b[3]:
            diff_hash += 1
    res.update(sweep_status_counts=dict(sorted(sw.items())), sweep_overlap=overlap, overlap_status_differs=diff_status,
               overlap_parser_hash_differs=diff_hash, status_diff_examples=examples[:5],
               in_rebuild_not_in_sweep=len(set(by_sha) - sw_shas), in_sweep_not_in_rebuild=len(sw_shas - set(by_sha)))
    extra = collections.Counter()
    for sha, b in by_sha.items():
        if sha not in sw_shas:
            extra[b[2]] += 1
    res["status_of_versions_not_in_sweep"] = dict(sorted(extra.items()))
    return res


def rederive(out, resolve, setid, va, vb):
    con = shard_con(out, setid)
    row = {v: (sha, mem) for v, sha, mem in con.execute(
        "SELECT version, raw_sha256, xml_member FROM versions WHERE setid=? AND version IN (?,?)", (setid, va, vb))}
    docs, shas = [], []
    for v in (va, vb):
        sha, mem = row[v]
        xml, bad, err, _ = R.load_raw(sha, mem, resolve([sha])[sha])
        if bad:
            return None, "raw:%s" % bad
        docs.append(P.parse(xml))
        shas.append(sha)
    rec = C.compare_docs(docs[0], docs[1], shas[0], shas[1])
    stored = con.execute("SELECT record_sha256 FROM pairs WHERE setid=? AND from_version=? AND to_version=?",
                         (setid, va, vb)).fetchone()[0]
    full = R.full_record(con, setid, va, vb)
    return (rec["record_sha256"] == stored and C.record_hash(full) == stored), None


def delta_pairs(out, delta):
    """Pairs (any comparison) in the build that have an admitted delta version as an endpoint."""
    adm = collections.defaultdict(set)
    for s, v, *_ in R.delta_admissions(delta):
        adm[s].add(v)
    res = []
    for s in sorted(adm):
        con = shard_con(out, s)
        for a, b in con.execute("SELECT from_version, to_version FROM pairs WHERE setid=? AND record_sha256 IS NOT NULL "
                                "ORDER BY from_version, to_version", (s,)):
            if a in adm[s] or b in adm[s]:
                res.append((s, a, b))
        con.close()
    return res


def check_b(out, resolve, n, seed, must_include=()):
    pairs = []
    for fp in all_shards(out):
        con = sqlite3.connect("file:%s?mode=ro&immutable=1" % fp, uri=True)
        pairs += list(con.execute("SELECT setid, from_version, to_version FROM pairs WHERE record_sha256 IS NOT NULL"))
        con.close()
    pairs.sort()
    forced = sorted(set(must_include))
    rest = [p for p in pairs if p not in set(forced)]
    sample = forced + random.Random(seed).sample(rest, max(0, min(n - len(forced), len(rest))))
    ok, bad = 0, []
    for s, a, b in sample:
        good, err = rederive(out, resolve, s, a, b)
        if good:
            ok += 1
        else:
            bad.append([s, a, b, err])
    return {"pairs_with_records": len(pairs), "sampled": len(sample), "forced_included": len(forced), "identical": ok, "mismatches": bad}


def check_c(out, rows_path, cache_dir, n, seed):
    from . import srlc_grade as G
    cands = []
    for line in open(rows_path):
        r = json.loads(line)
        for setid, c in r["candidates"].items():
            for p in c.get("pairs", []):
                if p.get("comparison") == "ok":
                    cands.append((setid, p["from_version"], p["to_version"]))
    cands = sorted(set(cands))
    rnd = random.Random(seed)
    rnd.shuffle(cands)
    res = {"candidates": len(cands), "compared": 0, "identical": 0, "differences": [], "no_digest": 0, "not_in_rebuild": 0}
    for setid, a, b in cands:
        if res["compared"] >= n:
            break
        if not os.path.exists(os.path.join(out, "shards", R.shard_of(setid) + ".sqlite")):
            res["not_in_rebuild"] += 1
            continue
        con = shard_con(out, setid)
        rows = {v: sha for v, sha in con.execute("SELECT version, raw_sha256 FROM versions WHERE setid=? AND version IN (?,?)",
                                                 (setid, a, b))}
        if len(rows) < 2 or not con.execute("SELECT 1 FROM pairs WHERE setid=? AND from_version=? AND to_version=?",
                                            (setid, a, b)).fetchone():
            res["not_in_rebuild"] += 1
            continue
        d = G.load_digest(cache_dir, G.digest_key(rows[a], rows[b], P.PARSER_VERSION, C.CHANGES_VERSION))
        if d is None:
            res["no_digest"] += 1
            continue
        res["compared"] += 1
        rec = R.full_record(con, setid, a, b)
        mine = [{"kind": c["kind"], "group": c["srlc_group"], "alt": c.get("alt_srlc_group"),
                 "ordinal_to": c.get("ordinal_to"), "ordinal_from": c.get("ordinal_from"),
                 "section_number": c.get("section_number"), "added": G._channel_intervals(c)} for c in rec["changes"]]
        theirs = [{k: c[k] for k in ("kind", "group", "alt", "ordinal_to", "ordinal_from", "section_number", "added")}
                  for c in d["changes"]]
        if mine == theirs and d.get("record_sha256") == rec["record_sha256"]:
            res["identical"] += 1
        else:
            res["differences"].append([setid, a, b])
    return res


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=R.DEFAULT_OUT)
    ap.add_argument("--evidence", default=R.DEFAULT_EVIDENCE)
    ap.add_argument("--raw-manifest", default=R.DEFAULT_MANIFEST)
    ap.add_argument("--sweep", default=None)
    ap.add_argument("--delta", default=None, help="delta evidence db: inventory = evidence + delta; all its pairs are sampled")
    ap.add_argument("--rows", default=None)
    ap.add_argument("--cache", default="/Volumes/Untitled/GitRx/srlc/grading/cache/pairs")
    ap.add_argument("--seed", type=int, default=20261111)
    ap.add_argument("--n-b", type=int, default=200)
    ap.add_argument("--n-c", type=int, default=50)
    ap.add_argument("--skip-a", action="store_true")
    a = ap.parse_args(argv)
    os.nice(10)
    out = {}
    if not a.skip_a:
        if not a.sweep:
            ap.error("--sweep is required unless --skip-a")
        out["a"] = check_a(a.out, a.evidence, a.sweep, None, a.delta)
        print(json.dumps({"a": out["a"]}, indent=1, sort_keys=True), flush=True)
    resolve = R.manifest_resolver(a.raw_manifest)
    forced = delta_pairs(a.out, a.delta) if a.delta else ()
    out["b"] = check_b(a.out, resolve, a.n_b, a.seed, forced)
    print(json.dumps({"b": out["b"]}, indent=1, sort_keys=True), flush=True)
    if a.rows:
        out["c"] = check_c(a.out, a.rows, a.cache, a.n_c, a.seed)
        print(json.dumps({"c": out["c"]}, indent=1, sort_keys=True), flush=True)
    return out


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
