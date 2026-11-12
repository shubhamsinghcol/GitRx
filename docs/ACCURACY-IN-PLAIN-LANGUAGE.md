# How accurate is GitRx? (plain language)

GitRx is checked against FDA's own public list of drug-safety label changes, the
Drug Safety-related Labeling Changes database (SrLC). Think of FDA's list as the answer key.
The test was run on a sealed sample of 983 drugs that was set aside before any tuning.
The full method, every number's source, and how to re-check it yourself are in
[VALIDATION-REASONING.md](VALIDATION-REASONING.md) and [../VERIFY.md](../VERIFY.md).

## The short version

| Question | Answer | Out of 100 |
|---|---|---|
| When FDA's new wording shows up in a DailyMed label version, does GitRx catch it in the right section? | Almost always | **98** (wording) / **99.6** (section) |
| Starting from FDA's whole list, does GitRx show the change in DailyMed around FDA's approval date? | Usually | **94** (section) / **79** (exact wording) |
| When GitRx reports a change in a safety section, is it also on FDA's list? | Mostly | **80**, or **87** allowing for timing |

## Why the second row is lower: DailyMed and FDA don't line up

The gap between "98" and "79" is mostly not GitRx missing things. It comes from comparing
two government sources that keep different records:

- **Different clocks.** FDA records the date it *approved* a change. The manufacturer posts the
  updated label to DailyMed on its own schedule, sometimes months before or after. GitRx only
  looks from a month before to a year after FDA's date, so changes posted outside that window are
  counted as misses.
- **Missing history.** DailyMed only has the label versions it received and kept. For about 3 in
  every 10 phrases FDA underlines, DailyMed has no version where that phrase newly appears:
  - about 13 in 100 never appear in any DailyMed version (often FDA's page shows the text
    differently, e.g. "?" in place of a symbol);
  - about 13 in 100 were already in the oldest version DailyMed has, meaning the change happened
    before DailyMed's history for that label begins;
  - about 5 in 100 first appear more than 2 years before, or more than 1 year after, FDA's date.
- **Errors in FDA's list.** Some FDA entries are filed under the wrong heading, e.g. text filed
  under "Boxed Warning" that the entry itself labels as section 5.4. GitRx finds that text in the
  section the label actually uses, and is marked wrong for it.

We set a goal of 95 out of 100 on the whole-list measure before testing. GitRx reached 94 for
sections and 79 for exact wording, so **that goal was not met**. We report this openly. The
"98 / 99.6" figures use a measure we adopted after seeing early results (before the sealed test
was run), which counts only changes DailyMed actually has.

## Why the third row is not 100

FDA's list covers only safety-related changes FDA chose to record, for brand-name (NDA/BLA)
drugs, from 2016 on. A change GitRx reports that is not on FDA's list is not automatically wrong.
Of the remainder, a large share is formatting (spacing, punctuation, capitalization), and the rest
is mostly real edits FDA does not list, or timing. This was measured on the 70% development sample.

## What comes next

A fresh test on FDA entries posted after 2026-11-11 is being collected (about 3–4 months). Both
measures and their pass marks will be fixed in writing before that test is graded.

Numbers: sealed Freeze 2 results (parser accuracy 98.30% text, 99.64% section; end-to-end 94.00%
section, 79.05% text); not-locatable fragments 3,344 + 3,169 + 1,203 of 25,315; precision 79.75%
crude / 86.56% timing-robust (open split). Sources: `docs/AUTHORITATIVE-PARSER-PLAN.md`,
`docs/VALIDATION-REASONING.md` sections 3–5.
