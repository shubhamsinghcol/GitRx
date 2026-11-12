# Evidence details and complete wording histories

> The local browser (`gitrx_backend.web`) was removed in the 2026-11-11 cleanup (see [CLEANUP](CLEANUP-2026-11-11.md)); the browser passages below describe the retired interface. CLI and MCP behavior is unchanged.

The native library, CLI, MCP and browser can use a separately pinned evidence
metadata projection for the selected release. It adds per-version extraction
status and diagnostics, publisher references, observed retrieval routes and
family inventory qualifications. It does not change selected ZIP bytes, parser
output, Git histories or the release identity.

Old configurations remain readable. When no projection is configured, native
metadata reports unknown extraction/inventory status rather than assuming
complete evidence. The browser retains its existing manifest-derived extraction
badges for those old configurations. A parser's `complete` status still does not
establish perfect source fidelity or complete historical discovery.

Publisher references distinguish observed version retrievals, bulk archive
members, current-only sources and constructed links that were not verified by a
retained retrieval. References retain release, raw SHA-256 and occurrence details.
A bulk archive link does not claim a direct HTTP download of its nested member.

## Trace wording through a family

Use `phrase-history` through the native CLI, or the `phrase_history` MCP tool.
Choose the family setid, literal phrase and optional native source code. Matching
is case insensitive by default; it does not invent synonyms or merge source
occurrences. In the browser choose **Wording history**, enter the phrase and
follow **More wording history** until the whole captured record has been read.
The existing **Find wording** view still shows only the first observed match.

Each version reports a located match, a nonmatch in extracted text, or unknown
negative evidence. A positive match can be useful even in an incompletely
extracted document. A nonmatch in partial or unknown extraction does not prove
absence from the original label. Source codes can repeat, so a match is across
all selected source occurrences, not an inferred continuous section lineage.

Gaps interrupt continuity. Wording present in the earliest captured version is
reported as present at the start of the record. A later match after a captured
nonmatch is an observation, not a proven addition date. Previously seen wording
may return, but missing versions or uncertain negative evidence prevent an exact
removal/reintroduction claim. Dates remain DailyMed publication observations and
SPL effective dates, each labeled separately.

History pages preserve selected release and query arguments in their continuation
cursor. Keep that release and phrase while following pages. A page's compact runs
are page fragments, not inferred calendar periods. Exhausting a page does not
establish a complete scan when a continuation remains.

## Saved browser releases

Starting the browser with a verified evidence projection for an already saved
release adds metadata to that same release's registry configuration and retains
the former configuration as `<snapshot>.legacy.json`. Existing exact links keep
their release identity. A saved configuration with an existing metadata pin is
not silently replaced by a different pin; use a separate registry to inspect a
different projection. Starting with an older configuration does not downgrade an
already saved metadata pin.

## Current configuration and commands

The checkout wrappers prefer `runtime/native-config.json` when no changed-cycle
pointer exists. It augments the original expanded snapshot with the new pinned
metadata; Git remains the default reader. Older configurations can still be
selected explicitly through `GITRX_CONFIG`.

```sh
./label-history --backend sqlite phrase-history 8ff46193-2a3b-4de0-9572-775dda8cd8b2 seizures --limit 20
./label-history --backend sqlite coverage 8ff46193-2a3b-4de0-9572-775dda8cd8b2
.venv/bin/python -B -m gitrx_backend.web --config runtime/native-config.json
```

Continue a history with `--cursor TOKEN`, keeping its phrase and filters. A cursor
also pins the metadata projection, matching-policy version and Unicode database
version. It is a position token rather than authenticated proof of earlier work;
predecessor state is recomputed from captured records. Long prefix scans remain
subject to the whole-query deadline; use SQLite or a larger declared timeout when
needed. Each page retains at most 20 matching occurrences per version and states
when further occurrences were omitted.

Build another projection explicitly into a new directory:

```sh
.venv/bin/python -B -m gitrx_backend.evidence_metadata --config /path/to/backend.json --output /new/metadata
```

The result includes a separate enriched `backend.json`. Neither reading nor
opening an old configuration builds metadata automatically. Changed release jobs
now stage a fresh projection with the new snapshot; they never carry an old pin
forward. This does not install a scheduler or run acquisition.
