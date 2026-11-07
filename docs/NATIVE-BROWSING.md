# Browse the expanded source archive

`release1-query` reads the new **7,239-family / 51,357-version** native snapshot.
It uses DailyMed's explicit section occurrences without running the context
classifier or requiring manual labels for each document. It is separate from
the accepted pilot's interpreted `gitrx-current`/MCP tools.

From the GitRx folder:

```sh
./release1-query families --search naltrexone
./release1-query versions 6de5637f-d527-4228-af1a-3aae71155cb5
./release1-query show 6de5637f-d527-4228-af1a-3aae71155cb5 1 --max-chars 8000
```

Family names select a setid; similar names never merge histories. Each
version view carries the expanded snapshot, exact raw hash, distinct source
publication/effective dates and native section ordinals/ancestry.

To inspect source-coded occurrences or one exact occurrence:

```sh
./release1-query show SETID VERSION --code 34066-1
./release1-query show SETID VERSION --ordinal 42 --max-chars 8000
./release1-query show SETID VERSION --ordinal 42 --max-chars 8000 --offset 8000
```

Replace `SETID` and `VERSION` with the chosen values. `--code` means exactly
that **source code**, not a context-validated product boxed-warning claim.
All repeated occurrences are kept. `--ordinal` selects one native occurrence;
text pagination reports truncation and the next offset. The default text
budget is 16,000 characters, maximum 200,000. No OCR, synonyms, inferred
absence/addition dates, legal applicability or medical interpretation.

The metadata database and prepared payloads are derived and hash checked.
Reads never fetch or ingest. The metadata index is built with:

```sh
./release1-query build-index
```

It uses pinned selected manifests, the scope title register and the verified
payload cache. An existing mismatched/tampered index is refused rather than
silently replaced. Originals and prior release histories remain intact.

For a quick overview, open [the searchable coverage report](../reports/continuation-20261106/coverage.html).
The higher-level section/phrase-history MCP remains scoped to the accepted
pilot until its broader interpretation build is separately pinned and tested.

A [November 6 backend experiment](../reports/query-backend-comparison-20261106/REPORT.md)
compares this metadata/payload route with pinned Git batch reads. It does not
change `release1-query` or extend interpreted query scope.
