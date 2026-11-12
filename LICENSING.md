# Licensing

GitRx has two kinds of material under two licenses, plus third-party source content that is not ours to license.

| Material | License |
|---|---|
| Source code: CLI, MCP server, parser, change records, grading and verification tools, tests | [Apache License 2.0](LICENSE). Keep the copyright and [NOTICE](NOTICE) when redistributing. |
| GitRx-created data and documentation: the rebuild (parsed sections and change records), the label-history Git export, grading outputs, precision analyses, gradebook structure, evidence/provenance records, and docs | [Creative Commons Attribution 4.0 International (CC BY 4.0)](LICENSE-DATA-CC-BY-4.0.txt). Attribute as shown in [CITATION.cff](CITATION.cff). |
| FDA Drug Safety-related Labeling Changes (SrLC) pages and their text | U.S. Government work, public domain in the United States. Credit: U.S. Food and Drug Administration. |
| DailyMed SPL label documents and label text (submitted by manufacturers, distributed by the U.S. National Library of Medicine) | Not licensed by GitRx. Redistributed as provided by NLM DailyMed, subject to NLM's terms and any third-party rights. Credit: U.S. National Library of Medicine, DailyMed. |

The CC BY 4.0 license applies only to what GitRx created (structure, annotations, derived records, analyses, documentation). It does not grant rights in manufacturer label text or other third-party content that appears inside those records.

Suggested attribution: "GitRx (Shubham Singh, 2026), licensed under CC BY 4.0", with the DOI once published.

## Source attribution and disclaimers (required wherever GitRx data is redistributed)

- **DailyMed:** "Courtesy of the U.S. National Library of Medicine." (wording required by NLM's
  [data download terms](https://www.nlm.nih.gov/databases/download/terms_and_conditions.html)),
  with the source URL https://dailymed.nlm.nih.gov/ and the retrieval dates of the snapshot.
- **SrLC:** "Source: U.S. Food and Drug Administration, Drug Safety-related Labeling Changes
  (SrLC) database", with the retrieval date (FDA recommends dating copied material).
- **Snapshot, not current:** every GitRx release is a fixed snapshot and does not reflect the
  most current data available from NLM or FDA. Use DailyMed and FDA for current labeling.
- **No endorsement:** NLM, NIH, HHS and FDA have not reviewed or endorsed GitRx. No agency
  names or logos are used as marks.
- **No warranty:** the data are provided as-is; NLM and FDA make no warranty and assume no
  liability for errors in the source data, and neither does GitRx (see LICENSE and the CC BY 4.0
  disclaimer).
- **Label content:** NLM does not review SPL content before publication; DailyMed "in use"
  labeling may differ from FDA-approved labeling. Labels may contain manufacturer-owned
  material (including images) and may be subject to non-US copyright; GitRx does not license it.
- **Changes from the source:** parsed sections, normalization and change records are GitRx
  transformations of the source documents; raw files are kept byte-for-byte as downloaded.
- **Not medical advice.**
