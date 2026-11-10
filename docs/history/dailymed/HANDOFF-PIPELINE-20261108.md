# Download-to-parser handoff correction

November 8, 2026. Owner requested repair of the persistent wait between downloaded
source material and parser work. Changes apply to the same finite pinned job.
No new corpus scope, selected-release promotion, extraction rules or frozen evidence
were changed.

## Measured cause

A timed 60-family baseline took 45.38 seconds and handled 124 new versions. The
native history collection/confirmation step consumed 27.60 seconds, averaging
460 ms/family. Direct ZIP admission to parser submission averaged 34.39 ms; native
ZIP validation averaged 8.00 ms. More parser processes would not address the
largest measured wait. The coordinator also drained each family's parser jobs
before admitting the next family, leaving otherwise usable workers idle.

Evidence: `runtime/full-dailymed-backfill/handoff-20261108/before.json` and `after.json`.
Measurements include nested functions and must not be added as disjoint percentages.

## Changes

Transport policy version 8 retains twenty download threads and the existing global
0.05-second start interval. It enables background history confirmation for single-
page inventories. Each confirmation is a distinct, later HTTP request tied to its
first observation. Both bodies/hashes/headers/request IDs remain retained. Native
history collection still checks complete agreeing inventories, identities, raw row
counts and pagination; changed/unstable/multipage histories use the existing fresh
fetch and third-pass behavior. Native pass start times use the actual retained
request-start observations when replaying a prefetched pass. ISO microsecond
rounding is allowed only at the one-microsecond comparison boundary. This does not
reuse one HTTP observation as two confirmations or invent publication information.

Processing policy version 3 allows twelve families in flight instead of waiting
for each family before admitting another. The existing twelve parser processes,
24-task queue, spool limits and 8 GiB aggregate RSS budget remain. One coordinator
still owns source SQLite. A family only becomes terminal after its native version
pins/parses are durable. Batch changes, freezing and bounded/manual pause results
drain all affected work first. In-progress families and retained spool/observations
are reconstructable on restart; failed extraction and quarantine outcomes retain
their existing meanings. Status exposes pipeline capacity and families in flight.

Future ready raw bodies remain locally available across completed-batch SSD
handoff, so disconnecting the SSD does not strand the prefetched queue. Older
external-only ready bodies are rehydrated as a bounded local cache. Canonical
per-batch history locators survive raw-buffer movement while original HTTP
observations remain unchanged.

## Validation and live result

117 focused maintained checks passed, including independent dated confirmation
observations, no extra fetch when a valid confirmed pair is ready, native inventory
persistence, early cross-family parser admission, bounded pause, interrupted-pipeline
restart, source/derived counts, offline-to-SSD transitions, future-body retention,
HTTP budgets/backoff, processing rescue, native freezing and recovery. Sealed
held-out answers and excluded tests were not accessed.

The adjacent post-change 60-family run took 18.46 seconds and handled 144 new
versions. Mean history collection fell from 460 to 69.74 ms/family; direct ZIP
admission-to-parser submission fell from 34.39 to 22.40 ms. The observed family
rate was approximately 2.46 times the baseline. These were different adjacent
families, not an identical-input or whole-corpus benchmark; no sustained completion
speed guarantee follows. Original parser and normalizer hashes remain unchanged.
The normal guardian is resumed after comparison; its fresh observation is retained
in `normal-guardian.json` in the same evidence directory.
