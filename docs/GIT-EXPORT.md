# Git export (gitrx-git-export-1)

A deterministic Git representation of the pinned rebuild release (`spl-sections-0.2.4__srlc-changes-0.3.2`,
COMPLETE.json sha256 `8dc88e7a...ce644b`). Built by `gitrx_core/git_export.py`; nothing is pushed or published
by it. The rebuild and the raw evidence stay the authority; this export is a rebuildable read representation.

## Layout
Output directory (under the external data root): `git-export/gitrx-git-export-1__spl-sections-0.2.4/`

- `repos/gitrx-labels-<lo>-<hi>.git` - bare repos, one per range of consecutive rebuild shard prefixes (setid hex
  prefix), 8 repos of 32 shards each (`00-1f`, `20-3f`, ... `e0-ff`). Branch `main`, a single linear history, no tags.
- `gitrx-labels-meta/` - README, `MANIFEST.json` (module sha256, input COMPLETE sha256, per-repo prefix range,
  HEAD, commit count, packed size, determinism proof), `map/<repo>.jsonl.gz` (setid, version -> repo, commit, in
  commit order) and `VERIFY-EXPORT.json`.
- `COMPLETE.json` - written last; `.state/<repo>.done.json` are per-repo resume markers.

Per commit (one per captured version): `<setid>/label.md` and `<setid>/meta.json`. `label.md` has one block per
section in document order: an HTML comment with canonical JSON (`ordinal`, `parent`, `code`, `title`, `chars`), a
Markdown heading (`## <LOINC code> | <title>`, deeper levels for children), a blank line, then the section text
exactly as stored (`own_text`: body, plus `\n\n` and the table text when present), then a blank line. `chars` makes
the framing exactly reversible (`git_export.parse_label`). Versions without a parsed document (status `no_raw`, ...)
commit `meta.json` only, with `label_md: not_available_unchanged_from_previous_commit_if_any`; `label.md` stays as
the family's previous version left it (or absent).

Commit author/committer: `GitRx <gitrx@users.noreply.github.com>`. Message: title, setid, version, raw sha256,
parse status, both dates, snapshot.

## Dates
`meta.json` keeps `dailymed_publication_date` and `spl_effective_date` separate; neither is an FDA approval date and
first observed is not actual introduction. The commit date is `key_date` at UTC midnight: the publication date when
valid, raised to the previous version's key date if earlier, carried forward when missing, else the next known date
of the family, else 1970-01-01 (`key_date_source` records which). Commits are ordered by (key_date, setid, version),
which keeps each family in numeric version order.

Titles are the latest DailyMed listing titles from the pinned names index (selection aids).

## Rebuild it
    GITRX_DATA_ROOT=... .venv/bin/python -m gitrx_core.git_export build --groups 8
    ... determinism --groups 8 <repo-name> <repo-name>
    ... verify --groups 8          # needs runtime/native-config-rebuild-0.2.4.json
    ... meta --groups 8 --determinism-json D --verify-json V ; ... complete
Refuses a COMPLETE.json whose sha256 differs from the pin, verifies each shard against COMPLETE.json, refuses to
overwrite existing repos, stops under 60 GiB free, and resumes per repo. Same input gives identical HEAD ids.

## Verify
`verify` samples 200 (setid, version) with a fixed seed (plus every version without a document), parses `label.md`
back from the git blob and compares it to `RebuildReader.read` (text, title, code, ordinal, parent) and `meta.json`
to the versions row. Results are in `VERIFY-EXPORT.json`. This checks export fidelity only, not parser accuracy.

## Caveats
Label text is DailyMed/NLM content, not relicensed by GitRx (see `LICENSING.md`); GitRx framing and metadata are
CC BY 4.0. Parsed text is not clinically validated. Missing text in a version can reflect partial extraction.
Text containing unpaired surrogates is stored with UTF-8 surrogatepass encoding to stay byte-faithful.

## Module versions

The published export `gitrx-git-export-1__spl-sections-0.2.4` was built by
`gitrx_core/git_export.py` sha256 `6406b59c...512123a` (commit 26d7281), which is the hash
recorded in its MANIFEST.json. A later commit changes only how the `git fast-import` pipe
is closed (Python 3.11 compatibility; no change to anything written). To reproduce the
recorded module hash exactly, check out commit 26d7281.
