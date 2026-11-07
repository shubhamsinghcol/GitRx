# Current limitations

- **Scope:** the expanded acquisition covers 7,239 captured source-category
  NDA/BLA families and 51,357 known version targets, not every historical US
  drug label. Current-listing discovery does not establish archived-only coverage.
- **Recovery:** all 15 staged targets are admitted with exact identities.
  NALTREXONE's upstream history inventory remains unresolved; recovered v1
  does not prove that no other historical versions exist.
- **Meaning of dates:** DailyMed publication observations, XML effectiveTime
  and retrieval timestamps are distinct. None alone establishes FDA approval,
  legal applicability, what shipped, risk-discovery time or causation.
- **Family identity:** a setid is a document lineage. Similar names do not
  authorize merging brands, ingredients, labelers or applications.
- **Extraction:** sections, tables and source references are retained with
  diagnostics; no OCR. Partial parses and successful text retention checks
  do not establish perfect visual/table fidelity or medical meaning.
- **Interpretation:** source codes can repeat or be miscoded. Context rules
  are derived; default/unknown instances cannot support confident named-section
  headlines. Anchored status is evidence found by a rule, not a probability.
- **Validation:** the original fresh sample had 59/60 agreement, with one IFU
  human label later corrected by the owner under the existing taxonomy.
  Adjudicated agreement is 60/60, not a second blind pass. This anchored-only
  tranche1 sample cannot establish global recall or release1-wide accuracy.
- **Queries:** `label-history` and native MCP serve the expanded captured release
  through the shared query library. Source-code occurrences remain unclassified;
  literal searches do not infer concepts or synonyms. Context-aware
  `gitrx-current` / pilot MCP remains the accepted interpreted pilot. Expanded
  native coverage does not establish corpus-wide interpretation accuracy.
- **History and bounds:** first-observed wording is not a proven addition date.
  Integer holes are not all known upstream inventory targets. Unfinished scans,
  partial extraction and missing dates limit negative conclusions. Respect
  pagination/continuation and [execution limits](docs/QUERY-PERFORMANCE.md).
- **Diffs:** native `compare` uses conservative v2 pairing and keeps ambiguous
  repeats unmatched. Token equality is not clinical or layout equivalence;
  an unmatched occurrence is not proof of an actual source addition/removal.
  The interpreted pilot and historical audit artifacts retain their own scope.
- **Browser:** exact references point to a retained local release and require
  its archive/service to reopen. They are not public permanently hosted URLs.
- **Freshness:** the selected release is a captured snapshot. Explicit refresh
  and promotion commands exist; queries do not refresh sources and no recurring
  scheduler is installed. Checking one family does not refresh the corpus.
- **Git:** an expanded release has new IDs under a new immutable tag. Old refs
  remain valid. Reproducibility from pinned inputs is not append-only chronology
  or a claim that fresh network observations reproduce historical bytes.
- **Distribution:** local engineering artifacts are not a public release.
  Licensing, third-party rights, hosting and publication remain owner decisions.
