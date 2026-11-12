# GitRx project brief

> **November 5 consolidation:** use [START-HERE](START-HERE.md) for the explanation
> and [BUILD-STATUS](BUILD-STATUS.md) for current counts and next steps.
> This November 2 brief's running-job figures and acquisition instructions
> are historical. The bounded run has completed; see the current status
> before taking operational action. [All documents](DOCUMENT-MAP.md).

Read this, [GLOBAL-LAW-PARITY.md](GLOBAL-LAW-PARITY.md), and
[ROADMAP.md](ROADMAP.md) at session start. Then read the latest
[progress](../reports/PROGRESS.md), [SPEC](../SPEC.md), and live
`./release1-status`. Historical reports describe their capture time; live
operational state wins. Updated 2026-11-02, America/Chicago.

## Mission and audience

Build an open, reproducible archive of US human-prescription label-family
history published by DailyMed. Keep every retrievable listed version, exact
source bytes, section instances, literal phrase histories, differences and
hash-citable provenance, with honest unknowns. The flagship question is:
“When did this phrase first appear in this section of this label family?”
Also answer what the captured DailyMed record said by a date and what changed
between two versions. Primary users are pharmacovigilance/informatics
researchers and AI-tool developers; clinicians and attorneys are secondary.
The intended outcomes include a data-resource paper, a pharmacovigilance
study and a faculty collaborator. Reproducibility and validation matter as
much as interface features.

This is the DailyMed-published record for a setid family. It does not establish
FDA approval, legal applicability, what shipped with a product, causation,
clinical advice, or drug identity across families. Current-listing discovery
does not establish the complete archived-only universe.

## Evidence and architecture

DailyMed listing → paginated family histories → ordinary per-version ZIP
route, with archive-ID fallback → exact SHA-256 raw artifacts and separate
append-only retrieval observations → frozen SPL extraction → SQLite →
derived context overlay → CLI/read-only MCP → deterministic Git export.

Raw artifacts plus SQLite are authoritative. URLs, response headers, status
and time belong to retrievals, not shared artifacts. Only retrieved-evidence
objects count toward coverage; pipeline-provenance and derived objects do
not. Internal XML setid/version establish identity. Wrong or ambiguous
identity and competing bytes cannot silently replace selected evidence.
Retain failures and quarantine evidence additively; never delete raw bytes.

Keep every section instance, with native ordinal, LOINC/code system, ancestry
and source pointers. Repeated codes never merge. Structured tables and
delimited searchable text coexist. Identical section payloads deduplicate;
context is rebuildable metadata, never part of text hashes or export paths.

Frozen `gitrx-rules-v1.0.1`, commit
`a75da35e1d5fce74bef0d5e10ad1b5cfc11b4d4c`, uses structural/heading/prose
evidence, never code alone, for six contexts. Named-section headlines require
anchored eligible body/Highlights evidence. Default/unknown instances stay
separate and cannot prove negative claims. Partial extraction can establish
a verified positive, not absence from an unchecked scope.

Queries name family/setid; distinguish DailyMed publication, SPL effective
and retrieval dates; expose anomalies, unlisted integers, missing evidence,
instances/previews, normalization, snapshot and `(setid, version, raw SHA-256)`
citations. “Added in” requires retrieved, adequately checked integer N−1.
Otherwise qualify the earliest observed presence. Presence runs never bridge
unknown evidence as proven continuous presence. `show --date` lists eligible
publication records and selects their highest numeric version, retaining
anomalies/unknowns; it does not select a legally applicable label.

Git preserves numeric order within each family, merging eligible queue heads
by `(publication date, setid, version)` across families. A flat date sort
would violate inverted-date histories. Paths remain
`labels/<setid[:2]>/<setid>/`. Fixed identities/dates/messages/trailers and
frozen snapshots yield reproducible commits. Old tags/histories remain;
published releases are never force-pushed.

## Verified state and current operations

Pilot: 20 families, 587/587 listed versions retrieved, including 38 archive-
route versions. Active CLI is `./gitrx-current`; accepted query snapshot is
`68f939ed3832fe7ba4a008b67afcb9d1baad6d8323ab27407cc1cbb636e71023`.
The five-tool local stdio MCP serves this pinned pilot, not the unclassified
release1 corpus. [Connection guide](MCP-CLAUDE-DESKTOP.md).

Census frame: 52,451 current-listing families; 300 sampled histories with
1,232 versions estimate 215,399 listed versions, approximate 95% interval
185,930–244,868. The 300 verified sampled ZIPs had no failures; this is not
verification of every estimated version. Tranche1: those 300 families and
all 1,232 listed versions. Partials 243/1,232 (19.72%); family-bootstrap 95%
interval 14.46–25.83%. No potentially lossy diagnostic code was observed;
this does not prove layout/clinical fidelity.

Pilot+tranche1 exports: 320 families/1,819 commits, reproducible head
`967db45ea6790795598a5eaade9d89d78303e028`, tree
`900a3aa36e539ea316e0bfbdc6f5313404fb510b`, about 144 MB per repository.
Release1 milestone exports now also reproduce: 1,000 families/6,970 versions
at 364,694,918 bytes; 2,000/14,131 at 744,472,905 bytes; 4,000/28,485 at
1,454,369,081 bytes. [Measurements](../reports/block3c/EXPORT-AT-SCALE.md).

Release1 scope is 7,239 source-category NDA/BLA families, including NDA
authorized generics, seed 20261106, tranches of 500. This supersedes the
earlier sample-based 6,819-family projection. Intake observation on
2026-11-02 evening CST: about 4,845 families done, 34,392 versions retrieved,
43,533/120,000 requests, 150.46 GiB free, nine verified tranches; running.
These counts expire; use status. Release1 is raw/parse-only, with no context
inspection. Operational family failures get two additive end-of-run sweeps.

The earlier identity stop was resolved in
[Block3c-5](../reports/block3c-5/REPORT.md): one listed NALTREXONE family
consistently returned an empty history envelope. Two fresh probes and a
neighbor check attributed it to the source; the audit checked all 19,175 then-
stored versions, finding no wrong-identity label admission. Only that exact
family/envelope may be skipped as `history_identity_conflict`; other identity
conflicts still stop. Original metadata remains in additive quarantine.

Fetching uses one sequential persistent connection and the existing adaptive
ladder. A permanent safety cap already forbids faster than one-second starts;
current holds can require two seconds. Do not restore earlier fixed pacing,
reset ledgers, or launch another requester. CPU parsing may use six bounded
workers with one SQLite writer. Preserve 20 GiB free on every written volume
and the inclusive 120,000-attempt cap. [Runbook](../reports/block3c/RUNBOOK.md).

## Release gates

Fresh 60-row validation at `reports/block2-validation/index.html` awaits
human labels. Do not open held-out/sealed content, score early or tune on it.
The earlier v1.0.0 sample had five anchored errors; its 60 rows are development
data, not a fresh pass for v1.0.1. Follow the preset 0/1/2+ gate in ROADMAP.

Annotations have schema/validator/CI and synthetic examples only. Approval
needs a cited source and independent reviewer; chronology never establishes
causation. Quirks are human-authored only. [Review rules](../annotations/REVIEW-RULES.md).
Licensing and publication remain owner decisions; nothing is public.
[Licensing summary](../reports/block3b/LICENSING-SUMMARY.md).
