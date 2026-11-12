# Verifying GitRx (researcher audit guide)

For a reader who does not trust GitRx and wants to check its accuracy, precision, false negatives and false
positives against FDA's Drug Safety-related Labeling Changes database (SrLC), and to understand how the layer
works. Casual users do not need this. Every statement below points to a file, a number or a command. The
reasoning behind the design and the interpretation of the numbers are in
[docs/VALIDATION-REASONING.md](docs/VALIDATION-REASONING.md); the pre-registered plan, amendments, freezes
and sealed results are in [docs/AUTHORITATIVE-PARSER-PLAN.md](docs/AUTHORITATIVE-PARSER-PLAN.md).

**Read this first.**
- The evidence (raw label files, databases, SrLC pages) is in the data folder, not in the Git repository. The
  commands below use `/Volumes/Untitled/GitRx` as that folder; substitute your copy's path. The data is being
  published to Hugging Face with a Zenodo DOI; until then ask the owner for a copy.
- The hashes below are what you can compare against.
- The parser has no human validation. The earlier "59/60" and "60/60" tranche1 scores were AI-graded and are not
  evidence ([docs/VALIDATION-CORRECTION-2026-11-10.md](docs/VALIDATION-CORRECTION-2026-11-10.md)). The only
  external check is SrLC, used as a mechanical gradebook. SrLC is itself incomplete and sometimes
  inconsistent (see VALIDATION-REASONING section 6).
- The 30% sealed split was graded twice (Freeze 1, Freeze 2). Freeze 2 is "seen" for one rule. An independent
  holdout (SrLC entries posted after 2026-11-11) is being collected and is still empty.

## 1. The artifacts

All paths are under the data folder (`/Volumes/Untitled/GitRx` in the commands below). Hashes are sha256.

| Artifact | Path | What it is | sha256 |
|---|---|---|---|
| Evidence database | `evidence/evidence-v1.sqlite` | Every retained source observation (HTTP ledgers, publication dates, inventory) consolidated from 769 SQLite sources. Raw ZIPs are not inside it. | `823a5bc6c384a5f4529ec76c738ba9120b4010a7cf212462662a641ff72ac3c6` (`evidence/VERIFY.json`) |
| Raw SPL ZIPs | `backfill-20261107/network/bodies/<sha256>` and `raw-internal-20261111/sha256/<xx>/<sha256>` | The downloaded DailyMed label ZIPs, content-addressed: the file name is the sha256 of the bytes. 487,963 unique ZIPs, 0 hash mismatches at consolidation. | file name |
| Rebuild (the GitRx change records) | `rebuild/spl-sections-0.2.4__srlc-changes-0.3.2/` | 256 shard SQLite files (`shards/NN.sqlite`, each with `NN.done.json` carrying its sha256 and size), `build-config.json`, `COMPLETE.json`, `runlog.jsonl`. Contains the parsed sections, 329,005 version pairs and 1,493,091 changes of 158,955 setids, derived deterministically from the raw ZIPs by the frozen code. | `COMPLETE.json`: `8dc88e7a55831af605dbedd2a2b3bccdbd6575d51eaacb62c75c399975ce644b` (older builds, selectable with `quick --build 0.2.3-delta` / `0.2.3`: `69db2791...`, `bbe9223a...`) |
| SrLC raw pages | `srlc/raw/sha256/<xx>/<sha256>` + `srlc/ledger.sqlite` | The FDA SrLC pages exactly as fetched (3,278 detail pages, 1,548 per-drug CSVs, 136 enumeration pages); the ledger records URL, time and HTTP status per request. | file name |
| SrLC gradebook | `srlc/gradebook.sqlite` | Parsed SrLC: 3,278 drugs, 11,167 supplements, 40,562 change rows with their underlined spans. Built by `gitrx_core/srlc.py` from the raw pages. | `863aea6712ed024fb49456d4a155337bb503d12cad4bf347c3ed46e04b896db4` |
| Sealed/open split | `srlc/split.json` | 3,278 DrugNameIDs: 983 sealed, 2,295 open, seed 20261111. Fingerprints were committed before any grading. | `45725c78830a4a5a657bd92bf3f01d6d85580a3f8f4abd23d56ae82b664750a9` |
| Version index | `srlc/grading/cache/index-allversions.sqlite` | Every known (setid, version) with its raw sha, publication date and the application numbers its SPL carries (488,016 versions). | `a5739f4d8a9e484901d201311f83a64f3a6eafc734ebfb4eddb5ae54ab3243e7` |
| Grading outputs | `srlc/grading/<run>/` | `summary.json`, `rows.jsonl` (one line per SrLC change row), `fragments.jsonl` (one per underlined fragment), `gitrx_only.jsonl` (GitRx changes SrLC does not have). See section 6. | `summary.json` hashes of sealed runs are declared in the plan |
| Frozen code | `gitrx_core/{spl_parser,normalize,section_groups,changes,srlc_grade,srlc,srlc_split}.py` | The code that produced every number. | declared in the plan, "Freeze 2" (checked by the tool) |

Grading runs kept (the directory name is `<split>-<parser>-<changes>-<harness>-<group mode>`):

| Run | What |
|---|---|
| `open-spl-sections-0.2.2-srlc-changes-0.2.2-h0.3.2-{either,primary}` | Freeze 1 code on the open 70% |
| `SEALED-spl-sections-0.2.2-h0.3.2-{either,primary}` | Freeze 1 on the sealed 30%, run once per mode |
| `open-spl-sections-0.2.3-srlc-changes-0.3.2-h0.3.2-{either,primary}` | Freeze 2 code on the open 70% |
| `SEALED-spl-sections-0.2.3-srlc-changes-0.3.2-h0.3.2-{either,primary}` | Freeze 2 on the sealed 30% ("seen" for the uncoded-box rule) |
| older `open-*` dirs | development iterations, kept as a record |

Pair-digest caches (`srlc/grading/cache/pairs`, `fulltext-render`) were deleted on 2026-11-11 under the owner-approved
deletion record (they are derived and rebuildable). Anything that needs them rebuilds them from the raw ZIPs
(the full mode below does this, in a new directory).

## 2. One command

```sh
cd /path/to/GitRx           # a checkout at the frozen commit with its .venv (needs lxml)
.venv/bin/python tools/verify_gitrx.py quick                   # minutes; read-only
.venv/bin/python tools/verify_gitrx.py quick --out /some/new/dir   # also saves verify-quick.json
.venv/bin/python tools/verify_gitrx.py full --out /some/new/dir    # hours; re-runs open-split grading
```

The tool reads only. It writes nowhere except the `--out` directory you give it (which must not exist or be
empty), opens databases read-only/immutable, writes no bytecode, refuses to run if the SSD is absent, and never
grades the sealed split (the harness is only called with `--split open`; the harness itself refuses sealed
grading without a frozen-parser marker). Exit status is 0 only if no check failed; skipped checks are listed and
are not passes. `--skip-big-hashes` skips the 22 GB of hashing for a developer smoke test.

### quick mode checks

1. Frozen code: sha256 of the seven frozen modules equals the "Freeze 2" declaration, parsed from the plan
   document itself; code version strings.
2. sha256 of `evidence-v1.sqlite`, `gradebook.sqlite`, `split.json`, `index-allversions.sqlite`, rebuild
   `COMPLETE.json`.
3. Split: population, sealed and open fingerprints recomputed from `split.json` and compared with the values
   fixed in the plan; sealed and open are disjoint; and the draw itself is repeated from the SrLC enumeration
   pages (`random.Random(20261111).sample`) and must give the same 983 IDs.
4. All 256 rebuild shards re-hashed against their sidecars; sidecar totals against `COMPLETE.json`.
5. N (default 20) seeded random change records re-derived from the raw ZIPs: re-hash the ZIP, parse with the
   frozen parser, compare with `changes.compare_docs`, and match `record_sha256` stored in the shard.
6. One seeded random SrLC fragment traced end to end (section 4).
7. For every kept grading run (open and sealed, both freezes, both group modes): the headline metrics
   (section recall, text recall in three matching variants, parser accuracy, not-locatable counts, by group) are
   recomputed from `rows.jsonl`/`fragments.jsonl`/`gitrx_only.jsonl` and must equal `summary.json` exactly; the
   sealed `summary.json` sha256 values must equal those declared in the plan; crude precision is recomputed from
   `summary.json` + `gitrx_only.jsonl`.
8. Interface agreement: the MCP server is started with the default configuration and its answers to the same
   queries must equal the command line's.

### full mode

Re-runs `gitrx_core.srlc_grade grade --split open` for both group modes into the new `--out`, with the pair
digests rebuilt from raw ZIPs into `--out/cache` (no cache is reused); diffs each `summary.json` against the kept
one (volatile keys `generated_at`, `runtime_seconds`, `digest_build` ignored); compares the three jsonl files
order-insensitively; recomputes the metrics again from the fresh jsonl; recomputes precision
(section 8) and compares it with the published numbers. `--rebuild-pilot` additionally builds a seeded 0.2% pilot
of the rebuild twice and compares the shards byte for byte. Expect hours.

### Output of `quick` on the day this guide was written

See section 10 at the end of this file.

## 3. Check any single GitRx change by hand

A GitRx "change record" is the comparison of two consecutive captured versions of one DailyMed setid. To
verify one without the tool:

1. Pick a pair: `shards/<first two hex digits of the setid>.sqlite`, table `pairs` (columns include
   `from_version`, `to_version`, `from_raw_sha256`, `to_raw_sha256`, `record_sha256`, `n_changes`); `changes`
   lists each changed section with its SrLC group, kind and span counts; `rebuild.full_record()` reassembles
   the whole record including the verbatim added/removed text spans.
2. Find the raw ZIPs: `versions.raw_sha256` of both versions; the ZIP is the file with that name in
   `backfill-20261107/network/bodies/` or `raw-internal-20261111/sha256/<xx>/`. `shasum -a 256` of the file
   must equal its name.
3. Re-parse with the frozen parser and compare with the frozen comparer. This script does exactly that for one
   concrete pair (a Kitabis Pak label, setid `da1e5afd-...`, versions 8 and 9, which the quick tool traced from
   an SrLC fragment); run it with the repository's `.venv/bin/python -I -B`:

```python
import hashlib, os, sqlite3, sys
sys.path.insert(0, "/path/to/GitRx")          # the repo checkout (frozen modules; needs lxml)
from gitrx_core import changes, rebuild, spl_parser

SSD = "/Volumes/Untitled/GitRx"
SETID, VA, VB = "da1e5afd-d707-4af1-8935-8195ba6d769f", 8, 9   # any pair from the rebuild
REB = SSD + "/rebuild/spl-sections-0.2.4__srlc-changes-0.3.2"

def raw_zip(sha):                                              # raw ZIPs are stored under their own sha256
    for p in (SSD + "/backfill-20261107/network/bodies/" + sha,
              SSD + "/raw-internal-20261111/sha256/%s/%s" % (sha[:2], sha)):
        if os.path.exists(p):
            return p
    raise SystemExit("raw ZIP %s not found" % sha)

con = sqlite3.connect("file:%s/shards/%s.sqlite?mode=ro&immutable=1" % (REB, SETID[:2]), uri=True)
stored = con.execute("SELECT record_sha256, n_changes FROM pairs WHERE setid=? AND from_version=? AND to_version=?",
                     (SETID, VA, VB)).fetchone()
docs, shas = [], []
for v in (VA, VB):
    sha, member = con.execute("SELECT raw_sha256, xml_member FROM versions WHERE setid=? AND version=?", (SETID, v)).fetchone()
    data = open(raw_zip(sha), "rb").read()
    assert hashlib.sha256(data).hexdigest() == sha, "raw ZIP does not match its name"
    docs.append(spl_parser.parse(rebuild.read_xml_bytes(data, member)))
    shas.append(sha)
rec = changes.compare_docs(docs[0], docs[1], shas[0], shas[1])
print("stored   ", stored[0], "n_changes", stored[1])
print("re-derived", rec["record_sha256"], "n_changes", len(rec["changes"]))
assert rec["record_sha256"] == stored[0]
```

   Output when this guide was written: both hashes `9be47f3a0a157696415fa2105c068316d93c74cb6391377e5f876d50a17bf86a`,
   8 changes, the group-WP change being `5.2 Ototoxicity`.
4. Read the section text itself with your own eyes against the label: the shard's `sections` table gives
   `text_h` for each section; `rebuild.section_text(con, h)` returns the text. The original label XML is inside
   the ZIP (`unzip -p <zip> '*.xml'`). The parser's contract is text conservation (nothing dropped or invented),
   enforced by build-failing invariants in `gitrx_core/spl_parser.py`.

To check the comparison against SrLC for the same pair: find the SrLC entry (section 4), or search
`gradebook.sqlite` for the drug and the approval date.

## 4. Trace an SrLC row to the GitRx change

This is what step 6 of the quick tool prints for a seeded random fragment. The chain, each link checkable:

1. **SrLC raw page**: `gradebook.sqlite` table `changes` has `source_sha256`; the page is
   `srlc/raw/sha256/<first two>/<sha256>` and hashes to its name; `srlc/ledger.sqlite` table `requests`
   (`body_sha256`) gives the URL, time and HTTP status of the fetch. The underlined spans in
   `underlined_spans_json` are text that appears in that page.
2. **Gradebook row** to **candidate setids**: the drug's application (e.g. `NDA205433`) is matched to DailyMed
   setids whose SPL approval list carries it (`index-allversions.sqlite`, table `approvals`).
3. **Version pair**: the consecutive captured versions whose to-version publication date lies in the grading
   window (-30 d to +365 d around the SrLC approval date, Amendment 1) for the end-to-end metric, or the
   nearest absent-to-present transition within -730 d to +365 d for the parser-accuracy metric (Amendment 3).
   `fragments.jsonl` records the pair under `parser` (`setid`, `from_version`, `to_version`, `delta_days`).
4. **Raw ZIP sha** for both versions (`index-allversions.sqlite`, table `versions`), re-hashed.
5. **GitRx change span**: re-parse both, find the fragment inside a changed same-group section's added tokens
   (`srlc_grade.DigestView.fragment_status`), and check that the shard stores the same `record_sha256`.

## 5. Understanding what the harness measures (so you can pick the number you care about)

- **Parser accuracy** (the pre-registered pass metric after Amendment 3): of SrLC fragments that can be located
  in captured history, the share GitRx reports as added text of a changed section of the same SrLC group.
  Not-locatable fragments are excluded and reported (coverage).
- **End-to-end recall** (Amendment 1 window): same, but also requires a captured version to fall in the window.
  Lower because of timing and missing history, not parser behavior (VALIDATION-REASONING section 3).
- **Precision**: the share of GitRx changes in SrLC groups that SrLC also lists (section 8).
- Matching variants: `strict` (literal NFC, case-sensitive tokens), `ws` (also whitespace-free), `render` (also
  casefold, format characters removed, list bullets stripped, SrLC "?" matches one non-ASCII character).
  `either` accepts the group or the alternative group for non-PLR subsections under WARNINGS/PRECAUTIONS;
  `primary` accepts the primary group only.

## 6. Inspect every miss and every GitRx-only change

All grading outputs are plain jsonl (one JSON object per line); no tool is needed. Examples (`jq` or Python):

```sh
G=/Volumes/Untitled/GitRx/srlc/grading/SEALED-spl-sections-0.2.3-srlc-changes-0.3.2-h0.3.2-either
# every located-but-missed parser fragment, with the descriptive label and the GitRx pair
jq -c 'select(.class=="main" and .parser.located and (.parser.found_render|not))
       | {drug_name_id,supplement_id,change_id,group,section_number,section_title,fragment,
          label:.parser.miss_label,setid:.parser.setid,from:.parser.from_version,to:.parser.to_version}' $G/fragments.jsonl
# every end-to-end miss and why (text), or section row miss and why
jq -c 'select(.class=="main" and .e2e and (.found|not)) | {drug_name_id,group,fragment,label:.miss_label,detail:.miss_detail}' $G/fragments.jsonl
jq -c 'select(.status=="graded" and (.section_found|not)) | {drug_name,group,approval_date,label:.miss_label}' $G/rows.jsonl
# every GitRx change in an SrLC group that no SrLC entry matched
jq -c 'select(.category=="gitrx_only_srlc_group")' $G/gitrx_only.jsonl   # (open dir for the analysis in section 8)
```

Field meanings: `fragments.jsonl` - `change_id`/`supplement_id`/`drug_name_id` index the gradebook;
`fragment` is the normalized SrLC underlined text (split at the SrLC ellipsis); `class` is `main` (>= 3 tokens,
in the bar), `short` or `table_referring` (reported separately); `found`/`found_strict`/`found_ws` are the
end-to-end results; `parser` holds the located pair and `found_render`/`found_strict`/`found_ws`, the
`any_group_*` variants and, for misses, `miss_label`. `rows.jsonl` - one SrLC (drug, supplement, group) row with
per-candidate pair lists, `section_found`, `miss_label`, `parser_section_found`. `gitrx_only.jsonl` - every
change in a graded pair that no SrLC row matched, with `category` `gitrx_only_srlc_group` (a group SrLC
covers; this is what precision scores) or `gitrx_only_non_srlc_section` (a section SrLC does not cover: not
scored).

To see why a particular miss is a miss, take its (setid, from, to) and follow section 3. The sealed BW and C
misses were all inspected this way; the result is in VALIDATION-REASONING section 4. Sealed misses are visible
in the kept sealed outputs because Freeze 1 and Freeze 2 have already been run; do not treat them as an
unseen test.

## 7. Recomputing summary metrics

`tools/verify_gitrx.py quick` does this for all eight kept runs. The recomputation code is
`recompute_metrics` in that file (about 40 lines); it uses `srlc_grade.rate` (Wilson 95%).

## 8. Precision: definition and recomputation

Definition, unit, denominator, matching rule and timing window are in the module docstring of
[`gitrx_core/srlc_precision.py`](gitrx_core/srlc_precision.py) and repeated in VALIDATION-REASONING section 5.
In one line: crude precision = SrLC-group changes in graded pairs of open-split entries that an SrLC row of the
same group also lists, divided by all such changes; GitRx-only changes are not automatically errors.

From the kept jsonl alone (no rebuild; this is in quick mode): matched count from `summary.json`, unmatched from
`gitrx_only.jsonl`: Freeze 2 either 63,105 matched + 16,020 unmatched = 79.75%; Freeze 1 either 67,260 + 19,394 =
77.62%.

Full breakdown (per group, timing-robust, formatting share, reference_only counted/excluded) needs the pair
digests, which have to be rebuilt from raw ZIPs (about 30-60 minutes with 3 workers, 10,272 pairs):

```sh
cd /path/to/GitRx
nice -n 10 .venv/bin/python -m gitrx_core.srlc_precision \
  --grading-dir /Volumes/Untitled/GitRx/srlc/grading/open-spl-sections-0.2.3-srlc-changes-0.3.2-h0.3.2-either \
  --out /some/new/dir        # writes precision.json, records.jsonl (every scored unit), report.txt, cache/
```

It cross-checks that its unmatched units are identical to the kept `gitrx_only.jsonl`. It refuses sealed runs
and sealed IDs. For Freeze 1 the digests depend on the Freeze 1 parser and comparer, which are no longer the
installed code: extract the frozen commit and run the module from there
(`git archive a00c5d27c8cc95cdf0f4216da198281ca9984ae0 gitrx_core | tar -x -C /tmp/f1`; copy
`gitrx_core/srlc_precision.py` into `/tmp/f1/gitrx_core/`; run it with that directory as the working directory
against `open-spl-sections-0.2.2-srlc-changes-0.2.2-h0.3.2-either`). `reference_only` does not exist in that
version and is reported as unavailable.

## 9. Known caveats for an auditor

- The rebuild's `build-config.json` records its raw manifest at a scratch path (`.../scratchpad/phase1/raw-manifest.sqlite`)
  that is not durable; a copy is kept at `session-artifacts-20261111/scratchpad/phase1/raw-manifest.sqlite`. The
  tool does not need it (it resolves ZIPs by sha256 from the SSD stores).
- The development scripts that first produced the precision numbers are kept in
  `session-artifacts-20261111/scratchpad/{fx/prec3,bw-precision}/` (the "p2 scripts"); `srlc_precision.py` is their
  maintained, tested form.
- Sealed grading outputs are in the kept directories and were read to produce this kit's recomputations; the
  sealed set is no longer an unseen test.
- If any hash in section 1 does not match, stop: the artifact is not the one the numbers describe.

## 10. Output of `tools/verify_gitrx.py quick` (2026-11-11, default settings, exit status 0)

73 checks passed, 0 failed, 0 skipped, in 46 s on this machine (hashing 22 GB took 28 s here; a slower disk or a
cold cache will take longer).

```
== 1. frozen code and versions
[PASS   ] Freeze 2 declaration parsed from plan -- 7 module hashes
[PASS   ] frozen code spl_parser.py -- 2bffee02825e
[PASS   ] frozen code normalize.py -- 4a613040e6f1
[PASS   ] frozen code section_groups.py -- f1a2684d4d1a
[PASS   ] frozen code changes.py -- 9b0bc06db5ad
[PASS   ] frozen code srlc_grade.py -- 26c40e528240
[PASS   ] frozen code srlc.py -- 99702e2aee1d
[PASS   ] frozen code srlc_split.py -- b8509beb7189
[PASS   ] code versions -- spl-sections-0.2.3 / srlc-groups-0.3.0 / srlc-changes-0.3.2 / srlc-grade-0.3.2
== 2. artifact hashes
[PASS   ] sha256 evidence-v1.sqlite -- 823a5bc6c384 (12s)
[PASS   ] sha256 gradebook.sqlite -- 863aea6712ed (0s)
[PASS   ] sha256 split.json -- 45725c78830a (0s)
[PASS   ] sha256 index-allversions.sqlite -- a5739f4d8a9e (0s)
[PASS   ] sha256 rebuild COMPLETE.json -- bbe9223a91eb (0s)
== 3. sealed split fingerprints
[PASS   ] split population_sha256 -- recomputed b065c433be0b393d
[PASS   ] split sealed_sha256 -- recomputed 354025929110b2c5
[PASS   ] split open_sha256 -- recomputed f494923ef173088e
[PASS   ] split population_n -- recomputed 3278
[PASS   ] split sealed_n -- recomputed 983
[PASS   ] split open_n -- recomputed 2295
[PASS   ] split seed -- recomputed 20261111
[PASS   ] split lists disjoint
[PASS   ] split.json internal fingerprints equal recomputed
[PASS   ] split re-draw from SrLC enumeration pages (seed 20261111) -- population 3278, sealed 983
== 4. rebuild shards
[PASS   ] rebuild COMPLETE.json lists 256 shards -- 256
[PASS   ] rebuild shards vs sidecars (sha256, bytes) -- 256 shards, bad: none (16s)
[PASS   ] rebuild sidecar totals equal COMPLETE.json counts -- setids 158955 pairs 328985 changes 1493012
[PASS   ] COMPLETE.json frozen_code_sha256 equals this repo's frozen modules
== 5. re-derive 20 seeded change records from raw ZIPs
[INFO   ] record sample -- 328985 pairs with records in rebuild; seed 20261111; sampled 20
[PASS   ] re-derive b4dfe70f v20240402->v20241007 -- 2 changes, record_sha256 a1d59da6a43a4b33
[PASS   ] re-derive 713db2c6 v31->v32 -- 2 changes, record_sha256 deb1bc60469665e7
[PASS   ] re-derive 9121804a v3->v4 -- 0 changes, record_sha256 156feb27f12b6f91
[PASS   ] re-derive 4139b5bf v2->v3 -- 2 changes, record_sha256 ace0e65c4d8caef6
[PASS   ] re-derive ba378074 v2->v3 -- 0 changes, record_sha256 2bcf3bae123a167e
[PASS   ] re-derive 84b31246 v1->v2 -- 0 changes, record_sha256 e555825149233403
[PASS   ] re-derive ee06186f v8->v9 -- 20 changes, record_sha256 1fe65c2364e3751b
[PASS   ] re-derive 2e6f96d0 v3->v4 -- 0 changes, record_sha256 3488a066bf30923c
[PASS   ] re-derive 4b81abf9 v2->v3 -- 1 changes, record_sha256 963f905481dbaec3
[PASS   ] re-derive 1e998229 v2->v4 -- 11 changes, record_sha256 81e8609f2d57afe5
[PASS   ] re-derive 11f1ad4c v2->v3 -- 0 changes, record_sha256 c8b56fd561012037
[PASS   ] re-derive c0d705bf v4->v5 -- 64 changes, record_sha256 129deb676613963d
[PASS   ] re-derive c2391373 v9->v11 -- 3 changes, record_sha256 84f021eb8c1426ca
[PASS   ] re-derive 69308693 v7->v8 -- 1 changes, record_sha256 b254c6ed45537c5f
[PASS   ] re-derive 3b1fbd1c v10->v13 -- 0 changes, record_sha256 1cd93302a150d4f6
[PASS   ] re-derive 7a2bec20 v9->v10 -- 0 changes, record_sha256 2e7d6a2a6a9e6887
[PASS   ] re-derive 985a5028 v1->v2 -- 2 changes, record_sha256 6c844565f3c99211
[PASS   ] re-derive 31512ece v1->v2 -- 4 changes, record_sha256 8e1de642d50d0798
[PASS   ] re-derive f972a8f1 v1->v2 -- 0 changes, record_sha256 29d88069f83abdd8
[PASS   ] re-derive 7335a293 v2->v3 -- 2 changes, record_sha256 978384296ed4dc14
[PASS   ] re-derived records identical -- 20 of 20
== 6. trace one SrLC fragment end to end (open split)
[INFO   ] trace: candidate fragments -- 38432 open-split located+found; picked change_id 32651 (seed 20261111)
  SrLC entry: KITABIS PAK (NDA205433) SUPPL-7 approved 2023-02-10, group WP section '5.2' 'Ototoxicity'
  fragment: 'Cases of ototoxicity with aminoglycosides have been observed in patients with certain variants in the mitochondrially encoded 12S rRNA gene (MT-RNR1), particularly the m.1555A>G variant. Ototoxicity o'
[PASS   ] trace 1: SrLC raw page sha256 equals its name -- b5c749a81aba6e4f /Volumes/Untitled/GitRx/srlc/raw/sha256/b5/b5c749a81aba6e4fb75ef9be24d0f40ee54f4c881e4d399311053e436447bdd2
[PASS   ] trace 1: gradebook drug.detail_sha256 == change.source_sha256 == supplement.source_sha256
  fetched: https://www.accessdata.fda.gov/scripts/cder/safetylabelingchanges/index.cfm?event=searchdetail.page&DrugNameID=2134 at 2026-11-11T02:55:34Z (HTTP 200)
[PASS   ] trace 1: an underlined span of the gradebook row occurs in the raw page and contains the fragment -- 2 of 2 spans found in page text
[PASS   ] trace 2: candidate setid carries the SrLC application number NDA205433 -- da1e5afd-d707-4af1-8935-8195ba6d769f
  GitRx pair: setid da1e5afd-d707-4af1-8935-8195ba6d769f v8 (2021-07-26) -> v9 (2023-04-14)
[PASS   ] trace 3: raw ZIP v8 re-hashed -- sha256 0053bb716c7810b5
[PASS   ] trace 3: raw ZIP v9 re-hashed -- sha256 7ce811df44dbeb23
[PASS   ] trace 4: frozen parser + compare_docs re-derivation: fragment inside a changed same-group section's added tokens -- pair record_sha256 9be47f3a0a157696
  GitRx change: modified WP '5.2' '5.2 Ototoxicity' (alt None)
[PASS   ] trace 5: rebuild shard stores the same record_sha256 -- shard 9be47f3a0a157696
== 7. kept grading outputs: declared summary hashes, metrics and precision recomputed from jsonl
[PASS   ] open-spl-sections-0.2.2-srlc-changes-0.2.2-h0.3.2-either: summary metrics recomputed from jsonl -- section recall 94.56%, text(render) 77.94%, parser text 98.44% (38417/39027), parser section 99.70% (9415/9443)
[PASS   ] open-spl-sections-0.2.2-srlc-changes-0.2.2-h0.3.2-either: precision from kept summary + gitrx_only.jsonl -- matched 67260, unmatched SrLC-group 19394, non-SrLC-section 86103 -> crude precision 77.62% (published 77.62%)
[PASS   ] open-spl-sections-0.2.2-srlc-changes-0.2.2-h0.3.2-primary: summary metrics recomputed from jsonl -- section recall 93.78%, text(render) 77.50%, parser text 97.83% (38179/39027), parser section 98.70% (9320/9443)
[PASS   ] open-spl-sections-0.2.2-srlc-changes-0.2.2-h0.3.2-primary: precision from kept summary + gitrx_only.jsonl -- matched 67123, unmatched SrLC-group 19531, non-SrLC-section 86103 -> crude precision 77.46%
[PASS   ] open-spl-sections-0.2.3-srlc-changes-0.3.2-h0.3.2-either: summary metrics recomputed from jsonl -- section recall 94.32%, text(render) 77.92%, parser text 98.48% (38432/39027), parser section 99.74% (9418/9443)
[PASS   ] open-spl-sections-0.2.3-srlc-changes-0.3.2-h0.3.2-either: precision from kept summary + gitrx_only.jsonl -- matched 63105, unmatched SrLC-group 16020, non-SrLC-section 76567 -> crude precision 79.75% (published 79.75%)
[PASS   ] open-spl-sections-0.2.3-srlc-changes-0.3.2-h0.3.2-primary: summary metrics recomputed from jsonl -- section recall 93.54%, text(render) 77.48%, parser text 97.87% (38194/39027), parser section 98.73% (9323/9443)
[PASS   ] open-spl-sections-0.2.3-srlc-changes-0.3.2-h0.3.2-primary: precision from kept summary + gitrx_only.jsonl -- matched 62993, unmatched SrLC-group 16132, non-SrLC-section 76567 -> crude precision 79.61%
[PASS   ] SEALED-spl-sections-0.2.2-h0.3.2-either: summary.json sha256 equals the value declared in the plan
[PASS   ] SEALED-spl-sections-0.2.2-h0.3.2-either: summary metrics recomputed from jsonl -- section recall 94.04%, text(render) 78.60%, parser text 97.62% (17180/17599), parser section 99.08% (4102/4140)
[PASS   ] SEALED-spl-sections-0.2.2-h0.3.2-primary: summary.json sha256 equals the value declared in the plan
[PASS   ] SEALED-spl-sections-0.2.2-h0.3.2-primary: summary metrics recomputed from jsonl -- section recall 93.15%, text(render) 77.77%, parser text 96.48% (16979/17599), parser section 97.92% (4054/4140)
[PASS   ] SEALED-spl-sections-0.2.3-srlc-changes-0.3.2-h0.3.2-either: summary.json sha256 equals the value declared in the plan
[PASS   ] SEALED-spl-sections-0.2.3-srlc-changes-0.3.2-h0.3.2-either: summary metrics recomputed from jsonl -- section recall 94.00%, text(render) 79.05%, parser text 98.30% (17299/17599), parser section 99.64% (4125/4140)
[PASS   ] SEALED-spl-sections-0.2.3-srlc-changes-0.3.2-h0.3.2-primary: summary.json sha256 equals the value declared in the plan
[PASS   ] SEALED-spl-sections-0.2.3-srlc-changes-0.3.2-h0.3.2-primary: summary metrics recomputed from jsonl -- section recall 93.11%, text(render) 78.22%, parser text 97.15% (17098/17599), parser section 98.48% (4077/4140)

SUMMARY {'pass': 73, 'fail': 0, 'skipped': 0, 'info': 2}  (46s)
```
