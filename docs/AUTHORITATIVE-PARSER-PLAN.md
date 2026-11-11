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
