# The Git export

The Git export is the whole release as ordinary Git repositories, so you can use `git log`, `git diff`
and `git grep` on label history. It is a rebuildable copy of the release `spl-sections-0.2.4`; the raw
files and the release databases remain the authority. Nothing here is pushed anywhere automatically.

## What you get

- Eight bare repositories, `gitrx-labels-00-1f.git` through `gitrx-labels-e0-ff.git`. A label family
  lives in the repository whose range contains the first two hex characters of its setid. Branch `main`, one linear history.
- A `gitrx-labels-meta` folder with a README, `MANIFEST.json`, and `map/*.jsonl.gz` that maps each (setid, version) to its repository and commit.
- One commit per captured version. Each commit writes two files under `<setid>/`:
  - `label.md`: the sections in document order. Each has a comment with its ordinal, code, title and length, a heading
    `## <LOINC code> | <title>`, then the text exactly as stored.
  - `meta.json`: setid, version, raw-file hash, parse status, and both dates.
  A version with no retained file has `meta.json` only, and its `label.md` stays as the previous version left it.

Try it (adjust paths to where the export is):

```sh
git -C gitrx-labels-80-9f.git log --oneline -- SETID/label.md
git -C gitrx-labels-80-9f.git log -p -- SETID/label.md
```

## Install only some families

`./label-history installation-plan SETID...` shows which shard repositories cover the families you chose. To clone just those:

```sh
python -m gitrx_backend.selective_install --config FILE --setid SETID --output /new/selected-installation
```

It refuses an existing destination and leaves failed installs marked `INCOMPLETE`. A shard also holds other families.
A partial install names the shards it lacks, and a search that would cross a missing shard fails instead of reporting zero hits.
There is no hosted download.

## Dates

`meta.json` keeps `dailymed_publication_date` and `spl_effective_date` separate. Neither is an FDA approval date.
The commit date is the posting date, adjusted only so versions of one family stay in version order (carried forward when
missing; `meta.json` records `key_date_source`). Do not read commit dates as when text was introduced; first observed is not introduction.

## Caveats

- Label text is DailyMed/NLM content, not relicensed by GitRx ([LICENSING.md](../LICENSING.md)). GitRx formatting and metadata are CC BY 4.0.
- Parsed text is not clinically validated, and a version can lack text because extraction was partial.
- Titles are the latest DailyMed listing titles.
- The export checks only that it reproduces the release faithfully (200 sampled versions plus every version without a file), not that parsing is accurate.

## For maintainers: rebuild

```sh
GITRX_DATA_ROOT=... .venv/bin/python -m gitrx_core.git_export build --groups 8
```

Also available: `determinism`, `verify`, `meta` and `complete` steps (see `--help`). The build refuses to overwrite existing
repositories or run with less than 60 GiB free, and the same input gives identical commit ids. The published export was built by
`gitrx_core/git_export.py` at commit 26d7281 (module sha256 `6406b59c...512123a`, as in its `MANIFEST.json`); later commits only change how a pipe is closed for Python 3.11, not what is written.
