# Source register

This register describes **captured project sources**, not a fresh survey of
their live behavior. The November 6 admission/export work used retained ZIPs;
one separate metadata-only history recheck reconfirmed the NALTREXONE anomaly.
Exact URLs, response times, statuses, headers, hashes
and original bodies are retained in source stores and evidence ledgers.

| Source | Role | Important boundary |
|---|---|---|
| DailyMed `/services/v2/spls.json` listings | Family discovery and category/listing targets | Current listing is not a complete archived-only universe |
| DailyMed `/services/v2/spls/{setid}/history.json` | Captured version/publication observations | Multiple dates, pagination changes and listing/history disagreements are retained |
| DailyMed `getFile.cfm?type=zip&setid=…&version=…` | Ordinary exact-version ZIP route | Internal XML identity is mandatory; a URL/HTTP200 alone is insufficient |
| DailyMed `getArchivalFile.cfm?archive_id=…` | Captured archive-ID fallback | Its distinct route is recorded; archive membership is not market status |
| DailyMed published bulk/update files | Alternative original SPL delivery | Member identity, enclosing archive, range-response provenance and source hashes stay linked |
| Captured openFDA set-ID responses | Independent metadata cross-check during recovery | JSON was not substituted for exact SPL XML/ZIP bytes |
| FDA section-code vocabulary capture | Code-registry provenance | A code is not proof of context or source coding correctness |

The 15 recovered targets came from:

[DailyMed May 2026 monthly update](https://dailymed-data.nlm.nih.gov/public-release-files/dm_spl_monthly_update_may2026.zip).
The retained [candidate manifest](reports/recovery-attempt-20261103/VERIFIED-CANDIDATES.json)
names each nested ZIP and XML member. The [range-response ledger](reports/recovery-attempt-20261103/evidence/requests.jsonl)
records actual HTTP206 captures. The supplemental database preserves these
separately from the offline-extracted member; no direct member HTTP response
or publication date is invented.

Fourteen targets were listing-only at the captured history endpoints. The
NALTREXONE v1 recovery establishes that version's bytes, not a complete family
history. Original upstream failures and empty-history responses remain evidence.

The [November 6 metadata recheck](reports/continuation-20261106/naltrexone-history-recheck/CORRECTED-INTERPRETATION.json)
returned HTTP200 with a blank source setid and zero history rows. It was not
accepted as a complete inventory and admitted no additional document.

Future FDA SrLC comparison is a planned independent research input, not a
source already reconciled into this release. RxNorm identity merging,
FDA-approval reconciliation, OTC and non-US sources are outside this build.

## Attribution and distribution

The [November 1 licensing-source review](reports/block3b/LICENSING-SUMMARY.md)
retains official NLM/DailyMed terms and unresolved third-party-rights questions.
It is a dated research record. This project has not selected a public data
license or concluded that every labeler-submitted text/image may be redistributed.
Public packaging needs the applicable attribution, non-endorsement and
historical-snapshot notices plus the owner's distribution decision.
