# Bounded native queries and accelerated literal search

Native query methods now accept `timeout_seconds` (default 10, range 0.01..60).
CLI places it before the command; MCP accepts it in tool arguments. Expiry is a
non-evidence error, never an empty search or a claim that wording was absent.
CLI includes reader initialization in its deadline. MCP reader initialization is
separate; tool deadlines start at dispatch. The runtime supports POSIX/macOS/Linux
main-thread workers. Threaded library use fails explicitly; run it in a dedicated
main-thread process. This is not a qualified multi-tenant HTTP service.

```sh
./label-history --timeout-seconds 5 search 'tendon rupture' --max-families 7239
```

Search also accepts `--time-budget-seconds` (default 2, range 0.01..55). It yields
at safe section/family boundaries with `stop_reason`, `partial`, and `continuation`.
A payload read can overrun the cooperative budget; the outer query deadline remains
active. If that deadline fires, no partial answer is returned: retry the same cursor
with narrower bounds or a larger deadline. A response completed under a cooperative
budget can be resumed without skipping or duplicating hits:

```sh
./label-history search 'tendon rupture' --max-families 7239 --cursor 'RETURNED_TOKEN'
```

Keep the exact phrase and immutable release. Tokens bind both; do not supply numeric
offsets with a token. Tokens are positions, not authenticated evidence of earlier
scanning. Result limits and family bounds also yield continuation. Completion means
`partial=false` and no continuation, not an empty page. Older numeric offsets remain
supported, but callers must pin their phrase and release themselves. Ctrl+C cancels
CLI work; MCP processes remain synchronous and do not implement in-flight JSON-RPC
cancellation. Time limits bound their active tool work.

## Optional text projection

An immutable SQLite projection stores casefolded latest-version section text,
indexed by family and position. Literal `instr` matching preserves punctuation,
spaces and Unicode casefold behavior; this is not token/semantic search. Positive
candidates are re-read through the selected original adapter for text and citations.
The projection uses disk, not a new database service. Existing source archives,
native indexes, Git refs and the default configuration are unchanged.

Build a fresh projection directory explicitly (never during a read):

```sh
.venv/bin/python -B -m gitrx_backend.search_index --config /path/to/backend.json --output /new/projection
```

Add `search_index` (path to `search.sqlite`) and `search_index_sha256` (from
`PIN.json`) to a separate backend configuration. Paths resolve relative to that
configuration. Reads verify the file hash, snapshot, format and Unicode version;
mismatch fails rather than silently falling back. New releases need new projections;
release jobs currently emit configurations without projection settings. Rebuilding
this optional accelerator is separate from preserving old releases/citations.

The measured local configuration is
`reports/query-limits-20261106/accelerated-config.json` (SQLite default). Use:

```sh
GITRX_CONFIG="$PWD/reports/query-limits-20261106/accelerated-config.json" ./label-history search 'tendon rupture' --max-families 7239
```

Git remains selectable with `--backend git`, retaining Git citations; Git history
lookups still cost more on broad scans. The acceleration measurements qualify the
SQLite route separately rather than assuming equal throughput for both adapters.

[Tests and measured evidence](../reports/query-limits-20261106/REPORT.md).

## Native 0.6.1 response paging and transport

`versions` returns at most 200 versions per page; `--limit` and `--offset`
select another page and `next_offset` identifies the next one. `show` and dated
reads return at most 100 matching occurrences by default (maximum 200), with
`--section-limit`, `--section-offset`, and `next_section_offset`. These occurrence
positions are separate from `--offset`, which pages characters within an exact
`--ordinal`. Pin the same release and filters while following pages. Character
truncation remains explicit on each returned occurrence.

Native MCP rejects invalid Unicode scalar values and messages nested deeper than
64 levels. Responses use safe JSON escaping and are limited to 1 MiB for the whole
serialized wire response, including metadata and both text/structured forms.
An oversized answer returns `ResponseTooLarge`, its release identity, and
`evidence_answer=false`; it is not a partial evidence answer. Retry with smaller
page/text bounds. Existing query deadlines do not cover response serialization.
Native 0.9.0 supersedes the original synchronous transport with [responsive dispatch and cancellation](MCP-CANCELLATION.md).
