# Start here

GitRx preserves DailyMed label versions and lets researchers inspect their captured
history. Raw bytes and source observations are evidence; Git exports and search
indexes are derived. Choose a label family by setid, keep release pins in citations,
and distinguish publication observations from effective dates. Publication of the
code and data buckets is pending (see the [README](../README.md)); licensing is in
[LICENSING.md](../LICENSING.md).

## Use the product

- [README](../README.md): scope, buckets, configuration, quick start.
- [Core workflow](CORE-WORKFLOW.md): resolve, versions, read, compare, literal history.
- [MCP](../MCP.md): assistant access; [installation](BACKEND-PACKAGING.md).
- [Limitations](../LIMITATIONS.md): coverage, extraction and interpretation boundaries.
- [Accuracy in plain language](ACCURACY-IN-PLAIN-LANGUAGE.md), [reasoning](VALIDATION-REASONING.md),
  [verify it yourself](../VERIFY.md).

## Work on the project

- [AGENTS](../AGENTS.md): development and evidence rules.
- [Authoritative parser plan](AUTHORITATIVE-PARSER-PLAN.md), [Step F gap repair](STEP-F-GAP-REPAIR.md),
  [product roadmap](CORE-PRODUCT-ROADMAP.md).
- [Document map](DOCUMENT-MAP.md), [repository layout](REPOSITORY-LAYOUT.md),
  [update jobs](../ops/UPDATES.md), [2026-11-11 cleanup](CLEANUP-2026-11-11.md).

Code map: shared native behavior lives in `gitrx_backend` (CLI, MCP, queries, readers);
the parser, change records, rebuild, SrLC grading and update jobs in `gitrx_core`;
the legacy fixture-release builder used by `gitrx-selftest` in `gitrx` and
`gitrx_services`. Interfaces use the shared queries.
