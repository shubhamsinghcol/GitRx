# GitRx: SrLC, fixed and expanded, in Git

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.24161295.svg)](https://doi.org/10.5281/zenodo.24161295)

FDA's [Drug Safety-related Labeling Changes (SrLC)](https://www.accessdata.fda.gov/scripts/cder/safetylabelingchanges/)
database tells you how a drug's safety labeling changed, but only for about 3,300 brand-name
drugs, only from 2016, and only in safety sections. It also has errors: text filed under the
wrong section, symbols corrupted to "?", and entries posted months or years late.

GitRx rebuilds that record directly from the label documents themselves, for every label in
[DailyMed](https://dailymed.nlm.nih.gov/):

- **Expanded:** 158,955 labels (brand, generic, OTC and more), 488,016 label versions, every
  section, as far back as DailyMed's history goes.
- **Fixed:** changes come from the label text itself, so SrLC's misfiled sections, corrupted
  characters and posting delays don't carry over. Checked against SrLC, GitRx finds FDA's
  wording in the right section about 98 times in 100.
- **In Git:** every label's history is a Git history, with one commit per label version. Clone it
  and run `git log`, `git diff` and `git grep` across the whole collection, or query the same data
  from the command line, SQLite or an AI assistant over MCP.

**Status:** first public release (parser `spl-sections-0.2.4`, November 2026). Code is here (archived on
Zenodo). The data and the Git export are being uploaded to Hugging Face; the link will appear here
when the upload finishes.

[Start here](docs/START-HERE.md) · [Verify the accuracy yourself](VERIFY.md) ·
[Limitations](LIMITATIONS.md) · [Flaws in DailyMed and SrLC](docs/SOURCE-DATA-ISSUES.md)

## How accurate is it, in plain terms?

Checked against FDA's own list of drug-safety label changes (SrLC): when FDA's new
wording appears in a DailyMed label version, GitRx catches it in the right section
about 98 times out of 100. Starting from FDA's whole list, GitRx shows the change in
DailyMed around FDA's approval date 94 times out of 100 by section and 79 by exact
wording; the gap is mostly because DailyMed and FDA keep different clocks and
DailyMed lacks some label versions. About 80 of every 100 changes GitRx reports are
on FDA's list (87 allowing for timing); the rest are mostly formatting edits and real
changes FDA's list doesn't record. This is a comparison with FDA's list on a sealed sample, not
independent clinical validation; a fresh test on new FDA entries is underway.

Details: [plain-language accuracy](docs/ACCURACY-IN-PLAIN-LANGUAGE.md) ·
[how each number was computed](docs/VALIDATION-REASONING.md) · [re-run the checks](VERIFY.md).

## Three ways to use it

| You want to… | Use |
|---|---|
| Analyse the whole collection (scripts, pandas, `git log`/`git grep`) | The Git export or the SQLite release files |
| Look up one drug's label history from a terminal | `./label-history` (JSON output) |
| Ask an AI assistant (Claude, etc.) questions about label history | The MCP server: [MCP.md](MCP.md) |

## Quick start

```sh
python3 -m venv .venv && .venv/bin/pip install -e .
export GITRX_DATA_ROOT=/path/to/downloaded/GitRx-data

./label-history resolve MOVIPREP                       # name -> label family (setid)
./label-history versions 8ff46193-2a3b-4de0-9572-775dda8cd8b2
./label-history diff 8ff46193-2a3b-4de0-9572-775dda8cd8b2 1 46 --code 43685-7
./label-history first-observed 8ff46193-2a3b-4de0-9572-775dda8cd8b2 seizures --code 43685-7
./label-history change-records 8ff46193-2a3b-4de0-9572-775dda8cd8b2 46
```

More commands and worked examples: [docs/CORE-WORKFLOW.md](docs/CORE-WORKFLOW.md).

## Read results correctly

- DailyMed dates (posting and label effective dates) are not FDA approval dates.
- "First observed" means first seen in DailyMed's captured versions, not when the text was
  first introduced. DailyMed's history has gaps and often starts late.
- Similar drug names are never merged; pick the label family (setid) you mean.
- GitRx reports text changes. It makes no clinical, legal or causal claims.

## License and credit

Code: Apache 2.0. GitRx-created data and docs: CC BY 4.0. DailyMed and FDA content is not
relicensed. Courtesy of the U.S. National Library of Medicine. See [LICENSING.md](LICENSING.md);
cite as DOI [10.5281/zenodo.24161295](https://doi.org/10.5281/zenodo.24161295) ([CITATION.cff](CITATION.cff)).

Created by Shubham Singh ([ORCID 0000-0002-0091-7391](https://orcid.org/0000-0002-0091-7391)).
Code written with AI coding assistants, OpenAI Codex and Anthropic Claude. The accuracy results
come from mechanical comparison with FDA's list, not AI grading.
