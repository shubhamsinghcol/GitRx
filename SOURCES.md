# Where the data comes from

GitRx is built from public sources. Original responses and files are kept so every result can be traced back.

| Source | Used for | Caveat |
|---|---|---|
| DailyMed listing (`/services/v2/spls.json`) | Finding label families | The current listing does not include labels that exist only in the archive |
| DailyMed history (`/services/v2/spls/{setid}/history.json`) | Which versions exist and when they were posted | Dates can disagree between listing and history; all are kept |
| DailyMed version download (`getFile.cfm?type=zip`) | The label file (SPL) for an exact version | The XML must identify the expected setid and version; a successful download alone is not enough |
| DailyMed archive download (`getArchivalFile.cfm`) | Fallback for versions the normal route lacks | Being in the archive says nothing about market status |
| DailyMed bulk/monthly update files | Another source of original label files | Each file stays linked to the archive it came from |
| openFDA | Cross-checking metadata during recovery of missing versions | Never used in place of the label file itself |
| FDA section-code list | Names for section codes | A code does not prove which section a passage belongs to |
| FDA Drug Safety-related Labeling Changes (SrLC) | The answer key for accuracy checks | Incomplete and sometimes inconsistent; see [SOURCE-DATA-ISSUES](docs/SOURCE-DATA-ISSUES.md) |

Fifteen labels that had no direct download were recovered from the
[DailyMed May 2026 monthly update](https://dailymed-data.nlm.nih.gov/public-release-files/dm_spl_monthly_update_may2026.zip);
the retained [candidate list](reports/recovery-attempt-20261103/VERIFIED-CANDIDATES.json) and
[request ledger](reports/recovery-attempt-20261103/evidence/requests.jsonl) show how. Fourteen of
them appear only in the listing, not in the history endpoint, so a family can have a recovered version without a full history.

Out of scope: RxNorm ingredient merging, reconciliation with FDA approvals, OTC-only sources and non-US labels.

## Attribution

Label text is DailyMed/NLM content and is not relicensed by GitRx. Terms and open rights questions are in
[LICENSING.md](LICENSING.md) and the dated [licensing review](reports/block3b/LICENSING-SUMMARY.md).
Labeler-submitted text and images may carry their own rights.
