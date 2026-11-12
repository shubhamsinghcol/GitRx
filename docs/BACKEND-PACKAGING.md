# Installing and configuring the package (for maintainers)

Source: [shubhamsinghcol/GitRx](https://github.com/shubhamsinghcol/GitRx). The package needs Python 3.11 or newer, Git, and `lxml`.
CI (`.github/workflows/ci.yml`) installs it on Ubuntu with Python 3.11 and 3.14 and runs the fixture tests with no data and no network.
Raw evidence and release repositories are not in source control.

## Install

```sh
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/gitrx-native --config backend.json families --search naltrexone
.venv/bin/gitrx-selftest --output /path/to/new-empty-folder    # builds a synthetic release to check the install
```

Installed commands: `gitrx-native` (CLI), `gitrx-native-mcp` (MCP), `gitrx-selftest`, `gitrx-recovery`.
The checkout launchers `./label-history` and `./label-history-mcp` use the default release config
`runtime/native-config-rebuild-0.2.4.json` (backend `rebuild`); set `GITRX_CONFIG` or pass `--config` to choose another.
Parser, rebuild and update modules in `gitrx_core` run with `python -m` and need `GITRX_DATA_ROOT`.

## Configuration file

Paths resolve relative to the config file. A `${GITRX_DATA_ROOT}` placeholder in a path expands to the data root.
Unknown keys or versions are rejected.

```json
{
  "format": "gitrx-backend-config-1",
  "backend": "git",
  "snapshot": "FULL_SNAPSHOT_SHA256",
  "meta_commit": "FULL_META_COMMIT_SHA1",
  "export_root": "exports/selected-release",
  "release_root": "releases/selected-release",
  "scope_path": "scope.json",
  "cache_families": 32
}
```

`--backend` selects `rebuild` (the default release), `sqlite` or `git`. `git` needs the meta repository, all shard repositories
and the scope file pinned by the meta manifest. Search and some commands return `next_*_offset` values to pass back for the next page.
Bad input returns JSON on stderr with exit status 2.

## Changing derived data

Derived changes build a new release directory, verify it, and select it through a new configuration. There is no
in-place migration of evidence databases, raw files or old Git refs. Changed output formats need a new contract version.
Packaging installs code only, not corpus data.
