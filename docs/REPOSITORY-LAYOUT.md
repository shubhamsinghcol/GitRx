# Repository layout and retention

The checkout combines a small source repository with a much larger local evidence
workspace. `.gitignore` is an allowlist; an ignored path can still be essential local
evidence. Never use `git clean -fdx` as a cleanup strategy here.

| Location | Role | Cleanup rule |
|---|---|---|
| `gitrx_backend/` | Shared native queries, readers, CLI, MCP, config, research helpers | Maintained; change with focused tests |
| `gitrx_core/` | Parser, change records, rebuild, SrLC grading/precision, update jobs, gap repair, evidence DB build | Data-producing modules are frozen per version; change through new versions |
| `gitrx/current/`, `gitrx_services/` | Legacy fixture-release builder used by `gitrx-selftest` and the git/sqlite readers; `spl_parser_v1` kept for parity tests | Keep imports stable |
| `tests/`, `tools/`, `.github/` | Tests (fixtures only), `verify_gitrx.py`, CI | Tests needing the data root skip without it |
| `ops/` | Update-job notes and LaunchAgent templates | Templates only; nothing is installed by the build |
| `runtime/` | Release configs (`native-config-rebuild-*.json`), section-code registry; local state is ignored | `runtime/local.json` is local only |
| `docs/` | Guides; `docs/history/` holds archived guides | Start with the document map |
| `reports/`, `research/`, `studies/`, `annotations/` | Local experiments, validation and scoring records (not in Git) | Preserve; no broad deletion |
| `artifacts/`, `data/`, `cohorts/` | Raw/source evidence, databases, captured selections (not in Git) | Preserve; raw downloads are never deleted |
| External data root | Evidence DB, rebuild, SrLC crawl and gradebook, update state, provenance bundle (`GITRX_DATA_ROOT`) | Preserve |
| `work/` | Development workspaces, including `work/legacy-tests-20261111/` (tests of removed code) | Not generic temp |
| `.venv/`, `gitrx.egg-info/`, caches | Local environment | Disposable when unused |

`SPEC.md` and `HANDOFF.md` (local, untracked) are retained historical contracts, not live dashboards.
Removed code is recoverable from git tag `codex-final` ([cleanup record](CLEANUP-2026-11-11.md)).
