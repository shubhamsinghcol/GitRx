# Run an incremental label release

From the GitRx folder:

```sh
./release-cycle status
./release-cycle run --setid 8ff46193-2a3b-4de0-9572-775dda8cd8b2
```

`run` refreshes the selected family's DailyMed history, downloads missing versions,
validates/parses them and builds/promotes a new native release if anything changed.
Repeat `--setid` for additional families in the existing supported scope. This is a
configured, scheduler-ready command; no recurring job has been installed.

For separate acquisition and build steps:

```sh
./release-cycle refresh --setid 8ff46193-2a3b-4de0-9572-775dda8cd8b2
./release-cycle cycle --inbox /path/returned/by/refresh.json
```

The [configuration](../reports/release-cycle-20261106/job-config.json) selects an
explicit workspace, parent release, frozen parser, section registry, lifetime
request budget and request spacing. `GITRX_JOB_CONFIG` selects another config file.
The installed equivalent is `gitrx-cycle --config FILE run --setid SETID`.

## What the cycle does

- Uses one writer lock, one workspace request ledger and a lifetime cap, with at
  least two seconds between request starts and up to three transient attempts.
- Requires two fresh agreeing complete history inventories. Shifted pagination,
  empty/unstable history and identity mismatches prevent promotion.
- Retains response bodies and failed attempts. Successful version downloads can
  be reused on restart; history passes stay fresh. Retry-After defers the cycle.
- Adds missing versions in any order and retains new publication-date observations
  without replacing the original selected ZIP. Conflicting bytes and malformed
  identities are quarantined. A quarantine requires corrected inputs or explicit
  reconciliation; retrying identical inputs reuses the quarantine result.
- Builds a separate SQLite/payload release and sixteen Git shards plus pinned meta
  commit, with parent lineage. Interrupted builds resume from frozen inputs.
- Verifies new native records through both adapters, then atomically selects the
  new backend configuration. A no-change cycle creates no release.

The current pointer is `runtime/release-cycles/current.json`, created on the first
changed release. `label-history` and `label-history-mcp` use it when present; otherwise
they retain the original selected release. Already-running MCP processes stay pinned
until restarted. `GITRX_CONFIG` can select an older immutable release explicitly.
Old release configurations and Git refs remain valid. Expanded releases may have
new commit IDs, following the owner's separately identified snapshot policy.

New releases currently rebuild derived Git snapshots when evidence changes; this
is not a constant-time append/export optimization. The supported scope is the
existing label-family register; discovering/approving new families is separate.
The configured parser version is explicit; automatic parser upgrades are refused.

## Finish the query workflow

```sh
./label-history coverage 8ff46193-2a3b-4de0-9572-775dda8cd8b2
./label-history section-history 8ff46193-2a3b-4de0-9572-775dda8cd8b2 --code 43685-7
```

These complement [historical reads, diffs and phrase history](CORE-WORKFLOW.md).
Native MCP now exposes ten tools, including `coverage` and `section_history`.
Coverage describes the captured release; source-code occurrences do not infer
complete extraction, a product-section classification or an actual addition date.

[Implementation and live-cycle evidence](../reports/release-cycle-20261106/REPORT.md).

## Select or roll back a retained release

Use `status` to identify the current configuration/snapshot. Then explicitly select
the configured original parent or a completed release retained by this job:

```sh
./release-cycle activate --target /absolute/path/to/retained/backend.json --expect-snapshot CURRENT_SNAPSHOT
```

Activation takes the writer lock, refuses a stale expected snapshot, validates the
selection and samples both read/citation paths in every shard, then atomically
replaces the pointer. Relative target paths are resolved before selection. An
activation intent is retained; it is not proof that selection completed if the
process crashed between the intent and pointer replacement. Check `status` and the
pointer after an interruption. Prior configs, raw evidence and Git refs remain intact.
New CLI/MCP processes follow the selected pointer; existing MCP processes remain
pinned until restart. A retained old configuration can always be queried explicitly.
The same command can select the newer retained release again.

[Controlled changed-source/rollback drill](../reports/release-drill-20261106/REPORT.md).

## Native 0.8 operational status and serving profiles

[Observed operational status](OPERATIONAL-STATUS.md) documents the per-family
check journal, build phases, manual mode and optional per-release search projection.
The checkout wrapper prefers `runtime/release-job-config.json` when available,
retaining the dated original configuration as its fallback. The current runtime
configuration uses the same workspace/request ledger/parser/scope and enriched
parent snapshot as the native CLI; `GITRX_JOB_CONFIG` still overrides it.
No refresh or scheduler is started by changing this configuration or reading status.
