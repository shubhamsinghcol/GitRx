"""Pre-registered sealed/open split of SrLC DrugNameIDs (Amendment 1).

Population: every DrugNameID returned by the successful SrLC enumeration requests
in the crawl ledger. Draw: sorted IDs, random.Random(20261111).sample(ids, round(0.3*n)).
Writes split.json (both lists + fingerprints) and prints the fingerprints, which are
committed to docs/AUTHORITATIVE-PARSER-PLAN.md before any grading.
Fingerprint = sha256 of the IDs as ascending decimal integers joined by "\n".
"""
import argparse
import hashlib
import json
import os
import random
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import srlc  # noqa: E402

SEED = 20261111
FRACTION = 0.30


def population(root):
    con = sqlite3.connect(f"file:{os.path.join(root, 'ledger.sqlite')}?mode=ro", uri=True)
    ids = set()
    for (sha,) in con.execute("SELECT body_sha256 FROM requests WHERE kind='enum' AND ok=1"):
        for r in srlc.parse_results(srlc.read_raw(root, sha).decode("utf-8", "replace")):
            ids.add(int(r["drug_name_id"]))
    return sorted(ids)


def fingerprint(ids):
    return hashlib.sha256("\n".join(str(i) for i in sorted(ids)).encode()).hexdigest()


def draw(ids, seed=SEED, fraction=FRACTION):
    ids = sorted(ids)
    sealed = sorted(random.Random(seed).sample(ids, round(fraction * len(ids))))
    s = set(sealed)
    return sealed, [i for i in ids if i not in s]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    ids = population(a.root)
    sealed, open_ = draw(ids)
    doc = {
        "format": "gitrx-srlc-split-1", "seed": SEED, "fraction": FRACTION,
        "population_n": len(ids), "population_sha256": fingerprint(ids),
        "sealed_n": len(sealed), "sealed_sha256": fingerprint(sealed),
        "open_n": len(open_), "open_sha256": fingerprint(open_),
        "sealed": sealed, "open": open_,
    }
    if os.path.exists(a.out):
        old = json.load(open(a.out))
        if old["sealed_sha256"] != doc["sealed_sha256"]:
            sys.exit(f"refusing to overwrite {a.out}: different sealed set")
    with open(a.out + ".tmp", "w") as f:
        json.dump(doc, f, indent=1, sort_keys=True)
    os.replace(a.out + ".tmp", a.out)
    print(json.dumps({k: v for k, v in doc.items() if k not in ("sealed", "open")}, indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
