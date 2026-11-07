"""Resumable complete ingest of the accepted 20-family slice."""
import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import urllib.parse

from .archive import BASE, Fetcher, artifact_bytes, now, validate_label
from .db import connect, dumps


def version_id(conn, setid, number):
    conn.execute("INSERT OR IGNORE INTO versions(setid,version) VALUES(?,?)", (setid.lower(), int(number)))
    return conn.execute("SELECT id FROM versions WHERE setid=? AND version=?", (setid.lower(), int(number))).fetchone()[0]


def import_step_a(conn, root):
    """Reuse only exact, independently rehashed raw observations; no invented fetch."""
    log = Path(root) / "research/evidence/retrievals.jsonl"
    if not log.exists():
        return
    fetcher = Fetcher(conn, root, None)
    for line in log.read_text().splitlines():
        record = json.loads(line)
        key = "step-a:" + hashlib.sha256(dumps(record).encode()).hexdigest()
        if conn.execute("SELECT 1 FROM retrievals WHERE observation_key=?", (key,)).fetchone():
            continue
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(record["url"]).query)
        vid = None
        if "getFile.cfm" in record["url"]:
            sid = query.get("setid", [""])[0].lower()
            if conn.execute("SELECT 1 FROM families WHERE setid=?", (sid,)).fetchone():
                vid = version_id(conn, sid, query["version"][0])
        body = (Path(root) / "research/evidence/raw" / record["sha256"]).read_bytes()
        if hashlib.sha256(body).hexdigest() != record["sha256"]:
            raise RuntimeError("Step A body no longer matches logged hash")
        fetcher.record(record, body, "label" if "getFile.cfm" in record["url"] else "step_a_metadata", vid, key)


def fetch_history(conn, fetcher, run_id, family):
    sid = family["setid"]
    if conn.execute("SELECT 1 FROM history_pages WHERE run_id=? AND setid=? AND page=1", (run_id, sid)).fetchone():
        # Reconstruct all pages for a partially completed family before resuming.
        first = conn.execute("SELECT total_pages FROM history_pages WHERE run_id=? AND setid=? AND page=1", (run_id, sid)).fetchone()
        total_pages = first[0]
    else:
        total_pages = 1
    page = 1
    while page <= total_pages:
        existing = conn.execute("SELECT * FROM history_pages WHERE run_id=? AND setid=? AND page=?", (run_id, sid, page)).fetchone()
        if existing:
            page += 1
            continue
        url = BASE + f"services/v2/spls/{sid}/history.json?pagesize=100&page={page}"
        response = fetcher.fetch(url, "history")
        if response["state"] != "available":
            raise RuntimeError(f"History not retrievable by history.json for {sid}, HTTP {response['status']}; run remains resumable")
        payload = json.loads(artifact_bytes(conn, fetcher.root, response["artifact_sha256"]))
        if payload["data"]["spl"]["setid"].lower() != sid:
            raise RuntimeError(f"History identity mismatch for {sid}")
        total_pages = int(payload["metadata"]["total_pages"])
        total_elements = int(payload["metadata"]["total_elements"])
        for entry in payload["data"]["history"]:
            vid = version_id(conn, sid, entry["spl_version"])
            raw = entry.get("published_date", "")
            try:
                date = datetime.strptime(raw, "%b %d, %Y").date().isoformat()
            except ValueError:
                date = None
            conn.execute("INSERT OR IGNORE INTO history_entries VALUES(?,?,?,?,?)", (run_id, vid, response["id"], raw, date))
        conn.execute("INSERT INTO history_pages VALUES(?,?,?,?,?,?)", (run_id, sid, page, response["id"], total_elements, total_pages))
        conn.commit()
        page += 1
    actual = conn.execute("SELECT COUNT(DISTINCT h.version_id) FROM history_entries h JOIN versions v ON v.id=h.version_id WHERE h.run_id=? AND v.setid=?", (run_id, sid)).fetchone()[0]
    expected = conn.execute("SELECT total_elements FROM history_pages WHERE run_id=? AND setid=? AND page=1", (run_id, sid)).fetchone()[0]
    if actual != expected:
        raise RuntimeError(f"History pagination count mismatch {sid}: {actual} != {expected}; source may have changed during paging")
    print(dumps({"stage": "history", "family": family["name"], "listed": actual, "pages": total_pages}), flush=True)


def flag_dates(conn, run_id):
    rows = conn.execute("""SELECT DISTINCT v.id,v.setid,v.version,h.published_date FROM versions v
      JOIN history_entries h ON h.version_id=v.id WHERE h.run_id=? ORDER BY v.setid,v.version""", (run_id,)).fetchall()
    for lower in rows:
        for higher in rows:
            if (lower["setid"] == higher["setid"] and lower["version"] < higher["version"]
                and lower["published_date"] and higher["published_date"]
                and lower["published_date"] > higher["published_date"]):
                conn.execute("INSERT OR IGNORE INTO date_anomalies VALUES(?,?,?,?,?)",
                             (run_id, lower["id"], higher["id"], lower["published_date"], higher["published_date"]))
                conn.execute("UPDATE versions SET date_anomaly=1 WHERE id IN (?,?)", (lower["id"], higher["id"]))
    conn.commit()


def ingest_version(conn, fetcher, row, retry_due=False):
    prior = conn.execute("SELECT * FROM retrievals WHERE version_id=? AND kind IN ('label','archive_route') ORDER BY id DESC", (row["id"],)).fetchall()
    successful = [r for r in prior if r["status"] == 200 and r["state"] in ("available", "identity_conflict")]
    if not successful:
        latest = prior[0] if prior else None
        if latest:
            if latest["state"] == "unavailable":
                conn.execute("UPDATE versions SET state='unavailable' WHERE id=?", (row["id"],))
                conn.commit()
                if not retry_due or not latest["next_retry_at"] or latest["next_retry_at"] > now():
                    return
        url = BASE + "getFile.cfm?" + urllib.parse.urlencode({"type": "zip", "setid": row["setid"], "version": row["version"]})
        response = fetcher.fetch(url, "label", row["id"])
        if response["state"] != "available":
            conn.execute("UPDATE versions SET state='unavailable',diagnostics_json=? WHERE id=?",
                         (dumps([f"retrieval {response['id']}: HTTP {response['status']}: {response['error']}"]), row["id"]))
            conn.commit()
            return
        successful = [response]
    results = []
    for response in successful:
        validation = (json.loads(response["validation_json"]) if response["validation_json"]
                      else validate_label(conn, fetcher.root, response["id"], row["setid"], row["version"]))
        results.append((response, validation))
    valid = [(r, v) for r, v in results if v["state"] == "downloaded"]
    hashes = {r["artifact_sha256"] for r, v in valid}
    diagnostics = [d for r, v in results for d in v["diagnostics"]]
    if any(v["state"] == "identity_mismatch" for r, v in results):
        state = "identity_mismatch"
    elif len(hashes) > 1:
        state = "byte_conflict"
        diagnostics.append(f"multiple raw hashes for requested identity: {sorted(hashes)}")
    elif not valid:
        state = "unavailable"
    else:
        state = "downloaded"
    if state == "downloaded":
        r, v = valid[0]
        conn.execute("UPDATE versions SET state=?,selected_sha256=?,xml_member=?,effective_date=?,diagnostics_json=? WHERE id=?",
                     (state, r["artifact_sha256"], v["xml_member"], v["effective_date"], dumps(diagnostics), row["id"]))
    else:
        conn.execute("UPDATE versions SET state=?,diagnostics_json=? WHERE id=?", (state, dumps(diagnostics), row["id"]))
    conn.commit()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--retry-due", action="store_true")
    parser.add_argument("--refresh-history", action="store_true", help="Start a new observation run with fresh histories")
    args = parser.parse_args()
    root = args.root.resolve()
    conn = connect(root / "data/gitrx.sqlite")
    families = json.loads((root / "research/families.json").read_text())["families"]
    for family in families:
        conn.execute("INSERT OR IGNORE INTO families(setid,name,metadata_json) VALUES(?,?,?)", (family["setid"], family["name"], dumps(family)))
    conn.commit()
    import_step_a(conn, root)
    run = conn.execute("SELECT * FROM ingest_runs ORDER BY id DESC LIMIT 1").fetchone()
    if args.refresh_history and run and run["completed_at"]:
        run = None
    if run:
        run_id = run["id"]
    else:
        run_id = conn.execute("INSERT INTO ingest_runs(started_at) VALUES(?)", (now(),)).lastrowid
        conn.commit()
    fetcher = Fetcher(conn, root, run_id)
    for family in families:
        fetch_history(conn, fetcher, run_id, family)
    conn.execute("UPDATE ingest_runs SET histories_complete=1 WHERE id=?", (run_id,))
    conn.commit()
    flag_dates(conn, run_id)
    for family in families:
        rows = conn.execute("""SELECT DISTINCT v.* FROM versions v JOIN history_entries h ON v.id=h.version_id
          WHERE h.run_id=? AND v.setid=? ORDER BY v.version""", (run_id, family["setid"])).fetchall()
        for i, row in enumerate(rows, 1):
            ingest_version(conn, fetcher, row, args.retry_due)
            if i % 10 == 0:
                print(dumps({"stage": "downloads", "family": family["name"], "processed": i, "of": len(rows)}), flush=True)
        counts = dict(conn.execute("""SELECT state,COUNT(*) FROM versions WHERE id IN
          (SELECT version_id FROM history_entries WHERE run_id=?) AND setid=? GROUP BY state""", (run_id, family["setid"])))
        print(dumps({"stage": "family_complete", "family": family["name"], "listed": len(rows), "states": counts}), flush=True)
    conn.execute("UPDATE ingest_runs SET completed_at=? WHERE id=?", (now(), run_id))
    conn.commit()
    print(dumps({"stage": "ingest_complete", "run_id": run_id}), flush=True)


if __name__ == "__main__":
    main()
