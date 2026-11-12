# Structural issues in DailyMed and FDA SrLC (what researchers should know)

GitRx compares two public U.S. government sources: **DailyMed** (the label documents distributed
by the National Library of Medicine) and FDA's **Drug Safety-related Labeling Changes (SrLC)**
database. Building GitRx meant downloading every captured DailyMed label version (158,955 label
families, 488,016 versions) and crawling SrLC (3,278 drugs, 40,562 change rows). Doing that
exposed structural properties of both sources. These properties affect **any** study that uses
either source to answer questions like "when did this warning enter the label?"

This page lists the serious ones and gives counts, evidence and what each one means for research.
None of it says FDA or NLM is clinically wrong. These are properties of how the records are kept
and published. Each count is reproducible from the published GitRx data (see [VERIFY.md](../VERIFY.md)).
The fuller technical record is [VALIDATION-REASONING.md section 7](VALIDATION-REASONING.md) and
[STEP-F-GAP-REPAIR.md](STEP-F-GAP-REPAIR.md).

Snapshot: DailyMed as collected through November 2026; SrLC as crawled 2026-11-10/11.

## Summary

| # | Issue | Source | Scale | Main consequence |
|---|---|---|---|---|
| 1 | FDA approval dates and DailyMed posting dates are different clocks | both | median 5 days, but months to years in a minority | "When did the label change?" has no single answer |
| 2 | DailyMed version history is incomplete and has gaps that cannot be filled | DailyMed | 429,282 version numbers never listed; 0 of 1,200 recoverable | Absence of text in DailyMed is weak evidence |
| 3 | Many changes predate the oldest DailyMed version | DailyMed | 12.5% of SrLC phrases | The start of DailyMed history is not the start of the label |
| 4 | SrLC files some text under the wrong label section | SrLC | 28 of 30 Boxed Warning misses, 17 of 18 Contraindications misses (sealed sample) | Section-level SrLC statistics are noisy |
| 5 | SrLC replaces non-ASCII characters with "?" | SrLC | 549 underlined spans in 426 rows | Exact text matching against SrLC fails |
| 6 | SrLC is posted late, sometimes years late | SrLC | 82 supplements > 90 days late, max 1,349 days | SrLC is not complete for recent periods |
| 7 | SrLC coverage is narrow | SrLC | NDA/BLA only, 2016 on, safety sections only | No record for generics or older labels |
| 8 | Boxed warnings are not always coded as boxed warnings | DailyMed (SPL) | 410 versions, 137 label families | Code-based searches miss some boxed warnings |
| 9 | DailyMed date fields are easy to misread | DailyMed | 3.0% of versions posted > 1 year after the label's own date | Wrong date choice shifts timelines by months or years |
| 10 | Smaller DailyMed defects | DailyMed | see below | Edge cases in pipelines |

## 1. Two clocks: FDA approval versus DailyMed posting

- **What happens:**
  - SrLC records the date **FDA approved** a labeling supplement.
  - DailyMed shows the label once the **manufacturer submits** the new SPL file and DailyMed posts it.
  - The manufacturer controls when that happens.
- **How far apart:**
  - For most changes the gap is short.
  - Of the SrLC phrases that GitRx can locate in DailyMed history, **1,203 of 25,315** first appear more
    than 2 years before or more than 1 year after FDA's approval date.
- **Inside DailyMed itself:**
  - The label's own effective date and the DailyMed posting date also drift.
  - The median gap is 3 days.
  - **36,622 versions (7.5%) were posted more than 90 days after the label's effective date, and 14,646 (3.0%) more than
    a year after.**
  - 2,240 versions have an effective date *later* than their posting date (216 by more than 30 days).
  - Source: `tools/audit_dailymed_dates.py` → [evidence/dailymed-date-audit-0.2.4.json](evidence/dailymed-date-audit-0.2.4.json).
- **Consequence:**
  - "When did this warning enter the label?" has at least three defensible answers: FDA approval, label
    effective date and DailyMed posting.
  - They can be months to years apart.
  - Studies should say which one they use. Matching SrLC to DailyMed needs a time window, and any window
    loses some cases.
  - GitRx keeps all three dates separate and never calls a DailyMed date an FDA approval date.

## 2. DailyMed history is incomplete and its gaps are unrecoverable

- **What happens:**
  - SPL version numbers increase but skip.
  - Across all families, **429,282 version numbers are implied missing**:
    - 368,094 lie below the first captured version. 2,899 families start at version 100.
    - 61,188 fall inside gaps, in 21,015 families.
  - **None** of them is listed in DailyMed's own per-family history.
- **Recovery test:**
  - GitRx requested 1,200 sampled missing numbers directly.
  - **All 1,200 returned HTTP 404.** The 95% upper bound on what could exist is 0.32%.
  - In addition, 55 versions that DailyMed's current listing *does* show are not downloadable (HTTP 404, tried at least twice).
  - One family (NALTREXONE KIT, setid `6de5637f-...`) returns an empty history on every request.
- **Consequence:**
  - A change can happen in a version DailyMed never had or no longer has.
  - When two captured versions are not consecutive, a change "first seen" in the later one may have happened earlier.
  - GitRx flags these: **37,563 non-consecutive version pairs**.
  - "Not in DailyMed" is weak evidence that text was not in the label.
- Source: [STEP-F-GAP-REPAIR.md](STEP-F-GAP-REPAIR.md).

## 3. Much label history predates DailyMed's record

- **What happens:** for **3,169 of 25,315 (12.5%)** SrLC-underlined phrases, the phrase is already
  present in the oldest DailyMed version of that label. DailyMed's history for that family starts after FDA's change.
- **Also:** **3,344 (13.2%)** never appear in any captured version. About 40% of a checked sample of
  those differ only in SrLC's character rendering (issue 5). The rest are labels not on DailyMed,
  new approvals not yet posted, or SrLC text that differs from the label.
- **Consequence:**
  - In total, **30.5%** of SrLC phrases have no matching "text appears" event in DailyMed.
  - The oldest DailyMed version is not the original label. GitRx's "first observed" means first observed in
    DailyMed's captured history, never "first introduced".
- Source: [VALIDATION-REASONING.md section 3](VALIDATION-REASONING.md).

## 4. SrLC sometimes files text under the wrong section

- **What happens:**
  - Some SrLC rows are filed under a heading, e.g. "Boxed Warning", while the row's own text or subheading
    names a different section, e.g. "5.4" (Warnings and Precautions), a Medication Guide or Postmarketing Experience.
  - In the sealed GitRx test, this explains **28 of 30** Boxed Warning misses and **17 of 18** Contraindications misses.
  - Examples: XARELTO (5.4 filed as Boxed Warning), VENCLEXTA (5.1/6.1), ROXYBOND (8.2), KESIMPTA (Medication
    Guide), LORBRENA (5.2), JANUVIA (5.4), ZEPZELCA (5.1 filed as Contraindications).
  - Separately, Boxed Warning rows exist for labels without a boxed warning, e.g. KYPROLIS and TRULICITY.
- **Also:**
  - 541 rows sit under a heading "Other" that maps to no section.
  - The detail page and the per-drug CSV disagree on row counts for 15 of 18,705 reconciled (drug, supplement, section) groups.
- **Consequence:** section-level counts from SrLC, such as "how many boxed warning changes per year", carry
  filing noise. That noise is concentrated in the small, high-stakes sections.
- These are descriptive categorizations made mechanically. No independent expert audit has confirmed
  that the label, rather than SrLC's heading, is right in every case.

## 5. SrLC loses characters

- **What happens:**
  - Non-ASCII characters (symbols and accented letters) appear as **"?"** in SrLC text.
  - This affects **549 of 173,383 underlined spans (426 rows)**.
  - 28.7% of rows (11,637) also elide text with "…", and 163 rows refer to a table instead of quoting it.
- **Consequence:** exact string matching between SrLC and the label fails for these rows. Tools need
  wildcard or normalized matching, and should report results both ways (GitRx does).

## 6. SrLC is updated late

- **What happens:**
  - Of 10,203 SrLC supplements, the "Database Updated" date is after the approval date in 10,111.
  - The median delay is 5 days, but **82 are more than 90 days late, 9 more than a year, and the maximum is 1,349 days**.
  - 33 show an update date *before* approval.
- **Consequence:** a study that pulls SrLC for a recent period will undercount. Entries can appear years
  later. Record the retrieval date.

## 7. SrLC coverage is narrow

- **Scope:**
  - NDA and BLA products only (2,896 + 382 drugs); **no generic (ANDA) labels**.
  - Entries from 2016 (one supplement from 2014).
  - Safety-related sections only.
  - Only changes FDA chose to record.
  - SrLC also underlines formatting, renumbering and revision-date edits as changes.
- **In GitRx's matching:** 14–15% of SrLC entries have no DailyMed family that GitRx could match them to,
  and a further 8–10% have no DailyMed version in the time window.
- **Consequence:**
  - Most DailyMed labels have no SrLC record at all.
  - Precision against SrLC can't reach 100% even for a perfect tool: GitRx reports changes SrLC does not
    list, many of which are real.
  - SrLC is a useful answer key but not ground truth.

## 8. Boxed warnings without the boxed-warning code

- **What happens:**
  - SPL marks a boxed warning with LOINC code 34066-1.
  - **410 versions in 137 label families** carry an obvious boxed warning as an uncoded section titled or
    starting "WARNING"/"BOXED WARNING".
  - Most are generic (ANDA) labels.
- **Consequence:** any analysis that finds boxed warnings by code alone misses these. GitRx assigns
  them to the boxed warning section and flags each one (`inferred:uncoded_box`) so the inference is visible.

## 9. DailyMed dates are easy to misread

- **Date fields:**
  - DailyMed exposes several dates: the label document's own effective time, the posting date and the
    retrieval time.
  - None of them is FDA's approval date.
  - 99 versions have no posting date, and 57 no effective date.
  - In 7 families the posting dates run backwards relative to version numbers.
- **Listing filter:** DailyMed's v2 listing service ignores its own `published_date_gte` filter, so
  incremental download tools that rely on it can silently miss updates.
- **Consequence:** pick a date field deliberately and state it. GitRx stores each separately and the
  git export records which date it used for each commit.

## 10. Smaller DailyMed defects

- **Deep nesting:** 26 SPL documents are nested deeper than 256 XML levels and fail with standard XML
  parser defaults. They need special handling; GitRx parser 0.2.4 lifts only that limit.
- **Unannounced changes:** NLM states that it does not review SPL content before publication. It reserves the right
  to change its data formats.
- **Rate limits:** FDA's website blocks automated access at modest request rates (HTTP 403 after about 45
  requests at 12 per second), so reproducing an SrLC crawl must be slow.

## How GitRx handles these

- **Raw files:** it keeps every raw DailyMed download byte for byte, with its SHA-256, so anyone can check the source.
- **Dates:** it keeps FDA approval, label effective and DailyMed posting dates separate. The SrLC comparison
  uses a stated window, and the accuracy numbers are reported both with and without timing allowances.
- **Version gaps:** it flags non-consecutive version pairs and never invents missing versions.
- **Two accuracy measures:**
  - Accuracy where DailyMed has the change: 98.3% text, 99.6% section.
  - Accuracy across all of SrLC: 79.1% text, 94.0% section.
  - The gap between the two is mostly issues 1–7 above. See
    [ACCURACY-IN-PLAIN-LANGUAGE.md](ACCURACY-IN-PLAIN-LANGUAGE.md).
- **No guessing:** missing or unknown data stays unknown.

## What researchers should do

1. Name the date you use (approval, effective or posting) and expect the others to differ.
2. Treat absence in DailyMed as weak evidence. Check for non-consecutive versions and for history that starts late.
3. Do not use SrLC section headings as ground truth for boxed warnings or contraindications without reading the row text.
4. Match SrLC text with normalization for "?", elisions and formatting.
5. Record the retrieval dates of both sources; both change after the fact.

*Courtesy of the U.S. National Library of Medicine. Source: U.S. Food and Drug Administration, SrLC
database. Neither agency has reviewed or endorsed this analysis. See [LICENSING.md](../LICENSING.md).*
