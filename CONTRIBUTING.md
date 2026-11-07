# Contributing to GitRx

Start with [README](README.md), [the native contract](docs/PRODUCTION-CONTRACT.md)
and [AGENTS](AGENTS.md). The source software and generated label-history Git
repositories are separate. Propose software changes in the source project;
generated label text is not a place for hand-written corrections.

## Report a defect with evidence

Include the selected release/snapshot, setid, SPL version and source occurrence
where relevant, the command and filters, actual versus expected behavior, and
an official-source reference or retained raw hash supporting the concern. State
whether the problem is acquisition, extraction, matching, interpretation, client
presentation or documentation. Similar drug names do not establish the same
label family. Distinguish publication observations, SPL effective dates and
retrieval timestamps.

Route a correction to the layer that owns it:

- Acquisition: retain the original response, failure and competing observations;
  admit separately verified evidence through a new selected build.
- Extraction: fix the parser and diagnostics through an explicitly versioned
  derived build. Preserve original bytes and old parser outputs.
- Interpretation/matching: specify the changed rule, classify regressions and
  retain the prior rule/snapshot. Do not promote native source codes into stronger
  clinical claims without the necessary evidence and validation.
- Annotations: keep them attributed and separate from source label text.
- Clients: use shared queries so CLI, MCP and browser do not invent different
  evidence answers for the same selection.

## Keep changes reviewable

Describe the concrete problem, resulting behavior and relevant verification. Add
focused tests for changed behavior, especially gaps, repeated sections, partial
extraction, old citations, paging and bounded failures. Check source-backed examples
when semantics change; agreement between wrappers of one algorithm is insufficient
independent evidence. Use the [performance record](docs/PERFORMANCE-RECORD.md) for
measured bottlenecks and compare equivalent workloads before adding infrastructure.

Preserve immutable raw objects, source observations, old releases/refs, frozen
rules, goldens and historical reports. Do not rewrite stored SPL text to make a
query pass. Keep original blind labels, predictions and later adjudication
separate. Do not open sealed answers or run excluded held-out packet tests.
Clinical validation and full-corpus recall are not established by unit-test counts.

Document AI assistance and what was actually human-reviewed when relevant to the
change. Repository code availability does not decide redistribution rights for
labeler-supplied text/images. Licensing, source attribution and public distribution
remain separate decisions; contributions should not select a license or publish
data as an incidental step. No contribution workflow installs recurring collection,
sends external messages, or changes a selected live release merely to run checks.
