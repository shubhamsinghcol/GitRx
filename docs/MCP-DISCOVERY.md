# Discover the configured native release

Start an assistant workflow with `describe_release`, then resolve a family name,
inspect coverage/versions, and read or compare the selected captured evidence.
The description gives the configured snapshot, served family count, supported
operations, metadata/search availability, matching/date rules and citation limits.
It does not scan every label or infer source freshness from file times or dates.
The CLI equivalent is `describe-release` over the same query library.

Native MCP advertises tools, resources and prompts through the existing
initialize-based protocols. It does not add a remote transport, subscriptions,
network collection, newer protocol claims or in-flight cancellation.

## Packaged resources

`resources/list` returns three resources pinned to the configured snapshot:

- `gitrx://release/SNAPSHOT/description`: the same structured release description.
- `gitrx://release/SNAPSHOT/capabilities`: supported native operations and purposes.
- `gitrx://release/SNAPSHOT/limitations`: evidence and interpretation boundaries.

These summaries ship in the installed package. Reading them does not open an
arbitrary workspace document or return the whole acquisition manifest. A server
only reads its configured release; `current` is not an accepted resource alias.

`resources/templates/list` exposes an exact native-occurrence template:

```text
gitrx://release/{snapshot}/label/{setid}/{version}/{ordinal}{?offset,max_chars}
```

For example, substitute the server's actual snapshot/setid, then use
`?offset=0&max_chars=5000`. `offset` and `max_chars` page characters within that
exact occurrence through the existing shared query. The returned JSON retains
release identity, raw hash, occurrence location, source metadata and truncation/
continuation details. Use its next character offset for another part. A mismatched
snapshot, unknown path, duplicate query parameter or unsupported parameter is an
error, never a fallback to a newer release. Response and query bounds still apply.

## Research prompts

`prompts/list` advertises three user-selected workflows:

| Prompt | Parameters | Workflow |
|---|---|---|
| `label_on_date` | `family`, `date` | Resolve ambiguity, inspect coverage, read by observed publication cutoff |
| `compare_versions` | `family`, `version_a`, `version_b` | Compare captured versions conservatively and follow pagination |
| `trace_wording` | `family`, `phrase` | Trace literal observations and unknown intervals across the captured history |

`prompts/get` validates the named fields and returns research instructions plus
quoted parameter data. It never executes a prompt, invokes a model, resolves a
family or modifies evidence. Label text and annotations remain data, not assistant
instructions. Prompts require retaining ambiguity, evidence limits and citations;
they do not turn captured text into clinical advice or actual addition dates.

Native 0.9.0 includes version_changes/search_changes in the capability registry; [change inspection](CHANGE-INSPECTION.md) documents them. Discovery controls remain responsive during queries through the [stdio transport](MCP-CANCELLATION.md).
