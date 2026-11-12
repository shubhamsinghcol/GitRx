# Recurring updates (weekly DailyMed, monthly SrLC)

Approved by the owner 2026-11-11. Collection only: nothing here parses, classifies,
grades or promotes a release. Plists in `ops/launchd/` are NOT installed by the build.

## What runs

| Job | Module | Schedule | Data |
| --- | --- | --- | --- |
| DailyMed weekly | `gitrx_core.dailymed_update` | Sunday 03:00 | `$GITRX_DATA_ROOT/updates/dailymed/` |
| SrLC monthly | `gitrx_core.srlc_update` | day 2, 04:00 | `$GITRX_DATA_ROOT/srlc/updates/` |

DailyMed: pages the official v2 listing (newest first) back to `watermark - 7 days`, treats a setid
as changed if its listed version is not already held (immutable `evidence-v1.sqlite`, read-only, plus the
delta DB), refreshes `history.json` for those setids and downloads only missing versions
(`getFile.cfm?type=zip`). Bodies are hash-named write-once files in `network/bodies/` and every request
is in `network/ledger.sqlite` (time, URL, status, bytes, sha256). New observations go to
`updates.sqlite` (runs, listing_observations, history_entries, version_files; the last three are
append-only by trigger). `WATERMARK.json` is replaced atomically only after a `complete` run; `partial`,
`blocked`, `interrupted`, `error` and `--smoke` runs leave it alone (the next run re-covers the window).
Defaults: 3000 downloads and 6000 requests per run, >= 2 s between requests.

SrLC: enumerates the last 6 monthly windows (late postings), single lane, >= 1 s (default 2 s) between
requests, 500 requests per run, user agent `GitRx-research/0.1 (+public SrLC validation)`. Detail pages
and CSVs are fetched only for DrugNameIDs that are new or whose (supplement date, Database Updated) pairs
are not in the frozen `gradebook.sqlite` (read-only) or earlier updates (`state.sqlite`). Raw bodies:
`srlc/updates/raw/`; ledger: `srlc/updates/ledger.sqlite` (new file; the original ledger is never
written). Each run builds `srlc/updates/gradebook-YYYYMMDD.sqlite` (same schema + `provenance`; never
overwrites; delta of drugs fetched by update runs). `srlc/gradebook.sqlite` stays frozen.

## Safety (both jobs)

Skip with exit 0 and a log line when: the SSD is absent or the identity marker/token differs
(`GitRx/.volume-identity`, same token as the backfill) -> "skipped: SSD absent"; `updates/STOP` exists;
the heavy-job lock is held; a `gitrx_core.rebuild` process is running; a backoff file is active;
or another instance of the same job holds its flock (`<job root>/job.lock`). Jobs run at `nice 10`.
Any HTTP 403/429 stops the run at once and writes `BACKOFF-UNTIL` (24 h); later runs skip until it passes.

- Heavy-job lock convention: a heavy job writes `$GITRX_DATA_ROOT/HEAVY-JOB.lock` (first line its PID,
  optional description) and removes it when done. A lock with a dead PID is ignored; one without a PID is
  honored. Updates also detect a running `gitrx_core.rebuild` process by command line.
- Stop all updates: `touch $GITRX_DATA_ROOT/updates/STOP` (a running job also stops at the next
  item). Resume: remove that file. Uninstall: `launchctl bootout gui/$(id -u)/org.gitrx.<name>`.
- Manual smoke/testing flags: `--smoke`, `--since-days N`, `--max-downloads N`, `--window FROM:TO`,
  `--run-cap N`, `--ignore-heavy` (never use `--ignore-heavy` unattended).

## Holdout rule (SrLC)

SrLC entries with an approval/supplement date OR Database Updated date after 2026-11-11 are the sealed
prospective holdout for Freeze 2. Jobs never grade, print or summarize them. Logs carry counts and
fingerprints only; `srlc/updates/holdout/manifest-<run>.json` lists DrugNameIDs, dates, which criterion
fired, and sha256 fingerprints (no text). The dated gradebook snapshot omits holdout supplements and
changes (and the per-drug CSV/reconcile rows of drugs that have any), so it cannot be graded against them
by accident. Raw bodies containing them are kept as evidence in the raw store; do not open them for
content before the holdout is unsealed by the owner.

## Install (owner, after the rebuild finishes)

    mkdir -p ~/Library/Logs/GitRx
    for n in dailymed-weekly srlc-monthly; do
      sed -e "s|@GITRX_ROOT@|$PWD|g" -e "s|@HOME@|$HOME|g" ops/launchd/org.gitrx.$n.plist > ~/Library/LaunchAgents/org.gitrx.$n.plist
      launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/org.gitrx.$n.plist
    done

The plists in `ops/launchd/` are templates (`@GITRX_ROOT@` = this checkout, `@HOME@`). The jobs find the data root
from `GITRX_DATA_ROOT` or the git-ignored `runtime/local.json` (`{"data_root": "...", "volume_token": "..."}`);
with a `volume_token` set they also require `<data_root>/.volume-identity` to contain it. Paths written as
`$GITRX_DATA_ROOT` in this guide mean that directory.

The first DailyMed run needs a watermark: run once by hand with `--since-days N` (or write
`updates/dailymed/WATERMARK.json` as `{"watermark": "YYYY-MM-DD"}`) before enabling the schedule.
