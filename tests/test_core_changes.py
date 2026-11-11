"""Tests for gitrx_core.changes and gitrx_core.section_groups (SrLC-style change records)."""
import json
from pathlib import Path

import pytest

from gitrx_core import changes as C
from gitrx_core import section_groups as G
from gitrx_core import spl_parser as P

FIX = Path(__file__).parent / "fixtures" / "core_changes"
SID = "11111111-2222-3333-4444-555555555555"
L = "2.16.840.1.113883.6.1"


def doc(body, version=1, eff="20240101"):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<document xmlns="urn:hl7-org:v3"><id root="99999999-0000-0000-0000-000000000000"/>
<code code="34391-3" codeSystem="{L}" displayName="HUMAN PRESCRIPTION DRUG LABEL"/>
<title>T</title><effectiveTime value="{eff}"/><setId root="{SID}"/><versionNumber value="{version}"/>
<component><structuredBody>{body}</structuredBody></component></document>""".encode("utf-8")


def sec(code, title, text="", sid=None, kids=(), table=""):
    c = f'<code code="{code}" codeSystem="{L}"/>' if code else ""
    i = f'<id root="{sid}"/>' if sid else ""
    t = f"<title>{title}</title>" if title is not None else ""
    body = f"<text>{text}{table}</text>"
    k = "".join(f"<component>{x}</component>" for x in kids)
    return f"<section>{i}{c}{t}{body}{k}</section>"


def body(*secs):
    return "".join(f"<component>{s}</component>" for s in secs)


def p(*txt):
    return "".join(f"<paragraph>{t}</paragraph>" for t in txt)


def parse(*secs, version=1):
    return P.parse(doc(body(*secs), version=version))


def cmp(a, b):
    return C.compare_docs(a, b)


def grp(xml_sec_doc, i=0):
    s = xml_sec_doc["sections"][i]
    return G.classify(s)


def flat_groups(d):
    return {s["title_normalized"]: G.classify(s) for s in d["sections"]}


# ------------------------------------------------------------------ section groups

def test_mapped_codes_are_in_registry():
    assert G.unregistered_mapped_codes() == []
    names = {v for v in P._REGISTRY_TITLES.values()}
    assert set(G.CODE_GROUPS) <= names | {"42229-5"}


@pytest.mark.parametrize("code,group", [
    ("34066-1", "BW"), ("34070-3", "C"), ("43685-7", "WP"), ("34071-1", "WP"), ("34084-4", "AR"),
    ("34073-7", "DI"), ("43684-0", "USP"), ("42228-7", "USP"), ("34081-0", "USP"), ("34082-8", "USP"),
    ("77290-5", "USP"), ("88436-1", "MG"), ("34076-0", "MG"), ("42231-1", "MG"), ("42230-3", "MG"),
    ("68498-5", "MG"), ("34067-9", None), ("34068-7", None), ("34090-1", None)])
def test_code_groups(code, group):
    d = parse(sec(code, "X heading", p("t")))
    r = grp(d)
    assert r["group"] == group
    if group:
        assert r["basis"] == f"code:{code}"


def test_plr_generic_subsection_inherits_from_ancestor():
    d = parse(sec("43685-7", "5 WARNINGS AND PRECAUTIONS", "", kids=[sec("42229-5", "5.2 Bleeding", p("x"))]))
    r = G.classify(d["sections"][1])
    assert r["group"] == "WP" and r["basis"] == "ancestor:43685-7" and r["flags"] == []
    d = parse(sec("34066-1", "WARNING: X", p("a"), kids=[sec("42229-5", "Sub", p("b"))]))
    assert G.classify(d["sections"][1])["group"] == "BW"
    d = parse(sec("43684-0", "8 USE IN SPECIFIC POPULATIONS", "", kids=[sec("42229-5", "8.4 Pediatric", p("z"))]))
    assert G.classify(d["sections"][1])["group"] == "USP"


def test_strong_ancestor_beats_title_hint():
    # "Drug Interactions" text inside 5.x of W&P stays WP (ancestor), not DI
    d = parse(sec("43685-7", "5 WARNINGS AND PRECAUTIONS", "", kids=[sec("42229-5", "5.4 Drug Interactions", p("x"))]))
    assert G.classify(d["sections"][1])["group"] == "WP"


def test_nonplr_warnings_and_precautions_subsections():
    pre = sec("42232-9", "PRECAUTIONS", p("intro"), kids=[
        sec("42229-5", "Pregnancy", p("p")), sec("42229-5", "Nursing Mothers", p("n")),
        sec("42229-5", "Pediatric Use", p("c")), sec("42229-5", "Geriatric Use", p("g")),
        sec("42229-5", "Drug Interactions", p("d")), sec("42229-5", "Information for Patients", p("i")),
        sec("42229-5", "General", p("ge")), sec("42229-5", "Carcinogenesis", p("c2")),
        sec(None, "Pregnancy Category C", p("pc")), sec("34076-0", "Information for Patients", p("i2"))])
    d = parse(sec("34071-1", "WARNINGS", p("w")), pre)
    got = {s["title_normalized"]: G.classify(s) for s in d["sections"][1:]}
    assert d["sections"][0]["code"] == "34071-1" and G.classify(d["sections"][0]) == {"group": "WP", "basis": "code:34071-1", "flags": []}
    assert got["PRECAUTIONS"]["group"] == "WP" and got["PRECAUTIONS"]["basis"] == "code:42232-9"
    for t, g in [("Pregnancy", "USP"), ("Nursing Mothers", "USP"), ("Pediatric Use", "USP"),
                 ("Geriatric Use", "USP"), ("Drug Interactions", "DI"), ("Information for Patients", "MG"),
                 ("Pregnancy Category C", "USP")]:
        assert got[t]["group"] == g, t
        assert got[t]["basis"].split(":")[0] in {"title_registry", "title_hint", "code"}
    assert got["General"]["group"] == "WP" and got["General"]["basis"].startswith("title_hint")
    assert got["Carcinogenesis"] == {"group": "WP", "basis": "ancestor_default:42232-9",
                                     "flags": ["default:precautions_subsection"]}


def test_uncoded_top_level_title_and_number_fallback():
    d = parse(sec(None, "CONTRAINDICATIONS", p("x")), sec(None, "6 Something Odd", p("y")),
              sec("42229-5", "1 Whatever", p("z")))
    a, b, c = (G.classify(s) for s in d["sections"])
    assert a["group"] == "C" and a["basis"].startswith("title_") and "inferred:title" in a["flags"]
    assert b["group"] == "AR" and b["basis"] == "number:6" and "inferred:number" in b["flags"]
    assert c["group"] is None and c["basis"] == "none"


def test_number_disagreement_flag_and_neutral_code_under_group():
    d = parse(sec("34070-3", "5 CONTRAINDICATIONS", p("x")))
    r = G.classify(d["sections"][0])
    assert r["group"] == "C" and "number_disagrees:5->WP" in r["flags"]
    d = parse(sec("43685-7", "5 W&amp;P", "", kids=[sec("34089-3", "5.9 Desc", p("x"))]))
    r = G.classify(d["sections"][1])
    assert r["group"] == "WP" and r["flags"] == ["neutral_code_under_group:34089-3"]


def test_approx_flag():
    d = parse(sec("34074-5", "Drug/Laboratory Test Interactions", p("x")))
    r = G.classify(d["sections"][0])
    assert r["group"] == "DI" and "approx:code" in r["flags"]


# ------------------------------------------------------------------ alignment

def methods(r):
    return {(x["a"], x["b"]): x["method"] for x in r["alignment"]}


def test_align_by_section_id_even_when_title_changes():
    a = parse(sec("42229-5", "Old title", p("same text here"), sid="aaaa-1"))
    b = parse(sec("42229-5", "Brand new title", p("same text here again"), sid="aaaa-1"), version=2)
    r = cmp(a, b)
    assert methods(r) == {(1, 1): "section_id"} and r["alignment"][0]["confidence"] == 0.99
    assert r["changes"][0]["kind"] == "modified" and r["changes"][0]["from_section_title"] == "Old title"


def test_align_by_match_key_when_ids_change():
    a = parse(sec("34070-3", "4 CONTRAINDICATIONS", p("None."), sid="id-a"))
    b = parse(sec("34070-3", "4 CONTRAINDICATIONS", p("None known."), sid="id-b"), version=2)
    r = cmp(a, b)
    assert methods(r) == {(1, 1): "match_key"}
    assert r["changes"][0]["srlc_group"] == "C"


def test_align_renumbered_and_retitled_and_moved():
    a = parse(
        sec("43685-7", "5 WARNINGS AND PRECAUTIONS", "", kids=[
            sec("42229-5", "5.2 Bleeding", p("bleeding text one two three")),
            sec("42229-5", "5.3 Liver injury", p("liver text four five six"))]),
        sec("34084-4", "6 ADVERSE REACTIONS", p("ar")))
    b = parse(
        sec("43685-7", "5 WARNINGS AND PRECAUTIONS", "", kids=[
            sec("42229-5", "5.3 Bleeding", p("bleeding text one two three")),       # renumbered
            sec("42229-5", "5.2 Hepatic injury", p("liver text four five six x"))]),  # retitled, same number? no: 5.3 -> 5.2
        sec("34084-4", "6 ADVERSE REACTIONS", p("ar")), version=2)
    r = cmp(a, b)
    m = methods(r)
    assert m[(2, 2)] == "title_code"      # Bleeding renumbered
    assert m[(3, 3)] == "number_code" or m[(3, 3)] in {"code_unique", "text_similarity"}
    assert r["formatting_changes"] and r["formatting_changes"][0]["type"] == "heading_only"
    # moved to a different parent
    a = parse(sec("42229-5", "Alpha", p("a a a"), kids=[sec("42228-7", "Pregnancy", p("x y z"))]))
    b = parse(sec("42229-5", "Alpha", p("a a a")), sec("42228-7", "Pregnancy", p("x y z q")), version=2)
    r = cmp(a, b)
    assert methods(r)[(2, 2)] == "title_moved"


def test_insert_delete_and_unmatched_sections():
    a = parse(sec("34070-3", "4 C", p("c")), sec("34084-4", "6 AR", p("old ar text")))
    b = parse(sec("34070-3", "4 C", p("c")), sec("34073-7", "7 DRUG INTERACTIONS", p("new di text")), version=2)
    r = cmp(a, b)
    kinds = {c["section_title"]: c["kind"] for c in r["changes"]}
    assert kinds == {"7 DRUG INTERACTIONS": "added_section", "6 AR": "removed_section"}
    di = [c for c in r["changes"] if c["kind"] == "added_section"][0]
    assert di["srlc_group"] == "DI" and di["added_spans"][0]["text"] == "7 DRUG INTERACTIONS\n\nnew di text"
    assert r["unmatched_from"] == [2] and r["unmatched_to"] == [2]


def test_repeated_codes_with_insertion():
    def three(*ts):
        return [sec("42229-5", "Note", p(t)) for t in ts]
    a = parse(*three("first note text alpha", "second note text beta"))
    b = parse(*three("first note text alpha", "inserted note gamma delta", "second note text beta"), version=2)
    r = cmp(a, b)
    m = {(x["a"], x["b"]) for x in r["alignment"]}
    assert (1, 1) in m and (2, 3) in m
    assert any(c["kind"] == "added_section" and "gamma" in c["added_spans"][0]["text"] for c in r["changes"]) or \
        any(c["kind"] == "modified" for c in r["changes"])
    assert all("repeated_key" in x["flags"] for x in r["alignment"])


def test_reordering_is_flagged():
    a = parse(sec("34070-3", "4 C", p("c text")), sec("34084-4", "6 AR", p("ar text")),
              sec("34073-7", "7 DI", p("di text")))
    b = parse(sec("34073-7", "7 DI", p("di text")), sec("34070-3", "4 C", p("c text")),
              sec("34084-4", "6 AR", p("ar text")), version=2)
    r = cmp(a, b)
    assert [x["flags"] for x in r["alignment"] if "reordered" in x["flags"]]
    assert r["changes"] == []


def test_text_similarity_last_resort_is_flagged():
    long = " ".join(f"word{i}" for i in range(60))
    a = parse(sec(None, "Alpha heading", p(long)))
    b = parse(sec(None, "Totally different heading", p(long + " extra")), version=2)
    r = cmp(a, b)
    al = r["alignment"][0]
    assert al["method"] == "text_similarity" and "fuzzy" in al["flags"] and al["confidence"] < 0.7


def test_split_detected():
    para = [" ".join(f"w{j}x{i}" for j in range(30)) for i in range(6)]
    a = parse(sec("34071-1", "WARNINGS", p(*para)))
    b = parse(sec("42229-5", "Part one", p(para[0], para[1])), sec("42229-5", "Part two", p(para[2], para[3])),
              sec("42229-5", "Part three", p(para[4], para[5])), version=2)
    r = cmp(a, b)
    assert r["splits_merges"] and r["splits_merges"][0]["kind"] == "split"
    assert {c["kind"] for c in r["changes"]} == {"added_section", "removed_section"}


# ------------------------------------------------------------------ text diff

def test_added_removed_spans_verbatim_with_context():
    a = parse(sec("43685-7", "5 W&amp;P", p("Monitor patients. Bleeding can occur in 5 mg users. Stop if rash appears.")))
    b = parse(sec("43685-7", "5 W&amp;P", p("Monitor patients. Serious bleeding can occur in 10 mg users. Stop if rash appears.")),
              version=2)
    c = cmp(a, b)["changes"][0]
    assert c["kind"] == "modified" and c["srlc_group"] == "WP"
    texts_a = [s["text"] for s in c["added_spans"]]
    texts_r = [s["text"] for s in c["removed_spans"]]
    assert texts_a == ["Serious bleeding", "10"]
    assert texts_r == ["Bleeding", "5"]
    for s in c["added_spans"]:
        assert s["context"] == "Serious bleeding can occur in 10 mg users."
        assert "Stop if rash" not in s["context"] and s["text"] in s["context"]
    for s in c["removed_spans"]:
        assert s["text"] in s["context"]


def test_span_offsets_slice_channel_text():
    a = parse(sec("34084-4", "6 AR", p("one two three four five")))
    b = parse(sec("34084-4", "6 AR", p("one two NEW WORDS three four five six")), version=2)
    c = cmp(a, b)["changes"][0]
    chan_b = C.channels(b["sections"][0])["text"]
    for s in c["added_spans"]:
        assert chan_b[s["char_start"]:s["char_end"]] == s["text"]
    assert [s["text"] for s in c["added_spans"]] == ["NEW WORDS", "six"]
    assert c["removed_spans"] == []


def test_moved_text_reported_separately_and_kept_in_added_removed():
    moved = "This sentence moved from warnings to precautions verbatim."
    a = parse(sec("34071-1", "WARNINGS", p("Intro one.", moved)), sec("42232-9", "PRECAUTIONS", p("Other text.")))
    b = parse(sec("34071-1", "WARNINGS", p("Intro one.")), sec("42232-9", "PRECAUTIONS", p("Other text.", moved)),
              version=2)
    r = cmp(a, b)
    rm = [c for c in r["changes"] if c["removed_spans"]][0]
    assert rm["moved_spans"] and rm["moved_spans"][0]["text"] == moved
    assert rm["moved_spans"][0]["to_section_key"] == [c for c in r["changes"] if c["added_spans"]][0]["section_key"]
    assert any(s["text"] == moved for c in r["changes"] for s in c["added_spans"])


def test_whitespace_only_is_formatting():
    a = parse(sec("34084-4", "6 AR", p("alpha beta gamma delta")))
    b = parse(sec("34084-4", "6 AR", p("alpha  beta\n   gamma delta")), version=2)
    r = cmp(a, b)
    assert r["changes"] == []
    assert [f["type"] for f in r["formatting_changes"]] == ["text:whitespace_only"]


def test_normalization_only_is_formatting():
    a = parse(sec("34084-4", "6 AR", p("patient’s dose 5 – 10 mg")))
    b = parse(sec("34084-4", "6 AR", p("patient's dose 5 - 10 mg")), version=2)
    r = cmp(a, b)
    assert r["changes"] == []
    assert r["formatting_changes"][0]["type"] == "text:normalization_only"
    assert r["formatting_changes"][0]["formatting_token_diffs"] >= 1


def test_formatting_tokens_inside_substantive_change_are_counted():
    a = parse(sec("34084-4", "6 AR", p("patient’s dose old value")))
    b = parse(sec("34084-4", "6 AR", p("patient's dose new value")), version=2)
    c = cmp(a, b)["changes"][0]
    assert c["formatting_token_diffs"] == 1
    assert [s["text"] for s in c["added_spans"]] == ["new"]


def test_empty_sections_added_removed_are_formatting_not_changes():
    a = parse(sec("34084-4", "6 AR", p("x")))
    b = parse(sec("34084-4", "6 AR", p("x")), sec("34073-7", None, ""), version=2)
    r = cmp(a, b)
    assert r["changes"] == []
    assert r["formatting_changes"][0]["type"] == "empty_added_section"


def test_table_channel_and_table_changed():
    def tb(v):
        return f"<table><thead><tr><th>Dose</th></tr></thead><tbody><tr><td>{v}</td></tr></tbody></table>"
    a = parse(sec("34068-7", "2 DOSAGE", p("text"), table=tb("5 mg")))
    b = parse(sec("34068-7", "2 DOSAGE", p("text"), table=tb("10 mg")), version=2)
    c = cmp(a, b)["changes"][0]
    assert c["table_changed"] is True
    assert {s["channel"] for s in c["added_spans"]} == {"table"}
    assert [s["text"] for s in c["added_spans"]] == ["10"] and c["removed_spans"][0]["text"] == "5"


def test_unchanged_pair_has_empty_changes_and_not_comparable_cases():
    a = parse(sec("34084-4", "6 AR", p("same")))
    b = parse(sec("34084-4", "6 AR", p("same")), version=2)
    r = cmp(a, b)
    assert r["changes"] == [] and r["formatting_changes"] == [] and r["comparison"] == "ok"
    empty = P.parse(doc(""))
    r = cmp(a, empty)
    assert r["comparison"] == "not_comparable" and r["changes"] == []


def test_record_fields_and_determinism():
    a = P.parse(doc(body(sec("34084-4", "6 AR", p("alpha beta"))), version=1, eff="20230101"))
    b = P.parse(doc(body(sec("34084-4", "6 AR", p("alpha gamma"))), version=2, eff="20240101"))
    r1 = C.compare_docs(a, b, "ZA", "ZB")
    r2 = C.compare_docs(P.parse(doc(body(sec("34084-4", "6 AR", p("alpha beta"))), 1, "20230101")),
                        P.parse(doc(body(sec("34084-4", "6 AR", p("alpha gamma"))), 2, "20240101")), "ZA", "ZB")
    assert C.canonical_json(r1) == C.canonical_json(r2)
    assert r1["setid"] == SID and (r1["from_version"], r1["to_version"]) == (1, 2)
    assert (r1["from_effective_time"], r1["to_effective_time"]) == ("20230101", "20240101")
    assert r1["from_artifact_sha256"] == "ZA" and r1["to_source_sha256"] == b["source_sha256"]
    assert r1["parser_version"] == P.PARSER_VERSION
    assert r1["from_parser_output_sha256"] == a["content_sha256"]
    ch = r1["changes"][0]
    for k in ("srlc_group", "group_basis", "section_number", "section_title", "section_key", "alignment_method",
              "kind", "added_spans", "removed_spans", "moved_spans", "table_changed"):
        assert k in ch
    assert r1["record_sha256"] == C.record_hash(r1)
    json.loads(C.canonical_json(r1))


# ------------------------------------------------------------------ real pairs

def load(name):
    return P.parse((FIX / f"{name}.xml").read_bytes())


@pytest.mark.parametrize("name,groups", [
    ("plr_nda", {"USP", "MG"}),  # 0.3.0: the section 5 change (bullet glyphs only) is formatting
    ("nonplr_anda", {"WP", "AR"}),
    ("bw_anda", {"AR"}),  # 0.3.0: the BW (list numbering) and WP (sup/sub spacing) edits are formatting
])
def test_real_pairs(name, groups):
    a, b = load(f"{name}_a"), load(f"{name}_b")
    r = C.compare_docs(a, b)
    assert r["comparison"] == "ok"
    assert groups <= {c["srlc_group"] for c in r["changes"]}
    assert C.canonical_json(r) == C.canonical_json(C.compare_docs(load(f"{name}_a"), load(f"{name}_b")))
    sa = {s["ordinal"]: C.channels(s) for s in a["sections"]}
    sb = {s["ordinal"]: C.channels(s) for s in b["sections"]}
    for c in r["changes"]:
        for s in c["added_spans"]:
            if c["kind"] == "modified":
                assert sb[c["ordinal_to"]][s["channel"]][s["char_start"]:s["char_end"]] == s["text"]
        for s in c["removed_spans"]:
            if c["kind"] == "modified":
                assert sa[c["ordinal_from"]][s["channel"]][s["char_start"]:s["char_end"]] == s["text"]
        assert c["kind"] != "modified" or c["added_spans"] or c["removed_spans"]
    # identity pair has no changes
    assert C.compare_docs(a, a)["changes"] == []


def test_real_pair_bw_anda_noise_edits_are_formatting_not_changes():
    r = C.compare_docs(load("bw_anda_a"), load("bw_anda_b"))
    noise = {(c["srlc_group"], c["type"]) for c in r["formatting_changes"]}
    assert {("BW", "text:noise_only"), ("WP", "text:noise_only")} <= noise
    assert "BW" not in {c["srlc_group"] for c in r["changes"]}


def test_real_pair_plr_section_numbers_and_titles():
    r = C.compare_docs(load("plr_nda_a"), load("plr_nda_b"))
    by = {c["section_number"]: c for c in r["changes"] if c["section_number"]}
    assert by["8.1"]["srlc_group"] == "USP" and by["8.1"]["section_title"].endswith("Pregnancy")
    assert "5" not in by  # bullet-glyph-only excerpt edit: formatting since srlc-changes-0.3.0
    fm = [c for c in r["formatting_changes"] if c["section_number"] == "5"]
    assert fm and fm[0]["srlc_group"] == "WP" and fm[0]["type"] == "excerpt:noise_only"
    assert by["17.4"]["srlc_group"] == "MG" and by["17.4"]["group_basis"] == "hierarchy:number:17.4"


def test_real_pair_nonplr_groups_via_codes():
    r = C.compare_docs(load("nonplr_anda_a"), load("nonplr_anda_b"))
    bases = {c["section_title"]: (c["srlc_group"], c["group_basis"]) for c in r["changes"]}
    assert bases["Drug/Laboratory Test Interactions"] == ("WP", "hierarchy:nonplr_warnings_precautions")
    assert all(c["topic_group"] == "DI" for c in r["changes"] if c["section_title"] == "Drug/Laboratory Test Interactions")
    assert bases["Gastrointestinal"][0] == "AR"
    assert any(c["kind"] == "added_section" for c in r["changes"])


# ------------------------------------------------------------------ hierarchy groups (srlc-groups-0.2.0)

def gd(d):
    return {s["title_normalized"]: v for s, v in ((s, G.classify_doc(d)[s["ordinal"]]) for s in d["sections"])}


def test_nonplr_precautions_and_warnings_subsections_are_wp():
    pre = sec("42232-9", "PRECAUTIONS", p("intro"), kids=[
        sec("42229-5", "Drug Interactions", p("d")), sec("42229-5", "Pediatric Use", p("c")),
        sec("42229-5", "Nursing Mothers", p("n")), sec("34076-0", "Information for Patients", p("i")),
        sec("42229-5", "Pregnancy", p("p"), kids=[sec("34077-8", "Teratogenic Effects", p("t")),
                                                 sec(None, None, p("deep unnamed"))])])
    war = sec("34071-1", "WARNINGS", p("w"), kids=[sec("42229-5", "Geriatric Use", p("g"))])
    ar = sec("34084-4", "ADVERSE REACTIONS", p("a"), kids=[sec("42229-5", "Pregnancy", p("pp"))])
    d = parse(war, pre, ar, sec("34073-7", "DRUG INTERACTIONS", p("top-level di")))
    assert not G.is_plr(d)
    secs = d["sections"]
    for s in secs[:-3]:
        r = G.classify_doc(d)[s["ordinal"]]
        assert r["group"] == "WP", s["title_normalized"]
    ids = {s["title_normalized"]: s["ordinal"] for s in secs}
    di = G.classify_doc(d)[ids["Drug Interactions"]]
    assert di["topic_group"] == "DI" and di["group"] == "WP" and di["alt_group"] == "DI"
    assert G.classify_doc(d)[ids["Pediatric Use"]]["alt_group"] == "USP"
    assert G.classify_doc(d)[secs[-1]["ordinal"]]["group"] == "DI"           # top-level DI stays DI
    assert G.classify_doc(d)[secs[-2]["ordinal"]]["group"] == "AR"           # child of ADVERSE REACTIONS: ancestor


def test_plr_number_hierarchy_beats_title_and_code():
    d = parse(sec("34070-3", "4 CONTRAINDICATIONS", p("c")),
              sec("43685-7", "5 WARNINGS AND PRECAUTIONS", "", kids=[
                  sec("42228-7", "5.11 Teratogenicity and Other Harm to the Newborn", p("t"),
                      kids=[sec(None, None, p("untitled child")), sec("34077-8", "Teratogenic Effects", p("x"))]),
                  sec("42229-5", "5.12 Drug Interactions", p("x"))]),
              sec("34084-4", "6 ADVERSE REACTIONS", p("a"), kids=[sec("42229-5", "6.1 Pregnancy outcomes", p("b"))]),
              sec("34073-7", "7 DRUG INTERACTIONS", p("d"), kids=[sec("42229-5", "7.1 Pediatric Use", p("e"))]),
              sec("43684-0", "8 USE IN SPECIFIC POPULATIONS", "", kids=[sec("42229-5", "8.1 Warnings", p("f"))]),
              sec("88436-1", "17 PATIENT COUNSELING INFORMATION", p("g"), kids=[sec("42229-5", "17.1 Bleeding", p("h"))]))
    assert G.is_plr(d)
    g = gd(d)
    for t, grp in [("5.11 Teratogenicity and Other Harm to the Newborn", "WP"), ("5.12 Drug Interactions", "WP"),
                   ("6.1 Pregnancy outcomes", "AR"), ("7.1 Pediatric Use", "DI"), ("8.1 Warnings", "USP"),
                   ("17.1 Bleeding", "MG"), ("4 CONTRAINDICATIONS", "C"), ("Teratogenic Effects", "WP")]:
        assert g[t]["group"] == grp, t
    assert g["5.11 Teratogenicity and Other Harm to the Newborn"]["topic_group"] == "USP"
    # unnamed child inherits the parent's group
    unnamed = [s for s in d["sections"] if s["title_normalized"] is None][0]
    r = G.classify_doc(d)[unnamed["ordinal"]]
    assert r["group"] == "WP" and r["basis"] == "hierarchy:parent"


def test_boxed_warning_and_plr_detection():
    d = parse(sec("34066-1", "WARNING: X", p("a"), kids=[sec(None, None, p("b"))]))
    assert not G.is_plr(d)
    assert [v["group"] for v in G.classify_doc(d).values()] == ["BW", "BW"]
    assert G.is_plr(parse(sec(None, "5 WARNINGS AND PRECAUTIONS", p("x"))))


def test_title_added_and_title_change_are_reported_as_added_text():
    a = parse(sec("43685-7", "5 WARNINGS AND PRECAUTIONS", "", kids=[sec("42229-5", "5.1 Bleeding", p("same body text"))]))
    b = parse(sec("43685-7", "5 WARNINGS AND PRECAUTIONS", "", kids=[
        sec("42229-5", "5.1 Bleeding", p("same body text")),
        sec("42229-5", "5.2 Endocrine Disorders: Pseudoaldosteronism", p("new body"))]), version=2)
    r = cmp(a, b)
    c = [x for x in r["changes"] if x["kind"] == "added_section"][0]
    assert c["added_spans"][0]["text"].startswith("5.2 Endocrine Disorders: Pseudoaldosteronism")
    assert c["srlc_group"] == "WP"
    # title-only (container) section added
    b2 = parse(sec("43685-7", "5 WARNINGS AND PRECAUTIONS", "", kids=[
        sec("42229-5", "5.1 Bleeding", p("same body text")), sec("42229-5", "5.2 Effects of X on Leucovorin", "")]),
        version=2)
    c2 = [x for x in cmp(a, b2)["changes"] if x["kind"] == "added_section"][0]
    assert c2["added_spans"][0]["text"] == "5.2 Effects of X on Leucovorin"
    # reworded title with equal body: substantive, title tokens are added
    b3 = parse(sec("43685-7", "5 WARNINGS AND PRECAUTIONS", "", kids=[
        sec("42229-5", "5.1 Hemorrhage and Bleeding", p("same body text"), )]), version=2)
    r3 = cmp(a, b3)
    assert r3["changes"] and "Hemorrhage" in r3["changes"][0]["added_spans"][0]["text"]
    chan = C.channels(b3["sections"][1])["text"]
    for s in r3["changes"][0]["added_spans"]:
        assert chan[s["char_start"]:s["char_end"]] == s["text"]
    # number-only or case-only title change with equal body stays formatting
    b4 = parse(sec("43685-7", "5 WARNINGS AND PRECAUTIONS", "", kids=[
        sec("42229-5", "5.2 BLEEDING", p("same body text"))]), version=2)
    r4 = cmp(a, b4)
    assert r4["changes"] == [] and r4["formatting_changes"][0]["type"] == "heading_only"


def test_title_unchanged_offsets_after_body_edit_with_renumber():
    a = parse(sec("42229-5", "5.1 Bleeding", p("one two three")))
    b = parse(sec("42229-5", "5.2 Bleeding", p("one two FOUR three")), version=2)
    c = cmp(a, b)["changes"][0]
    assert [s["text"] for s in c["added_spans"]] == ["FOUR"]
    chan = C.channels(b["sections"][0])["text"]
    assert chan[c["added_spans"][0]["char_start"]:c["added_spans"][0]["char_end"]] == "FOUR"


# ------------------------------------------------------------------ uncoded boxed warnings (srlc-groups-0.3.0)

BOX = '<table width="100%"><tbody><tr><td>WARNING: RISKS FROM CONCOMITANT USE WITH OPIOIDS; ABUSE, MISUSE</td></tr></tbody></table>'


def gb(d):
    g = G.classify_doc(d)
    return [(s["ordinal"], s["code"], s["title_normalized"], g[s["ordinal"]]["group"], g[s["ordinal"]]["basis"])
            for s in d["sections"]]


def test_uncoded_box_table_before_major_section_is_bw():
    d = parse(sec("42229-5", "", p("Ativan (lorazepam) Tablets"), table=BOX),
              sec("34089-3", "DESCRIPTION", p("d")), sec("34067-9", "INDICATIONS AND USAGE", p("i")))
    rows = gb(d)
    assert rows[0][3:] == ("BW", "uncoded_box:table")
    assert rows[1][3] is None and rows[2][3] is None
    assert G.classify_doc(d)[d["sections"][0]["ordinal"]]["flags"] == ["inferred:uncoded_box"]
    assert G.uncoded_box_sections(d) == {d["sections"][0]["ordinal"]: "table"}


def test_uncoded_box_title_and_lead_variants():
    d = parse(sec("42229-5", "WARNING: SUICIDALITY AND ANTIDEPRESSANT DRUGS", p("x")),
              sec(None, None, p("WARNING: SEVERE ALLERGIC REACTIONS", "more")),
              sec("42229-5", "WARNING", p("x")),
              sec("34067-9", "INDICATIONS AND USAGE", p("i")))
    assert [r[3:] for r in gb(d)[:3]] == [("BW", "uncoded_box:title"), ("BW", "uncoded_box:lead"),
                                         ("BW", "uncoded_box:title")]


def test_cross_references_and_major_sections_are_not_uncoded_boxes():
    d = parse(sec("42229-5", "", p("SEE BOXED WARNING AT THE START OF THE LABEL")),
              sec("42229-5", "WARNINGS", p("w")),
              sec("42229-5", "Warning", p("w")),
              sec("42229-5", "WARNING AND PRECAUTIONS", p("w")),
              sec("42229-5", "WARNING SIGNALS", p("w")),
              sec("42229-5", "", p("x"), table='<table><tbody><tr><td>SEE BOXED WARNING</td></tr></tbody></table>'),
              sec("42229-5", "", p("x"), table='<table><tbody><tr><td>Warning: lower case is not a box</td></tr></tbody></table>'),
              sec("34067-9", "INDICATIONS AND USAGE", p("i")))
    assert G.uncoded_box_sections(d) == {}
    assert all(r[3] != "BW" for r in gb(d))


def test_uncoded_box_needs_position_before_first_major_and_no_code():
    after = parse(sec("34067-9", "INDICATIONS AND USAGE", p("i")), sec("42229-5", "", p("x"), table=BOX))
    assert G.uncoded_box_sections(after) == {}
    # uncoded section titled like a major section ends the front matter
    after2 = parse(sec("42229-5", "DESCRIPTION", p("d")), sec("42229-5", "WARNING: X", p("x")))
    assert G.uncoded_box_sections(after2) == {}
    # recent major changes (43683-2) and other coded sections are never boxes; nested sections are skipped
    coded = parse(sec("43683-2", "RECENT MAJOR CHANGES", p("WARNING: X")), sec("42229-5", "", p("x"), table=BOX))
    assert G.uncoded_box_sections(coded) == {}
    nested = parse(sec("48780-1", None, "", kids=[sec("42229-5", "WARNING: X", p("x"))]),
                   sec("34067-9", "INDICATIONS AND USAGE", p("i")))
    assert G.uncoded_box_sections(nested) == {}
    # a coded box anywhere in the label: WARNING-led uncoded sections (headers, highlights) are not boxes
    both = parse(sec("42229-5", "", p("x"), table=BOX), sec("34066-1", "WARNING: X", p("x")),
                 sec("34067-9", "INDICATIONS AND USAGE", p("i")))
    assert G.uncoded_box_sections(both) == {}
    hl = parse(sec("42229-5", "HIGHLIGHTS OF PRESCRIBING INFORMATION", p("h"), table=BOX),
               sec("34067-9", "INDICATIONS AND USAGE", p("i")))
    assert G.uncoded_box_sections(hl) == {}
    # a properly coded box is untouched
    d = parse(sec("34066-1", "WARNING: X", p("x")), sec("34067-9", "INDICATIONS AND USAGE", p("i")))
    assert gb(d)[0][3:] == ("BW", "code:34066-1")


def test_uncoded_box_change_record_is_bw_and_formatting_noise_helper():
    a = parse(sec("34066-1", "WARNING: RISKS", p("old text")), sec("34067-9", "INDICATIONS AND USAGE", p("i")))
    b = parse(sec("42229-5", "", p("Ativan"), table=BOX), sec("34067-9", "INDICATIONS AND USAGE", p("i")), version=2)
    r = cmp(a, b)
    assert {"BW"} <= {c["srlc_group"] for c in r["changes"]}


# ------------------------------------------------------------------ formatting noise (srlc-changes-0.3.0)

def one(text_a, text_b, ch="text"):
    return C.diff_channel(text_a, text_b, 0, 0, ch)


@pytest.mark.parametrize("a,b", [
    ("Avoid use [see Warnings and Precautions (5.1)].", "Avoid use [see Warnings and Precautions ( 5.1) ]."),
    ("Hepatotoxicity (5.1) occurs.", "Hepatotoxicity ( 5.1) occurs."),
    ("SPORANOX ® capsules", "SPORANOX® capsules"), ("Drug™ daily", "Drug daily"), ("© 2020 Acme", "2020 Acme"),
    ("• Alpha\n• Beta", "Alpha\nBeta"), ("1. Alpha\n2. Beta", "Alpha\nBeta"), ("a) Alpha\nb) Beta", "Alpha\nBeta"),
    ("Alpha [FOOTNOTE:abc] beta", "Alpha [FOOTNOTE:xyz] beta"), ("Alpha beta", "Alpha [FOOTNOTE:xyz] beta"),
    ("Body.\nRevised: 06/2020", "Body.\nRevised: 11/2021"), ("Body.", "Body.\nRevised: June 2020"),
    ("Body.\nRep by X\n10008909/09", "Body.\nRep by X\n10008909/10"), ("Body.\n06/2020", "Body.\n11/2021"),
])
def test_noise_only_changes_are_formatting(a, b):
    d = one(a, b)
    assert d["kind"] == "noise_only" and d["added"] == [] and d["removed"] == []


@pytest.mark.parametrize("a,b,add,rem", [
    ("Take 5 mg daily.", "Take 10 mg daily.", ["10"], ["5"]),
    ("Rate was 5.1% overall.", "Rate was 5.2% overall.", ["5.2%"], ["5.1%"]),
    ("Patients aged 65 (12) years", "Patients aged 65 (13) years", ["(13)"], ["(12)"]),
    ("Mean 2.3 (1.5) units", "Mean 2.3 (1.6) units", ["(1.6)"], ["(1.5)"]),
    ("Use [see Contraindications (4)].", "Use [see Warnings and Precautions (5.1)].", ["Warnings and Precautions (5.1)]."],
     ["Contraindications (4)]."]),
    ("Brandex tablets", "generic tablets", ["generic"], ["Brandex"]),
    ("Take 5 mg (see Table 3)", "Take 5 mg (see Table 4)", ["4)"], ["3)"]),
])
def test_clinical_numbers_and_words_stay_substantive(a, b, add, rem):
    d = one(a, b)
    assert d["kind"] == "substantive"
    assert [x["text"] for x in d["added"]] == add and [x["text"] for x in d["removed"]] == rem


def test_table_numbers_in_parentheses_are_never_noise_and_bare_xref_only_in_excerpt():
    assert one("Cell\t(0.5)\tx", "Cell\t(0.6)\tx", "table")["kind"] == "substantive"
    assert one("Bleeding (5.1)", "Bleeding (5.4)", "excerpt")["reference_only"] is True
    assert one("Bleeding (4)", "Bleeding (5)", "excerpt")["reference_only"] is True
    d = one("Bleeding (4)", "Bleeding (5)", "text")
    assert d["kind"] == "substantive" and d["reference_only"] is False


def test_noise_tokens_are_excluded_from_added_spans_and_text_stays_verbatim():
    a = "Avoid use ( 5.1) of the drug ®"
    b = "Avoid use (5.1, 5.2) of the new drug® now [FOOTNOTE:f1]"
    d = one(a, b)
    assert d["kind"] == "substantive"
    assert [x["text"] for x in d["added"]] == ["(5.1, 5.2)", "new", "now"] and d["reference_only"] is False
    assert [x["text"] for x in d["removed"]] == ["( 5.1)"]
    for x in d["added"]:
        assert b[x["char_start"]:x["char_end"]] == x["text"]


def test_noise_only_section_change_is_listed_as_formatting_not_change():
    a = parse(sec("34084-4", "ADVERSE REACTIONS", p("Rash [see Warnings and Precautions (5.1)].  Revised: 06/2020")))
    b = parse(sec("34084-4", "ADVERSE REACTIONS", p("Rash [see Warnings and Precautions (5.3)].  Revised: 09/2021")), version=2)
    r = cmp(a, b)
    assert len(r["changes"]) == 1 and r["changes"][0]["reference_only"] is True   # xref renumbering only
    assert [s["text"] for s in r["changes"][0]["added_spans"]] == ["(5.3)]."]
    assert r["changes_version"] == "srlc-changes-0.3.2"
    pure = parse(sec("34084-4", "ADVERSE REACTIONS", p("Rash [see Warnings and Precautions (5.1)].  Revised: 09/2021")), version=2)
    r1 = cmp(a, pure)
    assert r1["changes"] == [] and r1["formatting_changes"][0]["type"] == "text:noise_only"
    c = parse(sec("34084-4", "ADVERSE REACTIONS", p("Rash 10 mg [see Warnings and Precautions (5.3)].  Revised: 09/2021")), version=2)
    r2 = cmp(a, c)
    assert [s["text"] for s in r2["changes"][0]["added_spans"]] == ["10 mg"] or "10" in r2["changes"][0]["added_spans"][0]["text"]


@pytest.mark.parametrize("a,b", [
    ("See [see Human Data].  Next", "See [see Human Data] .  Next"),
    ("Rash [see Warnings (5.3)]. Next", "Rash [see Warnings (5.3)] . Next"),
    ("Section 4.2Colitis/Enteritis", "Section 4.2 Colitis/Enteritis"), ("Take 5 mg", "Take 5mg"),
    ("Mg. sentence. (7.1) end", "Mg. sentence. ( 7.1) end"),
])
def test_spacing_only_and_sentence_final_xref_are_noise(a, b):
    assert one(a, b)["kind"] == "noise_only"


@pytest.mark.parametrize("a,b", [("Take 1 0 tablets", "Take 10 tablets"), ("Dose 5. 5 tablets", "Dose 5.5 tablets"),
                                 ("Rate 5% (12) overall", "Rate 5% (13) overall")])
def test_spacing_rule_never_merges_numbers(a, b):
    assert one(a, b)["kind"] == "substantive"


def test_reference_only_classification_and_mixed_case():
    d = one("Avoid [see Warnings and Precautions (5.1)].", "Avoid [see Warnings and Precautions (5.2)].")
    assert d["kind"] == "substantive" and d["reference_only"] is True
    assert [x["text"] for x in d["added"]] == ["(5.2)]."]
    m = one("Avoid [see Warnings and Precautions (5.1)].", "Avoid use [see Warnings and Precautions (5.2)].")
    assert m["kind"] == "substantive" and m["reference_only"] is False
    dose = one("Take 5 mg (5.1)", "Take 10 mg (5.1)")
    assert dose["reference_only"] is False and [x["text"] for x in dose["added"]] == ["10"]
    # other noise next to a cross-reference edit is still ignored in the spans
    both = one("Avoid ® (5.1) now", "Avoid (5.2) now")
    assert both["reference_only"] is True and [x["text"] for x in both["added"]] == ["(5.2)"]
    a = parse(sec("34084-4", "AR", p("Rash [see Warnings and Precautions (5.1)]. Dose 5 mg")))
    b = parse(sec("34084-4", "AR", p("Rash [see Warnings and Precautions (5.2)]. Dose 10 mg")), version=2)
    assert cmp(a, b)["changes"][0]["reference_only"] is False
