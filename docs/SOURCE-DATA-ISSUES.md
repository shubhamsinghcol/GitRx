# Structural flaws in DailyMed and FDA SrLC (what researchers should know)

GitRx compares two public U.S. government sources: **DailyMed** (the label documents distributed
by the National Library of Medicine) and FDA's **Drug Safety-related Labeling Changes (SrLC)**
database. Building GitRx meant downloading every captured DailyMed label version (158,955 label
families, 488,016 versions) and crawling SrLC (3,278 drugs, 40,562 change rows). Doing that
exposed structural flaws in both: places where the records are wrong, missing, corrupted or
inconsistent with each other. They affect **any** study that uses either source to answer
questions like "when did this warning enter the label?"

**What is not listed here.** Deliberate scope and policy choices are not flaws in the records,
and are documented in [LIMITATIONS.md](../LIMITATIONS.md) and
[VALIDATION-REASONING.md](VALIDATION-REASONING.md) instead. Examples:
- SrLC covering only brand-name (NDA/BLA) drugs, only from 2016, and only safety sections;
- SrLC underlining formatting edits;
- FDA's website rate limits.

None of this says FDA or NLM is clinically wrong. Each count is reproducible from the published
GitRx data (see [VERIFY.md](../VERIFY.md)). Fuller technical record:
[VALIDATION-REASONING.md section 7](VALIDATION-REASONING.md) and
[STEP-F-GAP-REPAIR.md](STEP-F-GAP-REPAIR.md).

Snapshot: DailyMed as collected through November 2026; SrLC as crawled 2026-11-10/11.

## Summary

| # | Flaw | Source | Scale | Main consequence |
|---|---|---|---|---|
| 1 | FDA approval and DailyMed posting run on different clocks, often far apart | both | months to years for a minority | "When did the label change?" has no single answer |
| 2 | DailyMed version history has gaps that cannot be filled | DailyMed | 429,282 version numbers never listed; 0 of 1,200 recoverable; 55 listed versions not downloadable | Absence of text in DailyMed is weak evidence |
| 3 | DailyMed history often starts after the change happened | DailyMed | 12.5% of SrLC phrases already in the oldest version | The start of DailyMed history is not the start of the label |
| 4 | SrLC files some text under the wrong label section | SrLC | 28 of 30 Boxed Warning misses, 17 of 18 Contraindications misses (sealed sample) | Section-level SrLC statistics are noisy |
| 5 | SrLC corrupts non-ASCII characters into "?" | SrLC | 549 underlined spans in 426 rows | Exact text matching against SrLC fails |
| 6 | SrLC entries are posted late, sometimes years late | SrLC | 82 supplements > 90 days late, max 1,349 days | SrLC is incomplete for recent periods |
| 7 | Boxed warnings are not always coded as boxed warnings | DailyMed (SPL) | 410 versions, 137 label families | Code-based searches miss some boxed warnings |
| 8 | DailyMed dates are missing, out of order, and its date filter does not work | DailyMed | 99 versions undated; 7 families out of order; listing filter ignored | Timelines and incremental downloads go wrong silently |

## 1. Two clocks: FDA approval versus DailyMed posting

- **The clocks:**
  - SrLC records the date **FDA approved** a labeling supplement.
  - DailyMed shows the new label only once the **manufacturer submits** the SPL file and DailyMed
    posts it, on the manufacturer's schedule.
  - Nothing in either record links the two events.
- **SrLC versus DailyMed:**
  - Of the SrLC phrases GitRx can locate in DailyMed history, **1,203 of 25,315** first appear more
    than 2 years before or more than 1 year after FDA's approval date.
- **Inside DailyMed:**
  - The label's own effective date and its posting date also drift. The median gap is 3 days, but
    **36,622 versions (7.5%) were posted more than 90 days after the label's effective date, and
    14,646 (3.0%) more than a year after**.
  - 2,240 versions have an effective date *later* than their posting date (216 by more than 30 days).
  - Source: `tools/audit_dailymed_dates.py` → [evidence/dailymed-date-audit-0.2.4.json](evidence/dailymed-date-audit-0.2.4.json).
- **Consequence:**
  - "When did this warning enter the label?" has at least three defensible answers, which can be
    months to years apart.
  - Matching SrLC to DailyMed needs a time window, and any window loses some cases.
  - GitRx keeps all three dates separate and never calls a DailyMed date an FDA approval date.

## 2. DailyMed history has gaps that cannot be filled

- **Skipped version numbers:**
  - SPL version numbers increase but skip. **429,282 version numbers are implied missing:**
    - 368,094 below the first captured version (2,899 families start at version 100);
    - 61,188 inside gaps, in 21,015 families.
  - **None** of them is listed in DailyMed's own per-family history.
  - GitRx requested 1,200 of them directly, and **all 1,200 returned HTTP 404** (95% upper bound on
    what could exist: 0.32%).
- **Listed but unavailable:**
  - 55 versions that DailyMed's current listing *does* show are not downloadable (HTTP 404, tried at
    least twice).
  - One family (NALTREXONE KIT, setid `6de5637f-...`) returns an empty history on every request.
- **Consequence:**
  - A change can happen in a version DailyMed never had or no longer has.
  - A change "first seen" in a version that follows a gap may have happened earlier. GitRx flags
    **37,563 non-consecutive version pairs**.
  - "Not in DailyMed" is weak evidence that text was not in the label.
- Source: [STEP-F-GAP-REPAIR.md](STEP-F-GAP-REPAIR.md).

## 3. DailyMed history often starts after the change

- **Already present:** for **3,169 of 25,315 (12.5%)** SrLC-underlined phrases, the phrase is
  already in the oldest DailyMed version of that label. DailyMed's record for that family begins
  after FDA's change.
- **Never present:** **3,344 (13.2%)** never appear in any captured version. About 40% of a checked
  sample of these differ only through SrLC's character corruption (flaw 5).
- **Consequence:**
  - In total, **30.5%** of SrLC phrases have no matching "text appears" event in DailyMed.
  - The oldest DailyMed version is not the original label. GitRx's "first observed" means first
    observed in DailyMed's captured history, never "first introduced".
- Source: [VALIDATION-REASONING.md section 3](VALIDATION-REASONING.md).

## 4. SrLC files some text under the wrong section

- **Wrong heading:** some SrLC rows are filed under one heading, e.g. "Boxed Warning", while the
  row's own text or subheading names a different section, e.g. "5.4" (Warnings and Precautions), a
  Medication Guide or Postmarketing Experience.
  - In the sealed GitRx test, this explains **28 of 30** Boxed Warning misses and **17 of 18**
    Contraindications misses.
  - Examples: XARELTO (5.4 filed as Boxed Warning), VENCLEXTA (5.1/6.1), ROXYBOND (8.2), KESIMPTA
    (Medication Guide), LORBRENA (5.2), JANUVIA (5.4), ZEPZELCA (5.1 filed as Contraindications).
- **Boxed warning with no box:** Boxed Warning rows exist for labels with no boxed warning, e.g.
  KYPROLIS and TRULICITY.
- **Internal disagreement:** the SrLC detail page and its own per-drug CSV disagree on row counts
  for 15 of 18,705 reconciled (drug, supplement, section) groups.
- **Consequence:** section-level counts from SrLC, such as "boxed warning changes per year", carry
  filing noise. That noise is concentrated in the small, high-stakes sections.
- **Caveat:** these are mechanical categorizations. No independent expert audit has confirmed that
  the label, rather than SrLC's heading, is right in every case.

## 5. SrLC corrupts characters

- **What happens:** non-ASCII characters (symbols and accented letters) appear as **"?"** in SrLC
  text: **549 of 173,383 underlined spans, in 426 rows**. The original character cannot be
  recovered from SrLC.
- **Consequence:** exact string matching between SrLC and the label fails for these rows. Tools
  need wildcard or normalized matching, and should report results both ways (GitRx does).

## 6. SrLC entries are posted late

- **What happens:**
  - Of 10,203 SrLC supplements, the "Database Updated" date is after the approval date in 10,111.
  - The median delay is 5 days, but **82 are more than 90 days late, 9 more than a year, and the
    maximum is 1,349 days**.
  - 33 show an update date *before* the approval date.
- **Consequence:** a study that pulls SrLC for a recent period will undercount, and entries can
  appear years later. Record the retrieval date.

## 7. Boxed warnings without the boxed-warning code

- **What happens:**
  - SPL marks a boxed warning with LOINC code 34066-1, but **410 versions in 137 label families**
    carry an obvious boxed warning in an uncoded section titled or starting "WARNING"/"BOXED WARNING".
  - Most are generic (ANDA) labels. These documents were accepted and published with the box uncoded.
- **Consequence:**
  - Any analysis that finds boxed warnings by code alone misses these.
  - GitRx assigns them to the boxed-warning section and flags each one (`inferred:uncoded_box`), so
    the inference is visible.

## 8. DailyMed dates are missing, out of order, and the date filter does not work

- **What happens:**
  - 99 versions have no posting date and 57 have no effective date.
  - In 7 families, posting dates run backwards against version numbers.
  - DailyMed's v2 listing service ignores its own `published_date_gte` filter and returns everything
    newest-first.
- **Consequence:**
  - Date-based timelines need explicit handling of missing and inverted dates.
  - Incremental download tools that trust the filter can silently miss updates or re-download
    everything.
  - GitRx records which date it used for each git-export commit and pages its weekly update past
    the filter.

## How GitRx handles these

- **Raw files:** every raw DailyMed download is kept byte for byte with its SHA-256, so anyone can
  check the source.
- **Dates:** FDA approval, label effective and DailyMed posting dates are kept separate. The SrLC
  comparison uses a stated window, and accuracy is reported with and without timing allowances.
- **Gaps:** non-consecutive version pairs are flagged, and missing versions are never invented.
- **Two accuracy measures:**
  - Accuracy where DailyMed has the change: 98.3% text, 99.6% section.
  - Accuracy across all of SrLC: 79.1% text, 94.0% section.
  - The gap between them is mostly flaws 1–6 above. See
    [ACCURACY-IN-PLAIN-LANGUAGE.md](ACCURACY-IN-PLAIN-LANGUAGE.md).

## What researchers should do

1. Name the date you use (approval, effective or posting) and expect the others to differ.
2. Treat absence in DailyMed as weak evidence. Check for non-consecutive versions and for history
   that starts late.
3. Do not use SrLC section headings as ground truth for boxed warnings or contraindications without
   reading the row text.
4. Match SrLC text with normalization for "?".
5. Record the retrieval dates of both sources; both change after the fact.

*Courtesy of the U.S. National Library of Medicine. Source: U.S. Food and Drug Administration, SrLC
database. Neither agency has reviewed or endorsed this analysis. See [LICENSING.md](../LICENSING.md).*
