# Evidence details and wording histories

## What each answer tells you

Besides text, queries report, for each version: how well it was extracted (status and diagnostics), where
the label file was obtained (a direct version download, a member of a DailyMed bulk archive, or a current-only
source), and whether the family's inventory is known to be complete. When this information is not available the
answer says "unknown" instead of assuming completeness. A parse marked `complete` still does not guarantee perfect table
or layout fidelity, or that DailyMed's history for the label is complete.

A link to a bulk archive means the label came from inside that archive; it does not claim a direct download of that one file.

## Trace a phrase through a family

```sh
./label-history phrase-history SETID "seizures" --limit 20
./label-history coverage SETID
```

Each captured version is reported as one of:

- a located match;
- no match in the extracted text;
- unknown, because the extraction is partial or missing.

Reading the result:

- A match counts even in a partly extracted version. A non-match in a partly extracted version does not show the
  original lacked the wording.
- Source codes can repeat, so a match is "somewhere among the selected sections", not a continuous section lineage.
- Missing versions break continuity. Wording present in the earliest captured version is "present at the start of the record".
  A match after a non-match is an observation, not a proven addition date, and removal or reintroduction cannot be
  stated exactly across gaps.
- Dates are DailyMed posting dates and label effective dates, shown separately.
- Matching is case-insensitive by default, with no synonyms.

To continue, pass `--cursor TOKEN` with the same phrase and filters. A page shows at most 20 matching sections per version
and says when more were omitted. Page results are fragments, not calendar periods. Exhausting a page is not a complete
scan while a continuation remains. Long scans are subject to the time limit; raise `--timeout-seconds` if needed.
