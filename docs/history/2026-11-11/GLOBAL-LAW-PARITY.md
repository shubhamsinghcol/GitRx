# Global-law parity and deliberate differences

Inspected shallow, without submodules, at
`/tmp/gitrx-global-law-20261102`, commit
[`c8ee9368232b966f81ff3d9bbe3afef3613b94ce`](https://github.com/lexifina/global-law/tree/c8ee9368232b966f81ff3d9bbe3afef3613b94ce).
Review date: 2026-11-02 America/Chicago. Read README, MCP, REPRODUCE,
the builder/reader/server implementation, test definitions, visualisation
documentation/pipeline, manifest and representative US/Germany/France source
registers. No submodule histories were fetched and no full-corpus test pass
or hash rebuild was attempted. The clone has 107 pinned corpora and 209
country source pages; numbers belong to this commit.

## What changed since the planning brief

The current `law.py` and MCP.md say that from November 2 the meta tree grows
by fast-forward pin updates, with dated reading from each corpus's own
history. Old root commits supply legacy dates only when necessary. README,
REPRODUCE and the rebuild script still describe the earlier interleaved-root
rebuild/force-push architecture. Report this documentation split; do not call
the historical description the sole current design. GitRx keeps immutable
release histories and never takes legal-effect semantics from this project.

“48 tests” is the older reproduction claim. Static inspection finds 52
`test_*` methods in `tests/test_law.py` and 91 in `tests/test_mcp.py` at this
commit. These are method counts, not executed/parameterized test results.
REPRODUCE reports 339,326 commits in an earlier two-build comparison; a
depth-one clone cannot independently verify that historical count.

## Parity matrix

| Global-law feature | GitRx status | Plan / decision |
|---|---|---|
| Per-corpus repositories and SHA-pinned submodules; selective country downloads and explicit unserved coverage | Deterministic federated exporter; no distributed shards yet | 16 repositories by **first hex character** of setid plus a SHA-pinned meta repo; retain first-two-hex content directories. Let users fetch chosen shards and name everything unsearched. |
| One original corpus commit per dated event, provenance trailers; old meta chronology now supplemented by corpus readers | One commit per label version; provenance trailers built | Keep numeric per-family order and deterministic queue-head merge. DailyMed publication is not legal in-force time. |
| `manifest.json`: coverage, history kind, record end, verification caveats; `jurisdiction.json` tree | Coverage endpoint, census and immutable evidence snapshots | Per-family release manifest: listed, retrieved, archive-route, route-specific failures/not attempted/conflicts, history status, explicit-or-unknown label status, date range and snapshot pins. No unavailable-from-one-route shortcut. |
| Country SOURCES registers distinguish official/community sources, historical reach, API/bulk, licensing and checked/unknown evidence | Licensing captures and retrieval provenance; no final SOURCES.md | Document DailyMed listing/history/ZIP/archive routes, bulk/update files and FDA SrLC separately, with capture dates/hashes, scope, access policy and unresolved rights. Source availability is not redistribution permission. |
| 18 read-only MCP tools, three prompts, document resources and dated provision resource template | Five local stdio tools serving the accepted pilot | Add word credit, entry/exit history, families changed between dates, as-of inventory, literal search, text-change impact and bounded claim checking; prompts/resources and MCP.md. Expand only with explicit snapshot/coverage semantics. |
| CLI `world`, `show`, `blame`, `words`, `diff`, `today`, `log`, `laws` | first-occurrence, section-first-present, show, diff, any-of/section filters | Add `blame`, `words`, `world`, `between` through shared query functions; preserve CLI/MCP agreement. “Blame” means observed text provenance, never author/regulator responsibility. |
| Known readings, corpus-tool agreement, plain-git agreement and hostile-input protocol tests | 366-test historical permitted suite, later targeted suites, byte goldens and reproducible exports | Famous label-change fixtures with independently checked source expectations; MCP-vs-CLI agreement; missing/gap/date/partial safety cases. Do not run the five original held-out packet tests. |
| REPRODUCE: exact inputs/build/environment/hashes, manual decisions and AI-use disclosure | Build/export manifests and measured two-build equality | Write runnable offline rebuild recipe with pinned parser/rules/normalization, input hashes and expected commit/tree hashes; disclose AI-authored code/docs and what humans have actually reviewed. |
| “What it does not show,” record limits attached to answers | Local LIMITATIONS draft and query uncertainty | Maintain failed-route/unknown-context/image-only/date/anomaly caveats in every interface; describe unvalidated scope clearly. |
| Interactive visualisation suite, checked captions, raw analysis JSON, stable input clones and HEAD-keyed caches | No GitRx research visualisation suite | Exportable lag distributions, version cadence and partial/coverage maps; pin analysis inputs, carry intervals/denominators, distinguish family from version weighting. |
| Historical rebuild/force-push; current meta fast-forward policy | Immutable old tags and separate rebuilt release refs | Keep immutable releases. Record corrections through new snapshots/releases without erasing earlier evidence. |
| Local security design, launch configs and videos | Read-only service and Claude Desktop guide; no final SECURITY/MCP overview or launch polish | Write SECURITY.md and MCP.md, exercise bounded inputs, pagination/deadlines/cancellation, then polish after scientific and owner gates. Do not copy a protocol claim without testing it. |

## Implementation lessons

`law.py` reads explicit date trailers, not Git timestamps; it exposes history
kinds and correction spans. Word credit uses deterministic sequence alignment,
including heuristics for rewritten islands. MCP adds restored-word tracing,
size/time bounds and declared line-credit fallback. GitRx must define and
version its own alignment/return semantics: unchanged words after a gap cannot
prove original introduction, repeated sections need conservative pairing,
and restored text needs distinct first-observed and reintroduced provenance.

The server validates argument schemas, paths and reachable commits; avoids
shells, external diff/textconv drivers and unbounded regular expressions;
pages long text, states truncation, bounds calls and supports cancellation.
Its optional audit log hashes arguments with HMAC rather than storing them.
Treat label text and annotations as data, never instructions. These are useful
design patterns, not a claim that GitRx already implements each one.

`build.py` merges queues deterministically, pins source repositories, records
missing/failed preflight corpora and supplies a fixed build date. Its source
registers keep unconfirmed sources visibly unconfirmed. Adopt that honesty.
Global-law's law-specific applicability/citation/precedent tools do not
transfer directly: GitRx's analogous claim check can compare a literal
statement against retrieved label text, and its impact tool can enumerate
changed text/instances, never infer clinical, regulatory or causal impact.

Visualisations are a separate reproducible analysis layer. Stable source
clones and data-checked captions are useful; the bridge/model analyses are
not necessary for label-family history and authorize no identity merging.
Global-law's CC0 applies to its own repository, with corpus terms separate;
it supplies no license for DailyMed labeler-submitted text or images.
