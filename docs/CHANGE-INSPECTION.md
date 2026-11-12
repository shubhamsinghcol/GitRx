# Captured version changes and changed-text search

`version-changes SETID VERSION` summarizes that captured version against its
greatest lower captured predecessor. An explicit earlier `--previous` chooses a
different endpoint pair. The earliest captured version is a baseline without a
captured predecessor, not proof that its content was added then.

Rows retain conservative native section pairing, exact source locations, source
codes, original-text hashes, source XML hashes when available, both citations and
bounded word differences. Summary counts distinguish normalized content, raw text,
headings, position and source representation. Repeated or uncoded occurrences may
remain unmatched: this is ambiguous presence, not a proven section addition or
removal. A heading or source-format change can occur without a text-token change.
Follow `next_offset` with the same release, endpoints and filters. Character
truncation is explicit, separately from comparison-row pagination.

`search-changes SETID PHRASE` searches consecutive **captured transitions of one
family**, excluding its baseline. This differs from endpoint comparison: an
A→B→A sequence has no endpoint content difference but contains two transitions.
It searches exact literal source text at aligned character edits, not generated
commit messages or all text surrounding a changed section. A phrase crossing
unchanged context and an edit can match; unchanged words elsewhere do not match
merely because the same line changed. Insertions/deletions inside a literal can
break/create its match and are included. Punctuation and whitespace are preserved;
case-insensitive mode uses Unicode casefold with source-codepoint match offsets.

Use `--code` to select all occurrences with that native source code. Repeated
occurrences retain distinct locations; a match does not establish a stable section
lineage or clinical context. One-sided ambiguous occurrences are labeled as such.
Their text is not proof of authored insertion/deletion. Partial extraction can
omit changes even when observed matches are useful. Numeric version gaps are
visible, never silently treated as a continuous history.

Optional `--start` and `--end` are inclusive YYYY-MM-DD bounds on any valid recorded
DailyMed publication date of the transition's **later endpoint**. A version without
a usable date is excluded and counted explicitly when date bounds are requested.
These dates are not clinical applicability or actual addition/removal dates.

Search pages have result and transition-scan bounds. A zero-hit page with a
continuation is incomplete; follow its release/query-bound cursor. A page retains
at most 20 matching text spans per hit and reports omitted spans. Cursors bind
family, phrase, source code, case/date selection, release, metadata pin, Unicode
version and matching policy. They are positions rather than authenticated proof
that earlier pages were scanned. Exclusion counters apply to each page; combine
all pages and their exclusions before describing scan coverage.

Alignment refuses inputs above 200,000 combined source-text characters before
allocating alignment structures, trims exact shared prefixes/suffixes, then refines bounded token
blocks into character edits. Exceptionally large unresolved alignments are counted
as unexamined occurrences instead of returning speculative changed-text matches.
`changed_text_coverage_complete` is true only for a complete one-page traversal
with no unknown-date or unresolved-alignment exclusions and complete parser status
on both endpoints of every examined transition. `extraction_uncertain_transitions`
counts partial/failed/unknown endpoint status on this page.
`selected_extracted_text_scan_complete` separately describes traversal/alignment
of selected extracted text, without promoting its extraction quality. Later continuation pages
cannot attest earlier pages. `page_changed_text_coverage_complete` qualifies the
current page. Neither field establishes full original-source extraction fidelity.

All methods run under shared whole-query deadlines. A timeout is an explicit
non-evidence error; retry the same cursor with narrower work or a larger timeout.
No read operation collects source data, builds indexes or selects a new release.

## Examples

```sh
./label-history --backend sqlite version-changes 8ff46193-2a3b-4de0-9572-775dda8cd8b2 2 --limit 20
./label-history --backend sqlite search-changes 8ff46193-2a3b-4de0-9572-775dda8cd8b2 seizures --limit 10
```

Use `--previous`, `--code`, `--offset` and `--max-chars` for change details.
Changed-text search accepts `--start`, `--end`, `--case-sensitive`,
`--max-transitions` (1..200, default50) and `--cursor` for continuation.
(The local browser mentioned below was removed on 2026-11-11.) The equivalent MCP tools are `version_changes` and `search_changes`. The browser
provides **Version changes** and **Search changes** for the selected family.
Matching uses the native extracted `own_text` representation; verify original
occurrences when table/extraction representation matters.
