# Research figures

Each figure has a PNG, an SVG, and a JSON holding the exact plotted numbers, the caption, and the sha256 of every
input. The script stops if a number in a caption is not in the JSON, or if a recomputed number differs from one
already published in the docs. An observed date difference is not an approval delay or a risk-recognition delay.

| Figure | What it shows (weighting) |
|---|---|
| `fig1_posting_minus_effective` | DailyMed posting date minus label effective date; median 3 days, 7.5% over 90 days, 2,240 negative (per version) |
| `fig2_coverage_cadence` | Versions per label family and families by year of first captured posting date (per family) |
| `fig3_srlc_locatability` | Why SrLC phrases can or cannot be placed in captured history: 3,344 / 3,169 / 1,203 of 25,315 (per fragment; sealed numbers as published, plus the open split) |
| `fig4_srlc_database_updated_lag` | SrLC "Database Updated" minus approval date; median 5 days, 82 over 90 days (per supplement) |
| `fig5_changes_per_year_by_group` | Change records per year by section group (per change record) |

Regenerate (needs the data SSD mounted; read-only, about 2 minutes):

```sh
nice -n 10 .venv/bin/python -I tools/figures/make_figures.py          # writes docs/figures/
```

Needs `matplotlib` in the project venv. Family counts here (158,953) exclude two synthetic fixture families
counted elsewhere (158,955). Shard inputs are pinned by their recorded sidecar hashes; `tools/verify_gitrx.py`
re-hashes the shards themselves.
