"""Tests for gitrx_core.srlc_grade (SrLC grading harness) on synthetic data."""
import argparse
import hashlib
import json
import sqlite3
import zipfile

import pytest

from gitrx_core import srlc_grade as G

SID = "11111111-2222-3333-4444-555555555555"
L = "2.16.840.1.113883.6.1"


def spl(version, w_text, appl="NDA021234"):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<document xmlns="urn:hl7-org:v3"><id root="99999999-0000-0000-0000-000000000000"/>
<code code="34391-3" codeSystem="{L}" displayName="HUMAN PRESCRIPTION DRUG LABEL"/>
<title>T</title><effectiveTime value="2020010{version}"/><setId root="{SID}"/><versionNumber value="{version}"/>
<component><structuredBody>
<component><section><id root="aaaaaaaa-0000-0000-0000-000000000001"/><code code="34084-4" codeSystem="{L}"/>
<title>ADVERSE REACTIONS</title><text><paragraph>{w_text}</paragraph></text></section></component>
<component><section><code code="34067-9" codeSystem="{L}"/><title>INDICATIONS</title><text><paragraph>Used for things.</paragraph></text>
<subject><manufacturedProduct><manufacturedProduct><name>X</name></manufacturedProduct>
<subjectOf><approval><id root="2.16.840.1.113883.3.150" extension="{appl}"/><code code="C73594" displayName="NDA"/></approval></subjectOf>
</manufacturedProduct></subject></section></component>
</structuredBody></component></document>""".encode()


def test_window_edges():
    a = "2020-06-01"
    assert G.in_window("2020-05-02", a)        # exactly -30 d
    assert not G.in_window("2020-05-01", a)    # -31 d
    assert G.in_window("2021-06-01", a)        # exactly +365 d
    assert not G.in_window("2021-06-02", a)    # +366 d
    assert not G.in_window(None, a)
    assert not G.in_window("2020-06-01", None)


def test_fragment_splitting_and_min_tokens():
    fr = G.split_fragments(["Alpha  beta gamma … delta epsilon", "two words", "…", "x’s  y – z"])
    assert [f["text"] for f in fr] == ["Alpha beta gamma", "delta epsilon", "two words", "x's y - z"]
    assert [f["tokens"] for f in fr] == [3, 2, 2, 4]
    assert sum(1 for f in fr if f["tokens"] >= G.MIN_FRAGMENT_TOKENS) == 2


def test_verbatim_and_overlap_rule():
    raw = "The drug causes   rash and fever in patients. Older text stays."
    start = raw.index("fever")
    nt, toks = G.norm_with_tokens(raw, [[start, start + len("fever in patients.")]])
    assert nt == "The drug causes rash and fever in patients. Older text stays."
    assert G.find_fragment("causes rash and fever", nt, toks) == (True, True)       # overlaps added tokens
    assert G.find_fragment("The drug causes rash", nt, toks) == (True, False)       # verbatim, no overlap
    assert G.find_fragment("drug causes fever", nt, toks) == (False, False)         # not verbatim
    assert G.find_fragment("lder text", nt, toks)[0]                                # literal substring (mid-word)


def test_wilson():
    assert G.wilson(0, 0) == (None, None)
    lo, hi = G.wilson(95, 100)
    assert 0.88 < lo < 0.90 and 0.97 < hi < 0.99
    assert G.wilson(10, 10)[1] == pytest.approx(1.0)


def _args(**kw):
    d = dict(split="open", sealed_final=False, parser_version=None, frozen_parser_marker=None)
    d.update(kw)
    return argparse.Namespace(**d)


def test_sealed_mode_refusals(tmp_path):
    assert G.check_split_mode(_args()) == "open"
    with pytest.raises(SystemExit):
        G.check_split_mode(_args(split="sealed"))
    with pytest.raises(SystemExit):
        G.check_split_mode(_args(sealed_final=True))
    with pytest.raises(SystemExit):
        G.check_split_mode(_args(sealed_final=True, parser_version="spl-sections-0.2.1",
                                 frozen_parser_marker=str(tmp_path / "missing.json")))
    m = tmp_path / "m.json"
    m.write_text(json.dumps({"frozen_parser_version": "spl-sections-9.9.9"}))
    with pytest.raises(SystemExit):
        G.check_split_mode(_args(sealed_final=True, parser_version="spl-sections-0.2.1", frozen_parser_marker=str(m)))
    m.write_text(json.dumps({"frozen_parser_version": "spl-sections-9.9.9"}))
    with pytest.raises(SystemExit):  # marker matches request but installed parser differs
        G.check_split_mode(_args(sealed_final=True, parser_version="spl-sections-9.9.9", frozen_parser_marker=str(m)))


def test_approvals_in_xml():
    assert G.approvals_in_xml(spl(1, "x")) == {"NDA021234"}
    assert G.approvals_in_xml(b"<a/>") == set()


def test_ws_matching_and_added_token_overlap():
    cases = [("Use ( 5.1 ) for details and more text.", "( 5.1 )", "(5.1)", "details"),
             ("The brand SPORANOX ® is oral.", "SPORANOX ®", "SPORANOX®", "oral."),
             ("An open- label study was run.", "open- label", "open-label", "study")]
    for raw, spaced, joined, other in cases:
        s0 = raw.index(spaced)
        iv = [[s0, s0 + len(spaced)]]
        nt, toks = G.norm_with_tokens(raw, iv)
        wt, flags = G.norm_ws_with_flags(raw, iv)
        frag_strict = G.normalize(joined)
        assert G.find_fragment(frag_strict, nt, toks)[0] is False      # strict literal-v1 misses
        assert G.find_fragment(G.normalize(spaced), nt, toks) == (True, True)
        assert G.find_fragment_ws(G.ws_squash(joined), wt, flags) == (True, True)   # ws hits, overlaps added tokens
        # same text but the added interval is elsewhere: verbatim, no overlap
        e0 = raw.index(other)
        wt, flags = G.norm_ws_with_flags(raw, [[e0, e0 + len(other)]])
        assert G.find_fragment_ws(G.ws_squash(joined), wt, flags) == (True, False)
    assert G.find_fragment_ws("zzz", "abc", bytearray(3)) == (False, False)


def test_ws_digest_found_but_strict_not():
    a = spl(1, "Nausea was reported.")
    b = spl(2, "Nausea was reported. Hepatic failure ( 5.1 ) has occurred rarely.")
    v = G.DigestView(G.make_digest(a, b, "a" * 64, "b" * 64))
    st = v.fragment_status("Hepatic failure (5.1) has occurred rarely", "AR")
    assert st["found"] and not st["found_strict"]
    st = v.fragment_status("Hepatic failure ( 5.1 ) has occurred rarely", "AR")
    assert st["found"] and st["found_strict"]


def test_digest_found_and_labels():
    a = spl(1, "Nausea was reported.")
    b = spl(2, "Nausea was reported. Hepatic failure has occurred rarely in treated patients.")
    d = G.make_digest(a, b, "a" * 64, "b" * 64)
    assert d["comparison"] == "ok"
    v = G.DigestView(d)
    assert v.has_group_change("AR") and not v.has_group_change("BW")
    st = v.fragment_status("Hepatic failure has occurred rarely", "AR")
    assert st["found"] and st["found_strict"]
    st = v.fragment_status("Nausea was reported.", "AR")     # verbatim in changed section, no added token
    assert not st["found"] and st["verbatim_changed_same_group"] and st["in_from_same_group"]
    assert G.candidate_label([(st, "2019-01-01", "2020-06-01")])[0] == G.L_FROM_BEFORE
    assert G.candidate_label([(st, "2020-06-01", "2020-06-01")])[0] == G.L_FROM_IN
    st = v.fragment_status("Hepatic failure has occurred rarely", "WP")  # wrong group
    assert not st["found"] and st["in_other_group"]
    assert G.candidate_label([(st, None, "2020-06-01")])[0] == G.L_TO_NOCHG
    st = v.fragment_status("completely absent words here", "AR")
    assert G.candidate_label([(st, None, "2020-06-01")]) == (None, None)
    st = v.fragment_status("Used for things.", "AR")     # in an unchanged other-group section
    assert G.candidate_label([(st, None, "2020-06-01")])[0] == G.L_TO_NOCHG
    # raw-XML only (title text is not a parsed section)
    st = v.fragment_status("ADVERSE REACTIONS", "AR")
    assert not st["found"]


def test_presence_classification():
    pubs = {1: "2018-01-01", 2: "2020-06-15", 3: "2023-01-01"}
    ap = "2020-06-01"
    assert G.classify_presence(pubs, ap, {3}) == G.L_AFTER
    assert G.classify_presence(pubs, ap, {1}) == G.L_BEFORE
    assert G.classify_presence(pubs, ap, {1, 3}) == G.L_BOTH
    assert G.classify_presence(pubs, ap, set()) == G.L_NONE
    assert G.classify_presence(pubs, ap, {2}) == G.L_INWIN_OTHER


def test_sibling_flag_and_names():
    assert G.is_sibling_or_repackager("SPORANOX", ["Sporanox- itraconazole capsule"]) is False
    assert G.is_sibling_or_repackager("SPORANOX", ["Itraconazole capsules"]) is True
    assert G.is_sibling_or_repackager("Humira (adalimumab)", ["HUMIRA"]) is False
    assert G.is_sibling_or_repackager("X", []) is False
    xml = spl(1, "x")
    assert G.product_names_in_xml(xml) == ["X", "T"]


def _make_world(tmp_path):
    """Two SrLC drugs in the open split: one mapped with versions in window, one with no candidate setid;
    a third (sealed) drug that must never be read."""
    zips = {}
    for ver, text in ((1, "Nausea was reported."), (2, "Nausea was reported. Hepatic failure has occurred rarely.")):
        xml = spl(ver, text)
        p = tmp_path / f"z{ver}.zip"
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("label.xml", xml)
        zips[ver] = (hashlib.sha256(p.read_bytes()).hexdigest(), str(p))
    ix = sqlite3.connect(tmp_path / "index.sqlite")
    ix.executescript(G.INDEX_SCHEMA)
    for ver, pub in ((1, "2019-01-01"), (2, "2020-06-15")):
        ix.execute("INSERT INTO versions VALUES(?,?,?,?,?,?,?)", (SID, ver, zips[ver][0], "label.xml", pub, None, 1))
        ix.execute("INSERT INTO paths VALUES(?,?)", zips[ver])
    ix.execute("INSERT INTO approvals VALUES(?,?,?)", (SID, 2, "NDA021234"))
    ix.commit()
    ix.close()
    gb = sqlite3.connect(tmp_path / "gb.sqlite")
    from gitrx_core.srlc import GRADE_SCHEMA
    gb.executescript(GRADE_SCHEMA)
    gb.execute("INSERT INTO drugs VALUES(1,'D1','x','NDA','021234','h1',NULL)")
    gb.execute("INSERT INTO drugs VALUES(2,'D2','x','NDA','999999','h2',NULL)")
    gb.execute("INSERT INTO drugs VALUES(3,'SEALED','x','NDA','021234','h3',NULL)")
    gb.execute("INSERT INTO supplements VALUES(1,1,'06/01/2020','2020-06-01','SUPPL-1','1',NULL,NULL,'h1')")
    gb.execute("INSERT INTO supplements VALUES(2,2,'06/01/2020','2020-06-01','SUPPL-1','1',NULL,NULL,'h2')")
    gb.execute("INSERT INTO supplements VALUES(3,3,'06/01/2020','2020-06-01','SUPPL-1','1',NULL,NULL,'h3')")
    ins = "INSERT INTO changes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    gb.execute(ins, (1, 1, 1, "SUPPL-1", "AR", "ADVERSE", "", "6", "Adverse", "", "", json.dumps(["Hepatic failure has occurred rarely"]), "[]", 0, 0, "h1"))
    gb.execute(ins, (2, 1, 1, "SUPPL-1", "BW", "BOX", "", "", "Box", "", "", json.dumps(["boxed words that never appear"]), "[]", 0, 0, "h1"))
    gb.execute(ins, (3, 1, 1, "SUPPL-1", "DI", "DI", "", "7", "DI", "", "", json.dumps(["two words"]), "[]", 0, 0, "h1"))
    gb.execute(ins, (4, 2, 2, "SUPPL-1", "AR", "ADVERSE", "", "6", "Adverse", "", "", json.dumps(["some other text here"]), "[]", 0, 0, "h2"))
    gb.execute(ins, (5, 3, 3, "SUPPL-1", "AR", "ADVERSE", "", "6", "Adverse", "", "", json.dumps(["SEALED fragment text"]), "[]", 0, 0, "h3"))
    gb.commit()
    gb.close()
    root = tmp_path / "srlc"
    root.mkdir()
    fp = lambda ids: hashlib.sha256("\n".join(str(i) for i in sorted(ids)).encode()).hexdigest()  # noqa: E731
    (root / "split.json").write_text(json.dumps({"open": [1, 2], "sealed": [3], "open_sha256": fp([1, 2]), "sealed_sha256": fp([3])}))
    return root


def test_end_to_end_open_grade_and_coverage_exclusion(tmp_path):
    root = _make_world(tmp_path)
    args = argparse.Namespace(
        split="open", sealed_final=False, parser_version=None, frozen_parser_marker=None, srlc_root=str(root),
        gradebook=str(tmp_path / "gb.sqlite"), index=str(tmp_path / "index.sqlite"), evidence_db="unused",
        raw_manifest=None, workers=1, out_dir=str(tmp_path / "out"), cache_dir=str(tmp_path / "cache"))
    s = G.run_grade(args)
    assert s["split"] == "open"
    assert s["coverage"]["entries_total"] == 2            # sealed drug never planned
    assert s["coverage"]["entries_excluded_no_candidate_setid"] == 1
    assert s["coverage"]["entries_graded"] == 1
    assert s["section_recall"]["n"] == 3 and s["section_recall"]["k"] == 1   # AR found; BW and DI missed
    assert s["text_recall_main"]["n"] == 2 and s["text_recall_main"]["k"] == 1
    assert s["text_short_fragments_lt3_tokens"]["n"] == 1
    rows = [json.loads(x) for x in open(tmp_path / "out" / "rows.jsonl")]
    assert {r["drug_name_id"] for r in rows} == {1}
    bw = next(r for r in rows if r["group"] == "BW")
    assert not bw["section_found"] and bw["miss_label"] == G.S_OTHER_NOSEC
    assert bw["sibling_or_repackager"] == [SID]   # product name X does not contain "D1"
    assert s["sibling_or_repackager_diagnostic"]["section_rows"]["rows_with_only_sibling_candidates"] == 3
    frs = [json.loads(x) for x in open(tmp_path / "out" / "fragments.jsonl")]
    boxed = next(f for f in frs if f["group"] == "BW")
    assert boxed["miss_label"] == G.L_NONE and not boxed["found"] and not boxed["found_strict"]
    assert s["text_recall_main_strict"]["k"] == 1
    assert s["text_miss_labels"] == {G.L_NONE: 1}
    assert s["section_miss_labels"] == {G.S_OTHER_NOSEC: 2}
    frags = open(tmp_path / "out" / "fragments.jsonl").read()
    assert "SEALED" not in frags
    # a second run reuses the digest cache
    s2 = G.run_grade(args)
    assert s2["digest_build"].get("cached", 0) >= 1
