# Native query contract (for maintainers)

What the shared query library (`gitrx_backend`, used by the CLI and MCP) promises. Output schema id: `gitrx-native-v1`.
Changing these semantics needs a versioned output decision and regression evidence, not a silent cache or schema change.

## Scope

- Serves one explicitly configured, immutable release, currently `spl-sections-0.2.4` (158,955 families, 488,016 versions).
  Every answer names its snapshot. Reading never downloads, ingests or promotes anything.
- Family names select setids; they never merge products into a clinical drug identity.
- The known targets do not prove every historical version was discovered.
- No hosted API, uptime or freshness guarantee. Not an adversarial multi-tenant service.

## Tool semantics

| Tool | Promise |
|---|---|
| resolve, families | Name or setid resolution; ambiguous families stay separate; bounded, ordered pages |
| versions | Captured versions of one setid in integer order, with raw hashes and each date observation |
| show, show_date | Exact version, or the greatest captured version posted by a cutoff; missing dates and order conflicts shown |
| compare | Conservative section pairing with bounded word changes; ambiguous repeats stay unmatched |
| first_observed, phrase_history | Literal matches in captured order with gaps and extraction uncertainty; no addition-date inference |
| search | Case-insensitive literal substring of section text, greatest version per family, explicit bounds and continuation |

Further tools (`version_changes`, `search_changes`, `change_records`, `coverage`, `section_history`, `inventory_on_date`,
`token_provenance`, `verify_citation`, `installation_plan`, `describe_release`) are described in [MCP](../MCP.md) and
[Core workflow](CORE-WORKFLOW.md).

## Rules every answer follows

- Section codes are source codes, not product-section context. Repeated sections keep distinct ordinals and ancestry.
  Codes, case, punctuation and symbols are not folded into semantic equivalence; casefolding is used only for case-insensitive
  matching and never reported as original-text offsets.
- Answers carry the snapshot and raw SHA-256. Git citations also carry the meta and label commits; SQLite citations carry the parse key.
  A query does not re-verify raw file bytes.
- Unknown versions and corrupted or mismatched inputs are errors, not evidence. An absent code returns zero occurrences,
  not a claim that a warning never existed. Empty searches cover only the families reported scanned.
- No clinical advice, FDA-approval inference, applicability date, causal explanation, semantic section history or first-addition inference.

## Bounds

32 MiB per decompressed payload or Git result; 30 s per Git command, one at a time per reader; history cache 1 to 128 families
(default 32, 4 MiB); section-blob cache 8 MiB; 200 returned hits; 200,000 returned text characters; search scans 128 families by default.
See [Query limits](QUERY-PERFORMANCE.md) for time limits and paging. Text limits do not cap JSON size or process memory.

## Not yet established

Full-corpus performance targets (warm exact `show` under 250 ms p95, bounded searches under 2 s p95), cold-start and
concurrent behavior, and deployed operation. See [Qualification](QUALIFICATION.md).
