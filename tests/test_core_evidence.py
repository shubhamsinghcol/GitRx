import hashlib
import json
import os
import shutil
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gitrx_core import evidence as ev  # noqa: E402


def make_gitrx(path, setid="s1", extra_version=False):
    c = sqlite3.connect(path)
    c.executescript("""
    CREATE TABLE families(setid TEXT PRIMARY KEY, name TEXT, metadata_json TEXT);
    CREATE TABLE versions(id INTEGER PRIMARY KEY, setid TEXT, version INTEGER, state TEXT,
        selected_sha256 TEXT, xml_member TEXT, effective_date TEXT, date_anomaly INTEGER,
        diagnostics_json TEXT);
    CREATE TABLE retrievals(id INTEGER PRIMARY KEY, observation_key TEXT, run_id INTEGER,
        version_id INTEGER, kind TEXT, url TEXT, final_url TEXT, retrieved_at TEXT, status INTEGER,
        headers_json TEXT, state TEXT, artifact_sha256 TEXT, error TEXT, attempt INTEGER,
        next_retry_at TEXT, validation_json TEXT);
    CREATE TABLE history_pages(run_id INTEGER, setid TEXT, page INTEGER, retrieval_id INTEGER,
        total_elements INTEGER, total_pages INTEGER);
    CREATE TABLE history_entries(run_id INTEGER, version_id INTEGER, retrieval_id INTEGER,
        published_raw TEXT, published_date TEXT);
    CREATE TABLE sections(id INTEGER PRIMARY KEY, own_text TEXT);
    CREATE TABLE backfill_parent_refs(version_id INTEGER PRIMARY KEY, reference_json TEXT);
    """)
    c.execute("INSERT INTO families VALUES (?,?,?)", (setid, "Drug", "{}"))
    c.execute("INSERT INTO versions VALUES (1,?,1,'downloaded','aa',NULL,'20200101',0,'[]')", (setid,))
    c.execute("INSERT INTO versions VALUES (2,?,2,'retained-parent',NULL,NULL,NULL,0,'[]')", (setid,))
    c.execute("INSERT INTO retrievals VALUES (1,?,1,NULL,'history','http://h',NULL,'2026-01-01T00:00:00',200,'[]','available','bb',NULL,1,NULL,NULL)",
              ("k-" + setid,))
    c.execute("INSERT INTO retrievals VALUES (2,?,1,1,'getFile_zip','http://z',NULL,'2026-01-01T00:00:01',200,'[]','available','aa',NULL,1,NULL,'{\"v\":1}')",
              ("z-" + setid,))
    c.execute("INSERT INTO history_pages VALUES (1,?,1,1,2,1)", (setid,))
    c.execute("INSERT INTO history_entries VALUES (1,1,1,'Jan  1, 2020','2020-01-01')")
    c.execute("INSERT INTO history_entries VALUES (1,2,1,'Feb 5, 2021 ','2021-02-05')")
    c.execute("INSERT INTO sections VALUES (1,'derived text')")
    c.execute("INSERT INTO backfill_parent_refs VALUES (2,?)",
              (json.dumps({"raw_path": "/x/aa", "version": {"parse_runs": [1, 2], "id": "s"}}),))
    if extra_version:
        c.execute("INSERT INTO versions VALUES (3,?,3,'downloaded','cc',NULL,'20220101',0,'[]')", (setid,))
    c.commit()
    c.close()


def make_ledger(path, wal=False, rows=3):
    c = sqlite3.connect(path)
    if wal:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA wal_autocheckpoint=0")
    c.execute("CREATE TABLE requests(id INTEGER PRIMARY KEY,url,started REAL,status,sha256,error,headers_json, bytes INTEGER DEFAULT 0)")
    for i in range(rows):
        c.execute("INSERT INTO requests(url,started,status,sha256,error,headers_json,bytes) VALUES (?,?,?,?,?,?,?)",
                  ("http://u/%d" % i, 1.5 + i, 200, "ab", None, "[]", 10))
    c.commit()
    return c


def run_build(tmp_path, sources, name="out", docs=()):
    out = str(tmp_path / (name + ".sqlite"))
    work = str(tmp_path / ("work-" + name))
    info = ev.build([str(s) for s in sources], out, work, list(docs), log=lambda m: None)
    return out, work, info


def test_import_dedupe_and_derived_skipped(tmp_path):
    a = tmp_path / "a.sqlite"
    b = tmp_path / "b.sqlite"
    make_gitrx(str(a))
    make_gitrx(str(b), extra_version=True)  # overlaps a, plus one new version
    out, work, info = run_build(tmp_path, [a, b])
    db = sqlite3.connect(out)
    assert db.execute("SELECT count(*) FROM versions").fetchone()[0] == 3  # 2 shared + 1 new
    assert db.execute("SELECT count(*) FROM retrievals").fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM row_provenance").fetchone()[0] == 2 * (1 + 2 + 2 + 1 + 2 + 1) + 1
    # derived table not imported, recorded as skipped
    assert db.execute("SELECT policy FROM source_tables WHERE table_name='sections' LIMIT 1").fetchone()[0] == "skip"
    # exact strings preserved (trailing space, double space)
    raws = {r[0] for r in db.execute("SELECT published_raw FROM history_entries")}
    assert raws == {"Jan  1, 2020", "Feb 5, 2021 "}
    # parse_runs stripped from parent refs, everything else kept
    ref = json.loads(db.execute("SELECT reference_json_without_parse_runs FROM backfill_parent_refs").fetchone()[0])
    assert "parse_runs" not in ref["version"] and ref["raw_path"] == "/x/aa"
    # natural-key refs resolved (no local ids)
    assert db.execute("SELECT setid, version FROM retrievals WHERE kind='getFile_zip'").fetchone() == ("s1", 1)
    assert db.execute("SELECT retrieval_key FROM history_pages").fetchone()[0] == "k-s1"


def test_symlink_alias_collapses(tmp_path):
    a = tmp_path / "a.sqlite"
    make_gitrx(str(a))
    link = tmp_path / "link.sqlite"
    os.symlink(a, link)
    out, _, _ = run_build(tmp_path, [a, link])
    db = sqlite3.connect(out)
    assert db.execute("SELECT count(*) FROM source_dbs").fetchone()[0] == 1
    assert len(json.loads(db.execute("SELECT aliases_json FROM source_dbs").fetchone()[0])) == 2


def test_wal_rows_are_read_and_input_untouched(tmp_path):
    p = tmp_path / "ledger.sqlite"
    conn = make_ledger(str(p), wal=True, rows=3)
    c2 = sqlite3.connect(str(p))  # main file only has the schema/page-1 state; rows live in the WAL
    assert os.path.getsize(str(p) + "-wal") > 0
    before = {f: hashlib.sha256(open(f, "rb").read()).hexdigest()
              for f in (str(p), str(p) + "-wal")}
    out, _, _ = run_build(tmp_path, [p])
    after = {f: hashlib.sha256(open(f, "rb").read()).hexdigest() for f in before}
    assert before == after
    db = sqlite3.connect(out)
    assert db.execute("SELECT count(*) FROM http_requests").fetchone()[0] == 3
    assert db.execute("SELECT wal_bytes FROM source_dbs").fetchone()[0] > 0
    c2.close()
    conn.close()


def test_http_requests_variants_and_unreadable(tmp_path):
    p = tmp_path / "l1.sqlite"
    make_ledger(str(p)).close()
    h = tmp_path / "l2.sqlite"
    c = sqlite3.connect(str(h))
    c.execute("CREATE TABLE http(id INTEGER PRIMARY KEY,url TEXT,started_at TEXT,start_epoch REAL,status INTEGER,sha256 TEXT,bytes INTEGER,error TEXT)")
    c.execute("INSERT INTO http VALUES (1,'http://u/0','2026',1.5,200,'ab',10,NULL)")
    c.commit()
    c.close()
    bad = tmp_path / "bad.sqlite"
    bad.write_bytes(b"not a database at all")
    out, _, _ = run_build(tmp_path, [p, h, bad])
    db = sqlite3.connect(out)
    assert db.execute("SELECT count(*) FROM http_requests").fetchone()[0] == 4  # different columns => distinct
    assert db.execute("SELECT status FROM source_dbs WHERE path LIKE '%bad.sqlite'").fetchone()[0] == "unreadable"


def test_determinism_two_builds_identical(tmp_path):
    a = tmp_path / "a.sqlite"
    b = tmp_path / "b.sqlite"
    make_gitrx(str(a))
    make_gitrx(str(b), setid="s2")
    make_ledger(str(tmp_path / "l.sqlite")).close()
    srcs = [a, b, tmp_path / "l.sqlite"]
    o1, _, _ = run_build(tmp_path, srcs, "one")
    o2, _, _ = run_build(tmp_path, list(reversed(srcs)), "two")
    assert open(o1, "rb").read() == open(o2, "rb").read()


def test_verify_passes_then_detects_tampering(tmp_path):
    a = tmp_path / "a.sqlite"
    make_gitrx(str(a))
    out, _, _ = run_build(tmp_path, [a])
    rep = ev.verify(out, str(tmp_path / "vw"), log=lambda m: None)
    assert rep["ok"], rep["problems"]
    assert rep["tables_checked"] == rep["tables_ok"] > 0
    # 1: change content of an imported row
    shutil.copy(out, str(tmp_path / "t1.sqlite"))
    db = sqlite3.connect(str(tmp_path / "t1.sqlite"))
    db.execute("UPDATE history_entries SET published_raw='Jan 1, 2020'")
    db.commit()
    db.close()
    assert not ev.verify(str(tmp_path / "t1.sqlite"), str(tmp_path / "vw"), log=lambda m: None)["ok"]
    # 2: drop a provenance row (row parity)
    shutil.copy(out, str(tmp_path / "t2.sqlite"))
    db = sqlite3.connect(str(tmp_path / "t2.sqlite"))
    db.execute("DELETE FROM row_provenance WHERE (table_id, src_rowid) IN (SELECT table_id, src_rowid FROM row_provenance LIMIT 1)")
    db.commit()
    db.close()
    assert not ev.verify(str(tmp_path / "t2.sqlite"), str(tmp_path / "vw"), log=lambda m: None)["ok"]
    # 3: the source changes after the build
    c = sqlite3.connect(str(a))
    c.execute("INSERT INTO families VALUES ('s9','New','{}')")
    c.commit()
    c.close()
    assert not ev.verify(out, str(tmp_path / "vw"), log=lambda m: None)["ok"]


def test_documents_registered_and_stored(tmp_path):
    d = tmp_path / "docs"
    d.mkdir()
    (d / "small.json").write_text('{"a": 1}')
    (d / "big.json").write_text("x" * 100)
    docs = ev.collect_docs([str(d / "small.json"), str(d / "big.json")], [], 50)
    a = tmp_path / "a.sqlite"
    make_gitrx(str(a))
    out, _, _ = run_build(tmp_path, [a], docs=docs)
    db = sqlite3.connect(out)
    rows = {os.path.basename(r[0]): r[1:] for r in db.execute("SELECT path, stored, sha256, content FROM json_documents")}
    assert rows["small.json"][0] == 1 and rows["small.json"][2] == b'{"a": 1}'
    assert rows["big.json"][0] == 0 and rows["big.json"][2] is None
    assert rows["big.json"][1] == hashlib.sha256(b"x" * 100).hexdigest()
    assert ev.verify(out, str(tmp_path / "vw"), log=lambda m: None)["ok"]
