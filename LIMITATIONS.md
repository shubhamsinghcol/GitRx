# Current limitations

- **Default release:** the Step E rebuild (158,955 families / 488,016 versions;
  56 versions have no retained raw and no text) reads from an external SSD; if
  it is absent queries fail rather than fall back. Parser/section scores
  (Freeze 2 sealed: text 98.30%, section 99.64%, uncoded-box rule "seen") are
  not independent validation; the independent holdout is pending. Change
  records are mechanical text comparisons with rule-derived groups, not clinical
  or FDA-approval statements. Family titles are latest listing titles (selection
  aids). 
- **Scope:** the release covers what the evidence inventory holds (158,955 families,
  488,016 captured versions), not every historical US drug label. Current-listing
  discovery does not establish archived-only coverage; upstream history beyond the
  inventory is unknown. The earlier 7,239-family release was deleted on 2026-11-11
  (fingerprints retained).
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
- **Validation — corrected 2026-11-10:** the 59/60 and 60/60 results below were
  AI-graded, not human-reviewed; the parser currently has no human validation.
  See [validation correction](docs/VALIDATION-CORRECTION-2026-11-10.md).
  Original text retained: the original fresh sample had 59/60 agreement, with one IFU
  human label later corrected by the owner under the existing taxonomy.
  Adjudicated agreement is 60/60, not a second blind pass. This anchored-only
  tranche1 sample cannot establish global recall or release1-wide accuracy.
- **Queries:** `label-history` and native MCP serve the release through the shared query library.
  Source-code occurrences remain unclassified; literal searches do not infer concepts or synonyms.
- **History and bounds:** first-observed wording is not a proven addition date.
  Integer holes are not all known upstream inventory targets. Unfinished scans,
  partial extraction and missing dates limit negative conclusions. Respect
  pagination/continuation and [execution limits](docs/QUERY-PERFORMANCE.md).
- **Diffs:** native `compare` uses conservative v2 pairing and keeps ambiguous
  repeats unmatched. Token equality is not clinical or layout equivalence;
  an unmatched occurrence is not proof of an actual source addition/removal.
  
- **Freshness:** the selected release is a captured snapshot. Weekly DailyMed and monthly SrLC collection
  jobs ([ops/UPDATES.md](ops/UPDATES.md)) only collect; new data appears in a release only after an explicit
  rebuild. Queries never refresh sources. Checking one family does not refresh the corpus.
- **Git:** an expanded release has new IDs under a new immutable tag. Old refs
  remain valid. Reproducibility from pinned inputs is not append-only chronology
  or a claim that fresh network observations reproduce historical bytes.
- **Distribution:** publication is pending. Code is Apache-2.0, GitRx-created data and docs CC BY 4.0, and DailyMed/FDA
  content is not relicensed ([LICENSING.md](LICENSING.md)). Researcher evidence on Hugging Face with a Zenodo DOI is planned, not yet published.
