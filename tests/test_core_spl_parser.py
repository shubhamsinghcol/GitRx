"""Tests for gitrx_core.spl_parser (spl-sections-0.2.0): statuses, identity, buckets, invariants."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from gitrx_core import spl_parser as P

FIX = Path(__file__).parent / "fixtures" / "core_spl"
ROOT = Path(__file__).resolve().parents[1]
SID = "11111111-2222-3333-4444-555555555555"


def doc(body, code="34391-3", name="HUMAN PRESCRIPTION DRUG LABEL", head_title="Test label"):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<document xmlns="urn:hl7-org:v3" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<id root="99999999-0000-0000-0000-000000000000"/>
<code code="{code}" codeSystem="2.16.840.1.113883.6.1" displayName="{name}"/>
<title>{head_title}</title>
<effectiveTime value="20240131"/>
<setId root="{SID.upper()}"/>
<versionNumber value="7"/>
{body}
</document>""".encode("utf-8")


def structured(*sections):
    inner = "".join(f"<component>{s}</component>" for s in sections)
    return f"<component><structuredBody>{inner}</structuredBody></component>"


def sec(code, title, text="", extra="", children="", sid=None, codesystem="2.16.840.1.113883.6.1"):
    code_xml = f'<code code="{code}" codeSystem="{codesystem}" displayName="X"/>' if code else ""
    id_xml = f'<id root="{sid}"/>' if sid else ""
    t = f"<title>{title}</title>" if title is not None else ""
    body = f"<text>{text}</text>" if text is not None else ""
    kids = "".join(f"<component>{c}</component>" for c in children) if children else ""
    return f"<section>{id_xml}{code_xml}{t}{body}{extra}{kids}</section>"


def parsed(xml):
    return P.parse(xml)


GOOD = structured(sec("34067-9", "1 INDICATIONS &amp; USAGE", "<paragraph>Use for pain.</paragraph>", sid="aaaa"))


# ---------------------------------------------------------------- statuses

def test_complete():
    d = parsed(doc(GOOD))
    assert d["status"] == "complete" and d["status_reasons"] == []
    assert d["setid"] == SID and d["version"] == 7 and d["effective_time"] == "20240131"
    assert d["document_type"]["code"] == "34391-3"
    s = d["sections"][0]
    assert s["state"] == "ok" and s["narrative"]["own_text"] == "Use for pain."


def test_complete_with_source_gaps():
    body = structured(sec("34067-9", "INDICATIONS", "<paragraph>x</paragraph>"), sec("42228-7", "PREGNANCY", None),
                      sec("42229-5", "Empty", ""))
    d = parsed(doc(body))
    assert d["status"] == "complete_with_source_gaps"
    assert [s["state"] for s in d["sections"]] == ["ok", "empty_in_source", "empty_in_source"]
    codes = {x["code"] for x in d["sections"][1]["diagnostics"]} | {x["code"] for x in d["sections"][2]["diagnostics"]}
    assert codes == {"no_text_element", "empty_text_element"}  # diagnostics are info, not defects
    assert all(x["severity"] == "info" for x in d["diagnostics"])


def test_nbsp_only_text_is_empty():
    d = parsed(doc(structured(sec("34067-9", "T", "<paragraph>  </paragraph>"))))
    assert d["sections"][0]["state"] == "empty_in_source"


def test_placeholder_and_container_and_data_sections_are_ok():
    data = "<subject><manufacturedProduct><manufacturedProduct><name>Drug</name></manufacturedProduct></manufacturedProduct></subject>"
    inner = sec("34084-4", "AR", "<paragraph>a</paragraph>")
    body = structured(sec("48780-1", None, ""), sec("42229-5", "Container", None, children=[inner]),
                      sec("48780-1", None, None, extra=data))
    d = parsed(doc(body))
    assert [s["state"] for s in d["sections"]] == ["ok", "ok", "ok", "ok"]
    assert d["status"] == "complete"


def test_identification_incomplete_for_uncoded_narrative():
    d = parsed(doc(structured(sec(None, "Teratogenic Effects", "<paragraph>text</paragraph>"))))
    assert d["status"] == "identification_incomplete"
    s = d["sections"][0]
    assert s["state"] == "uncoded" and s["code"] == "<uncoded>" and s["code_state"] == "uncoded"


def test_non_loinc_code_system_is_uncoded():
    d = parsed(doc(structured(sec("X1", "T", "<paragraph>t</paragraph>", codesystem="1.2.3"))))
    assert d["sections"][0]["state"] == "uncoded"


@pytest.mark.parametrize("text,code", [
    ("<table><tbody></tbody></table>", "table_has_no_rows"),
    ("<paragraph>a</paragraph><weird>b</weird>", "unknown_tag"),
    ('<paragraph>a<footnoteRef IDREF="nope"/></paragraph>', "unresolved_ref"),
    ('<paragraph>a<renderMultiMedia referencedObject="nomedia"/></paragraph>', "unresolved_ref"),
])
def test_extraction_defects(text, code):
    d = parsed(doc(structured(sec("34067-9", "T", text))))
    assert d["status"] == "extraction_defect"
    assert code in d["status_reasons"]
    assert any(x["code"] == code and x["severity"] == "defect" for x in d["diagnostics"])


def test_defect_outranks_identification():
    body = structured(sec(None, "x", "<paragraph>t</paragraph>"), sec("34067-9", "T", "<table><tbody></tbody></table>"))
    assert parsed(doc(body))["status"] == "extraction_defect"


def test_malformed_xml_is_defect_not_exception():
    d = P.parse(b"<document><unclosed>")
    assert d["status"] == "extraction_defect" and d["status_reasons"] == ["xml_error"]
    assert d["content_sha256"]


def test_dtd_is_defect():
    d = P.parse(b'<!DOCTYPE document [<!ENTITY e "x">]><document xmlns="urn:hl7-org:v3">&e;</document>')
    assert d["status"] == "extraction_defect"


def test_not_hl7_document():
    assert P.parse(b"<root/>")["status"] == "extraction_defect"


@pytest.mark.parametrize("body,kind", [
    ("<component><structuredBody/></component>", "empty_structured_body"),
    ("<component><nonXMLBody><text mediaType='application/pdf'/></nonXMLBody></component>", "non_xml_body"),
    ("", "none"),
])
@pytest.mark.parametrize("code", ["72090-4", "75030-7", "86445-4", "34391-3"])
def test_no_label_body(body, kind, code):
    d = parsed(doc(body, code=code))
    assert d["status"] == "no_label_body" and d["body_kind"] == kind and d["sections"] == []
    assert d["document_type"]["code"] == code


def test_bad_input_type():
    with pytest.raises(TypeError):
        P.parse("<document/>")


# ---------------------------------------------------------------- identity

def test_section_identity_fields():
    leaf = sec("34084-4", "5.2 Hepatotoxicity", "<paragraph>liver</paragraph>", sid="leaf-id")
    mid = sec("42229-5", "5 WARNINGS AND PRECAUTIONS", "<paragraph>w</paragraph>", children=[leaf], sid="mid-id")
    top = sec("43685-7", "WARNINGS AND PRECAUTIONS", None, children=[mid])
    d = parsed(doc(structured(top)))
    s = d["sections"][2]
    assert s["ordinal"] == 3 and s["parent_ordinal"] == 2 and s["depth"] == 2
    assert s["code_path"] == ["43685-7", "42229-5"]
    assert s["section_number"] == "5.2" and s["title_normalized"] == "5.2 Hepatotoxicity"
    assert s["section_id"] == {"root": "leaf-id", "extension": None}
    assert s["code"] == "34084-4" and s["display_name"] == "X" and s["code_system"] == "2.16.840.1.113883.6.1"
    assert s["section_key"] == "43685-7|42229-5|34084-4#leaf-id"
    assert d["sections"][0]["parent_ordinal"] is None and d["sections"][0]["code_path"] == []
    assert d["sections"][1]["section_number"] == "5"


def test_section_number_rules():
    assert P.split_section_number("5.2 Foo") == ("5.2", "Foo")
    assert P.split_section_number("8.1. Pregnancy") == ("8.1", "Pregnancy")
    assert P.split_section_number("2010 results") == (None, "2010 results")
    assert P.split_section_number("12") == (None, "12")
    assert P.split_section_number("5.2.1.1 Deep") == ("5.2.1.1", "Deep")


def test_registry_hint_is_inferred_and_never_overwrites_code():
    body = structured(sec("42229-5", "5 WARNINGS AND PRECAUTIONS", "<paragraph>a</paragraph>"),
                      sec("34084-4", "ADVERSE REACTIONS", "<paragraph>a</paragraph>"),
                      sec("42229-5", "BOXED WARNING: Hepatotoxicity", "<paragraph>b</paragraph>"),
                      sec("42229-5", "Random heading", "<paragraph>c</paragraph>"))
    s = parsed(doc(body))["sections"]
    assert s[0]["code"] == "42229-5"
    assert s[0]["registry_hint"] == {"inferred": True, "rule": "registry_name", "loinc": "43685-7", "agrees_with_code": False}
    assert s[1]["registry_hint"]["agrees_with_code"] is True
    assert s[2]["registry_hint"]["loinc"] == "34066-1" and s[2]["code"] == "42229-5"
    assert s[3]["registry_hint"] is None


def test_keys_unique_and_deterministic_for_repeated_sections():
    body = structured(*[sec("42229-5", "Same", "<paragraph>x</paragraph>") for _ in range(3)])
    keys = [s["section_key"] for s in parsed(doc(body))["sections"]]
    assert len(set(keys)) == 3 and keys == [s["section_key"] for s in parsed(doc(body))["sections"]]
    mkeys = [s["match_key"] for s in parsed(doc(body))["sections"]]
    assert len(set(mkeys)) == 3


def test_approvals_and_marketing_category():
    prod = """<subject><manufacturedProduct><manufacturedProduct><code code="0001-0002" codeSystem="2.16.840.1.113883.6.69"/>
    <name>Drugex</name><formCode code="C42998" displayName="TABLET"/></manufacturedProduct>
    <subjectOf><approval><id extension="NDA021234" root="2.16.840.1.113883.3.150"/>
    <code code="C73594" codeSystem="2.16.840.1.113883.3.26.1.1" displayName="NDA"/></approval></subjectOf>
    </manufacturedProduct></subject>"""
    d = parsed(doc(structured(sec("48780-1", None, None, extra=prod))))
    assert d["application_numbers"] == ["NDA021234"]
    p = d["products"][0]
    assert p["name"] == "Drugex" and p["approvals"][0]["category"]["displayName"] == "NDA"
    assert p["product_codes"][0]["code"] == "0001-0002"
    assert d["marketing_categories"][0]["code"] == "C73594"


# ---------------------------------------------------------------- text & buckets

def test_blocks_tables_markers_and_conservation_buckets():
    text = """<paragraph>Alpha<br/>beta <footnoteRef IDREF="f1"/> end</paragraph>
    <list><item>one</item><item>two</item></list>
    <table><caption>Cap</caption><tbody><tr><td>a</td><td>b<footnote ID="f1">fn</footnote></td></tr></tbody></table>
    <paragraph>tail <renderMultiMedia referencedObject="m1"/></paragraph>"""
    media = '<component><observationMedia ID="m1"><text>alt text</text><value mediaType="image/jpeg"><reference value="a.jpg"/></value></observationMedia></component>'
    d = parsed(doc(structured(sec("34067-9", "Title", text, extra=media))))
    s = d["sections"][0]
    assert d["status"] == "complete"
    assert "[FOOTNOTE:f1]" in s["narrative"]["own_text"] and "[MEDIA:m1]" in s["narrative"]["own_text"]
    kinds = [b["kind"] for b in s["narrative"]["blocks"]]
    assert "table_flat" in kinds and "table_cell" in kinds and "table_caption" in kinds
    assert "<CELL>" in s["narrative"]["tables"][0]["flattened_text"]
    assert s["media_text"] == [["text", "alt text"]]
    b = d["buckets"]
    assert b["media_text"] == len("alttext") and b["title"] == len("Title") and b["unassigned"] == 0
    assert d["bucket_total"] == sum(b.values())


def test_product_data_and_unassigned_buckets():
    prod = "<subject><manufacturedProduct><manufacturedProduct><name>Name  X</name></manufacturedProduct></manufacturedProduct></subject>"
    odd = "<author><assignedEntity><representedOrganization><name>Org Inc</name></representedOrganization></assignedEntity></author>"
    d = parsed(doc(structured(sec("48780-1", None, None, extra=prod + odd))))
    s = d["sections"][0]
    assert s["product_data"] == [["name", "Name  X"]]
    assert d["unassigned"] == [{"section": 1, "tag": "name", "text": "Org Inc", "xpath": d["unassigned"][0]["xpath"]}]
    assert d["buckets"]["unassigned"] == len("OrgInc") and d["buckets"]["product_data"] == len("NameX")
    assert d["bucket_total"] == d["buckets"]["unassigned"] + d["buckets"]["product_data"]


def test_excerpt_bucket_and_excerpt_only_kind():
    ex = "<excerpt><highlight><text><paragraph>Highlight text</paragraph></text></highlight></excerpt>"
    d = parsed(doc(structured(sec("43683-2", "RECENT", None, extra=ex))))
    s = d["sections"][0]
    assert s["kind"] == "excerpt_only" and s["excerpts"][0]["own_text"] == "Highlight text"
    assert d["buckets"]["excerpt"] == len("Highlighttext") and s["state"] == "ok"


def test_empty_excerpt_is_empty_in_source():
    ex = "<excerpt><highlight><text/></highlight></excerpt>"
    assert parsed(doc(structured(sec("43683-2", "RECENT", None, extra=ex))))["sections"][0]["state"] == "empty_in_source"


def test_nested_table_and_cell_footnote_order_ok():
    text = ("<table><tbody><tr><td>outer1 <table><tbody><tr><td>inner</td></tr></tbody></table> after</td>"
            "<td>outer2</td></tr></tbody></table><paragraph>post</paragraph>")
    d = parsed(doc(structured(sec("34067-9", "T", text))))
    s = d["sections"][0]
    assert len(s["narrative"]["tables"]) == 2
    assert s["narrative"]["tables"][0]["rows"][0]["cells"][0]["nested_tables"] == [2]
    assert "[NESTED-TABLE]" in s["narrative"]["tables"][0]["flattened_text"]


def test_media_marker_kept_when_render_multimedia_wraps_caption():
    text = ('<paragraph>before</paragraph><renderMultiMedia referencedObject="m1"><caption>Figure 1</caption></renderMultiMedia>'
            "<paragraph>after</paragraph>")
    media = '<component><observationMedia ID="m1"><text>alt</text></observationMedia></component>'
    s = parsed(doc(structured(sec("34067-9", "T", text, extra=media))))["sections"][0]
    assert "[MEDIA:m1]" in s["narrative"]["own_text"] and "Figure 1" in s["narrative"]["own_text"]


def test_soft_hyphen_only_text_conserved():
    d = parsed(doc(structured(sec("34067-9", "T", "<paragraph>­</paragraph><paragraph>real</paragraph>"))))
    assert d["buckets"]["narrative"] == 5


def test_comment_text_not_counted_but_tail_is():
    d = parsed(doc(structured(sec("34067-9", "T", "<paragraph>a<!-- hidden -->b</paragraph>"))))
    assert d["sections"][0]["narrative"]["own_text"] == "ab" and d["buckets"]["narrative"] == 2


def test_section_text_in_document_order_across_nesting():
    child = sec("34084-4", "C", "<paragraph>child</paragraph>")
    body = structured(sec("34067-9", "P", "<paragraph>parent</paragraph>", children=[child]), sec("34071-1", "N", "<paragraph>next</paragraph>"))
    s = parsed(doc(body))["sections"]
    assert [x["narrative"]["own_text"] for x in s] == ["parent", "child", "next"]


# ---------------------------------------------------------------- determinism / hashing

def test_deterministic_and_hash():
    xml = doc(GOOD)
    a, b = P.parse(xml), P.parse(xml)
    assert P.canonical_json(a) == P.canonical_json(b)
    assert a["content_sha256"] == P.content_hash(a) == hashlib.sha256(
        P.canonical_json({k: v for k, v in a.items() if k != "content_sha256"}).encode()).hexdigest()
    assert json.loads(P.canonical_json(a)) == a
    assert a["source_sha256"] == hashlib.sha256(xml).hexdigest()
    assert "\n" not in P.canonical_json(a) and ": " not in P.canonical_json(a).split('"title"')[0]


def test_hash_changes_with_input():
    assert P.parse(doc(GOOD))["content_sha256"] != P.parse(doc(GOOD, head_title="other"))["content_sha256"]


_CHILD = """
import sys, json
sys.path.insert(0, sys.argv[1])
from gitrx_core import spl_parser as P
from pathlib import Path
print(json.dumps({p.name: P.parse(p.read_bytes())["content_sha256"] for p in sorted(Path(sys.argv[2]).glob("real_*.xml"))}))
"""


def test_determinism_across_processes_and_hash_seeds():
    outs = []
    for seed in ("1", "2", "random"):
        env = {"PYTHONHASHSEED": seed, "PATH": "/usr/bin:/bin"}
        r = subprocess.run([sys.executable, "-B", "-c", _CHILD, str(ROOT), str(FIX)], env=env,
                           capture_output=True, text=True, check=True)
        outs.append(json.loads(r.stdout))
    assert outs[0] == outs[1] == outs[2]
    here = {p.name: P.parse(p.read_bytes())["content_sha256"] for p in sorted(FIX.glob("real_*.xml"))}
    assert here == outs[0]


# ---------------------------------------------------------------- real fixtures

MANIFEST = json.loads((FIX / "real_manifest_0.2.3.json").read_text())


@pytest.mark.parametrize("name", sorted(MANIFEST))
def test_real_fixture_golden(name):
    exp = MANIFEST[name]
    xml = (FIX / name).read_bytes()
    d = P.parse(xml)
    assert d["source_sha256"] == exp["xml_sha256"] and hashlib.sha256(xml).hexdigest() == exp["xml_sha256"]
    assert d["status"] == exp["status"] and len(d["sections"]) == exp["sections"]
    assert d["content_sha256"] == exp["content_sha256"]
    assert d["setid"] == exp["setid"] and d["version"] == exp["version"]
    assert d["bucket_total"] == sum(d["buckets"].values())


def test_real_status_coverage():
    # spl-sections-0.2.3: all 113 real extraction_defect versions of 0.2.2 were span attributes that HTML repairs,
    # so no real fixture is a defect any more; synthetic tests (test_extraction_defects) cover the defect path.
    assert {m["status"] for m in MANIFEST.values()} == {"complete", "complete_with_source_gaps",
        "identification_incomplete", "no_label_body"}
    assert MANIFEST["real_e999cb0a_v1.xml"]["status"] == "complete"  # extraction_defect (invalid span) in 0.2.2


# 0.1.0 (frozen) dropped renderMultiMedia captions inside table cells; 0.2.1 keeps them.
LOST_BY_010 = {"real_129ef5e0_v3.xml"}


def test_real_matches_frozen_010_narrative_text():
    """Narrative text equals frozen 0.1.0 own_text (modulo the recovered [MEDIA:] marker)."""
    import re
    from gitrx.current import spl_parser_v1 as old
    registry = {c["code"] for c in json.loads((FIX / "section-code-registry.json").read_text())["codes"]}
    strip = lambda s: re.sub(r"\s+", "", re.sub(r"\[MEDIA:[^\]]*\]", "", s))  # noqa: E731
    for name in MANIFEST:
        if name in LOST_BY_010:
            continue
        xml = (FIX / name).read_bytes()
        d = P.parse(xml)
        if d["status"] == "no_label_body":
            with pytest.raises(ValueError):
                old.parse_document(xml, registry)
            continue
        rows, _ = old.parse_document(xml, registry)
        assert len(rows) == len(d["sections"])
        for o, n in zip(rows, d["sections"]):
            assert (o["loinc_code"], o["title"]) == (n["code"], n["title"])
            assert strip(o["own_text"]) == strip(n["narrative"]["own_text"]), (name, o["ordinal"])


# ---------------------------------------------------------------- invariants fire

RICH = doc(structured(sec("34067-9", "Title", """<paragraph>One <footnoteRef IDREF="f1"/> two</paragraph>
<table><caption>c</caption><tbody><tr><td>a<footnote ID="f1">n</footnote></td><td>b</td></tr></tbody></table>
<paragraph>three</paragraph>""", extra="<excerpt><highlight><text><paragraph>hi</paragraph></text></highlight></excerpt>")))


def test_rich_fixture_passes():
    assert P.parse(RICH)["status"] == "complete"


def test_invariant_conservation_dropped_text(monkeypatch):
    orig = P.Narrative._take

    def lossy(self, node, kind):
        return "" if kind == "tail" else orig(self, node, kind)
    monkeypatch.setattr(P.Narrative, "_take", lossy)
    xml = doc(structured(sec("34067-9", "T", "<paragraph>a<content>b</content>TAILTEXT</paragraph>")))
    with pytest.raises(P.InvariantViolation, match="conservation"):
        P.parse(xml)


def test_invariant_conservation_block_text_altered(monkeypatch):
    orig = P.Narrative.block

    def altered(self, kind, text, node, **meta):
        return orig(self, kind, text[:-1] if len(text) > 3 else text, node, **meta)
    monkeypatch.setattr(P.Narrative, "block", altered)
    with pytest.raises(P.InvariantViolation, match="conservation"):
        P.parse(RICH)


def test_invariant_conservation_source_total(monkeypatch):
    orig = P.scan_source

    def drop(roots, ordinals):
        atoms, refs = orig(roots, ordinals)
        i = next(i for i, (n, k, _c) in enumerate(atoms) if P.nonws_len(n.text if k == "text" else n.tail))
        return atoms[:i] + atoms[i + 1:], refs
    monkeypatch.setattr(P, "scan_source", drop)
    with pytest.raises(P.InvariantViolation, match="conservation|assigned|order"):
        P.parse(RICH)


def test_invariant_duplicate_assignment(monkeypatch):
    orig = P.Narrative.block

    def twice(self, kind, text, node, **meta):
        acc = self.acc
        orig(self, kind, text, node, **meta)
        self.acc = acc
        orig(self, kind, text, node, **meta)
    monkeypatch.setattr(P.Narrative, "block", twice)
    with pytest.raises(P.InvariantViolation, match="more than once"):
        P.parse(doc(structured(sec("34067-9", "T", "<paragraph>dup me</paragraph>"))))


def test_invariant_narrative_element_assigned_twice(monkeypatch):
    orig = P._build_section

    def dup(*a, **k):
        s, st = orig(*a, **k)
        st["text_els"] = st["text_els"] * 2
        return s, st
    monkeypatch.setattr(P, "_build_section", dup)
    with pytest.raises(P.InvariantViolation, match="twice"):
        P.parse(RICH)


def test_invariant_order_reversed(monkeypatch):
    orig = P.Narrative.extract

    def reverse(self, text):
        orig(self, text)
        self.blocks.reverse()
        return self
    monkeypatch.setattr(P.Narrative, "extract", reverse)
    with pytest.raises(P.InvariantViolation, match="order"):
        P.parse(doc(structured(sec("34067-9", "T", "<paragraph>first</paragraph><paragraph>second</paragraph>"))))


def test_invariant_order_cells_swapped(monkeypatch):
    orig = P.Narrative.table

    def swap(self, table, outer=None):
        r = orig(self, table, outer)
        cells = [b for b in self.blocks if b["kind"] == "table_cell"]
        i, j = self.blocks.index(cells[0]), self.blocks.index(cells[1])
        self.blocks[i], self.blocks[j] = self.blocks[j], self.blocks[i]
        return r
    monkeypatch.setattr(P.Narrative, "table", swap)
    with pytest.raises(P.InvariantViolation, match="order"):
        P.parse(doc(structured(sec("34067-9", "T", "<table><tbody><tr><td>a</td><td>b</td></tr></tbody></table>"))))


def test_invariant_section_sequence_order(monkeypatch):
    orig = P._build_section

    def swapped(element, ordinal, *a, **k):
        s, st = orig(element, ordinal, *a, **k)
        return s, st
    # Make section 1's narrative claim section 2's atoms by swapping extracted narratives.
    holder = {}

    def swap(element, ordinal, *a, **k):
        s, st = orig(element, ordinal, *a, **k)
        holder[ordinal] = (s, st)
        if ordinal == 2:
            (s1, st1) = holder[1]
            st1["narr"], st["narr"] = st["narr"], st1["narr"]
            st1["text_els"], st["text_els"] = st["text_els"], st1["text_els"]
        return s, st
    monkeypatch.setattr(P, "_build_section", swap)
    body = structured(sec("34067-9", "A", "<paragraph>aaa</paragraph>"), sec("34071-1", "B", "<paragraph>bbb</paragraph>"))
    with pytest.raises(P.InvariantViolation):
        P.parse(doc(body))


def test_invariant_reference_markers(monkeypatch):
    orig = P.scan_source

    def extra_ref(roots, ordinals):
        atoms, refs = orig(roots, ordinals)
        return atoms, refs + refs[:1]
    monkeypatch.setattr(P, "scan_source", extra_ref)
    with pytest.raises(P.InvariantViolation, match="references"):
        P.parse(RICH)


def test_invariant_unresolved_reference_needs_diagnostic(monkeypatch):
    orig = P._check_invariants

    def strip_diag(root, atoms, pos, by_ctx, internals, expected, references, doc_diag, *a):
        return orig(root, atoms, pos, by_ctx, internals, expected, references,
                    [d for d in doc_diag if d.get("code") != "unresolved_ref"], *a)
    monkeypatch.setattr(P, "_check_invariants", strip_diag)
    with pytest.raises(P.InvariantViolation, match="without diagnostic"):
        P.parse(doc(structured(sec("34067-9", "T", '<paragraph>x<footnoteRef IDREF="zzz"/></paragraph>'))))


def test_invariants_do_not_fire_on_clean_inputs():
    for f in sorted(FIX.glob("real_*.xml")):
        P.parse(f.read_bytes())


# ---------------------------------------------------------------- 0.2.1 regressions (full-corpus sweep)

def test_version_is_023():
    assert P.PARSER_VERSION == "spl-sections-0.2.3"


def test_media_caption_inside_table_cell_is_conserved():
    """Class A: renderMultiMedia wrapping a caption inside a table cell (setid 76957c0d v2/v3).
    0.2.0 classified the caption as narrative but never emitted it -> conservation failure."""
    cell = ('<td><paragraph><renderMultiMedia referencedObject="m1"><caption>  Tablet 100 mg  </caption>'
            "</renderMultiMedia></paragraph></td>")
    text = f"<table><tbody><tr>{cell}<td>x</td></tr></tbody></table>"
    media = '<component><observationMedia ID="m1"><text>alt</text></observationMedia></component>'
    d = parsed(doc(structured(sec("34067-9", "T", text, extra=media))))
    cells = d["sections"][0]["narrative"]["blocks"]
    cell_text = next(b["text"] for b in cells if b["kind"] == "table_cell")
    assert "[MEDIA:m1]" in cell_text and "Tablet 100 mg" in cell_text
    assert d["buckets"]["narrative"] == len("Tablet100mg") + 1 and d["buckets"]["media_text"] == 3


def test_empty_media_wrapper_in_cell_unchanged():
    text = '<table><tbody><tr><td><renderMultiMedia referencedObject="m1"> </renderMultiMedia></td></tr></tbody></table>'
    media = '<component><observationMedia ID="m1"><text>alt</text></observationMedia></component>'
    c = next(b for b in parsed(doc(structured(sec("34067-9", "T", text, extra=media))))["sections"][0]["narrative"]["blocks"]
             if b["kind"] == "table_cell")
    assert c["text"] == " [MEDIA:m1] "


# ---------------------------------------------------------------- 0.2.2 (table spans, separators)

def _cells(table_xml):
    d = parsed(doc(structured(sec("34067-9", "T", table_xml))))
    t = d["sections"][0]["narrative"]["tables"][0]
    return d, t, [c for r in t["rows"] for c in r["cells"]]


def test_rowspan_zero_is_valid_and_spans_row_group():
    tx = ("<table><thead><tr><th>H</th></tr></thead><tbody>"
          '<tr><td rowspan="0">a</td><td>b</td></tr><tr><td>c</td></tr><tr><td>d</td></tr></tbody></table>')
    d, t, cells = _cells(tx)
    assert d["status"] == "complete" and t["diagnostics"] == []
    a = next(c for c in cells if c["text"] == "a")
    assert a["attributes"]["rowspan"] == "0" and a["effective_rowspan"] == 3  # tbody only, not thead


def test_rowspan_zero_from_second_row_and_no_group():
    d, t, cells = _cells('<table><tr><td>x</td></tr><tr><td rowspan="0">a</td></tr><tr><td>c</td></tr></table>')
    assert d["status"] == "complete"
    assert next(c for c in cells if c["text"] == "a")["effective_rowspan"] == 2


def test_colspan_zero_is_valid_and_treated_as_one():
    d, t, cells = _cells('<table><tbody><tr><td colspan="0">a</td><td colspan=" 3 ">b</td><td colspan="2">c</td></tr></tbody></table>')
    assert d["status"] == "complete" and t["diagnostics"] == []
    assert [c.get("effective_colspan") for c in cells] == [1, 3, 2]


@pytest.mark.parametrize("attr,value,eff", [
    ("rowspan", "-1", 1), ("colspan", "-2", 1), ("colspan", "NaN", 1), ("colspan", "100%", 100),
    ("rowspan", "", 1), ("colspan", "1.5", 1), ("rowspan", "65535", 65534), ("colspan", "1001", 1000),
    ("colspan", "\u0663", 1), ("rowspan", "a", 1), ("rowspan", "2'", 2), ("colspan", "  7px", 7),
    ("rowspan", "99999999999999999999", 65534), ("colspan", "+3", 3)])
def test_html_span_repairs_are_diagnostics_not_defects(attr, value, eff):
    d, t, cells = _cells(f'<table><tbody><tr><td {attr}="{value}">a</td></tr></tbody></table>')
    assert d["status"] == "complete"
    assert [(x["code"], x["value"]) for x in t["diagnostics"]] == [("nonstandard_table_span", value)]
    assert cells[0]["attributes"][attr] == value
    assert cells[0]["effective_" + attr] == eff
    assert all(x["severity"] == "info" for x in d["diagnostics"] if x["code"] == "nonstandard_table_span")


def test_clean_spans_leave_no_diagnostic():
    d, t, cells = _cells('<table><tbody><tr><td rowspan=" 2 " colspan="3">a</td></tr><tr><td>b</td></tr></tbody></table>')
    assert t["diagnostics"] == [] and cells[0]["effective_rowspan"] == 2 and cells[0]["effective_colspan"] == 3


def test_span_limits_accepted():
    d, t, cells = _cells('<table><tbody><tr><td rowspan="65534" colspan="1000">a</td></tr></tbody></table>')
    assert d["status"] == "complete" and cells[0]["effective_rowspan"] == 65534 and cells[0]["effective_colspan"] == 1000


def test_cell_row_caption_block_boundaries_have_whitespace():
    from gitrx_core import changes
    tx = ("<table><caption>Cap</caption><thead><tr><th>Pediatric</th><th>Adult</th></tr></thead><tbody>"
          "<tr><td>Intervention:</td><td>Use lower dose</td></tr></tbody></table>")
    d = parsed(doc(structured(sec("34067-9", "T", tx + "<paragraph>One</paragraph><paragraph>Two</paragraph>"
                                  "<list><item>I1</item><item>I2</item></list><paragraph>A<br/>B</paragraph>"))))
    ch = changes.channels(d["sections"][0])
    assert "|" not in ch["table"]
    toks = ch["table"].split()
    assert toks == ["Cap", "Pediatric", "Adult", "Intervention:", "Use", "lower", "dose"]
    assert ch["table"].index("Intervention:") > ch["table"].index("Adult")
    assert ch["text"].split() == ["T", "One", "Two", "I1", "I2", "A", "B"]


def test_block_elements_inside_cell_do_not_fuse():
    d, t, cells = _cells("<table><tbody><tr><td><paragraph>One</paragraph><paragraph>Two</paragraph>"
                         "<list><item>I1</item><item>I2</item></list>x<br/>y</td></tr></tbody></table>")
    assert cells[0]["text"].split() == ["One", "Two", "I1", "I2", "x", "y"]
