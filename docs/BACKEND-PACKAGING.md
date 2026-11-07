# Portable package and module boundaries

The private source repository is [shubhamsinghcol/GitRx](https://github.com/shubhamsinghcol/GitRx).
[Remote CI](https://github.com/shubhamsinghcol/GitRx/actions/workflows/backend.yml)
builds and installs the wheel on fresh Ubuntu runners with Python 3.11/3.14,
then exercises synthetic releases and the maintained native regressions away
from the source checkout. Raw evidence and generated release repositories are
excluded from source control. The disposable runners remove unused Android/.NET
SDKs to meet the existing 20 GiB archive disk guard; application guards are unchanged.

The installed interface is `gitrx-native`; the repeatable offline build check is
`gitrx-selftest`. Both use the installed wheel, not scripts reached through the
checkout. Python >=3.11, Git with SHA-1 repository support, and pinned lxml are
runtime requirements. Local evidence was collected on macOS/Python 3.14; Linux
and Python 3.11 are CI targets, not locally claimed execution results.

## Install and configure

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/gitrx-native --config backend.json families --search naltrexone
.venv/bin/gitrx-selftest --output /tmp/a-new-synthetic-gitrx-release
```

`backend.json` is deliberately explicit. Relative paths resolve beside the
configuration file, independent of current directory or package installation.
`GITRX_CONFIG` may provide the filename; `--config` overrides it.

```json
{
  "format": "gitrx-backend-config-1",
  "backend": "git",
  "snapshot": "FULL_EXPANDED_SNAPSHOT_SHA256",
  "meta_commit": "FULL_META_COMMIT_SHA1",
  "export_root": "exports/selected-release",
  "release_root": "releases/selected-release",
  "scope_path": "scope.json",
  "cache_families": 32
}
```

Select `--backend sqlite` to use the existing metadata/payload release. Git requires
its meta repo, all sixteen shard repositories and the scope file whose hash the
meta manifest pins. Full captured family history requires complete shard history;
shallow-history support is not a v1 promise. The local configured example is
[local-config.json](../reports/backend-foundation-20261106/local-config.json).
It binds this checkout's existing selected inputs; it is not bundled into the wheel.

Examples, with SETID and versions replaced by selected values:

```sh
gitrx-native --config backend.json versions SETID
gitrx-native --config backend.json show SETID 2 --ordinal 1 --max-chars 8000
gitrx-native --config backend.json diff SETID 1 2 --ordinal 1
gitrx-native --config backend.json search 'renal impairment' --max-families 128
```

Search returns `next_family_offset` and `next_section_offset`; pass them back as
`--family-offset` and `--section-offset`. When a result limit interrupts a family,
the cursor remains within that family so the next page does not lose matches.
Output is JSON; malformed/unknown inputs return stderr JSON and exit status 2.
Help/missing required arguments use argparse's conventional exit status 2.

## Module inventory and maintenance lanes

| Lane | Modules | Rule |
|---|---|---|
| Portable production-facing native reads | gitrx_backend/config.py, readers.py, query.py, cli.py | Explicit paths, common query contract, no workspace tools imports |
| Portable offline acceptance builder | gitrx_backend/selftest.py | Synthetic sources only; explicit new output folder; no network |
| Shared build/storage helpers | gitrx_services/federation.py, export_payloads.py, native_query.py, shard_release.py | Reuse with explicit roots; native_query's older module CLI and shard release orchestration retain checkout convenience defaults |
| Preserved evidence/extraction/export dependencies | gitrx/current/archive.py, db.py, parser.py, spl_parser_v1.py, normalize.py, content_storage.py, export_git.py | Preserved byte-for-byte; inputs passed explicitly by the portable builder |
| Accepted interpreted pilot | gitrx/current query/tooling/section_history, gitrx_services/mcp.py | Existing pilot scope; not silently promoted to full corpus |
| Historical replay engines | gitrx/legacy_*, gitrx/current/legacy_*, frozen report bundles | Preserve rather than merge/refactor; bundled Python dependencies permit replay with the separately retained original inputs |
| Stage-specific acquisition/validation drivers | tools/*, release1 supervisor/job/rounds, fresh_score | Existing research execution lane; not installed production entry points; retained configurations/manifests may contain original absolute paths |

The archive root contains no Git checkout. CI is a committed-file definition for
a future repository/check-in; defining it locally does not run GitHub Actions.
Do not infer a successful hosted CI run from the local suite.

The reusable Federation/One/checkpoint-pins helpers were lifted from stage scripts
into `gitrx_services.federation` so release builders no longer import those tools.
Prepared-payload recipe discovery falls back to the installed exporter source;
its byte hash and SQL recipe stay identical. Stage discovery and historical scoring
still have tool dependencies and are outside the production entry-point lane.
Future acquisition automation must port those drivers explicitly (roadmap M3).
The production reader and installed synthetic pipeline have no tools dependency.

## Configuration and schema migration policy

Configuration format 1 accepts only documented keys; unknown versions fail closed.
The existing native metadata schema is validated by use of required tables/columns
and pinned database bytes; the new reader does not mutate it. Derived-index changes
must build a new release directory, verify it and select a new configuration. There
is no in-place migration of evidence SQLite, raw objects or historical Git refs.
Output format changes require a new versioned contract. Current packaging installs
Python code, not corpus data, raw ZIPs, registries or private stage reports. The
selftest uses a tiny explicit synthetic code registry rather than importing live
registry evidence from the checkout.
