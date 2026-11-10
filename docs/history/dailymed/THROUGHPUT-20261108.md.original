# Cross-batch acquisition throughput, November 8, 2026

Owner request: approximately double measured DailyMed ZIP downloading throughput.
This extends only the existing pinned finite plan; no new scope or release promotion.

## Diagnosis and design

The previous 20-thread/10-Hz configuration averaged about 4.9 HTTP starts/second
in the inspected five-minute interval. ZIP downloads and native parsing were still
coupled through command interruption, whole-wave delivery, and the 250-family batch
boundary. A 20-Hz streaming-ready trial improved a 132.38-second sample to 12,727
ZIPs/hour and 4.58 GB/hour, but still idled after exhausting the active batch. That
trial was insufficient to establish the requested doubling.

Version 7 keeps twenty network threads and permits one global start every 0.05
seconds, at most twenty starts/second total. Adaptive pressure/slow-response backoff,
persisted Retry-After, HTTP/byte budgets, identity validation and durable response
retention remain. Completed responses become readable after their own ledger and
buffer commits, before unrelated requests finish. Native demand and pause commands
retain priority; ordinary bookkeeping does not interrupt a prefetch wave. A queued
consumer command can be canceled when its requested retained response becomes ready;
executing commands cannot be canceled that way.

Acquisition now seeds download hints from all remaining families in the immutable
finite plan once. The operational queue persists real native batch identity and
listing latest-version hints per task; a durable seed marker prevents reseeding on
every batch/restart. The queue can continue into future batches while the native
consumer validates/parses its current batch. Prefetched histories remain hints until
the unchanged native agreement/identity checks admit them. Ready raw downloads do
not count as successfully parsed families or release coverage.

Download-ahead is still bounded at 16 GiB, within the existing 20 GiB raw-work,
40 GiB combined local work, and 20 GiB local free-space guards. Acquisition pauses
and drains for batch handoff so no concurrent handoff races with active writers.
Future raw objects are retained and hash-verified even when handed off before their
native batch is processed. Body lookup checks raw staging, local completed CAS,
then the SSD CAS; it remains usable while the SSD is absent. Only immutable CAS
bodies opt into same-volume hardlink handoff, avoiding a second physical copy of a
large future buffer. Cross-volume transfer still copies/fsyncs/verifies. Selected
parser rules, raw bytes, prior frozen batches, manifests and releases are unchanged.

NLM API terms reviewed: https://documentation.uts.nlm.nih.gov/terms-of-service.html
(20 requests/second/IP stated there). DailyMed documentation reviewed:
https://dailymed.nlm.nih.gov/dailymed/app-support-web-services.cfm
The DailyMed page does not itself state a numerical rate allowance; the operational
ceiling is not a guarantee against throttling. Server backoff remains authoritative.

## Evidence

`runtime/full-dailymed-backfill/throughput-20261108/` retains source/installed
preimages, module hashes, version-5/6 policies and measurements, and final results.
Final validation and live measurements are appended after completion below.

## Final validation and observations

106 focused maintained tests passed before the cross-batch installation, including
actual 20-Hz/20-way mock overlap, global pacing, persisted server backoff, early
response delivery, queue restart/corruption/byte limits, cross-batch task identity,
future-body handoff and lookup, native processing/freezing/restart, offline mobile
work then verified SSD reconnection, guardian, recovery and HTTP accounting.
No held-out answers or excluded tests were opened.

A follow-up retained-path safeguard stores the original HTTP observation unchanged,
then gives the native history collector a canonical per-batch artifact locator.
This allows deferred history reads after the shared raw buffer moves. Its additional
regression and 24 backfill/recovery/inventory checks passed. Prior frozen records
are untouched. The installed preimage and updated hash are retained separately in
`stable-path-installation.json`.

Fresh pre-change sample: 162 ZIPs / 52.70 MB in 66.14 seconds (8,818 ZIPs/hour,
2.87 GB/hour), including one batch freeze. Final cross-batch sample: 869 ZIPs /
327.37 MB in 197.49 seconds (15,841 ZIPs/hour, 5.97 GB/hour), including one batch
freeze. All 1,503 HTTP observations started within the final window returned 200.
Peak ready queue during that window was 220 responses / 91.35 MB. These samples
contain different histories; they are not a controlled same-file benchmark.
Compared with the earlier conversational 11,415 ZIPs/hour / 3.49 GB/hour snapshot,
the final sample is about 39% faster by ZIP count and 71% faster by bytes. Compared
with the fresh baseline, it is about 80% faster by ZIP count and 108% faster by bytes.
No universal or sustained twofold completion-time claim follows from these windows.

The owner accepted the current speed during tuning, so further performance changes
were stopped. At that check the ready buffer had grown to 382 responses / 113.60 MB;
375 responses / 111.07 MB belonged to the next native batch. The parser queue was
empty, RSS approximately 1.20 GiB, no parser recycling, guardian consecutive failures
zero, and no completed batches waiting to archive. Coordination/durable writes and
safe batch handoff still introduce waits. Download-ahead is now building; its count
includes metadata responses as well as ZIPs. The normal guardian remains in control.
