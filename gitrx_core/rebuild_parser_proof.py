"""Version-level proof that a new parser version changes nothing except where intended (read-only on inputs).

    python -m gitrx_core.rebuild_parser_proof versions --base <build dir> --out <result.json> \
        [--old-version spl-sections-0.2.3] [--evidence ...] [--delta ...] [--delta-sha256 ...] [--raw-manifest ...]

Parses EVERY inventory version with the checked-out spl_parser and compares with the stored parser output of the base
build (versions table).  The parser output hash embeds the parser_version string, so exactly that one field is
normalized back to --old-version and the hash recomputed before comparing; no other field is touched.
"""
import argparse
import collections
import hashlib
import json
import multiprocessing
import os
import sqlite3
import sys
import time

from . import rebuild as R


def _work(task):
    from . import spl_parser as P
    setid, v, sha, member, cands, old_version = task
    xml, bad, err, _ = R.load_raw(sha, member, cands)
    if bad:
        return (setid, v, "RAW:" + bad, None, None, None)
    doc = P.parse(xml)
    new_hash = doc["content_sha256"]
    doc["parser_version"] = old_version
    return (setid, v, doc["status"], P.content_hash(doc), len(doc["sections"]), new_hash)


def versions(a):
    inv = R.load_inventory(a.evidence, a.delta)
    resolve = R.manifest_resolver(a.raw_manifest)
    by_shard = collections.defaultdict(list)
    for s in sorted(inv):
        by_shard[R.shard_of(s)].append(s)
    total = sum(len(v) for v in inv.values())
    res = {"inventory_versions": total, "parser_version_new": None, "normalized_to": a.old_version, "compared": 0,
           "no_raw_rows": 0, "identical": 0, "differences": [], "changed_status": collections.Counter(),
           "new_status_counts": collections.Counter()}
    from . import spl_parser as P
    res["parser_version_new"] = P.PARSER_VERSION
    pool = multiprocessing.get_context("spawn").Pool(a.workers)
    t0 = time.time()
    try:
        for i, sh in enumerate(sorted(by_shard), 1):
            con = sqlite3.connect("file:%s?mode=ro&immutable=1" % os.path.join(a.base, "shards", sh + ".sqlite"), uri=True)
            stored = {(s, v): (st, h, n) for s, v, st, h, n in con.execute(
                "SELECT setid, version, status, parser_output_sha256, n_sections FROM versions")}
            con.close()
            recs = [(s, r) for s in by_shard[sh] for r in inv[s]]
            shas = sorted({r["sha"] for _, r in recs if r.get("sha")})
            cand = resolve(shas)
            tasks = [(s, r["version"], r["sha"], r.get("member"), cand.get(r["sha"], []), a.old_version)
                     for s, r in recs if r.get("sha")]
            res["no_raw_rows"] += sum(1 for _, r in recs if not r.get("sha"))
            seen = set()
            for s, v, st, h, n, newh in pool.imap(_work, tasks, chunksize=8):
                seen.add((s, v))
                res["compared"] += 1
                res["new_status_counts"][st] += 1
                old = stored[(s, v)]
                if (st, h, n) == old:
                    res["identical"] += 1
                else:
                    res["differences"].append({"setid": s, "version": v, "old": list(old), "new": [st, h, n],
                                               "new_unnormalized_hash": newh})
                    res["changed_status"]["%s->%s" % (old[0], st)] += 1
            miss = [k for k in stored if k not in seen and stored[k][0] != "no_raw"]
            if miss:
                res["differences"].append({"missing_from_new_run": miss[:5], "shard": sh})
            print("shard %s (%d/%d) compared=%d diffs=%d %.0fs" % (sh, i, len(by_shard), res["compared"],
                  len(res["differences"]), time.time() - t0), flush=True)
    finally:
        pool.terminate()
        pool.join()
    res["changed_status"] = dict(res["changed_status"])
    res["new_status_counts"] = dict(sorted(res["new_status_counts"].items()))
    res["differences_count"] = len(res["differences"])
    R.atomic_write(a.out, json.dumps(res, indent=1, sort_keys=True) + "\n")
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("command", choices=["versions"])
    ap.add_argument("--base", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--old-version", default="spl-sections-0.2.3")
    ap.add_argument("--evidence", default=R.DEFAULT_EVIDENCE)
    ap.add_argument("--delta", default=None)
    ap.add_argument("--raw-manifest", default=R.DEFAULT_MANIFEST)
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args(argv)
    if a.workers > 4:
        sys.exit("at most 4 workers")
    os.nice(10)
    R.check_ssd(R.SSD_ROOT)
    r = versions(a)
    print(json.dumps({k: v for k, v in r.items() if k != "differences"}, indent=1, sort_keys=True))
    print("differences:", len(r["differences"]))


if __name__ == "__main__":
    main()
