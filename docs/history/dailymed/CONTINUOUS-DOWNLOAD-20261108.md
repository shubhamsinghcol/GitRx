# Continuous download correction — November 8, 2026

The downloader remained paused through native database freezing and completed-batch
SSD publication. Those steps do not write the live acquisition spool. Cross-batch
acquisition now resumes immediately after the short raw-file handoff, before the
native freeze and SSD copy. The existing pause remains around raw-file movement.

The scheduling wait now wakes at the next global pacing deadline rather than
rounding the 50 ms interval through repeated 20 ms sleeps. Repeated free-space
calls were moved inside the existing hot-storage 50 ms scan window and mobile
2-second scan window. Per-response and per-chunk download space checks remain.
No raw hash, HTTP observation, extraction or native release rule was removed.

46 focused checks passed for parallel transport, buffering, backfill and storage;
two explicit buffered-backfill cases additionally passed with an assertion that
cross-batch acquisition is active when native database freezing begins. Installed
preimages, module hashes and transport policy version 13 are retained under
`runtime/full-dailymed-backfill/continuous-download-20261108/`.

The warm live check lasted 90.385 seconds and crossed one completed-batch handoff:
733 HTTP starts (8.110/second), 404 successful ZIPs / 159.808 MB. The longest
request-start gap was 1.341 seconds, versus the prior measured 13.864-second gap.
Only one of 90 approximately one-second buffer samples reported paused. The
median start gap was 73.96 ms. This demonstrates removal of the observed long
handoff stall in this window, not sustained 20 starts/second or a tenfold gain.
Twenty network threads and a global 50 ms minimum interval remain configured.
The guardian was running with zero consecutive failures at sample end. An older
retained LAST-ERROR records upstream Retry-After; it predates this installation
and is not erased. Server-directed backoff remains authoritative.
