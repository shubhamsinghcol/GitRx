# Repeatable native performance record

`gitrx_backend.benchmark` measures an explicitly selected small family cohort and
appends one JSON object to a separate `.jsonl` ledger. It uses configured native
readers and the shared Query library. It makes no network requests, collects no
new labels, and does not change evidence, indexes or selected releases.

```sh
.venv/bin/python -B -m gitrx_backend.benchmark \
  --config runtime/native-config.json --backend sqlite \
  --setid 8ff46193-2a3b-4de0-9572-775dda8cd8b2 \
  --phrase seizures --repeat 3 --output reports/performance/runs.jsonl
```

Repeat `--setid` for a fixed cohort (maximum 20 families) and `--phrase` for literal
wording (maximum 10). The backend is required; it never silently switches. Without
phrases the workload contains only latest exact reads and earliest/latest captured
version comparisons. For each phrase it also reads the first 50-version history
page. Exact reads and comparisons use fixed text/record limits. Continuation and
truncation remain in the recorded results: these timings do **not** represent
exhaustive history traversal, an all-corpus search, or complete result export.

One new reader startup is timed separately, including configured index hash
verification. This is a cold *application reader*, not a cold-disk experiment:
the tool does not clear OS caches. Each operation gets one unmeasured warm-up,
then three timed calls by default. The ledger retains each sample, its median,
serialized result size and a canonical result digest. Process-lifetime high-water
RSS is recorded in bytes where supported; it includes earlier Python work and
excludes Git children, so it is neither per-query memory nor a memory budget. It checks repeat answers
agree. A digest equality is an engineering consistency check, not proof that
extraction or interpretation is correct.

## Comparable runs

A comparison requires an identical descriptor: selected snapshot, backend and
Git/meta pin where relevant, metadata/search-projection identities, cache settings,
workload and bounds, runtime/hardware description, repetition count, timeout,
cache conditions, and benchmark harness identity. Query software hashes and package
version are recorded separately so a software change can be measured against its
previous implementation on the same data. A changed cohort, release, backend or
harness establishes a new baseline instead of producing a misleading regression.

The baseline is the last comparable ledger row. The default slowdown threshold
requires **both** a factor greater than 1.5 and an increase greater than 0.1 seconds.
These tolerances suppress very small timing noise; they are not user-facing SLOs.
`--regression-ratio` and `--regression-seconds` allow an explicit experiment to use
other tolerances. Timings include local system noise and are not production p95.

A run is still appended when it finds a regression or changed result digest, and
the CLI exits 1. Review those findings: a changed schema, policy or intentional answer correction
can change a digest. This is a review signal, not an established incorrect answer;
a faster wrong answer is not a successful optimization. An exception
while measuring produces no completed row. Do not run multiple writers against one
ledger. The command rejects output inside configured evidence/export roots and
refuses to append to an existing non-performance JSONL file.

This is an explicitly invoked developer tool, not a scheduler, monitoring daemon,
capacity test or hosted-service qualification. Existing broader measurements remain
in their original dated reports; this ledger supplies continuity for future runs.
