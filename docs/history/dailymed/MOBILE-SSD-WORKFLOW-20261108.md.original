# Mobile SSD workflow

The finite acquisition job now uses a local canonical working directory under
`~/Library/Application Support/GitRx/backfill/mobile-workspace/`. The original
SSD job/configuration and frozen batches remain retained. This is an explicit
versioned working-profile migration, not a selected application release update.

## What stays on the laptop

The queue and HTTP ledger, active batch databases, source census/seed metadata,
raw download buffer, parser spool and completed batches awaiting archive are local.
The older parent-label archive used by this job is already on the laptop. Missing
SSD-dependent parent evidence is deferred without claiming it was verified, while
other families continue. Deferred families are retried after reconnection; totals
are replaced per family rather than added twice.

A 40 GiB combined monitored working-storage budget covers the local mobile workspace,
raw staging, hot databases and parser spool. The existing 20 GiB free-space floor
remains. The version-8 transport permits 20 download threads at shared 0.05-second
request-start spacing. Download-ahead spans the remaining pinned batches. Download-ahead has no file-count stop and a 16 GiB response
budget within the existing local raw-work limit; see
[cross-batch throughput changes](THROUGHPUT-20261108.md). Background independent
history confirmation and up to twelve families in flight reduce the parser-admission
wait; see [handoff correction](HANDOFF-PIPELINE-20261108.md). Work pauses for review if local capacity/integrity checks fail;
reconnection can release the outbox even when the work budget is full.

## What uses the external SSD

Completed batches and their original raw source bytes are transferred to the
identity-verified SSD. Raw bodies, source database, inbox and batch manifest are
hash-checked before local completed-batch links are released. Existing immutable
SSD batches are never overwritten. Incomplete old working directories are retained
as migration incidents rather than silently discarded.

Local frozen metadata and receipts retain the hashes of earlier archived batches.
An offline restart uses those previously verified identities, without claiming a
fresh integrity scan of an unavailable disk. Active/local completed data still gets
normal integrity checks. Working queue/ledger state remains local; this workflow
is not an independent full-archive backup or disaster-recovery qualification.

## Desktop buttons

**GitRx Eject SSD.command** briefly pauses this worker, checks the prepared local
configuration, disables SSD use, and asks macOS to eject normally. It never forces
a busy disk to unmount. Unplug only after the button reports success. It then
resumes the same finite job locally. If macOS refuses, it clearly says not to
unplug and resumes local work with SSD use disabled.

**GitRx Reconnect SSD.command** verifies the expected mounted drive (or asks macOS
to mount its recorded UUID), briefly pauses the worker, copies waiting completed
batches with hash verification, enables future SSD transfers, and resumes the job.
A missing/wrong drive does not stop healthy local collection. Plugging it in alone
does not undo the durable SSD-disabled setting; use Reconnect.

**GitRx Status.command** shows live local progress, parser/download state, SSD-use
setting and waiting archive batches even while the SSD is absent. Start/Pause remain
available. The guardian starts the prepared pinned local configuration after login
and can restart workers without the SSD. Scope and request/byte limits are unchanged.

Work can continue during a 20-minute/hour absence while the laptop is awake, has
internet, and has local space. Duration is not an unlimited storage guarantee.
Closed-lid sleep can suspend work. Controlled ejection is safer than pulling an
actively written drive; no software guarantees arbitrary physical-device failures.

## Validation

Synthetic tests remove the archive directory entirely, complete multiple local
batches, reconnect and verify original batch/database/inbox hashes. Separate tests
exercise SSD-dependent family deferral across another batch, retry accounting,
mobile guardian restart without a drive, fresh offline status, refusal of busy or
unprepared ejection, identity checks and reconnection at the local-storage cap.
Live working-profile preparation and verification records are retained under
`runtime/full-dailymed-backfill/mobile-20261108/`. Actual physical ejection is left
to the user's new desktop button.
