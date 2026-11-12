"""Tests for gitrx_core.srlc_precision on synthetic data (no SSD needed)."""
import gzip
import hashlib
import io
import json
import sqlite3
import zipfile

import pytest

from gitrx_core import changes as CH
from gitrx_core import spl_parser as SP
from gitrx_core import srlc_grade as G
from gitrx_core import srlc_precision as P

SID = "11111111-2222-3333-4444-555555555555"
L = "2.16.840.1.113883.6.1"
APPL = "NDA021234"


def spl(version, ar, wp, ind="Used for things."):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<document xmlns="urn:hl7-org:v3"><id root="99999999-0000-0000-0000-000000000000"/>
<code code="34391-3" codeSystem="{L}" displayName="HUMAN PRESCRIPTION DRUG LABEL"/>
<title>T</title><effectiveTime value="2020010{version}"/><setId root="{SID}"/><versionNumber value="{version}"/>
<component><structuredBody>
<component><section><code code="34084-4" codeSystem="{L}"/><title>ADVERSE REACTIONS</title><text><paragraph>{ar}</paragraph></text></section></component>
<component><section><code code="34071-1" codeSystem="{L}"/><title>WARNINGS</title><text><paragraph>{wp}</paragraph></text></section></component>
<component><section><code code="34067-9" codeSystem="{L}"/><title>INDICATIONS</title><text><paragraph>{ind}</paragraph></text>
<subject><manufacturedProduct><manufacturedProduct><name>X</name></manufacturedProduct>
<subjectOf><approval><id root="2.16.840.1.113883.3.150" extension="{APPL}"/><code code="C73594" displayName="NDA"/></approval></subjectOf>
</manufacturedProduct></subject></section></component>
</structuredBody></component></document>""".encode()


V1 = spl(1, "Nausea was seen.", "Avoid alcohol use.", "Used for pain.")
V2 = spl(2, "Nausea and severe rash were seen in trials.", "Avoid alcohol use and driving at night.", "Used for chronic pain.")


def sha(b):
    return hashlib.sha256(b).hexdigest()


def make_world(tmp_path, with_second_supplement=True):
    """Gradebook with one drug and AR supplement 10 (2020-06-01) [+ WP supplement 11 on 2020-09-20]; index with 2 versions;
    the pair digest is built from synthetic labels and cached."""
    gb = tmp_path / "gb.sqlite"
    con = sqlite3.connect(gb)
    con.executescript("""
    CREATE TABLE drugs(drug_name_id INTEGER PRIMARY KEY, drug_name TEXT, active_ingredient TEXT, appl_type TEXT, appl_no TEXT, detail_sha256 TEXT, csv_sha256 TEXT);
    CREATE TABLE supplements(supplement_id INTEGER PRIMARY KEY, drug_name_id INTEGER, approval_date_raw TEXT, approval_date_iso TEXT, suppl_ref TEXT, suppl_no TEXT, label_pdf_url TEXT, database_updated TEXT, source_sha256 TEXT);
    CREATE TABLE changes(id INTEGER PRIMARY KEY, drug_name_id INTEGER, supplement_id INTEGER, suppl_ref TEXT, section_group TEXT, group_heading TEXT, subheading_raw TEXT, section_number TEXT, section_title TEXT, description_html TEXT, description_text TEXT, underlined_spans_json TEXT, comment_spans_json TEXT, has_elision INTEGER, refers_to_table INTEGER, source_sha256 TEXT);
    """)
    con.execute("INSERT INTO drugs VALUES (1,'DRUGX','X','NDA','021234','d','c')")
    con.execute("INSERT INTO supplements VALUES (10,1,'06/01/2020','2020-06-01','SUPPL-1','1','','', 'd')")
    con.execute("INSERT INTO changes (id,drug_name_id,supplement_id,section_group,underlined_spans_json) VALUES (100,1,10,'AR',?)",
                (json.dumps(["Nausea and severe rash were seen in trials."]),))
    if with_second_supplement:
        con.execute("INSERT INTO supplements VALUES (11,1,'09/20/2020','2020-09-20','SUPPL-2','2','','', 'd')")
        con.execute("INSERT INTO changes (id,drug_name_id,supplement_id,section_group,underlined_spans_json) VALUES (101,1,11,'WP',?)",
                    (json.dumps(["Avoid alcohol use and driving at night."]),))
    con.commit()
    con.close()
    ix = tmp_path / "ix.sqlite"
    con = sqlite3.connect(ix)
    con.executescript("""
    CREATE TABLE versions(setid TEXT, version INTEGER, sha256 TEXT, xml_member TEXT, pub_date TEXT, effective_date TEXT, rx INTEGER, PRIMARY KEY(setid, version));
    CREATE TABLE paths(sha256 TEXT PRIMARY KEY, path TEXT);
    CREATE TABLE approvals(setid TEXT, version INTEGER, appl TEXT, PRIMARY KEY(setid, version, appl));
    """)
    s1, s2 = sha(V1), sha(V2)
    con.execute("INSERT INTO versions VALUES (?,1,?,NULL,'2020-05-01','20200501',1)", (SID, s1))
    con.execute("INSERT INTO versions VALUES (?,2,?,NULL,'2020-06-15','20200615',1)", (SID, s2))
    con.execute("INSERT INTO approvals VALUES (?,1,?)", (SID, APPL))
    con.execute("INSERT INTO approvals VALUES (?,2,?)", (SID, APPL))
    con.commit()
    con.close()
    cache = tmp_path / "pairs"
    key = G.digest_key(s1, s2, SP.PARSER_VERSION, CH.CHANGES_VERSION)
    d = G.make_digest(V1, V2, s1, s2)
    (cache / key[:2]).mkdir(parents=True)
    with gzip.open(cache / key[:2] / (key + ".json.gz"), "wt") as f:
        json.dump(d, f)
    return gb, ix, cache, d


def analyse(tmp_path, group_mode="either", with_second=True):
    gb_p, ix_p, cache, d = make_world(tmp_path, with_second)
    gb = sqlite3.connect(gb_p)
    ix = sqlite3.connect(ix_p)
    graded = P.graded_pairs(gb, ix, {1}, SP.PARSER_VERSION, CH.CHANGES_VERSION)
    recs, cnt = P.collect(graded, [str(cache)], group_mode, workers=1)
    P.attach_matches(recs, P.SrlcIndex(gb, {1}), group_mode)
    return graded, recs, cnt, d


# ----------------------------------------------------------------------------- noise canonicalization
def test_canon_drops_only_formatting_never_numbers_or_words():
    assert P.canon("(5.1)") == "" and P.canon("[see") == "see"       # cross-reference numbers are noise
    assert P.canon("5") == "5" and P.canon("10") == "10"             # bare numbers are content
    assert P.canon("Doses") == "doses" and P.canon("DOSES®") == "doses"
    assert P.canon("12/2019") == ""
    assert P.canon("mg/kg") == "mgkg"
    # a changed number is NOT formatting
    r = P.diff_channel_tokens("dose is 5 mg", "dose is 6 mg")
    add, rem = r[0], r[1]
    assert "".join(P.canon(t) for t in add) != "".join(P.canon(t) for t in rem)


def test_canon_full_ignores_revision_lines_and_xrefs():
    a = "Take with food. (5.1) Revised: 12/2019"
    b = "Take with food. (5.2) Revised: 03/2021"
    assert P.canon_full(a) == P.canon_full(b)
    assert P.canon_full("Take with food.") != P.canon_full("Take without food.")


def test_diff_channel_tokens_add_remove():
    add, rem, na, nb = P.diff_channel_tokens("a b c d", "a x c d e")
    assert add == ["x", "e"] and rem == ["b"] and (na, nb) == (4, 5)


# ----------------------------------------------------------------------------- categorization and arithmetic
def rec(**kw):
    base = {"matched": False, "timing": False, "text_hit": False, "fmt": False, "nadd": 3, "nrem": 0, "chans": ["text"],
            "big": False, "add": "plain new words", "rem": "", "group": "WP", "alt": None, "kind": "modified", "refonly": False}
    base.update(kw)
    return base


def test_categorize_order():
    assert P.categorize(rec(fmt=True, text_hit=True)) == "a_formatting_noise"
    assert P.categorize(rec(text_hit=True)) == "b_timing_other_supplement_text"
    assert P.categorize(rec(timing=True)) == "b_timing_group_level"
    assert P.categorize(rec(nadd=0, nrem=4, add="", rem="gone words here")) == "d_pure_deletion"
    assert P.categorize(rec(nadd=0, nrem=0, add="")) == "d_no_text_delta"
    assert P.categorize(rec(big=True)) == "d_too_large_to_diff"
    assert P.categorize(rec(chans=["table"])) == "c1_table_data"
    assert P.categorize(rec(add="risk of fatal bleeding")) == "c4_possible_srlc_omission_safety_kw"
    assert P.categorize(rec(add="dosage mg adjusted")) == "c3_non_safety_content"
    assert P.categorize(rec(add="alpha beta gamma")) == "c5_other_text"
    assert P.categorize(rec(nrem=3, add="take one tablet daily with water", rem="take one tablet daily with food")) == "c2_editorial_rewording"


def test_fmt_kind():
    assert P.fmt_kind(rec(add="Dose", rem="dose")) == "case_only"
    assert P.fmt_kind(rec(add="a b", rem="ab")) == "space_only"
    assert P.fmt_kind(rec(add="a,b", rem="ab")) == "punct_only"


def test_row_and_summarize_arithmetic():
    rs = [rec(matched=True, timing=True, group="WP"), rec(group="WP", timing=True), rec(group="WP", fmt=True),
          rec(group="BW", matched=True, timing=True), rec(group="BW", refonly=True)]
    for r in rs:
        r.setdefault("text_hit", False)
    s = P.summarize(rs)
    assert s["all_kinds"]["ALL"]["n"] == 5 and s["all_kinds"]["ALL"]["matched"] == 2
    assert s["all_kinds"]["ALL"]["crude_pct"] == 40.0
    assert s["all_kinds"]["ALL"]["timing_robust_pct"] == 60.0
    assert s["all_kinds"]["WP"]["crude_pct"] == pytest.approx(33.33)
    assert s["reference_only"]["ALL"]["reference_only_units"] == 1
    assert s["reference_only"]["ALL"]["excluded"]["n"] == 4
    assert s["formatting"]["formatting_noise"] == 1 and s["formatting"]["unmatched_total"] == 3
    assert s["excluding_formatting_noise_from_denominator"]["ALL"]["n"] == 4


# ----------------------------------------------------------------------------- end-to-end on a synthetic world
def test_graded_pair_units_and_matching(tmp_path):
    graded, recs, cnt, d = analyse(tmp_path)
    assert len(graded) == 1                       # only pair 1->2 has its to-version in the supplement-10 window
    by = {r["group"]: r for r in recs}
    assert set(by) == {"AR", "WP"}                # INDICATIONS is not an SrLC group
    assert cnt["non_srlc_section"] == 1 and cnt["matched"] == 1 and cnt["unmatched_srlc_group"] == 1
    assert by["AR"]["matched"] is True
    assert by["WP"]["matched"] is False
    # WP is credited by timing: supplement 11 (WP, 2020-09-20) is within [to-365d, to+730d] of 2020-06-15
    assert by["WP"]["timing"] is True and by["AR"]["timing"] is True
    # and its added text overlaps the SrLC fragment of the other supplement
    assert by["WP"]["text_hit"] is True
    s = P.summarize(recs)["all_kinds"]["ALL"]
    assert (s["crude_pct"], s["timing_robust_pct"]) == (50.0, 100.0)


def test_without_second_supplement_wp_is_unmatched_everywhere(tmp_path):
    _g, recs, _c, _d = analyse(tmp_path, with_second=False)
    wp = [r for r in recs if r["group"] == "WP"][0]
    assert not wp["matched"] and not wp["timing"] and not wp["text_hit"]
    assert P.categorize(wp) not in ("a_formatting_noise", "b_timing_group_level", "b_timing_other_supplement_text")


def test_group_mode_primary_vs_either(tmp_path):
    gb_p, ix_p, cache, d = make_world(tmp_path, with_second_supplement=False)
    # make the WARNINGS change carry AR as its alternative group in the cached digest, then re-save
    for c in d["changes"]:
        if c["group"] == "WP":
            c["alt"] = "AR"
    s1, s2 = sha(V1), sha(V2)
    key = G.digest_key(s1, s2, SP.PARSER_VERSION, CH.CHANGES_VERSION)
    with gzip.open(cache / key[:2] / (key + ".json.gz"), "wt") as f:
        json.dump(d, f)
    gb, ix = sqlite3.connect(gb_p), sqlite3.connect(ix_p)
    graded = P.graded_pairs(gb, ix, {1}, SP.PARSER_VERSION, CH.CHANGES_VERSION)
    ra, _ = P.collect(graded, [str(cache)], "either", workers=1)
    rp, _ = P.collect(graded, [str(cache)], "primary", workers=1)
    assert sum(r["matched"] for r in ra) == 2 and sum(r["matched"] for r in rp) == 1


def test_missing_digest_is_counted_not_guessed(tmp_path):
    gb_p, ix_p, cache, _d = make_world(tmp_path)
    gb, ix = sqlite3.connect(gb_p), sqlite3.connect(ix_p)
    graded = P.graded_pairs(gb, ix, {1}, SP.PARSER_VERSION, CH.CHANGES_VERSION)
    recs, cnt = P.collect(graded, [str(tmp_path / "empty")], "either", workers=1)
    assert recs == [] and cnt["pair_not_ok_or_missing_digest"] == 1


def test_ensure_digests_refuses_other_code_version(tmp_path):
    gb_p, ix_p, cache, _d = make_world(tmp_path)
    gb, ix = sqlite3.connect(gb_p), sqlite3.connect(ix_p)
    graded = P.graded_pairs(gb, ix, {1}, "spl-sections-0.0.0", CH.CHANGES_VERSION)   # key differs from the cached one
    with pytest.raises(SystemExit, match="installed code"):
        P.ensure_digests(graded, [str(cache)], str(tmp_path / "build"), str(ix_p), "spl-sections-0.0.0", CH.CHANGES_VERSION)


def test_ensure_digests_builds_from_raw_zip(tmp_path):
    gb_p, ix_p, _cache, _d = make_world(tmp_path)
    # real ZIPs on disk, registered in the index paths table
    con = sqlite3.connect(ix_p)
    for data, v in ((V1, 1), (V2, 2)):
        z = io.BytesIO()
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr("label.xml", data)
        raw = z.getvalue()
        h = sha(raw)
        p = tmp_path / h
        p.write_bytes(raw)
        con.execute("UPDATE versions SET sha256=?, xml_member='label.xml' WHERE setid=? AND version=?", (h, SID, v))
        con.execute("INSERT INTO paths VALUES (?,?)", (h, str(p)))
    con.commit()
    con.close()
    gb, ix = sqlite3.connect(gb_p), sqlite3.connect(ix_p)
    graded = P.graded_pairs(gb, ix, {1}, SP.PARSER_VERSION, CH.CHANGES_VERSION)
    build = tmp_path / "build"
    res = P.ensure_digests(graded, [], str(build), str(ix_p), SP.PARSER_VERSION, CH.CHANGES_VERSION, workers=1)
    assert res["built"] == 1 and res["unresolved_raw"] == 0
    recs, cnt = P.collect(graded, [str(build)], "either", workers=1)
    assert cnt["pairs_ok"] == 1 and {r["group"] for r in recs} == {"AR", "WP"}


# ----------------------------------------------------------------------------- kept outputs and refusals
def write_grading_dir(path, matched, unmatched, split="open", groups=("WP", "WP", "AR")):
    path.mkdir()
    go = {"gitrx_only_non_srlc_section": 1, "gitrx_only_srlc_group": len(groups), "matched_by_srlc": matched}
    (path / "summary.json").write_text(json.dumps({"split": split, "group_mode": "either", "parser_version": SP.PARSER_VERSION,
                                                   "changes_version": CH.CHANGES_VERSION, "gitrx_only": go}))
    with open(path / "gitrx_only.jsonl", "w") as f:
        for g in groups:
            f.write(json.dumps({"category": "gitrx_only_srlc_group", "group": g, "setid": SID, "from_version": 1,
                                "to_version": 2, "section_number": None, "section_title": g, "kind": "modified", "drugs": [1]}) + "\n")
        f.write(json.dumps({"category": "gitrx_only_non_srlc_section", "group": None, "setid": SID, "from_version": 1,
                            "to_version": 2, "section_number": None, "section_title": "x", "kind": "modified", "drugs": [1]}) + "\n")


def test_kept_precision_from_jsonl_only(tmp_path):
    write_grading_dir(tmp_path / "g", matched=9, unmatched=3)
    k = P.kept_precision(str(tmp_path / "g"))
    assert k["crude_precision_pct"] == 75.0 and k["jsonl_consistent_with_summary"]
    assert k["unmatched_by_group"] == {"WP": 2, "AR": 1} and k["gitrx_only_non_srlc_section"] == 1


def test_kept_precision_detects_inconsistent_jsonl(tmp_path):
    write_grading_dir(tmp_path / "g", matched=9, unmatched=3)
    s = json.load(open(tmp_path / "g" / "summary.json"))
    s["gitrx_only"]["gitrx_only_srlc_group"] = 99
    json.dump(s, open(tmp_path / "g" / "summary.json", "w"))
    assert P.kept_precision(str(tmp_path / "g"))["jsonl_consistent_with_summary"] is False


def test_crosscheck_equals_kept_gitrx_only(tmp_path):
    w = tmp_path / "w"
    w.mkdir()
    graded, recs, cnt, d = analyse(w)
    g = tmp_path / "g"
    g.mkdir()
    wp = [r for r in recs if r["group"] == "WP"][0]
    (g / "summary.json").write_text(json.dumps({"gitrx_only": {"matched_by_srlc": 1, "gitrx_only_srlc_group": 1,
                                                                 "gitrx_only_non_srlc_section": 1}}))
    (g / "gitrx_only.jsonl").write_text(json.dumps({"category": "gitrx_only_srlc_group", "group": "WP", "setid": SID,
                                                    "from_version": 1, "to_version": 2, "section_number": wp["num"],
                                                    "section_title": wp["title"], "kind": wp["kind"], "drugs": [1]}) + "\n")
    cc = P.compare_with_kept(recs, str(g))
    assert cc["unmatched_identical_to_kept_gitrx_only_jsonl"] is True and cc["matched_recomputed"] == 1


def test_run_refuses_sealed_run_and_sealed_ids(tmp_path):
    write_grading_dir(tmp_path / "s", 1, 1, split="sealed")
    with pytest.raises(SystemExit, match="open split only"):
        P.run(str(tmp_path / "s"))
    write_grading_dir(tmp_path / "o", 1, 1, split="open")
    sp = tmp_path / "split.json"
    sp.write_text(json.dumps({"open": [1, 2], "sealed": [3, 4]}))
    with pytest.raises(SystemExit, match="sealed DrugNameIDs"):
        P.run(str(tmp_path / "o"), split_path=str(sp), ids={1, 3})


def test_timing_window_edges(tmp_path):
    gb_p, ix_p, cache, d = make_world(tmp_path, with_second_supplement=False)
    gb = sqlite3.connect(gb_p)
    gb.execute("INSERT INTO supplements VALUES (12,1,'x','2022-06-16','SUPPL-3','3','','', 'd')")   # to_pub + 731 d
    gb.execute("INSERT INTO changes (id,drug_name_id,supplement_id,section_group,underlined_spans_json) VALUES (102,1,12,'WP','[]')")
    gb.commit()
    idx = P.SrlcIndex(gb, {1})
    r = {"to_pub": "2020-06-15", "drugs": [1], "group": "WP", "alt": None}
    assert idx.timing_match(r, "either") is False          # 731 d after the to-version: just outside
    gb.execute("UPDATE supplements SET approval_date_iso='2022-06-15' WHERE supplement_id=12")
    gb.commit()
    assert P.SrlcIndex(gb, {1}).timing_match(r, "either") is True    # exactly +730 d
