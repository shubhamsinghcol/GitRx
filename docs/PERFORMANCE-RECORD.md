# Performance ledger (for maintainers)

`gitrx_backend.benchmark` times a small fixed set of queries and appends one JSON row to a separate `.jsonl` ledger.
It makes no network requests and changes no data.

```sh
.venv/bin/python -B -m gitrx_backend.benchmark \
  --config runtime/native-config.json --backend sqlite \
  --setid 8ff46193-2a3b-4de0-9572-775dda8cd8b2 \
  --phrase seizures --repeat 3 --output reports/performance/runs.jsonl
```

Repeat `--setid` (up to 20 families) and `--phrase` (up to 10). The backend (`git` or `sqlite`) must be given; this tool does not read the default rebuild release. The workload is the latest exact reads,
earliest-versus-latest comparisons, and for each phrase the first 50-version history page, so timings do not represent
exhaustive traversal or all-corpus search. A new reader startup is timed separately (not cold-disk: OS caches are not cleared).
Each operation gets one warm-up and then timed repeats; the row keeps each sample, the median, response size, a result digest and peak memory.

Two runs are comparable only if their descriptor matches (release, backend, projections, cache settings, workload, runtime,
repeat count, timeout). Otherwise the row starts a new baseline. A regression needs both a factor above 1.5 and an increase above 0.1 s
(`--regression-ratio`, `--regression-seconds` change this). A regression or changed digest still appends the row and exits 1;
treat it as a signal to review, not proof of a wrong answer. Do not run two writers on one ledger.
