# GitRx roadmap and operating rules

> **Current core-product sequence (November 6):** [CORE-PRODUCT-ROADMAP](CORE-PRODUCT-ROADMAP.md). It accounts for the delivered native CLI/MCP workflow and release-cycle command; older next-step lists below retain their historical context.

> **Current sequence, November 5:** [BUILD-STATUS](BUILD-STATUS.md) reconciles this
> roadmap with completed acquisition, the staged recoveries, the export-growth
> blocker, and the unfinished section-diff audit. Section A's running-job
> instructions below are historical. The later product/research goals and
> applicable operating rules remain planning inputs. [Explanation](START-HERE.md).

Current owner brief accepted 2026-11-02 America/Chicago. Read
[PROJECT-BRIEF](PROJECT-BRIEF.md), [parity](GLOBAL-LAW-PARITY.md), latest
[progress](../reports/PROGRESS.md), [SPEC](../SPEC.md) and `./release1-status`
before acting. Later SPEC contracts supersede historical stops. This roadmap
is local planning; publication and licensing require the owner's decision.

## A. Finish the isolated release1 acquisition

Block3c-5 already diagnosed the NALTREXONE empty-history stop, audited all
then-selected 19,175 versions and resumed. Reuse the healthy detached job;
do not duplicate it or rerun probes merely because the chat changed. Verify
live PIDs/caffeinate, advancing heartbeats and counts, disk/cap/stop state.

Finish all 7,239 source-declared NDA/BLA families in seeded order 20261106,
500-family tranches, followed by both existing additive recovery sweeps.
Keep failed families explicit; a finished bounded run need not recover every
family. Verify immutable tranche hashes, source identities and parsings,
and final family-source/recovery manifests. Never count failed histories as
zero-version families with complete coverage. Final counts must distinguish
scope, histories resolved, listed targets, retrieved, partial/failed parses,
archive routes, unknowns and remaining family failures.

Maintain exactly one sequential persistent HTTP connection, adaptive pacing,
descriptive User-Agent, Retry-After/holds, the permanent one-second safety
cap already reached, 120,000 inclusive requests and 20 GiB free on every
written volume. Parser workers may parallelize CPU extraction only. Preserve
all evidence, ledgers and pinned scope/config; never reset counters. Only the
documented family/envelope exception is allowed; other unexplained identities
or competing bytes stop. Follow the [runbook](../reports/block3c/RUNBOOK.md).

The seeded order supports a random prefix at a predetermined family count.
A disk-, time-, request- or byte-dependent stopping point need not be a fixed-
size simple random sample; disclose the stopping mechanism and do not claim
unbiased national estimates from it automatically. NDA/BLA scope itself is
not the whole human-Rx frame and excludes a complete archived-only census.

## B. Fresh blind release gate

The owner labels `reports/block2-validation/index.html`. Keep predictions,
held-out content and seals closed until a completed human label export exists.
Validate label schema/identity/commitments without tuning, then score once
against frozen v1.0.1 under the
[fresh plan](../reports/block1c/FRESH-VALIDATION-PLAN.md).

- Zero anchored errors: sample gate passes.
- One: preserve the one-time score, make the specific source regression/fix,
  freeze v1.0.2, verify all guards/goldens/integrity, and inspect a structural
  diff for recurrence of that defect class. Used cases become development
  data; a corrected replay is not another blind pass.
- Two or more: stop release, repair with source regressions, freeze a new
  version and draw a new independent sample.

Report scorable/uncertain/unreadable counts, conditional binomial intervals,
context/audit-stratum denominators, clusters and weighted points separately.
Anchored-only sampling cannot establish global recall. Do not turn zero
sample errors into a zero-population-error claim. Nothing public before the
required branch passes; no classifier tuning while labels are pending.

## C. Release engineering, locally reviewable first

Specify behavior before implementation. Shard by first setid hex character
into 16 repos and a meta repo pinning exact SHAs; keep existing content paths.
Use the final recovery manifest, not only primary/milestone DBs. Rebuild twice
and compare every commit/tree/ref; retain old tags. Publishable per-family
manifests must link coverage/status/route failures to raw hashes and snapshots.
Raw archives and databases stay outside GitHub; choose a separate distribution
channel such as Zenodo, Hugging Face or a bucket with the owner.

Before preparing pushes, fetch **current official GitHub documentation** for
file, repository, push and release-asset limits. Measure largest historical
blob, pack and per-shard sizes; create a dry-run push plan and chunked push
script that never rewrites an existing release. Approximate remembered limits
are not acceptance criteria. Local artifacts/scripts can be prepared before
approval; actual remote/public actions wait for the owner.

Write REPRODUCE.md with exact offline commands, dependency/runtime pins,
raw/parser/rule/export hashes, two-build proof and AI-use disclosure. Write
SOURCES.md for DailyMed ordinary/archive/bulk/update sources and FDA SrLC
with scope/dates/provenance. Complete SECURITY.md, MCP.md, coverage page,
README and LIMITATIONS. Retain NLM attribution/non-endorsement and historical-
snapshot notices; the owner resolves rights in third-party label text/images
using the [licensing summary](../reports/block3b/LICENSING-SUMMARY.md).

## D. Product and parity

Build a plain local search/results page over a pinned database, with family
selection, sections, date/version views, exact differences and expandable
source citations/unknowns. Context expansion to newly ingested families is
a separately pinned derived build after the relevant gate; the existing MCP
currently serves only the accepted pilot.

Add CLI/MCP shared implementations of word provenance (`blame`/`words`),
phrase entry/exit, all-family as-of inventory (`world`), changed families
between publication dates (`between`), literal text search and bounded text-
change impact. A claim-check tool must expose matched/contradicted/unknown
text evidence and scope; it must not pronounce clinical/legal truth. No
cross-family drug merging or clinical equivalence by name. Specify pagination,
timeouts, cancellation, snapshots, literal matching and uncertainty first.
Add prompts/resources and known-reading plus CLI/MCP agreement tests. Keep
the protected AMBIEN/montelukast/CIPRO headlines and qualifications intact.

## E. Research outputs

Design an external check against FDA's SrLC safety labeling changes since
January 2016 for NDA/BLA (not ANDA). Verify official source semantics and
capture exact inputs before analysis. Match explicit application/label-family
evidence conservatively; unknown matches remain unknown. Compare FDA action
dates with **DailyMed publication** observations, preserving SPL effective
dates separately, numeric gaps, ambiguities and all presence intervals.

Report agreement and lag with uncertainty/denominators, source coverage and
censoring; investigate contradictory event ordering as a possible pipeline
bug before interpreting it. Chronology alone establishes neither causation
nor compliance. Produce reproducible lag/cadence/partial/coverage figures,
one-page result sheets by drug class, methods and limitations suitable for a
data-resource paper and pharmacovigilance study. Prepare collaborator-facing
materials locally; do not contact anyone without explicit authorization.

## F. Later scope

Live reviewed annotations; daily incremental DailyMed updates; an ANDA
tranche; evidence-backed RxNorm identity links; OTC; other regulators/EMA;
optional FDA approval reconciliation. Each needs its own scope, source
contracts, budgets and validation. No real annotations or quirks are seeded.

## Standing execution contract

Work autonomously in substantial blocks, record decisions in PROGRESS and
update SPEC before behavior changes. Repair defects in at most five bounded
iterations. Stop for protected-hash drift (fixed OS exclusions only), an
unattributable identity/byte conflict, projected free space below 20 GiB,
request cap, protected headline/qualification change, or public/irreversible
action. Retain raw evidence and quarantine instead of deleting. Cohort and
validation operations expose counts only; never inspect held-out or sealed
content or tune on scored validation cases. No new handoff unless requested.
Final reports are at most 20 lines plus links; section-0 intake report at
most 15 lines. Backfill completion, validation pass and publication are
separate milestones and must never be conflated.
