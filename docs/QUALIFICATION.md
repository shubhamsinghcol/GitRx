# Workload qualification (for maintainers)

`python -m gitrx_backend.qualification --plan PLAN.json --output RUNS.jsonl` runs offline query cases, each in a fresh
Python process, and appends results to a separate ledger. It collects and promotes nothing, and refuses output
inside evidence or export folders.

A plan (`gitrx-qualification-plan-1`) names configuration profiles and 1 to 100 unique cases. Each case has an `id`,
`profile`, `backend`, query `method` and `arguments`; optional: `traverse` (`first` or `all`), `max_pages` (up to 10,000),
`repeat` (1 to 10), `timeout_seconds` (up to 60), `max_seconds` (up to 3,600), `equivalence_group`.

What it records per case:

- reader startup, first query or full traversal, warm repeats, response sizes, page and record counts, truncations, and peak memory
  of the fresh process (excluding Git children; OS caches are not controlled);
- result digests, so cases in one `equivalence_group` (for example the Git and SQLite readers) must give identical normalized answers.

Running out of page budget is a failed measurement, never a completed scan. Optional blocks measure concurrent readers
(1 to 8, local contention only) and a real stdio session (read, long scan, overtaking ping, cancel, recovery read).
Results are scope measurements for the stated plan and machine, not service objectives. Current evidence:
[November 7 qualification](../reports/qualification-20261107/REPORT.md).
