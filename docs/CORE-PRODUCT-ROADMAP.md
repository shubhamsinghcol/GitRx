# GitRx core-product roadmap

Updated November 6, 2026 (America/Chicago). This is the current product sequence;
the older roadmap retains historical acquisition and research plans.

## The product we should finish

A researcher can find a DailyMed label family, open its captured history, read
the label by version or publication cutoff, compare sections, find the first
observed wording, and copy a citation that remains valid after updates.
The same answers are available to software through CLI and MCP.

Production v1 supports the existing declared corpus. It does not require more
regulators, more drug categories, semantic search, clinical interpretation,
community annotations, or a research paper. A family remains a setid lineage.

## Starting point

The expanded release has 7,239 families and 51,357 known retrieved versions.
The shared native library already supplies ten CLI/MCP tools, including dated
reads, conservative comparisons, phrase history, coverage and section history.
Portable packaging, pinned Git/SQLite readers and a configured incremental
acquisition/release command exist. These are implemented capabilities, not yet
a qualified public service.

Evidence: [workflow](CORE-WORKFLOW.md), [release cycles](RELEASE-CYCLES.md),
[foundation](../reports/backend-foundation-20261106/REPORT.md), and
[latest delivery](../reports/release-cycle-20261106/REPORT.md).
Changed-release/restart tests used synthetic fixtures; the live MoviPrep cycle
was a no-change cycle. Broad performance and hosted operation remain unqualified.

## 1. Establish a trustworthy core release

Verify the current implementation as one product before adding features.
Assemble representative source-backed journeys covering straightforward labels,
multiple same-name families, long histories, partial parses, repeated/uncoded
sections, tables, unknown dates, gaps and publication-order conflicts.
Check resolution → history → read → compare → first-observed → citation through
the shared library, CLI and an actual MCP client. Verify both readers agree for
the selected cases. Repair defects that change answers or hide limitations.

Keep native source occurrences usable without requiring full-corpus context
classification. Friendly section names may select source codes, but must retain
all occurrences and disclose unclassified context. “First observed” remains the
product wording, including where a CLI alias says `when-added`.

**Done when:** the acceptance journeys pass on a pinned candidate; citations,
missing evidence and incomplete scans are consistent across adapters; no critical
answer/citation defect remains. Record actual test scope rather than implying
that one human sample validates the whole corpus.

## 2. Make the existing workflow predictably fast and bounded

Measure cold/warm reads, long-history phrase scans, large comparisons and latest-
corpus literal searches on the maintained readers. Record latency, memory and
result size. Use the existing contract's performance goals as initial targets.
Add whole-request deadlines/cancellation and explicit resumable progress for
expensive scans; existing per-Git-command timeouts are not whole-query limits.

Optimize measured bottlenecks with bounded caches or a derived text projection;
add local FTS only if needed, preserving literal semantics and release pins.
Do not replace the evidence database or introduce a database service by default.

**Done when:** ordinary journeys meet declared latency targets on stated hardware;
large requests finish within bounds or return a useful continuation; resources
remain bounded under the intended concurrency. Publish the measured limits.

## 3. Close the update-to-release loop

Build on `release-cycle` rather than writing another ingestion system. Exercise
a changed-source cycle through acquisition, parsing, staging, reader verification,
atomic selection and rollback. Use controlled fixtures for faults and a real
changed family when available; a no-change refresh is not that proof.

Make the supported update scope, request budget, refresh cadence, last successful
refresh, quarantine state and current release visible. Choose a sustainable
manual cadence first; install recurring scheduling only when freshness demands it.
Ensure retained release configurations and citations survive new promotion and
process restarts. No automatic parser/rule upgrades.

**Done when:** a changed release can be built, selected and rolled back without
rewriting old evidence or invalidating old citations; failed cycles leave the last
good release usable and report a clear reason. Freshness claims match operations.

Independent backup copies remain outside the owner's current scope. The stopped
restore drill is not completed; this roadmap does not silently reinstate that work
or claim disaster recovery has been demonstrated.

## 4. Add the smallest complete user interface

After the backend acceptance gates, build a plain browser interface around the
same library: family search/selection, version timeline, historical read, native
section selector, two-version comparison, phrase history and copyable citations.
Keep coverage, publication/effective-date distinctions, ambiguous occurrences and
partial extraction visible at the point of use. Preserve readable table content
or explicitly show extraction limitations.

Use a thin read-only service for the interface, with typed responses, stable
errors, pagination, release selection and request bounds. Keep ingestion/admin
commands separate. A local interface can establish usability before public
hosting; the CLI/MCP-only release remains a valid developer-facing milestone.

**Done when:** a user can complete the flagship journey without terminal commands
or interpreting internal JSON, and can reopen the same cited release later.
No accounts, dashboards or visual polish are required for this gate.

## 5. Qualify and ship a controlled production v1

Create one reproducible install/deploy path, run the maintained acceptance checks
in CI, and verify a clean installation outside this workstation. For hosted use,
test concurrent reads, request limits, untrusted inputs, health/readiness, logs,
release selection and restart/rollback in staging. Set authentication/rate limits
according to exposure. Keep the initially shipped scope small and explicit.

Resolve attribution, third-party rights and distribution choices before public
data publication. Choose who operates updates and responds to failures. Release
first to a small controlled audience, fix blocking workflow defects, then make
the public launch decision separately. No deployment or publication is authorized
by this planning document.

**Done when:** another user can install or access the product, complete the core
journey, obtain stable citations, and receive clear bounded failures; an operator
can update and roll back it using a short runbook. Document the remaining
disaster-recovery limitation if independent backups remain excluded.

## Priority and exclusions

Sequence: core correctness → measured performance → reliable releases → plain
interface → controlled launch. Acceptance-case design and performance measurement
can begin together; UI implementation follows the backend gates. Operational
logging starts with release work, not after launch.

The next implementation block is milestone 1 plus baseline measurements from
milestone 2: produce a candidate acceptance report, fix blocking defects, and
identify the actual bottleneck before selecting new infrastructure.

Defer word-by-word blame, semantic/AI summaries, claim checking, all-family date
inventories, broad change analytics, RxNorm merging, ANDA/OTC expansion, additional
regulators, reviewed annotations and FDA-comparison research. None is required to
ship the declared label-family history product. Full-corpus inferred context is
an optional later interpretation layer, not a prerequisite for native v1.

## First acceptance block completed

[Acceptance and timing evidence](../reports/core-acceptance-20261106/REPORT.md):
sampled real workflows and adapter/client agreement pass; 20 final focused tests
pass. Native answers now explicitly qualify extraction completeness. Broad scans
are the demonstrated bottleneck: Git 128-family search 14.58s versus SQLite 0.56s;
full-corpus SQLite scan 30.23s. Next implement whole-query bounds and measured
scan acceleration. This is sampled functional acceptance, not completed production
performance, concurrency, or exhaustive source-extraction qualification.

## Bounded search and release drill delivered

[Query limits/acceleration](../reports/query-limits-20261106/REPORT.md) now provide
wall-clock execution deadlines and release-bound search continuation. The optional
SQLite projection reduces measured sparse full-corpus searches to 1.06–2.18s;
very large complete result exports remain slower and paginated. A modest four-
process local read check passed; this is not hosted-service qualification.
[Changed-release/rollback evidence](../reports/release-drill-20261106/REPORT.md)
covers real build/promotion machinery against controlled HTTP fixtures, including
retained old citations and quarantine. Live full-corpus changed-release capacity,
public operation and disaster recovery are not claimed by that drill.

## Plain local browser delivered

The [browser workflow](BROWSER-WORKFLOW.md) now exposes the flagship journey with
ordinary controls, structured table text, contextual dates/coverage/ambiguity and
copyable release-pinned references. [Browser and automated checks](../reports/browser-workflow-20261106/REPORT.md)
cover real label journeys and reopening older references after registering a newer
release. The next product gate is controlled installation/operation and launch;
this delivery is local and does not establish public hosting or service capacity.

## Global-law transfer implementation: first block

Native 0.6.1 implements Unicode-safe MCP input/output, a whole-response wire cap,
version/occurrence pagination, and browser consumption of version pages. Current
README/MCP/security/limitations now distinguish expanded native access from the
interpreted pilot; a concise AGENTS guide records evidence and validation rules.
The installed-wheel CI definition includes the maintained native regression set.
See [implementation evidence](../reports/global-law-transfer-review-20261106/IMPLEMENTATION.md).
Next transfer work is shared extraction/inventory/citation metadata, followed by
complete one-family literal phrase histories. Responsive MCP cancellation,
resources/prompts and freshness integration remain separate increments; no new
acquisition, selected release or recurring scheduler is implied by these fixes.

## Shared evidence and complete phrase histories delivered — November 7

Native 0.7.0 adds a separately pinned evidence metadata projection, shared
extraction/inventory/source-reference details across adapters and clients, and
complete paginated one-family literal phrase histories with unknown intervals.
The browser exposes the same histories and source provenance. Current checkout
wrappers use a verified enriched configuration of the existing snapshot; old
configs remain readable. Changed-release staging now builds its own metadata pin.
[Usage](EVIDENCE-HISTORY.md) · [delivery and remaining work](../reports/evidence-history-20261107/REPORT.md).
Next: MCP discovery/resources/prompts and explicit freshness/operational status;
responsive cancellation and optional search-projection preparation remain open.

## Discovery and observed operations delivered — November 7

Native 0.8.0 adds release discovery, packaged pinned resources and research
prompts, per-family check/build/selection status, optional fresh search projections
in candidate staging, a comparable local performance ledger and contribution
routing. [Delivery and remaining work](../reports/discovery-operations-20261107/REPORT.md).
The operation mode is manual; no scheduler/live acquisition was installed or run.
Next functional work is responsive MCP cancellation and version-change details,
followed by scoped changed-text search. Corpus-wide performance/hosting
qualification and conditional later features remain separate.

## Responsive transport and change inspection delivered — November 7

Native 0.9.0 adds cancellable stdio query work, responsive pings/control requests,
frozen-release worker restart, version-change summaries and one-family literal
changed-text search through shared CLI/MCP/browser queries. The dated previous
next-step lists are superseded by this delivery.
[Delivery and verification](../reports/cancellation-changes-20261107/REPORT.md).
The core transfers selected from the global-law review are implemented. Broader
capacity/source qualification, workload expansion and conditional later features
remain distinct from the implemented product behavior.

## Deadline repair and expanded qualification — November 7

Native 0.10.0 fixes nested timer restoration and adds repeated fresh-process
regressions. The maintained qualification runner covers dated reads, complete
histories/comparisons, negative/sparse/dense full-corpus searches, retained
configurations and local concurrent readers. Shared research helpers add bounded
token replay, citation verification, explicit cohort-date inventories and
selective local shard installation; descriptive research figures retain input pins.
[Current results and external prerequisites](../reports/qualification-20261107/REPORT.md).
Live checks/build qualification are distinct from automatic freshness. Remote CI,
another-machine installation and hosted operation need an owning external target;
local matrix tests do not establish those outcomes.
