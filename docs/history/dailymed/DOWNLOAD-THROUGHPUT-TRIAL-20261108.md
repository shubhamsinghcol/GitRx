# Download throughput trial — November 8, 2026

The owner requested at least tenfold download volume. This target was not achieved.

A fresh baseline retained 554 ZIPs / 196.832 MB in 134.477 seconds
(247.18 ZIPs / 87.82 MB per minute). A trial batched independent history
confirmations through the existing globally paced parallel pool, bypassing the
first observation cache. Its live sample retained 348 ZIPs / 122.457 MB in
134.180 seconds (155.61 ZIPs / 54.76 MB per minute). Both windows included
one batch freeze and different adjacent families; they are not identical-input
benchmarks. All recorded responses in both windows were HTTP 200. There was
no evidence of an improvement, so the runtime and source changes were reverted.

The initial 38 focused checks plus two added regressions passed; a subsequent
34-check transport/recovery/raw-evidence run also passed. Those checks establish
fixture correctness, not a throughput improvement. Trial-only tests were removed
with their implementation. Original source modules match retained installed
preimages. Rollback keeps monotonically increasing transport policy version 10,
with the original 20 lanes and 0.05-second minimum spacing. The guardian resumes
the same finite job; evidence, parser rules and selected release are unchanged.

The existing global ceiling is 20 starts/second: at most 1,200 total HTTP starts
per minute before metadata, retries and coordination costs. Ten times the fresh
baseline would require about 2,472 individual ZIPs per minute, already exceeding
that ceiling even without metadata. Achieving the requested target requires a
substantially different transfer path or a justified revised upstream rate budget;
no numerical DailyMed rate permission was established by this trial. Bulk current
releases do not establish a replacement for historical-version acquisition.

Retained evidence: `runtime/full-dailymed-backfill/confirmation-pool-20261108/`
contains preimages, installation/rollback pins and comparison. Detailed windows
are `runtime/full-dailymed-backfill/throughput-20261108/{before,after}-confirmation-pool.json`.
