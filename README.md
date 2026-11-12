# GitRx: captured DailyMed label history

GitRx lets researchers find a drug-label family, read captured versions, compare
sections, trace the first observed wording, and copy a release-pinned citation.
CLI, local MCP and a plain local browser use the shared native query library.

[Start here](docs/START-HERE.md) · [Document map](docs/DOCUMENT-MAP.md) ·
[DailyMed collection protocol](docs/DAILYMED-ACQUISITION.md).

This private source repository contains software, current guides and synthetic
engineering regressions. Raw archives, source databases, generated Git histories,
runtime configurations and validation answers remain in the local evidence
workspace. Links to `reports/` and `cohorts/` refer to those retained local
artifacts, not files bundled with this source checkout. A clean source checkout
can run `gitrx-selftest` offline after installing dependencies; real label queries
require a separately supplied pinned archive configuration.

**How accurate is it, in plain terms?** Checked against FDA's own list of
drug-safety label changes: when FDA's new wording appears in a DailyMed label
version, GitRx catches it in the right section about 98 times out of 100. Starting
from FDA's whole list, GitRx shows the change in DailyMed around FDA's approval date
94 times out of 100 by section and 79 by exact wording. The gap is mostly because
DailyMed and FDA keep different clocks and DailyMed lacks some label versions.
About 80 of every 100 changes GitRx reports are on FDA's list (87 allowing for
timing). See [plain-language accuracy](docs/ACCURACY-IN-PLAIN-LANGUAGE.md).

**Default release (from 2026-11-11):** the Step E deterministic rebuild
`spl-sections-0.2.3__srlc-changes-0.3.2` (158,955 families / 488,016 captured
versions, 82 without retained raw; snapshot id = SHA-256 of its COMPLETE.json,
`bbe9223a...`). It lives on the external SSD and is pinned by
`runtime/native-config-rebuild-0.2.3.json`; with the SSD absent, queries fail
with a clear error (no fallback). It adds SrLC-style change records
(`change-records`). Validation is limited: Freeze 2 sealed result parser text
98.30% / section 99.64% with the uncoded-box rule "seen"; the independent holdout
is pending. DailyMed dates are not FDA approval dates. The earlier release below
stays selectable with `GITRX_CONFIG=runtime/native-config.json`.

The November 6, 2026 expanded release (previous default, snapshot `1baa5bca...`)
contains **7,239 families and 51,357
retrieved known version targets**. All 15 exact recoveries are admitted.
NALTREXONE's upstream history inventory remains unresolved; known-target coverage
is not proof of complete historical coverage. This remains a local release
candidate; public distribution is pending.

## Start with the native workflow

From this prepared checkout:

```sh
./label-history describe-release
./label-history resolve MOVIPREP
./label-history versions 8ff46193-2a3b-4de0-9572-775dda8cd8b2
./label-history show 8ff46193-2a3b-4de0-9572-775dda8cd8b2 --date 2025-12-24 --code 34066-1
./label-history diff 8ff46193-2a3b-4de0-9572-775dda8cd8b2 1 46 --code 43685-7
./label-history first-observed 8ff46193-2a3b-4de0-9572-775dda8cd8b2 seizures --code 43685-7
```

A name can resolve to multiple setids. Choose one family; similar names do not
merge document lineages. `first-observed` (also `when-added`) identifies the first
match in captured SPL-version order, not a proven addition date. Source-code
filters retain repeated occurrences with unclassified context.

[Complete CLI/MCP workflow](docs/CORE-WORKFLOW.md) ·
[query limits and accelerated literal search](docs/QUERY-PERFORMANCE.md) ·
[native contract](docs/PRODUCTION-CONTRACT.md).

For installation outside this checkout, use the
[package and configuration guide](docs/BACKEND-PACKAGING.md). Installed entry
points include `gitrx-native`, `gitrx-native-mcp` and `gitrx-web`; explicit
configuration selects immutable data, with paths relative to its configuration.
The checkout wrappers use the configured release-cycle pointer when present,
otherwise the original selected release. Reads perform no acquisition.

## Browser and assistant access

The [local browser](docs/BROWSER-WORKFLOW.md) provides family selection, historical
reads, section comparisons, first-observed wording and copyable exact local
references. It runs on loopback and requires the retained archive/service to reopen
saved links; it is not public permanent hosting.

Launch `./label-history-mcp` for the native read-only stdio server over the expanded
release. [MCP.md](MCP.md) distinguishes this server from the accepted interpreted
pilot and provides setup and tool semantics.

## Evidence and interpretation

Exact response bodies and ZIPs are retained by SHA-256 alongside source-observation
databases. Pinned parser output preserves native section occurrences and tables.
Immutable derived releases supply SQLite/payload and Git readers; Git is a
reproducible read/distribution representation, not the entire raw evidence archive.

DailyMed publication observations, SPL effective dates and retrieval timestamps
have distinct meanings. Dated reads select the greatest captured SPL version with
a recorded publication observation on/before the cutoff, and disclose unknown
dates and ordering conflicts. Partial extraction, missing evidence, repeated
sections and unfinished scans limit negative conclusions.

The native workflow does not infer clinical meaning, FDA approval, legal
applicability, causation or what accompanied a dispensed product. The separate
`gitrx-current` / `gitrx_services.mcp` interpreted lane remains the accepted
20-family / 587-version pilot, with frozen v1.0.1 context rules and its own
validation boundary. Native expanded access does not expand that validation.

[Limitations](LIMITATIONS.md) · [source register](SOURCES.md) ·
[security and evidence boundaries](SECURITY.md) · [reproduction](REPRODUCE.md).

## Release operation and current work

`./release1-status` reports the expanded evidence release; `--legacy` shows the
original acquisition controller and its retained historical failures.
`./release-cycle status` identifies the configured native release. Status reads
do not restart collection. The [release-cycle guide](docs/RELEASE-CYCLES.md)
documents explicit refresh, build, selection and rollback commands. No recurring
scheduler is installed, and new source checks do not happen during queries.

[Historical build status](docs/BUILD-STATUS.md) · [current product roadmap](docs/CORE-PRODUCT-ROADMAP.md) ·
[project explanation](docs/START-HERE.md) · [document map](docs/DOCUMENT-MAP.md).

The [historical pilot workflow](docs/HISTORICAL-PILOT-WORKFLOW.md) preserves the
former README's stage narrative, commands and artifact links. Frozen reports and
protocols retain their original facts; their older deferred-feature statements
are not the current feature list.

Native 0.7.0 adds [shared evidence details and complete wording histories](docs/EVIDENCE-HISTORY.md).

Native 0.8.0 adds [MCP discovery](docs/MCP-DISCOVERY.md), [observed operational status and staged search acceleration](docs/OPERATIONAL-STATUS.md), and a [repeatable performance record](docs/PERFORMANCE-RECORD.md). See [contribution guidance](CONTRIBUTING.md).

Native 0.9.0 adds [responsive MCP cancellation](docs/MCP-CANCELLATION.md) and [version-change summaries and scoped changed-text search](docs/CHANGE-INSPECTION.md).

Native 0.10.0 repairs nested deadline cleanup and adds [maintained workload qualification](docs/QUALIFICATION.md) plus [bounded research helpers](docs/RESEARCH-HELPERS.md): citation checks, token replay, cohort-date inventories and selective local Git installations. [Verification and operating limits](reports/qualification-20261107/REPORT.md).

To audit the validation yourself, see [VERIFY.md](VERIFY.md) (one-command checks, `tools/verify_gitrx.py`) and [docs/VALIDATION-REASONING.md](docs/VALIDATION-REASONING.md) (the reasoning behind every number).
