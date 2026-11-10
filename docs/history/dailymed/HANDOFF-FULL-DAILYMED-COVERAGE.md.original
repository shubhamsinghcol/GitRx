# Handoff: Full DailyMed coverage and local distribution

Prepared November 7, 2026 for continuation in a new GitRx project chat.

## Owner's direction

- Do not build or operate a hosted GitRx website or query service. Users should
  download the software/data and run GitRx locally.
- Keep the software source repository on GitHub. The owner is willing to pay a
  modest object-storage bill to distribute research evidence, rather than charge
  researchers for access.
- Investigate expanding beyond the current NDA/BLA cohort toward complete
  DailyMed coverage: all in-scope label families, their captured versions and
  source evidence. Define the scope precisely before collection. “Drug family”
  means a DailyMed setid lineage; do not combine similar drug names or products.
- Preserve the maintained evidence rules: immutable raw bytes, source
  observations, pinned snapshots, additive/versioned builds, qualified
  uncertainty, and no silent clinical interpretation.

This handoff authorizes planning, inventory, code review, local measurement, and
reversible implementation work. It does not authorize public release, license
selection, external communications, or automatic/recurring collection. Follow
`AGENTS.md` and current owner instructions if they differ.

## Current verified baseline

- Expanded native release: **7,239 families / 51,357 retrieved known targets**.
- Those families are the selected source-declared NDA/BLA scope, not all
  DailyMed. There are zero failures among the selected known targets, but one
  family's upstream history inventory remains unknown.
- **46,195 versions parse as complete; 5,162 are partial.** Only 5,749 families
  have every retrieved version marked complete. Complete retrieval is not proof
  that every historical DailyMed version ever existed or was captured.
- A prior current human-Rx listing census found **52,451 families**; archived-only
  and other DailyMed category totals were not established in that project census.
  FDA/FDALabel category counts measure labeling documents and are not automatically
  a setid-family denominator. Re-census current official sources before making
  any percentage or “complete DailyMed” claim.
- A complete generated Git release currently has **16 first-setid-hex shard repos
  plus one meta repo**, totaling **2,602,483,565 pack/index bytes (~2.60 GB)**.
  Largest historical blob: 11,201,188 bytes. Two independently built copies and
  a clone check were used for verification; distribute only one release.
- Source Git repo is small (about 2.64 MiB tracked files). Raw/source-observation
  evidence and generated histories are separate distribution lanes.
- Local release1 acquisition workspace occupied about **110 GB** when measured;
  whole project checkout was about **158 GB** at that measurement. Most extra
  bytes are tranche copies, parse intermediates, retries, prior exports, reports,
  and research artifacts. This is not the end-user download size.
- The measured release1 network object store is about **25.1 GB decimal**. This is
  not yet a verified, complete, deduplicated research-package manifest.

See `docs/BUILD-STATUS.md`, `docs/NATIVE-BROWSING.md`, `docs/RESEARCH-HELPERS.md`,
`docs/RELEASE-CYCLES.md`, `REPRODUCE.md`, and the November 6 continuation report
for evidence and current contracts.

## Provisional size model (do not present as a measured final package)

The existing 7,239-family release was linearly scaled against the old 52,451
current human-Rx family frame. That yields a rough human-Rx-only illustration:

- One complete Git history release: **about 19 GB**.
- Source evidence: **about 182 GB**.
- Research download containing both: **about 200 GB**.

These are extrapolations, not a build. Family history lengths, label sizes,
images, raw retrievals, deduplication and categories vary. The estimate excludes
the 110 GB research/build workspace. “All DailyMed” including OTC, animal and
other label categories is a broader scope and could be substantially larger; do
not extrapolate it from the human-Rx family denominator without a new census and
category-specific sample.

Cloudflare R2 was discussed as an object-storage option: at the currently
published standard rate of $0.015/GB-month, 182 GB less the 10 GB free tier would
be roughly $2.60/month, with no egress fee under current pricing. Treat this as a
dated planning estimate. Re-check provider pricing, account limits, request costs,
download packaging, rights and terms before choosing or creating a public bucket.
The owner has expressed willingness to pay storage costs; no bucket or public
upload has been authorized by this note.

## Recommended distribution structure

1. **GitHub source repository:** software, docs, tests, build manifests and
   checksums only; no raw archive, research workspace, or large generated data
   mixed into the source repo.
2. **Generated Git history release:** keep the existing 16 shards by first
   hexadecimal character of setid, plus a small meta repository pinning exact
   shard commit IDs and scope. It is a reasonable starting point, not a proven
   final shard count for all DailyMed. Build once, measure every shard and push
   in bounded increments. GitHub recommends repos below 1 GB where possible and
   strongly recommends below 5 GB; pushes are limited to 2 GiB and regular Git
   files above 100 MiB are blocked. Current shard sizes do not prove full-scope
   GitHub support. Do not upload the second reproducibility copy.
3. **Research source-evidence package:** content-addressed original source files,
   source-observation/retrieval metadata, stable family/version inventory,
   release manifests, hash manifests, provenance/readme, and a local verification
   command. Store this outside Git, e.g. object storage with direct downloads.
   Design chunked archives or a downloader and resumable hash checks; do not make
   a single 180+ GB browser download the only path. Preserve canonical source
   files; never replace them with parsed JSON as though it were raw evidence.
4. **Ordinary-user install:** fetch source package and selected Git data shards
   (all shards for full coverage; optional subset only if query behavior clearly
   reports unavailable scope). No account, server, website, or compute service
   is needed to run locally.

GitHub LFS is not the default raw-evidence solution: the current Free allowance
is far below the projected archive and download bandwidth can be exhausted.
The native selective installer exists, but public remote shard distribution is
not yet qualified. Retain full-shard default behavior until a public installer
and manifest are tested.

## Recommended next work sequence

### 1. Establish the real target and current census

- Read this repo's `AGENTS.md`, `docs/CORE-PRODUCT-ROADMAP.md`, `docs/BUILD-STATUS.md`,
  `docs/BACKEND-PRODUCTION-ROADMAP.md`, source and release manifests first.
- Re-check live Git remotes/repo state and official DailyMed source endpoints.
- Define “all DailyMed” by label type and setid: human Rx/biologic, human OTC,
  animal, and any other requested classes. State whether archived-only families,
  superseded/withdrawn labels, media, and failed/incomplete histories are in
  scope. Keep all categories and statuses separately countable.
- Build a versioned family/setid and history inventory; follow API pagination,
  retain raw responses/timestamps/hashes, distinguish publication/effective dates,
  and disclose any unobserved archive history. Reconcile counts with an official
  source without conflating products, NDCs, SPL documents, setids and versions.
- Produce a read-only capacity report before acquisition: sampled ZIP bytes,
  history-length distributions, deduplicated vs duplicated raw bytes, parse DB,
  metadata and Git output estimates by category, plus expected staging space.

### 2. Decide local storage before backfill

- Owner reports about **160 GB of local storage**. Confirm whether this means
  free space or total disk capacity. The previous measured checkout alone was
  about 158 GB; the rough full human-Rx research bundle is about 200 GB before
  build staging. Therefore 160 GB is **not sufficient** to hold the completed
  research bundle locally, and it is not a safe full-corpus build budget.
- At handoff preparation, `df -h` reported about **147 GiB free** on the current
  workspace volume (roughly 158 GB decimal). This is a point-in-time filesystem
  reading, not confirmation of the laptop's separate user-reported figure.
- Do not start broad backfill on the assumption that the old output can be
  deleted; source evidence, old immutable releases and historical reports must
  be preserved. First identify which retained outputs are reproducible caches
  versus protected evidence, and measure actual free space.
- Options to investigate: attach an external SSD with adequate space and reserve,
  or stage/bundle/upload directly from a suitable machine/volume. Set an explicit
  minimum free-space reserve (the existing acquisition guidance uses 20 GiB on
  every written volume) plus room for transactional staging and failed runs.
- The full human-Rx bundle estimate implies at least 200 GB for final outputs;
  practical build volume should be materially larger. A broader all-category
  target needs a new estimate first. Do not promise that 250 GB or any single
  figure is sufficient until the sampled/capacity model accounts for staging.

### 3. Implement backfill as additive, bounded releases

- Reuse `release-cycle`, the maintained collector/parser, and configured release
  staging. Do not create a parallel ingestion stack or rewrite old raw evidence.
- Acquire by explicit category and deterministic family batches, with persisted
  checkpoints, one polite controlled collection lane, request/byte/time caps,
  resumable work, Retry-After compliance, identity checks inside XML/ZIP, bounded
  workers, and quarantine for conflicts. No automatic schedule by default.
- Preserve exact response bytes and failed attempts; deduplicate by SHA-256 while
  retaining distinct retrieval/source-observation records. Never infer missing
  versions or silently treat unavailable history as a zero-version family.
- Parse to a candidate derived release. Report family coverage, known/history
  inventory status, listed/retrieved/failed targets, complete/partial parse
  counts, duplicate bytes, extraction caveats, media and unknowns by category.
- Add regression evidence for new source categories and every changed shared
  query/export behavior. Keep held-out packets sealed and historical scoring
  records immutable.

### 4. Qualify and package the data for people

- Build a representative candidate twice from identical pinned inputs and
  compare all commit IDs, trees, metadata and hashes. Keep one deliverable; retain
  the other only as local verification evidence.
- Measure projected final size and per-shard sizes from the actual full-scope
  candidate. Test shard-clone and meta registration on a clean machine. Use a
  public distribution dry run before any external push; do not assume GitHub
  multi-repository aggregate behavior is approved merely because one shard fits.
- Produce a separate raw evidence package manifest with per-file SHA-256, source
  identity, category/setid/version, retrieval provenance, archive chunk list and
  dedup accounting. Test resumed downloads and local hash/citation verification.
- Resolve NLM/FDA attribution, source-text/media rights, redistribution terms,
  license, notices and publication decision before public data upload. Public
  code hosting does not itself clear data redistribution rights.
- Make final end-user install work on a clean machine using only documented
  local commands and downloaded artifacts. Website hosting and a backend server
  are not part of this plan.

## Prompt for the next project chat

> Continue GitRx toward a user-runnable, locally installed DailyMed history
> archive. First inspect `AGENTS.md` and
> `docs/HANDOFF-FULL-DAILYMED-COVERAGE.md`, then verify the current checkout,
> release state, actual free disk space, source manifests, and official DailyMed
> scope/counts. Do not begin broad collection or publish anything yet. Produce a
> precise current-vs-target scope, category/setid/version census plan, evidence
> and history completeness definitions, measured storage forecast and staging
> recommendation for my ~160 GB local capacity. Keep GitHub for software and
> potentially the 16 first-setid-hex history shards plus meta repo; keep raw
> evidence outside Git in a separately measured downloadable object package.
> Evaluate a public object store and current costs, but create no public bucket
> or upload without a separately explicit instruction. Preserve all source
> hashes, old releases, refs and validation boundaries. Then give me the next
> concrete product work for backfilling all specifically enumerated DailyMed
> families, source histories and versions with clear progress, limits and
> honest uncertainty. Do not claim complete historical DailyMed coverage from a
> current listing or from a successful retrieval run.
