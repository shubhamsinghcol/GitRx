# DailyMed SSD incident recovery and faster resumable acquisition

The November 8 SSD interruption corrupted the mutable HTTP SQLite ledger. APFS verification succeeded; the control database, immutable batch 1, and active batch 2 passed integrity checks. Original damaged files and journals remain preserved under the SSD job's incidents directory.

Offline SQLite recovery yielded 1,420 request rows. Twelve additional rows were reconstructed from independently retained original source observations. Ten missing response bodies were fetched again and matched the original SHA-256 exactly. The verified ledger contains 1,432 requests; 1,123 unique referenced bodies and 1,431 source observations were checked. Proof: `runtime/full-dailymed-backfill/recovery-20261108/VERIFIED-RECOVERY.json`. This verifies retained evidence, not unknowable activity that never committed.

## Changes now installed

Active SQLite databases, WAL files, pacing state and parser temporary files reside on the laptop. External database paths resolve to these canonical local working files until completed batches freeze to verified regular SSD files. Original external databases are retained in migration history. Recent raw downloads additionally pass through a bounded 512 MiB local write journal; this is not a complete independent archive backup.

A login LaunchAgent supervises the same finite, pinned job. It respects a durable Pause marker, waits for the correct SSD and network, retries transient failures, watches worker heartbeats, and invokes conservative recovery for recognized storage damage. Recovery requires original byte hashes; changed upstream bytes cannot silently replace missing evidence. Unproved integrity failures stop for attention. Unexpected worker termination kills the remaining owned process group before retry. A shut-down laptop resumes after user login; sleep/lid closure can suspend execution. No software guarantees against physical device failure or arbitrary filesystem loss. Caffeinate inhibits idle sleep only while the worker runs.

Desktop executable controls:

- `/Users/shubham/Desktop/GitRx Start.command`
- `/Users/shubham/Desktop/GitRx Pause.command`
- `/Users/shubham/Desktop/GitRx Progress.command`
- `/Users/shubham/Desktop/GitRx Health.command`

Progress distinguishes finite-plan attempts, captured family union coverage, partial extraction and staged data. Health checks process identity, guardian/worker heartbeats, volume identity and space; it is not full-corpus qualification. Cached progress remains available while disconnected.

## Speed and resources

A persistent HTTP connection replaces repeated setup. Adaptive request spacing starts at one second and halves after each 300 clean successes toward 0.2 seconds: a 5-request-starts/second ceiling, ten times the former 0.5/second ceiling. Server Retry-After, failures and slow-response backoff remain enabled. This is a ceiling, not a measured tenfold whole-job improvement. First ten new requests after recovery succeeded, with recorded response latencies approximately 0.11–0.63 seconds. Telemetry records actual spacing, latency and connection reuse.

Parsing supports up to six subprocesses, one database writer, a bounded queue, approximately 1 GiB monitored worker RSS each, 2 GiB coordinator allowance and 8 GiB aggregate monitored RSS. Monitoring is not a kernel memory reservation. macOS chooses performance/efficiency cores; exact 4P+2E affinity is not promised. Families with few versions may use fewer workers. Temporary parser storage is bounded to 3 GiB locally; hot databases have an 8 GiB allowance, with a 20 GiB laptop free-space floor and 20 GiB SSD reserve.

Validation: 102 focused maintained tests passed, including raw journaling, hot storage, recovery, supervision, controls, processing and acquisition. Real Start/Pause/Health checks succeeded. A deliberate unexpected worker termination is recorded separately in `runtime/full-dailymed-backfill/recovery-20261108/RESTART-TEST.json`; consult that proof for its observed outcome.

At recovery: 307 finite-plan families completed, 810 new valid versions (663 complete parses, 147 partial), 63 reused parent versions. Captured family union: 7,533 of 158,982 (4.738%); archived-only inventory remains unknown. These staged additions do not change the selected application release. Collection and parsing are algorithmic local code, without model/API calls. No release promotion or publication occurred.
