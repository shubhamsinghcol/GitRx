"""Build the GitRx research figures (5) from pinned read-only inputs.

Usage: nice -n 10 .venv/bin/python -I tools/figures/make_figures.py [--data-root /Volumes/Untitled/GitRx] [--out docs/figures]
Reads: rebuild shards (versions, pairs, changes), srlc/gradebook.sqlite (search_hits), one open-split grading
summary, docs/VALIDATION-REASONING.md, docs/evidence/dailymed-date-audit-0.2.4.json. Opens everything read-only.
Never reads sealed grading directories. Fails if a caption number is not in the plotted JSON, or if a number
that is also published in the docs differs from the recomputed value.
"""
import argparse, hashlib, glob, json, os, re, sqlite3, statistics, sys
from collections import Counter, defaultdict
from datetime import date
sys.dont_write_bytecode = True
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import matplotlib.pyplot as plt
import figlib as L

ap = argparse.ArgumentParser()
ap.add_argument("--data-root", default=os.environ.get("GITRX_DATA_ROOT", "/Volumes/Untitled/GitRx"))
ap.add_argument("--out", default=None)
a = ap.parse_args()
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = a.out or os.path.join(REPO, "docs", "figures")
os.makedirs(OUT, exist_ok=True)
if not os.path.isdir(a.data_root):
    sys.exit("data root not mounted: " + a.data_root)
RB = os.path.join(a.data_root, "rebuild", "spl-sections-0.2.4__srlc-changes-0.3.2")
GB = os.path.join(a.data_root, "srlc", "gradebook.sqlite")
OPEN = os.path.join(a.data_root, "srlc", "grading", "open-spl-sections-0.2.3-srlc-changes-0.3.2-h0.3.2-either", "summary.json")
AUD = os.path.join(REPO, "docs", "evidence", "dailymed-date-audit-0.2.4.json")
VR = os.path.join(REPO, "docs", "VALIDATION-REASONING.md")
L.style()

def parse(s):
    if not s: return None
    s = s.replace("-", "")
    try: return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except Exception: return None

def need(cond, msg):
    if not cond: sys.exit("CHECK FAILED: " + msg)

# ---- pinned inputs -------------------------------------------------------
shards = sorted(glob.glob(RB + "/shards/*.sqlite"))
need(len(shards) == 256, "expected 256 shards")
shard_sha = {}
for f in shards:
    side = json.load(open(f[:-7] + ".done.json"))
    shard_sha[os.path.basename(f)] = side["sha256"]   # sidecar hash; full re-hash is tools/verify_gitrx.py
complete_sha = L.sha256_file(os.path.join(RB, "COMPLETE.json"))
inputs_common = {"rebuild_COMPLETE.json_sha256": complete_sha,
                 "rebuild_shard_sidecar_sha256_of_all_256": hashlib.sha256("".join(k + shard_sha[k] for k in sorted(shard_sha)).encode()).hexdigest(),
                 "shards_hash_method": "sha256 over concatenated 'NN.sqlite'+sidecar sha256 (NN.done.json), sorted"}

# ---- one sequential pass over shards ------------------------------------
lag_counter = Counter(); lag_list = []
fam_nver = {}; fam_first = {}
chg = Counter()   # (year, group) -> change records
n_versions = 0
SYN = "setid not like 'a0000000%' and setid not like '00000000-0000%'"
for f in shards:
    db = sqlite3.connect("file:%s?mode=ro&immutable=1" % f, uri=True)
    for setid, ver, pub, eff in db.execute("select setid,version,pub_date,effective_date from versions where %s" % SYN):
        n_versions += 1
        p, e = parse(pub), parse(eff)
        fam_nver[setid] = fam_nver.get(setid, 0) + 1
        if p and (setid not in fam_first or p < fam_first[setid]): fam_first[setid] = p
        if p and e:
            d = (p - e).days; lag_list.append(d)
    for yr, grp, n in db.execute(
        "select substr(p.to_pub_date,1,4), c.srlc_group, count(*) from changes c join pairs p "
        "on p.setid=c.setid and p.from_version=c.from_version and p.to_version=c.to_version "
        "where c.setid not like 'a0000000%' and c.setid not like '00000000-0000%' group by 1,2"):
        chg[(yr, grp or "other")] += n
    db.close()

# ---- Figure 1: posting minus effective date -------------------------------
aud = json.load(open(AUD))
lag_list.sort()
n = len(lag_list)
need(n == aud["pub_minus_eff_days"]["n"], "lag n differs from audit")
need(statistics.median(lag_list) == aud["pub_minus_eff_days"]["median"], "median differs")
need(sum(l > 90 for l in lag_list) == aud["pub_minus_eff_days"]["gt90"], "gt90 differs")
need(sum(l > 365 for l in lag_list) == aud["pub_minus_eff_days"]["gt365"], "gt365 differs")
need(sum(l < 0 for l in lag_list) == aud["eff_after_pub"], "negatives differ from audit")
need(sum(l < -30 for l in lag_list) == aud["eff_after_pub_30"], "neg>30 differs from audit")
bins1 = [("< -365", None, -366), ("-365 to -91", -365, -91), ("-90 to -31", -90, -31), ("-30 to -8", -30, -8),
         ("-7 to -1", -7, -1), ("0", 0, 0), ("1 to 7", 1, 7), ("8 to 30", 8, 30), ("31 to 90", 31, 90),
         ("91 to 365", 91, 365), ("> 365", 366, None)]
c1 = []
for lab, lo, hi in bins1:
    c1.append(sum(1 for l in lag_list if (lo is None or l >= lo) and (hi is None or l <= hi)))
need(sum(c1) == n, "bins do not sum")
d1 = {"unit": "label versions (version-weighted; one observation per DailyMed version with both dates)",
      "n_versions_with_both_dates": n, "bins": [b[0] for b in bins1], "counts": c1,
      "median_days": int(statistics.median(lag_list)), "min_days": lag_list[0], "max_days": lag_list[-1],
      "n_posted_before_effective": sum(l < 0 for l in lag_list),
      "n_posted_before_effective_by_over_30_days": sum(l < -30 for l in lag_list),
      "n_over_90_days": sum(l > 90 for l in lag_list), "pct_over_90_days": L.pct(sum(l > 90 for l in lag_list), n),
      "n_over_365_days": sum(l > 365 for l in lag_list), "pct_over_365_days": L.pct(sum(l > 365 for l in lag_list), n),
      "inputs": dict(inputs_common, audit_json_sha256=L.sha256_file(AUD))}
fig, ax = plt.subplots(figsize=(9, 6.4)); fig.subplots_adjust(bottom=0.30, top=0.9, left=0.09, right=0.98)
cols = [L.VERMILION if b[0].startswith("-") or b[0].startswith("<") else L.BLUE for b in bins1]
ax.bar(range(len(c1)), c1, color=cols, width=0.75); ax.set_yscale("log")
ax.set_xticks(range(len(c1))); ax.set_xticklabels([b[0] for b in bins1], rotation=30, ha="right")
for i, v in enumerate(c1): ax.text(i, v * 1.12, f"{v:,}", ha="center", fontsize=8)
ax.set_ylim(10, 2e6)
ax.set_xlabel("DailyMed posting date minus label effective date (days; log-spaced bins)"); ax.set_ylabel("label versions (log scale)")
ax.set_title("Labels are usually posted within days of their effective date, with a long tail either side")
cap1 = (f"Version-weighted, {n:,} DailyMed versions with both dates. Median {d1['median_days']} days; "
        f"{d1['n_over_90_days']:,} ({d1['pct_over_90_days']}%) posted more than 90 days after the effective date, "
        f"{d1['n_over_365_days']:,} ({d1['pct_over_365_days']}%) more than 365; {d1['n_posted_before_effective']:,} have an effective date after "
        f"posting (red, {d1['n_posted_before_effective_by_over_30_days']} by more than 30 days). This is an observed date difference between two "
        "recorded fields, not an FDA approval delay or a risk-recognition delay.")
L.save(fig, OUT, "fig1_posting_minus_effective", d1, cap1, allow=("30", "90", "365"))

# ---- Figure 2: coverage / cadence -----------------------------------------
fam_n = len(fam_nver)
vb = [("1", 1, 1), ("2", 2, 2), ("3", 3, 3), ("4-5", 4, 5), ("6-10", 6, 10), ("11-20", 11, 20), ("21-50", 21, 50), ("51+", 51, None)]
cv = [sum(1 for v in fam_nver.values() if lo <= v and (hi is None or v <= hi)) for _, lo, hi in vb]
need(sum(cv) == fam_n, "version bins")
years = sorted({d.year for d in fam_first.values()})
fy = Counter(d.year for d in fam_first.values())
d2 = {"unit_left": "label families (family-weighted)", "unit_right": "label families, by year of earliest DailyMed posting date among captured versions",
      "n_families": fam_n, "n_versions": n_versions, "versions_per_family_bins": [b[0] for b in vb], "families": cv,
      "median_versions_per_family": statistics.median(fam_nver.values()), "max_versions_per_family": max(fam_nver.values()),
      "families_with_one_version": cv[0], "pct_families_with_one_version": L.pct(cv[0], fam_n),
      "first_capture_years": years, "families_by_first_capture_year": [fy[y] for y in years],
      "families_first_captured_2023_or_later": sum(v for y, v in fy.items() if y >= 2023),
      "inputs": dict(inputs_common)}
fig, (ax, bx) = plt.subplots(1, 2, figsize=(11, 5.6), gridspec_kw={"width_ratios": [1, 1.25]})
fig.subplots_adjust(bottom=0.28, top=0.88, left=0.08, right=0.99, wspace=0.28)
ax.bar(range(len(cv)), cv, color=L.BLUE, width=0.75); ax.set_yscale("log")
ax.set_xticks(range(len(cv))); ax.set_xticklabels([b[0] for b in vb])
for i, v in enumerate(cv): ax.text(i, v * 1.12, f"{v:,}", ha="center", fontsize=8)
ax.set_ylim(10, 1e6); ax.set_xlabel("captured versions per label family"); ax.set_ylabel("label families (log scale)")
ax.set_title("Most families have few versions")
bx.bar(years, [fy[y] for y in years], color=L.GREEN, width=0.8)
bx.set_xlabel("year of earliest captured posting date"); bx.set_ylabel("label families"); bx.set_title("When each family first appears")
bx.set_xticks(years[::2]); bx.tick_params(axis="x", rotation=45)
cap2 = (f"Family-weighted: {fam_n:,} label families, {n_versions:,} captured versions (synthetic fixture families excluded). Median "
        f"{d2['median_versions_per_family']:g} versions per family; {cv[0]:,} families ({d2['pct_families_with_one_version']}%) have exactly one, the maximum is "
        f"{d2['max_versions_per_family']}. Right panel: year of the earliest captured posting date, which is when DailyMed history begins "
        "for that family, not when the label was first approved or introduced.")
L.save(fig, OUT, "fig2_coverage_cadence", d2, cap2)

# ---- Figure 3: why SrLC phrases are/are not locatable --------------------
txt = open(VR).read()
sealed = {"never present in any captured version": 3344, "present from the first captured version": 3169,
          "transition outside [-730 d, +365 d] of approval": 1203}
need(all(("{:,}".format(v)) in txt for v in sealed.values()) and "25,315" in txt and "7,716" in txt, "docs lack sealed decomposition numbers")
sealed_total = 25315; need(sum(sealed.values()) == 7716, "sealed sum")
op = json.load(open(OPEN))["parser_accuracy"]
nl = op["not_locatable_reasons"]
op_tot = op["main_fragments_in_parser_gradable_entries"]
open_vals = {"never present in any captured version": nl["never present in any captured version"],
             "present from the first captured version": nl["present from first captured version (no absent->present transition)"],
             "transition outside [-730 d, +365 d] of approval": nl["absent->present transitions only outside [approval-730 d, approval+365 d]"]}
open_loc = op["located"]
need(op_tot - sum(open_vals.values()) == open_loc, "open located mismatch")
rows3 = {"sealed split (numbers as published in VALIDATION-REASONING.md)": (sealed_total, sealed, sealed_total - 7716),
         "open split (grading summary.json)": (op_tot, open_vals, open_loc)}
d3 = {"unit": "SrLC-underlined main fragments (fragment-weighted; fragments cluster by drug and label, so they are not independent)",
      "splits": {}, "inputs": dict(srlc_open_summary_sha256=L.sha256_file(OPEN), validation_reasoning_md_sha256=L.sha256_file(VR))}
for k, (tot, rs, loc) in rows3.items():
    d3["splits"][k] = {"total_fragments": tot, "located": loc, "pct_located": L.pct(loc, tot),
                       "not_locatable": dict(rs), "pct_not_locatable": {r: L.pct(v, tot) for r, v in rs.items()},
                       "pct_not_locatable_total": L.pct(sum(rs.values()), tot)}
fig, ax = plt.subplots(figsize=(10.5, 5.4)); fig.subplots_adjust(bottom=0.40, top=0.86, left=0.30, right=0.97)
segs = [("located", L.BLUE)] + list(zip(sealed.keys(), [L.ORANGE, L.VERMILION, L.PURPLE]))
for yi, (k, (tot, rs, loc)) in enumerate(rows3.items()):
    left = 0
    for (lab, col) in segs:
        v = loc if lab == "located" else rs[lab]; w = 100 * v / tot
        ax.barh(yi, w, left=left, color=col, height=0.55, label=lab if yi == 0 else None)
        ax.text(left + w / 2, yi, f"{w:.1f}%", ha="center", va="center", fontsize=8, color="white" if col in (L.BLUE, L.VERMILION, L.PURPLE) else L.INK)
        left += w
ax.set_yticks(range(2)); ax.set_yticklabels([f"{k.split(' (')[0]}\n(n = {v[0]:,})" for k, v in rows3.items()])
ax.set_xlim(0, 100); ax.set_xlabel("% of SrLC-underlined main fragments"); ax.invert_yaxis()
ax.legend(loc="lower center", bbox_to_anchor=(0.45, -0.38), ncol=2, frameon=False, fontsize=8)
ax.set_title("Where SrLC phrases cannot be placed in captured DailyMed history")
s = d3["splits"][list(rows3)[0]]; o = d3["splits"][list(rows3)[1]]
cap3 = (f"Fragment-weighted. Sealed split (published figures): {s['not_locatable']['never present in any captured version']:,} never present, "
        f"{s['not_locatable']['present from the first captured version']:,} present from the first captured version, "
        f"{s['not_locatable']['transition outside [-730 d, +365 d] of approval']:,} with a transition outside the window, of {s['total_fragments']:,} "
        f"({s['pct_not_locatable_total']}% not locatable). Open split: {o['pct_not_locatable_total']}% of {o['total_fragments']:,}. "
        "Not-locatable fragments say nothing about parser accuracy. Locatability depends on where captured history starts and on SrLC text differing from the label.")
L.save(fig, OUT, "fig3_srlc_locatability", d3, cap3, allow=("365", "730"))

# ---- Figure 4: SrLC Database Updated minus approval date -----------------
g = sqlite3.connect("file:%s?mode=ro&immutable=1" % GB, uri=True)
lag4 = []
for ap_, up_ in g.execute("select suppl_date_iso, database_updated_iso from search_hits where database_updated_iso<>'' and suppl_date_iso<>''"):
    x, y = parse(ap_), parse(up_)
    if x and y: lag4.append((y - x).days)
g.close(); lag4.sort(); m = len(lag4)
bins4 = [("< 0", None, -1), ("0", 0, 0), ("1 to 7", 1, 7), ("8 to 30", 8, 30), ("31 to 90", 31, 90), ("91 to 365", 91, 365), ("> 365", 366, None)]
c4 = [sum(1 for l in lag4 if (lo is None or l >= lo) and (hi is None or l <= hi)) for _, lo, hi in bins4]
need(sum(c4) == m, "fig4 bins")
# published in SOURCE-DATA-ISSUES.md section 6
need(m == 10203 and sum(l > 0 for l in lag4) == 10111 and sum(l < 0 for l in lag4) == 33 and statistics.median(lag4) == 5
     and sum(l > 90 for l in lag4) == 82 and sum(l > 365 for l in lag4) == 9 and lag4[-1] == 1349, "SrLC lag differs from published")
d4 = {"unit": "SrLC supplements with an approval date and a Database Updated date (supplement-weighted; all drugs, not split-specific; this is a source property, not a grading result)",
      "n_supplements": m, "bins": [b[0] for b in bins4], "counts": c4, "median_days": int(statistics.median(lag4)),
      "n_updated_before_approval": sum(l < 0 for l in lag4), "n_over_90_days": sum(l > 90 for l in lag4),
      "n_over_365_days": sum(l > 365 for l in lag4), "max_days": lag4[-1],
      "inputs": {"gradebook_sqlite_sha256": L.sha256_file(GB), "table": "search_hits (suppl_date_iso, database_updated_iso)"}}
fig, ax = plt.subplots(figsize=(8.5, 6.0)); fig.subplots_adjust(bottom=0.33, top=0.9, left=0.1, right=0.98)
ax.bar(range(len(c4)), c4, color=[L.VERMILION] + [L.BLUE] * 6, width=0.75); ax.set_yscale("log")
ax.set_xticks(range(len(c4))); ax.set_xticklabels([b[0] for b in bins4])
for i, v in enumerate(c4): ax.text(i, v * 1.12, f"{v:,}", ha="center", fontsize=8)
ax.set_ylim(1, 2e4)
ax.set_xlabel("SrLC 'Database Updated' minus supplement approval date (days)"); ax.set_ylabel("SrLC supplements (log scale)")
ax.set_title("SrLC entries usually appear within days, but some years late")
cap4 = (f"Supplement-weighted, {m:,} SrLC supplements with both dates. Median {d4['median_days']} days; {d4['n_over_90_days']} more than 90 days, "
        f"{d4['n_over_365_days']} more than a year, maximum {d4['max_days']:,}; {d4['n_updated_before_approval']} show an update date before the approval date (red). "
        "This is the gap between two SrLC fields, not an FDA approval delay or a risk-recognition delay; it shows only that SrLC is incomplete for recent periods.")
L.save(fig, OUT, "fig4_srlc_database_updated_lag", d4, cap4, allow=("90",))

# ---- Figure 5: change records per year by section group ---------------------
GN = {"BW": "Boxed Warning", "C": "Contraindications", "WP": "Warnings and Precautions", "AR": "Adverse Reactions",
      "DI": "Drug Interactions", "USP": "Use in Specific Populations", "MG": "Patient information / Medication Guide",
      "other": "Other sections (outside SrLC groups)"}
order = ["WP", "AR", "BW", "C", "DI", "USP", "MG", "other"]
cols5 = [L.BLUE, L.ORANGE, L.VERMILION, L.GREEN, L.SKY, L.PURPLE, L.YELLOW, L.GREY]
yrs = sorted({y for (y, _) in chg if y and y.isdigit() and 2006 <= int(y) <= 2026})
tot_all = sum(chg.values()); in_range = sum(v for (y, _), v in chg.items() if y in yrs)
by = {g_: [chg[(y, g_)] for y in yrs] for g_ in order}
d5 = {"unit": "change records (record-weighted: one record per changed section between consecutive captured versions; a version pair can have many)",
      "years_by_to_version_posting_date": [int(y) for y in yrs], "groups": {GN[g_]: by[g_] for g_ in order},
      "total_change_records_all_years": tot_all, "total_change_records_plotted": in_range,
      "records_outside_plotted_years": tot_all - in_range,
      "records_by_group_total": {GN[g_]: sum(by[g_]) for g_ in order},
      "inputs": dict(inputs_common)}
fig, ax = plt.subplots(figsize=(10.5, 6.2)); fig.subplots_adjust(bottom=0.27, top=0.9, left=0.09, right=0.98)
bot = [0] * len(yrs)
for g_, c_ in zip(order, cols5):
    ax.bar([int(y) for y in yrs], by[g_], bottom=bot, color=c_, width=0.8, label=GN[g_], edgecolor="white", linewidth=0.3)
    bot = [b + v for b, v in zip(bot, by[g_])]
ax.set_xlabel("year of the later version's DailyMed posting date"); ax.set_ylabel("change records")
ax.set_xticks([int(y) for y in yrs][::2]); ax.legend(ncol=2, frameon=False, fontsize=8, loc="upper left")
ax.set_title("Detected label changes per year, by section group")
ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{int(v):,}"))
cap5 = (f"Record-weighted: {in_range:,} change records between consecutive-or-gapped captured versions, plotted by the posting year of the later version "
        f"({d5['records_outside_plotted_years']:,} outside the plotted years or undated are omitted). Counts reflect what DailyMed history captured and "
        "what the parser found; early years are thin because captured history starts late, so a rise over time is not a rise in actual label changes.")
L.save(fig, OUT, "fig5_changes_per_year_by_group", d5, cap5)
print("ok", OUT)
