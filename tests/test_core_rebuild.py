"""Tests for gitrx_core.rebuild (Step E full rebuild): determinism, resume, gaps, hash errors, frozen-code refusal."""
import hashlib
import io
import json
import sqlite3
import zipfile

import pytest

from gitrx_core import changes as C
from gitrx_core import rebuild as R

L = "2.16.840.1.113883.6.1"
SID_A = "aa000000-2222-3333-4444-555555555555"
SID_B = "bb000000-2222-3333-4444-555555555555"
SID_A2 = "aa111111-2222-3333-4444-555555555555"  # same shard "aa" as SID_A


def xml(setid, version, texts):
    secs = "".join(
        f'<component><section><code code="{c}" codeSystem="{L}"/><title>{t}</title>'
        f"<text><paragraph>{p}</paragraph></text></section></component>" for c, t, p in texts)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<document xmlns="urn:hl7-org:v3"><id root="99999999-0000-0000-0000-000000000000"/>
<code code="34391-3" codeSystem="{L}" displayName="HUMAN PRESCRIPTION DRUG LABEL"/>
<title>T</title><effectiveTime value="2024010{version}"/><setId root="{setid}"/><versionNumber value="{version}"/>
<component><structuredBody>{secs}</structuredBody></component></document>""".encode("utf-8")


def make_zip(tmp_path, data, name=None):
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("label.xml", data)
    raw = z.getvalue()
    sha = hashlib.sha256(raw).hexdigest()
    p = tmp_path / (name or sha)
    p.write_bytes(raw)
    return sha, str(p)


def fixture(tmp_path, bad_hash=False):
    """Family A: versions 1,2,4 captured (3 missing), 5 known but without raw.  Family B: version 1 only."""
    inv, paths = {}, {}

    def add(setid, v, texts, states="downloaded", raw=True):
        rec = {"version": v, "states": states, "sha": None, "member": "label.xml", "eff": "2024010%d" % v,
               "pub": "2024-01-0%d" % v}
        if raw:
            sha, p = make_zip(tmp_path, xml(setid, v, texts))
            rec["sha"] = sha
            paths[sha] = [p]
        inv.setdefault(setid, []).append(rec)
    base = [("34067-9", "INDICATIONS", "Used for pain."), ("34084-4", "ADVERSE REACTIONS", "Nausea was seen.")]
    add(SID_A, 1, base)
    add(SID_A, 2, base[:1] + [("34084-4", "ADVERSE REACTIONS", "Nausea and rash were seen.")])
    add(SID_A, 4, base[:1] + [("34084-4", "ADVERSE REACTIONS", "Nausea, rash and fatigue were seen.")])
    add(SID_A, 5, base, states="pending", raw=False)
    add(SID_B, 1, base)
    add(SID_A2, 1, base)
    if bad_hash:
        sha = inv[SID_B][0]["sha"]
        p = tmp_path / "corrupt"
        p.write_bytes(b"not the zip")
        paths[sha] = [str(p)]
    return inv, (lambda shas: {s: paths.get(s, []) for s in shas})


def build(tmp_path, name, inv, resolve, **kw):
    out = tmp_path / name
    out.mkdir(exist_ok=True)
    return out, R.run(str(out), inv, resolve, scratch_dir=str(tmp_path / ("scratch-" + name)), workers=0, **kw)


def shard_hashes(out):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((out / "shards").glob("*.sqlite"))}


def test_frozen_hashes_match_and_refusal(tmp_path):
    assert R.check_frozen()  # real repo files equal the Freeze 2 hashes
    bad = dict(R.FROZEN_SHA256, **{"changes.py": "0" * 64})
    with pytest.raises(R.FrozenCodeError):
        R.check_frozen(bad)
    inv, resolve = fixture(tmp_path)
    with pytest.raises(R.FrozenCodeError):
        R.run(str(tmp_path / "o"), inv, resolve, scratch_dir=str(tmp_path / "s"), workers=0, expected_frozen=bad)
    assert not (tmp_path / "o" / "shards").exists()  # refused before any work


def test_build_and_nonconsecutive_pairs(tmp_path):
    inv, resolve = fixture(tmp_path)
    out, manifest = build(tmp_path, "a", inv, resolve)
    assert manifest and (out / "COMPLETE.json").exists()
    assert manifest["status_counts"]["no_raw"] == 1
    con = sqlite3.connect(str(out / "shards" / "aa.sqlite"))
    pairs = {(r[0], r[1], r[2]): r for r in con.execute(
        "SELECT setid, from_version, to_version, consecutive, gap_versions, known_missing, comparison, record_sha256 "
        "FROM pairs")}
    assert set(pairs) == {(SID_A, 1, 2), (SID_A, 2, 4)}  # no invented 3 and no pair through unparsed v5
    assert pairs[(SID_A, 1, 2)][3] == 1 and pairs[(SID_A, 1, 2)][4] == 0
    assert pairs[(SID_A, 2, 4)][3] == 0 and pairs[(SID_A, 2, 4)][4] == 1
    assert json.loads(pairs[(SID_A, 2, 4)][5]) == []  # 3 is unknown to the inventory
    # the full record reproduces its hash and the verbatim added span
    rec = R.full_record(con, SID_A, 1, 2)
    assert C.record_hash(rec) == pairs[(SID_A, 1, 2)][7] == rec["record_sha256"]
    added = [s["text"] for c in rec["changes"] for s in c["added_spans"]]
    assert any("rash" in a for a in added)
    # family bookkeeping
    fam = con.execute("SELECT n_inventory, n_raw, n_parsed, n_pairs, n_nonconsecutive_pairs FROM families "
                      "WHERE setid=?", (SID_A,)).fetchone()
    assert fam == (4, 3, 3, 2, 1)
    # sections carry groups and channel text
    row = con.execute("SELECT srlc_group, text_h FROM sections WHERE setid=? AND version=1 AND title='ADVERSE REACTIONS'",
                      (SID_A,)).fetchone()
    assert row[0] == "AR" and "Nausea" in R.section_text(con, row[1])


def test_known_missing_version_between(tmp_path):
    inv, resolve = fixture(tmp_path)
    inv[SID_A][2]["sha"] = None  # v4 known but has no raw -> pair 2 -> ? none; add v6 captured
    sha, p = make_zip(tmp_path, xml(SID_A, 6, [("34067-9", "INDICATIONS", "Used for pain.")]))
    inv[SID_A].append({"version": 6, "states": "downloaded", "sha": sha, "member": "label.xml", "eff": "x", "pub": None})
    paths = {sha: [p]}
    old = resolve
    resolve2 = lambda shas: {**old(shas), **{s: paths[s] for s in shas if s in paths}}  # noqa: E731
    out, _ = build(tmp_path, "gap", inv, resolve2)
    con = sqlite3.connect(str(out / "shards" / "aa.sqlite"))
    rows = con.execute("SELECT from_version, to_version, consecutive, gap_versions, known_missing FROM pairs "
                       "WHERE setid=? ORDER BY 1,2", (SID_A,)).fetchall()
    assert rows == [(1, 2, 1, 0, "[]"), (2, 6, 0, 3, '[[4,"no_raw"],[5,"no_raw"]]')]


def test_raw_hash_mismatch_is_an_error_not_skipped(tmp_path):
    inv, resolve = fixture(tmp_path, bad_hash=True)
    out, manifest = build(tmp_path, "bad", inv, resolve)
    assert manifest["status_counts"]["raw_hash_mismatch"] == 1
    con = sqlite3.connect(str(out / "shards" / "bb.sqlite"))
    status, err = con.execute("SELECT status, error FROM versions WHERE setid=?", (SID_B,)).fetchone()
    assert status == "raw_hash_mismatch" and "has sha256" in err
    assert con.execute("SELECT count(*) FROM sections WHERE setid=?", (SID_B,)).fetchone()[0] == 0


def test_second_copy_rescues_corrupt_copy_with_warning(tmp_path):
    inv, resolve = fixture(tmp_path)
    sha = inv[SID_B][0]["sha"]
    bad = tmp_path / "corrupt"
    bad.write_bytes(b"junk")
    good = resolve([sha])[sha]
    res = lambda shas: {s: ([str(bad)] + good if s == sha else resolve([s])[s]) for s in shas}  # noqa: E731
    out, manifest = build(tmp_path, "rescue", inv, res)
    con = sqlite3.connect(str(out / "shards" / "bb.sqlite"))
    status, warn = con.execute("SELECT status, warnings FROM versions WHERE setid=?", (SID_B,)).fetchone()
    assert status == "complete" and "copy_hash_mismatch" in warn


def test_determinism_byte_identical(tmp_path):
    inv, resolve = fixture(tmp_path)
    out1, m1 = build(tmp_path, "d1", inv, resolve)
    out2, m2 = build(tmp_path, "d2", inv, resolve)
    assert shard_hashes(out1) == shard_hashes(out2)
    assert {k: v for k, v in m1["shards"].items()} == m2["shards"]


def test_resume_after_interruption(tmp_path):
    inv, resolve = fixture(tmp_path)
    ref_out, _ = build(tmp_path, "ref", inv, resolve)
    out = tmp_path / "res"
    out.mkdir()
    # first run stops after one shard
    assert R.run(str(out), inv, resolve, scratch_dir=str(tmp_path / "sc"), workers=0, max_shards=1) is None
    assert not (out / "COMPLETE.json").exists()
    done = sorted(p.name for p in (out / "shards").glob("*.done.json"))
    assert len(done) == 1
    # simulate a kill in the middle of the second shard: no sidecar, no final shard file is counted
    class Boom(BaseException):
        pass

    def hook(n, res):
        raise Boom()
    with pytest.raises(Boom):
        R.run(str(out), inv, resolve, scratch_dir=str(tmp_path / "sc"), workers=0, family_hook=hook)
    assert sorted(p.name for p in (out / "shards").glob("*.done.json")) == done
    assert not (out / "COMPLETE.json").exists()
    # resume finishes the rest and skips the finished shard (its file is untouched)
    finished = out / "shards" / done[0].replace(".done.json", ".sqlite")
    mtime = finished.stat().st_mtime_ns
    manifest = R.run(str(out), inv, resolve, scratch_dir=str(tmp_path / "sc"), workers=0)
    assert manifest and finished.stat().st_mtime_ns == mtime
    assert shard_hashes(out) == shard_hashes(ref_out)


def test_resume_refuses_different_configuration(tmp_path):
    inv, resolve = fixture(tmp_path)
    out = tmp_path / "cfg"
    out.mkdir()
    R.run(str(out), inv, resolve, scratch_dir=str(tmp_path / "sc"), workers=0, max_shards=1)
    inv2 = {k: v for k, v in inv.items() if k != SID_B}
    with pytest.raises(RuntimeError, match="differs"):
        R.run(str(out), inv2, resolve, scratch_dir=str(tmp_path / "sc"), workers=0)


def test_ssd_vanishing_aborts_without_counting_shard(tmp_path):
    inv, resolve = fixture(tmp_path)
    out = tmp_path / "gone"
    out.mkdir()
    with pytest.raises(R.SsdGone):
        R.run(str(out), inv, resolve, scratch_dir=str(tmp_path / "sc"), workers=0,
              require_dir=str(tmp_path / "does-not-exist"))
    assert not list((out / "shards").glob("*.done.json"))


def test_pilot_sample_is_seeded():
    inv = {"%02x" % i + "000000-0000": [] for i in range(200)}
    a = R.select_setids(inv, (0.05, 20261111))
    assert a == R.select_setids(inv, (0.05, 20261111)) and len(a) == 10
    assert a != R.select_setids(inv, (0.05, 1))


def test_unavailable_store_does_not_finalize_shard(tmp_path):
    inv, resolve = fixture(tmp_path)
    gone = lambda shas: {s: [str(tmp_path / "missing" / s)] for s in shas}  # noqa: E731
    out = tmp_path / "outage"
    out.mkdir()
    with pytest.raises(R.ShardAnomaly):
        R.run(str(out), inv, gone, scratch_dir=str(tmp_path / "sc"), workers=0)
    assert not list((out / "shards").glob("*.done.json"))
    # an I/O error (not just "not found") is also an anomaly even below the missing-file tolerance
    def eio(shas):
        d = tmp_path / "isdir"
        d.mkdir(exist_ok=True)
        return {s: [str(d)] if s == inv[SID_B][0]["sha"] else resolve([s])[s] for s in shas}
    with pytest.raises(R.ShardAnomaly):
        R.run(str(out), inv, eio, scratch_dir=str(tmp_path / "sc"), workers=0)
    # after the store is back the same directory resumes and finishes cleanly
    manifest = R.run(str(out), inv, resolve, scratch_dir=str(tmp_path / "sc"), workers=0)
    assert manifest and not manifest["status_counts"].get("raw_not_found")


def test_runlog_failure_does_not_abort(tmp_path):
    inv, resolve = fixture(tmp_path)
    out = tmp_path / "rl"
    out.mkdir()
    # make runlog.jsonl a directory: every event write fails, the run must still finish
    (out / "runlog.jsonl").mkdir()
    manifest = R.run(str(out), inv, resolve, scratch_dir=str(tmp_path / "sc"), workers=0)
    assert manifest
