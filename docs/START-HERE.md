# Start here

GitRx preserves DailyMed label versions and lets researchers inspect their captured
history. Raw bytes and source observations are evidence; Git exports and search
indexes are derived representations. Choose a label family by setid, keep release
pins in citations, and distinguish publication observations from effective dates.

## Use the product

- [README](../README.md): supported entry points and scope.
- [Core workflow](CORE-WORKFLOW.md): resolve → versions → read → compare → literal history.
- [Browser workflow](BROWSER-WORKFLOW.md): the local interface.
- [MCP](../MCP.md) and [installation](BACKEND-PACKAGING.md): assistant and packaged access.
- [Limitations](../LIMITATIONS.md): coverage, extraction and interpretation boundaries.

## Work on the project

- [AGENTS](../AGENTS.md): development and evidence rules.
- [Product roadmap](CORE-PRODUCT-ROADMAP.md): priorities and remaining gates.
- [Document map](DOCUMENT-MAP.md): maintained guides and historical records.
- [Repository layout](REPOSITORY-LAYOUT.md): source, local state and retained evidence.
- [Operations](OPERATIONS.md): finite backfill notes and how to interpret dated status.

The expanded native reader and the interpreted pilot have separate semantics.
Shared native behavior belongs in `gitrx_backend`; parser/archive modules live in
`gitrx`, and service builders in `gitrx_services`. Interfaces should use shared queries.

Earlier long-form architecture explanations and delivery logs are preserved in the
[November 8 navigation archive](history/2026-11-08/README.md). They describe their
capture time; consult implementation, selected manifests and live status for current facts.
