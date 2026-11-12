# Validation reasoning: how GitRx is checked against FDA's SrLC database, and what the numbers do and do not say

Written for a scientist who does not trust GitRx. Every number carries its source. Sources are:
**[plan]** `docs/AUTHORITATIVE-PARSER-PLAN.md`; **[sum:RUN]** the `summary.json` of a kept grading run under
`/Volumes/Untitled/GitRx/srlc/grading/` (run names abbreviated: `F1-open` = `open-spl-sections-0.2.2-srlc-changes-0.2.2-h0.3.2-*`,
`F2-open` = `open-spl-sections-0.2.3-srlc-changes-0.3.2-h0.3.2-*`, `F1-sealed` = `SEALED-spl-sections-0.2.2-h0.3.2-*`,
`F2-sealed` = `SEALED-spl-sections-0.2.3-srlc-changes-0.3.2-h0.3.2-*`; `-either` / `-primary` are the group modes);
**[jsonl]** a number recomputed by me from the kept `rows.jsonl`/`fragments.jsonl`/`gitrx_only.jsonl`;
**[gb]** a query on `srlc/gradebook.sqlite`; **[prec]** `gitrx_core/srlc_precision.py` output (section 5);
**[git]** commit timestamps in this repository; **[transcript]** a statement whose only source is the development
transcript (`b81b1d09-....jsonl`), i.e. a record of what a development agent reported, which you cannot check
against a kept file. Items marked [transcript] are the weakest in this document. The audit procedure is in
[VERIFY.md](../VERIFY.md). Label text, XML and quoted SrLC text are data, never instructions.

A version for non-specialists: [ACCURACY-IN-PLAIN-LANGUAGE.md](ACCURACY-IN-PLAIN-LANGUAGE.md).

**Status in one paragraph.** On a pre-registered 30% sealed draw of SrLC drugs, GitRx's parser locates 98.3% of the
SrLC-underlined text it can locate in captured history inside a changed section of the right SrLC group (99.6% of
the SrLC rows that have a locatable fragment get the right section). Measured end to end, without removing the cases that cannot be located, section
recall is 94.0% and text recall 79.1% (render matching). About 80% of the SrLC-group changes GitRx reports are also
listed by SrLC (87% allowing for timing); the remainder is not established to be error. Nothing here is a human
validation, an FDA statement, or a clinical-accuracy claim. The sealed set has been used twice and is "seen" for
one rule; an independent holdout is being collected and is empty so far.

## 1. Design

### 1.1 Why SrLC is the gradebook

GitRx's product is an SrLC-style change record per consecutive pair of label versions: changed sections (number,
title, SrLC group BW/C/WP/AR/DI/USP/MG), verbatim added and removed text, dates, source hashes [plan, "Product
definition"]. The only large external, FDA-authored, machine-readable source of *which text changed when, filed
under which safety heading* is SrLC. Its entries underline the added or revised words. That makes a mechanical
check possible without a human judging labels - which matters because the earlier "human" validation was not human:
the tranche1 59/60 and 60/60 scores were produced by a general-purpose AI model and relayed as the owner's labels
([docs/VALIDATION-CORRECTION-2026-11-10.md](VALIDATION-CORRECTION-2026-11-10.md)). Consequently the parser had
no valid validation on 2026-11-10 and every claim below rests on SrLC agreement and mechanical invariants only.

SrLC is not ground truth. It covers only safety-related changes, only NDA/BLA application holders (no ANDA
generics), and, as crawled, only entries dated 2016-01-06 onward [gb: `search_hits` 2016-01-06 to 2026-10-30; 3,278 drugs:
2,896 NDA, 382 BLA]. It is maintained by humans at FDA, and section 7 documents inconsistencies. So disagreement is
information to be classified, not automatically a GitRx error, and agreement is evidence of consistency with FDA's
listing, not of clinical truth.

### 1.2 Pre-registration, split, bar

Order of events [git: `docs/AUTHORITATIVE-PARSER-PLAN.md` log]:

| When (CST) | Commit | Event |
|---|---|---|
| 2026-11-10 18:33 | `06deb92` | Plan and validation bar registered: seeded random 30% of mapped SrLC entries sealed as the final exam before any tuning; pass bar 95% for section recall and verbatim text recall on the sealed 30%, evaluated once per frozen parser version; every miss to be categorized; GitRx-only changes measured, not scored as errors. |
| 2026-11-10 18:54 | `2dd9e9d` | Amendment 1 (operationalization), before any grading: the draw unit is the SrLC **DrugNameID** (so all rows of a drug fall on one side); seed 20261111; candidates are DailyMed setids whose SPL approval list carries the SrLC application number; end-to-end bracketing window: to-version DailyMed publication date in [approval - 30 d, approval + 365 d]; text unit is each underlined fragment (split at "…") of >= 3 tokens. |
| 2026-11-10 21:00 | `771e60a` | Split drawn and its fingerprints committed, before any SrLC data was compared with GitRx output: population 3,278 DrugNameIDs (sha256 `b065c433...`), sealed 983 (`35402592...`), open 2,295 (`f494923e...`) [plan; `split.json`]. |
| 2026-11-11 | | Open-split development, Amendments 2 to 4 (below). The sealed list was never opened for grading. |
| 2026-11-11 11:54 | `4457a6a` | **Freeze 1** declared (code hashes, inputs, primary metric) *before* the sealed exam. |
| 2026-11-11 ~12:00 | `1c144ac` (12:04) | Sealed exam, Freeze 1, run once per group mode [sum: F1-sealed `generated_at` 18:00:53Z and 18:04:05Z]. |
| 2026-11-11 12:28 | `fac68d4` | Amendment 5, *after* the Freeze 1 sealed result: sealed misses categorized; consequence rule for independence. |
| 2026-11-11 14:22 | `34ff8d0`, `2383347` | **Freeze 2** code and declaration, before the second sealed run. |
| 2026-11-11 ~14:28 | `bdc5fa2` (14:32) | Sealed exam, Freeze 2, once per group mode [sum: F2-sealed `generated_at` 20:28:42Z and 20:31:53Z]. |
| 2026-11-11 14:51 | `d3af2bc` | Freeze 2 sealed BW/C miss categorization (after the result). |

The sealed list can be recomputed: `tools/verify_gitrx.py quick` repeats the draw from the SrLC enumeration pages
with `random.Random(20261111).sample` and compares fingerprints with the ones committed in the plan.

### 1.3 The amendments: what changed, why, when, and the one that matters most

All of Amendments 1 to 4 were made, and their effects measured, on the **open** split only, and all before the
Freeze 1 sealed run (see timestamps above).

- **Amendment 2** (11-11 09:25, `b20e00e`): the primary text match became whitespace-insensitive, because label XML
  puts spaces inside cross-references and symbols ("( 5.1 )", "SPORANOX ®") and SrLC pages contain broken-word
  artifacts ("open- label"). Miss labels became descriptive, not attributive. Correction recorded the same day
  (`8bf2383`): the "55% of sampled misses" figure had come from a looser letters-and-digits comparison;
  whitespace-free matching alone raised open text recall from 73.6% to 75.7% [plan].
- **Amendment 3** (09:49, `402b817`): separates *parser accuracy* from *SrLC/DailyMed timing*. Reason, from open-split
  diagnostics [plan]: of text misses that were already in the label before the compared pair, only 30% were in the
  first captured version, while 19% appeared 31-180 days and 17% 181-365 days before the SrLC approval, 35% more than
  a year earlier (labels change before approval, for example under Changes Being Effected; a -30 d window misses
  them); and about 40% of fragments absent from every captured version differ only in rendering ("?" for non-ASCII,
  bullets, case, invisible format characters). The new **primary metric** locates, for each fragment, the captured
  transition (absent in one captured version, present in the next) nearest the approval date within
  [approval - 730 d, approval + 365 d], then asks whether GitRx reported that fragment in the added tokens of a
  substantive change of the same SrLC group in that pair. Also added: rendering normalization (case-fold, remove
  Unicode format characters, strip leading list markers, an SrLC "?" matches any one non-ASCII character).
- **Amendment 4** (10:43, `a574d6f`): group conventions. Of 80 sampled open group misses, 67 were old-format
  (non-PLR) labels in which SrLC files text from subsections under PRECAUTIONS/WARNINGS (Drug Interactions,
  Pediatric Use, ...) under WP while GitRx used the subsection topic; 12 were SrLC placements differing from the
  label; 1 was a GitRx classification error [plan]. Rule: those subsections get group WP with the topic group kept as
  alternative; the grader accepts either ("either" mode). The stricter "primary" mode (WP only) is reported alongside.
  SrLC itself is inconsistent here.
- **Freeze 1 / Freeze 2** (section 1.4). **Amendment 5**: described in section 4.

**What this changes about the pre-registered bar - read this.** The original bar (>= 95% section recall and >= 95%
verbatim text recall, Amendment 1 definitions) is the *end-to-end* quantity. It was **not** met on the sealed set:
section recall 94.04% (Freeze 1) and 94.00% (Freeze 2), text recall 78.60% and 79.05% (render matching; strict
75.44% and 75.85%) [sum: F1-sealed-either, F2-sealed-either]. Amendment 3 moved the pass criterion to the parser-accuracy metric after the
open-split run showed 94.2% section and 73.6% text end to end [plan, Amendment 2]. The argument for the move is that
most of the end-to-end gap is not parser behavior (section 3). The counter-argument is real: the criterion was
changed after seeing a failing open-split result, even though before any sealed result. A skeptic should treat the
end-to-end figures as the unmoved original metric and the parser-accuracy figures as the post-hoc, better-targeted
one. Both are reported for every run.

### 1.4 Freeze 1 and Freeze 2, side by side (sealed set, group-mode either unless stated)

Freeze 1: parser `spl-sections-0.2.2`, changes `srlc-changes-0.2.2`, groups `srlc-groups-0.2.0`, harness
`srlc-grade-0.3.2`, commit `a00c5d27`. Freeze 2: parser `spl-sections-0.2.3`, changes `srlc-changes-0.3.2`,
groups `srlc-groups-0.3.0`, same harness, commit `34ff8d0e`. Inputs identical (gradebook `863aea67...`, split
`45725c78...`, index `a5739f4d...`). Changes between the freezes: HTML-spec table span parsing; uncoded boxed warnings
classified BW (`inferred:uncoded_box`); formatting-noise canonicalization; cross-reference-only edits kept as changes
flagged `reference_only` [plan].

| Sealed set metric (983 drugs; 3,365 entries, 2,626 graded) | Freeze 1 | Freeze 2 | Bar |
|---|---|---|---|
| Parser text accuracy, same group, render | 97.62% (17,180/17,599; CI 97.38-97.83) | **98.30%** (17,299/17,599; CI 98.09-98.48) | >= 95% (**primary**) |
| Parser section accuracy | 99.08% (4,102/4,140; CI 98.74-99.33) | **99.64%** (4,125/4,140; CI 99.40-99.78) | >= 95% (**primary**) |
| Primary group mode: text / section | 96.48% / 97.92% | 97.15% / 98.48% | reported |
| Text strict / ws / any-group | 93.39 / 96.61 / 99.24 | 94.05 / 97.27 / 99.84 | reported |
| End-to-end section recall (Amendment 1 window) | 94.04% | 94.00% | original bar 95%: **not met** |
| End-to-end text recall, render / strict | 78.60% / 75.44% | 79.05% / 75.85% | original bar 95%: **not met** |
| Not locatable fragments | 7,716 of 25,315 | 7,716 of 25,315 | coverage |
| Located misses: different group / pair not comparable / same-group outside added tokens / raw-XML-not-parsed | 285 / 111 / 15 / 4 | 272 / 0 / 19 / 4 | |

[sum: F1-sealed-either, F2-sealed-either, `-primary`; confirmed by `tools/verify_gitrx.py quick`, which recomputes each from the jsonl.]
By group (text, either): AR 98.1 -> 98.5, BW 84.7 -> **90.4**, C 92.8 -> **93.8**, DI 95.2 -> 95.5, MG 97.7 -> 98.4, USP 97.8 -> 98.5,
WP 98.6 -> 99.1; BW section 90.2 -> 91.3 [sum]. BW and C text remain below 95% (section 4).

The reference results on the open split with the same frozen code [sum: F1-open-either, F2-open-either, jsonl]:

| Open split (2,295 drugs; 7,802 entries, 5,880 graded) | Freeze 1 | Freeze 2 |
|---|---|---|
| Parser text / section accuracy, either | 98.44% (38,417/39,027) / 99.70% (9,415/9,443) | 98.48% (38,432/39,027) / 99.74% (9,418/9,443) |
| Primary mode | 97.83% / 98.70% | 97.87% / 98.73% |
| End-to-end section / text (render) | 94.56% / 77.94% | 94.32% / 77.92% |
| Open BW text | 97.88% (647/661, 144 drugs) | 97.88% (647/661, 144 drugs) |

Freeze 2 is **not** an independent test. The uncoded-box rule was motivated by a categorized sealed miss (ATIVAN, setid
`89057c93`), so the sealed set is "seen" for that rule [plan, Amendment 5 and Freeze 2]. The span and noise fixes were
motivated by open-split sweeps and open precision only [plan]. Honest reading of the open row: the rule changed nothing
on the open split (open BW text is 647/661 under both freezes), so the open data neither supports nor contradicts the
sealed BW gain from 84.7% to 90.4%; that gain is "seen" and, per the plan's own account, matches both the ATIVAN uncoded box and a JENTADUETO table-span defect.

**Prospective holdout.** To get an independent confirmation, `gitrx_core/srlc_update.py` (monthly job; owner-approved
2026-11-11 [git `e691f70`]) re-enumerates recent months of the SrLC database and seals every entry whose supplement
date or "Database Updated" date is after 2026-11-11 (`HOLDOUT_AFTER`): such entries are never graded, printed or
summarized; the snapshot omits their supplements and changes; each run writes a manifest of IDs and fingerprints
with no text (`srlc/updates/holdout/manifest-*.json`). As of this writing the four manifests contain 0 entries
(fingerprint `e3b0c442...` is the sha256 of the empty string): nothing is yet held out and no holdout result exists.
GitRx must also have captured the new label versions for an entry to be gradable (a weekly DailyMed job exists).

## 2. What each metric is

Unit: an SrLC *row* is (drug, supplement, SrLC group). A *fragment* is one underlined span, split at the SrLC
ellipsis, normalized, with >= 3 tokens ("main"); shorter fragments and rows that only refer to a table are reported
separately [plan]. A *candidate* is a DailyMed setid whose SPL approvals contain the SrLC application number.

- **Parser accuracy (text)**: among *located* main fragments, the share that GitRx reports, in the same SrLC group, inside
  the added tokens of a substantive change of the located pair. Located means: some candidate has a captured version
  without the fragment followed by a captured version with it, to-version published within [approval - 730 d,
  approval + 365 d]; the transition nearest the approval is chosen. **Presence is decided on the tag-stripped raw
  XML text of each version, not on GitRx's parsed sections** (`srlc_grade._strip_xml_text`, `load_or_build_fulltext`).
  So a parser that drops or mangles text does not escape the metric: the fragment is still located in raw XML and is
  scored as a miss (label "present in raw XML but not parsed sections": 4 sealed cases in each freeze).
- **Parser accuracy (section)**: among rows with at least one located fragment, the share where the located pair has a
  substantive change in that SrLC group.
- **End-to-end recall**: Amendment 1's window and no relocation: the row's group is changed in a bracketing
  pair (section recall), or the fragment appears in a changed same-group section's added tokens of a bracketing pair
  (text recall). Entries with no candidate setid or no captured version in the window are *excluded* and reported
  as coverage (sealed: 483 + 256 of 3,365 entries; open: 1,160 + 762 of 7,802) [sum].
- **Matching variants**: `strict` = literal NFC normalization, case-sensitive, tokens; `ws` = whitespace-free; `render` =
  case-fold, format characters removed, list bullets stripped, SrLC "?" matches one non-ASCII character. `render` is
  primary. Group modes: `either` (primary or alternative group) and `primary`.
- **Precision**: section 5. **Wilson 95% intervals** treat fragments as independent; they are not (fragments cluster by
  drug and label), so they are optimistic; see the cluster bootstrap in section 4.

## 3. Why the metrics differ: parser accuracy 98.3% versus end-to-end recall 94% (section) and 79% (text)

The same SrLC fragments, scored two ways [sum: F2-sealed-either]:

**Parser accuracy** conditions on the fragment being locatable in captured history. Of 25,315 main fragments in
parser-gradable entries, **7,716 (30.5%) are not locatable**:

| Reason | Fragments | What it is |
|---|---|---|
| never present in any captured version | 3,344 | the SrLC text is not in any version GitRx holds (new approvals not yet on DailyMed or never captured; SrLC text differing from the label - section 7; labels outside the captured families) |
| present from the first captured version | 3,169 | no absent-to-present transition: captured history starts after the change, or the setid is new |
| transition outside [approval - 730 d, + 365 d] | 1,203 | the text changed in captured history, but far from the SrLC date |

Not-locatable fragments are not parser errors and not parser successes; they say nothing about the parser, and they
are the reason the parser metric is computed on a subset (Amendment 3). The identical decomposition holds for
Freeze 1 and Freeze 2 [sum] (changes to the parser do not change what is locatable, because locating uses raw XML).

**End-to-end text recall (79.05%, 18,398 of 23,273 main fragments)** counts every fragment whose entry could be
graded. Its 4,875 misses are labelled descriptively [sum: F2-sealed `text_miss_labels`]:

| Label | Count |
|---|---|
| absent from every captured version, including window versions | 2,064 |
| present in the from-version (published inside the window): the text was added by an earlier version in the window than the pair the bracketing finds | 1,394 |
| no comparable in-window pair | 597 |
| present in a version published > 30 d before approval | 410 |
| present in the to-version but not in a changed same-group section | 232 |
| present only after the window (138), only before (15), before and after (2) | 155 |
| in a changed same-group section but outside GitRx's added tokens | 20 |
| present in raw XML but not parsed sections | 3 |

Only the last two rows (23 fragments, 0.1% of the 23,273) are parser/diff behavior; the rest are about *which pair*
the text entered in, relative to the SrLC date, or about text GitRx does not hold. Why timing and bracketing matter: the
SrLC date is FDA's approval of a supplement; the DailyMed publication date is when the sponsor's version was posted.
Labels can change under Changes Being Effected before approval, and posting can lag by days to months. The Amendment 1
window [-30 d, +365 d] is a fixed compromise, and "version bracketing" errors (the right text appears in a neighbouring
pair) are the largest cause of end-to-end misses. SrLC's own posting lag is visible (section 7).

The three matching variants isolate formatting: on the sealed set, parser text accuracy is 94.05% strict, 97.27% ws, 98.30% render, 99.84%
any-group-render. The gap between 94% and 98% is spacing, case, bullets and "?" artifacts, not missing text. (On the open split, 292 main
end-to-end fragments contain a "?" [jsonl]; 193 are found under render matching and 81 under strict.)

`either` versus `primary`: the group-mode difference (sealed text 98.30 vs 97.15, section 99.64 vs 98.48) is
entirely the non-PLR convention of Amendment 4 (old-format labels where SrLC files WARNINGS/PRECAUTIONS subsections under
WP or under the topic group, inconsistently). The open split shows where it bites: DI text 96.3% (either) vs 89.4% (primary)
[sum: F2-open]. Both are reported because the choice is a judgment about SrLC's convention, not about label content.

## 4. Why sealed BW (90.4%) and C (93.8%) text are below 95%

Sealed Freeze 2, parser text accuracy, either mode: BW 283 of 313 located fragments (30 missed); C 272 of 290 (18 missed)
[sum: F2-sealed-either]. Section accuracy: BW 91.3% (84/92), C 99.3% (146/147).

**Categorization (descriptive, done after the result, not used to re-score, and not tuning).** Listing every
sealed BW and C miss from the kept `fragments.jsonl` [jsonl, reproducing the plan's d3af2bc categorization]:

| Group | Misses | By drug (count) | What the SrLC row is |
|---|---|---|---|
| BW | 28 of 30 | XARELTO 8, VENCLEXTA 10 (8 + 2 under "6.1 Clinical Trials Experience"), ROXYBOND 4 ("8.2 Lactation"), KESIMPTA 3 ("Medication Guide"), LORBRENA 2, JANUVIA 1 ("5.4 Hypoglycemia ...") | rows filed in SrLC under the "Boxed Warning" heading whose own subheading or text names another section (XARELTO "5.4 Use in Patients with Renal Impairment", VENCLEXTA "5.1 Tumor Lysis Syndrome" and "6.1", LORBRENA "5.2 CNS Effects", a Medication Guide storage paragraph). GitRx found each of them in the section the row itself names. |
| BW | 2 of 30 | LATUDA 2 | GitRx marks the box changed and contains the sentence, but the word-level diff aligns most of it with the prior box sentence: word-level diff granularity, a real (small) GitRx limitation. |
| C | 17 of 18 | ZEPZELCA 17 | one SrLC row filed under "4 Contraindications" whose text is "5.1 Myelosuppression". |
| C | 1 of 18 | TRILEPTAL 1 | section 4 changed and contains the fragment, but the diff aligned "or to" with existing tokens: diff granularity. |

[plan, "Freeze 2 sealed BW/C miss categorization"; drug and section names reproduced from `fragments.jsonl`.] Therefore: excluding the SrLC rows
that contradict their own heading, BW is 283/285 (99.3%) and C 272/273 (99.6%); with the three diff-granularity cases the
remaining GitRx-side misses are 3 fragments. In both groups the any-group text accuracy is 100% (BW 313/313, C 290/290):
every fragment is in a changed section somewhere in GitRx's record, just not under the heading SrLC filed it. [sum: F2-sealed-either `text_any_group`.]

Why small numbers mislead here. Seven BW drugs account for all 30 BW misses (of 74 sealed BW drugs, 313 fragments) and six drugs for
28 of them. A drug-level cluster bootstrap of the open BW fragments (the open BW accuracy is 97.88%, 647/661, 144 drugs, 9 drugs
with the 14 misses, Kyprolis alone 5): I resampled the 144 drugs 200,000 times with seed 20261111 on the Freeze 2 open data [jsonl];
the lowest resampled accuracy was 91.5%, so none reached the sealed 90.4% (or Freeze 1's 84.7%). A 20,000-resample 95% interval
is 95.8%-99.3%; the development transcript reports 95.86-99.31 for 2,000 resamples under Freeze 1 and "never reached the sealed
miss rate" for 200,000 [transcript; plan Amendment 5]. Two readings follow, and the data cannot separate them: either the sealed
set contains a few drugs with unusual SrLC filing (the categorization above), or BW generalization is genuinely worse than the
open split suggests. The categorization favors the first; it is an after-the-fact reading, which is why the sealed BW/C text figures are
reported unretouched and flagged below the bar. The open BW misses (14 fragments) were audited under Freeze 1 and described as SrLC
filing choices (BW rows for labels without a box; BW text the label places in 5.x or 17; text referenced from the box) [plan Amendment 5;
transcript for the per-case audit]; the 296 of 296 box sections classed BW in that audit [plan] are likewise only documented, not kept as a file.

**Amendment 5 and the independence rule** (12:28, after the Freeze 1 result [git `fac68d4`]): categorizing the sealed misses was
a deliberate owner decision, because the plan requires every miss to be categorized. The consequence stated in advance: any change
motivated by sealed misses makes the sealed set non-independent for that change; such changes are validated on a fresh, later
holdout and the sealed set is reported as "seen" for them. The uncoded-box rule is the case (below).

## 5. Why precision is about 80% crude and 87% timing-robust, and what the remainder is

### 5.1 Definition (also in the module docstring of `gitrx_core/srlc_precision.py`)

- **Unit.** One entry of the `changes` list of a GitRx comparison between two consecutive captured versions of one
  setid, in one of the seven SrLC groups. A section added, removed or modified is one unit. Units in sections SrLC does not
  cover (indications, dosage, how supplied, ...) are counted separately (open Freeze 2: 76,567) and never scored.
- **Population.** The graded pairs of the **open** split: for each SrLC supplement with candidate setids and a captured candidate
  version in the Amendment 1 window, each consecutive captured pair of a candidate whose to-version is in that window
  (10,272 pairs; one pair graded for several supplements counts once).
- **Crude match.** The unit's SrLC group (or its alternative group, in `either` mode) equals the group of at least one SrLC
  change row of a supplement that graded the pair. Crude precision = matched / all SrLC-group units. This equals the harness
  counters `matched_by_srlc` and `gitrx_only_srlc_group`.
- **Timing-robust match.** Crude match, or an SrLC supplement of the same drug and the same group whose approval date is
  within [to-version date - 365 d, to-version date + 730 d] (the Amendment 3 locator window seen from the other side).
- **Why GitRx-only is not an error.** SrLC lists only safety-related labeling changes, only NDA/BLA holders, and only what
  FDA recorded and posted; SrLC's own recall is unknown [plan]. A GitRx change without an SrLC row can be a real change SrLC
  does not record (a non-safety edit inside a safety section, a deletion, table data), a timing mismatch, a formatting artifact,
  or a GitRx error. The categories below are heuristics that make the composition visible; none is an adjudication.
- **Counted versus excluded `reference_only`.** Edits that only renumber cross-references are kept as changes flagged
  `reference_only` (owner decision, srlc-changes-0.3.2) because SrLC underlines most of them (below); both precisions are given.

### 5.2 Reproduction of the published numbers

Recomputed with `gitrx_core/srlc_precision.py` from the raw ZIPs (the pair-digest cache of the original scripts was deleted on
2026-11-11; I rebuilt all 10,272 digests into a new directory, in about 30 minutes), open split, Freeze 2 code, either mode:

| | Published | Recomputed | Match |
|---|---|---|---|
| Crude, `reference_only` counted | 79.75% | **79.75%** (63,105 / 79,125) | exact |
| Crude, `reference_only` excluded | 80.54% | **80.54%** (n = 75,779) | exact |
| Timing-robust, counted | 86.56% | **86.56%** | exact |
| Timing-robust, excluded | 87.19% | **87.19%** | exact |
| Formatting share of unmatched | 26.9% (4,306 of 16,020) | 26.88% (4,306 / 16,020) | exact |
| `reference_only` units | 3,346 (2,069 matched) | 3,346 (2,069 matched) | exact |
| Unmatched units vs kept `gitrx_only.jsonl` | | identical multiset (16,020) | exact |
| Freeze 1 crude (`F1-open-either`) | 77.62% (67,260 / 86,654) | **77.62%** (67,260 / 86,654) | exact |
| Freeze 1 timing-robust | 84.93% | **84.93%** | exact |
| Freeze 1 formatting share | 39.5% (7,664 of 19,394) | 39.5% (7,664 / 19,394) | exact |

[prec; sources of the published values: plan Freeze 2 and the transcript's precision table.] Per-group values equal the original
scripts' output (`bw-precision/p2b.out`, `fx/prec3/p2b.out`) to the second decimal. One reproduction detail worth knowing: a first
rebuild resolved 10,268 of 10,272 pairs, because four pairs need raw ZIPs that exist only in the SSD copy of the former
internal-disk store (`raw-internal-20261111/`); with that store searched the totals match exactly. Digest rebuilding is deterministic: digests built now
equal the ones the original run used (identical unit multisets).

Freeze 1 was reproduced the same way from a `git archive` of the frozen commit `a00c5d27` (so the Freeze 1 parser and comparer built the digests; the module is code-version agnostic and `reference_only` is reported unavailable there), with the unmatched units identical to the kept `gitrx_only.jsonl` (19,394). Freeze 1 per-group crude/timing-robust: BW 39.02/43.17, C 35.28/40.98, WP 84.67/90.98, AR 81.74/89.84, DI 54.87/62.96, USP 77.98/85.50, MG 80.78/88.81 [prec]. No number needed adjusting; none failed to reproduce.

### 5.3 Per-group precision, open Freeze 2, either (crude / timing-robust / with `reference_only` excluded)

| Group | Units | Crude | Timing-robust | Crude excl. ref-only | Unmatched: formatting share |
|---|---|---|---|---|---|
| BW | 1,563 | 43.06% | 47.09% | 43.74% | 243 / 890 = 27.3% |
| C | 2,109 | 37.79% | 43.29% | 43.33% | 635 / 1,312 = 48.4% |
| WP | 22,438 | 86.04% | 91.97% | 86.30% | 846 / 3,133 = 27.0% |
| AR | 16,764 | 83.51% | 90.77% | 83.69% | 718 / 2,765 = 26.0% |
| DI | 5,189 | 59.11% | 67.01% | 61.39% | 695 / 2,122 = 32.8% |
| USP | 16,472 | 80.67% | 87.46% | 81.77% | 834 / 3,184 = 26.2% |
| MG | 14,590 | 82.08% | 89.85% | 81.90% | 335 / 2,614 = 12.8% |
| **All** | **79,125** | **79.75%** (Wilson 79.47-80.03) | **86.56%** | **80.54%** | 4,306 / 16,020 = 26.9% |

[prec; `precision.json`.] By kind: modified 78.32% (55,319), added section 85.46% (14,262), removed section 79.52% (9,544); a removed BW
section matches only 19.6% (37 of 189) and a removed C section 43.6% [prec]. The Wilson intervals are optimistic (clustering).

### 5.4 What the unmatched 20% is made of

Open Freeze 2, 16,020 unmatched SrLC-group units, ordered categories (first that applies) [prec; heuristic, not adjudicated]:

| Category | Units | Share |
|---|---|---|
| a. formatting noise (added and removed tokens equal after case/punctuation/spacing/list-number/date canonicalization, or whole sections equal after removing cross-references, dates and numbering) | 4,306 | 26.9% |
| b. timing: the same text or the same group has an SrLC row for another supplement of the drug within [to - 365 d, to + 730 d] | 3,455 + 1,114 | 21.6% + 7.0% |
| c4. added text containing a safety keyword (risk, fatal, bleeding, ...) with no SrLC row: candidate SrLC omission, **not** established as such | 1,739 | 10.9% |
| c5. other added text, unexplained residual | 2,759 | 17.2% |
| d. pure deletion or no text delta | 1,827 | 11.4% |
| c3 non-safety content (dose, trial, population terms) / c1 table data / c2 editorial rewording | 476 / 262 / 82 | 3.0 / 1.6 / 0.5% |

Bounds on precision implied by these categories (so a reader can pick a stance) [prec]: if formatting noise were the only true
error class, precision would be 94.56%; counting formatting and pure deletions as errors, 92.25%; counting formatting, deletions
and the unexplained c5 residual as errors, 88.76%. Treating all unmatched as errors gives the crude 79.75%. By group the
"formatting + deletions + c5 are errors" bound is BW 63.5%, C 54.2%, DI 72.8%, USP 89.8%, AR 91.5%, WP 92.7%, MG 91.8%.

**The formatting-noise share over versions** (open, same method) [transcript for Freeze 1 and 0.3.1; prec for 0.3.2]: Freeze 1 39.5%
(7,664 of 19,394 unmatched), changes 0.3.1 20.6%, 0.3.2 26.9% (4,306 of 16,020). The 0.3.1 to 0.3.2 rise is because `reference_only` units
(cross-reference renumberings, kept as changes in 0.3.2) fall into the formatting yardstick. Crude precision over versions: 77.62 (F1) -> 78.67
(interim 0.3.0) -> 80.53 (0.3.1) -> 79.75 (0.3.2, `reference_only` counted) / 80.54 (excluded) [transcript; the F1 and 0.3.2 values and the
interim run summaries are in the kept `open-*` dirs]. Formatting kinds among the 4,306: section-level canonical equal 1,534, punctuation only
1,304, case only 519, spacing only 415, case plus punctuation 240, other 294 [prec].

### 5.5 Why BW, C and DI are lowest

The data say where, not why with certainty. BW: of 890 unmatched units, 173 are pure deletions, 186 are added safety-keyword text, 155 residual,
243 formatting; removed sections match only 19.6%. SrLC underlines added or revised words, so a removed box or a box deletion plausibly has nothing to underline, and
the box may not be re-listed when only its wording is re-wrapped (neither is tested here). C: 635 of 1,312 unmatched are formatting-like and 341 of the 2,109 units are `reference_only`
(only 31 of them matched), consistent with Contraindications text carrying many "(5.x)" cross-references that renumber when Warnings sections are reorganized;
excluding them lifts C from 37.8% to 43.3%. DI: 695 formatting-like, 338 pure deletions, 379 residual; DI is also the group with the strongest `either`/`primary`
convention dependence (section 3), and drug-interaction tables change often. These are readings of counts, not tested explanations.

### 5.6 How to read the 80%

Precision here is "SrLC agrees", not "the change is real". The 4,306 formatting units and 1,827 deletion/no-delta units are cheap to inspect (`records.jsonl`
in the precision output lists every unit with its added and removed tokens). Recall of SrLC itself is unknown, so a sizeable part of the "unmatched" bucket may be
true label changes SrLC did not record. The timing-robust rate (86.56%) credits label changes whose SrLC supplement is up to a year earlier or two years later.

## 6. Every judgment call and its trade-off

1. **Metric moved after the first open results** (section 1.3). Trade-off: a metric aimed at the parser, at the price of excluding 30.5% of fragments as
   not locatable; the original metric is still reported and fails the bar.
2. **Drug-level split, DrugNameID** (Amendment 1). Prevents leakage between supplements of one drug; makes the sealed set clustered (74 BW drugs in
   the sealed BW sample), so small groups have wide uncertainty.
3. **Window [-30 d, +365 d] (end-to-end) versus locator [-730 d, +365 d]** (Amendments 1 and 3). The wide locator reduces timing artifacts and adds a way to pick
   the wrong transition when a text recurs; the transition nearest the approval is chosen, ties broken by setid.
4. **Fragments of >= 3 tokens only in the bar**; shorter ones are reported separately (sealed end-to-end render: 84.98% of 17,647 short fragments;
   85.22% of 433 table-referring rows) [sum]. Short fragments match by chance more easily, so they are not in the bar.
5. **Matching normalization** (Amendments 2 and 3): each relaxation widens matching. The SrLC "?" matching any one non-ASCII character can create false
   matches; the strict and ws variants stay reported and differ by about 4 points of parser text accuracy (94.05 strict versus 98.30 render, sealed).
6. **Group mode `either`** (Amendment 4): accepts SrLC's inconsistent placement of non-PLR subsections at the cost of leniency; `primary` is reported with every run.
7. **Candidates by application number.** Includes sibling products and repackagers sharing an application number; a found match in any candidate counts. A
   diagnostic excluding candidates flagged as siblings or repackagers (370 of 1,448 candidate setids in the sealed run) gives 93.4% section and 79.1% text
   end-to-end against 94.0% and 79.1% overall [sum: F2-sealed `sibling_or_repackager_diagnostic`]: small effect.
8. **Noise canonicalization** (`changes.py` NOISE, 0.3.0 to 0.3.2). Formatting-only differences are not changes: trademark symbols, list bullets, footnote and media ids,
   "(5.1)"-style cross-reference numbers and respacings, list numbering at line start, "Revised:"/date lines, repackager codes, and boundary-only spacing differences.
   Deliberate limits: numbers that are not cross-references (doses, percentages, ages, table cells) and words are **never** noise, so a brand-to-generic swap or a
   "see Contraindications" to "see Warnings" edit stays substantive; a boundary that touches a digit ("1 0" versus "10", "5. 5" versus "5.5") is not noise. Trade-off:
   SrLC itself underlines such edits, so 48 end-to-end rows that Freeze 1 found are not found under 0.3.2 (found under Freeze 1 and not now; 10 moved the other way): net
   section recall on the open split 94.56% -> 94.32% (-38 rows) [jsonl: 48 lost, 10 gained, 47 labelled "other groups only, unchanged section of this group"
   and 1 "no substantive change"]. The development report attributes all 48 to formatting-only pair differences (spacing 216 edits, bullets 135, cross-reference respacing 94,
   footnote/media ids 12, trademark 8, revision lines 2; e.g. BOTOX C 2018, CELEBREX BW 2016, ZOLOFT 2023) [transcript]. The owner accepted the -0.25 point rather
   than adding a flagged category for bullets and spacing (the shipped code has none).
9. **`reference_only` kept as changes** (owner decision, 0.3.2): SrLC counts cross-reference renumbering (2,069 of 3,346 `reference_only` units matched, 61.8%,
   compared with 79.75% overall), and dropping them would have hidden changes SrLC records. Precision is given both ways (79.75 vs 80.54).
10. **Uncoded-box rule** (spl-sections-0.2.3). A top-level section with a generic or missing code, before the first major section, in a document with no coded boxed
    warning (34066-1) anywhere, not a Highlights section, whose title, first narrative text (when untitled) or first top-level table cell starts with upper-case
    `WARNING` or `BOXED WARNING`, becomes BW, flagged `inferred:uncoded_box`. Excluded by design: plain "WARNINGS"/"Warning", "SEE BOXED WARNING", "WARNING AND
    PRECAUTIONS", Recent Major Changes, anything after the first major section. Prevalence in the rebuild: 410 of 488,016 versions (0.084%), 137 setids
    (title 72, lead 194, table 144 sections) [query of the rebuild shards, group_basis `uncoded_box:*`; the development report counted 407 in its 487,201-version
    sweep]. Checks reported by the developer: a first draft detected 510 versions, a sample showed false positives (the Highlights copy of the box; WARNING headers
    before a coded box), two exclusions removed them; a 50-detection sample (seed 20261112) all read as real boxes; a 50-non-detection sample found 12 tables headed plural
    "WARNINGS" and one "WARNINGS:" box that stay excluded - a known limitation [transcript, not kept as a file]. Trade-off: it targets mostly ANDA labels (336 of 407
    versions in the development report's count [transcript]) that SrLC does not cover, so it cannot be validated on SrLC for most of its prevalence; it was motivated by one sealed miss and is "seen".
11. **Pairing and consecutiveness.** Changes are computed between consecutive *captured* versions; when versions are missing the pair is flagged non-consecutive
    (37,563 pairs in the rebuild [COMPLETE.json]) and no intermediate version is invented. A change first seen in a pair may have happened earlier.
12. **No tuning on sealed results**, and the freeze declarations list code hashes; but the sealed set was run twice and categorized (section 4), so it is not
    pristine for the BW/C story.

## 7. FDA/SrLC database and website issues observed (with evidence)

Counts are over the gradebook built from the crawl (3,278 drugs, 11,167 supplements, 40,562 change rows) unless noted. None of these is a claim that FDA is wrong in
a clinical sense; they are properties that make SrLC an imperfect gradebook.

1. **Rows filed under a heading whose own text names another section.** Sealed Freeze 2: 28 of the 30 BW misses and 17 of the 18 C misses come from such rows (section 4).
   Across the whole gradebook, 12 of 833 BW rows have a subheading beginning with a numbered section heading or naming a Medication Guide (for example SABRIL, JANUVIA,
   VENCLEXTA, ROXYBOND, KESIMPTA) [gb; a conservative count, because several sealed cases such as XARELTO do not carry a numeric subheading].
2. **Non-ASCII characters rendered "?"**: 549 of 173,383 underlined spans (426 rows) contain "?" [gb]; without a "?" wildcard, 292 main end-to-end open-split fragments with "?" match only
   81 times strictly versus 193 under render matching [jsonl]. About 40% of fragments absent from every captured version differed only in rendering in the Amendment 3 sample [plan].
3. **Formatting-only and renumbering edits are underlined as changes**: 140 spans contain bullet glyphs and 14 contain "Revised"/"Rev." text [gb]; 61.8% of GitRx's cross-reference-only
   changes have an SrLC row (above); the lost-row analysis (section 6, item 8) shows SrLC credits spacing, bullet and revision-date edits [jsonl, transcript].
4. **Elisions**: 11,637 of 40,562 rows (28.7%) contain "…" inside the underlined text, which the harness must split on [gb `has_elision`]; 163 rows refer to a table instead of quoting text.
5. **BW rows for labels without a box and box-referenced text filed as BW**: the 14 open BW parser misses, 9 drugs (KYPROLIS 5, TRULICITY 2, then JEVTANA KIT, FIORICET W/ CODEINE,
   SUBSYS, PEGASYS, SPINRAZA "6.3 Postmarketing Experience", EMFLAZA "Patient Counseling Information", LEQEMBI 1 each) [jsonl]; the per-case audit calling these SrLC filing choices is
   [plan Amendment 5; transcript].
6. **Detail page versus per-drug CSV disagree**: of 18,705 (drug, supplement, group) triples reconciled across 1,548 drugs, 18,690 match and 15 differ in row count (CSV has more rows in
   14, the HTML page in 1) [gb `reconcile`; REPORT.md says "15 count mismatches"]. The detail page is the gradebook source; the CSV is reconciliation only (it was not fetched for all drugs).
7. **Unmapped group**: 541 rows are filed under heading "Other" and cannot be assigned to an SrLC group; they are excluded from group-level grading (131 in the sealed set) [gb, sum].
8. **Late postings**: of 10,203 search results (one per supplement), the "Database Updated" date is after the supplement's approval date in 10,111, equal in 59 and *before* it in 33;
   82 are more than 90 days late, nine more than 365 days, the maximum 1,349 days, median 5 days [gb `search_hits`]. A 30-day-before/365-day-after window is therefore usually, not always, adequate,
   and an entry can be added to the database long after it exists.
9. **Coverage limits**: NDA and BLA only (2,896 + 382 drugs), no ANDA generics; the supplement dates start 2014-12-19 (one supplement) and the search enumeration starts 2016-01-06 [gb]. Of the entries,
   14.4% of sealed and 14.9% of open (483/3,365; 1,160/7,802) have no candidate setid in the captured corpus, and a further 7.6% and 9.8% have no captured version in the window [sum] - SrLC records
   changes GitRx cannot grade.
10. **Rate limiting**: after about 45 requests at 12 requests/second (12 lanes, owner-directed after 3.2 s single-lane spacing) Akamai returned HTTP 403 "excessive requests" 31 times; the crawler stopped on its
    consecutive-failure guard and made no retry [srlc/REPORT.md]; 4,995 of the 5,000-request cap were used. The crawl is polite single-lane; the failure is documented so a second auditor does not repeat it.
11. **DailyMed side**: the v2 `spls.json` listing ignores its `published_date_gte` filter (the server returns newest-first; verified live [transcript]; the weekly update job pages until two consecutive pages are older
    than the watermark). DailyMed has several date fields with different meanings (XML `effectiveTime` is the sponsor's label date; "published" is when the version was posted; neither is FDA's approval date) - [LIMITATIONS.md](../LIMITATIONS.md).

GitRx-side limitations and bad practices found and fixed (from this record):
- **The earlier "validation" was AI-graded** (section 1.1); corrected 2026-11-10, historical files left unedited, and the corrected position is in AGENTS.md and LIMITATIONS.md.
- **SSD disconnection during the rebuild** (about 15:47 on 2026-11-11): shard `90` was finalized while the volume was dropping; it was quarantined (`quarantine-ssd-disconnect-20261111/`), rebuilt, and the code now
  refuses to finalize a shard after any raw read error or more than 3 `raw_not_found`/`raw_unreadable` [runlog.jsonl; `rebuild.py`; transcript]. The first 144 shards were built before this hardening; the data-producing
  code path is unchanged and sidecars record which `rebuild.py` hash built each shard.
- **Corrupted ledgers**: the SrLC crawl ledger was malformed twice when the SSD disconnected (2026-11-11 02:56Z and about 03:20Z; likely overheating) and was recovered fully with `sqlite .recover`
  (2,253 and 2,593 rows equal to the logged totals; the second corrupt copy is kept) [srlc/REPORT.md]. Earlier, two `ledger-original.sqlite` files in the backfill incident folder were found unreadable (0 rows) and their recovered copies hold 1,420 and 1,432 rows [transcript].
  The cause of these corruptions was not established beyond the SSD disconnects; "concurrent load" as a cause is not supported by a kept record.
- **A non-durable path**: the rebuild's `build-config.json` records its raw manifest in a session scratch directory (copy kept on the SSD).

## 8. Known limitations and what would falsify the claims

Limitations (also in [LIMITATIONS.md](../LIMITATIONS.md) for the product):
- SrLC is the only external check, and it is incomplete, FDA-curated and inconsistent in places. Nothing validates sections outside SrLC's reach (pre-2016, generics, tables, non-safety sections); there
  GitRx is covered by the same frozen code and mechanical invariants only (text conservation, order, determinism - build-failing in `spl_parser.py`).
- The pass criterion was changed after open-split results; the original end-to-end bar is not met (section 1.3).
- The sealed set has been used twice and categorized; it is not an unseen test for the BW/C story or for the uncoded-box rule. No independent holdout result exists yet.
- 30.5% of fragments are not locatable, so parser accuracy says nothing about them; end-to-end recall is 79% (text) / 94% (section).
- Precision cannot be measured: SrLC recall is unknown. The categories in section 5 are heuristics; the "c4" bucket is a list to inspect, not a finding.
- Word-level diff granularity can misalign wording (LATUDA, TRILEPTAL) and group assignment for non-PLR labels is a convention, not a fact.
- Intervals ignore clustering except where a bootstrap is stated; units are not independent.
- No OCR; tables are extracted as text with structure caveats; the parser has no human validation.
- Evidence is on one external SSD; no public hosting; commits unpushed; independent backups are out of current scope [AGENTS.md].

What would falsify the claims, concretely:
1. A hash in VERIFY.md section 1 or in the plan not matching the artifact (the tool checks each).
2. `tools/verify_gitrx.py quick` or `full` failing: a re-derived change record whose `record_sha256` differs from the stored one, a metric that does not recompute from the jsonl,
   or a re-run of the harness whose summary differs from the kept one (it should be identical apart from time stamps).
3. A fresh holdout (SrLC entries posted after 2026-11-11, collected sealed by the monthly job) on which parser text or section accuracy falls below the 95% bar under the frozen Freeze 2 code.
4. An independent audit of the 14 + 28 + 17 categorized BW/C cases finding that SrLC's heading, not the label, is right (that GitRx misfiled the text).
5. A sample of the "unmatched" c4/c5 units (safety-keyword added text SrLC lacks) in which a domain expert finds the GitRx change is not in the label (a parse or alignment error) - that would reduce
   real precision below the crude figure.
6. A label family where the raw XML text contains SrLC text that GitRx's parsed sections lack (label "present in raw XML but not parsed": 4 sealed, 18 open fragments): the parser's text conservation would
   be broken.
