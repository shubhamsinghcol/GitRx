# Native backend v1 contract

> Native runtime 0.7.0 adds [shared evidence metadata and complete literal histories](EVIDENCE-HISTORY.md). The original contract below retains its dated initial scope; the linked current guides supersede historical capability and bound descriptions.

> Native runtime 0.6.0 adds [whole-query deadlines, resumable search and optional acceleration](QUERY-PERFORMANCE.md). Historical performance/timeout descriptions below are superseded by that guide and its measured report.

Selected defaults for the owner's November 6 authorization. This is the initial
production contract, not a declaration of deployment or historical completeness.
Frontend and a hosted HTTP API are outside this contract.

## Audience, corpus and freshness

The first audience is researchers and software/AI-tool developers inspecting
DailyMed label-family history. The supported corpus is one explicitly configured,
immutable expanded release: initially 7,239 families / 51,357 retrieved versions,
snapshot `1baa5bca2fd0091dfd8da9c82e260709483513515637df81a7c2db7765d61abc`.
The known-target count does not prove every historical version has been discovered.
NALTREXONE's upstream history inventory remains unknown. Family names select setids;
they never merge products into a clinical drug identity.

Freshness is **snapshot-based and manually promoted** in v1, not daily or live.
Every answer identifies its snapshot. Reading never downloads or ingests. New
acquisition, automated release promotion, backups and hosted availability belong
to subsequent roadmap milestones. Old releases retain their identities.

## Supported tools and semantics

| Tool | Promise |
|---|---|
| resolve | Name/setid resolution; ambiguous families remain separate |
| families | Literal case-insensitive title/setid selection; deterministic setid ordering; bounded pagination |
| versions | Captured versions of one canonical UUID setid, ordered by integer SPL version; raw hashes and distinct date observations |
| show | Exact setid/version; native source occurrences; exact code and ordinal filters; character pagination |
| diff / compare | Conservative native section pairing across exact versions, with bounded word changes and ambiguous repeats disclosed; optional ordinal diagnostic remains |
| show_date | Greatest captured SPL version observed published by the cutoff; missing dates and order conflicts explicit |
| first_observed | First literal match in captured SPL-version order, with predecessor citation and gaps; not an actual addition-date inference |
| phrase_history | Complete paginated observations within one family, with gaps and extraction uncertainty; no addition/removal date inference |
| search | Case-insensitive literal substring in native own_text; one result per matching occurrence; greatest SPL version per family; explicit scan/result bounds and continuation cursor |

The library owns query behavior; SQLite/payload and Git adapters only supply
records and citations. Git is a first-class read backend, following global-law's
Git-and-caches approach. SQLite/source observations and raw archives retain evidence
authority. No additional database server is required.

DailyMed section codes mean source codes, not context-validated product sections.
Repeated occurrences retain distinct ordinals and ancestry. Codes, case,
punctuation and medical symbols are not folded into semantic equivalence.
Search uses casefolded text solely for case-insensitive literal matching; it does
not report folded positions as original-text offsets. A text preview is the start
of an occurrence, not necessarily the matching substring. Greatest SPL version is
not a historical-date snapshot. The interpreted pilot CLI/MCP remain separate.

## Citations and uncertainty

Output schema identifier is `gitrx-native-v1`. All answers carry the release
snapshot and explicitly unclassified context; content answers carry raw SHA-256.
Git citations add the pinned meta commit and label commit; SQLite citations add
the selected parse key. Section answers include native occurrence ordinals,
source code/system, ancestry and observed dates. A query does not revalidate raw
ZIP bytes; those pointers lead to separately retained evidence.

Unknown versions and corrupted/mismatched inputs produce errors, not evidence
answers. An absent native code returns zero matching captured occurrences, without
claiming a clinical warning never existed. Empty search results cover only the
reported scanned families. Truncation, scan limits and continuation cursors are
explicit. Partial parses remain source-native observations, not complete documents.
No clinical advice, FDA-approval inference, applicability date, causal explanation,
semantic section history or first-addition inference is supported by this interface.

## Resource and service targets

Hard bounds: 32 MiB per decompressed payload/Git result; Git command timeout
30 seconds; at most one Git command at a time per reader and no persistent child
processes; configurable history LRU 1..128 families (default 32), with a 4 MiB
serialized-record budget; section-blob LRU 8 MiB; 200 returned family/search hits;
200,000 returned text characters; search defaults to scanning at most 128 families
and can explicitly scan at most 7,239. The time bound is per Git command, not a
whole-query deadline. Diff input is blob-bounded; this is not yet an adversarial
multi-tenant service. Text limits do not cap JSON metadata size or total process RSS.

Initial performance goals, to validate on representative hardware: warm exact
show under 250 ms p95; 128-family bounded searches under 2 seconds p95; full
latest-corpus exhaustive searches under 15 seconds. These are goals, not measured
production SLOs. The initial batch-reader experiment cannot qualify this bounded
adapter's performance. Cold startup, peak memory and concurrent workloads remain
release qualification work. No hosted uptime/freshness SLA is promised by local tools.

## Acceptance checklist and evidence

- [x] Native tool semantics, supported corpus, freshness and uncertainty fixed above.
- [x] Shared library and two storage adapters, with explicit immutable release pins.
- [x] No installed-package Desktop path assumption; configuration paths relative to file.
- [x] Synthetic offline source ZIP → parser → evidence SQLite → payload cache → sixteen Git shards → both readers.
- [x] Missing-version, snapshot/hash mismatch, pagination and repeated-instance tests.
- [x] Package wheel installed and selftest run outside the original checkout; see foundation report.
- [x] CI definition runs installed-package fixture tests across Python versions; remote execution remains separate.
- [ ] Full-corpus qualification of the new bounded adapter (earlier comparison used a different batched adapter).
- [ ] Performance goals, memory budget and cold/concurrent behavior qualified.
- [ ] Backup/restore drill, automated acquisition/release operation and deployment gates.

The first six checked engineering gates are implemented locally. The unchecked
items block claiming a deployed production backend; they do not block using the
new local native CLI. Changes to this contract require a versioned output/semantics
decision and regression evidence, not silent cache/schema changes.
