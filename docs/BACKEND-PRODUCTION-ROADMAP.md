# Backend production roadmap

> **Current core-product sequence (November 6):** [CORE-PRODUCT-ROADMAP](CORE-PRODUCT-ROADMAP.md). It accounts for the delivered native CLI/MCP workflow and release-cycle command; older next-step lists below retain their historical context.

Proposed November 6, 2026. Frontend excluded. This is a plan, not authorization to publish, select a license, change scientific semantics or contact others. Baseline: 7,239 families / 51,357 known versions; reproducible local shards; source-first browsing across the release; interpreted queries/MCP still pilot-scoped.

Production means reliable evidence, reproducible processing, correct and bounded queries, recoverable operations, and query tools clients can depend on. It does not mean historical completeness or clinical authority.

## Recommended architecture

Keep one controlled ingestion writer, immutable raw objects, source-observation databases, and separately versioned derived releases. Expose read-only releases through one shared query library with CLI and MCP adapters. A hosted HTTP API is an optional later distribution choice. Build new releases away from readers, verify them, then atomically promote a release pointer. Retain old releases for citations and rollback. Git remains a reproducible distribution/audit representation and a candidate read backend; raw objects and source observations retain evidence authority.

Start with a modular Python application plus a separate worker process. Retain SQLite for immutable read-heavy releases until measurements justify another serving database. Avoid splitting into microservices or adding a search cluster before there is a demonstrated need.

## M0 — Freeze the production contract

Define first supported audience, corpus, tools, freshness goal, supported query semantics, citation format, and expected workload. Separate source-native views from inferred product-section answers. Specify missing/partial/conflicting evidence, dates, gaps and ambiguity. Choose measurable latency, availability and restore targets before deployment.

Exit: an approved v1 backend contract and acceptance checklist. Initial production scope is the existing release, not the entire national universe. Proposed API features: family selection, coverage, versions, native sections, show, diff, section presence and literal phrase history, each explicitly scoped/versioned.

## M1 — Make the software maintainable and portable

Inventory active modules and dependencies; separate archive ingestion, parsing, derived interpretation, query logic, export and adapters. Keep frozen historical engines as replay dependencies rather than editing them. Remove desktop-path assumptions from active configuration. Package every runtime dependency, including builders currently importing workspace tools. Add documented configuration, schema migrations, deterministic build inputs and CI.

Exit: a clean checkout installs and runs in an isolated environment without Desktop paths, hidden workspace files or manual setup. A small offline fixture exercises ingestion-to-query-to-export. Current protected outputs remain stable.

## M2 — Make evidence durable and recoverable

Copy raw objects and manifests to durable object storage with hash verification and originals retained. Back up source-observation databases consistently; retain failed requests/history evidence as well as successful ZIPs. Separate authoritative evidence from disposable indexes/caches. Define retention, integrity audits, permissions and encryption. Perform an actual restore onto a clean environment.

Exit: a restored archive reproduces selected evidence and a working query release within declared recovery targets. A backup log alone is insufficient. Independent hash audits detect corruption; restoration never manufactures unknown history.

## M3 — Automate repeatable acquisition and release builds

Generalize date-specific recovery/build scripts into configured jobs. Implement idempotent updates, versioned parser selection, checkpoints, quarantine, bounded retries, one global acquisition budget and polite rate control. Preserve late arrivals and competing bytes. Keep parsing/network work bounded. Build derived indexes and Git shards in staging; validate before atomic promotion. Record job and release lineage.

Exit: mocked source changes, duplicates, out-of-order versions, pagination changes, bad identities and interrupted workers recover without duplication or evidence loss. A scheduled incremental cycle creates a new immutable release and can be rerun safely. Do not test crash recovery by damaging protected production evidence.

## M4 — Complete the query engine and establish correctness

Build a pinned derived query release for the full supported corpus. Define and integrate conservative section matching/diffs, repeated/uncoded sections and ambiguity. Keep source-native access automatic; inferred context is an additional layer. Precompute reusable histories/diffs and add appropriate search indexes. Share one implementation across adapters. Provide explicit source citations, release IDs, coverage and bounded pagination.

Validate exact source preservation, date/gap/partial behavior, old-version citations, adapter agreement and representative historical readings. Use targeted independent review when introducing or materially changing inference; do not manually label every document. Preserve the original 59/60 comparison and separate adjudication; neither establishes full-corpus recall.

Exit: every supported operation works across the declared corpus with specified unknown/error behavior. Ambiguous identity is disclosed, not silently resolved. No release promotes changed interpretation without classified regressions and a versioned rule/index record.

## M5 — Expose a stable backend API and MCP

Specify a versioned HTTP API and OpenAPI contract over the shared query library. Add typed requests/responses, stable errors, release selection, pagination, text limits, timeouts/cancellation, health/readiness and caching keyed by release. Keep ingestion/admin operations separate from public reads. Define authentication, quotas and rate limits according to deployment exposure. Bring CLI and MCP into agreement; test real client initialization and calls.

Exit: automated clients can discover and call supported endpoints, receive pinned citations, page large results, and handle failures predictably. Existing stdio MCP support is not proof of a production remote MCP transport.

## M6 — Prove reliability, security and capacity

Measure realistic workloads and worst-case documents before selecting final infrastructure. Exercise concurrent readers, release swaps, cold starts, expensive phrase searches and repeated-section diffs. Inspect query plans, memory and output sizes. Add structured logs, job/release metrics, latency/error monitoring, disk/request-budget alerts and actionable runbooks. Review XML/ZIP bounds, untrusted source content, injection, path traversal, secrets, dependencies and access boundaries.

Exit: agreed service targets pass under declared load; dangerous/expensive requests are bounded; failures trigger useful alerts; rollback and restore drills succeed. Preserve measured limits rather than inventing a scale claim from unit tests.

## M7 — Stage, release and operate

Deploy a reproducible runtime to staging with production-like storage and restricted access. Pin images/dependencies and release manifests. Smoke-test current and old citations, failure handling, backup restoration and rollback. Run a monitored soak period. Resolve attribution, third-party rights, license, distribution and current hosting limits before public redistribution. Promote only an explicitly approved release; keep prior releases available.

Exit: repeatable deploy/rollback, completed restore/incident drills, observable incremental updates, documented on-call/ownership and a controlled launch decision. Frontend can then consume the same stable API without redefining backend semantics.

## Order and parallel work

Critical path: M0 → M1 → M2/M3 → M4 → M5 → M6 → M7. Storage backup planning can proceed alongside code cleanup. API contract design can proceed during query work, but the API should not implement a second query engine. Instrumentation starts early; M6 is its evidence gate, not its first implementation. Rights/distribution research can proceed in parallel with engineering.

Private backend alpha: M0–M5 with a tested restore path and explicit limitations. Public production v1: M0–M7 for the declared corpus. Frontend is outside both backend implementation sequences.

## Immediate next block

Do M0 and the M1 inventory first: specify v1 promises and targets, map active versus frozen code, identify desktop-path/runtime dependencies, and produce a concrete refactoring plan with protected replay checks. Then implement packaging/configuration and one fixture-based end-to-end build. Avoid starting more collection, a new database platform, corpus-wide word blame, advanced annotations, RxNorm merging or FDA research analyses before the core backend contract is stable.

## Owner clarification: global-law parity first (November 6)

The owner wants reasonable architectural parity with lexifina/global-law, with justified domain differences. Current main inspected at d7f0fa1efc5b2e3b9b07b6ac585628478be0a6d8 uses Git-backed dated trees and git grep for law-text search, git log message search for search_changes, and derived per-corpus history/file indexes cached on disk in its own GLX2/pickle representation. These are not a SQLite/FTS search database. GitRx already has SQLite evidence and derived native indexes; retain them, but do not assume a new centralized full-text platform is a production prerequisite. M4 means completing query capabilities: first compare direct Git reading plus bounded caches with the existing SQLite route, verify result parity, and benchmark representative queries. Add further materialized search/diff indexes only where measurements justify them. An HTTP API/hosted service is a separately chosen distribution milestone, not necessary merely to mirror global-law's local CLI/MCP model.

## Query comparison completed (November 6)

[Measured comparison](../reports/query-backend-comparison-20261106/REPORT.md):
all 51,357 version metadata records agree, sampled section/filter/diff/date
checks agree, and five literal searches agree across all 7,239 latest versions.
Git batch reads were faster for the measured workloads, with slower history-index
startup. Full latest-corpus five-phrase scans still took about 9.4 seconds via
Git versus 15.1 via metadata/payload reads. Proceed with a shared native reader
interface and bounded Git caches; retain evidence SQLite. Before selecting search
acceleration, set latency limits and compare a text-only projection/Git search
against optional local FTS with equivalent literal semantics. This experiment
completes the backend comparison subtask, not all of M4 or production qualification.


## Backend foundation implemented (November 6)

The owner authorized M0, M1 and the shared-reader recommendation. The initial
[native v1 contract](PRODUCTION-CONTRACT.md) now fixes scope, tools, citations,
freshness, uncertainty and qualification targets. The
[portable package guide](BACKEND-PACKAGING.md) inventories module lanes, explicit
configuration and additive schema policy. `gitrx-native` shares query logic across
SQLite/payload and pinned Git adapters with bounded LRUs. `gitrx-selftest` builds
a synthetic ZIP/parser/SQLite/16-shard release using installed modules. A wheel
and clean-environment evidence are in the
[foundation report](../reports/backend-foundation-20261106/REPORT.md).

M0 is specified; M1's production-facing package and offline acceptance path are
implemented. Historical acquisition/validation stage drivers remain a separate
research lane rather than silently becoming supported production entry points.
Their orchestration migration belongs with M3. Remote CI, broad workload
qualification, restore drills and hosted deployment are not completed by this step.

## Owner priority override: ship the main workflow (November 6)

Prioritize usable label-history features over further infrastructure qualification.
The functional recovery drill was stopped at the owner's steering; no completed
restore is claimed. Independent backup copies are out of scope by explicit owner
instruction. Next implementation should deliver the family → historical label →
section comparison → first-observed wording workflow through CLI/MCP, with pinned
citations and explicit coverage/date semantics. Use focused checks for changed
behavior; defer additional packaging, backup and broad qualification work unless
it blocks that workflow. Frontend remains last.


## Core workflow delivered (November 6)

[CLI/MCP workflow](CORE-WORKFLOW.md): name resolution, observed-publication
historical reads, conservative native section comparisons and first-observed
wording now run across the expanded release through the shared library.
[Real MoviPrep examples and focused tests](../reports/core-workflow-20261106/REPORT.md).
Native context/coverage qualifications remain explicit; no new infrastructure
was required for this slice.


## Milestones 3 and 4: native v1 implementation (November 6)

[Configured release cycles](RELEASE-CYCLES.md) now handle acquisition through
immutable staging and atomic selection, with focused changed-source/restart tests
and a successful live no-change MoviPrep cycle. The native query workflow includes
coverage and source-code section history through CLI/MCP (ten tools).
[Delivery evidence](../reports/release-cycle-20261106/REPORT.md).
M2 remains a deliberately stopped functional drill; independent copies are out of
scope. No recurring scheduler, hosted service, full-corpus interpretation or broad
search latency qualification is implied by this delivery.
