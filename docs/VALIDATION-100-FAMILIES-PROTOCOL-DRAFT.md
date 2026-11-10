# Proposed 100-family external validation

Status: design draft, November 8, 2026. No families selected, reference answers
created, or evaluation executed. This is separate from all historical validation
packets, scores, and sealed answers.

Local execution tooling is built but not started. See
[local runner and review workflow](VALIDATION-100-LOCAL-RUNNER.md). Its scoring
separates mechanical checks from human clinical/event adjudication.

## Objective and unit

Validate (1) source fidelity, (2) agreement with independently adjudicated
safety-change evidence, including SrLC, and (3) reproduction of captured-history
timing. These are separate endpoints, with separate denominators.

Use 100 unique DailyMed setid families, not 100 individual versions. A family is
a document lineage, not an ingredient or FDA application. Explicitly verify the
application/product/formulation/labeler mapping; do not infer it from name alone.
Classify mappings as verified, ambiguous, or unresolved and retain every selected
family in the disposition report. Do not silently replace difficult cases.

Primary clinical target: new or revised safety-risk wording in the actual boxed
warning, distinguishing a new box, modification, removal, relocation, and purely
formatting changes. Clinical semantic assessment is human adjudication; the
current native tools provide literal/source-occurrence evidence, not an automated
clinical event classifier. Name the tested workflow accordingly.

## Sampling proposal

Freeze a source frame and use a recorded random seed within strata. Proposed
period: January 1, 2016 through December 31, 2025, subject to feasibility review
before freezing the protocol. Preserve baseline versions before the period and
follow-up through a separately declared captured-source cutoff. Do not imply
complete follow-up or archived-only family coverage.

- 50 SrLC-positive families: independently identified boxed-warning events in
  the period; sample families rather than allowing prolific applications to
  dominate. Prespecify an index event selection rule before inspecting GitRx
  results. Within-family event detection also examines all relevant events in
  the declared window, not only the index event.
- 30 families with no identified SrLC boxed-warning entry in the window: these
  are candidate comparison cases, NOT presumed true negatives. Review original
  label histories independently to establish changes, no changes, or uncertainty.
- 20 distinct challenge families: selected using recorded metadata or independent
  source review for repeated sections, patient-label overlap, complex tables,
  partial extraction, missing versions, or conflicting/unknown dates. Deduplicate
  against the other groups using a frozen allocation rule. This is a deliberately
  enriched stress sample, reported separately.

SrLC covers NDAs and CDER-regulated BLAs, not ANDAs. Restrict the main external
comparison to that overlap. Confirm feasibility and frame counts before promising
the proposed allocation. Sparse strata require a documented protocol amendment
before evaluation, not outcome-driven substitutions.

The enriched sample does not estimate population prevalence, overall corpus
accuracy, or population PPV. Report by stratum; any population-weighted estimate
requires known sampling probabilities and a justified target population.

## Freeze and independence

Use 10 additional non-test families to develop abstraction instructions and
settle operational definitions. These do not count toward the 100. Do not access
the original held-out packet or its excluded tests.

Freeze the test manifest, GitRx release/configuration/software identities,
query workflow, source-record copies, matching rules, normalization policy,
clinical event definitions, timing rules, and analysis plan before evaluation.
Raw hashes, occurrence identifiers, source URLs, and retrieval times belong in
the evidence manifest. Freeze and hash GitRx outputs before reference unblinding.

Two independent reviewers build source-based reference records without GitRx
predictions. Review original documents, not GitRx-extracted text alone. Separate
source-text abstraction from SrLC reconciliation where feasible. Resolve disputes
by a third reviewer or documented consensus; preserve pre-adjudication labels.
Report reviewer qualifications and any limits on independence.

## Endpoint 1: source fidelity

Audit all 100 families. Inspect at least two prespecified distinct versions per
family when available; inspect all versions needed to locate index transitions
and establish prior captured absence. Retain single-version or inaccessible
families as unevaluable for relevant tasks. Do not imply the study is only 200
documents: locating transitions can require many more.

For audited versions check identity, version number, independently transcribed
dates and date type, boxed-warning existence and boundaries, full target-section
text, section hierarchy, tables/footnotes, and correct citation-to-source linkage.
Include prespecified adjacent/non-target sections to test leakage and false
section matches. For labels with no box, verify that absence from the original.

Predefine allowable whitespace normalization; do not normalize away numbers,
units, negation, table relationships, or substantive punctuation. Record exact
normalized-text agreement and substantive omission/addition/misattribution.
Report component rates, families with any material error, and audit coverage.
These checks validate audited content, not every character in every document.

## Endpoint 2: external event agreement

Run the frozen GitRx-assisted workflow without revealing reference results.
Independently enumerate relevant original-document transitions and SrLC events
within the declared windows. Match events one-to-one using verified product
identity, target section, and adjudicated safety content; allow one regulatory
event to map to multiple source lineages without counting those as independent
regulatory events. Fix temporal matching rules in development before testing.

Report two separate comparisons:
1. detection against adjudicated original-document transitions (TP/FP/FN);
2. correspondence with SrLC events, distinguishing supported matches, absent
   captured evidence, ambiguous mappings, and genuine source differences.

Calculate precision and sensitivity only for a clearly defined evaluable event
universe. Specificity requires independently reviewed negative opportunities
defined in advance (for example family/window); do not mix event and window
denominators. Absence from SrLC alone never establishes a false positive.
Include abstention/unevaluable rates and an all-selected-case disposition table;
do not improve apparent accuracy by hiding missing histories.

## Endpoint 3: timing

First validate the GitRx measurement against independent manual reconstruction
of the SAME captured history. For evaluable events compare first-present version,
first-present publication observation, predecessor, prior captured absence, and
uncertainty classification. Report exact version/date agreement and absolute
date errors; distinguish error from abstention.

Then describe cross-source differences:
- first observed DailyMed publication date minus verified SrLC regulatory date;
- for the subset with a prespecified, verified FDA communication, communication
  to regulatory change and communication to first captured DailyMed appearance.

Retain zero and negative intervals. Regulatory dates, publication observations,
SPL effective dates, and retrieval times are different fields. Cross-source date
differences are findings to adjudicate, not automatically extraction errors or
evidence of regulatory/manufacturer delay.

Where justified by comparable chronology, report the interval between last
captured absence and first captured presence; this bounds observed document
transition uncertainty, not FDA action or actual market introduction. Baseline
presence, gaps, partial extraction, conflicting dates, and absent follow-up require
explicit censoring/unknown categories. Never substitute retrieval time for a
historical date or call unknown history confirmed absence.

## Analysis and discrepancies

Declare family, document, occurrence, and event counts separately. Account for
dependence within families and shared applications/class-wide events. Use family
or higher appropriate clustering for repeated observations; small challenge
subgroups remain descriptive. Provide 95% intervals with the stated method.
Timing uses evaluable matched-event denominators, which can be much smaller
than 100. Size is a feasibility proposal, not a completed power calculation.

Report performance by sampling group and evidence difficulty. Document every
discrepancy as identity/mapping, acquisition/inventory, extraction, section
selection, comparison, query/pagination, semantic interpretation, date handling,
reference-review error, or genuine source divergence. Preserve overlapping causes.

No post-unblinding repairs alter the original score. A revised system may be
reported as exploratory on these cases and confirmed on a fresh untouched set.
Do not claim general clinical validity or full-corpus recall from this study.

## Deliverables and boundaries

Deliver frozen protocol/manifest, source evidence register, independent reviewer
forms and adjudications, frozen GitRx outputs, endpoint-specific results and
confidence intervals, all-case disposition, discrepancy appendix, and replay
instructions. Report review time to assess researcher effort.

This plan authorizes no collection run, release promotion, publication, external
messages, or recurring service. Reference-source acquisition, if later requested,
must remain separate from read-only evaluation of the frozen GitRx release.

## Primary source references

- FDA SrLC scope and guide:
  https://www.accessdata.fda.gov/scripts/cder/safetylabelingchanges/index.cfm
- NLM DailyMed source and FDA-labeling distinctions:
  https://dailymed.nlm.nih.gov/dailymed/about-dailymed.cfm
- Local implementation limits: ../LIMITATIONS.md
