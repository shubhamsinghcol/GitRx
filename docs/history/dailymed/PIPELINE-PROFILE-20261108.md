# Backfill pipeline profiling and transport version 5

November 8, 2026. Owner-authorized performance work on the existing finite job.
No new corpus scope, release promotion, parser rules, or evidence rewriting.

## Measurements

Evidence is in `runtime/full-dailymed-backfill/profile-20261108/`.
A 15-second macOS stack sample observed filesystem durability and file operations
on the coordinator. A bounded 150-family live cProfile run retained real acquisition,
validation, parsing, native checkpoints and evidence handling. It completed in
112.96 seconds, downloading/parsing 326 new versions and completing 150 families.
It did not cross a batch freeze. Startup/recovery is outside that timing.

The profile recorded approximately 64,000 HotStorage checks, millions of file-stat
calls, and approximately 1,800 RSS `ps` invocations. Threads overlap; cumulative
profile times must not be added or interpreted as wall-time percentages. These
measurements identify redundant operations, not a CPU-only diagnosis. A short
adjacent-family before/after comparison cannot prove a sustained corpus-wide rate.

## Changes

- Versioned transport policy 5 permits up to 20 simultaneous network threads.
  Global minimum start spacing stays 0.1 seconds, at most ten starts/second total;
  Retry-After/adaptive server backoff and aggregate request/byte caps remain.
  Twenty threads are capacity, not twenty continuously active requests. Fast
  responses need only a small number of overlapping connections at this rate.
- The 120-file download-ahead stop is disabled explicitly with JSON null. The
  response buffer grows within a 16 GiB byte budget. It still reserves 100 MiB per
  prospective response. Existing 20 GiB raw-work, 40 GiB combined work, and 20 GiB
  local free-space limits remain. The finite current 250-family batch still bounds
  lookahead; acquisition drains/pauses for safe batch handoff. This is not an
  unbounded whole-corpus download queue or concurrent cross-batch archiver.
- HotStorage uses directory-entry metadata rather than building Paths and issuing
  redundant type/stat calls for each file. Every write guard still checks capacity.
  A 1,000-check local benchmark counted identical bytes in 0.046 seconds versus
  0.112 seconds previously; this is a component result, not total-job speedup.
- Main-thread RSS checks reuse readings no older than 250 ms. The independent
  watchdog still obtains fresh readings on its 250 ms interval, and cached readings
  are evaluated against the same thresholds. This eliminates repeated `ps` forks
  immediately before/after small tasks. The 12-parser pool and memory limits stay
  as configured because this profile did not establish a parser-capacity shortage.

Source/policy preimages and installation hashes are retained in the evidence directory.
Original raw bytes, successful parse pins, frozen batches and selected release stay
intact. The installed six operational modules are updated only with the worker stopped.

## Validation

86 focused maintained tests passed across transport, actual 10/20-way overlap and
shared start spacing, queue backpressure/restart/corruption, uncapped file-count
buffering with enforced byte budget, native batch freezing/restart, processing
failure persistence, memory freshness/breach detection, staging, guardian, recovery,
controls, hot storage and raw journal. No sealed held-out answers/tests were used.
Live comparison and restored-guardian results are recorded below after verification.

## Live comparison and final display optimization

The adjacent post-change 150-family profile took 147.89 seconds, handled 456 new
versions, and crossed one complete batch freeze plus verified SSD handoff (batch
90). New-version throughput was 11,100/hour versus 10,390/hour in the earlier
segment. Family throughput was 3,651/hour versus 4,781/hour because these were
different histories, with 40% more new versions and a handoff in the later segment.
These are observations, not an attributable 7% improvement or family-rate regression.
RSS snapshot calls fell from 1,767 to 559 despite the larger/later workload.

A third change follows the measured progress-report overhead: acquisition display
publishes now run at most once per five seconds across family changes. This avoids
repeated full-plan state scans, ETA metadata commits and status-file fsyncs for each
small family/version. Native family/version checkpoints, counters, raw observations
and durable pins keep their existing cadence. Terminal/paused/waiting results still
publish immediately. The display can lag a few families; it is not the authoritative
queue. 38 focused backfill/control/guardian tests passed after this final change.
The original installed backfill module and its replacement hash are retained with
the other six operational module preimages/hashes. The normal guardian is restarted
with all seven modules; live normal-run verification follows in `live-summary.json`.

Final normal-guardian observation: 104 families and 265 new versions over 90.20
seconds, approximately 4,151 families/hour and 10,577 new versions/hour. This short
window included a 15-second low-frequency macOS stack sample, no completed-batch
handoff, and different source histories from both cProfile segments. It establishes
continued healthy work, not a controlled or sustained end-to-end speedup. Peak
coordinator-plus-parser RSS was 1.07 GiB; parser pool recycles and guardian
consecutive failures were both zero. At the saved endpoint 22,677 families had been
attempted, 8 failed extractions remained, 10 versions were unavailable, and no
completed batches were waiting for SSD transfer. The final stack sample shows much
of coordinator wall time waiting on synchronization plus remaining durable writes;
extra parser capacity was not established as the remedy.

The old recent-rate ETA temporarily includes these short restart/startup intervals;
it must not be presented as a clean post-change or last-hour performance estimate.
All seven installed module hashes match their recorded replacements; frozen parser
and normalizer hashes match their pre-change values. The worker and guardian remain
running normally after verification.
