# Historical pilot workflow and artifacts

This preserves the former README milestone narrative as of November 6, 2026.
Its deferred-feature, coverage and validation statements describe earlier stages,
not the current runtime. Use the [current README](../README.md) and
[build status](BUILD-STATUS.md) for supported entry points and present scope.
Commands below include acquisition and parser drivers; they are historical
reproduction instructions, not read-only query startup. Frozen reports remain
unchanged.

Steps A–D: the accepted 20-family corpus, preserved version-specific
ZIPs, provenance/identity checks, SQLite section-instance extraction,
five source/extraction samples, explicit section inference/scope, and the
snapshot-pinned phrase-history CLI. Read [SPEC.md](../SPEC.md) for the promises and
limitations. No clinical, legal-applicability, or historical-completeness
claim is made.

## Local artifacts

- [reports/E1C-ARTIFACT-RECONCILIATION.md](../reports/E1C-ARTIFACT-RECONCILIATION.md):
  the original 65 extra CAS objects, subsequent additions, and proof that they
  do not inflate downloaded/parsed document coverage. This is an accounting
  audit, not validation of label text or classifier predictions.
- [reports/E1F-REVIEW.md](../reports/E1F-REVIEW.md): current fail-closed confidence
  semantics, confidence census, review-only lint and the blank 60-case human
  validation packet. No precision/recall is available before human labels.
- [reports/STEP-D-FULL-OUTPUT.md](../reports/STEP-D-FULL-OUTPUT.md): complete,
  unabridged text output for all six cases and scope/heading/any-of/inference
  variants, plus linked JSON outputs.
- [reports/STEP-D-REVIEW.md](../reports/STEP-D-REVIEW.md): findings, FDA comparison,
  per-case limitations, ranked surprises and assumptions.
- [reports/E1B2-REVIEW.md](../reports/E1B2-REVIEW.md): structural section history,
  heading-only notices, exact Unicode distinctions, golden classification
  and the targeted original-XML audit.
- [reports/E1E-REVIEW.md](../reports/E1E-REVIEW.md): accepted earlier product-section context
  semantics, AMBIEN v17 correction, all-family code/context scan, full outputs
  and golden classification. Earlier code-only outputs remain legacy evidence.
- [reports/B2-404-INVESTIGATION.md](../reports/B2-404-INVESTIGATION.md): alternate
  latest-version routes work, but adoption awaits approval; archive unchanged.
- [reports/C2-AUDIT-AND-PARTIALS.md](../reports/C2-AUDIT-AND-PARTIALS.md): five-sample
  fidelity review, all 21 partial causes and 228 inferred / 3 unknown narrative
  identities. Model review does not constitute independent clinician review.

- `data/gitrx.sqlite`: SQLite record and FTS5 index.
- `artifacts/sha256/<prefix>/<sha256>`: immutable exact response bodies and
  retained parser/vocabulary source artifacts.
- [reports/STEP-B.md](../reports/STEP-B.md): per-family download outcomes, every
  unavailable version's oldest/middle/newest position, dates and code flags.
- [reports/STEP-C.md](../reports/STEP-C.md): extraction counts and diagnostics.
- [reports/audit/index.html](../reports/audit/index.html): five side-by-side XML
  and extraction pairs; open locally in a browser for manual review.
- [reports/audit/CHECKLIST.md](../reports/audit/CHECKLIST.md): human audit remains
  pending; automated structure checks do not count as clinical review.
- `reports/SNAPSHOT-ID.txt` and `reports/snapshot-<id>.json`: frozen evidence
  checkpoint, including failures, history responses and parser configuration.
- `reports/QUERY-SNAPSHOT-ID.txt`: D checkpoint including identity policy,
  Unicode/engine identity and checksums of actual query inputs.

Raw data and the SQLite database are intentionally local and ignored by Git.
This checkout has not been publicly published. Its future Git export is a
separate Step E deliverable; this source checkout is not that export.

## Reproduce or resume

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m gitrx.ingest
.venv/bin/python -m gitrx.report --stage b
.venv/bin/python -m gitrx.parser
.venv/bin/python -m gitrx.report --stage c
.venv/bin/python -m gitrx.audit
.venv/bin/python -m pytest -q
```

The ingest is sequential and conservatively rate-limited. Successful raw
retrievals are reused after validation; interrupted histories/downloads
resume. Failed retrievals have a persisted `next_retry_at` with backoff.
`python -m gitrx.ingest --retry-due` retries only eligible failures; it does
not bypass their schedule. `--refresh-history` starts a fresh history
observation run after the previous run completes. Reports read persisted
evidence and make no network requests.

The initial corpus proposal is pinned in [research/families.json](../research/families.json).
Stage A probe successes are imported with their original URLs, timestamps,
headers, hashes and attribution as Step A observations. Only successful
matching raw label artifacts are reused; unavailable versions have no text.

## Start with section existence, then search literal phrases

Start by asking whether a source-coded or visibly inferred section exists.
Then inspect its instances and use the exact wording you want to track:

```sh
.venv/bin/python -m gitrx.query section-first-present c43d1edb-c162-4317-9a89-d84e4eff5512 --section boxed-warning
.venv/bin/python -m gitrx.query section-first-present c43d1edb-c162-4317-9a89-d84e4eff5512 --section boxed-warning --scope highlights
.venv/bin/python -m gitrx.query first-occurrence c43d1edb-c162-4317-9a89-d84e4eff5512 --section boxed-warning --scope both --phrase 'neuropsychiatric events' --ignore-case
.venv/bin/python -m gitrx.query first-occurrence 888dc7f9-ad9c-4c00-8d50-8ddfd9bd27c0 --section boxed-warning --phrase 'tendon rupture' --phrase tendinitis --ignore-case --format json
```

The installed entry point is `gitrx first-occurrence`. Select a known alias,
LOINC code, or `whole-document`; scope is `full-label`, `highlights`, or `both`.
Headings are included and identified by default; `--no-headings` excludes
section titles and recognized inline headings. Table cells and footnotes
remain eligible. Use `--location body` or explicit location exclusions when
needed; ambiguous typography still uses the visible prose-default rule.
Case-sensitive matching is the default; examples
explicitly request case folding. Repeated phrases are logical OR, without
automatic synonyms. `--snapshot <query-id>` selects a pinned query checkpoint.
Synthetic table/reference markers never create authored-text matches.

Literal matching is intentionally sensitive to wording. `neuropsychiatric
events` does not match `neuropsychiatric (NP) events`: `(NP)` is actual text,
not a Unicode variant. Supply another `--phrase` explicitly for any-of search.
Matching versions with only title/heading hits carry a notice; missing data or
body-excluding filters never justify a claim that unexamined body text lacks
the phrase.

Named sections now require both code/inferred identity and **anchored**
full-label-body or Highlights context. Generic body assignments are default,
not positive boundary evidence: current body-scope headlines therefore decline
to infer first presence and show unclassified instances separately. Actual
Highlights elements supply explicit anchors. AMBIEN's anchored Highlights box
first appears in v17; code-bearing Medication Guide instances are outside the
product answer. Confidence is evidence status, not a correctness estimate.
Context comes from XML ancestry/document-part headings, not codes. Every instance prints version, context,
ordinal, ancestry and first 200 characters. Context rules and anchors remain
visible; code plus context is still not clinical validation of every section.
Whole-document phrase queries retain all contexts and label each; their
`--scope full-label` means all non-Highlights representations, including patient
and package text, rather than a product-section claim.
Empty or text-extraction-failed sections can still be structurally present;
missing documents cannot establish absence.

Every checked version is cited, every verified hit is scoped to its section
instance and coded/inferred/unknown identity, unknown versions are named,
and numeric gaps split observed presence runs. Those runs are not inferred
calendar periods or proof of clinical warning removal/reintroduction.

The D snapshot rejects altered search payloads, identity annotations, a
different engine/normalizer or Unicode/segmenter version. Original B/C
snapshots and parse records remain retained. The full CLI demonstration is
reproducible with `tools/run_step_d.py` against this prepared local archive.
Legacy byte fixtures live under `tests/golden/legacy/`, with compatibility
paths and pinned engines retained. The two E1b no-headings corrections are
accepted, as are the E1e context corrections. Their original bytes are retained;
the new fail-closed semantics are separately tested and await human validation.

## SQLite semantics

- `document_type_allowlist` contains `34391-3` and `45129-4`.
- `document_type_observations` and `section_code_observations` retain codes
  seen in persisted source responses, with unknowns explicitly flagged.
- `date_anomalies` retains every inverted publication-date pair and
  `versions.date_anomaly` flags every participant.
- `sections` stores actual XML section instances, never one merged row per
  code. Ordinal and ancestry preserve source identity and order.
- `narrative_tables` contains exact table XML, structured rows/cells/spans,
  and an explicitly delimited flat representation. `narrative_blocks` stores
  full/excerpt scopes, individual cells, flat tables and narrative blocks.
- `section_excerpts` keeps highlights separately. Excerpt-only sections are
  not fabricated full narratives or parse failures.
- `parse_runs` records parser/configuration hashes, engine versions and
  diagnostics; unsuccessful extraction is distinct from source absence.
- `gitrx.db.as_of` is an internal selection helper returning all published-
  by-date versions with `date anomaly` warnings. `gitrx.query` implements the
  flagship phrase-history CLI. Git export, MCP and storage migration remain
  deferred.

Keep the database **and** its content-addressed artifacts when backing up.
The database alone does not contain the original ZIP bytes. With the writer
stopped, use SQLite's backup API for a consistent copy. Snapshot manifests
pin evidence selections; they are not a replacement for those artifacts.

## Current local pilot release

The active local CLI is `./gitrx-current` (also `.venv/bin/gitrx`), backed by
`gitrx/current` and `runtime/current` → `data/current/gitrx.sqlite`. Frozen
classifier: `gitrx-rules-v1.0.1`. The original modules/database/snapshot and
v1.0.0 score remain intact; `python -m gitrx.query` is the legacy entry.

```sh
./gitrx-current show LEVAQUIN --date 2020-01-01 --section contraindications
./gitrx-current diff GLUCOPHAGE 1 8 --section contraindications
./gitrx-current first-occurrence CIPRO --section boxed-warning --phrase "tendon rupture" --ignore-case
```

`--phrase` may repeat; `--any-of FILE.json` loads a JSON array of literal OR
alternatives. All views support section scope. `show`/`diff` support
`--scope full-label|highlights|both` and `--json`; default/unanchored evidence
appears separately. On section existence, literal alternatives produce a
separate phrase-presence answer and never replace the structural headline.
Dates are observed DailyMed publication dates, SPL effective dates and
retrieval timestamps, each labeled. No market status or FDA approval claim.

Local evidence, iteration/golden classification, rollback and census details:
[Block 1c reports](../reports/block1c). No public release is authorized before
the fresh independent anchored-validation gate passes.
