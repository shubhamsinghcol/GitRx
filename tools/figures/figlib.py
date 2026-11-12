"""Shared helpers for the GitRx research figures (deterministic, read-only on inputs)."""
import hashlib, json, os, re, sys, textwrap
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# Okabe-Ito colour-blind-safe palette
BLUE, ORANGE, GREEN, VERMILION, SKY, PURPLE, YELLOW, GREY = (
    "#0072B2", "#E69F00", "#009E73", "#D55E00", "#56B4E9", "#CC79A7", "#F0E442", "#7F7F7F")
INK = "#222222"

def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()

def style():
    plt.rcParams.update({
        "figure.facecolor": "white", "axes.facecolor": "white", "savefig.facecolor": "white",
        "font.family": "DejaVu Sans", "font.size": 10, "text.color": INK, "axes.labelcolor": INK,
        "xtick.color": INK, "ytick.color": INK, "axes.edgecolor": "#555555",
        "axes.spines.top": False, "axes.spines.right": False, "axes.grid": False,
        "svg.fonttype": "none", "svg.hashsalt": "gitrx-figures", "axes.titlesize": 11,
        "axes.titleweight": "bold", "axes.titlelocation": "left",
    })

def _flat(o, out):
    if isinstance(o, dict):
        for k, v in o.items():
            if k not in ("caption", "inputs", "caption_allow"):
                _flat(v, out)
    elif isinstance(o, (list, tuple)):
        for v in o:
            _flat(v, out)
    elif isinstance(o, bool):
        pass
    elif isinstance(o, (int, float)):
        out.append(o)

def check_caption(caption, data, allow=()):
    """Every number in the caption must equal a number in the plotted JSON (or be allow-listed)."""
    nums = []
    _flat(data, nums)
    # percentages and ratios derived as pct(x) are stored in the JSON, so exact lookup is enough
    bad = []
    for tok in re.findall(r"\d[\d,]*\.?\d*", caption):
        t = tok.rstrip(".,")
        if t in allow:
            continue
        v = float(t.replace(",", ""))
        dec = len(t.split(".")[1]) if "." in t else 0
        if not any(abs(round(n, dec) - v) < 1e-9 for n in nums):
            bad.append(tok)
    if bad:
        raise SystemExit("CAPTION/JSON MISMATCH: %s not in plotted numbers" % bad)

def save(fig, outdir, name, data, caption, allow=()):
    check_caption(caption, data, allow)
    data = dict(data)
    data["caption"] = caption
    fig.text(0.01, 0.01, textwrap.fill(caption, int(fig.get_figwidth() * 13)), ha="left", va="bottom", fontsize=8, color="#333333")
    fig.savefig(os.path.join(outdir, name + ".svg"), format="svg", metadata={"Date": None})
    fig.savefig(os.path.join(outdir, name + ".png"), format="png", dpi=160,
                metadata={"Software": None})
    plt.close(fig)
    with open(os.path.join(outdir, name + ".json"), "w") as f:
        json.dump(data, f, indent=1, sort_keys=True)
        f.write("\n")

def pct(a, b, nd=1):
    return round(100.0 * a / b, nd)
