# Buffered acquisition and twelve parser workers

November 8, 2026. The owner authorized independent download-ahead buffering, ten
parallel download lanes at 0.1-second shared spacing, and twelve parser workers.
This extends the earlier two-lane delivery; no selected release is promoted.

## Independent stages

A dedicated acquisition thread owns HTTP and its ledger. Up to ten network threads
transfer raw responses, with one shared start interval and aggregate request/byte
budgets. Ten lanes means at most ten simultaneous responses and at most ten new
request starts per second total at the fastest setting. Fast responses may need
only one or two active connections to sustain that start rate.

The acquisition thread reads ahead across families in the current 250-family batch,
using raw history pages only as retrieval hints. The unchanged native consumer
still checks family identity, captures agreeing complete inventories, admits and
validates raw ZIPs, parses, records limitations, and saves durable native version
pins. A hinted version is not an admitted version or a completeness claim.

Downloaded responses are kept locally in the content-addressed store, with their
original HTTP observations. An independently durable operational queue records
request IDs and ready/consumed state under raw-staging/buffer/download-buffer.sqlite.
Ready responses can be read without waiting for acquisition or parser work. Consumer
requests take priority over speculative downloads; a prefetch wave stops launching
new work when a consumer command arrives, retaining its already-started responses.

The prefetch buffer pauses at 120 unconsumed responses or a 1 GiB response budget,
reserving up to 100 MiB per new transfer before starting a wave. Demand-driven
consumer downloads can additionally occupy one bounded lane group. The overall
20 GiB local raw-work cap and 20 GiB free-space floor still apply. Acknowledging
versions/families frees queue capacity; it does not delete original evidence.
Queue metadata is operational, not a replacement source-evidence database.

The acquisition thread pauses and drains before each completed batch handoff.
It does not run ahead into the next batch until the consumer activates that batch.
Consequently unfinished future-batch raw work is not swept into an earlier completed
batch. After hash-verified external handoff and the normal native batch freeze,
the next batch starts. SSD absence still pauses the finite job; metadata and parent
records remain on the SSD. Existing source bytes and frozen batch pins remain intact.

## Parser capacity

The installed policy increases maximum parser subprocesses from six to twelve and
pending parse tasks from twelve to twenty-four. On this 16 GiB, 10-core laptop,
aggregate monitored coordinator-plus-parser RSS stays capped at 8 GiB: 2 GiB for
the coordinator and 512 MiB per parser worker. Workers are spawned as needed; twelve
configured workers does not mean twelve busy CPUs, twelve concurrent families,
or twice the throughput. Native family result accounting still drains that family's
parses before finalizing its outcome. The download queue independently crosses
family boundaries even while that native accounting remains sequential.

Existing per-task deadlines, bounded retries, failed-version earmarks, repeated
failure circuit breakers and integrity/systemic stops remain. Memory thresholds
are monitored, not kernel reservations. Other archive/service parser profiles are
unchanged; this policy applies to the finite backfill worker.

## Status and verification

GitRx Status shows lanes, start interval, buffered file count/bytes and worker state.
The queue can be empty when the consumer keeps up; a full buffer pauses acquisition
rather than growing without bound. ETA still uses observed native family progress,
not download counts, so it is meaningful only after enough active samples.

Focused tests cover ten overlapping transfers with shared actual start spacing,
prefetch interruption for consumer priority, acquisition while the consumer is idle,
backpressure, queue restart/cache reuse, corruption refusal, twelve-worker extraction,
and complete buffered native processing across batch handoff/restart. Retained live
installation and verification records are in runtime/full-dailymed-backfill/buffered-20261108/.
