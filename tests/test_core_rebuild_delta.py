"""Tests for the delta evidence inventory source and byte-for-byte shard reuse in gitrx_core.rebuild."""
import copy
import hashlib
import json
import sqlite3

import pytest

from gitrx_core import rebuild as R
from gitrx_core import rebuild_delta as D
from test_core_rebuild import SID_A, SID_B, build, fixture, make_zip, shard_hashes, xml


def evidence_db(path, inv):
    con = sqlite3.connect(str(path))
    con.executescript('CREATE TABLE history_entries(id INTEGER PRIMARY KEY, setid, version, published_date);'
                      'CREATE TABLE versions(id INTEGER PRIMARY KEY, setid, version, state, selected_sha256, xml_member,'
                      ' effective_date);')
    for s, recs in inv.items():
        for r in recs:
            con.execute("INSERT INTO history_entries(setid,version,published_date) VALUES(?,?,?)", (s, r["version"], r["pub"]))
            con.execute("INSERT INTO versions(setid,version,state,selected_sha256,xml_member,effective_date) VALUES(?,?,?,?,?,?)",
                        (s, r["version"], r["states"], r["sha"], r["member"], r["eff"]))
    con.commit()
    con.close()


def delta_db(path, rows):
    con = sqlite3.connect(str(path))
    con.executescript(D.SCHEMA)
    con.execute("INSERT INTO delta_meta VALUES('format',?)", (R.DELTA_FORMAT,))
    for s, v, sha, mem, eff in rows:
        con.execute("INSERT INTO admissions(setid,version,raw_sha256,bytes,xml_member,effective_date,source_body_path,"
                    "admission_reason,admitted_at,ledger_requests_json,backfill_ledger_requests_json,gap_repair_json)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (s, v, sha, 1, mem, eff, "x", D.REASON, "t", "[]", "[]", "{}"))
    con.commit()
    con.close()


def setup(tmp_path):
    """Evidence-only inventory has A v5 pending without raw; the delta admits a body for it."""
    inv, resolve = fixture(tmp_path)
    base = [("34067-9", "INDICATIONS", "Used for pain.")]
    sha, p = make_zip(tmp_path, xml(SID_A, 5, base + [("34084-4", "ADVERSE REACTIONS", "Nausea, rash and edema.")]))
    paths = {s: resolve([s])[s] for s in [r["sha"] for rs in inv.values() for r in rs if r["sha"]]}
    paths[sha] = [p]
    res = lambda shas: {s: paths.get(s, []) for s in shas}
    ev = tmp_path / "ev.sqlite"
    evidence_db(ev, inv)
    dl = tmp_path / "delta.sqlite"
    delta_db(dl, [(SID_A, 5, sha, "label.xml", "20240105")])
    return inv, res, ev, dl, sha


def test_inventory_without_delta_is_unchanged_and_with_delta_applies(tmp_path):
    inv, res, ev, dl, sha = setup(tmp_path)
    plain = R.load_inventory(str(ev))
    assert R.load_inventory(str(ev), None) == plain
    v5 = [r for r in plain[SID_A] if r["version"] == 5][0]
    assert v5["sha"] is None and v5["states"] == "pending"
    withd = R.load_inventory(str(ev), str(dl))
    w5 = [r for r in withd[SID_A] if r["version"] == 5][0]
    assert w5["sha"] == sha and w5["member"] == "label.xml" and w5["eff"] == "20240105"
    assert w5["states"] == "delta-admitted+pending"
    assert withd[SID_B] == plain[SID_B] and [r for r in withd[SID_A] if r["version"] != 5] == \
        [r for r in plain[SID_A] if r["version"] != 5]


def test_delta_conflicts_are_refused(tmp_path):
    inv, res, ev, dl, sha = setup(tmp_path)
    other = tmp_path / "d2.sqlite"
    held = inv[SID_A][0]["sha"]
    delta_db(other, [(SID_A, 1, "f" * 64, "label.xml", None)])  # v1 already has a different raw artifact
    with pytest.raises(ValueError, match="conflicts"):
        R.load_inventory(str(ev), str(other))
    unknown = tmp_path / "d3.sqlite"
    delta_db(unknown, [(SID_A, 9, held, "label.xml", None)])
    with pytest.raises(ValueError, match="not in the evidence inventory"):
        R.load_inventory(str(ev), str(unknown))


def test_delta_database_is_append_only(tmp_path):
    inv, res, ev, dl, sha = setup(tmp_path)
    con = sqlite3.connect(str(dl))
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        con.execute("UPDATE admissions SET bytes=2")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        con.execute("DELETE FROM admissions")
    con.close()


def test_partial_rebuild_reuses_unaffected_shards_byte_for_byte(tmp_path):
    inv, res, ev, dl, sha = setup(tmp_path)
    base_inv = R.load_inventory(str(ev))
    base_out, _ = build(tmp_path, "base", base_inv, res)
    new_inv = R.load_inventory(str(ev), str(dl))
    full_out, full = build(tmp_path, "full", new_inv, res)  # reference: from-scratch build of the delta inventory
    reuse = R.reuse_base(str(base_out), {SID_A})
    part_out, part = build(tmp_path, "part", new_inv, res, reuse_from=reuse,
                           config_extra={"reuse_from": {"complete_sha256": reuse["complete_sha256"]}})
    h_base, h_full, h_part = shard_hashes(base_out), shard_hashes(full_out), shard_hashes(part_out)
    assert h_part == h_full                      # reuse + recompute equals a complete rebuild
    assert h_part["bb.sqlite"] == h_base["bb.sqlite"]   # untouched shard copied unchanged
    assert h_part["aa.sqlite"] != h_base["aa.sqlite"]   # affected shard recomputed
    assert part["shards"]["bb"]["reused_from_complete_sha256"] == reuse["complete_sha256"]
    assert "reused_from_complete_sha256" not in part["shards"]["aa"]
    assert part["status_counts"].get("no_raw", 0) == 0 and part["comparison_counts"] == full["comparison_counts"]
    assert part["counts"] == full["counts"]
    con = sqlite3.connect(str(part_out / "shards" / "aa.sqlite"))
    assert con.execute("SELECT status FROM versions WHERE setid=? AND version=5", (SID_A,)).fetchone()[0] == "complete"
    assert con.execute("SELECT count(*) FROM pairs WHERE setid=? AND to_version=5", (SID_A,)).fetchone()[0] == 1


def test_reuse_refuses_a_base_shard_with_a_different_family_count(tmp_path):
    inv, res, ev, dl, sha = setup(tmp_path)
    base_out, _ = build(tmp_path, "base", R.load_inventory(str(ev)), res)
    reuse = R.reuse_base(str(base_out), {SID_A})
    bigger = copy.deepcopy(R.load_inventory(str(ev), str(dl)))
    bigger["bb999999-2222-3333-4444-555555555555"] = [copy.deepcopy(bigger[SID_B][0])]  # shard bb now holds 2 setids
    with pytest.raises(RuntimeError, match="families"):
        build(tmp_path, "bad", bigger, res, reuse_from=reuse)


def test_only_shards_builds_just_those_and_never_finalizes(tmp_path):
    inv, res, ev, dl, sha = setup(tmp_path)
    new_inv = R.load_inventory(str(ev), str(dl))
    full_out, _ = build(tmp_path, "full", new_inv, res)
    out, manifest = build(tmp_path, "only", new_inv, res, only_shards={"aa"})
    assert manifest is None and not (out / "COMPLETE.json").exists()
    assert sorted(p.name for p in (out / "shards").glob("*.sqlite")) == ["aa.sqlite"]
    assert shard_hashes(out)["aa.sqlite"] == shard_hashes(full_out)["aa.sqlite"]
