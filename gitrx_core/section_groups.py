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
