# Local execution of the proposed 100-family validation

The implementation is `gitrx_backend/validation_study.py`. It uses Python and the
existing shared native query library. No agent, model API, API token, browser, or
network request is required by the runner. Nothing starts on import or at login.
It never collects labels, refreshes sources, or promotes releases.

**Current state: built, not started.** The starter plan deliberately contains
invalid placeholder release/configuration values and an empty candidate frame.
No real study sample or score exists. The automated tests use synthetic labels.

## What code does and what reviewers do

| Local code | Independent reviewer work |
|---|---|
| Seeded stratified selection from an imported candidate frame | Assemble/verify SrLC records and application-to-setid mapping |
| Freeze plan, software, config, and local source-file hashes | Define clinically meaningful warning changes and eligibility |
| Read every captured version of selected histories | Check originals against extracted text, tables, and boundaries |
| Fully paginate inventories, section text, phrase histories, changes | Decide whether differently worded passages represent the same risk |
| Preserve citations, dates, extraction diagnostics, and ambiguity | Match clinical events and adjudicate source differences |
| Checkpoint and resume without repeating successful tasks | Independently reconstruct the captured-history timing reference |
| Compare independently transcribed source fields and dates | Preserve baseline presence, missing history, and uncertainty |
| Calculate endpoint summaries from adjudications | Explain substantive discrepancies |

This validates a GitRx-assisted workflow. It does not introduce an automated
clinical classifier. Event TP/FP/FN and SrLC correspondence are supplied by
adjudicated human event matching; the code checks counts and computes metrics.
Timing entries describe adjudicated events, with observed publication dates
checked against collected source-backed version metadata. Exact cross-source
regulatory dates need not agree with DailyMed dates.

## Before any real execution

Read `VALIDATION-100-FAMILIES-PROTOCOL-DRAFT.md`. Use 10 separate development
families to finalize definitions. Keep all original held-out material separate.
The planned sample is 50 SrLC-positive, 30 comparison, and 20 distinct challenge
families; no unidentified missing cases are silently replaced.

Populate `studies/validation-100/plan.json` with an existing explicit backend
configuration path, snapshot SHA-256, and valid follow-up cutoff. Set
`evidence_files` to retained local copies of the sampling/reference-source frame
and relevant source records. Freeze hashes detect later changes to these files;
URLs alone do not freeze a remote webpage. Copies of reviewer source documents
must be retained and listed in the evidence register.

The runner does NOT download or scrape SrLC. Import locally saved source records
or prepare them separately before freezing. It also does not infer application
mapping or label clinical outcomes. This avoids disguising unvalidated name
matching or text similarity as a reference standard.

Candidate records can be entered directly in the plan or imported from CSV.
Required CSV columns: `id,setid,stratum,mapping_status,mapping_evidence,source_refs,code,phrases`.
Optional columns include `application,product`. The three evidence/phrase columns
contain JSON arrays, using standard CSV quoting. `code` is usually `34066-1` for
this protocol. Strata are `srlc_positive`, `comparison`, `challenge`; mapping
status is `verified`, `ambiguous`, or `unresolved`. Evidence references and unique
canonical setids are mandatory. Retain uncertain mappings in the disposition.

Use an adequate frame rather than selecting only successful GitRx examples.
The import command validates and writes a NEW plan; it does not run queries:

```sh
.venv/bin/python -m gitrx_backend.validation_study import-frame \
  --plan studies/validation-100/plan.json \
  --csv /absolute/path/candidate-frame.csv \
  --output studies/validation-100/populated-plan.json
```

## Commands for later — not executed during implementation

From `/Users/shubham/Desktop/GitRx`, freeze the sample:

```sh
.venv/bin/python -m gitrx_backend.validation_study freeze \
  --plan studies/validation-100/populated-plan.json \
  --output studies/validation-100/manifest.json
```

Make independent reviewer forms before sharing predictions:

```sh
.venv/bin/python -m gitrx_backend.validation_study review-forms \
  --manifest studies/validation-100/manifest.json \
  --output studies/validation-100/review-forms
```

These forms omit GitRx predictions, search phrases, and sampling strata. Separate
reviewer A and B files are for independent abstraction; the adjudication file is
completed afterward. Do not copy GitRx text into independent source-reference
fields. Reviewer independence is a workflow responsibility; software cannot
prove it from reviewer names.

When authorized, run one family, then continue with the same command without the
case limit. This is local archive reading only:

```sh
.venv/bin/python -m gitrx_backend.validation_study run \
  --manifest studies/validation-100/manifest.json \
  --output studies/validation-100/predictions --max-cases 1
```

```sh
.venv/bin/python -m gitrx_backend.validation_study run \
  --manifest studies/validation-100/manifest.json \
  --output studies/validation-100/predictions
```

Stop with Ctrl-C; completed tasks are retained. Repeat the run command to resume.
Interrupted cases without a final status resume automatically. Explicit errors
remain errors unless `--retry-errors` is supplied. Configuration, software,
frozen source-file, and completed-checkpoint drift cause refusal. Interrupted
in-progress tasks are recomputed; completed results are not overwritten.

A family can require many versions; `--max-cases 1` is a family limit, not a time
limit. Each query has a frozen timeout. `max_versions`/`max_pages` bound traversal;
exceeding them records an error, not a successful incomplete scan. The runner is
single-process to minimize resource competition with acquisition. Local files
must remain available; do not disconnect their storage during a run.

Read disposition without starting queries:

```sh
.venv/bin/python -m gitrx_backend.validation_study status \
  --manifest studies/validation-100/manifest.json \
  --output studies/validation-100/predictions
```

## Reference fields and scoring

The starter `reference-example.json` documents the adjudication schema. Use only
finalized case rows when creating a partial reference file; keep unfinished rows
in the reviewer forms. Two distinct reviewer identifiers, `adjudicated: true`,
and source references are required for scored reference cases.

- `fidelity`: independent source transcriptions with `version`, `ordinal`, and
  one or more `expected_text`, `expected_title`, `expected_source_code`,
  `expected_parent_ordinal`, or `expected_ancestry`. Whitespace alone is normalized
  in text checks. Medical wording, numbers, units, and punctuation are retained.
- Version-level fidelity: use `ordinal: null` with `expected_setid`,
  `expected_version`, `expected_publication_dates`, `expected_effective_date`,
  `expected_raw_sha256`, or `expected_section_code_counts`. Counts are keyed by
  source code, using `<uncoded>` for missing codes. Independently confirm clinical
  section context; a source-code count is not that assessment. Keep original date
  formats in expected fields; do not mix effective dates and publication dates.
- `event_counts`: `status: evaluable` plus nonnegative integer `tp`, `fp`, `fn`
  against independently adjudicated original-document transitions. Preserve the
  underlying event-by-event matching register in source references. Uncertain
  histories have `status: unknown`; they are never assumed negative.
- `srlc_counts`: `status: evaluable` plus `matched`, `unmatched_reference`,
  `unmatched_gitrx`, `unknown`. Unmatched GitRx events are not automatically false
  positives. Retain event mappings and discrepancy causes in the source register.
- `timing`: `status: evaluable`, positive `reference_version` and
  `observed_version`, ISO `reference_date` and `observed_date`, source references,
  and `uncertainty` (`bounded`, `first_observed`, or `complete_captured`). Optional
  `regulatory_date` and `communication_date` support cross-source intervals.
  Unknown/baseline/censored cases should remain non-evaluable with their explicit
  status and notes, rather than fabricated dates. "Complete captured" refers to
  selected captured evidence, not complete upstream history.

After unblinding and adjudication, calculate a new immutable report:

```sh
.venv/bin/python -m gitrx_backend.validation_study score \
  --manifest studies/validation-100/manifest.json \
  --output studies/validation-100/predictions \
  --reference /absolute/path/adjudicated-reference.json \
  --report studies/validation-100/results-original.json
```

Reports preserve all 100 dispositions, endpoint-specific denominators,
case-level fidelity/timing results, SrLC correspondence counts, and summaries by
stratum. Reports refuse overwrite. They are not automatically a publication-ready
analysis: no pooled population PPV is inferred from enriched sampling. Wilson
intervals on family agreement assume independent families. Event/timing metrics
remain descriptive pending appropriate clustering for shared applications,
within-family events, and class-wide regulatory actions. No sample-size adequacy
or power claim is implied by choosing 100 families.

Per-version full text is reconstructed across section/text pagination. Change
and phrase outputs can contain bounded excerpts; `truncated_rows` and original
query diagnostics remain visible. Pagination completion never proves extraction
accuracy, upstream completeness, or semantic equivalence. Full version text is
available for adjudicating an excerpt-truncated change.

The tests are `tests/test_validation_study.py`; they use synthetic local release
fixtures only. Do not run any excluded historical held-out tests.
