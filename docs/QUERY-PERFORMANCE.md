# Query limits

## Time limits

Every query has a whole-query time limit: default 10 seconds, range 0.01 to 60. Put it before the command:

```sh
./label-history --timeout-seconds 30 search 'tendon rupture' --max-families 5000
```

Hitting the limit is an error, never an empty result and never evidence a phrase is absent. Retry with a narrower
query or a larger limit. Ctrl+C cancels a CLI query. The query runner needs macOS or Linux, in a main thread; it is not
a multi-user web service.

## Search in pieces

`search` also has a time budget (`--time-budget-seconds`, default 2, range 0.01 to 55). When it runs out, or a result or
family limit is reached, the answer includes `partial`, `stop_reason` and a `continuation`:

```sh
./label-history search 'tendon rupture' --max-families 5000 --cursor 'RETURNED_TOKEN'
```

Keep the same phrase and release. The token already holds your position, so do not also pass offsets. A search is complete only when
`partial` is false and there is no continuation; an empty page is not "none found". Tokens mark a position; they do not prove
earlier pages were scanned.

## Page sizes

- `versions`: up to 200 per page (`--limit`, `--offset`, then `next_offset`).
- `show` and dated reads: up to 100 sections per page by default, maximum 200 (`--section-limit`, `--section-offset`).
  `--offset` pages characters within one section selected with `--ordinal`.
- Text is limited to 200,000 characters per response; truncation is marked on each section.
- The MCP server refuses responses over 1 MiB and messages nested deeper than 64 levels. See [MCP](../MCP.md).

Follow pages with the same release and filters.

## Fast search

The default release ships a search index, so `search` across all 158,955 label families
returns its first page in under a second. The index only proposes candidates; every hit is
re-checked against the label text, so results are exactly those of a full scan (tested on 40
phrases: [proof](evidence/search-equality-proof-0.2.4.json)). Phrases shorter than 3 characters
use the full scan. To run without the index: `GITRX_CONFIG=runtime/native-config-rebuild-0.2.4.json`.

## For maintainers: optional search index

An optional prebuilt SQLite index of latest-version text speeds up literal search; matches are re-read from the original data for citations.
It is built explicitly, never during a read, and a hash mismatch fails instead of falling back:

```sh
.venv/bin/python -B -m gitrx_backend.search_index --config /path/to/backend.json --output /new/projection
```

Add `search_index` (path to `search.sqlite`) and `search_index_sha256` (from `PIN.json`) to a backend configuration. Each release needs its own index.
Measurements: [query-limits report](../reports/query-limits-20261106/REPORT.md).
