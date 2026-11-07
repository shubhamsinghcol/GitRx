# Bounded native research helpers

Native 0.10.0 exposes these operations through the shared Query library, CLI and
MCP. Each requires an explicitly pinned configuration. Reads do not collect data,
build indexes or advance the selected release.

## Citation verification

`verify-citation SETID VERSION --snapshot SHA --raw-sha256 SHA [--ordinal N]
[--quote TEXT]` checks the selected retained raw identity, occurrence existence and
optional case-sensitive literal quote in extracted `own_text`. A release mismatch
is refused; a wrong hash, missing occurrence or absent quote returns `verified:
false`. This verifies evidence identity, not a clinical claim or live publisher
availability. Original citation fields and metadata remain in the response.

## Captured token provenance

`token-provenance SETID VERSION ORDINAL [--limit 100] [--offset 0]` replays
case-sensitive whitespace tokens in one conservatively paired occurrence lineage.
Equal token runs inherit the earlier captured origin; changed tokens begin a new
uninterrupted run. An identical token observed earlier in the replay is marked as
an observed return, separately from its current uninterrupted origin. Individual
word returns do not establish restoration of an entire phrase.

Numeric gaps, partial/unknown extraction, unmatched occurrences and interchangeable
repeated sections stop backward replay. Token alignment can be ambiguous for
repeated words; the response marks those tokens. This is a declared sequence
alignment policy, not validated authorship or proof of actual introduction. It
uses neither `git blame` over JSON nor clinical inference. Bounds: 10,000 captured
versions, 10,000 tokens per occurrence and 250,000 token-alignment product. An
unsupported alignment fails explicitly rather than returning guessed provenance.
Each page recomputes the same bounded lineage and carries origin/raw citations.

## Explicit publication-cutoff cohort

`inventory-on-date YYYY-MM-DD SETID ... [--limit 50] [--offset 0]` composes the
maintained publication-date selector for 1–200 explicit families. Alternatively,
`inventory-on-date YYYY-MM-DD --all-families` pages all registered captured
families; choose one scope explicitly. It preserves
unknown dates, inverted observations, extraction qualifications, inventory status
and selected citations. Follow `next_offset`. These are registered captured-family readings, not a historical market-wide
snapshot or clinical applicability date.

## Selective local installation

`installation-plan SETID ...` resolves selected families to the deterministic
first-hex shards. A Git reader also supplies pinned shard commits and local
installation state. SQLite-only plans cannot assert Git commit availability.
A shard includes other families too. Omitted shards remain unavailable scope.

```sh
python -m gitrx_backend.selective_install --config FILE \
  --setid SETID --output /new/selected-installation
```

The installer clones only selected local shards plus the pinned meta repository,
copies scope and optional hash-verified evidence metadata, then verifies selected
reads. It refuses an existing destination, uses no hardlinks and retains failed
installations with an `INCOMPLETE` marker. The resulting `backend.json` supports
Git reads. The family register still names omitted families; every response from a partial
installation names uninstalled shards, and accessing an absent shard fails explicitly. Broad searches crossing unavailable scope fail rather than
silently treating missing evidence as zero hits. Search acceleration is not copied.
There is no hosted download endpoint or cleared public redistribution in this
feature; remote artifact distribution still needs an owning repository/host.

## Reproducible descriptive figures

`tools/native_research_figures.py --config FILE --output /new/figures` verifies
native/metadata pins and emits PNG/SVG figures, caption and input JSON. It plots
captured-version counts per family and parser-status counts per version. These
have different denominators; neither establishes complete history or extraction
accuracy. Matplotlib is a figure-generation dependency, not a service dependency.
The input record pins the script, data identities and Matplotlib/Python versions.
