# Repository layout and retention

The checkout combines a small source repository with a much larger local archive.
`.gitignore` explicitly allowlists source; an ignored path can still be essential
local evidence. Never use `git clean -fdx` as a cleanup strategy here.

| Location | Role | Cleanup rule |
|---|---|---|
| `gitrx_backend/` | Shared native queries, CLI/MCP/browser and operational adapters | Maintained source; change with focused tests |
| `gitrx/` | Archive/parser/export and frozen/current rule lanes | Preserve frozen code and goldens |
| `gitrx_services/` | Service and release-building support | Keep imports/configuration stable |
| `tests/`, `tools/`, `.github/` | Tests, operational tools and CI | Some local tests/tools are intentionally outside Git; preserve held-out boundaries |
| `docs/` | Maintained guides plus indexed dated operations notes | Start with the document map; avoid new competing status pages |
| `docs/history/` | Superseded mutable navigation/planning docs | Retain original preimages and hashes |
| `reports/`, `research/`, `studies/`, `annotations/` | Experiments, validation and research records | Preserve history and original scoring; no broad deletion |
| `artifacts/`, `data/`, `cohorts/` | Raw/source evidence, databases and captured selections | Preserve; do not move while jobs/configurations reference them |
| `exports/` | Generated Git releases, reproducibility builds and clone checks | Derived does not mean disposable: preserve immutable releases/refs |
| `runtime/`, `work/` | Job state, spools, incidents and development workspaces | Inspect owning job before changing; not generic temp directories |
| `.venv/`, `gitrx.egg-info/` | Local Python environment/install metadata | Keep while wrappers or installed tools depend on them |
| `build/`, `.pytest_cache/`, Python bytecode | Reproducible build/test caches | Disposable when no build/test uses them |

## Working conventions

Keep one front door ([README](../README.md) → [start here](START-HERE.md)), one
[document map](DOCUMENT-MAP.md), one [product roadmap](CORE-PRODUCT-ROADMAP.md)
and one [operations index](OPERATIONS.md). Put implementation details in their
owning guide and measurements in dated reports. Link to evidence rather than
copying another delivery narrative into every entry point.

`SPEC.md` and `HANDOFF.md` are retained historical contracts/chronology, not live
job dashboards. Existing local absolute paths and release configurations make
moving the archive a migration task, not a cosmetic folder rename.
