# Step F: gap repair (measurement, plan, cheap probe)

Written 2026-11-11. Owner-approved scope: unavailable versions, NALTREXONE history,
families whose captured history starts after version 1. Sections 1-5 are the collection-only
measurement and probe: nothing was parsed, rebuilt, promoted or deleted, and `evidence-v1.sqlite`
(sha256 823a5bc6...ac3c6) and the rebuild shards were opened read-only. Section 6 records the follow-up
the owner approved the same day (delta admission of the 26 held bodies and a partial rebuild); section 7
records the 1,000-request tightening probe that replaced the full recovery run. Unrecoverable items remain
explicitly unknown.

Code: `gitrx_core/gap_repair.py` (tests `tests/test_core_gap_repair.py`, mocked HTTP; all
`tests/test_core_*.py` pass, 283). Observations: `/Volumes/Untitled/GitRx/updates/gap-repair/gap-repair.sqlite`
(new, append-only triggers); ledger and write-once sha256-named bodies under
`updates/gap-repair/network/`. HTTP is the maintained `gitrx_backend.jobs.HTTP` (nlm.nih.gov only,
>=2 s spacing, single lane), guarded by `update_common.preflight` (SSD identity, STOP,
HEAVY-JOB.lock, backoff), `nice -n 10`.

## 1. Measurements (no network)

Source: rebuild `spl-sections-0.2.3__srlc-changes-0.3.2` shards (158,955 setids, 488,016 versions,
328,985 pairs) plus the evidence history inventory (`history_entries`, 158,937 families).

### 1a. The 82 `no_raw` versions
- 1 is the synthetic test fixture family `a0000000-0000-0000-0000-000000000000` v5 (in evidence as
  "Synthetic a"); it is excluded from every request. 81 real versions in 76 setids.
- 55 are in the 69 frozen "listing-only" targets (version appears in the current listing only, not in any
  history); 26 are in the history inventory.
- Prior failures (evidence `http_requests`): 55 were HTTP 404 on `getFile.cfm?type=zip&setid&version`
  (36 once, 19 twice); the other 26 returned HTTP 200 with a body (see section 3, they are not source
  failures). Bulk-file recovery on 2026-11-03 had already filled 14 other listing-only targets plus
  NALTREXONE v1 (`reports/recovery-attempt-20261103`).

### 1b. NALTREXONE
Setid `6de5637f-d527-4228-af1a-3aae71155cb5`, "NALTREXONE KIT [AMNEAL PHARMACEUTICALS NY LLC]". Known: v1
only (downloaded, complete, effective 20260130, recovered by bulk file). The current listing says
`spl_version` 1, published May 20, 2026. Its history endpoint returns HTTP 200 with a blank setid and zero
rows on every request (7 history captures Nov 2-6, including the 2026-11-06 recheck), the versionless/version-1
getFile returned 404, and an archive search had no ZIP links. Nothing else is known. The listing's
version 1 is consistent with a family that has one version, but that is not proof; a complete history
stays **unknown**. No further request was made (the failure is server-side and has been reproduced).
Other naltrexone-named setids (about 57 in evidence) are separate families; family names select setids and
are not merged.

### 1c. Families whose captured history starts after version 1, and internal gaps
Versions above 200 (1,546 versions in 252 families, e.g. 20191111, 1111777777) are source version-number
anomalies and are excluded from gap arithmetic; 695 of the 37,563 non-consecutive pairs involve them.

| Measure | Count |
|---|---|
| Families with earliest captured version > 1 | 31,368 |
| ... of which start exactly at 100 | 2,899 (287,001 implied-missing integers) |
| ... others (start at 2..) | 28,469 (81,093 implied-missing integers) |
| Implied-missing integers below the earliest version | 368,094 |
| Non-consecutive pairs (all) / with plausible numbers | 37,563 / 36,868 |
| Families with internal gaps | 21,015 |
| Implied-missing integers inside gaps | 61,188 (equals the sum of `gap_versions` on plausible pairs) |
| Implied-missing integers that the history inventory lists | **0** |

Gap size is mostly 1 (27,811 pairs), then 2 (5,358), 3 (1,735).

### Why the gaps exist
- Of 429,282 implied-missing integers, none is listed by DailyMed's own per-setid history (checked for
  every one). The history inventory equals the captured set except for 69 families where the current
  listing adds a newer version, and those are the listing-only targets above. So a gap is a number that
  DailyMed's history never claims, not a version we failed to download. The history inventory is the
  best available source of what DailyMed publishes; it does not prove a version never existed.
- SPL version numbers are chosen by the labeler/FDA submission, increase monotonically but may skip, and
  some labelers start high (2,899 families start at 100; typical of repackager or system-generated
  numbering). DailyMed also keeps only versions it received/retains. Skipped or unretained numbers
  cannot be recovered from any DailyMed route; this is a hypothesis, supported by section 3.
- The 26 "unavailable" versions that had HTTP 200 earlier are an admission gap in our pipeline, not
  a source gap (section 3).

### Which DailyMed sources could recover what
| Route | Covers | Already tried / status |
|---|---|---|
| `getFile.cfm?type=zip&setid&version` | any version DailyMed still serves | the main route (488,259 successes); 404 for non-served versions; probed here |
| `services/v2/spls/{setid}/history.json` | inventory only | complete for 158,937 families; NALTREXONE returns an empty envelope |
| archive index `archives/index.cfm` and `getArchivalFile.cfm?archive_id` | older archived versions | 780 archive-id fetches in evidence; no target-version admission for the 14 recovery targets |
| monthly update zips (`dm_spl_monthly_update_*`) | versions released in the past ~12 months | recovered NALTREXONE v1 and 14 targets; cannot cover older versions |
| full release zips (`dm_spl_release_human_rx_part1-6`, `remainder`) | current (latest) version of each setid only | useful only for a listing-only target whose latest is missing |
| openFDA label API, drugInfo pages, NLM FTP | metadata only / no SPL history | tried 2026-11-03, no new versions |

Not repeated here: history, archival and versionless-latest requests (see
`reports/recovery-attempt-20261103/ALREADY-ATTEMPTED.md`).

## 2. Plan and request estimates

Cap 1,500 requests (112 already spent today by the weekly DailyMed update job, counted toward the cap; the
earlier crashed Step F attempt left no ledger rows anywhere, none found in any ledger, so nothing else).
1. Retry the 81 real unavailable versions once each: 81 requests.
2. 20 control requests for versions already held (proves the request form returns 200 and tells 404
   from a broken request): 20.
3. Seeded stratified sample of 200 implied-missing versions (seed 20261111; `random.Random("seed:stratum")`):
   70 below-v1-other, 60 below-v1 from families starting at 100, 70 internal gap: 200.
Planned 301; actual 302 (one extra request: the first run stopped itself after 5 consecutive 404s because of a
bug in the stop rule, fixed so only non-404 failures count; the 11 completed attempts were not repeated,
the aborted-run log and an erroneous 24 h backoff file renamed
`BACKOFF-UNTIL.erroneous-404-stop-20261111` remain). Total today 414 of 1,500.

## 3. Results

Single lane, 2.2 s mean spacing, 11 minutes total, 12.4 MB, no 403/429.

| Probe | n | Recovered | Rate (Wilson 95%) |
|---|---|---|---|
| Control: held versions | 20 | 20 (all byte-identical to held) | 100% (83.9-100%) |
| Retry of 81 unavailable | 81 | 26 bodies, 55 HTTP 404 | 32.1% (22.9-42.9%) |
| Sample below v1, families starting >1 (not 100) | 70 | 0 (70 x 404) | 0% (0-5.2%) |
| Sample below v1, families starting at 100 | 60 | 0 (60 x 404) | 0% (0-6.0%) |
| Sample internal gaps | 70 | 0 (70 x 404) | 0% (0-5.2%) |
| All 200 missing-version samples | 200 | 0 | 0% (0-1.9%) |

Interpretation:
- The sample gives no sign that any implied-missing integer is downloadable. With 0/200 the upper 95% bound
  is 1.9% pooled, so up to about 8,100 of 429,282 could still exist; the point estimate is none. Per-stratum
  bounds are 5-6%, so strata are not individually conclusive. Stratum rates must be reweighted by pool size
  (below-other 81,093; start-100 287,001; internal 61,187) for a pooled estimate; with zero hits every
  reweighting gives 0 with the bounds above.
- The 26 "recovered" versions are not new bytes from the source: each is byte-identical (sha256 equal) to a
  body ledgered on 2026-11-09 by the backfill (`backfill-20261107/network/bodies/`, all 26 present) and each
  ZIP's XML setId/versionNumber match the target. They were `pending` in evidence-v1 (HTTP 200 seen but
  not selected), so the fix is admission of existing bytes into a delta, not a source recovery. The 55
  true-404 targets (all listing-only) stay unknown.
- Everything else stays explicitly unknown: version numbers absent from DailyMed's history have no
  date, no bytes and no evidence they exist.

## 4. Estimate for a full recovery run (not run)

Target: every implied-missing integer, 429,282 (368,094 below + 61,188 internal;), one GET each.
- Requests: about 429,300 (plus about 1% for retries); the 1,500-request cap would not allow it.
- Time: at the observed 2.2 s/request about 262 hours (about 11 days continuous at one lane); at the 2 s
  minimum about 10 days. Parallel lanes are possible for the backfill but not authorized here.
- Bytes: a 404 body is 39 bytes (about 17 MB total plus ledger overhead, about 150 MB of sqlite). If recovery
  were at the 1.9% upper bound: about 8,100 ZIPs x 377 KB mean (evidence mean for getFile bodies) is about 3 GB.
- Expected yield from the sample: zero. A full run is not recommended. A cheaper bound-tightening probe:
  1,000 more sampled requests (about 37 min) would narrow the pooled upper bound to about 0.4% if all 404.
  Higher-value targets: the 55 listing-only 404s (already tried twice; re-check once per quarter at most) and, if
  the owner wants, a single re-check of the NALTREXONE history after a month.

## 5. What a delta rebuild needs (done in section 6)
1. Admit the 26 byte-identical Nov 9 bodies as new version rows (state downloaded) in a new delta
   evidence database; their sha256 are in `gap-repair.sqlite` (`attempts` where `outcome='recovered_new'`)
   and in `backfill-20261107/network/bodies/`. Evidence-v1 stays unchanged.
2. Raw store: place those ZIPs in the SSD content-addressed raw tree used by the rebuild raw manifest.
3. Rebuild only the 21 affected setids (26 new `versions` rows with the frozen `spl-sections-0.2.3`
   parser), then recompute their `pairs` (3 of the 26 sit between held versions; the other 23 lie at the edge of
   their family's captured range, so they add pairs rather than changing existing ones) with `srlc-changes-0.3.2`, writing a new pinned release; derived names/index
   shard updates, not a full rebuild.
4. Keep the 55 listing-only 404s as `unavailable` with attempt count and dates (evidence plus today's 55
   additional 404s); keep the implied-missing integers as "not in DailyMed history", never as versions.
5. The weekly update job's deltas (`updates/dailymed`) are a separate input and should be merged in the
   same delta build if the owner wants currency; no promotion happens automatically.

## 6. Delta admission and partial rebuild (done 2026-11-11, owner-approved)

Nothing was downloaded for this step. `evidence-v1.sqlite` (sha256 823a5bc6...ac3c6, re-hashed after the build:
unchanged) and the Step E rebuild (COMPLETE sha256 bbe9223a...a6869c, re-hashed: unchanged) were only read.

**Delta evidence.** `/Volumes/Untitled/GitRx/evidence/evidence-delta-20261111.sqlite` (sha256
`b1d65eaf6497e7428194593b1d6e23abc4c3b628c9a027a1f382635cebf454b0`; hash and per-row re-derivation in
`evidence/VERIFY-delta-20261111.json`) holds 26 `admissions` rows in 21 setids. Each row records setid, version,
raw sha256, bytes, XML member, effective date (XML `effectiveTime`, the same convention as evidence-v1), the body path
under `backfill-20261107/network/bodies/`, the evidence-v1 `pending` version row id and history publication date, every
evidence-v1 `http_requests` row with that sha256 (id, url, time, status), the backfill ledger request where one exists
(2 rows), and the gap-repair re-fetch (attempt id, run id, ledger request id, time, equal sha256, XML identity
check). Reason: "byte-identical body ledgered but not admitted". Triggers refuse UPDATE/DELETE. The ledgered
requests are dated 2026-11-08 to 2026-11-09 (UTC); the re-fetches 2026-11-11 23:14-23:16 UTC. Code: `gitrx_core/rebuild_delta.py`
(`create`, `verify`, `prove`); inventory source: `rebuild.load_inventory(evidence, delta_path)` (default `None` keeps the
old behaviour).

**Partial rebuild.** `/Volumes/Untitled/GitRx/rebuild/spl-sections-0.2.3__srlc-changes-0.3.2__delta-20261111/`
(COMPLETE sha256 `69db2791bad3ca2dca4d211bc738603700fe3b5d7dda49da71f85988e78c4c2a`; names index
`153661d511bf747d1252699d1b50b3295afd2bc34d99046e258df6314143d083`, families table identical to the old one, only its
`complete_sha256` meta differs). Built with the frozen modules (all four sha256 unchanged, check passed) through
`gitrx_core.rebuild --delta ... --reuse-from <old build>`; `rebuild.py` gained backwards-compatible `delta_path`,
`reuse_from` (copy shards without a delta setid, sha256-verified against the old COMPLETE, sidecar kept verbatim plus a
`reused_from_complete_sha256` note) and `only_shards` (verification builds), new sha256
`faf6b44bbbe40468067a37c99d4760c56e5921fdad2321e4e659078071fea319` (old `8593d840...417f`); tests in
`tests/test_core_rebuild_delta.py` (reuse + recompute equals a from-scratch build of the same inventory).

Proof (`DELTA-PROOF.json` in the build dir, `rebuild_delta prove`, ok = true):
- 237 of 256 shards are byte-identical to the old build (re-hashed both sides); 19 shards were recomputed
  (01 07 0d 0e 1e 25 2a 2d 2e 31 48 61 6d 8a 94 9a a2 e3 e9).
- Row-level diff of the 19 shards against the old ones (all tables, EXCEPT both directions): differences exist only in
  the 21 affected families; no other family changed; no text was lost; 0 new texts.
- Determinism: the 19 affected shards were built a second time from scratch into a separate directory; all 19 are
  byte-identical to the first build.
- Inventory coverage: evidence + delta = 488,016 (setid, version) = the rebuild rows; raw sha256 equal for every row.
- Validation sample (`DELTA-VALIDATION-SAMPLE.json`, `rebuild_validate --delta`): 50 pair records re-derived from raw
  with the frozen `compare_docs`, including all 21 new/recomputed pairs of the affected families; 50/50 record hashes
  identical.
- `tests/test_core_*.py` and `tests/test_rebuild_reader.py`: 327 passed.

**Result: the 26 now have rows, but not parsed text.** All 26 admitted bodies are intact ZIPs with matching setId/version, and
all 26 end up as status `extraction_defect` (reason `xml_error`): the frozen parser's XML read rejects them with "Excessive
depth in document: 256, use XML_PARSE_HUGE option" (lxml parses all 26 with `huge_tree=True`). That is a limit of the frozen
parser (spl-sections-0.2.3), not a defect in the bytes; the modules stay frozen, so the 26 carry no sections or text and are
not compared with their neighbours (their 21 pairs are recorded as `not_comparable`, reasons `from_status:/to_status:
extraction_defect`, never invented as changes). Reading them needs a new, versioned parser build, which was not done.

| Count | Step E | Delta build |
|---|---|---|
| versions | 488,016 | 488,016 |
| `no_raw` (no retained raw) | 82 | **56** (55 listing-only 404s + the synthetic fixture version) |
| `extraction_defect` | 0 | 26 |
| pairs | 328,985 | 329,005 (+21 new, -1 replaced: one old pair spanned an admitted version) |
| change records | 1,493,012 | 1,493,011 |
| `not_comparable` pairs | 7 | 28 |

`describe-release` reports `versions_without_raw: 56` (it is derived from the release's status counts); the release
notes in `runtime/native-config-rebuild-0.2.3-delta-20261111.json` say so, and that config is now the default of
`label-history` and `label-history-mcp`. The old config `runtime/native-config-rebuild-0.2.3.json` still selects the Step E
release (82). Weekly-update deltas were not merged.

## 7. Tightened gap estimate (1,000 more probes, 2026-11-12 02:16-02:49 UTC)

Run `20261112T021604Z` of the same `gap_repair probe`: seed 20261112, stratified (350 below-v1-other, 300 below-v1
from families starting at 100, 350 internal gap), drawn from the implied-missing pools after removing the 200 targets of
the first sample (no target probed twice; pools 81,023 / 286,941 / 61,117), no retries or controls. One lane,
nice 10, 2.0 s per request, guarded by `preflight` (SSD identity, STOP, HEAVY-JOB.lock, backoff), stop on 403/429.
1,000 requests, 12.4 MB, status 404 for all 1,000, no 403/429. Total requests today 1,414 of the 1,500 cap
(414 earlier). Rows are in `updates/gap-repair/gap-repair.sqlite` (`attempts`, kind `missing_sample`); summary in
`updates/gap-repair/SUMMARY-missing-sample-20261112.json`. Code change: `draw_sample(exclude=)`, `--skip-retries`,
`--rebuild`.

| Sample (Wilson 95%) | n | Recovered | Upper bound |
|---|---|---|---|
| First 200 (section 3) | 200 | 0 | 1.88% |
| New 1,000 | 1,000 | 0 | 0.38% |
| **Pooled, all missing-version probes** | **1,200** | **0** | **0.32%** (about 1,370 of 429,282) |
| below-v1, families starting >1 (not 100) | 420 | 0 | 0.91% |
| below-v1, families starting at 100 | 360 | 0 | 1.06% |
| internal gap | 420 | 0 | 0.91% |

The pooled row is unweighted and under-represents the start-100 stratum (30% of draws, 67% of the pool). Reweighting by
pool size with zero hits keeps the point estimate at 0; the conservative ceiling (pool size x per-stratum upper bound,
summed; not a joint 95% bound) is 1.0%, about 4,300 versions, almost all in the start-100 stratum.

Recommendation: do not run the ~429k-request full recovery. 0 of 1,200 sampled implied-missing integers exist at
DailyMed, which agrees with section 1: none of them is in DailyMed's own history. If a residual doubt about the start-100
families matters, a further 500 probes of that stratum alone (about 17 minutes) would lower its bound to about 0.5%; it is not
needed for any current product path. Keep the 55 listing-only 404s and the NALTREXONE history as unknown (re-check at most
quarterly). A better use of effort is the 26 parser-unreadable versions above, which are in hand and need only a new
versioned parser (large-depth XML), not more collection.
