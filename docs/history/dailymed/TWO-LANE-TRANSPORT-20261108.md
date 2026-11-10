# Two-lane download transport — November 8, 2026

The owner requested 0.1-second request spacing and two simultaneous download lanes. Version 3 of the live transport policy explicitly enables both, with ten request starts/second as a combined maximum across the lanes. This is a ceiling, not a guaranteed measured rate.

The backfill groups up to two missing versions of the current family. Two reusable connection threads download those ZIPs concurrently. The coordinator retains exclusive ownership of SQLite writes, request IDs, shared request/byte budgets, response evidence admission and adaptive pacing. Actual wire starts also share a spacing lock. History inventory passes and bounded archive fallback remain sequential; families with only one missing version use one download lane. The six parser subprocesses remain separate from download threads.

Both responses, including failures, are retained before a group-level failure propagates. Successful cached responses survive restart and are reused by hash. Shared Retry-After prevents further request starts after the instruction is observed; the other already in-flight request may finish. Adaptive pressure/slow-response backoff applies to both lanes, and an active server backoff survives a versioned policy upgrade. Redirects and retryable failures use the existing bounded official-host transport after the current group has drained.

The request interval is now 0.1 seconds at its configured fastest setting. Existing budget, source identity, frozen parser, local staging, batch handoff, failure deferral and systemic-stop rules remain in effect. No release promotion or publication is part of the change.

Validation includes demonstrated overlapping mock network responses, minimum actual/ledger start spacing, shared request-budget enforcement, retained responses during failure, persisted Retry-After, cache reuse, complete two-lane backfill evidence/freezing, and the maintained processing/guardian/recovery/staging/release tests. Installation and live verification evidence is retained under runtime/full-dailymed-backfill/two-lane-20261108/.
