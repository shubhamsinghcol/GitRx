# Download scheduling diagnosis and correction — November 8, 2026

Configured parallelism is not measured request-start throughput. The prior 20-lane,
0.05-second policy was active, but actual starts were about 5–8 per second.

Diagnosis found repeated hot-storage directory scans on native SQL guard calls
(roughly 70,000 checks in a 100-family instrumented run), bounded acquisition
waves that drained before selecting another group, and serial background history
confirmations. The thread profiler records overlapping activity across threads;
its cumulative times must not be added or treated as wall-time percentages.
Two initial profiling harness attempts failed before completing measurement:
Python 3.14 profiling-tool contention, then a missing multiprocessing main guard.
The corrected harness completed 100 families; text export failed on an empty
unused main profile. Acquisition stats remain retained, not a controlled speed
benchmark. No failure was converted into an admitted source success.

Changes installed with transport policy version 12 and local-storage policy 5:
- A bounded scheduling window of up to 200 tasks, further reduced by existing
  full-response byte reservations, feeds the same maximum of 20 worker threads.
  Finished lanes can take another task while a slow request remains in flight.
- Independent background history confirmations use the parallel pool with cache
  reuse disabled. First and second HTTP observations remain distinct and durable.
- Hot-storage directory scans can coalesce for at most 50 ms only with 256 MiB
  of headroom; explicit allocation checks and calls near the cap still scan.
  Free-space checks remain on every guard call. This is a sampled operational
  guard, not a guarantee against arbitrary concurrent external disk writes.
- Raw staging scans use DirEntry metadata once and serialize concurrent scans.
  A local read-only 20-iteration scan comparison measured 26.4 ms before versus
  11.0 ms after; this is scan performance, not network throughput.

45 initial focused checks passed for pool refill, hot storage, download buffering
and native backfill. The combined confirmation changes passed 38 focused checks
plus a separate fresh-observation regression. Sealed answers were not opened.

Final warm sample: 292 successful ZIPs / 98.975 MB in 60.309 seconds, 488 request
starts (8.092/second), median start gap 0.08044 seconds. The maximum gap was
13.864 seconds; sustained 20 starts/second is NOT established. At sample end
some newly started observations were still in flight, while completed observed
responses were HTTP 200. Different adjacent histories prevent a controlled speedup
claim. For context, the earlier unprofiled baseline was 554 ZIPs / 196.832 MB in
134.477 seconds, approximately 247 ZIPs / 87.8 MB per minute.

The job is running under its normal guardian, with zero consecutive failures at
final observation. All versioned policies, installed preimages/module pins and
measurements are retained under
`runtime/full-dailymed-backfill/download-diagnosis-20261108/`.
No extraction rules, selected release, frozen old evidence or corpus scope changed.
