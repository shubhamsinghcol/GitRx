"""Deterministic section -> SrLC group mapping (BW, C, WP, AR, DI, USP, MG).

classify(section) takes one section dict from gitrx_core.spl_parser (spl-sections-0.2.0) and returns
{"group": str|None, "basis": str, "flags": [str]}. The basis names the signal that decided:

  code:<loinc>            the section's own LOINC code is a group-defining code
  title_registry:<loinc>  generic/uncoded section whose title equals a registry section name
  title_hint:<key>        generic/uncoded section whose title matches the small hint table below
  ancestor:<loinc>        generic/uncoded section nested under a group-defining ancestor
  ancestor_default:<loinc> PRECAUTIONS container default (non-PLR) when no title hint applies
  number:<n>              (secondary, always flagged) PLR top-level number of a generic section
  none                    no signal; group is None

Signals in precedence order for a section whose own code is group-defining: code. For generic codes
(42229-5 "unclassified section" and uncoded): strong ancestor (PLR-style container) > title (registry
or hint) > weak ancestor (42232-9 PRECAUTIONS default WP) > number. Nothing is inferred beyond these
tables; every decision is recorded. Group meanings follow the FDA SrLC grouping given in the project
plan; `approx:` flags mark code assignments that are a judgement call about which group a code
belongs to (for example 34074-5 drug/laboratory test interactions -> DI).
"""
import json
import re
from pathlib import Path

from .normalize import normalize

GROUPS = ("BW", "C", "WP", "AR", "DI", "USP", "MG")
GROUP_NAMES = {
    "BW": "Boxed Warning", "C": "Contraindications", "WP": "Warnings and Precautions",
    "AR": "Adverse Reactions", "DI": "Drug Interactions", "USP": "Use in Specific Populations",
    "MG": "Patient Counseling Information / Patient Information / Medication Guide",
}
GENERIC_CODES = {"42229-5", "<uncoded>"}  # unclassified section / no code: carry no meaning of their own

# Own-code -> group. All codes verified against runtime/release-job-config.json section_codes (see tests).
CODE_GROUPS = {
    "34066-1": "BW",   # BOXED WARNING SECTION
    "34070-3": "C",    # CONTRAINDICATIONS
    "43685-7": "WP",   # WARNINGS AND PRECAUTIONS (PLR)
    "34071-1": "WP",   # WARNINGS (non-PLR)
    "42232-9": "WP",   # PRECAUTIONS (non-PLR); subsections remapped below
    "34072-9": "WP",   # GENERAL PRECAUTIONS
    "34084-4": "AR",   # ADVERSE REACTIONS
    "90374-0": "AR",   # CLINICAL TRIALS EXPERIENCE
    "90375-7": "AR",   # POSTMARKETING EXPERIENCE
    "34073-7": "DI",   # DRUG INTERACTIONS
    "34074-5": "DI",   # DRUG/LABORATORY TEST INTERACTIONS (approx)
    "43684-0": "USP",  # USE IN SPECIFIC POPULATIONS
    "42228-7": "USP",  # PREGNANCY
    "77290-5": "USP",  # LACTATION
    "77291-3": "USP",  # FEMALES AND MALES OF REPRODUCTIVE POTENTIAL
    "34080-2": "USP",  # NURSING MOTHERS
    "34081-0": "USP",  # PEDIATRIC USE
    "34082-8": "USP",  # GERIATRIC USE
    "34079-4": "USP",  # LABOR AND DELIVERY
    "34077-8": "USP",  # TERATOGENIC EFFECTS (pregnancy subsection)
    "34078-6": "USP",  # NONTERATOGENIC EFFECTS (pregnancy subsection)
    "88828-9": "USP",  # RENAL IMPAIRMENT subsection
    "88829-7": "USP",  # HEPATIC IMPAIRMENT subsection
    "88436-1": "MG",   # PATIENT COUNSELING INFORMATION
    "34076-0": "MG",   # INFORMATION FOR PATIENTS
    "42231-1": "MG",   # SPL MEDGUIDE
    "42230-3": "MG",   # SPL PATIENT PACKAGE INSERT
    "68498-5": "MG",   # PATIENT MEDICATION INFORMATION
}
APPROX_CODES = {"34074-5", "34077-8", "34078-6", "34079-4", "88828-9", "88829-7", "90374-0", "90375-7",
                "34072-9"}
# Container codes whose descendants inherit the group when they are generic.
STRONG_ANCESTORS = {c: g for c, g in CODE_GROUPS.items() if c not in {"42232-9", "34072-9"}}
WEAK_ANCESTORS = {"42232-9": "WP"}  # non-PLR PRECAUTIONS: children default WP unless title says otherwise

# Title hints for generic sections (matched on the title text after any leading number; casefolded,
# '&'->'and', punctuation-trimmed). Exact key or prefix + boundary.
TITLE_HINTS = (
    ("boxed warning", "BW"), ("contraindications", "C"), ("contraindication", "C"),
    ("warnings and precautions", "WP"), ("warnings", "WP"), ("warning", "WP"), ("precautions", "WP"),
    ("general precautions", "WP"),
    ("adverse reactions", "AR"), ("adverse events", "AR"), ("clinical trials experience", "AR"),
    ("postmarketing experience", "AR"), ("post marketing experience", "AR"),
    ("drug interactions", "DI"), ("drug interaction", "DI"),
    ("use in specific populations", "USP"), ("pregnancy", "USP"), ("nursing mothers", "USP"),
    ("nursing", "USP"), ("lactation", "USP"), ("pediatric use", "USP"), ("pediatrics", "USP"),
    ("geriatric use", "USP"), ("geriatrics", "USP"), ("labor and delivery", "USP"),
    ("females and males of reproductive potential", "USP"),
    ("renal impairment", "USP"), ("hepatic impairment", "USP"),
    ("information for patients", "MG"), ("information for the patient", "MG"),
    ("patient counseling information", "MG"), ("patient information", "MG"),
    ("medication guide", "MG"), ("medication guides", "MG"),
)
PLR_NUMBER_GROUPS = {4: "C", 5: "WP", 6: "AR", 7: "DI", 8: "USP", 17: "MG"}


def _title_rest(section):
    from .spl_parser import _title_key, split_section_number
    t = section.get("title_normalized") or ""
    num, rest = split_section_number(t)
    return _title_key(rest)


def _title_group(key):
    if not key:
        return None
    for hint, group in TITLE_HINTS:
        if key == hint or (key.startswith(hint) and not key[len(hint)].isalnum()):
            return hint, group
    if key == "general":
        return "general", "WP"  # exact only; effective as a PRECAUTIONS subsection title
    return None


def _number_group(section):
    num = section.get("section_number")
    if not num:
        return None
    try:
        return int(num.split(".")[0]), PLR_NUMBER_GROUPS.get(int(num.split(".")[0]))
    except ValueError:
        return None


def classify(section):
    code = section.get("code") or "<uncoded>"
    path = list(section.get("code_path") or [])
    flags = []
    num = _number_group(section)
    num_group = num[1] if num else None

    def done(group, basis, extra=()):
        fl = list(flags) + list(extra)
        if group and num_group and num_group != group and not basis.startswith("number:"):
            fl.append(f"number_disagrees:{num[0]}->{num_group}")
        return {"group": group, "basis": basis, "flags": sorted(set(fl))}

    if code in CODE_GROUPS:
        if code in APPROX_CODES:
            flags.append("approx:code")
        # a PRECAUTIONS-family or WARNINGS container keeps its code group
        return done(CODE_GROUPS[code], f"code:{code}")

    generic = code in GENERIC_CODES or section.get("code_state") == "uncoded"
    if not generic:
        # specific non-group code (indications, dosage, description, ...).  Inherit only from a strong
        # ancestor, flagged, so the decision is visible; otherwise no group.
        for anc in reversed(path):
            if anc in STRONG_ANCESTORS:
                return done(STRONG_ANCESTORS[anc], f"ancestor:{anc}", ["neutral_code_under_group:" + code])
        return done(None, "none")

    # generic code: strong ancestor first
    strong = next((a for a in reversed(path) if a in STRONG_ANCESTORS), None)
    weak = next((a for a in reversed(path) if a in WEAK_ANCESTORS), None)
    if strong is not None:
        # strong ancestor deeper than the weak one wins; a strong ancestor above a weak one also wins
        # unless the weak container is nearer (e.g. a PRECAUTIONS block inside WARNINGS AND PRECAUTIONS)
        if weak is None or len(path) - 1 - path[::-1].index(strong) >= len(path) - 1 - path[::-1].index(weak):
            return done(STRONG_ANCESTORS[strong], f"ancestor:{strong}")
    key = _title_rest(section)
    hint = section.get("registry_hint")
    if hint and hint.get("loinc") in CODE_GROUPS:
        return done(CODE_GROUPS[hint["loinc"]], f"title_registry:{hint['loinc']}", ["inferred:title"])
    th = _title_group(key)
    if th:
        return done(th[1], f"title_hint:{th[0]}", ["inferred:title"])
    if weak is not None:
        return done(WEAK_ANCESTORS[weak], f"ancestor_default:{weak}", ["default:precautions_subsection"])
    if strong is not None:
        return done(STRONG_ANCESTORS[strong], f"ancestor:{strong}")
    if num_group:
        return done(num_group, f"number:{num[0]}", ["inferred:number"])
    return done(None, "none")


def registry_codes(root=None):
    """Section codes from runtime/release-job-config.json (the 109-code registry)."""
    root = Path(root) if root else Path(__file__).resolve().parents[1]
    return set(json.loads((root / "runtime" / "release-job-config.json").read_text())["section_codes"])


def unregistered_mapped_codes(root=None):
    """Codes used by CODE_GROUPS that are not in the registry (must be empty)."""
    return sorted(set(CODE_GROUPS) - registry_codes(root))


# ---------------------------------------------------------------------------------------------
# srlc-groups-0.2.0: document-level hierarchy classification
#
# classify() above is the section-local topic classifier (the 0.1.0 behaviour, unchanged). It is kept
# as `topic_group` in classify_doc() results. classify_doc() applies the SrLC filing convention, which is
# hierarchical rather than topical:
#
#   PLR label  (is_plr(doc) true):
#     1. a section with its own dotted number x.y whose top number is in PLR_NUMBER_GROUPS takes that
#        group regardless of title or code (5.x WP, 6.x AR, 7.x DI, 8.x USP, 17.x MG, 4.x C);
#     2. otherwise a section whose PARENT has a group inherits it (also unnumbered/untitled children and
#        children with a different own code), except a section whose own code is the boxed warning code;
#     3. otherwise the topic classification (code / title / ancestor / number) decides.
#   Non-PLR label:
#     1. a section nested at any depth under a WARNINGS (34071-1) or PRECAUTIONS (42232-9) section, or an
#        uncoded section titled WARNINGS/PRECAUTIONS, gets WP whatever its own topic (Pregnancy, Pediatric
#        Use, Drug Interactions, Information for Patients ... filed under PRECAUTIONS are WP). SrLC is
#        not consistent here (open-split evidence: it files some such subsections under their topic group), so
#        the topic group is kept as `alt_group` (an accepted alternative) when it differs from WP;
#     2. otherwise a section with no group of its own, or an untitled generic one, inherits its parent's
#        group; 3. otherwise the topic classification.
#
# srlc-groups-0.3.0 adds the UNCODED BOX rule (applied last, to every document): a top-level section with a
# generic or missing code (42229-5 / uncoded), located before the first major section of the label, without
# a 34066-1 code, is BW when its leading text is an upper-case boxed-warning heading:
#   box_title  its title starts with WARNING or BOXED WARNING (upper case, not followed by a letter, so
#              "WARNINGS", "Warning", "SEE BOXED WARNING", "WARNING AND PRECAUTIONS", "WARNING SIGNALS" and
#              "WARNING TO WOMEN ..." do not match; a bare "WARNING" does);
#   box_lead   it has no title and its first narrative text (media markers skipped) starts that way;
#   box_table  (titled or untitled) one of its top-level tables (the usual bordered box) has a first non-empty cell that starts
#              that way.
# "Before the first major section" = ordinal below the first top-level section that has a non-generic
# code other than 48780-1 (SPL product data elements) and 34066-1, or a generic code with a title naming a
# standard major section (INDICATIONS, DESCRIPTION, CLINICAL PHARMACOLOGY, DOSAGE, CONTRAINDICATIONS,
# WARNINGS, PRECAUTIONS, ADVERSE REACTIONS, HOW SUPPLIED ...). Recent Major Changes (43683-2), Highlights and
# other coded sections are never touched. The decision is flagged inferred:uncoded_box and its basis names the
# signal (uncoded_box:title|lead|table). Not applied when the document has any 34066-1 section (the box is then
# coded; WARNING-led uncoded sections are headers, empty containers or Highlights copies) nor to sections
# titled HIGHLIGHTS ... (the Highlights block holds a copy of the box plus everything else).
#
# PLR detection (mechanical, from parser output): the document has a section coded 43685-7 (WARNINGS AND
# PRECAUTIONS) or 43684-0 (USE IN SPECIFIC POPULATIONS), or any section with a Highlights excerpt, or a
# top-level section numbered exactly 5/6/7/8 whose title is that PLR major-section title.
# ---------------------------------------------------------------------------------------------
GROUPS_VERSION = "srlc-groups-0.3.0"
PLR_CODES = {"43685-7", "43684-0"}
NONPLR_WP_CONTAINERS = {"34071-1", "42232-9"}
_PLR_TITLE_NUM = {"5": "warnings and precautions", "6": "adverse reactions", "7": "drug interactions",
                  "8": "use in specific populations"}


def is_plr(doc):
    for s in doc.get("sections", []):
        if s.get("code") in PLR_CODES or s.get("excerpts"):
            return True
        if s.get("section_number") in _PLR_TITLE_NUM and _title_rest(s) == _PLR_TITLE_NUM[s["section_number"]]:
            return True
    return False


def _dotted_top_group(section):
    num = section.get("section_number")
    if not num or "." not in num:
        return None
    return _number_group(section)[1] if _number_group(section) else None


def _is_wp_container(sec):
    if sec.get("code") in NONPLR_WP_CONTAINERS:
        return True
    return sec.get("code") in GENERIC_CODES and _title_rest(sec) in {"warnings", "precautions", "warning"}


def classify_doc(doc):
    """-> {ordinal: {"group","basis","flags","topic_group","topic_basis"}} for every section of a parsed doc."""
    plr = is_plr(doc)
    secs = doc.get("sections", [])
    boxes = uncoded_box_sections(doc)
    by = {s["ordinal"]: s for s in secs}
    out = {}
    for s in secs:  # document order: parents precede children
        topic = classify(s)
        res = {"group": topic["group"], "basis": topic["basis"], "flags": list(topic["flags"])}
        parent = out.get(s.get("parent_ordinal"))
        if plr:
            dg = _dotted_top_group(s)
            if dg and s.get("code") != "34066-1":
                res = {"group": dg, "basis": "hierarchy:number:" + s["section_number"],
                       "flags": [f for f in topic["flags"] if f.startswith("approx:")]}
            elif parent and parent["group"] and s.get("code") != "34066-1" and not topic["basis"].startswith("code:34066-1"):
                res = {"group": parent["group"], "basis": "hierarchy:parent",
                       "flags": ["inherited_from_parent"]}
        else:
            anc = []
            p = by.get(s.get("parent_ordinal"))
            while p is not None:
                anc.append(p)
                p = by.get(p.get("parent_ordinal"))
            if s.get("code") != "34066-1" and any(_is_wp_container(a) for a in anc):
                res = {"group": "WP", "basis": "hierarchy:nonplr_warnings_precautions", "flags": [],
                       "alt_group": topic["group"] if topic["group"] not in (None, "WP") else None}
            elif parent and parent["group"] and (
                    topic["group"] is None or (not s.get("title_normalized") and
                                               (s.get("code") or "<uncoded>") in GENERIC_CODES)):
                res = {"group": parent["group"], "basis": "hierarchy:parent", "flags": ["inherited_from_parent"]}
        if s["ordinal"] in boxes:
            res = {"group": "BW", "basis": "uncoded_box:" + boxes[s["ordinal"]], "flags": ["inferred:uncoded_box"],
                   "alt_group": None}
        res["flags"] = sorted(set(res["flags"]))
        res.setdefault("alt_group", None)
        res["topic_group"] = topic["group"]
        res["topic_basis"] = topic["basis"]
        out[s["ordinal"]] = res
    return out


# ---------------------------------------------------------------------------------------------
# srlc-groups-0.3.0: uncoded boxed warning rule
# ---------------------------------------------------------------------------------------------
_BOX_HEAD = re.compile(r"^(?:BOXED WARNING|WARNING)(?![A-Za-z])(?!\s*(?:AND|&)\s*PRECAUTION)(?!\s+(?:SIGN|SYMPTOM|LABEL|TO\b))")
_NEUTRAL_CODES = {"48780-1", "34066-1"}
_MAJOR_TITLES = ("indications", "description", "clinical pharmacology", "dosage", "contraindications", "warnings",
                 "precautions", "adverse reactions", "how supplied", "overdosage", "drug abuse and dependence",
                 "use in specific populations", "warnings and precautions", "drug interactions")
_NON_TEXT_KINDS = {"table_flat", "table_cell", "table_caption"}
_MEDIA_MARK = re.compile(r"\[(?:MEDIA|FOOTNOTE|NESTED-TABLE)[^\]]*\]")


def _box_text(text):
    return _MEDIA_MARK.sub(" ", text or "").replace("\u2022", " ").strip(" \t\n\r\u00a0*-")


def _is_box_head(text):
    t = normalize(_box_text(text))
    return bool(_BOX_HEAD.match(t))


def _is_major(sec):
    code = sec.get("code") or "<uncoded>"
    if code not in GENERIC_CODES:
        return code not in _NEUTRAL_CODES
    key = _title_rest(sec)
    return any(key == m or key.startswith(m + " ") for m in _MAJOR_TITLES) if key else False


def _uncoded_box_signal(sec):
    """'title' | 'lead' | 'table' | None for a generic-coded top-level section (position checked by the caller)."""
    title = sec.get("title_normalized") or ""
    if title.strip():
        if _is_box_head(title):
            return "title"
    else:
        for b in sec["narrative"]["blocks"]:
            if b["kind"] in _NON_TEXT_KINDS or not _box_text(b["text"]).strip():
                continue
            if _is_box_head(b["text"]):
                return "lead"
            break
    tables = sec["narrative"]["tables"]
    paths = [t["source_xpath"] for t in tables]
    for t in tables:
        if any(t["source_xpath"].startswith(q + "/") for q in paths):
            continue  # nested table
        first = next((c["text"] for r in t["rows"] for c in r["cells"] if _box_text(c["text"]).strip()), None)
        if first is not None and _is_box_head(first):
            return "table"
    return None


def uncoded_box_sections(doc):
    """{ordinal: signal} for the top-level uncoded boxed-warning sections of a parsed doc (see the 0.3.0 note)."""
    out = {}
    if any(s.get("code") == "34066-1" for s in doc.get("sections", [])):
        return out  # the box is coded: any WARNING-led uncoded section is a header, container or highlights copy
    for s in doc.get("sections", []):
        if s.get("parent_ordinal") is None and _is_major(s):
            break
        if s.get("parent_ordinal") is not None or _title_rest(s).startswith("highlights"):
            continue
        if (s.get("code") or "<uncoded>") in GENERIC_CODES or s.get("code_state") == "uncoded":
            sig = _uncoded_box_signal(s)
            if sig:
                out[s["ordinal"]] = sig
    return out
