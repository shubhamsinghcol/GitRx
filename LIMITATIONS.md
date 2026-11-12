# Limitations

Read these before drawing conclusions from GitRx output.

## What the data is

- **Release:** `spl-sections-0.2.4`, a snapshot of 158,955 DailyMed label families and 488,016
  versions. 56 versions have no retained file and so have no text. It is not every historical
  US drug label, and DailyMed's own history for a label can be incomplete or start late.
- **Snapshot, not live.** Queries never fetch anything. New DailyMed versions appear only after
  a new release is built.
- **Family names are only a way to find a label.** A family is one DailyMed setid. Similar names do
  not mean the same product, ingredient, labeler or application; pick the setid you mean.
  Family titles are the latest listing titles.

## What dates mean

DailyMed posting dates, label effective dates and the time we retrieved a file are three
different things. None is an FDA approval date, the date a product shipped, or when a risk was
discovered. "First observed" is the first captured version containing the text, not when the text
was introduced.

## What absence means

A phrase not found is weak evidence it was absent. Versions can be missing (see `coverage`),
extraction can be partial (no OCR; tables and layout can be imperfect), and a search that did not
finish says nothing about what it did not reach. Follow continuation until a search reports it is
complete.

## What the comparison and change records mean

- Changes are mechanical text comparisons. They are not clinical or FDA statements, and equal text
  is not proof of equal meaning or layout.
- Section codes in labels can repeat or be wrong. GitRx reports each coded section as found and
  does not decide which product section it belongs to. Repeated sections that cannot be paired
  are shown as unmatched; unmatched is not proof of an addition or removal.
- Search is literal text matching; it does not know synonyms.
- Nothing here is clinical advice.

## Accuracy

Checked against FDA's SrLC list on a sealed sample, with the results and caveats in
[plain language](docs/ACCURACY-IN-PLAIN-LANGUAGE.md). The parser has no human validation: the
earlier 59/60 and 60/60 scores were graded by AI and are not evidence
([correction](docs/VALIDATION-CORRECTION-2026-11-10.md)). A fresh test on new FDA entries is
being collected. Known flaws in the source data are in [SOURCE-DATA-ISSUES](docs/SOURCE-DATA-ISSUES.md).

## Git export

An expanded release gets a new export; old ones stay valid. The export is rebuilt from pinned
inputs, so it reproduces the same history, but it is not a record of when things happened.

## Licensing

Code is Apache-2.0; GitRx-created data and docs are CC BY 4.0. DailyMed and FDA content is not
relicensed by GitRx ([LICENSING.md](LICENSING.md)).
