# Authoritative parser plan (approved 2026-11-10)

## Product definition
GitRx is a complete, bulk-accessible superset of FDA's Drug Safety-related
Labeling Changes (SrLC) database. Priority 1: authoritative — validated
mechanically against SrLC as a gradebook. Priority 2: extend SrLC-style change
records to every DailyMed family (generics/ANDA, OTC, other) and to pre-2016
history, served through CLI, MCP and a deterministic Git structure.

Core output: an SrLC-style change record per consecutive version pair — changed
sections (number, title, SrLC group: BW, C, WP, AR, DI, USP, MG), verbatim added
and removed text, DailyMed effective/publication dates (never presented as FDA
supplement approval dates) and source hashes.

## Starting facts (2026-11-10 audits)
- Raw evidence intact: 487,963 unique SPL ZIPs (184.6 GB), 0 hash mismatches,
  all 487,943 backfill targets present; 81 unavailable (55 HTTP 404, 26 invalid).
- Source observations and derived parse data are mixed in 769 SQLite files.
- Parser `spl-sections-0.1.0`: text conserved and deterministic on a 1,543-version
  seeded sample (seed 20261110); weaknesses are the status model, section identity
  (37% generic 42229-5 codes, repeated codes, no section id) and no change-record
  layer. Prior "human" validation was AI-graded (VALIDATION-CORRECTION-2026-11-10.md).

## Steps
A. Consolidate source observations into one evidence database with verified row
   parity; raw ZIPs stay content-addressed on the external SSD. No deletion yet.
B. Parser `spl-sections-0.2.0` (new versioned build; 0.1.0 code stays frozen):
   honest status model, `no_label_body` documents, section identity capture,
   explicit unassigned/product-data buckets, build-failing invariants (text
   conservation, order, determinism), seeded regression set.
C. Change records: cross-version section alignment, SrLC group mapping (PLR and
   non-PLR), verbatim added/removed text.
D. SrLC gradebook: polite crawl of the public SrLC site (raw responses kept as
   evidence), mapping SrLC entries to NDA/BLA-holder setids and bracketing versions.
E. Full rebuild of all versions from raw; then delete superseded derived data.
F. Gap repair: unavailable versions, NALTREXONE history, families whose captured
   history starts after version 1. Unrecoverable items remain explicitly unknown.
Code cleanup proceeds alongside; removed code stays recoverable at tag `codex-final`.

## Pre-registered validation (fixed before any SrLC results)
- Split: a seeded random 30% of mapped SrLC entries is sealed as the final exam
  before any tuning; its entry list is fingerprinted (sha256) and committed.
  Development and tuning use only the remaining 70%.
- Pass bar on the sealed 30%, evaluated once per frozen parser version:
  - Section recall >= 95%: SrLC's changed section appears changed in GitRx's
    record for the bracketing version pair of the holder setid.
  - Verbatim text recall >= 95%: SrLC's underlined added/revised text (whitespace
    and markup normalized) is present in GitRx's reported added text.
  - Every miss is assigned a category (parser, alignment, mapping, version
    bracketing, SrLC-side inconsistency, missing DailyMed version).
- Changes GitRx reports that SrLC lacks are measured and reported, not scored as
  errors (SrLC recall is unknown and it excludes non-safety sections).
- Out of SrLC's reach (pre-2016, generics, tables, non-safety sections) is covered
  by the same frozen code plus mechanical invariants, and disclosed as such.

## Amendment 1 — metric operationalization (2026-11-11, before any grading)
Fixed before the sealed draw and before any GitRx output is compared with SrLC.
- Sealed draw unit: SrLC DrugNameID. A seeded random 30% of drugs (seed
  20261111, sorted DrugNameIDs, Python `random.Random(seed).sample`) is sealed;
  all supplements/rows of a drug fall on one side, preventing leakage.
- Candidate families: DailyMed setids whose SPL product approvals contain the
  SrLC application number (type + number). An entry is graded against each
  candidate; it counts as found if any candidate satisfies the criterion
  (per-candidate results are also reported).
- Bracketing: a consecutive version pair whose to-version DailyMed publication
  date lies in [SrLC approval date − 30 days, SrLC approval date + 365 days].
- Section recall unit: one SrLC change row (drug, supplement, group). Found if a
  bracketing pair has a substantive (non-formatting) change whose srlc_group
  equals the row's group.
- Text recall unit: each underlined fragment (split at "…"; whitespace/markup
  normalized with GitRx literal-v1) with ≥3 tokens. Found if the fragment occurs
  verbatim in the to-version text of a changed same-group section in a
  bracketing pair AND overlaps at least one GitRx added token. Fragments <3
  tokens and table-referring rows are reported separately, not in the 95% bar.
- Excluded from both denominators and reported as coverage metrics: entries with
  no candidate setid in the captured corpus, and entries with no captured version
  published inside the bracketing window.

## Deletion gates (must all pass before any derived/legacy data is deleted)
- evidence-v1.sqlite VERIFY.json ok=true (met 2026-11-11: sha256 823a5bc6…ac3c6,
  0 problems, byte-identical rebuild).
- Hash-registered-only JSON (batches/*/inbox.json and BATCH.json, mobile-workspace
  inbox.json, EXPANDED-SELECTION.json) either stored in full or proven derivable
  from imported evidence tables, field by field (inbox.json carries raw
  publication strings). Checked 2026-11-11: all source observations are in the
  evidence DB, but BATCH.json family states/result_json, the batch_qualification
  caveat and EXPANDED-SELECTION parse-run/scope metadata are not. Resolution: archive
  these files intact as a hash-recorded pipeline-provenance bundle on the SSD
  before deletion.
- Every raw ZIP and source body present in the consolidated raw store on the SSD,
  re-hashed after any move.
- Owner approval of the exact deletion list.

## Sealed split record (2026-11-11, before any grading)
Drawn by `gitrx_core/srlc_split.py` from the SrLC enumeration ledger (crawl still
fetching detail pages; no SrLC change data compared with GitRx output yet).
Fingerprint = sha256 of ascending decimal DrugNameIDs joined by "\n".
- Population: 3,278 DrugNameIDs, sha256 `b065c433be0b393db3b734bdbfe61ea5b319d86cf0eee46f1681b85f9c2f03f0`
- Sealed (30%): 983, sha256 `354025929110b2c565129d565b2ddcdc6ab7e5bc7a9d889f0ba4a241e897d894`
- Open (70%): 2,295, sha256 `f494923ef173088e164810c46a312cd964a4e3ab78d12a63be4d7cb4646ed333`
- Lists: /Volumes/Untitled/GitRx/srlc/split.json (sha256 `45725c78830a4a5a657bd92bf3f01d6d85580a3f8f4abd23d56ae82b664750a9`).
Development reports must filter to the open list; sealed-drug grading runs once per frozen parser.

## Amendment 2 — text matching and miss labels (2026-11-11, open split only; sealed set untouched)
Open-split run 1 (parser 0.2.1, harness srlc-grade-0.1.0): section recall 94.2%, text recall 73.6%.
Diagnosis on seeded open-split samples (seed 7/11/3, scripts kept in the session scratchpad):
55% of sampled "absent from label" text misses are present in an in-window label version
once spacing is ignored. Label XML puts spaces inside cross-references and symbols
("( 5.1 )", "SPORANOX ®"); SrLC pages contain broken-word artifacts ("open- label",
"His tamine"). These are matching failures, not parser or SrLC errors.
Changes, fixed before re-grading:
- Primary text metric: a fragment is "verbatim" if it equals the label text after literal-v1
  normalization AND removal of all whitespace (the original plan's "whitespace and markup
  normalized"). The overlap-with-added-token rule is unchanged, evaluated on the same mapping.
- The strict literal-v1 result stays reported alongside, for both the open and sealed runs.
- Automatic miss labels are descriptive, not attributions: "absent from label text",
  "present before window", "present only after window", etc. No category is named
  "SrLC-side inconsistency" unless confirmed per case.
- Pre-existing checks are made per candidate setid; candidates sharing an application
  number but with different product names are reported as sibling-product candidates.
Thresholds, window, units, split and the 3-token rule are unchanged.
Correction (same day, after the Amendment 2 re-grade): the "55%" figure above came from a
looser comparison (letters and digits only, case-folded), not from whitespace removal alone.
Whitespace-free matching raised open-split text recall from 73.6% to 75.7% (strict unchanged
at 73.6%); section recall unchanged at 94.2%. Run: grading/open-spl-sections-0.2.1-h0.2.0.

## Amendment 3 — separate parser accuracy from SrLC/DailyMed timing (2026-11-11, open split only)
Open-split diagnostics (harness 0.2.0, seeded samples of 200/60):
- Of text misses already present before the pair, the text's first appearance in captured
  history was: 30% in the first captured version (history starts too late or a new setid),
  19% 31–180 d before SrLC approval, 17% 181–365 d before, 35% >1 year before. Labels can
  change before approval (Changes Being Effected supplements), so a −30 d window misses them.
  Of found fragments, 92% first appear within [−30, +365] d.
- Of fragments absent from every captured version, ~40% differ only in rendering
  (leading bullet characters, "?" for non-ASCII such as "≥", case, invisible format
  characters); the rest are absent in wording (often 2026 approvals not yet on DailyMed).
These are not parser behaviors, so the bar is moved to a metric that isolates the parser.
Changes, fixed before re-grading:
1. Rendering normalization (applied to fragment and label text, on top of Amendment 2):
   Unicode case-folding; removal of Unicode format characters (category Cf); removal of
   leading list markers (•, ◦, ▪, -, *, o followed by space) at fragment start; a "?" in an
   SrLC fragment matches any single non-ASCII label character. Wording, punctuation and
   numbers stay strict.
2. Parser accuracy metric (carries the ≥95% bar on the sealed set): for each ≥3-token
   fragment, find in captured history of each candidate setid the transition pair (fragment
   absent in a captured version, present in the next captured version) whose to-version date
   is nearest the SrLC approval within [approval − 730 d, approval + 365 d]. Fragments with
   no such transition are "not locatable" (reported as coverage). Text accuracy = located
   fragments reported by GitRx inside added tokens of a substantive change in that pair
   (same SrLC group; any-group also reported). Section accuracy = rows with ≥1 located
   fragment whose transition pair has a substantive same-group change.
3. End-to-end SrLC concordance (Amendment 1 window, with Amendment 2/3 matching) and the
   strict literal-v1 variant stay reported for open and sealed runs, without the bar.
Split, 3-token rule and units are unchanged. The sealed set is still untouched.

## Amendment 4 — SrLC group conventions (2026-11-11, open split only; sealed set untouched)
Diagnosis (80 sampled open-split group misses): 67 were old-format (non-PLR) labels, where
SrLC files text from subsections under PRECAUTIONS/WARNINGS (Drug Interactions, Pediatric
Use, Nursing Mothers, Pregnancy…) under WP while GitRx used the subsection topic; 12 were
SrLC placements differing from the label; 1 was a GitRx PLR misclassification (5.11 → USP).
Changes (srlc-groups-0.2.0, srlc-changes-0.2.1, harness srlc-grade-0.3.1; parser unchanged 0.2.1):
- PLR: numbered x.y sections take the group of their major section (4 C, 5 WP, 6 AR, 7 DI,
  8 USP, 17 MG); unnamed/unnumbered children inherit the parent's group.
- Non-PLR: sections nested under WARNINGS/PRECAUTIONS get srlc_group WP, and keep their topic
  group as alt_group. SrLC is itself inconsistent here (it files some such subsections under
  the topic group), so the grader accepts either srlc_group or alt_group as "same group".
  The WP-only variant (no alternative accepted) is reported alongside: open-split text
  97.6%, section 98.5% (run …srlc-changes-0.2.0-h0.3.0).
- Section titles are part of the text channel, so added or reworded headings count as added text.
- Harness note (Amendment 3): SrLC "?" also matches a literal "?" in the label.
Open split, final (…srlc-changes-0.2.1-h0.3.1): parser text accuracy same-group 98.2%
(any-group 99.6%; strict 94.1%; ws 97.1%); section accuracy 99.5%; lowest group DI text 95.4%.
End-to-end (Amendment 1 window): section 94.4%, text render 77.8% (strict 74.8%).

## Freeze 1 — sealed exam (2026-11-11), declared before any sealed result
Frozen at commit a00c5d27c8cc95cdf0f4216da198281ca9984ae0: parser spl-sections-0.2.2,
changes srlc-changes-0.2.2, groups srlc-groups-0.2.0, harness srlc-grade-0.3.2.
Inputs: gradebook.sqlite 863aea67…896db4, split.json 45725c78…4750a9 (sealed 983 DrugNameIDs,
354025929110…d894), application index index-allversions.sqlite a5739f4d…3243e7.
Code sha256:
- spl_parser.py 99885a78f989ac65a255acf58629fd180642e40f92c1e3fefcad1bc5711add38
- normalize.py 4a613040e6f1660d917f955ebea194b6084765a121e28e5caef3d45be886d12b
- section_groups.py 92b4fd5c96ba9e8957ef5786542a2cdbc35629421b78591e8fb1f344f97e93f4
- changes.py 1d22bc4cbb16263a01d8f375a430243a85072d86d60f34896ff93025767e181b
- srlc_grade.py 26c40e528240bea241bedb75d82c15a7e587a293a483a5294e09bed9becb88e9
- srlc.py 99702e2aee1d9bbdca59c20a2445872d82a7441965b1e36ee9923bc9e10179b9
- srlc_split.py b8509beb71895874a14702a83184f8bd348426ff7bedb70874f1729e12343a79
Runs (each exactly once): sealed --group-mode either, then sealed --group-mode primary.
Primary pass/fail (Amendment 3 bar, ≥95%): parser accuracy on the sealed set, render matching,
group-mode either — text same-group AND section accuracy. Reported alongside, without the bar:
group-mode primary (WP-only), strict and ws text variants, any-group text, per-group results,
not-locatable coverage, end-to-end SrLC concordance (Amendment 1 window).
Open-split reference (same frozen code): either 98.44% text / 99.70% section; primary 97.83% / 98.70%.
Every sealed miss is listed with its descriptive label; no tuning on sealed results.

## Sealed exam result — Freeze 1 (2026-11-11), run once per declared mode
Outputs: grading/SEALED-spl-sections-0.2.2-h0.3.2-either (summary sha256 e80b56405eae6112c1422fbd6cf804e51873eb441384ee0611a87d174af33bdb) and
…-primary (summary sha256 e4619a0a6e667132fd34e0150ddb78a623736b1a8c0bb67227fdca8e9588d08d). Sealed set: 983 DrugNameIDs; 3,365 entries, 2,626 graded.
**Primary metric (Amendment 3, render, group-mode either): PASS.**
- Parser text accuracy, same group: 97.62% (17,180/17,599; 95% CI 97.38–97.83).
- Parser section accuracy: 99.08% (4,102/4,140; 95% CI 98.74–99.33).
Reported alongside (no bar):
- Group-mode primary (WP-only for non-PLR): text 96.48% (CI 96.19–96.74), section 97.92% (97.44–98.31).
- Text strict 93.39%, ws 96.61%; any-group text 99.24%.
- By group, text / section (either): AR 98.1/99.5, BW 84.7/90.2 (n 313/92), C 92.8/98.0
  (n 290/147), DI 95.2/98.2, MG 97.7/99.3, USP 97.8/99.2, WP 98.6/99.7. BW and C are below 95%
  on text; BW is below 95% on sections.
- Coverage: 7,716 of 25,315 main fragments not locatable (3,344 never present in captured
  history; 3,169 present from the first captured version; 1,203 transitions outside range).
- Located misses (either): 285 different group, 111 pair not comparable (73 from setid
  f6dd9b86…, 38 from 2eda833b…, extraction defects), 15 same-group but outside added tokens,
  4 in raw XML but not parsed, 3 other-group only, 1 same-group not marked changed.
- End-to-end SrLC concordance (Amendment 1 window): section 94.04%; text render 78.60%,
  ws 77.77%, strict 75.44%.
No tuning was done on these results. Any later parser change is a new frozen version and
needs a new sealed run, reported together with this one.

## Amendment 5 — sealed miss categorization and holdout independence (2026-11-11)
Open-split study (frozen Freeze 1 code): BW classification audit 296/296 box sections BW, 0
failure patterns; the 14 open BW parser misses are all SrLC filing choices (BW rows for labels
without a box; BW text that the label places in 5.x/17; text referenced from the box). A
drug-cluster bootstrap of open BW (200,000 resamples) never reached the sealed 15.3% miss rate.
Owner decision: the sealed BW misses are categorized descriptively, as the plan requires for
every miss. Consequence: any change motivated by sealed misses makes the Freeze 1 sealed set
non-independent for that change. Such changes are validated on a fresh, later holdout (e.g.
SrLC entries posted after 2026-11-11) and the sealed set is reported as "seen" for them.

## Freeze 2 — sealed rerun (2026-11-11), declared before any Freeze 2 sealed result
Frozen at commit 34ff8d0e2aacdb025ac5ee2ce80480b21a7f7a44: parser spl-sections-0.2.3, changes srlc-changes-0.3.2,
groups srlc-groups-0.3.0, harness srlc-grade-0.3.2 (unchanged). Inputs identical to Freeze 1
(gradebook 863aea67…896db4, split 45725c78…4750a9, sealed 354025929110…d894, index a5739f4d…3243e7).
Code sha256:
- spl_parser.py 2bffee02825edf82e97a6373a45e8fdab40c7d3f39f8ed95edcdeccde6468853
- normalize.py 4a613040e6f1660d917f955ebea194b6084765a121e28e5caef3d45be886d12b
- section_groups.py f1a2684d4d1aead3d2c42ff8b89b90495513cb1877ae9dd0af2381774a538b80
- changes.py 9b0bc06db5adaffd38d5a1099db50a4fd4ddfddca4d9fb3a1cf1fbac02e11c95
- srlc_grade.py 26c40e528240bea241bedb75d82c15a7e587a293a483a5294e09bed9becb88e9
- srlc.py 99702e2aee1d9bbdca59c20a2445872d82a7441965b1e36ee9923bc9e10179b9
- srlc_split.py b8509beb71895874a14702a83184f8bd348426ff7bedb70874f1729e12343a79
Changes since Freeze 1: HTML-spec table span parsing; uncoded boxed warnings → BW
(inferred:uncoded_box); formatting-noise canonicalization; cross-reference-only edits kept as
changes flagged reference_only (owner decision).
Independence (Amendment 5): the uncoded-box rule was motivated by a categorized sealed miss
(ATIVAN 89057c93); the sealed set is "seen" for that rule and Freeze 2 sealed results are
reported as seen, alongside Freeze 1. The span and noise fixes were motivated by open-split
sweeps and open precision only. Independent confirmation: fresh holdout of SrLC entries posted
after 2026-11-11.
Runs (each exactly once): sealed --group-mode either, then primary. Same primary metric and bar
as Freeze 1. Open-split reference (same code): either 98.48% text / 99.74% section; primary
97.87% / 98.73%; end-to-end section 94.32% (Freeze 1 94.56%; 48 lost rows all formatting-only
pairs); precision crude 79.75% (80.54% excluding reference_only), timing-robust 86.56%.
