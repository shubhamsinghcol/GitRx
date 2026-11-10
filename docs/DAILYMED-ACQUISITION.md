# DailyMed acquisition protocol

Maintained algorithm guide, consolidated November 8, 2026. Code and explicit
versioned configuration define execution. The [historical notes](history/dailymed/README.md)
retain experiments and earlier profiles; do not combine their settings.

## Entry point and deployed copy

The prepared launch agent `org.gitrx.full-dailymed-backfill` invokes `resume.sh`,
which sets `PYTHONPATH` to the installed source under
`~/Library/Application Support/GitRx/backfill/installed/` and starts
`gitrx_backend.backfill_guardian`. Its runner/guardian policies select the finite
worker configuration. Checkout edits do not automatically update that installed
copy. Never casually replace installed files or policy pins during cleanup.

The mobile configuration uses
`~/Library/Application Support/GitRx/backfill/mobile-workspace/` as canonical
working storage. Completed evidence batches transfer to the identity-verified
SSD. The original SSD-first runner remains relevant to preparation and recovery;
it is not the mobile profile's active worker command.

## Collection and admission

1. **Pin the plan.** `Backfill` checks parser/configuration hashes, parent release,
   census and seed pins, acquires the single writer lock, verifies databases and
   reconstructs the queue/frozen batch checkpoints. The recorded scope is 158,963
   current-listing setids plus 19 retained families, in 250-family batches.
2. **Acquire raw responses ahead.** `DownloadBuffer` owns HTTP/ledger writes on
   one acquisition thread. `DownloadPool` runs bounded network transfers. A history
   response supplies untrusted version/page hints for prefetch; those hints never
   establish an accepted inventory. Known parent/sample versions can be reused.
3. **Confirm inventories.** The shared `release1_history.collect` requires complete
   agreeing passes, preserves duplicate publication observations and checks source
   identity, pagination and raw row totals. Single-page background confirmation
   must be a distinct later request; a cached response is not two observations.
   Unstable or multipage histories follow the native collector's bounded behavior.
4. **Validate ZIPs and retain provenance.** Bodies are SHA-256 addressed and tied
   to durable request observations, including failed attempts/prefixes. Native
   validation checks setid/version inside XML. Identity conflicts are retained and
   quarantined. Missing versions use the bounded archive fallback; unexplained
   inventory gaps remain unknown. No invented dates or cross-family substitution.
5. **Parse with one database coordinator.** `ProcessingPool` runs frozen parsing
   in bounded processes/spool storage. The coordinator persists native sections,
   tables, diagnostics and version pins. Multiple families may have parses in
   flight; a family becomes terminal only after its durable outcomes are known.
6. **Freeze and transfer batches.** At a completed boundary, outstanding native
   work drains, raw movement briefly pauses acquisition, and ready future bodies
   stay local. Cross-batch downloading can resume before native freezing/SSD copy.
   Manifests, database and inbox hashes pin the batch. `MobileArchive` verifies
   completed SSD transfers and retains receipts before releasing local batch links.
7. **Resume the same finite job.** The guardian respects durable manual pause,
   completion markers, persisted Retry-After and integrity/recovery outcomes.
   Retained checkpoints avoid treating interrupted prefixes as reusable successes.
   No derived-release promotion or recurring collection follows completion.

## Settings and resource ownership

The prepared mobile policies inspected November 8 now use transport version 27,
processing version 3 and local-storage version 5. Version 27 returns to a fixed 12 Hz target after sustained operation at 13 Hz
produced HTTP 429s despite a clean initial one-minute trial. It preserves the
active 40-lane fallback limit. Server cooldowns wait in-process; completed bodies
and cached processing can continue while new starts are withheld. Errors and
Retry-After deadlines remain durable. Operational success counters checkpoint
once per second; raw HTTP evidence and request-budget records are unaffected.
Restart verification hashes each released database once, then checks that digest
against its batch manifest, control selection and hot-storage frozen pin. It does
not re-read those same immutable bytes through no-op freeze/release calls. Active
or interrupted hot-storage states still use the full recovery procedure.


| Setting | Prepared value |
|---|---|
| Download lanes / scheduling window | 40 active; see pacing state / up to 200 tasks |
| Shared request-start interval | Fixed 1/12 seconds; server cooldowns pause starts |
| Download-ahead | Across pinned batches, 16 GiB ready-response budget, no item-count cap |
| History confirmation | Independent background requests enabled |
| Parser processes / queue / families in flight | 12 / 24 / 12 |
| Aggregate parser RSS budget | 8 GiB |
| Mobile combined working-storage budget | 40 GiB; separate component caps also apply |
| Free-space floor | 20 GiB |

These are configuration observations, not measured throughput or promises of
completion within a storage allowance. Inspect `TRANSPORT-POLICY.json`,
`PROCESSING-POLICY.json`, `LOCAL-STORAGE-POLICY.json`, `config.json` and pacing
state in the prepared profile before reporting active settings. Top-level copies
in the support directory can be older than workspace policies.

The worker raises its own inherited open-file soft limit to 4,096 (bounded by
its hard limit), without changing global launchd limits. Heartbeats report the
loaded allowance and open-descriptor count. Temporary seed/parent source readers
are explicitly closed after each read, including failures. This repairs the
November 9 stop under launchd's inherited 256-file allowance; retained source
bytes and parser rules are unchanged.

## Controls and live status

Use the prepared GitRx Start/Pause/Status buttons. Status reports live local
progress even without the SSD. Eject briefly pauses for a safe handoff, disables
SSD use durably and asks macOS to eject; Reconnect verifies the drive, transfers
waiting batches and reenables SSD use. Mounting alone does not clear that setting.
Local collection can continue without the SSD while awake, online and within
storage limits. Sleep can suspend progress; integrity/capacity failures can need
attention. This is not an independent archive backup.

Report attempted, captured, parsed, staged and selected counts separately. A
finished queue is not proof of all historical DailyMed coverage; archived-only
families remain unresolved. The manual release-cycle journal is a separate
system documented in [observed release-cycle status](OPERATIONAL-STATUS.md).

## Source ownership and deliberate fallback paths

| Module | Responsibility |
|---|---|
| `backfill_guardian`, `backfill_control`, `mobile_control` | Finite-worker supervision and user controls |
| `backfill` | Queue, admission, family outcomes and frozen batches |
| `download_buffer`, `parallel_downloads`, `transport_policy` | Prefetch, bounded network transfers and shared pacing |
| `jobs.HTTP` | Shared request ledger, sequential retries/redirects and body lookup |
| `release1_history` in `gitrx_services` | Authoritative history confirmation/persistence |
| `backfill_processing` | Bounded parser process pipeline |
| `hot_storage`, `backfill_staging`, `mobile_archive` | Database locality, raw handoff and verified SSD archive |
| `backfill_recovery`, `raw_journal`, `mobile_setup`, `acquisition_runner` | Recovery, retained older profiles and explicit setup/preparation |

Parallel and sequential HTTP are both reachable: the former supplies throughput,
the latter handles ordinary fetches and retry/redirect fallback. Old-profile
branches and recovery tools are not dead merely because the prepared mobile
worker normally bypasses them. Demonstrate unreachability and retained-config
compatibility before deleting these paths.
