# Maintained native workload qualification

`python -m gitrx_backend.qualification --plan PLAN.json --output RUNS.jsonl`
runs explicit offline cases in fresh main-thread Python processes. Outputs are
append-only in a separate qualification ledger. Evidence/export roots and existing
non-qualification ledgers are refused. No collection or promotion occurs.

A `gitrx-qualification-plan-1` plan names configuration profiles and 1–100 unique
cases. Each case supplies `id`, `profile`, explicit `backend`, shared query `method`
and schema-validated `arguments`. Optional fields are `traverse` (`first`/`all`),
`max_pages` (up to 10,000), `repeat` (1–10), query `timeout_seconds` (up to 60), whole
worker `max_seconds` (up to 3,600), and `equivalence_group`.

Full traversal supports native search, phrase histories, changed-text search,
versions and comparison/change-detail rows. Exhausting a page budget is a failed
measurement, never a completed scan. Row text and located excerpts may still be
truncated; the output records that separately. Case-sensitive original evidence
and locations participate in streaming result digests. Adapter-specific commit/
parse representation keys are omitted for cross-reader equivalence; retained
raw hashes, snapshot identities and shared evidence metadata remain compared.

Each case records reader startup including configured hash checks, the first
query/traversal, subsequent warm repetitions, serialized byte totals, record/page
counts, exclusion/truncation counts and fresh-process lifetime peak RSS. OS caches
are uncontrolled. RSS excludes Git children and is not an aggregate memory budget.
An optional `concurrency` block names one case and 1–8 independent reader processes,
recording per-worker results/RSS and joint wall time. This is local contention,
not hosted capacity or a percentile service objective.

An optional `transport` block supplies `profile`, `backend`, `sid`, `version` for
an actual stdio session: pinned read → long corpus scan → overtaking ping → cancel
→ pinned recovery read. It records response IDs and observed latency and refuses
to call cancellation successful if the query replied. Use a sufficiently large
unaccelerated profile for this probe. Synthetic deterministic cancellation/fault
coverage remains in the portable regression suite.

Plans, exact profiles, data/software hashes and hardware/runtime accompany every
run. `equivalence_group` requires identical normalized ordered result digests;
first/warm repetitions must also agree. The existing performance ledger supplies
relative/absolute regression comparisons for its fixed workloads. Qualification
runs are additional scope measurements, not automatic speed-regression claims
between different plans, machines, datasets or response shapes.

Current evidence and actual remaining operating limits:
[November 7 qualification](../reports/qualification-20261107/REPORT.md).
