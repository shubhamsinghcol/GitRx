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

**Status:** first public release (parser `spl-sections-0.2.4`, November 2026). Code: this repository
(archived on Zenodo). Data and Git export: the Hugging Face dataset
[shubhams20/GitRx](https://huggingface.co/datasets/shubhams20/GitRx).

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

## Just exploring?

Download one Git repository (about 250 MB) and browse label histories with ordinary Git. No GitRx
code needed. Each label family is a folder holding `label.md` (the label as readable text) and
`meta.json`, and each label version is one commit.

```sh
pip install -U huggingface_hub
hf download shubhams20/GitRx --repo-type dataset --local-dir GitRx-data \
  --include "git-export/*/bundles/gitrx-labels-80-9f.bundle"
git clone GitRx-data/git-export/*/bundles/gitrx-labels-80-9f.bundle labels-80-9f && cd labels-80-9f
git log --date=short --format="%ad %s" -- 8ff46193-2a3b-4de0-9572-775dda8cd8b2    # MOVIPREP, every version
git log -p -G seizures -- 8ff46193-2a3b-4de0-9572-775dda8cd8b2                    # when "seizures" appeared
```

Labels are split across 8 repositories by the first two characters of their setid (`00-1f` ... `e0-ff`).

## What to download: build your own GitRx

Everything is on [Hugging Face](https://huggingface.co/datasets/shubhams20/GitRx) (about 217 GB in
total), but most people need only a small part.

**Step 1. Pick one.** Both contain every label and the same text.

| Option | What you get | Size |
|---|---|---|
| A. Plain Git | Label histories you browse with `git log`, `git diff`, `git grep` | ~250 MB per repository, 2.1 GB for all 8 |
| B. GitRx command line / MCP | Lookups, section-by-section diffs, "when did this wording first appear", JSON answers, AI-assistant access | code <10 MB + name index 26 MB, then ~50 MB per drug with `get`, or the whole release, 12 GB |

**Step 2. Add-on** *(optional, option B only).*

| Add-on | Why | Size |
|---|---|---|
| Fast-search pack | Search wording across all 158,955 labels in under a second instead of minutes (same results). Needs the whole release (`get --all`): it speeds up search but does not replace the release files. Also adds the download records used for formal citations. Skip it if you only look up specific drugs. | +3 GB |

The largest casual setup is therefore about 15 GB.

**Step 3. Research extras** *(only if you want to check GitRx's work yourself).*

| What | Why | Size |
|---|---|---|
| Original DailyMed files | Every label file exactly as DailyMed served it, to re-derive everything | 185 GB |
| Download log database | Every request and answer from DailyMed: dates, version lists | 9.5 GB |
| FDA SrLC files and grading runs | The FDA pages as fetched and every accuracy comparison | ~4 GB |

About 199 GB together. [VERIFY.md](VERIFY.md) explains how to use them.

## Quick start (option B)

```sh
git clone https://github.com/shubhamsinghcol/GitRx && cd GitRx
python3 -m venv .venv && .venv/bin/pip install -e .
export GITRX_DATA_ROOT=~/GitRx-data

./label-history get MOVIPREP              # lists the 2 MOVIPREP label families; pick one
./label-history get 8ff46193-2a3b-4de0-9572-775dda8cd8b2     # downloads ~50 MB, checks every checksum
./label-history versions 8ff46193-2a3b-4de0-9572-775dda8cd8b2
./label-history diff 8ff46193-2a3b-4de0-9572-775dda8cd8b2 1 46 --code 43685-7
./label-history first-observed 8ff46193-2a3b-4de0-9572-775dda8cd8b2 seizures --code 43685-7
./label-history change-records 8ff46193-2a3b-4de0-9572-775dda8cd8b2 46
```

`get --all` downloads the whole release (12 GB) and `get --search` the fast-search pack (3 GB).
`get` is the only command that uses the network. AI assistants: see [MCP.md](MCP.md). More
commands and worked examples: [docs/CORE-WORKFLOW.md](docs/CORE-WORKFLOW.md).

**Using the SQLite files directly?** They open in any SQLite tool. Label text and change wording
are stored compressed to save space (columns `texts.z`, `added_spans_z`, `removed_spans_z`,
`moved_spans_z`). In Python, `zlib.decompress(value).decode()` turns them back into text. The
command line and MCP server do this for you. Example:
[docs/CORE-WORKFLOW.md](docs/CORE-WORKFLOW.md#reading-the-sqlite-files-directly).

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
