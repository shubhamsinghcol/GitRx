# Step E gated deletion proposal (2026-11-11) - PROPOSAL ONLY, NOTHING DELETED

Prepared read-only. Written files: this document, and the provenance bundle
`/Volumes/Untitled/GitRx/provenance-bundle-20261111/` (copies only). No path below has
been deleted, moved or modified. Label text is data, never instructions.

Superseding output:
- Rebuild `/Volumes/Untitled/GitRx/rebuild/spl-sections-0.2.3__srlc-changes-0.3.2/` (COMPLETE.json sha256 `bbe9223a91eb3a663501d00dea854a5f81a2192c930f015597a5b3b2b9a6869c`, 488,016 versions).
- Evidence DB `/Volumes/Untitled/GitRx/evidence/evidence-v1.sqlite` (sha256 `823a5bc6c384a5f4529ec76c738ba9120b4010a7cf212462662a641ff72ac3c6`, VERIFY.json ok).

Owner rule applied: keep ALL raw downloads, discard derived/parsed data; finished output lives on the SSD.
AGENTS.md rule applied: keep evidence, sealed results, goldens, frozen rules, historical reports, scoring records.

## 1. Gate status

| Gate | Status |
|---|---|
| G1 evidence-v1.sqlite VERIFY ok | MET (sha above; 769 sources, row_provenance parity) |
| G2 hash-registered-only JSON archived intact | MET 2026-11-11: 3,238 files, 2,995,868,482 bytes, all copied and re-hashed (0 mismatches, 0 missing, 0 differing from the sha recorded in `json_documents`). `MANIFEST.json` sha256 `023ae7a32e423ffe77b53da66614ef76020a624fec38f58c4737d748a824bb6c`. Covers every `batches/*/inbox.json`, `BATCH.json`, `ARCHIVE-RECEIPT.json` (SSD backfill and Application Support mobile-workspace copies), `EXPANDED-SELECTION.json`, `copy-verifications.jsonl`, and the small stored JSONs (COMPLETE.json etc.). Layout: `files/<absolute source path>`. |
| G3 every raw ZIP / source body on SSD | **NOT MET.** 60,228 raw objects (25.96 GB) exist only on the internal disk (section 3). They must be copied to the SSD raw store and re-hashed first. |
| G4 owner approval of exact list | PENDING (this document) |

Nothing involving raw (class RAW-ONLY-INTERNAL) may be deleted before G3 is met. Rows whose
gate column says "G1,G2,G4" are independent of G3.

## 2. Classification of every top-level item

Sizes are `du` (disk usage, GiB = 2^30), hardlinks counted once per du call, so
totals are estimates. Classes: RAW, EVIDENCE, PROVENANCE, DERIVED-SUPERSEDED, UNSURE.

### 2a. /Users/shubham/Desktop/GitRx (internal disk; all of these are gitignored)

| Path | Size | Class | What it is / what rebuilds it | Proposed action / gate |
|---|---|---|---|---|
| cohorts/release1/network/objects (+partial, responses) | 23.8 | RAW | HTTP bodies and SPL ZIPs (58,000 objects exist nowhere else) | KEEP; copy to SSD raw store first (G3). Internal copy deletable only after rehash |
| cohorts/release1/network/ledger.sqlite, pacing*.jsonl | 0.16 | RAW | release1 HTTP ledger (66,067 rows; also in functional-recovery backup) | KEEP; copy to SSD with raw |
| cohorts/release1/tranches/*/artifacts | 23.9 (hardlinks of network/objects) | RAW | ZIP hardlinks of the above | frees nothing until network/objects is removed too; handle with raw move |
| cohorts/release1/tranches/*/data (gitrx.sqlite, -shm, -wal; 15 tranches) | 39.15 | DERIVED-SUPERSEDED | per-tranche archive DBs: parsed sections/parse spools mixed with source observations; source rows are imported in evidence-v1 with row parity (769 sources) | DELETE candidate. G1,G2,G4. Question Q1 (original source-observation containers) |
| cohorts/release1/tranches/*/{reports,research} | ~0 | DERIVED-SUPERSEDED | tiny SNAPSHOT.json etc. | delete with parent |
| cohorts/release1/retries/round-0{1,2,3}/tranches/*/data | 0.74 | DERIVED-SUPERSEDED | retry DBs, rows imported | DELETE candidate. G1,G2,G4 |
| cohorts/release1/retries/round-*/tranches/*/artifacts | 0.47 | RAW | 784 ZIPs + bodies only here | G3 first |
| cohorts/release1/exports/{25pct,50pct,first1000}-{a,b} | 4.78 | UNSURE | generated Git exports, a/b determinism pairs | Q2 |
| cohorts/release1/{control.sqlite,scope.json,discovery-status.json,recovery-manifest-round-*.json,copy-verifications.jsonl,run/,incidents/,quarantine/} | 0.05 | RAW (ledgers/observations) + PROVENANCE (json, already bundled) | source observations and run records | KEEP (tiny). `scope.json` is read by runtime/native-config.json |
| cohorts/release1-recovered-20261106 | 4.48 | DERIVED-SUPERSEDED | old release payloads (`export-payloads` 4.20 GiB gz parsed sections 0.1.0), native-query.sqlite, reports/ (docs hash-stored in evidence DB and in bundle) | BLOCKED: it is `release_root` of runtime/native-config.json, which the `label-history` CLI/MCP use. Delete only after native config is repointed to the new rebuild/release. Raw there: 31 objects (15 are 30-byte stubs) G3. Q3 |
| cohorts/tranche1/{net,artifacts} | 1.06 | RAW | transport bodies, 290 ZIPs (14 only here) | G3 first, then optional |
| cohorts/tranche1/data, targets.json | 0.77 | DERIVED-SUPERSEDED / PROVENANCE | tranche1 capture DB; targets.json registered | UNSURE Q4 (tranche1 is the AI-graded evidence set); default KEEP |
| cohorts/census-e4 | 0.19 | RAW + PROVENANCE | census sample bodies (259 JSON only here), frame.json registered | KEEP; copy raw to SSD |
| cohorts/e1g, cohorts/e1g-anchor-diff-v101 | 0.39 | EVIDENCE | held-out/sealed packets (`sealed/` not opened) | KEEP (by name) |
| artifacts/sha256 | 0.70 | RAW | 691 CAS files; 596 unique objects not on SSD | G3 first; keep until copied |
| data/gitrx.sqlite | 2.33 | UNSURE | original single archive DB (Oct 31); sources imported | Q5 |
| data/current | 1.74 | UNSURE | legacy "current" DB behind `gitrx-current` and `runtime/current/data` symlink | Q5 |
| data/backups/pre-archive-admission, pre-artifact-classes | 4.23 | UNSURE | protected pre-change baselines (referenced in docs/history) | Q5; default KEEP |
| data/*.log, entrypoint-check.txt | <0.01 | EVIDENCE | historical logs | KEEP |
| work/pilot-completion/work (e2-before-oracle, body-shadow) | 4.64 | DERIVED-SUPERSEDED | pilot-era shadow parse/oracle spools | DELETE candidate. G1,G4 |
| work/pilot-completion/data | 4.07 | DERIVED-SUPERSEDED | pilot archive DBs, sources imported | DELETE candidate. G1,G4 |
| work/pilot-completion/{exports,cohorts} | 0.36 | DERIVED-SUPERSEDED | pilot Git exports/cohort copies | DELETE candidate (raw there: 1,501 objects, 0.73 GB, G3 first) |
| work/pilot-completion/artifacts | 0.70 | RAW | ZIP/JSON/XML copies (1,501 objects only here: 590 ZIPs, 678 JSON, 221 XML, 12 HTML) | G3 first |
| work/pilot-completion/{reports,research,gitrx,tests,tools,HANDOFF.md,SPEC.md,pyproject.toml} | 0.43 | EVIDENCE | pilot-time snapshot of code and reports | KEEP (Q6 if owner wants it gone) |
| exports/* (11 trees: release1-expanded-20261106-{a,clone-check,indexed-a,indexed-b}, block2-{a,b,clone}, block3b-rc-{a,b}, main-v101) | 10.73 | UNSURE | generated Git releases/clone checks; `indexed-a` is `export_root` of native-config. Rebuildable but AGENTS.md says preserve immutable releases/refs | Q2 |
| reports/ (111 entries, 16.6 GiB) | 15.85 | EVIDENCE | historical reports, scoring records, goldens, baselines | KEEP, except bulk sub-trees below |
| reports/block2-sealed*/, reports/e1g-sealed-v1/ (and e1g-blind-v1, human-blind-labels) | - | EVIDENCE | sealed results; not opened | KEEP (by name) |
| reports/qualification-20261107/live-build/releases | 7.45 | UNSURE | derived release builds from qualification run | Q7 |
| reports/qualification-20261107/selective-real | 0.57 | UNSURE | derived qualification build | Q7 |
| reports/block3c-4/parser-parity/* | 5.91 | UNSURE | parser 0.1.0 parity outputs (49 raw objects only here) | Q7; G3 first |
| reports/section-schema-audit-20261103/derived-v2 | 0.57 | UNSURE | derived parse of audit | Q7 |
| reports/query-limits-20261106/projection | 0.46 | UNSURE | search projection (rebuildable) | Q7 |
| reports/evidence-history-20261107/metadata | 0.14 | UNSURE | `evidence_metadata` of native-config (in use) | KEEP until repointed |
| reports/{recovery-attempt-20261103,audit,block1c,...} | - | EVIDENCE | contain ~330 raw objects not on SSD (reports/recovery-attempt 96, audit 136, block1c 73, etc.) | KEEP; copy raw to SSD |
| runtime/ (31 MB: native-config.json, release-job-config.json, release-cycles, full-dailymed*, web, current symlinks) | 0.03 | EVIDENCE | job state, incidents, config in use | KEEP |
| studies/ (validation-100-srlc-20261108) | 0.17 | EVIDENCE | validation records (AI-graded; not validation per VALIDATION-CORRECTION) | KEEP |
| research/ | 0.07 | EVIDENCE | research evidence (79+ raw only here) | KEEP; copy raw to SSD |
| annotations/ | <0.01 | EVIDENCE | annotation catalog and records | KEEP |
| .venv, .git, .pytest_cache, tests/, tools/, docs/, gitrx*/, label-history* | ~0.05 | source | code | KEEP |

### 2b. /Users/shubham/Desktop/GitRx-functional-recovery-20261106

| Path | Size | Class | Why | Action |
|---|---|---|---|---|
| backup/objects (256 shards: 48,738 gz parsed-section objects, 3 SQLite incl. identical copy of the release1 ledger) | 4.43 | DERIVED-SUPERSEDED | parsed sections 0.1.0 plus duplicate ledger; no raw objects unique to it (manifest: 0 raw only here); the 3 SQLite sources are imported | DELETE candidate. G1,G4 |

### 2c. ~/Library/Application Support/GitRx

| Path | Size | Class | Why | Action |
|---|---|---|---|---|
| backfill/hot/objects/*.sqlite (+entries) | 2.06 | RAW | live HTTP ledger (758,117 requests, incl. WAL rows), queue/control; `/Volumes/Untitled/GitRx/backfill-20261107/network/ledger.sqlite` is a symlink into it | KEEP. Move to SSD and repoint symlink before any cleanup (separate task) |
| backfill/mobile-workspace/batches (636 batch dirs: inbox.json, BATCH.json, ARCHIVE-RECEIPT.json; artifacts and data are symlinks to the SSD) | 1.59 | PROVENANCE | all 1,908 files are in the bundle with matching hashes | DELETE candidate after owner approval (G2 met) |
| backfill/mobile-workspace/{control.sqlite,COMPLETE.json,config*.json,status*,inputs/census,network}, backfill/*.json, baseline-setids.json, logs, policies | 0.25 | RAW (ledger/observations) + PROVENANCE | small records; census manifest and COMPLETE.json in bundle | KEEP (tiny) |
| backfill/raw-journal (objects 2,170 + index.sqlite) | 0.51 | RAW | all 2,170 hash-named objects verified present by name on SSD `network/bodies` | duplicate; may delete after rehash. Optional |
| backfill/raw-staging/network | 0.15 | RAW | all 583 objects present on SSD | optional, as above |
| backfill/raw-staging/buffer, buffer-before-mobile | 0.32 | DERIVED-SUPERSEDED | download-buffer task DBs (rows imported as aux_rows) | DELETE candidate G1,G4 |
| backfill/installed, census-runtime, processing-spool, pacing | 0.05 | DERIVED-SUPERSEDED | installed copies of old code (gitrx 0.10.0, lxml) and spool | DELETE candidate G4 |

mobile-workspace raw check: the folder holds no raw object that is absent from the SSD. Its only hash-named content (raw-journal, raw-staging) is on the SSD; `mobile-workspace/network/bodies` and the batch `artifacts` entries are symlinks to the SSD.

LaunchAgents: `launchctl list` shows only `org.gitrx.dailymed-weekly` and `org.gitrx.srlc-monthly` (both exit 0, not running). The backfill guardian, `full-dailymed-backfill`, `full-dailymed-census` and `pause-20261109` agents are NOT loaded and no gitrx/backfill process is running. Their plist files remain in ~/Library/LaunchAgents with `RunAtLoad` true and point at `backfill/resume.sh` / `resume-census.sh` in Application Support; recommend removing those three plists at the same time as the folder cleanup so a login does not restart them (not done). The weekly and monthly agents run from `.venv` in the repo and write under `/Volumes/Untitled/GitRx/updates` and `srlc`, so they do not depend on any candidate path above.

### 2d. /Volumes/Untitled/GitRx (SSD)

| Path | Size | Class | Why | Action |
|---|---|---|---|---|
| backfill-20261107/network/bodies | 158.8 (hardlinked with batches/*/artifacts) | RAW | 436k SPL ZIPs and bodies, hash = filename verified | KEEP |
| backfill-20261107/batches/*/artifacts | (shared inodes) | RAW | hardlinks of bodies | KEEP (deleting frees nothing) |
| backfill-20261107/batches/*/data (636 gitrx.sqlite + shm/wal) | 175.52 | DERIVED-SUPERSEDED | mixed parse data and per-version retrieval/publication source observations, all imported in evidence-v1 (backfill_versions 436,587; retrievals; history_*) | DELETE candidate. G1,G2,G4. Q1 |
| backfill-20261107/batches/*/{inbox.json,BATCH.json,ARCHIVE-RECEIPT.json} | 2.4 | PROVENANCE | in the bundle | KEEP in place unless owner wants removal (cheap) |
| backfill-20261107/incidents (corrupt ledger originals + recoveries), .hot-storage-history, network/ledger.sqlite symlink, network/partial, policies, control.sqlite, status/policy JSON | 0.04 | RAW / EVIDENCE | incident record of corrupt ledgers | KEEP |
| full-dailymed-20261107 (census.sqlite, network bodies, capacity/) | 0.23 | RAW | census pages; capacity/ 0.14 GiB is probe data | KEEP (capacity/ UNSURE, trivial) |
| evidence/ | 9.35 | EVIDENCE | evidence-v1.sqlite, VERIFY.json, REPORT.md | KEEP |
| rebuild/spl-sections-0.2.3__srlc-changes-0.3.2 | 11.5 | EVIDENCE | superseding output | KEEP |
| srlc/raw, ledger*, gradebook.sqlite, split.json, grading/open-*, grading/SEALED-*, updates/ | 3.1 | EVIDENCE / RAW | SrLC raw crawl, gradebook, sealed and open results, prospective holdout | KEEP |
| srlc/grading/cache (fulltext-render, index*.sqlite, pairs) | 6.52 | DERIVED-SUPERSEDED | grading caches from older parser outputs; rebuildable | UNSURE Q8 |
| updates/dailymed | 0.02 | RAW | weekly job state and bodies | KEEP |
| provenance-bundle-20261111 | 2.80 | PROVENANCE | the new bundle | KEEP |

### 2e. Session scratchpad `/private/tmp/claude-502/-Users-shubham-Desktop-GitRx/b81b1d09-9a13-41a3-9d0c-882a73ea8595/scratchpad` (14.0 GiB, internal /private/tmp)

| Path | Size | Class | Action |
|---|---|---|---|
| phase1/raw-manifest.sqlite | 0.74 | EVIDENCE | **KEEP; copy to SSD** (`rebuild/.../build-config.json` references this scratchpad path as `raw_manifest_path`) |
| phase1/REPORT.md, raw-summary.csv, groups.json, scripts | <0.01 | EVIDENCE | copy to SSD/docs |
| phase1/{refs,manifest-work,scan-*}.sqlite, walcopy | 7.5 | DERIVED-SUPERSEDED | rebuildable scans; delete at session end after raw copy (G3 uses raw-manifest) |
| t1 (e.sqlite, work) | 3.38 | DERIVED-SUPERSEDED | scratch |
| phase2, rb, phase15, fx, bw-precision, diag, ixtest, srlcroot | ~2.8 | DERIVED-SUPERSEDED | scratch dev outputs; `rb/` is the rebuild run work area |
| gradebook-dev.sqlite, grade*.log, sealed*-*.log, sealed-results.md, freeze-hashes.txt, *.py | 0.11 | EVIDENCE | logs of the sealed runs and Freeze hashes exist only here; copy to SSD (Q9) |
| del/ | 0.02 | scratch | this task's scripts and `raw-only-internal.tsv` |

## 3. Gate G3 detail: raw present ONLY in internal locations

Method: `phase1/raw-manifest.sqlite` (hash-verified inventory, 2026-11-10 17:56; 657,539 unique raw objects). Files newer than the manifest in the candidate trees: none except 15 SQLite `-shm` files. A raw object counts as "on SSD" if the same sha256 appears in any `V:` location; the 60,228 below were then stat-checked in `backfill-20261107/network/bodies` and `full-dailymed-20261107/network/bodies` (0 found).

| Kind | Objects only on internal disk | Bytes |
|---|---|---|
| SPL ZIP | 51,369 | 25,904,035,400 |
| DailyMed API JSON | 8,240 | 18,658,529 |
| HTML bodies | 341 | 21,758,538 |
| standalone SPL XML (fixtures/audit) | 242 | 12,152,888 |
| openFDA JSON | 16 | 1,961,214 |
| 30-byte "PK" stubs | 20 | 600 |
| **Total** | **60,228** | **25,958,567,169** |

By location (unique objects; many appear in several): cohorts/release1 58,915 (25.89 GB); work/pilot-completion 1,501; artifacts 596; cohorts/tranche1 291; reports/block3c-4 49; cohorts/e1g and e1g-anchor-diff-v101 673 each; cohorts/census-e4 868; reports/recovery-attempt-20261103 96; research/evidence 79; research/e1d-2 28; cohorts/release1-recovered-20261106 31; reports/audit 136; reports/block1c 73; others under 25 each (full list). Note: 51,369 ZIPs = the release1 retained-parent set (51,356) plus 13 extras, and the new rebuild read them from the internal disk through the raw manifest. Nothing in Application Support or the functional-recovery backup is raw-only.

Full object list (sha256, kind, bytes, one source path): `/private/tmp/claude-502/-Users-shubham-Desktop-GitRx/b81b1d09-9a13-41a3-9d0c-882a73ea8595/scratchpad/del/raw-only-internal.tsv`. Required action before any related deletion: copy these 60,228 objects to a content-addressed SSD raw store (about 26 GB; SSD has 159 GiB free), re-hash every copy, add them to a refreshed raw manifest, then they count as present.

Not part of this finding: the 81 unavailable versions (55 HTTP 404 and 26 invalid bodies) have bodies on the SSD already.

## 4. Totals

Internal disk (estimates, du-based; hardlinks not double counted):

| Bucket | GiB |
|---|---|
| A. Delete candidates, gate-clear on owner approval (release1 tranche/retry DBs 39.89; pilot work/data/exports/cohorts 9.07; functional-recovery 4.43; Application Support batches 1.59, buffer 0.32, installed 0.05) | ~55.4 |
| B. Blocked until native config repointed (cohorts/release1-recovered-20261106) | 4.48 |
| C. Raw: freed from internal only after copy to SSD and rehash (cohorts/release1 network/tranches artifacts ~23.8, plus retries, tranche1, artifacts, pilot artifacts, ~2 more; optional raw dupes in Application Support 0.66) | ~27 |
| D. UNSURE (exports 10.73 + release1/exports 4.78; data 8.30; reports bulk 15.1; scratchpad 14.0) | ~52.9 |

Internal free today 160 GiB. A alone: about 215 GiB free; A+B+C: about 247 GiB.

SSD (772 GiB used, 159 GiB free):

| Bucket | GiB |
|---|---|
| Delete candidate: backfill batches/*/data (636 DBs) | 175.52 |
| UNSURE: srlc/grading/cache | 6.52 |
| Adds: raw copy +26 GB, bundle +2.8 (already added) | -26 |

Net SSD after A and raw copy: roughly 150 GiB reclaimed.

## 5. Explicit KEEP list

All RAW (all SPL ZIPs and bodies, wherever they live until copied); `backfill-20261107/network/bodies`, `batches/*/artifacts`, `incidents`, `.hot-storage-history`, `control.sqlite`; `hot/objects` ledgers; release1/census/tranche1 ledgers and control DBs; `full-dailymed-20261107`; `updates/`; `srlc/raw`, ledgers, `gradebook.sqlite`, `split.json`, `grading/open-*`, `grading/SEALED-*`, `srlc/updates` (prospective holdout, never print); `evidence/`; `rebuild/`; `provenance-bundle-20261111/`; `reports/` (all historical reports, `block2-sealed*`, `e1g-sealed-v1`, `e1g-blind-v1`, `human-blind-labels`, goldens, baselines) apart from the UNSURE bulk sub-trees; `cohorts/e1g*`; `studies/`; `research/`; `annotations/`; `runtime/`; `docs/`; all source directories, `tests/`, `.git`, `.venv`; `data/*.log`; tag `codex-final`.

## 6. UNSURE items and questions for the owner

- Q1. Per-batch and per-tranche SQLite files mix parsed data with the original source-observation tables. All rows are imported in evidence-v1 with row provenance, but the original files are the first-hand containers. Is the evidence DB sufficient (delete 175.5 GiB on SSD and 39.9 GiB internal), or keep the original containers on the SSD? (Hash of every source DB is in `source_dbs`.)
- Q2. `exports/*` and `cohorts/release1/exports/*` are rebuildable Git exports, but AGENTS.md says preserve immutable releases/refs, and `exports/release1-expanded-20261106-indexed-a` is the live `export_root`. Delete (15.5 GiB) or keep? Note the 2026-11-06 release determinism records are in reports/.
- Q3. `cohorts/release1-recovered-20261106` and `reports/evidence-history-20261107/metadata` serve the `label-history` CLI/MCP today (`runtime/native-config.json`). Delete only after the new rebuild is wired into a native config and tested. Confirm that ordering, or keep as the old release.
- Q4. `cohorts/tranche1/data` (0.77 GiB) supports the AI-graded tranche1 labels; keep or delete?
- Q5. `data/gitrx.sqlite`, `data/current`, `data/backups/*` (8.3 GiB): legacy archive DBs behind `gitrx-current` and `runtime/current`. Only 38 version keys exist solely in `data/backups` and those sources are in evidence-v1. Delete as legacy, or keep the two `backups` as protected pre-change baselines?
- Q6. `work/pilot-completion/{reports,research,gitrx,tests}` (0.43 GiB): pilot-time snapshot. Keep as history?
- Q7. Derived bulk inside historical reports (qualification live-build 7.45, selective-real 0.57, parser-parity 5.91, derived-v2 0.57, projection 0.46 GiB): delete while keeping the report text and JSON, or leave `reports/` untouched?
- Q8. `srlc/grading/cache` (6.5 GiB): needed to re-run past gradings or the monthly job? Rebuildable.
- Q9. Sealed-run logs and Freeze hash files in the session scratchpad (`sealed*-primary.log`, `sealed-results.md`, `freeze-hashes.txt`, `gradebook-dev.sqlite`) exist only in /private/tmp: copy to SSD/`docs` before session end? Also copy `phase1/raw-manifest.sqlite` to the SSD (the rebuild config points at it).
- Q10. The three unloaded LaunchAgent plists (full-dailymed-backfill, full-dailymed-census, pause-20261109) have `RunAtLoad` true: remove them?
- Q11. Where should the SSD raw store live for the 60,228 internal-only objects (suggest `/Volumes/Untitled/GitRx/raw-internal-20261111/sha256/<2-hex>/<sha256>` plus a manifest), and may the internal copies be removed afterwards (frees ~27 GiB)?

## 7. Proposed sequence (after approval)

1. Copy 60,228 internal-only raw objects to the SSD; re-hash every copy; refresh raw manifest; store it on the SSD (G3).
2. Copy the Q9 scratch evidence to the SSD.
3. Re-run the pilot/check that the weekly and monthly agents and native CLI work with candidate paths absent (dry check with paths renamed, not deleted).
4. Owner approves exact rows; delete in bucket order A, then C, then B after repointing.

## 8. Final list for owner approval (v1, 2026-11-11, after G3 met)

G3 is now met. 60,228 internal-only raw objects were copied to
`/Volumes/Untitled/GitRx/raw-internal-20261111/` and every copy was re-hashed: 0 mismatches.
MANIFEST.json sha256 is 56f253c6a6241bfd0e40acba45d3df9a76e7e70ea4a06139366ae9ccb2d630d1.
The session scratchpad was copied to `/Volumes/Untitled/GitRx/session-artifacts-20261111/` (0 checksum
differences); this covers Q9.

Owner answers: Q1 delete; Q7/Q8 delete the bulk and keep the report documents; Q11 keep both raw copies
(internal and SSD). **Rule for every row:** any file whose sha256 is a raw object in
`raw-manifest.sqlite` (objects.raw=1) is left in place and is not deleted.

| # | Path | ~GiB | Raw files kept in place |
|---|---|---|---|
| 1 | /Volumes/Untitled/GitRx/backfill-20261107/batches/*/data (636 dirs) | 175.5 | 0 |
| 2 | ~/Desktop/GitRx/cohorts/release1/tranches/*/data | 39.2 | 0 |
| 3 | ~/Desktop/GitRx/cohorts/release1/retries/round-*/tranches/*/data | 0.7 | 0 |
| 4 | ~/Desktop/GitRx/work/pilot-completion/{work,data,exports,cohorts} | 9.1 | 1,381 (0.8 GB) |
| 5 | ~/Desktop/GitRx-functional-recovery-20261106 (whole folder) | 4.4 | 0 |
| 6 | ~/Desktop/GitRx/reports/qualification-20261107/live-build/releases | 7.5 | 0 |
| 7 | ~/Desktop/GitRx/reports/qualification-20261107/selective-real | 0.6 | 0 |
| 8 | ~/Desktop/GitRx/reports/block3c-4/parser-parity | 5.9 | 1,022 (1.3 GB) |
| 9 | ~/Desktop/GitRx/reports/section-schema-audit-20261103/derived-v2 | 0.6 | 0 |
| 10 | ~/Desktop/GitRx/reports/query-limits-20261106/projection | 0.5 | 0 |
| 11 | /Volumes/Untitled/GitRx/srlc/grading/cache/{pairs,fulltext-render} (index*.sqlite KEPT: Freeze-pinned inputs) | ~6 | 0 |
| 12 | ~/Library/Application Support/GitRx/backfill/mobile-workspace/batches (all in provenance bundle) | 1.6 | 0 |
| 13 | ~/Library/Application Support/GitRx/backfill/raw-staging/{buffer,buffer-before-mobile} | 0.3 | 0 |
| 14 | ~/Library/Application Support/GitRx/backfill/{installed,census-runtime,processing-spool,pacing} (retired guardian code) | 0.05 | 0 |
| 15 | ~/Library/LaunchAgents plists for the retired, unloaded agents: full-dailymed-backfill, full-dailymed-census, pause-20261109 (Q10) | 0 | 0 |

Totals: about 183 GiB on the SSD and about 70 GiB on the internal disk.

Procedure:
1. Move each row into `_pending-delete-20261111/` on the same volume. Raw files stay in place.
2. Run the core tests, a dry run of each update job, and the rebuild COMPLETE.json re-hash.
3. Only if all pass, permanently delete the staging folders.
4. Record a deletion log (path, bytes, sha256) on the SSD.

Deferred, not in this list:
- Q2 old release exports: the recommendation is to delete them after the CLI/MCP are switched to the new rebuild.
- Q3 `cohorts/release1-recovered-20261106` and evidence-history metadata: blocked until the switch.
- Q5 legacy `data/`: decide during the switch.

Kept:
- Q4 `cohorts/tranche1/data`.
- Q6 pilot code and report snapshot.
- Application Support raw-journal and raw-staging/network (raw duplicates).

## 9. Execution record (2026-11-11)
The owner approved all 15 rows. They were staged by `scratchpad/del/stage.py`: 228,707 entries, 250.7 GiB,
with 2,403 raw files left in place. Census-runtime (row 14) did not exist.

Checks run before the delete, all passed:
- 278 core tests.
- DailyMed update smoke (4 requests).
- All 256 rebuild shards re-hashed against their sidecars, 0 mismatches. COMPLETE.json bbe9223a… unchanged.
- evidence-v1 823a5bc6… unchanged.
- 200/200 sampled raw objects verified on the SSD and still present internally.
- `label-history describe-release` OK.

The staging was then permanently deleted. Deletion log: /Volumes/Untitled/GitRx/DELETION-LOG-20261111.jsonl
(sha256 266fbe8349fca09dcb15e34b9588777582232fd5fb26f713aad122f0ba60d2ad). Free space after the delete:
internal 211 GiB, SSD 300 GiB.

Owner decision on Q2/Q3 (2026-11-11): delete the old release exports and the release1-recovered root
only after the following sequence:
1. Switch the CLI and MCP to the new rebuild.
2. Verify they answer correctly.
3. Record the old release's fingerprints (snapshot, commit IDs, file hashes).
4. Delete.

## 10. Execution record: old release and legacy local databases (2026-11-11)

Owner approved deleting the old release 1baa5bca after the CLI and MCP defaulted to the Step E rebuild. Before deletion:
the fingerprint JSON `old-release-fingerprints-1baa5bca.json` (sha256 `f910fcde0cf59f82c0bf8431c3955e1877f0653bb448bd69e69bb0182a3fa6df`)
existed in two copies (SSD and `reports/old-release-fingerprints/`, identical). Coverage was verified: all 74,135 files of the trees to delete
(non-.git files of 17 export/release trees, 9.8 GB) were present in it with matching size, and all 50 `.git` directories are listed in its
`git_repositories`. The legacy local databases were fingerprinted in a second JSON, `/Volumes/Untitled/GitRx/legacy-local-db-fingerprints-20261111.json`
(62 files, 8,910,528,007 bytes; none are raw objects).

Deleted (files staged in `_pending-delete-20261111-old-release/` on the same volume, then removed after the checks below):
`exports/*` (11 trees), `cohorts/release1/exports/*` (6 trees), `cohorts/release1-recovered-20261106` (all but 31 files), `data/gitrx.sqlite*`,
`data/current`, `data/backups/*`. Total 32,777,785,067 bytes (30.5 GiB, of which 14.05 GB inside `.git` object stores).
Left in place by the raw rule: the 31 raw objects under `cohorts/release1-recovered-20261106/artifacts/sha256/`; also kept `data/*.log`,
`cohorts/release1/{control.sqlite,scope.json,...}` and `reports/evidence-history-20261107/metadata`.
References retired: `runtime/native-config.json` renamed to `runtime/native-config-1baa5bca.RETIRED-20261111.json`; symlinks
`runtime/release1-current` and `runtime/current/data` removed; release-cycle, `release1-*`, `gitrx-current` wrappers and the code that built the old release removed (see [CLEANUP](CLEANUP-2026-11-11.md)).

Checks run with the files staged away (all passed): 436 fixture tests, `./label-history describe-release` (snapshot `bbe9223a...`),
`tools/verify_gitrx.py quick` (73 pass, 0 fail), `gitrx_backend.selftest`, rebuild `COMPLETE.json` sha256 unchanged.
Deletion log (path, bytes, sha256 per file; commit ids and pack names per Git repository):
`/Volumes/Untitled/GitRx/DELETION-LOG-20261111-old-release.jsonl` (74,218 lines, sha256 `daad36ccd47c73f9d9e5dddf4d9d022d21f7dc5b08d49d27026876966a9fa621`).
