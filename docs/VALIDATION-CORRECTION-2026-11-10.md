# Validation correction (2026-11-10)

**Status: the parser has no human validation.**

The tranche1 fresh-sample results recorded elsewhere in this repository —
"59/60 original blind agreement" and "60/60 owner-adjudicated agreement" — were
not produced by human review. The owner disclosed on 2026-11-10 that the 60
cases were given to a general-purpose AI model, and that model's grades were
relayed back as if they were the owner's labels. The tranche1-007 "adjudication"
was likewise not an independent human judgment.

Consequences:

- No document, report, release manifest or status output in this repository
  should be read as evidence that section extraction or classification is
  correct. Strings such as `owner-adjudicated 60/60` embedded in existing release
  manifests (`gitrx_services/shard_release.py`, `gitrx_services/release_status.py`)
  and historical reports are left unchanged because they are part of immutable
  recorded outputs; this note supersedes their meaning.
- Historical scoring files are not edited or deleted; they remain as a record of
  what was claimed at the time.
- Parsed/derived data built by the current parser (`spl-sections-0.1.0`) is
  treated as unvalidated and is scheduled for regeneration after a corrected
  parser passes mechanized validation.

Planned replacement: mechanized validation without manual label review —
deterministic text-conservation checks against source XML, agreement with the
FDA Drug Safety-related Labeling Changes (SrLC) database as an external
gradebook for 2016+ changes (thresholds and a held-out split fixed before
tuning), and an independent source for pre-2016 labels (candidate: Drugs@FDA
approved-label PDFs). AI use is limited to logged checkpoint review.
