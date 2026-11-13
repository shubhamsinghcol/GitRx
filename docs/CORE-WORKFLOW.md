# Query label history from the command line

Run these from the GitRx folder with `GITRX_DATA_ROOT` set (see the [README](../README.md)). Output is JSON.
Every answer includes the release id and raw-file hashes; keep them when you cite a result. Reads never fetch
data. Run `./label-history COMMAND --help` for all options.

## The usual path

```sh
./label-history describe-release                                  # what this release covers
./label-history resolve MOVIPREP                                  # name -> candidate families (setids)
./label-history versions 8ff46193-2a3b-4de0-9572-775dda8cd8b2     # captured versions and dates
./label-history coverage 8ff46193-2a3b-4de0-9572-775dda8cd8b2     # gaps in the captured history
./label-history show 8ff46193-2a3b-4de0-9572-775dda8cd8b2 --date 2025-12-24 --code 34066-1
./label-history diff 8ff46193-2a3b-4de0-9572-775dda8cd8b2 1 46 --code 43685-7
./label-history first-observed 8ff46193-2a3b-4de0-9572-775dda8cd8b2 seizures --code 43685-7
```

`resolve MOVIPREP` returns several families; choose one setid rather than merging them. A name that matches exactly
one family can be used in place of the setid; an ambiguous name returns the candidates as an error.
`families --search TEXT` lists matches.

Common section codes (LOINC) for `--code`:

| Code | Section |
|---|---|
| 34066-1 | Boxed warning |
| 43685-7 | Warnings and precautions |
| 34071-1 | Warnings |
| 34067-9 | Indications and usage |
| 34070-3 | Contraindications |
| 34068-7 | Dosage and administration |

A code filter returns every section carrying that code, including repeats. GitRx does not decide which product
section each belongs to.

## Download only what you need

`./label-history get NAME` downloads just the files for one drug from the public Hugging Face dataset
(`shubhams20/GitRx`): the release metadata, the name index and the one shard holding that family (tens of MB, not
12 GB). It is an explicit download command, the only one that uses the network; queries never fetch. Every file is
checked against its pinned SHA-256, written atomically and resumable. An ambiguous name lists candidates; rerun with a
setid. `get --dry-run` shows sizes, `get --all` takes every shard (~12 GB), and `get --search` adds the fast-search
pack (search index plus download-records lookup, ~3 GB). Searching wording across all labels needs every shard
(`get --all`); the search pack only makes that search fast. The data root is `GITRX_DATA_ROOT`, else
`runtime/local.json`, else `--dest DIR` (default `~/GitRx-data`); `get` prints the `export` line for next time.

`./label-history` and `./label-history-mcp` use the fast-search configuration when that pack is installed, and
otherwise the same release without it. Both pin the same release and give the same answers; without the pack,
whole-collection phrase search scans every shard and takes minutes. `GITRX_CONFIG` overrides the choice.

## Reading the SQLite files directly

Each shard (`shards/NN.sqlite`) is an ordinary SQLite file holding the families whose setid starts with `NN`.
Most columns are plain (setid, version, section number and title, LOINC code, section group, counts). To save
space, text is stored zlib-compressed: section text in `texts.z` (joined from `sections.text_h`), and change
wording in `changes.added_spans_z`, `removed_spans_z` and `moved_spans_z` (JSON lists of spans).

```python
import json, sqlite3, zlib
con = sqlite3.connect("GitRx-data/rebuild/spl-sections-0.2.4__srlc-changes-0.3.2/shards/8f.sqlite")
con.create_function("unz", 1, lambda b: zlib.decompress(b).decode() if b else None)
sid = "8ff46193-2a3b-4de0-9572-775dda8cd8b2"   # MOVIPREP (Salix)

# Section text of every captured version, Warnings and Precautions only
for version, title, text in con.execute(
        "select s.version, s.title, unz(t.z) from sections s join texts t on t.h = s.text_h "
        "where s.setid = ? and s.code = '43685-7' order by s.version, s.ordinal", (sid,)):
    print(version, title, text[:80])

# Wording added between consecutive captured versions
for a, b, title, spans in con.execute(
        "select from_version, to_version, section_title, unz(added_spans_z) from changes "
        "where setid = ? and added_spans_z is not null", (sid,)):
    print(a, "->", b, title, [x["text"][:60] for x in json.loads(spans)])
```

`./label-history` and the MCP server read the same files and decompress for you.

## Commands

**Read a version.** `show SETID VERSION` reads an exact version. `show SETID --date YYYY-MM-DD` reads the latest
captured version posted on DailyMed on or before that date; unknown dates and order conflicts are reported.
`--ordinal N` selects one section; `--max-chars` and `--offset` page its text; `--section-limit` and
`--section-offset` page sections. `versions` returns up to 200 versions per page (`--limit`, `--offset`).

**Compare two versions.** `diff SETID A B` pairs sections conservatively (by code, hierarchy, equal text and unique
headings) and reports word changes with locations, hashes and both citations. Sections that cannot be paired
unambiguously stay unmatched. Page with `--limit`, `--offset`.

**What changed in a version.** `version-changes SETID VERSION` compares it with the nearest earlier captured version
(use `--previous N` to pick another). The earliest captured version is a baseline, not proof its content was new then.
Follow `next_offset` for more rows. `--code`, `--limit`, `--offset`, `--max-chars` narrow the output.

**FDA-style change records.** `change-records SETID [TO_VERSION]` gives per-version-pair changes grouped by
type (`--group` BW, C, WP, AR, DI, USP, MG; `--from-version` picks the start). These are mechanical text comparisons,
not FDA or clinical statements.

**Find when wording appeared.**
- `first-observed SETID PHRASE` (also `when-added`): the first captured version containing the phrase, the version
  before it, and any gaps. This is not the date the wording was added.
- `phrase-history SETID PHRASE`: every captured version as match, no match, or unknown. Follow `--cursor`.
- `search-changes SETID PHRASE`: the transitions between consecutive captured versions where the phrase appears
  inside an edit (`--start`, `--end` bound the later version's posting date; `--max-transitions` 1 to 200).
  A to B to A has no net difference but two transitions.
- `section-history SETID --code CODE`: where a code appears across versions.
- `token-provenance SETID VERSION ORDINAL`: the earliest captured version each word run in one section can be traced to.
  It stops at gaps and unmatched sections, and is a stated alignment rule, not authorship.

Matching is literal and case-insensitive by default (`--case-sensitive` changes that). There are no synonyms.

**Search every family.** `search PHRASE` scans the latest captured version of each family.
It stops at a time budget (`--time-budget-seconds`, default 2) and returns a `continuation`. Pass it back with `--cursor`,
keeping the same phrase. Only a result with `partial=false` and no continuation is complete; an empty page with a
continuation says nothing about absence. `--max-families` limits how many families are scanned.
For a stricter overall time limit, put `--timeout-seconds N` (default 10, up to 60) before the command.

**Cohorts and citations.**
- `inventory-on-date YYYY-MM-DD SETID...` (or `--all-families`): which version each family had on that date by posting date.
  This is not a market-wide snapshot.
- `verify-citation SETID VERSION --snapshot SHA --raw-sha256 SHA [--ordinal N] [--quote TEXT]`: checks that the raw file,
  section and quote match this release. It checks evidence identity, not clinical truth.
- `installation-plan SETID...`: which Git shards cover chosen families (see [Git export](GIT-EXPORT.md)).

## How to read the coverage fields

Searches over changed text report `changed_text_coverage_complete`, true only for a finished one-page scan with no
unknown dates, no unaligned changes, and complete parsing at both ends of every transition. Later pages cannot vouch
for earlier ones, so combine the pages and their exclusion counts before claiming a scan was complete. Very large
section comparisons (over 200,000 characters combined) are skipped and counted rather than guessed.
Text is compared in its extracted form; check the original when table layout matters.

## Always remember

See [Limitations](../LIMITATIONS.md): dates are not FDA approval dates, first observed is not introduction, and
missing text is weak evidence of absence.
