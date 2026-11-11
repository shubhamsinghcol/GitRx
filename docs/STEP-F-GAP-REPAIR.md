# Step F: gap repair (measurement, plan, cheap probe)

Written 2026-11-11. Owner-approved scope: unavailable versions, NALTREXONE history,
families whose captured history starts after version 1. Collection only: nothing was
parsed, rebuilt, promoted or deleted. `evidence-v1.sqlite` (sha256 823a5bc6...ac3c6) and the
rebuild shards were opened read-only. Unrecoverable items remain explicitly unknown.

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

## 5. What a delta rebuild needs (not done)
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
