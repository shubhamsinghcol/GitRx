"""SrLC-style change records between two parsed SPL versions (parser spl-sections-0.2.0 output).

compare_docs(a_doc, b_doc) -> canonical change record dict (use canonical_json for bytes).
Pure and deterministic: no I/O, no clock, no randomness.

ALIGNMENT (sections of A matched to sections of B; each pass only sees still-unmatched sections):
  1 section_id     identical <id root/extension>, unique on both sides, same code        conf 0.99
  2 match_key      parser match_key (code_path|code|number|title hash), repeated keys
                   paired in order when counts equal, else by monotone similarity DP      0.98 (0.85 repeated)
  3 title_code     same code_path+code+title (number changed / renumbered)               0.90
  4 number_code    same code_path+code+number (title reworded)                           0.80
  5 title_moved    same code+title, different parent path (moved)                        0.75
  6 code_unique    same code, the only unmatched one of that code on both sides          0.70
  7 text_similarity last resort: greedy best SequenceMatcher ratio >= 0.6 (flagged)      = ratio
After the passes, matched pairs whose B order is not on a longest increasing subsequence are flagged
`reordered`; unmatched sections whose text is mostly contained in >= 2 unmatched sections on the other
side are reported in `splits_merges` (they stay added/removed sections).

HEADINGS (0.2.0): the "text" channel starts with the section's normalized title (when it has one), then a
blank line, then the narrative blocks, so a new or reworded section/subsection title is reported as added
text. A heading that differs only in number or case (same _title_key) is not a text change: diffing then
starts after the title on both sides (offsets stay relative to the full channel text) and, with the body
equal, the pair is `heading_only` formatting as before. Sections that have only a title (no narrative)
are real added/removed sections when unmatched. Group assignment is hierarchical (section_groups.classify_doc);
each change carries `topic_group`, the section-local topic classification, alongside `srlc_group`, and
`alt_srlc_group` (non-PLR WARNINGS/PRECAUTIONS subsections: the topic group SrLC sometimes uses instead of WP).

TEXT DIFF: each section has three channels: "text" (narrative blocks, excluding tables), "table"
(captions + cell text, top-level tables, rows on lines, cells separated by " | ") and "excerpt".
A channel is tokenized with regex \\S+ keeping character offsets, so every reported span is an exact
substring of the channel text. Tokens are compared on normalize(token) (literal-v1: NFC, soft-hyphen
removal, quote/dash folding, whitespace collapse; case-sensitive). The diff is
difflib.SequenceMatcher(None, a, b, autojunk=False) over those token keys after trimming the common
prefix/suffix; opcodes replace/delete/insert become removed/added spans (a replace gives one removed
and one added span). Adjacent opcodes are not merged beyond what SequenceMatcher yields.
Moved text: a removed span of >= 4 tokens whose exact token sequence occurs inside any added span of
the record is also listed in moved_spans (it stays in added/removed too; nothing is hidden).

NOISE (0.3.0, spacing rule and sentence-final cross-reference fix in 0.3.1): the substantive/formatting decision and the added/removed spans are computed on canonical
token keys. A token key is normalize(token) after deleting "noise" characters; a token whose key is empty
is dropped from both sequences, so noise never forms an added/removed span of its own (spans still run over
the verbatim text, offsets unchanged, and may contain interior noise). Noise, by channel:
  all channels   ®/™/©/℠; list-bullet glyphs; footnote reference markers ("[FOOTNOTE:id]" tokens are dropped) and media ids ("[MEDIA:id]" -> "[MEDIA:]", so presence still counts);
  text, excerpt  cross-reference numbers: "(5.1)", "( 5.1 )", "(5.1, 7)" (parenthesized section-number lists
                 with a dotted number; bare "(4)" only in excerpts), and inside "[see ...]" / "(see ...)"
                 spans "(4)" and dotted numbers; leading list numbering/lettering ("1.", "a)", "(2)") at line
                 start; revision/date lines ("Revised: 06/2020", "Rev. 05/20", a date-only line) and
                 repackager revision codes (6+ digits "/" 2-3 digits).
  all channels   spacing: key sequences that differ only in where token boundaries fall ("Data]." / "Data] .",
                 "4.2Colitis" / "4.2 Colitis", "5 mg" / "5mg"), unless a differing boundary touches a digit
                 next to a non-letter ("1 0" / "10", "5. 5" / "5.5" stay substantive).
Numbers that are not cross-references (doses, percentages, ages, counts, table cells) are never noise, nor
is any word: a brand<->generic swap or a "see ... Contraindications" -> "see ... Warnings" edit stays
substantive. 0.3.2: cross-reference noise (dotted "(5.1)" refs, numbers in [see ...] / (see ...), bare "(4)" in excerpts) is
NOT formatting: when a channel pair differs only by such tokens (after all other noise) it stays a change, its
spans computed without the other noise, and the change carries reference_only=true (true only when every
substantive channel of the change is reference-only; mixed changes are false). Added/removed spans of every
change keep cross-reference tokens as ordinary tokens.
A channel pair that differs only by noise is `noise_only` formatting (listed in
formatting_changes, never in changes). Earlier: if a channel differs byte-wise but its normalized token
sequence is identical, that is
`formatting` (whitespace_only when the raw tokens are equal, otherwise normalization_only); heading-only
changes (number/title differ, text identical) and empty added/removed sections are formatting too. They
are listed in `formatting_changes`, never in `changes`. Inside modified sections, equal-aligned tokens
whose raw form differs are counted in `formatting_token_diffs`.
"""
import difflib
import hashlib
import json
import re

from .normalize import normalize
from .section_groups import classify_doc
from .spl_parser import _title_key, split_section_number

CHANGES_VERSION = "srlc-changes-0.3.2"  # 0.3.2: cross-reference-only edits are kept as changes flagged reference_only; 0.3.1: + spacing-only rule, sentence-final xref fix; 0.3.0: formatting-noise canonicalization (see NOISE); 0.2.2: tab cell separator
CHANNELS = ("text", "table", "excerpt")
_TOKEN = re.compile(r"\S+")
MOVE_MIN_TOKENS = 4
SIM_MIN = 0.6


def canonical_json(rec):
    return json.dumps(rec, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def record_hash(rec):
    body = {k: v for k, v in rec.items() if k != "record_sha256"}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ section views

_NON_TEXT = {"table_flat", "table_cell", "table_caption"}


def _table_text(tables):
    paths = [t["source_xpath"] for t in tables]
    lines = []
    for t in tables:
        if any(t["source_xpath"].startswith(p + "/") for p in paths):
            continue  # nested table: its text is already inside the outer cell
        for c in t.get("caption", []):
            lines.append(c["text"])
        for r in t["rows"]:
            lines.append("\t".join(c["text"] for c in r["cells"]))
    return "\n".join(lines)


def body_text(sec):
    return "\n\n".join(b["text"] for b in sec["narrative"]["blocks"] if b["kind"] not in _NON_TEXT)


def title_prefix(sec):
    t = (sec.get("title_normalized") or "").strip()
    return t + "\n\n" if t else ""


def channels(sec):
    n = sec["narrative"]
    body = body_text(sec)
    text = title_prefix(sec) + body if body.strip() else (sec.get("title_normalized") or "").strip() + body
    table = _table_text(n["tables"])
    ex_parts = []
    for ex in sec.get("excerpts", []):
        ex_parts.append("\n\n".join(b["text"] for b in ex["blocks"] if b["kind"] not in _NON_TEXT))
        t = _table_text(ex["tables"])
        if t:
            ex_parts.append(t)
    return {"text": text, "table": table, "excerpt": "\n\n".join(p for p in ex_parts if p)}


class View:
    def __init__(self, sec):
        self.sec = sec
        self.o = sec["ordinal"]
        self.ch = channels(sec)
        self.skip = len(title_prefix(sec))  # chars of the text channel taken by the title
        self.body = self.ch["text"][self.skip:]
        t = sec.get("title_normalized") or ""
        self.number, rest = split_section_number(t) if t else (None, "")
        self.title_key = _title_key(rest)
        self.path = tuple(sec["code_path"])
        self.code = sec["code"]
        self._sh = None

    def has_text(self):
        return any(v.strip() for v in self.ch.values())

    def has_body(self):
        return bool(self.body.strip() or self.ch["table"].strip() or self.ch["excerpt"].strip())

    def all_text(self):
        return "\n".join(v for v in [self.body, self.ch["table"], self.ch["excerpt"]] if v)

    def shingles(self):
        if self._sh is None:
            w = [normalize(x) for x in _TOKEN.findall(self.all_text())]
            self._sh = {" ".join(w[i:i + 3]) for i in range(max(len(w) - 2, 0))} or set(w)
        return self._sh


def _jacc(a, b):
    if not a.shingles() and not b.shingles():
        return 1.0
    inter = len(a.shingles() & b.shingles())
    return inter / (len(a.shingles() | b.shingles()) or 1)


# ------------------------------------------------------------------ alignment

def _pair_group(As, Bs):
    """Pair views from two lists that share an alignment key; returns [(a, b, flags)]."""
    if len(As) == len(Bs):
        return [(a, b, ["repeated_key"] if len(As) > 1 else []) for a, b in zip(As, Bs)]
    n, m = len(As), len(Bs)
    sim = [[_jacc(a, b) for b in Bs] for a in As]
    # monotone DP maximizing summed similarity of pairs with sim >= 0.3
    best = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            take = sim[i][j] + best[i + 1][j + 1] if sim[i][j] >= 0.3 else -1
            best[i][j] = max(best[i + 1][j], best[i][j + 1], take)
    out, i, j = [], 0, 0
    while i < n and j < m:
        take = sim[i][j] + best[i + 1][j + 1] if sim[i][j] >= 0.3 else -1
        if take >= best[i][j] - 1e-12 and take >= 0:
            out.append((As[i], Bs[j], ["repeated_key", "count_mismatch"]))
            i += 1
            j += 1
        elif best[i + 1][j] >= best[i][j + 1]:
            i += 1
        else:
            j += 1
    return out


def _base_key(match_key):
    return re.sub(r"~\d+$", "", match_key)


def align(av, bv):
    """av, bv: lists of View. Returns (pairs, unmatched_a, unmatched_b); pair = dict."""
    ua, ub = list(av), list(bv)
    pairs = []

    def take(a, b, method, conf, flags=()):
        pairs.append({"a": a.o, "b": b.o, "method": method, "confidence": round(conf, 3), "flags": sorted(flags)})
        ua.remove(a)
        ub.remove(b)

    def keyed(method, conf, keyfn, only_unique=False):
        ga, gb = {}, {}
        for v in ua:
            k = keyfn(v)
            if k is not None:
                ga.setdefault(k, []).append(v)
        for v in ub:
            k = keyfn(v)
            if k is not None:
                gb.setdefault(k, []).append(v)
        for k in sorted(ga, key=lambda x: repr(x)):
            if k not in gb:
                continue
            if only_unique and (len(ga[k]) != 1 or len(gb[k]) != 1):
                continue
            for a, b, fl in _pair_group(ga[k], gb[k]):
                take(a, b, method, conf - (0.13 if fl else 0), fl)

    def sid(v):
        s = v.sec.get("section_id")
        if not s or not s.get("root"):
            return None
        return (s["root"].lower(), s.get("extension"), v.code)

    keyed("section_id", 0.99, sid, only_unique=True)
    keyed("match_key", 0.98, lambda v: _base_key(v.sec["match_key"]))
    keyed("title_code", 0.90, lambda v: (v.path, v.code, v.title_key) if v.title_key else None)
    keyed("number_code", 0.80, lambda v: (v.path, v.code, v.number) if v.number else None)
    keyed("title_moved", 0.75, lambda v: (v.code, v.title_key) if v.title_key else None, only_unique=True)
    keyed("code_unique", 0.70, lambda v: v.code if v.code != "<uncoded>" else None, only_unique=True)

    # last resort: greedy best text similarity
    cand = []
    for a in ua:
        if not a.has_text():
            continue
        for b in ub:
            if not b.has_text() or _jacc(a, b) < 0.3:
                continue
            r = difflib.SequenceMatcher(None, a.all_text()[:4000], b.all_text()[:4000], autojunk=False).ratio()
            if r >= SIM_MIN:
                cand.append((-r, a.o, b.o, a, b, r))
    for _, _, _, a, b, r in sorted(cand, key=lambda x: x[:3]):
        if a in ua and b in ub:
            take(a, b, "text_similarity", min(r, 0.69), ["fuzzy"])

    pairs.sort(key=lambda p: (p["a"], p["b"]))
    # reordering: LIS over b ordinals when sorted by a
    seq = [p["b"] for p in pairs]
    keep = _lis_indices(seq)
    for i, p in enumerate(pairs):
        if i not in keep:
            p["flags"] = sorted(set(p["flags"]) | {"reordered"})
    return pairs, ua, ub


def _lis_indices(seq):
    import bisect
    tails, idx, prev = [], [], [-1] * len(seq)
    for i, x in enumerate(seq):
        k = bisect.bisect_left(tails, x)
        if k == len(tails):
            tails.append(x)
            idx.append(i)
        else:
            tails[k] = x
            idx[k] = i
        prev[i] = idx[k - 1] if k else -1
    out, i = set(), idx[-1] if idx else -1
    while i != -1:
        out.add(i)
        i = prev[i]
    return out


def _splits_merges(ua, ub):
    out = []
    for side, mine, other in (("split", ua, ub), ("merge", ub, ua)):
        for v in mine:
            sh = v.shingles()
            if len(sh) < 8:
                continue
            covers = []
            for o in other:
                c = len(sh & o.shingles()) / len(sh)
                if c >= 0.2:
                    covers.append((o.o, c))
            tot = len(sh & set().union(*[o.shingles() for o in other if o.o in {x for x, _ in covers}])) / len(sh) \
                if covers else 0
            if len(covers) >= 2 and tot >= 0.8:
                out.append({"kind": side, "ordinal": v.o, "other_ordinals": sorted(x for x, _ in covers),
                            "coverage": round(tot, 3)})
    return out


# ------------------------------------------------------------------ text diff

def tokens(text):
    return [(m.start(), m.end(), m.group()) for m in _TOKEN.finditer(text)]


_SENT_BREAK = re.compile(r"(?<=[.!?])[\"')\]]*\s+(?=[\"'(\[]?[A-Z0-9])|\n\s*\n|\n")


def sentence_spans(text):
    spans, start = [], 0
    for m in _SENT_BREAK.finditer(text):
        if m.start() > start and text[start:m.start()].strip():
            spans.append((start, m.start()))
        start = m.end()
    if text[start:].strip():
        spans.append((start, len(text)))
    return spans


def _context(text, sents, s, e):
    hit = [(a, b) for a, b in sents if a < e and b > s] or [(s, e)]
    return text[hit[0][0]:hit[-1][1]]


_MONTH = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
_DATE = (r"(?:" + _MONTH + r"\.?\s*\d{0,2},?\s*\d{4}|\d{1,2}/\d{1,2}/\d{2,4}|\d{1,2}/\d{2,4}|\d{1,2}-\d{4}"
         r"|\d{4}-\d{2}(?:-\d{2})?|\d{4})")
_NUM = r"\d{1,2}(?:\.\d{1,2}){0,3}"
_NOISE_ALL = (re.compile(r"[\u00ae\u2122\u00a9\u2120\u2022\u25e6\u25aa\u25ab\u25cf\u25a0\u25a1\u25cb\u00b7\u2023\u2043\u2219]"),
              re.compile(r"(?<=\[FOOTNOTE:)[^\]\s]+(?=\])"), re.compile(r"(?<=\[MEDIA:)[^\]\s]+(?=\])"))
_FOOTNOTE_TOKEN = re.compile(r"\[FOOTNOTE:[^\]\s]*\]")
_SEE_SPAN = re.compile(r"[\[(]\s*see\b[^\]\[]*[\])]", re.I)
_SEE_INNER = re.compile(r"\(\s*" + _NUM + r"(?:\s*[,;]\s*" + _NUM + r")*\s*\)|(?<![\w.%])\d{1,2}(?:\.\d{1,2}){1,3}(?![\w%])")
_XREF_DOTTED = re.compile(r"\(\s*(?=[^)]*\d\.\d)" + _NUM + r"(?:\s*[,;]\s*" + _NUM + r")*\s*\)")
_XREF_BARE = re.compile(r"\(\s*" + _NUM + r"(?:\s*[,;]\s*" + _NUM + r")*\s*\)")
_LIST_MARK = re.compile(r"(?m)^[ \t]*(?:\(?(?:\d{1,2}|[a-z]|[ivx]{1,4})\)|(?:\d{1,2}|[a-z]|[ivx]{1,4})[.)](?=[ \t]))")
_REVISION = re.compile(r"\b(?:Last\s+)?(?:Revised|Rev\.?|Revision(?:\s+Date)?|Issued|Updated|Date\s+of\s+(?:Revision|Issue))\s*[:.]?\s*"
                       + _DATE, re.I)
_DATE_LINE = re.compile(r"(?m)^[ \t]*\d{1,2}/\d{4}[ \t]*$")
_REVCODE = re.compile(r"\b\d{6,}/\d{2,3}\b")
_PROSE_CHANNELS = ("text", "excerpt")
_AFTER_VALUE = re.compile(r"[\d%]\s*$")


def _spacing_only(ka, kb):
    """True when two key sequences differ only in where token boundaries fall (the concatenations are equal) and
    no differing boundary touches a digit unless the other side is a letter ("5 mg"/"5mg", "Data]."/"Data] ."
    are spacing; "1 0"/"10" and "5. 5"/"5.5" are not)."""
    ja, jb = "".join(ka), "".join(kb)
    if ja != jb:
        return False

    def bounds(keys):
        out, n = set(), 0
        for k in keys[:-1]:
            n += len(k)
            out.add(n)
        return out
    for i in bounds(ka) ^ bounds(kb):
        left, right = ja[i - 1], ja[i]
        if (left.isdigit() and not right.isalpha()) or (right.isdigit() and not left.isalpha()):
            return False
    return True


def _noise_mask(text, channel, xref=True):
    """bytearray: 1 for characters that are formatting noise in this channel (see NOISE)."""
    mask = bytearray(len(text))

    def mark(m, lo=0):
        mask[m.start() + lo:m.end()] = b"\x01" * (m.end() - m.start() - lo)

    for rx in _NOISE_ALL:
        for m in rx.finditer(text):
            mark(m)
    if channel in _PROSE_CHANNELS:
        for sp in (_SEE_SPAN.finditer(text) if xref else ()):
            for m in _SEE_INNER.finditer(sp.group()):
                mask[sp.start() + m.start():sp.start() + m.end()] = b"\x01" * (m.end() - m.start())
        for rx in (((_XREF_DOTTED, _XREF_BARE) if channel == "excerpt" else (_XREF_DOTTED,)) if xref else ()):
            for m in rx.finditer(text):
                if _AFTER_VALUE.search(text, max(0, m.start() - 3), m.start()):
                    continue  # "2.3 (1.5)", "5% (12)": a value, not a cross-reference
                mark(m)
        for rx in (_LIST_MARK, _REVISION, _DATE_LINE, _REVCODE):
            for m in rx.finditer(text):
                mark(m)
    return mask


def canonical_tokens(text, channel, toks, xref=True):
    """[(start, end, raw, key)] for the tokens of `text` whose canonical key is non-empty (see NOISE)."""
    mask = _noise_mask(text, channel, xref)
    out = []
    for st, en, raw in toks:
        if _FOOTNOTE_TOKEN.fullmatch(raw):
            continue  # a bare footnote reference marker carries no text
        seg = mask[st:en]
        key = normalize("".join(c for c, m in zip(raw, seg) if not m)) if any(seg) else normalize(raw)
        if key:
            out.append((st, en, raw, key))
    return out


def diff_channel(a_text, b_text, a_skip=0, b_skip=0, channel="text"):
    """Return dict(added, removed, equal_raw_diffs, kind) for one channel.
    kind: 'same' | 'whitespace_only' | 'normalization_only' | 'noise_only' | 'substantive'."""
    if a_text[a_skip:] == b_text[b_skip:]:
        return {"kind": "same", "added": [], "removed": [], "fmt_tokens": 0}
    ta = [t for t in tokens(a_text) if t[0] >= a_skip]
    tb = [t for t in tokens(b_text) if t[0] >= b_skip]
    ka, kb = [normalize(t[2]) for t in ta], [normalize(t[2]) for t in tb]
    if ka == kb:
        raw_equal = [t[2] for t in ta] == [t[2] for t in tb]
        return {"kind": "whitespace_only" if raw_equal else "normalization_only", "added": [], "removed": [],
                "fmt_tokens": sum(1 for x, y in zip(ta, tb) if x[2] != y[2])}
    # formatting noise without cross-references decides formatting and drives the spans; a pair that is
    # substantive only through cross-reference tokens stays a change, flagged reference_only
    ca = canonical_tokens(a_text, channel, ta, False)
    cb = canonical_tokens(b_text, channel, tb, False)
    ka, kb = [t[3] for t in ca], [t[3] for t in cb]
    ta, tb = [t[:3] for t in ca], [t[:3] for t in cb]
    if ka == kb or _spacing_only(ka, kb):
        return {"kind": "noise_only", "added": [], "removed": [], "fmt_tokens": sum(1 for x, y in zip(ta, tb) if x[2] != y[2])}
    xa = [t[3] for t in canonical_tokens(a_text, channel, [t for t in tokens(a_text) if t[0] >= a_skip])]
    xb = [t[3] for t in canonical_tokens(b_text, channel, [t for t in tokens(b_text) if t[0] >= b_skip])]
    ref_only = xa == xb or _spacing_only(xa, xb)
    if ref_only and "".join(ka) == "".join(kb):  # cross-reference respaced ("( 5.1)" / "(5.1)"): spacing only
        return {"kind": "noise_only", "added": [], "removed": [], "fmt_tokens": sum(1 for x, y in zip(ta, tb) if x[2] != y[2])}
    pre = 0
    while pre < len(ka) and pre < len(kb) and ka[pre] == kb[pre]:
        pre += 1
    suf = 0
    while suf < len(ka) - pre and suf < len(kb) - pre and ka[-1 - suf] == kb[-1 - suf]:
        suf += 1
    ma = ka[pre:len(ka) - suf]
    mb = kb[pre:len(kb) - suf]
    ops = difflib.SequenceMatcher(None, ma, mb, autojunk=False).get_opcodes() if (ma and mb) else \
        [("delete" if ma else "insert", 0, len(ma), 0, len(mb))]
    sa, sb = sentence_spans(a_text), sentence_spans(b_text)  # context sentences come from the full text
    added, removed, fmt = [], [], 0
    for tag, i1, i2, j1, j2 in ops:
        i1 += pre
        i2 += pre
        j1 += pre
        j2 += pre
        if tag == "equal":
            fmt += sum(1 for k in range(i2 - i1) if ta[i1 + k][2] != tb[j1 + k][2])
            continue
        if i2 > i1:
            s, e = ta[i1][0], ta[i2 - 1][1]
            removed.append({"text": a_text[s:e], "char_start": s, "char_end": e, "tokens": i2 - i1,
                            "context": _context(a_text, sa, s, e)})
        if j2 > j1:
            s, e = tb[j1][0], tb[j2 - 1][1]
            added.append({"text": b_text[s:e], "char_start": s, "char_end": e, "tokens": j2 - j1,
                          "context": _context(b_text, sb, s, e)})
    fmt += sum(1 for k in range(pre) if ta[k][2] != tb[k][2])
    fmt += sum(1 for k in range(1, suf + 1) if ta[-k][2] != tb[-k][2])
    return {"kind": "substantive", "added": added, "removed": removed, "fmt_tokens": fmt, "reference_only": ref_only}


def _span(ch, s):
    return {"channel": ch, **s}


# ------------------------------------------------------------------ record

def _sec_info(v, groups):
    g = groups[v.o]
    return {"srlc_group": g["group"], "group_basis": g["basis"], "group_flags": g["flags"],
            "topic_group": g["topic_group"], "alt_srlc_group": g["alt_group"]}


def _title(v):
    return v.sec.get("title_normalized")


def compare_docs(a, b, a_artifact_sha256=None, b_artifact_sha256=None):
    rec = {
        "changes_version": CHANGES_VERSION,
        "setid": b.get("setid") or a.get("setid"),
        "from_setid": a.get("setid"), "to_setid": b.get("setid"),
        "from_version": a.get("version"), "to_version": b.get("version"),
        "from_effective_time": a.get("effective_time"), "to_effective_time": b.get("effective_time"),
        "from_artifact_sha256": a_artifact_sha256, "to_artifact_sha256": b_artifact_sha256,
        "from_source_sha256": a.get("source_sha256"), "to_source_sha256": b.get("source_sha256"),
        "from_parser_output_sha256": a.get("content_sha256"), "to_parser_output_sha256": b.get("content_sha256"),
        "parser_version": b.get("parser_version"), "from_parser_version": a.get("parser_version"),
        "comparison": "ok", "comparison_reasons": [],
        "alignment": [], "splits_merges": [], "unmatched_from": [], "unmatched_to": [],
        "changes": [], "formatting_changes": [],
    }
    bad = [(w, d["status"]) for w, d in (("from", a), ("to", b)) if d.get("status") in ("no_label_body", "extraction_defect")]
    if bad or a.get("parser_version") != b.get("parser_version"):
        rec["comparison"] = "not_comparable"
        rec["comparison_reasons"] = [f"{w}_status:{s}" for w, s in bad] + \
            (["parser_version_mismatch"] if a.get("parser_version") != b.get("parser_version") else [])
        rec["record_sha256"] = record_hash(rec)
        return rec
    if a.get("content_sha256") and a.get("content_sha256") == b.get("content_sha256"):
        rec["comparison_reasons"] = ["identical_parser_output"]
    av = [View(s) for s in a["sections"]]
    bv = [View(s) for s in b["sections"]]
    ia = {v.o: v for v in av}
    ib = {v.o: v for v in bv}
    pairs, ua, ub = align(av, bv)
    rec["alignment"] = pairs
    rec["splits_merges"] = _splits_merges(ua, ub)
    rec["unmatched_from"] = [v.o for v in ua]
    rec["unmatched_to"] = [v.o for v in ub]

    groups_a, groups_b = classify_doc(a), classify_doc(b)
    groups = groups_b
    changes, fmts = [], []
    for p in pairs:
        va, vb = ia[p["a"]], ib[p["b"]]
        heading_changed = (va.number != vb.number) or (va.title_key != vb.title_key)
        per, any_sub, table_changed, fmt_tok, fmt_kinds, ref_flags = {}, False, False, 0, [], []
        for ch in CHANNELS:
            same_title = ch == "text" and va.title_key == vb.title_key
            d = diff_channel(va.ch[ch], vb.ch[ch], va.skip if same_title else 0, vb.skip if same_title else 0, ch)
            per[ch] = d
            if d["kind"] == "substantive":
                any_sub = True
                ref_flags.append(d["reference_only"])
                if ch == "table":
                    table_changed = True
            elif d["kind"] != "same":
                fmt_kinds.append(f"{ch}:{d['kind']}")
            fmt_tok += d["fmt_tokens"]
        base = {"section_number": vb.sec.get("section_number"), "section_title": _title(vb),
                "section_key": vb.sec["section_key"], "ordinal_to": vb.o, "ordinal_from": va.o,
                "alignment_method": p["method"], "alignment_confidence": p["confidence"],
                "section_kind": vb.sec["kind"], **_sec_info(vb, groups)}
        if heading_changed:
            base["from_section_number"] = va.sec.get("section_number")
            base["from_section_title"] = _title(va)
            base["from_section_key"] = va.sec["section_key"]
        ga = groups_a[va.o]
        if ga["group"] != base["srlc_group"]:
            base["from_srlc_group"] = ga["group"]
        if any_sub:
            added = [_span(ch, s) for ch in CHANNELS for s in per[ch]["added"]]
            removed = [_span(ch, s) for ch in CHANNELS for s in per[ch]["removed"]]
            changes.append({**base, "kind": "modified", "added_spans": added, "removed_spans": removed,
                            "moved_spans": [], "table_changed": table_changed,
                            "formatting_token_diffs": fmt_tok, "reference_only": all(ref_flags)})
        elif fmt_kinds or heading_changed or fmt_tok:
            fmts.append({**base, "type": "heading_only" if heading_changed and not fmt_kinds else
                         ("+".join(fmt_kinds) or "token_form"), "formatting_token_diffs": fmt_tok,
                         "heading_changed": heading_changed})
    for v, kind in [(x, "added_section") for x in ub] + [(x, "removed_section") for x in ua]:
        info = _sec_info(v, groups_b if kind == "added_section" else groups_a)
        base = {"section_number": v.sec.get("section_number"), "section_title": _title(v),
                "section_key": v.sec["section_key"], "alignment_method": "unmatched", "alignment_confidence": 1.0,
                "section_kind": v.sec["kind"], **info}
        if not v.has_text():
            fmts.append({**base, "type": "empty_" + kind, "ordinal_" + ("to" if kind == "added_section" else "from"): v.o,
                         "formatting_token_diffs": 0, "heading_changed": False})
            continue
        spans = [{"channel": ch, "text": v.ch[ch], "char_start": 0, "char_end": len(v.ch[ch]),
                  "tokens": len(tokens(v.ch[ch])), "context": ""} for ch in CHANNELS if v.ch[ch].strip()]
        added = spans if kind == "added_section" else []
        removed = spans if kind == "removed_section" else []
        changes.append({**base, "kind": kind, "added_spans": added, "removed_spans": removed, "moved_spans": [],
                        "ordinal_" + ("to" if kind == "added_section" else "from"): v.o,
                        "table_changed": bool(v.ch["table"].strip()), "formatting_token_diffs": 0,
                        "reference_only": False})

    # moved text: removed spans (>= MOVE_MIN_TOKENS) found verbatim (token-wise) inside any added span
    added_idx = []
    for c in changes:
        for s in c["added_spans"]:
            added_idx.append((c, [normalize(t[2]) for t in tokens(s["text"])]))
    for c in changes:
        for s in c["removed_spans"]:
            rt = [normalize(t[2]) for t in tokens(s["text"])]
            if len(rt) < MOVE_MIN_TOKENS:
                continue
            for c2, at in added_idx:
                if c2 is c and c["kind"] == "modified":
                    pass
                if _contains(at, rt):
                    c["moved_spans"].append({"channel": s["channel"], "text": s["text"],
                                             "to_section_key": c2["section_key"],
                                             "to_section_number": c2["section_number"]})
                    break

    def order(c):
        if c["kind"] == "removed_section":
            return (1, c["ordinal_from"])
        return (0, c["ordinal_to"])
    rec["changes"] = sorted(changes, key=order)
    rec["formatting_changes"] = sorted(fmts, key=lambda c: (c.get("ordinal_to") or 10 ** 6, c.get("ordinal_from") or 0))
    rec["record_sha256"] = record_hash(rec)
    return rec


def _contains(hay, needle):
    n = len(needle)
    if n > len(hay):
        return False
    first = needle[0]
    for i in range(len(hay) - n + 1):
        if hay[i] == first and hay[i:i + n] == needle:
            return True
    return False
