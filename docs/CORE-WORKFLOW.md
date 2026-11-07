# Use the label-history workflow

> Native runtime 0.6.0 adds [whole-query deadlines, resumable search and optional acceleration](QUERY-PERFORMANCE.md). Historical performance/timeout descriptions below are superseded by that guide and its measured report.

Run these from the GitRx folder. `label-history` uses the pinned expanded release;
`--backend sqlite` selects the faster metadata/payload route when desired. Git is
the default in this workspace's configuration and provides label/meta commit citations.

```sh
./label-history resolve MOVIPREP
./label-history versions 8ff46193-2a3b-4de0-9572-775dda8cd8b2
./label-history show 8ff46193-2a3b-4de0-9572-775dda8cd8b2 --date 2025-12-24 --code 34066-1
./label-history diff 8ff46193-2a3b-4de0-9572-775dda8cd8b2 1 46 --code 43685-7
./label-history when-added 8ff46193-2a3b-4de0-9572-775dda8cd8b2 seizures --code 43685-7
```

The example setid is Salix's MoviPrep family. Resolving MOVIPREP returns multiple
families; choose one setid instead of merging their histories. A uniquely matching
name can be passed directly to show/versions/diff/when-added. Ambiguous names return
an error with candidates. `families --search` remains available for browsing.

| Source section code | Meaning |
|---|---|
| 34066-1 | Boxed warning |
| 43685-7 | Warnings and precautions |
| 34071-1 | Warnings |
| 34067-9 | Indications and usage |
| 34070-3 | Contraindications |
| 34068-7 | Dosage and administration |

Code filters retain all native occurrences, including repeated ones. The tool does
not silently classify those occurrences as a particular product-section context.

`show --date` selects the greatest captured SPL version with a recorded DailyMed
publication date on/before the cutoff. Effective dates are displayed separately.
Unknown dates, eligible versions and publication-order conflicts are reported.
Exact reads remain `show SETID VERSION`; `--ordinal` and character pagination work
with either form.

`diff` now matches sections using the existing code/system/ancestry pairing rules,
with equal text and unique headings helping match shifted occurrences. Ambiguous
repeated sections remain unmatched. It returns word changes, locations, text hashes
and both citations. `--limit`/`--offset` page comparisons; `--max-chars` bounds text.
The old `diff ... --ordinal N` mechanical position diagnostic still works.

`when-added` (also `first-observed`) reports the first matching captured SPL version,
matching occurrence locations/excerpts, publication/effective dates, the preceding
captured version's citation, and gaps. It does **not** establish the actual addition
date or complete historical absence. `--case-sensitive` changes literal matching;
`--max-versions` bounds history scanning and exposes an incomplete scan.

## Native MCP

Launch `./label-history-mcp` for the same shared tools over stdio. The installed
entry point is `gitrx-native-mcp` after installing the updated package. For a local
MCP client, the command is `/Users/shubham/Desktop/GitRx/label-history-mcp`; optional
arguments `--backend sqlite` select that adapter. No separate server or index build
is required. This server exposes resolve, families, versions, show, show_date,
compare, first_observed, search, coverage and section_history across the expanded native release. The accepted
interpreted pilot MCP remains available separately.

A captured real stdio session and examples are in
[the feature report](../reports/core-workflow-20261106/REPORT.md).


For incremental updates and the new coverage/section-history commands, see
[release cycles](RELEASE-CYCLES.md).
