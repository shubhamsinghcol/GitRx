# Core product roadmap

Consolidated November 8, 2026 (America/Chicago). This is the current priority list.
The [full preceding roadmap and delivery log](history/2026-11-08/CORE-PRODUCT-ROADMAP.md)
is retained, including the preexisting November 7 full-DailyMed authorization.

## Product and implemented foundation

A researcher can resolve a DailyMed name to a setid, inspect captured versions,
read by version or publication cutoff, compare occurrences, search literal wording
history and copy a release-pinned citation. Native CLI, MCP and the local browser
share backend queries. See [core workflow](CORE-WORKFLOW.md) and
[qualification](QUALIFICATION.md) for behavior and measured limits.

The source package declares native 0.10.0. The earlier expanded release comprises
7,239 families and 51,357 retrieved versions; these are recorded release counts,
not the current finite backfill's completion totals. Read selected manifests and
`describe-release` before answering corpus questions.

## Remaining priorities

1. Keep native answers, citations, coverage and partial-extraction disclosures
   consistent across adapters. Fix shared backend behavior before browser extensions.
2. Qualify measured latency, dense searches, long histories, cancellation and
   resource bounds on a pinned candidate. Local samples and fixture CI do not
   establish corpus-wide extraction recall or hosted capacity.
3. Finish the explicitly authorized finite full-DailyMed acquisition and assess
   staged evidence before building a broader candidate. The plan records 158,963
   current-listing setids plus 19 retained families; archived-only discovery is
   unresolved. Use [operations](OPERATIONS.md) for the evolving working profile.
4. Verify changed-candidate integration, retained citations, selection and rollback
   before promoting a broader release. Acquisition completion, promotion and
   distribution are separate milestones.
5. Resolve a reproducible external installation and distribution/rights decisions
   before a controlled launch. Public hosting or publication needs its own decision.

## Owner constraints

Raw artifacts and source observations remain authoritative; generated Git is a
rebuildable representation. Preserve old evidence/releases/refs, frozen rules,
goldens, original human scores and historical reports. “First observed” never
means proven introduction. No silent clinical interpretation or family merging.

The owner authorized broader DailyMed scope and one finite resumable acquisition
job. This does not authorize automatic recurring collection or public distribution.
Independent backups remain outside scope; the stopped restore drill is incomplete.
The original held-out packet remains sealed/excluded. The 60/60 "owner-adjudicated"
sample was AI-graded, not human-reviewed (corrected 2026-11-10); the parser has no
human validation. See [validation correction](VALIDATION-CORRECTION-2026-11-10.md).

Defer additional regulators, semantic search, clinical summaries, RxNorm merging,
community annotation infrastructure and broader analytics until a demonstrated
product need. Do not infer live progress, transport settings or job health from
this roadmap or dated delivery reports.
