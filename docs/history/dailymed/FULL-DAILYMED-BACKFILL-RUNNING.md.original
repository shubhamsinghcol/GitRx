# Full DailyMed backfill: downloading and processing together

Current working profile: [mobile SSD workflow](MOBILE-SSD-WORKFLOW-20261108.md). The live queue and working metadata are now local; the SSD receives verified completed batches. The launch paths below describe the earlier profile. The latest processing change is [handoff correction](HANDOFF-PIPELINE-20261108.md). Transport changes are documented in [cross-batch throughput](THROUGHPUT-20261108.md); earlier profiling is retained in [pipeline profiling](PIPELINE-PROFILE-20261108.md).

Started November 7, 2026. This is one finite, manually authorized acquisition job,
not recurring collection. The existing selected native release remains unchanged.

The worker acquires each family's source history, saves original ZIP evidence,
checks internal setid/version identity, and parses each validated version before
moving on. It does not accumulate a whole unprocessed corpus first. It reuses
verified parent evidence and the preparation sample rather than downloading those
ZIPs again. Original publication strings, normalized dates, history observations,
retrieval provenance, source document types and extraction limitations are retained.

## Running scope and limits

- **158,982 valid families queued:** the 158,963-setid frozen current listing plus
  19 additional families already in the retained parent release. Existing families
  get new history checks; already captured versions remain source references.
- Four malformed source setids remain in the preparation census anomaly table.
  Nonpositive source versions are retained as anomalies, not repaired or used as
  ordinary positive-version download identities.
- **250 families per batch**, one sequential HTTP lane with at least two seconds
  between starts. Lifetime caps: **900,000 requests**, **225 GB received response
  bytes**, and **450 GB accounted retained data**. The real filesystem guard keeps
  at least **20 GiB free**. Future Git/build staging still needs its separate
  50 GB allowance; no Git export is running alongside collection.
- Budgets are limits, not promises that every target fits or is retrievable. Failed
  or unknown histories, unavailable ZIPs, identity conflicts and partial/failed
  extractions are reported separately. A fully attempted plan is not complete
  historical DailyMed coverage. Archived-only families outside these known frames
  remain an unresolved discovery scope.

At the configured polite rate, the full job is expected to take **days to weeks**,
longer while the laptop sleeps. No one-night completion promise is made.

## Storage and checkpoints

Workspace: `/Volumes/Untitled/GitRx/backfill-20261107/`.

`status.json` reports family states, newly validated downloads, complete/partial
parses, retained parent references, request/byte usage and current work. Version
counters commit at family boundaries, so a currently active family's newly parsed
versions may not be reflected until that family finishes.

`control.sqlite` holds the durable queue. `network/ledger.sqlite` retains every
request observation and failed prefix. Successful original bodies live once in
`network/bodies/`; per-batch evidence pointers use verified same-volume hardlinks.
Successful transfer staging files are removed only after their exact bytes and
observation are durably retained. Failed prefixes remain evidence and are never
reused as successful downloads. Transfer accounting survives staging-file removal.

Each batch has its own `data/gitrx.sqlite`. A completed batch freezes its database,
family outcomes and candidate `inbox.json` under hash-pinned `BATCH.json`. The inbox
includes only fully retrieved/parsed families (complete or partial extraction),
including dated observations for retained-parent versions. Other evidence remains
available for review. Scope admission, source-observation transfer, raw publication
observations and stage-without-promotion are implemented in the shared release-cycle
library; new-family history coverage remains explicitly unknown there until its
history projection is integrated and qualified.

The worker does **not** rebuild the entire Git release after every family/batch or
change the selected application release. Candidate integration and qualification
are separate checkpoints. Old evidence and old citations remain available.

## Sleep, shutdown and interruption

The installed runtime is frozen in
`~/Library/Application Support/GitRx/backfill/installed/`. Configuration pins the
census, parent, seed sample and full parser environment/configuration. Edits to the
checkout do not change the running worker. The login/mount LaunchAgent is
`~/Library/LaunchAgents/org.gitrx.full-dailymed-backfill.plist`.

Idle sleep is inhibited while running. Closed-lid sleep can pause computation;
shutdown stops it. On login or SSD mounting, the same finite job checks its saved
queue, unfinished batch, parsed-version pins, frozen database/inbox hashes and
continues. Raw objects are hash-checked when reused or admitted; the whole archive
is not re-read on every login. A missing/wrong volume prevents writes. Scope,
integrity and capacity errors stop for attention; transient failures get bounded
retries with persisted Retry-After. This does not guarantee against hardware failure
or unsafe drive removal.

To pause, create `STOP` in the workspace. The normal stop is at a family boundary.
To resume after removing STOP:

```sh
launchctl kickstart gui/$(id -u)/org.gitrx.full-dailymed-backfill
```

Runner state and append-only logs are under Application Support/GitRx/backfill.
The completed preparation runner remains a separate no-new-requests job.

## Qualification before launch

48 focused tests passed before launch. They cover shared inventory evidence and
publication persistence, single-CAS byte accounting, HTTP partial recovery,
immutable scope admission, stage-only candidates, real parser failure quarantine,
raw publication strings, adapter parity, parsed-version restart and batch-freeze
faults. The launcher was checked for access to retained parent evidence paths.
The first live families produced validated and parsed versions before a controlled
worker interruption test. See the dated report for the observed continuation.
Historical held-out answers were not opened. No cloud bucket, public repository
or upload was created.

[Preparation/storage budget](FULL-DAILYMED-PREPARATION-RESULT.md) ·
[Live status](/Volumes/Untitled/GitRx/backfill-20261107/status.json)

## Observed launch checkpoint

The worker was force-killed after 10 completed families / 32 new versions. The
supervisor restarted it automatically; it continued beyond 22 completed families /
58 new versions, and the active source database returned SQLite integrity `ok`.
A subsequent family-boundary checkpoint had 29 families processed, 77 new valid
ZIPs parsed (53 complete / 24 partial) and 21 retained-parent versions reused,
with zero parse failures or unavailable targets at that observation. These are
launch observations, not a full-run outcome. 49 final focused tests passed.
