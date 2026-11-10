# Automatic rescue, local working storage and unified status

November 8, 2026. Installed into the existing finite backfill; no release promotion.

## Failure handling

Ordinary ValueError/TypeError/KeyError failures inside document extraction are returned from the CPU worker as explicit failure diagnostics. The coordinator records a native extraction_failed run, raw hash, version identity, and durable backfill version pin. That version stays earmarked as parse_failed and is skipped on restart; other versions and families continue. It is not admitted as successful extraction. Existing parser section-level partial-extraction diagnostics remain unchanged.

Unexpected worker exits, worker memory failures and 180-second queued/processing deadlines receive three pool retries, then the affected pending versions are recorded as failed and processing moves on. Since a pool crash cannot reliably identify which pending task killed it, all tasks that individually exhaust that retry budget are deferred rather than falsely attributed to a single culprit. Raw evidence and any unacknowledged spool remain retained.

Ten consecutive failed parses trigger a persistent circuit breaker and stop for review. Ten consecutive family source/identity failures also stop. Hash/pin mismatches, spool corruption, unexpected program exceptions, SQLite/storage corruption, coordinator memory failure and budget exhaustion retain job-wide stop behavior. Transient connectivity/disconnection is handled by the existing guardian and saved checkpoints. This is bounded automatic rescue, not automatic repair of arbitrary corrupted evidence.

Failed versions remain in each batch database's backfill_versions and parse_runs with their native diagnostics and source references; family outcome/totals remain in the control queue. Existing completed batch manifests are immutable. Retrying a deferred failed parse requires a separately controlled later pass; normal restart does not loop on it.

## Storage

Before this change, mutable SQLite/WAL, processing spool and a bounded recent raw journal were local, but network downloads and source artifacts were written immediately to the external SSD.

The new version-3 local-storage policy adds a 20 GiB bounded raw staging area on the laptop. New HTTP bodies, failed prefixes and active batch artifacts stay local, alongside the existing 8 GiB hot-database budget and 3 GiB parser-spool budget. The existing 20 GiB local free-space floor remains. Prior SSD evidence remains where it was; active-batch artifacts are copied and verified locally without deleting the older source directory.

At each terminal 250-family batch, all staged response bytes (including unsuccessful responses) are copied into the external content-addressed store, fsynced and SHA-256 checked. Batch source artifacts become external hardlinks. A durable handoff checkpoint permits restart across an interrupted directory switch. Local raw copies are removed only after verified external handoff. The database, inbox and batch pins then freeze using the existing mechanism. An attempted batch can include earmarked failures; terminal does not mean every version parsed successfully.

The external SSD still holds the finite-job metadata/configuration, census/parent evidence and completed archive, so an SSD disconnection pauses the worker until the correct volume returns. Downloads/processing no longer use external storage as the primary raw working area. This does not promise uninterrupted offline collection. Budgets stop rather than overflowing laptop storage.

## Desktop controls

Use **GitRx Status.command** on the Desktop for health, processed and remaining percentages, ETA, failure counts and storage status. Start and Pause remain separate actions. Old Health/Progress launchers were retained under the local repair report directory and removed from the Desktop.

ETA is calculated from recent family-boundary progress, after at least 60 seconds and three families. A gap longer than two minutes resets the sample. Paused/offline/unhealthy states suppress ETA. History sizes, retries and sleep can change it significantly. Percent processed means finite-plan family attempts, including incomplete/deferred outcomes; it is not historical corpus completeness.

## Validation

Focused synthetic checks cover native failed-parse persistence, exhausted worker deferral, persistent systemic circuit breaking, local staging through restart/freeze, interrupted handoff, corrupt-destination refusal and the unified read-only status output. Existing guardian, hot-storage, raw-journal and release-cycle tests were also run. Live after-state evidence is retained in runtime/full-dailymed-backfill/rescue-staging-20261108/.

Final result: 77 focused maintained tests passed. Live batch 13 completed local-to-external raw handoff, its database/inbox pins verified, and the worker continued into batch 14. Final runtime includes processing deadlines and staging-aware recovery.
