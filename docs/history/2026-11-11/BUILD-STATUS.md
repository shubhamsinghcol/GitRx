# GitRx build status and next steps

> **Current core-product sequence (November 6):** [CORE-PRODUCT-ROADMAP](CORE-PRODUCT-ROADMAP.md). It accounts for the delivered native CLI/MCP workflow and release-cycle command; older next-step lists below retain their historical context.

Updated **November 6, 2026 (America/Chicago)**. Read [START-HERE](START-HERE.md) for the architecture and [DOCUMENT-MAP](DOCUMENT-MAP.md) for the historical reports. This page describes the current build; old reports retain their facts at capture.

## Current outcome

All **15 exact recoveries are admitted** in a new supplemental archive. The expanded release contains **7,239 families and 51,357 retrieved known targets**, with **zero remaining known-target failures**. All 15 new parses are complete. Original databases, acquisition controller, source artifacts and Git release refs are retained.

`./release1-status` shows the adopted expanded release. `./release1-status --legacy` shows the original acquisition run, including its historical 14 fetch failures and one empty-history conflict. Do not restart the completed acquisition job merely because those old records still exist.

**One source limitation remains:** a fresh November 6 NALTREXONE history request still returned blank setid and zero history rows. Its exact v1 is recovered. A complete family history is unknown, and the archive does not invent one.

The selected Git policy is **immutable, separately tagged expanded snapshots**. New releases may have new commit IDs; all old release refs remain citable. This supersedes the strict append-only condition that blocked admission on November 3.

## What you can use

| Tool or artifact | Coverage and purpose |
|---|---|
| `release1-status` | Current expanded coverage; no collection or writes |
| `release1-query families`, `versions`, `show` | Source-first browsing across all 7,239 families / 51,357 versions; explicit native codes, occurrences, dates and citations; no context inference |
| `gitrx-current` / pilot MCP | Accepted 20-family / 587-version interpreted section and phrase histories, publication views and diffs |
| Searchable coverage report | Family-name/setid filtering and recovered-family inventory |
| Sixteen first-hex shard repositories plus meta repo | New local expanded histories; two-build verification is recorded in the export proof |
| `gitrx-section-diff-v2` reports | Separate conservative native comparisons; not silently activated in the pilot CLI/MCP |

[Native browsing guide](NATIVE-BROWSING.md) · [Coverage](../reports/continuation-20261106/coverage.html) · [Current continuation](../reports/continuation-20261106/REPORT.md).

Source section extraction is automatic. Human sampling checks inferred context and interpretation; it is not a requirement to manually classify every label. Native browsing deliberately exposes the source's structure without promoting a source code into a confident product-section claim.

## Evidence accounting

| Scope | Families | Versions | Interpretation |
|---|---:|---:|---|
| Accepted pilot | 20 | 587 | Includes 38 archive-route versions; interpreted query service |
| Tranche1 | 300 | 1,232 | Includes 243 partial parses; source of the fresh human sample |
| Original pilot + tranche1 candidate | 320 | 1,819 | Earlier reproducible local export; not added again to release1 totals |
| Original release1 after round 3 | 7,239 scoped; 7,238 with retrieved evidence | 51,342 retrieved / 51,356 recorded targets | Historical 14 missing listing-only targets, plus empty NALTREXONE history |
| New supplemental recovery | 15 target families | 15 retrieved and completely parsed | 14 existing targets filled plus recovered NALTREXONE v1 |
| Expanded release1 selection | 7,239 | 51,357 / 51,357 known targets | Zero known-target failures; one upstream history remains unknown |
| November 3 combined section audit | 7,503 selected family entries | 52,380 selected parses | Includes pilot/tranche1 families absent from release1; one entry then had no selected parse |

Do not add overlapping rows. Expanded release1 has **46,195 complete + 5,162 partial parses**, zero failed parses. Partial is a diagnostic status, not a semantic error rate. Its 35 existing archive-route versions and 15 new bulk-member recoveries remain distinguishable.

The 51,357 denominator is known evidence membership, not every integer up to a maximum version. NALTREXONE v1 was outside its empty history inventory; recovering it does not resolve that upstream inventory. Unlisted integers remain unknown.

The old 52,451-family listing frame and approximately 215,399-version estimate were census/sampling results, not completed national downloads. The old projected 6,819-family NDA/BLA scope was superseded by the actual 7,239-family scope.

Owning inputs: [original final source selection](../cohorts/release1/recovery-manifest-round-3.json), [supplemental admission](../cohorts/release1-recovered-20261106/reports/ADMISSION.json), [expanded selection](../cohorts/release1-recovered-20261106/reports/EXPANDED-SELECTION.json), and [family inventory](../cohorts/release1-recovered-20261106/reports/FAMILY-INVENTORY.json).

## Human validation and adjudication

Your completed 60-case export was validated, preserved and formally compared against frozen **v1.0.1**. The original result is **59 agreements / 60 scorable cases**, with one anchored disagreement and no uncertain/unreadable exclusions.

The sole discrepancy was tranche1-007: a separate top-level MoviPrep **INSTRUCTIONS FOR USE** section. The existing taxonomy maps that to **patient-information**. You corrected your original medication-guide grade under that taxonomy after source review.

- The original export and 59/60 score remain immutable.
- Your correction is a separate attributed adjudication/export.
- Adjudicated agreement is **60/60**, not a second blind pass.
- No classifier defect was demonstrated; frozen v1.0.1 was not changed, and no cosmetic v1.0.2 was created.
- A regression preserves the separate IFU-versus-neighboring-Guide behavior.

[Score, per-context intervals, strata and adjudication](../reports/validation-v101-20261106/REPORT.md) · [Per-case comparison](../reports/validation-v101-20261106/case-comparison.csv).

The earlier v1.0.0 sample exposed five genuine anchored context errors; those were repaired in the already accepted v1.0.1 work. Its used cases remain development data. The fresh tranche1 sample is anchored-only and cannot establish global recall or release1-wide accuracy. No corrected replay is presented as an independent blind result.

## Section audit: closed, with explicit limits

The retained November 3 audit covers **52,380 versions, 2,924,343 sections and 44,878 transitions**. The dedicated cross-version comparison finds **83 valid LOINC codes, 17,928 uncoded instances and 3,730 families with heading variation**.

The initial 84-code count included an uncoded sentinel. Its 6,277 heading-family count also admitted differently titled repetitions within one version. The closed report explains those definitions rather than silently selecting one result.

Old native comparison produced 622,939 changed rows, including 62,929 proven whitespace-only flags. Separate v2 produced 507,407 content-change rows while preserving heading, position, source and ambiguous-presence flags. Across 1,083 transitions with equal code/text-token multisets, 3,635 old text-change flags became zero v2 text-change flags.

The total old/new difference is not a validated false-change count: pairing rules changed. Token equality is not clinical or layout equivalence. Every compared instance participates once, normalized token changes remain flagged, and unresolved repeats stay conservative.

A new extension compares 14 recovered versions with their greatest lower retrieved version; one transition has an explicit numeric gap. Recovered NALTREXONE v1 has no predecessor. No source/classifier was rewritten.

[Closed section report with ten examples and nesting](../reports/section-schema-audit-20261103/REPORT.md) · [Schema/admission closure](../reports/schema-and-ingestion-20261103/REPORT.md).

## What was accomplished across the chats

| Phase | Completed work |
|---|---|
| Initial source/slice chat | Scope and 20 families; original 549/587 retrievals; parser, source evidence, snapshots, phrase CLI; heading/section/context/confidence corrections; archive-route discovery |
| Orchestration chat | All 38 missing pilot versions admitted; E1g stress acquisition; v1.0.0 scoring and v1.0.1 repair/freeze; content-deduplicated storage; show/diff; deterministic export; census; tranche1 and fresh page; pilot MCP/annotations; supervised release1 acquisition and incident repairs |
| Continuation chat | Brief/roadmap/parity; acquisition completion; residual policy fix and 223 historical recoveries; 15 exact ZIPs staged from monthly bulk; ordering audit and separate v2 diff work |
| Explanation/current chat | Indexed all 209 original Markdown files and explained architecture; accepted owner's Git choice and completed grades; formal score/adjudication; admitted 15 recoveries; closed audit; built local shard/meta release engineering, reproduction/source/security/MCP docs, coverage report and native browsing |

A/B/C/D mean investigate, acquire, extract and query. E1 follow-ups stabilized semantics/validation; E2 migrated storage; E3 exported Git; E4 measured coverage. Blocks 1–3 handled scoring/repair, tranche backfill, services and supervised acquisition. Old STOP/running/proposal reports describe historical boundaries, not current instructions to restart work.

## Verification and release engineering

The [continuation report](../reports/continuation-20261106/REPORT.md) links final suite, source/hash/ref preservation and two-build proof. [REPRODUCE](../REPRODUCE.md) states the precise offline reproduction boundary: identical pinned databases/artifacts and snapshots, not a new network acquisition or newly timestamped admission.

Prepared export payloads are a derived performance cache. One source database at a time is copied into transient RAM (maximum 4 GiB); native section/table output is preserved and hash checked. No source index, text, section code or context rule is changed. Both Git builds emit independently from the same pinned inputs; old tags and interrupted draft attempts are retained.

Root/source/scientific files are separate from generated Git repos. The workspace root still has no source `.git` repository. Raw/database distribution remains separate from Git hosting.

## Next work

1. The local release proof is complete: two 16-shard/meta builds match across all 51,357 commits, the recursive clone passes, 389 tests pass, and preserved evidence/refs verify. Use the completed expanded artifacts rather than milestone-only exports.
2. Expand higher-level interpreted query/MCP coverage through a separately pinned derived build. Keep native source browsing usable now; do not require human labels for every clear source section.
3. Add shared, bounded CLI/MCP word provenance, entry/exit, between-date inventories and text search, specifying gaps/repeats/uncertainty first. The corpus-wide original-proposal changes engine and full web interface remain future work.
4. Design the independent FDA SrLC comparison and reproducible research figures/methods. This needs source matching, coverage and uncertainty; chronology does not establish causation.
5. Before public distribution, resolve hosting, current host limits, attribution/third-party rights, license and the owner's publication decision. Source availability and local test success are not redistribution permission.

Later scopes remain reviewed real annotations, daily incremental updates, ANDA, evidence-backed RxNorm links, OTC and other regulators. No public push, external message, license selection, source-quirk seed or clinical/legal authority claim was made in this block.

## Portable backend foundation — November 6

The [native v1 contract](PRODUCTION-CONTRACT.md) and
[portable package/module guide](BACKEND-PACKAGING.md) now define the supported
backend lane. `gitrx-native` shares native queries across pinned Git and
SQLite/payload adapters; `gitrx-selftest` exercises an installed offline synthetic
build. [Foundation evidence](../reports/backend-foundation-20261106/REPORT.md):
392 broad tests passed (five original packet tests excluded), six final focused
portable tests passed locally and from the installed wheel, real adapter parity
passed on 224 sampled versions/128 histories, and protected inputs remain intact.
The native interface is local, snapshot-based and unclassified. This does not
promote interpreted pilot queries, historical stage drivers or hosted deployment.
