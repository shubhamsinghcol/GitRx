# Repository layout (for maintainers)

The checkout is a small source repository next to a much larger evidence area that is not in Git. An ignored path
can still be essential evidence. Never use `git clean -fdx` here.

| Location | What it is |
|---|---|
| `gitrx_backend/` | Shared queries, readers, CLI, MCP, config |
| `gitrx_core/` | Parser, change records, rebuild, SrLC grading, update jobs, Git export |
| `gitrx/`, `gitrx_services/` | Fixture-release builder behind `gitrx-selftest` and parity tests |
| `tests/`, `tools/`, `.github/` | Tests (fixtures only; data-dependent tests skip without the data root), verification script, CI |
| `ops/` | Update-job notes and LaunchAgent templates (nothing is installed by the build) |
| `runtime/` | Release configs (`native-config-rebuild-*.json`); `runtime/local.json` is local and git-ignored |
| `docs/` | Guides; `docs/history/` holds archived ones; see the [document map](DOCUMENT-MAP.md) |
| `reports/`, `research/`, `studies/`, `annotations/` | Experiment, validation and scoring records. Preserve. |
| `artifacts/`, `data/`, `cohorts/` | Raw and source evidence. Never delete raw downloads. |
| `GITRX_DATA_ROOT` (external) | Evidence database, rebuild, SrLC data, update state |
| `.venv/`, caches | Disposable |

Code removed in the 2026-11-11 cleanup is recoverable from git tag `codex-final` ([record](CLEANUP-2026-11-11.md)).
