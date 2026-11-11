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
    assert di["srlc_group"] == "DI" and di["added_spans"][0]["text"] == "new di text"
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
    b = parse(sec("34084-4", "6 AR", p("x")), sec("34073-7", "7 DI", ""), version=2)
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
    ("plr_nda", {"WP", "USP", "MG"}),
    ("nonplr_anda", {"DI", "AR"}),
    ("bw_anda", {"BW", "WP", "AR"}),
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


def test_real_pair_plr_section_numbers_and_titles():
    r = C.compare_docs(load("plr_nda_a"), load("plr_nda_b"))
    by = {c["section_number"]: c for c in r["changes"] if c["section_number"]}
    assert by["8.1"]["srlc_group"] == "USP" and by["8.1"]["section_title"].endswith("Pregnancy")
    assert by["5"]["srlc_group"] == "WP"
    assert by["17.4"]["srlc_group"] == "MG" and by["17.4"]["group_basis"] == "ancestor:34076-0"


def test_real_pair_nonplr_groups_via_codes():
    r = C.compare_docs(load("nonplr_anda_a"), load("nonplr_anda_b"))
    bases = {c["section_title"]: (c["srlc_group"], c["group_basis"]) for c in r["changes"]}
    assert bases["Drug/Laboratory Test Interactions"] == ("DI", "code:34074-5")
    assert bases["Gastrointestinal"][0] == "AR"
    assert any(c["kind"] == "added_section" for c in r["changes"])
