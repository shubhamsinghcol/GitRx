"""Tests for tools/verify_gitrx.py on small synthetic fixtures (no SSD needed)."""
import hashlib
import importlib.util
import io
import json
import os
import sqlite3
import zipfile

import pytest

from gitrx_core import rebuild as R
from gitrx_core import srlc_grade as G

_spec = importlib.util.spec_from_file_location(
    "verify_gitrx", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "verify_gitrx.py"))
V = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(V)

L = "2.16.840.1.113883.6.1"
SID_A = "aa000000-2222-3333-4444-555555555555"
SID_B = "bb000000-2222-3333-4444-555555555555"


def xml(setid, version, texts):
    secs = "".join(f'<component><section><code code="{c}" codeSystem="{L}"/><title>{t}</title>'
                   f"<text><paragraph>{p}</paragraph></text></section></component>" for c, t, p in texts)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<document xmlns="urn:hl7-org:v3"><id root="99999999-0000-0000-0000-000000000000"/>
<code code="34391-3" codeSystem="{L}" displayName="HUMAN PRESCRIPTION DRUG LABEL"/>
<title>T</title><effectiveTime value="2024010{version}"/><setId root="{setid}"/><versionNumber value="{version}"/>
<component><structuredBody>{secs}</structuredBody></component></document>""".encode("utf-8")


def make_zip(tmp_path, data):
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("label.xml", data)
    raw = z.getvalue()
    sha = hashlib.sha256(raw).hexdigest()
    (tmp_path / sha).write_bytes(raw)
    return sha, str(tmp_path / sha)


def build_rebuild(tmp_path):
    """Two families, three versions: a real (tiny) Step E rebuild produced with the frozen code."""
    inv, paths = {}, {}
    base = [("34067-9", "INDICATIONS", "Used for pain."), ("34084-4", "ADVERSE REACTIONS", "Nausea was seen.")]
    var = base[:1] + [("34084-4", "ADVERSE REACTIONS", "Nausea and rash were seen.")]
    for setid, v, texts in ((SID_A, 1, base), (SID_A, 2, var), (SID_B, 1, base), (SID_B, 2, var)):
        sha, p = make_zip(tmp_path, xml(setid, v, texts))
        paths[sha] = [p]
        inv.setdefault(setid, []).append({"version": v, "states": "downloaded", "sha": sha, "member": "label.xml",
                                          "eff": "2024010%d" % v, "pub": "2024-01-0%d" % v})
    out = tmp_path / "rebuild"
    out.mkdir()
    R.run(str(out), inv, lambda shas: {s: paths.get(s, []) for s in shas}, scratch_dir=str(tmp_path / "scratch"), workers=0)
    return str(out), (lambda shas: {s: paths.get(s, []) for s in shas}), paths


# ----------------------------------------------------------------------------- hashes
def test_file_hash_pass_fail_missing_and_skip(tmp_path):
    a, b = tmp_path / "a.bin", tmp_path / "b.bin"
    a.write_bytes(b"alpha")
    b.write_bytes(b"beta")
    files = {"a": ("a.bin", hashlib.sha256(b"alpha").hexdigest()), "b": ("b.bin", "0" * 64), "c": ("c.bin", "1" * 64)}
    rep = V.Report(echo=False)
    V.check_file_hashes(rep, str(tmp_path), files)
    st = {c["name"]: c["status"] for c in rep.checks}
    assert st == {"sha256 a": "pass", "sha256 b": "fail", "sha256 c": "fail"}
    assert len(rep.failed) == 2


def test_report_skipped_is_not_a_pass(tmp_path):
    rep = V.Report(echo=False)
    rep.add("x", "skipped")
    rep.ok("y", True)
    assert rep.summary() == {"pass": 1, "fail": 0, "skipped": 1, "info": 0} and not rep.failed


# ----------------------------------------------------------------------------- split
def split_doc(sealed, opn):
    pop = sorted(set(sealed) | set(opn))
    return {"format": "gitrx-srlc-split-1", "seed": 7, "fraction": 0.3, "population_n": len(pop),
            "population_sha256": V.fingerprint(pop), "sealed_n": len(sealed), "sealed_sha256": V.fingerprint(sealed),
            "open_n": len(opn), "open_sha256": V.fingerprint(opn), "sealed": sealed, "open": opn}


def test_split_fingerprints_and_tamper(tmp_path):
    sealed, opn = [3, 5, 9], [1, 2, 4, 6, 7, 8]
    doc = split_doc(sealed, opn)
    p = tmp_path / "split.json"
    p.write_text(json.dumps(doc))
    expected = {k: doc[k] for k in ("population_sha256", "sealed_sha256", "open_sha256", "population_n", "sealed_n", "open_n", "seed")}
    rep = V.Report(echo=False)
    V.check_split(rep, str(p), expected)
    assert not rep.failed
    # moving one drug between the lists breaks the declared fingerprints
    doc2 = dict(doc, sealed=[3, 5, 8], open=[1, 2, 4, 6, 7, 9])
    p.write_text(json.dumps(doc2))
    rep = V.Report(echo=False)
    V.check_split(rep, str(p), expected)
    assert any("sealed_sha256" in c["name"] for c in rep.failed)
    assert any("open_sha256" in c["name"] for c in rep.failed)


def test_fingerprint_matches_the_srlc_split_module():
    from gitrx_core import srlc_split
    assert V.fingerprint([5, 1, 3]) == srlc_split.fingerprint([1, 3, 5])


# ----------------------------------------------------------------------------- frozen code
def test_parse_freeze_and_code_check(tmp_path):
    repo = tmp_path / "repo"
    (repo / "gitrx_core").mkdir(parents=True)
    lines = []
    for n in V.FROZEN_MODULES:
        (repo / "gitrx_core" / n).write_text("# " + n)
        lines.append("- %s %s" % (n, hashlib.sha256(("# " + n).encode()).hexdigest()))
    plan = tmp_path / "plan.md"
    plan.write_text("## Freeze 1 — sealed exam\n- spl_parser.py %s\n\n## Freeze 2 — sealed rerun (x)\nCode sha256:\n%s\n\n## Other\n- changes.py %s\n"
                    % ("a" * 64, "\n".join(lines), "b" * 64))
    assert V.parse_freeze(plan.read_text(), "Freeze 2 — sealed rerun")["spl_parser.py"] == hashlib.sha256(b"# spl_parser.py").hexdigest()
    rep = V.Report(echo=False)
    V.check_frozen_code(rep, str(repo), str(plan))
    assert not rep.failed
    (repo / "gitrx_core" / "changes.py").write_text("# edited")
    rep = V.Report(echo=False)
    V.check_frozen_code(rep, str(repo), str(plan))
    assert [c["name"] for c in rep.failed] == ["frozen code changes.py"]


def test_repo_matches_the_real_freeze_2_declaration():
    rep = V.Report(echo=False)
    V.check_frozen_code(rep)
    assert not rep.failed, rep.failed
    V.check_versions(rep)
    assert not rep.failed


# ----------------------------------------------------------------------------- rebuild shards, re-derivation
def test_shards_and_rederivation_and_tamper(tmp_path):
    out, resolve, paths = build_rebuild(tmp_path)
    rep = V.Report(echo=False)
    V.check_shards(rep, out)
    # the tiny rebuild has 2 shards (aa, bb), not 256; everything else must pass
    assert [c["name"] for c in rep.failed] == ["rebuild COMPLETE.json lists 256 shards"], rep.failed
    good, n = V.rederive_records(rep, out, resolve, 10, 1)
    assert (good, n) == (2, 2)
    # flip one byte of a shard: the sidecar check fails
    fp = os.path.join(out, "shards", "aa.sqlite")
    data = bytearray(open(fp, "rb").read())
    data[-1] ^= 0xFF
    open(fp, "wb").write(bytes(data))
    rep = V.Report(echo=False)
    V.check_shards(rep, out)
    assert "rebuild shards vs sidecars (sha256, bytes)" in [c["name"] for c in rep.failed]


def test_rederive_detects_a_corrupted_stored_record_and_raw(tmp_path):
    out, resolve, paths = build_rebuild(tmp_path)
    con = sqlite3.connect(os.path.join(out, "shards", "aa.sqlite"))
    con.execute("UPDATE pairs SET record_sha256=? WHERE setid=?", ("0" * 64, SID_A))
    con.commit()
    con.close()
    rep = V.Report(echo=False)
    good, n = V.rederive_records(rep, out, resolve, 10, 1)
    assert (good, n) == (1, 2)
    # corrupt the raw ZIP of SID_B v1: re-hash fails -> reported as a failure, not skipped
    sha = sqlite3.connect(os.path.join(out, "shards", "bb.sqlite")).execute(
        "SELECT raw_sha256 FROM versions WHERE setid=? AND version=1", (SID_B,)).fetchone()[0]
    open(paths[sha][0], "wb").write(b"corrupt")
    rep = V.Report(echo=False)
    good, n = V.rederive_records(rep, out, resolve, 10, 1, sample=[(SID_B, 1, 2)])
    assert good == 0 and "raw_hash_mismatch" in rep.failed[0]["detail"]


def test_sample_is_seeded_and_stable(tmp_path):
    out, _r, _p = build_rebuild(tmp_path)
    _all, s1 = V.sample_pairs(out, 1, 5)
    _all, s2 = V.sample_pairs(out, 1, 5)
    assert s1 == s2 and len(_all) == 2


# ----------------------------------------------------------------------------- metrics recomputation
def make_grading_dir(path, flip=False):
    path.mkdir()
    groups = G.SRLC_GROUPS
    rows = [{"status": "graded", "group": "WP", "section_found": True, "parser_located_fragments": 1, "parser_section_found": True},
            {"status": "graded", "group": "BW", "section_found": False, "parser_located_fragments": 1, "parser_section_found": False},
            {"status": "parser_only", "group": "WP", "parser_located_fragments": 0}]
    frags = [{"class": "main", "e2e": True, "group": "WP", "found": True, "found_strict": True, "found_ws": True,
              "parser": {"located": True, "found_render": True, "found_strict": True, "found_ws": True, "any_group_render": True,
                         "any_group_strict": True, "any_group_ws": True}},
             {"class": "main", "e2e": True, "group": "BW", "found": False, "found_strict": False, "found_ws": False,
              "parser": {"located": True, "found_render": False, "found_strict": False, "found_ws": False, "any_group_render": True,
                         "any_group_strict": False, "any_group_ws": True}},
             {"class": "main", "e2e": False, "group": "WP", "found": False, "found_strict": False, "found_ws": False,
              "parser": {"located": False, "not_locatable_reason": "never present in any captured version"}},
             {"class": "short", "e2e": True, "group": "WP", "found": True}]
    open(path / "rows.jsonl", "w").write("".join(json.dumps(r) + "\n" for r in rows))
    open(path / "fragments.jsonl", "w").write("".join(json.dumps(r) + "\n" for r in frags))
    open(path / "gitrx_only.jsonl", "w").write(json.dumps({"category": "gitrx_only_srlc_group"}) + "\n")
    rate, by = G.rate, lambda f: {g: f(g) for g in groups}
    sec = [r for r in rows if r["status"] == "graded"]
    summary = {
        "end_to_end": {
            "section_recall": rate(1, 2),
            "section_recall_by_group": by(lambda g: rate(sum(1 for r in sec if r["group"] == g and r["section_found"]),
                                                          sum(1 for r in sec if r["group"] == g))),
            "text_recall_main": {"render": rate(1, 2), "strict": rate(1, 2), "ws": rate(1, 2)}},
        "parser_accuracy": {
            "main_fragments_in_parser_gradable_entries": 3, "located": 2, "not_locatable": 1,
            "not_locatable_reasons": {"never present in any captured version": 1},
            "text_same_group": {"strict": rate(1, 2), "ws": rate(1, 2), "render": rate(1, 2)},
            "text_any_group": {"strict": rate(1, 2), "ws": rate(2, 2), "render": rate(2, 2)},
            "text_same_group_by_group": by(lambda g: {n: rate(1 if g == "WP" else 0, 1 if g in ("WP", "BW") else 0)
                                                      for n in ("strict", "ws", "render")}),
            "section_accuracy": rate(1, 2),
            "section_accuracy_by_group": by(lambda g: rate(1 if g == "WP" else 0, 1 if g in ("WP", "BW") else 0))},
        "gitrx_only": {"gitrx_only_srlc_group": 1, "matched_by_srlc": 5}}
    if flip:
        summary["parser_accuracy"]["text_same_group"]["render"] = rate(2, 2)
    json.dump(summary, open(path / "summary.json", "w"))


def test_metrics_recomputed_from_jsonl_match_and_detect_edits(tmp_path):
    make_grading_dir(tmp_path / "g")
    rep = V.Report(echo=False)
    V.compare_metrics(rep, "g", str(tmp_path / "g"))
    assert not rep.failed, rep.failed
    make_grading_dir(tmp_path / "h", flip=True)
    rep = V.Report(echo=False)
    V.compare_metrics(rep, "h", str(tmp_path / "h"))
    assert rep.failed and "text same-group" in rep.failed[0]["name"]


def test_diff_summaries_ignores_volatile_keys_only():
    a = {"generated_at": "x", "runtime_seconds": 1, "digest_build": {"built": 5}, "v": {"k": 1.0, "l": [1, 2]}}
    b = {"generated_at": "y", "runtime_seconds": 9, "digest_build": {"cached": 5}, "v": {"k": 1.0, "l": [1, 2]}}
    assert V.diff_summaries(a, b) == []
    b["v"]["l"] = [1, 3]
    assert V.diff_summaries(a, b) == ["/v/l"]


def test_jsonl_digest_is_order_insensitive(tmp_path):
    (tmp_path / "a").write_text('{"x":1,"y":2}\n{"z":3}\n')
    (tmp_path / "b").write_text('{"z":3}\n{"y":2,"x":1}\n')
    assert V.jsonl_digest(str(tmp_path / "a")) == V.jsonl_digest(str(tmp_path / "b"))


# ----------------------------------------------------------------------------- guards
def test_page_squash():
    assert V.page_squash(b"<p>Hi&nbsp;there &amp; <b>You</b></p>") == "hithere&you"


def test_harness_command_is_open_only_and_sealed_is_refused_by_harness():
    cmd = V.grade_cmd("either", "/o", "/c", "/i", "/r", 2)
    assert cmd[cmd.index("--split") + 1] == "open" and "--sealed-final" not in cmd and not any("sealed" in c.lower() for c in cmd)
    import argparse
    with pytest.raises(SystemExit, match="only --split open"):
        G.check_split_mode(argparse.Namespace(sealed_final=False, split="sealed"))
    with pytest.raises(SystemExit, match="refusing sealed grading"):
        G.check_split_mode(argparse.Namespace(sealed_final=True, split="sealed", parser_version=None, frozen_parser_marker=None))


def test_refuses_missing_ssd_missing_out_and_dirty_out(tmp_path, capsys):
    assert V.main(["quick", "--ssd", str(tmp_path / "nope")]) == 2
    assert V.main(["full", "--ssd", str(tmp_path)]) == 2          # present SSD but no --out
    (tmp_path / "dirty").mkdir()
    (tmp_path / "dirty" / "f").write_text("x")
    with pytest.raises(SystemExit, match="not an empty directory"):
        V.prepare_out(str(tmp_path / "dirty"))
    assert V.prepare_out(None) is None
    out = tmp_path / "new"
    assert V.prepare_out(str(out)) == str(out) and out.is_dir()


def test_tool_writes_no_bytecode_and_opens_databases_immutably(tmp_path):
    import sys
    assert sys.dont_write_bytecode is True
    db = tmp_path / "x.sqlite"
    sqlite3.connect(db).executescript("CREATE TABLE t(a); INSERT INTO t VALUES (1);")
    before = sorted(os.listdir(tmp_path))
    con = V.ro(str(db))
    assert con.execute("SELECT a FROM t").fetchone() == (1,)
    with pytest.raises(sqlite3.OperationalError):
        con.execute("INSERT INTO t VALUES (2)")
    con.close()
    assert sorted(os.listdir(tmp_path)) == before
