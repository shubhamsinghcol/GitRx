# Reproduce the local expanded export

This recipe reproduces **Git objects from the same pinned evidence databases,
manifests and raw files**. It does not claim that a new DailyMed download today,
or a new database admission with new processing timestamps, yields the same
snapshot. Source observations and parse-run records are retained inputs.

## Inputs

- `cohorts/release1/recovery-manifest-round-3.json` selects the original family sources.
- Forty-six original closed database pins are enumerated by
  `tools.release1_completion_report.checkpoint_pins`.
- `cohorts/release1-recovered-20261106/reports/ADMISSION.json` pins the new
  15-version supplemental database and its source snapshot.
- `EXPANDED-SELECTION.json` in that same reports directory pins the combined
  selection: 7,239 families and 51,357 retrieved known targets.
- The source databases' registered raw artifacts must remain accessible at
  their recorded roots. The database alone is not a raw-source backup.
- `reports/continuation-20261106/OLD-REFS-BEFORE.json` inventories preserved
  earlier export refs; they are never overwritten by this recipe.

The Python project declares Python >=3.11, lxml 6.1.3 and pytest 9.2.0.
Exact executed environment and source hashes are recorded in
`reports/continuation-20261106/ENVIRONMENT.json`.

## Offline export

From `/Users/shubham/Desktop/GitRx`, using the installed environment:

```sh
.venv/bin/python -B -m gitrx_services.shard_release --output-base exports/local-reproduction
```

Choose an unused output base. The command first prepares hash-checked, context-free section/table payloads.
It copies one read-only source database at a time into transient RAM (maximum
4 GiB) and writes compressed derived payloads; it never adds source indexes
or changes source bytes. The prepared cache is identified by the expanded
snapshot and the unchanged native exporter hash. Each payload is checked on use.

It then builds `local-reproduction-a` and `-b`,
each containing 16 first-setid-hex repositories and a meta repository with
exact gitlink pins. It compares all 51,357 reachable commit IDs, shard tips
and trees, and meta tips and trees, and runs Git object checks. The builds may run in two bounded local workers with disjoint output roots; no network fetching is parallelized. Existing tags
are immutable. Interrupted output directories are retained; use a new base
rather than deleting or resetting them.

Expected canonical results are in
`cohorts/release1-recovered-20261106/reports/EXPORT-VERIFICATION.json` and each
completed build's `BUILD.json`. Reproduction outside this local layout needs
an explicit path-restoration/relocation plan; no portable raw-to-database
hash-for-hash rebuild has been claimed.

## Recovery admission and tests

`python -B -m gitrx_services.recovered_release` validates retained candidate
ZIP/XML identities and admits them into the supplemental root, never the old
stores. It performs no network requests. The nested member has no invented
direct HTTP status: actual HTTP206 range observations and exact bulk-member
provenance are separate records. A completed supplemental database is reused
only after its pin matches.

The full permitted test command is recorded in the continuation report.
Five original packet tests remain excluded; the newly owner-labeled tranche1
packet is distinct. Golden comparisons test compatibility, not clinical truth.

## AI and human contributions

The owner designed/orchestrated the project with AI assistance. AI generated
much of the code, tests, reports and this documentation. Tests, source/hash
checks and two-build equality constrain implementation claims; they do not
constitute independent clinical review. The owner labeled a fresh 60-case
packet. Its original 59/60 comparison is retained; one IFU grading correction
was later adjudicated by the owner, yielding 60/60 adjudicated agreement.
That correction is not a second blind pass. No v1.0.1 classifier rule was
changed to match it. No public distribution or clinical/legal validation is
established by this recipe.

The [release manifest](cohorts/release1-recovered-20261106/reports/RELEASE-MANIFEST.json) and [lineage](cohorts/release1-recovered-20261106/reports/RELEASE-LINEAGE.json) link the expanded snapshot, meta commit, preserved prior refs, supplemental/index pins and human adjudication.
