# GitRx: captured DailyMed label history

GitRx lets researchers find a drug-label family, read captured versions, compare
sections, trace the first observed wording, and copy a release-pinned citation.
The command line (`label-history`) and a local read-only MCP server share one
native query library. There is no web front end.

**Status: publication pending.** This repository is being prepared for public
release. Licensing is in [LICENSING.md](LICENSING.md) (code Apache-2.0; GitRx-created
data and documentation CC BY 4.0; DailyMed and FDA content is not relicensed).

[Start here](docs/START-HERE.md) · [Document map](docs/DOCUMENT-MAP.md) ·
[Verify the accuracy claims yourself](VERIFY.md) · [Limitations](LIMITATIONS.md)

## How accurate is it, in plain terms?

Checked against FDA's own list of drug-safety label changes (SrLC): when FDA's new
wording appears in a DailyMed label version, GitRx catches it in the right section
about 98 times out of 100. Starting from FDA's whole list, GitRx shows the change in
DailyMed around FDA's approval date 94 times out of 100 by section and 79 by exact
wording; the gap is mostly because DailyMed and FDA keep different clocks and
DailyMed lacks some label versions. About 80 of every 100 changes GitRx reports are
on FDA's list (87 allowing for timing). See
[plain-language accuracy](docs/ACCURACY-IN-PLAIN-LANGUAGE.md),
[the reasoning behind every number](docs/VALIDATION-REASONING.md) and
[VERIFY.md](VERIFY.md). This is a comparison with FDA's list on a sealed sample, not
independent clinical validation; the independent holdout is pending. The earlier
tranche1 "59/60" and "60/60" scores were AI-graded and are not validation
([correction](docs/VALIDATION-CORRECTION-2026-11-10.md)).

## What is in the product

- **Parser layer (SrLC-validated):** `gitrx_core` parses retained SPL ZIPs into native
  section occurrences (`spl_parser`, `normalize`, `section_groups`) and derives
  SrLC-style change records (`changes`). The modules that produce data are frozen and
  versioned; grading code (`srlc*`) and the verification kit are included. Plan:
  [AUTHORITATIVE-PARSER-PLAN](docs/AUTHORITATIVE-PARSER-PLAN.md).
- **Rebuild:** `gitrx_core.rebuild` deterministically rebuilds a release from retained
  raw artifacts plus the evidence database. Default release:
  `spl-sections-0.2.4__srlc-changes-0.3.2` (158,955 families / 488,016 captured versions,
  56 without retained raw; snapshot = SHA-256 of its `COMPLETE.json`, `8dc88e7a...`;
  config `runtime/native-config-rebuild-0.2.4.json`; older builds stay selectable).
- **CLI and MCP:** `label-history`, `label-history-mcp` (installed: `gitrx-native`,
  `gitrx-native-mcp`) over `gitrx_backend`. See [MCP.md](MCP.md) and
  [the workflow](docs/CORE-WORKFLOW.md).
- **Update jobs:** weekly DailyMed and monthly SrLC collection
  (`gitrx_core.dailymed_update`, `gitrx_core.srlc_update`), and gap repair
  (`gitrx_core.gap_repair`, [Step F](docs/STEP-F-GAP-REPAIR.md)). Collection only; see
  [ops/UPDATES.md](ops/UPDATES.md). Queries never collect, classify or promote.
- **Verification kit:** `tools/verify_gitrx.py` ([VERIFY.md](VERIFY.md)).
- **Offline self-test:** `gitrx-selftest` builds a tiny synthetic release (no data root,
  no network).

## Where things live (buckets)

| Bucket | Contents | Status |
|---|---|---|
| GitHub (this repository) | Code, tests, docs, verification kit | Publication pending |
| Sharded label-history Git export | Captured label versions as Git histories (derived, rebuildable) | Planned, not yet published |
| Researcher evidence on Hugging Face, with a Zenodo DOI | Raw-hash manifests, evidence database, rebuild, SrLC grading outputs, provenance bundle | Planned, not yet published |

Raw DailyMed downloads and large databases are not in Git. They live in an external
data root.

## Configuration

Real queries need a pinned release. The data root (raw evidence, evidence database,
rebuild, SrLC records) is located by `GITRX_DATA_ROOT` or by `data_root` in the
git-ignored `runtime/local.json`:

```json
{"data_root": "/path/to/GitRx-data"}
```

Release configurations (`runtime/native-config-rebuild-*.json`) pin a rebuild by hash;
relative paths resolve beside the config file and `${GITRX_DATA_ROOT}` expands to the
data root. `GITRX_CONFIG` selects a configuration for `label-history`. With the data
root absent, queries fail with a clear error and there is no fallback. Tests that need
the data root skip when it is absent.

## Quick start

```sh
python3 -m venv .venv && .venv/bin/pip install -e '.[test]'
.venv/bin/gitrx-selftest --output /tmp/gitrx-synthetic      # offline check
.venv/bin/python -m pytest -q                               # fixtures only
./label-history describe-release                            # needs the data root
./label-history resolve MOVIPREP
./label-history versions 8ff46193-2a3b-4de0-9572-775dda8cd8b2
./label-history show 8ff46193-2a3b-4de0-9572-775dda8cd8b2 --date 2025-12-24 --code 34066-1
./label-history diff 8ff46193-2a3b-4de0-9572-775dda8cd8b2 1 46 --code 43685-7
./label-history first-observed 8ff46193-2a3b-4de0-9572-775dda8cd8b2 seizures --code 43685-7
```

A name can resolve to several setids; choose one family, similar names never merge.
`first-observed` is the first match in captured SPL-version order, not a proven
introduction date. Publication observations, SPL effective dates and retrieval times
are distinct; none is an FDA approval date.

## Evidence and interpretation

Raw response bodies and ZIPs are kept by SHA-256 beside source-observation databases
and are the authority; Git exports and search indexes are derived. The native
workflow does not infer clinical meaning, FDA approval, legal applicability, causation
or what accompanied a dispensed product. Label text is data, never instructions.

[Limitations](LIMITATIONS.md) · [sources](SOURCES.md) · [security and evidence boundaries](SECURITY.md) ·
[contributing](CONTRIBUTING.md) · [what was removed in the 2026-11-11 cleanup](docs/CLEANUP-2026-11-11.md).
