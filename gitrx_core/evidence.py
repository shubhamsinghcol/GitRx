"""Consolidated, deduplicated SOURCE-OBSERVATION evidence database ('gitrx-evidence-1').

Reads many mixed SQLite files (read-only), copies only source observations
(derived parse data is deliberately skipped), deduplicates by exact row content,
and records per-source-row provenance so row parity can be verified.

Nothing here modifies any input. WAL-bearing inputs are copied (db + wal) to a
scratch directory and read from the copy so committed WAL rows are included.

CLI (stdlib only):
  python -I -B gitrx_core/evidence.py build  --sources-list F --out DB --work-dir D [...]
  python -I -B gitrx_core/evidence.py verify --db DB [...]
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time

FORMAT = "gitrx-evidence-1"
SQLITE_MAGIC = b"SQLite format 3\x00"
BATCH = 20000

# --------------------------------------------------------------------------
# Entities: normalized, deduplicated tables. (cols exclude the integer id.)
# --------------------------------------------------------------------------
ENTITIES = {
    "http_requests": dict(
        cols=["url", "origin_url", "started_epoch", "started_at", "completed_epoch", "status",
              "sha256", "bytes", "error", "headers_json", "final_url", "archive_request",
              "complete", "latency_seconds", "spacing_seconds", "connection_serial",
              "pacing_signals_json"],
        order=["url", "started_epoch", "started_at"]),
    "retrievals": dict(
        cols=["observation_key", "kind", "url", "final_url", "retrieved_at", "status",
              "headers_json", "state", "artifact_sha256", "error", "attempt", "next_retry_at",
              "validation_json", "setid", "version"],
        order=["observation_key", "retrieved_at", "url"]),
    "versions": dict(
        cols=["setid", "version", "state", "selected_sha256", "xml_member", "effective_date",
              "date_anomaly", "diagnostics_json"],
        order=["setid", "version", "state"]),
    "families": dict(cols=["setid", "name", "metadata_json"], order=["setid", "name"]),
    "history_pages": dict(
        cols=["setid", "page", "retrieval_key", "total_elements", "total_pages"],
        order=["setid", "page", "retrieval_key"]),
    "history_entries": dict(
        cols=["setid", "version", "retrieval_key", "published_raw", "published_date"],
        order=["setid", "version", "retrieval_key"]),
    "document_type_observations": dict(
        cols=["retrieval_key", "xml_member", "code", "code_system", "display_name", "allowed",
              "xml_setid", "xml_version"],
        order=["retrieval_key", "xml_member", "code"]),
    "archive_route_sources": dict(
        cols=["retrieval_key", "archive_id", "listing_retrieval_key", "listed_setid",
              "listed_version", "provenance_json"],
        order=["retrieval_key", "archive_id", "listed_setid"]),
    "identity_conflicts": dict(
        cols=["retrieval_key", "xml_member", "requested_setid", "requested_version",
              "observed_setid", "observed_version", "reason"],
        order=["retrieval_key", "xml_member", "reason"]),
    "bulk_member_sources": dict(cols=["retrieval_key", "provenance_json"],
                                order=["retrieval_key", "provenance_json"]),
    "backfill_http": dict(cols=["retrieval_key", "observation_json"],
                          order=["retrieval_key", "observation_json"]),
    "backfill_versions": dict(cols=["setid", "version", "state", "pin", "details_json"],
                              order=["setid", "version", "state"]),
    # reference_json with the derived 'parse_runs' list of the retained version removed.
    "backfill_parent_refs": dict(cols=["setid", "version", "reference_json_without_parse_runs"],
                                 order=["setid", "version"]),
    "release1_history_passes": dict(
        cols=["setid", "pass_number", "started_at", "status", "signature", "canonical_json",
              "anomalies_json"], order=["setid", "pass_number", "signature"]),
    "release1_history_pages": dict(
        cols=["setid", "pass_number", "page", "retrieval_key", "response_json"],
        order=["setid", "pass_number", "page"]),
    "release1_history_selection": dict(cols=["setid", "pass_number"], order=["setid", "pass_number"]),
    "release1_current_history": dict(cols=["setid", "version", "pass_number"],
                                     order=["setid", "version", "pass_number"]),
    "tranche_publication_observations": dict(
        cols=["setid", "version", "ordinal", "publication_raw", "publication_date"],
        order=["setid", "version", "ordinal"]),
    "release1_listing_only_targets": dict(cols=["setid", "version", "provenance_json"],
                                          order=["setid", "version"]),
    "release1_http_provenance": dict(cols=["retrieval_key", "provenance_json"],
                                     order=["retrieval_key", "provenance_json"]),
    "tranche_targets": dict(cols=["cohort", "setid", "version", "status", "route"],
                            order=["cohort", "setid", "version"]),
    "tranche_transport_provenance": dict(
        cols=["retrieval_key", "source_namespace", "http_id", "metadata_sha256"],
        order=["retrieval_key", "source_namespace", "http_id"]),
    "tranche_label_metadata": dict(
        cols=["setid", "version", "categories_json", "approval_numbers_json", "labeler_role",
              "metadata_json"], order=["setid", "version"]),
    # Generic: any other source table, one JSON object per row (all columns, sorted keys).
    "aux_rows": dict(cols=["table_name", "row_json"], order=["table_name", "row_json"]),
}
ENTITY_NAMES = sorted(ENTITIES)
ENTITY_CODE = {n: i + 1 for i, n in enumerate(ENTITY_NAMES)}

_V = "LEFT JOIN versions v ON v.id={a}.version_id"
_R = "LEFT JOIN retrievals r ON r.id={a}.retrieval_id"

# table -> (entity, sql). First column is always the base-table rowid.
GITRX_SPECS = {
    "retrievals": ("retrievals",
        "SELECT r.rowid, r.observation_key, r.kind, r.url, r.final_url, r.retrieved_at, r.status, "
        "r.headers_json, r.state, r.artifact_sha256, r.error, r.attempt, r.next_retry_at, "
        "r.validation_json, v.setid, v.version FROM retrievals r " + _V.format(a="r") +
        " ORDER BY r.rowid"),
    "versions": ("versions",
        "SELECT v.rowid, v.setid, v.version, v.state, v.selected_sha256, v.xml_member, "
        "v.effective_date, v.date_anomaly, v.diagnostics_json FROM versions v ORDER BY v.rowid"),
    "families": ("families",
        "SELECT f.rowid, f.setid, f.name, f.metadata_json FROM families f ORDER BY f.rowid"),
    "history_pages": ("history_pages",
        "SELECT h.rowid, h.setid, h.page, r.observation_key, h.total_elements, h.total_pages "
        "FROM history_pages h " + _R.format(a="h") + " ORDER BY h.rowid"),
    "history_entries": ("history_entries",
        "SELECT h.rowid, v.setid, v.version, r.observation_key, h.published_raw, h.published_date "
        "FROM history_entries h " + _V.format(a="h") + " " + _R.format(a="h") + " ORDER BY h.rowid"),
    "document_type_observations": ("document_type_observations",
        "SELECT d.rowid, r.observation_key, d.xml_member, d.code, d.code_system, d.display_name, "
        "d.allowed, d.xml_setid, d.xml_version FROM document_type_observations d " +
        _R.format(a="d") + " ORDER BY d.rowid"),
    "archive_route_sources": ("archive_route_sources",
        "SELECT a.rowid, r.observation_key, a.archive_id, l.observation_key, a.listed_setid, "
        "a.listed_version, a.provenance_json FROM archive_route_sources a " + _R.format(a="a") +
        " LEFT JOIN retrievals l ON l.id=a.listing_retrieval_id ORDER BY a.rowid"),
    "identity_conflicts": ("identity_conflicts",
        "SELECT c.rowid, r.observation_key, c.xml_member, c.requested_setid, c.requested_version, "
        "c.observed_setid, c.observed_version, c.reason FROM identity_conflicts c " +
        _R.format(a="c") + " ORDER BY c.rowid"),
    "bulk_member_sources": ("bulk_member_sources",
        "SELECT b.rowid, r.observation_key, b.provenance_json FROM bulk_member_sources b " +
        _R.format(a="b") + " ORDER BY b.rowid"),
    "backfill_http": ("backfill_http",
        "SELECT b.rowid, r.observation_key, b.observation_json FROM backfill_http b " +
        _R.format(a="b") + " ORDER BY b.rowid"),
    "backfill_versions": ("backfill_versions",
        "SELECT b.rowid, v.setid, v.version, b.state, b.pin, b.details_json FROM backfill_versions b "
        "LEFT JOIN versions v ON v.id=b.version_id ORDER BY b.rowid"),
    "backfill_parent_refs": ("backfill_parent_refs",
        "SELECT b.rowid, v.setid, v.version, b.reference_json FROM backfill_parent_refs b "
        "LEFT JOIN versions v ON v.id=b.version_id ORDER BY b.rowid"),
    "release1_history_passes": ("release1_history_passes",
        "SELECT p.rowid, p.setid, p.pass_number, p.started_at, p.status, p.signature, "
        "p.canonical_json, p.anomalies_json FROM release1_history_passes p ORDER BY p.rowid"),
    "release1_history_pages": ("release1_history_pages",
        "SELECT h.rowid, p.setid, p.pass_number, h.page, r.observation_key, h.response_json "
        "FROM release1_history_pages h LEFT JOIN release1_history_passes p ON p.id=h.pass_id " +
        _R.format(a="h") + " ORDER BY h.rowid"),
    "release1_history_selection": ("release1_history_selection",
        "SELECT s.rowid, s.setid, p.pass_number FROM release1_history_selection s "
        "LEFT JOIN release1_history_passes p ON p.id=s.pass_id ORDER BY s.rowid"),
    "release1_current_history": ("release1_current_history",
        "SELECT c.rowid, c.setid, c.version, p.pass_number FROM release1_current_history c "
        "LEFT JOIN release1_history_passes p ON p.id=c.pass_id ORDER BY c.rowid"),
    "tranche_publication_observations": ("tranche_publication_observations",
        "SELECT t.rowid, t.setid, t.version, t.ordinal, t.publication_raw, t.publication_date "
        "FROM tranche_publication_observations t ORDER BY t.rowid"),
    "release1_listing_only_targets": ("release1_listing_only_targets",
        "SELECT t.rowid, t.setid, t.version, t.provenance_json FROM release1_listing_only_targets t "
        "ORDER BY t.rowid"),
    "release1_http_provenance": ("release1_http_provenance",
        "SELECT p.rowid, r.observation_key, p.provenance_json FROM release1_http_provenance p " +
        _R.format(a="p") + " ORDER BY p.rowid"),
    "tranche_targets": ("tranche_targets",
        "SELECT t.rowid, t.cohort, t.setid, t.version, t.status, t.route FROM tranche_targets t "
        "ORDER BY t.rowid"),
    "tranche_transport_provenance": ("tranche_transport_provenance",
        "SELECT t.rowid, r.observation_key, t.source_namespace, t.http_id, t.metadata_sha256 "
        "FROM tranche_transport_provenance t " + _R.format(a="t") + " ORDER BY t.rowid"),
    "tranche_label_metadata": ("tranche_label_metadata",
        "SELECT m.rowid, v.setid, v.version, m.categories_json, m.approval_numbers_json, "
        "m.labeler_role, m.metadata_json FROM tranche_label_metadata m " + _V.format(a="m") +
        " ORDER BY m.rowid"),
}

LEDGER_TABLES = {"requests", "http"}

# Tables imported generically (one JSON object per row).
AUX_TABLES = {
    "ingest_runs", "history_captures", "artifacts", "document_type_allowlist", "meta", "metadata",
    "families", "queue", "batches", "pages", "targets", "tranches", "exports", "retry_tranches",
    "family_events", "listing_anomalies", "cache", "imports", "run", "ledger_reconstruction",
    "transport_state", "tasks", "history_confirmations", "objects", "originals", "evictions",
    "cohort_cache", "cohort_clusters", "cohort_families", "cohort_history_rows", "cohort_http",
    "cohort_old_attempts", "cohort_run", "cohort_targets",
}

# Derived parse data / operational or internal tables: deliberately NOT imported.
SKIP_TABLES = {
    "sections", "narrative_blocks", "narrative_tables", "section_excerpts", "parse_runs",
    "block_instances", "content_blobs", "content_store_meta", "excerpt_instances",
    "section_instances", "table_instances", "derived_artifacts", "section_contexts",
    "section_identities", "context_confidences", "context_policies", "identity_policies",
    "section_code_observations", "section_code_registry", "source_quirks", "snapshots",
    "release1_parse_selection", "tranche_parse_selection", "date_anomalies", "checks",
    "operations", "sqlite_sequence", "family_label_status",
}

HTTP_OUT_FROM = {
    # out col -> candidate source column names (first present wins)
    "url": ["url"], "origin_url": ["origin_url"],
    "started_epoch": ["started", "start_epoch"], "started_at": ["started_at"],
    "completed_epoch": ["completed_epoch"], "status": ["status"], "sha256": ["sha256"],
    "bytes": ["bytes"], "error": ["error"], "headers_json": ["headers_json"],
    "final_url": ["final_url"], "archive_request": ["archive_request"],
    "complete": ["complete"], "latency_seconds": ["latency_seconds"],
    "spacing_seconds": ["spacing_seconds"], "connection_serial": ["connection_serial"],
    "pacing_signals_json": ["pacing_signals_json"],
}


# --------------------------------------------------------------------------
# hashing / encoding
# --------------------------------------------------------------------------
def _enc(v):
    if isinstance(v, (bytes, bytearray, memoryview)):
        return {"$b": bytes(v).hex()}
    return v


def row_hash(entity: str, vals) -> bytes:
    s = json.dumps([entity, [_enc(v) for v in vals]], separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(s.encode("ascii")).digest()


def sha256_file(path: str) -> str:
    m = hashlib.sha256()
    with open(path, "rb", buffering=0) as f:
        while True:
            b = f.read(8 << 20)
            if not b:
                break
            m.update(b)
    return m.hexdigest()


def strip_parse_runs(reference_json):
    """Remove derived version.parse_runs from a parent-ref JSON string (all else verbatim)."""
    if reference_json is None:
        return None
    try:
        j = json.loads(reference_json)
    except Exception:
        return reference_json
    if isinstance(j, dict) and isinstance(j.get("version"), dict) and "parse_runs" in j["version"]:
        del j["version"]["parse_runs"]
    return json.dumps(j, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# --------------------------------------------------------------------------
# source access
# --------------------------------------------------------------------------
class Source:
    """A read-only handle to one input SQLite (WAL-aware via a scratch copy)."""

    def __init__(self, path: str, tmproot: str, serial: int):
        self.path = path
        self.conn = None
        self.error = None
        self.wal_path = None
        self.wal_bytes = None
        self.wal_sha256 = None
        self.tmpdir = None
        self.open_path = path
        wal = None
        for cand in (path + "-wal", os.path.splitext(path)[0] + "-wal"):
            if os.path.isfile(cand) and os.path.getsize(cand) > 0:
                wal = cand
                break
        try:
            with open(path, "rb") as f:
                if f.read(16) != SQLITE_MAGIC:
                    raise sqlite3.DatabaseError("not a SQLite file")
            if wal:
                self.wal_path = wal
                self.wal_bytes = os.path.getsize(wal)
                self.wal_sha256 = sha256_file(wal)
                self.tmpdir = os.path.join(tmproot, "walcopy-%d" % serial)
                os.makedirs(self.tmpdir, exist_ok=True)
                dst = os.path.join(self.tmpdir, os.path.basename(path))
                shutil.copyfile(path, dst)
                shutil.copyfile(wal, dst + "-wal")
                self.conn = sqlite3.connect(dst)
                self.conn.execute("PRAGMA query_only=1")
                self.open_path = dst
            else:
                self.conn = sqlite3.connect("file:%s?mode=ro&immutable=1" % _uri_quote(path), uri=True)
            self.conn.text_factory = lambda b: b.decode("utf-8", "surrogatepass")
            self.conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        except Exception as e:  # unreadable / corrupt input
            self.error = "%s: %s" % (type(e).__name__, e)
            if self.conn is not None:
                try:
                    self.conn.close()
                except Exception:
                    pass
            self.conn = None

    def tables(self):
        return sorted(r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"))

    def close(self):
        if self.conn is not None:
            try:
                self.conn.close()
            except Exception:
                pass
        if self.tmpdir:
            shutil.rmtree(self.tmpdir, ignore_errors=True)


def _uri_quote(p):
    from urllib.parse import quote
    return quote(p, safe="/")


def plan_source(conn, tables):
    """Return list of dicts: table, policy(entity|aux|skip|unclassified), entity, reason."""
    tset = set(tables)
    is_gitrx = {"retrievals", "versions"} <= tset
    is_derived_export = ("versions" in tset) and not is_gitrx
    out = []
    for t in tables:
        d = dict(table=t, policy="skip", entity=None, reason=None)
        if t.startswith("sqlite_") or "_fts" in t:
            d["reason"] = "internal"
        elif is_derived_export:
            d["reason"] = "derived_export"
        elif t in LEDGER_TABLES:
            d.update(policy="entity", entity="http_requests")
        elif is_gitrx and t in GITRX_SPECS:
            d.update(policy="entity", entity=GITRX_SPECS[t][0])
        elif t in AUX_TABLES:
            d.update(policy="aux", entity="aux_rows")
        elif t in SKIP_TABLES:
            d["reason"] = "derived_or_operational"
        else:
            d.update(policy="unclassified", reason="table not classified")
        out.append(d)
    return out


def table_sql(conn, plan):
    """SQL (first column = rowid) and optional transform for a planned table."""
    t = plan["table"]
    if plan["entity"] == "http_requests":
        cols = {r[1] for r in conn.execute('PRAGMA table_info("%s")' % t)}
        exprs = []
        for out in ENTITIES["http_requests"]["cols"]:
            src = next((c for c in HTTP_OUT_FROM[out] if c in cols), None)
            exprs.append('"%s"' % src if src else "NULL")
        return 'SELECT rowid, %s FROM "%s" ORDER BY rowid' % (", ".join(exprs), t), None
    if plan["policy"] == "entity":
        xf = None
        if plan["entity"] == "backfill_parent_refs":
            xf = lambda v: [v[0], v[1], strip_parse_runs(v[2])]
        return GITRX_SPECS[t][1], xf
    return None, None  # aux handled separately


def iter_table(conn, plan):
    """Yield (rowid, values_list) for a planned entity/aux table."""
    t = plan["table"]
    if plan["policy"] == "aux":
        try:
            cur = conn.execute('SELECT rowid, * FROM "%s" ORDER BY rowid' % t)
        except sqlite3.OperationalError:
            cur = conn.execute('SELECT row_number() OVER (), * FROM "%s"' % t)
        names = [d[0] for d in cur.description][1:]
        for row in cur:
            obj = {n: _enc(v) for n, v in zip(names, row[1:])}
            yield row[0], [t, json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)]
        return
    sql, xf = table_sql(conn, plan)
    try:
        cur = conn.execute(sql)
    except sqlite3.OperationalError as e:
        if "rowid" not in str(e):
            raise
        cur = conn.execute(_norowid(sql))
    for row in cur:
        vals = list(row[1:])
        if xf:
            vals = xf(vals)
        yield row[0], vals


def _norowid(sql):
    # WITHOUT ROWID fallback: synthetic ordinal in place of the base rowid, no rowid ordering.
    _head, rest = sql.split(", ", 1)
    return ("SELECT row_number() OVER (), " + rest).rsplit(" ORDER BY ", 1)[0]


def count_rows(conn, table):
    return conn.execute('SELECT count(*) FROM "%s"' % table).fetchone()[0]


# --------------------------------------------------------------------------
# JSON documents
# --------------------------------------------------------------------------
def collect_docs(store_globs, register_globs, max_store_bytes):
    docs = {}
    for pat in store_globs:
        for p in sorted(glob.glob(pat, recursive=True)):
            if os.path.isfile(p):
                docs[os.path.realpath(p)] = (p, True)
    for pat in register_globs:
        for p in sorted(glob.glob(pat, recursive=True)):
            if os.path.isfile(p) and os.path.realpath(p) not in docs:
                docs[os.path.realpath(p)] = (p, False)
    out = []
    for rp in sorted(docs):
        p, store = docs[rp]
        size = os.path.getsize(rp)
        out.append(dict(path=p, resolved=rp, bytes=size, sha256=sha256_file(rp),
                        stored=bool(store and size <= max_store_bytes)))
    return out


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------
def _ent_ddl(name, cols, with_h):
    c = ", ".join('"%s"' % x for x in cols)
    return ("CREATE TABLE %s (%s%s)" % (name, "h BLOB, " if with_h else "id INTEGER PRIMARY KEY, ", c))


def _stage_create(st):
    for n in ENTITY_NAMES:
        st.execute(_ent_ddl("s_" + n, ENTITIES[n]["cols"], True))
        st.execute("CREATE UNIQUE INDEX s_%s_h ON s_%s(h)" % (n, n))
    st.execute("CREATE TABLE p(ent INTEGER, tid INTEGER, rid INTEGER, h BLOB)")


def read_hash_cache(path):
    cache = {}
    if path and os.path.isfile(path):
        for line in open(path):
            parts = line.rstrip("\n").split("\t")
            if len(parts) == 3:
                cache[(parts[0], int(parts[2]))] = parts[1]
    return cache


def build(sources, out_path, work_dir, docs=(), hash_cache=None, log=print):
    t0 = time.time()
    os.makedirs(work_dir, exist_ok=True)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    stage_path = os.path.join(work_dir, "stage.sqlite")
    for ext in ("", "-journal", "-wal", "-shm"):
        if os.path.exists(stage_path + ext):
            os.remove(stage_path + ext)
    st = sqlite3.connect(stage_path)
    st.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-1000000;")
    _stage_create(st)
    cache = read_hash_cache(hash_cache)

    # unique inputs by resolved path (symlink aliases collapse)
    resolved = {}
    for p in sources:
        resolved.setdefault(os.path.realpath(p), []).append(p)
    src_rows, tbl_rows = [], []
    tid = 0
    stats = {"entity_source_rows": {n: 0 for n in ENTITY_NAMES}}
    for sid, rp in enumerate(sorted(resolved), 1):
        aliases = sorted(set(resolved[rp]))
        size = os.path.getsize(rp) if os.path.exists(rp) else None
        sh = cache.get((rp, size)) or cache.get((aliases[0], size))
        if sh is None and size is not None:
            sh = sha256_file(rp)
        src = Source(rp, work_dir, sid)
        rec = dict(id=sid, path=rp, aliases=aliases, bytes=size, sha256=sh,
                   wal_path=src.wal_path, wal_bytes=src.wal_bytes, wal_sha256=src.wal_sha256,
                   status="ok", error=src.error, classification=None)
        if src.conn is None:
            rec["status"] = "unreadable"
            src_rows.append(rec)
            log("[%d] UNREADABLE %s: %s" % (sid, rp, src.error))
            continue
        try:
            tables = src.tables()
            plans = plan_source(src.conn, tables)
            kinds = {p["reason"] for p in plans if p["reason"] == "derived_export"}
            rec["classification"] = "derived_export" if kinds else (
                "evidence_db" if {"retrievals", "versions"} <= set(tables) else "other")
            for pl in plans:
                tid += 1
                trow = dict(id=tid, source_id=sid, table_name=pl["table"], policy=pl["policy"],
                            entity=pl["entity"], reason=pl["reason"], source_rows=None,
                            imported_rows=0, error=None)
                tbl_rows.append(trow)
                if pl["policy"] not in ("entity", "aux"):
                    continue
                ent = pl["entity"]
                code = ENTITY_CODE[ent]
                try:
                    trow["source_rows"] = count_rows(src.conn, pl["table"])
                    buf_e, buf_p = [], []
                    n = 0
                    ins = "INSERT OR IGNORE INTO s_%s (h, %s) VALUES (?, %s)" % (
                        ent, ", ".join('"%s"' % c for c in ENTITIES[ent]["cols"]),
                        ", ".join("?" * len(ENTITIES[ent]["cols"])))
                    for rid, vals in iter_table(src.conn, pl):
                        h = row_hash(ent, vals)
                        buf_e.append((h, *vals))
                        buf_p.append((code, tid, rid, h))
                        n += 1
                        if len(buf_e) >= BATCH:
                            st.executemany(ins, buf_e)
                            st.executemany("INSERT INTO p VALUES (?,?,?,?)", buf_p)
                            buf_e, buf_p = [], []
                    if buf_e:
                        st.executemany(ins, buf_e)
                        st.executemany("INSERT INTO p VALUES (?,?,?,?)", buf_p)
                    trow["imported_rows"] = n
                    stats["entity_source_rows"][ent] += n
                except Exception as e:
                    trow["error"] = "%s: %s" % (type(e).__name__, e)
                    log("  table error %s %s: %s" % (rp, pl["table"], trow["error"]))
            st.commit()
        except Exception as e:
            rec["status"] = "error"
            rec["error"] = "%s: %s" % (type(e).__name__, e)
        finally:
            src.close()
        src_rows.append(rec)
        if sid % 25 == 0:
            log("[%d/%d] %.0fs %s" % (sid, len(resolved), time.time() - t0, rp[-70:]))
    st.commit()
    log("staging done %.0fs; assembling final" % (time.time() - t0))

    # ---- final DB (built under a temp name, renamed at the end) ----
    tmp_out = out_path + ".building"
    for ext in ("", "-journal", "-wal", "-shm"):
        if os.path.exists(tmp_out + ext):
            os.remove(tmp_out + ext)
    db = sqlite3.connect(tmp_out)
    db.executescript("PRAGMA page_size=4096; PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;"
                     "PRAGMA auto_vacuum=0; PRAGMA cache_size=-1000000;")
    db.execute("CREATE TABLE evidence_meta (key TEXT PRIMARY KEY, value TEXT) WITHOUT ROWID")
    db.execute("""CREATE TABLE source_dbs (id INTEGER PRIMARY KEY, path TEXT, aliases_json TEXT,
        bytes INTEGER, sha256 TEXT, wal_path TEXT, wal_bytes INTEGER, wal_sha256 TEXT,
        status TEXT, error TEXT, classification TEXT)""")
    db.execute("""CREATE TABLE source_tables (id INTEGER PRIMARY KEY, source_id INTEGER,
        table_name TEXT, policy TEXT, entity TEXT, reason TEXT, source_rows INTEGER,
        imported_rows INTEGER, error TEXT)""")
    db.execute("CREATE TABLE entities (code INTEGER PRIMARY KEY, name TEXT, columns_json TEXT, rows INTEGER)")
    for n in ENTITY_NAMES:
        db.execute(_ent_ddl(n, ENTITIES[n]["cols"], False))
    db.execute("""CREATE TABLE row_provenance (table_id INTEGER, src_rowid INTEGER, entity INTEGER,
        row_id INTEGER, PRIMARY KEY (table_id, src_rowid)) WITHOUT ROWID""")
    db.execute("""CREATE TABLE json_documents (id INTEGER PRIMARY KEY, path TEXT, resolved_path TEXT,
        bytes INTEGER, sha256 TEXT, stored INTEGER, content BLOB)""")
    db.execute("PRAGMA foreign_keys=OFF")
    db.execute("ATTACH ? AS st", (stage_path,))
    db.execute("CREATE TABLE st.pp (tid INTEGER, rid INTEGER, ent INTEGER, id INTEGER)")
    counts = {}
    for n in ENTITY_NAMES:
        e = ENTITIES[n]
        order = ", ".join('"%s"' % c for c in e["order"]) + ", h"
        db.execute("CREATE TABLE st.m_%s AS SELECT h, row_number() OVER (ORDER BY %s) AS id FROM st.s_%s"
                   % (n, order, n))
        db.execute("CREATE UNIQUE INDEX st.m_%s_h ON m_%s(h)" % (n, n))
        cols = ", ".join('s."%s"' % c for c in e["cols"])
        db.execute("INSERT INTO main.%s (id, %s) SELECT m.id, %s FROM st.s_%s s JOIN st.m_%s m ON m.h=s.h "
                   "ORDER BY m.id" % (n, ", ".join('"%s"' % c for c in e["cols"]), cols, n, n))
        counts[n] = db.execute("SELECT count(*) FROM main.%s" % n).fetchone()[0]
        db.execute("INSERT INTO st.pp SELECT p.tid, p.rid, p.ent, m.id FROM st.p p JOIN st.m_%s m ON m.h=p.h "
                   "WHERE p.ent=%d" % (n, ENTITY_CODE[n]))
        db.execute("INSERT INTO main.entities VALUES (?,?,?,?)",
                   (ENTITY_CODE[n], n, json.dumps(e["cols"]), counts[n]))
    db.execute("INSERT INTO main.row_provenance SELECT tid, rid, ent, id FROM st.pp ORDER BY tid, rid")
    db.execute("CREATE INDEX prov_entity_row ON row_provenance(entity, row_id)")
    for r in src_rows:
        db.execute("INSERT INTO source_dbs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (r["id"], r["path"], json.dumps(r["aliases"]), r["bytes"], r["sha256"],
                    r["wal_path"], r["wal_bytes"], r["wal_sha256"], r["status"], r["error"],
                    r["classification"]))
    for r in tbl_rows:
        db.execute("INSERT INTO source_tables VALUES (?,?,?,?,?,?,?,?,?)",
                   (r["id"], r["source_id"], r["table_name"], r["policy"], r["entity"],
                    r["reason"], r["source_rows"], r["imported_rows"], r["error"]))
    for i, d in enumerate(docs, 1):
        content = open(d["resolved"], "rb").read() if d["stored"] else None
        db.execute("INSERT INTO json_documents VALUES (?,?,?,?,?,?,?)",
                   (i, d["path"], d["resolved"], d["bytes"], d["sha256"], int(d["stored"]), content))
    db.execute("INSERT INTO evidence_meta VALUES ('format', ?)", (FORMAT,))
    db.execute("INSERT INTO evidence_meta VALUES ('entities', ?)", (json.dumps(ENTITY_NAMES),))
    db.commit()
    db.execute("DETACH st")
    db.close()
    st.close()
    if os.path.exists(out_path):
        os.remove(out_path)
    os.replace(tmp_out, out_path)
    log("build done %.0fs" % (time.time() - t0))
    return dict(seconds=round(time.time() - t0, 1), entity_rows=counts, sources=len(src_rows),
                source_tables=len(tbl_rows), docs=len(docs))


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------
def _rehash_entity(db, name):
    """List index id -> hash computed from the stored final row; also entity-wide key digest."""
    cols = ENTITIES[name]["cols"]
    q = "SELECT id, %s FROM %s ORDER BY id" % (", ".join('"%s"' % c for c in cols), name)
    hashes = {}
    for row in db.execute(q):
        hashes[row[0]] = row_hash(name, row[1:])
    return hashes


def verify(db_path, work_dir, log=print, source_filter=None):
    """Independent second pass over the inputs and the evidence DB. Returns report dict."""
    t0 = time.time()
    db = sqlite3.connect("file:%s?mode=ro" % _uri_quote(db_path), uri=True)
    db.text_factory = lambda b: b.decode("utf-8", "surrogatepass")
    rep = dict(format=FORMAT, tables=[], entities={}, problems=[], documents={})
    rehash = {n: _rehash_entity(db, n) for n in ENTITY_NAMES}
    for n in ENTITY_NAMES:
        hs = rehash[n]
        d = hashlib.sha256()
        for h in sorted(hs.values()):
            d.update(h)
        distinct = len(set(hs.values()))
        nprov = db.execute("SELECT count(DISTINCT row_id) FROM row_provenance WHERE entity=?",
                           (ENTITY_CODE[n],)).fetchone()[0]
        e = dict(rows=len(hs), distinct_hashes=distinct, rows_with_provenance=nprov,
                 key_digest_sha256=d.hexdigest())
        rep["entities"][n] = e
        if distinct != len(hs):
            rep["problems"].append("entity %s has duplicate rows" % n)
        if nprov != len(hs):
            rep["problems"].append("entity %s: %d rows lack provenance" % (n, len(hs) - nprov))
    sources = db.execute("SELECT id, path, status FROM source_dbs ORDER BY id").fetchall()
    ttabs = {}
    for r in db.execute("SELECT id, source_id, table_name, policy, entity, source_rows, imported_rows, error "
                        "FROM source_tables ORDER BY id"):
        ttabs.setdefault(r[1], []).append(r)
    for sid, path, status in sources:
        if source_filter and not source_filter(path):
            continue
        if status != "ok":
            rep["tables"].append(dict(source_id=sid, path=path, status=status, ok=None))
            continue
        src = Source(path, work_dir, 100000 + sid)
        try:
            if src.conn is None:
                rep["problems"].append("verify: source %s unreadable now: %s" % (path, src.error))
                continue
            tabs = src.tables()
            plans = {p["table"]: p for p in plan_source(src.conn, tabs)}
            for (tid, _s, tname, policy, entity, srows, irows, terr) in ttabs.get(sid, []):
                if policy not in ("entity", "aux"):
                    continue
                rec = dict(source_id=sid, path=path, table=tname, entity=entity,
                           source_rows_recorded=srows, imported_rows=irows)
                try:
                    cnt = count_rows(src.conn, tname)
                    d_src = hashlib.sha256()
                    n_iter = 0
                    for rid, vals in iter_table(src.conn, plans[tname]):
                        d_src.update(b"%d:" % rid + row_hash(entity, vals) + b"\n")
                        n_iter += 1
                    d_ev = hashlib.sha256()
                    n_prov = 0
                    hs = rehash[entity]
                    for rid, row_id in db.execute(
                            "SELECT src_rowid, row_id FROM row_provenance WHERE table_id=? ORDER BY src_rowid",
                            (tid,)):
                        d_ev.update(b"%d:" % rid + hs[row_id] + b"\n")
                        n_prov += 1
                    rec.update(source_count_now=cnt, rows_read=n_iter, provenance_rows=n_prov,
                               source_key_digest=d_src.hexdigest(), evidence_key_digest=d_ev.hexdigest())
                    rec["ok"] = (cnt == srows == irows == n_iter == n_prov
                                 and d_src.hexdigest() == d_ev.hexdigest() and not terr)
                    if not rec["ok"]:
                        rep["problems"].append("table mismatch %s %s" % (path, tname))
                except Exception as e:
                    rec.update(ok=False, error="%s: %s" % (type(e).__name__, e))
                    rep["problems"].append("verify error %s %s: %s" % (path, tname, e))
                rep["tables"].append(rec)
        finally:
            src.close()
        if sid % 25 == 0:
            log("verify [%d/%d] %.0fs" % (sid, len(sources), time.time() - t0))
    # json documents
    docs = db.execute("SELECT path, resolved_path, bytes, sha256, stored, content FROM json_documents").fetchall()
    bad = 0
    for p, rp, b, sh, stored, content in docs:
        ok = os.path.isfile(rp) and sha256_file(rp) == sh and os.path.getsize(rp) == b
        if stored:
            ok = ok and content is not None and hashlib.sha256(content).hexdigest() == sh
        if not ok:
            bad += 1
            rep["problems"].append("document mismatch %s" % p)
    rep["documents"] = dict(count=len(docs), stored=sum(1 for d in docs if d[4]), mismatches=bad)
    rep["tables_checked"] = sum(1 for t in rep["tables"] if t.get("ok") is not None)
    rep["tables_ok"] = sum(1 for t in rep["tables"] if t.get("ok") is True)
    rep["verify_seconds"] = round(time.time() - t0, 1)
    rep["ok"] = not rep["problems"]
    return rep


# --------------------------------------------------------------------------
# cross-checks against the real corpus (read from the evidence DB itself)
# --------------------------------------------------------------------------
def real_cross_checks(db_path, raw_manifest=None):
    db = sqlite3.connect("file:%s?mode=ro" % _uri_quote(db_path), uri=True)
    out = {}
    q = lambda sql, *a: db.execute(sql, a).fetchall()
    batch_tids = "(SELECT st.id FROM source_tables st JOIN source_dbs s ON s.id=st.source_id " \
                 "WHERE s.path LIKE '%/backfill-20261107/batches/%/data/gitrx.sqlite' AND st.table_name=?)"
    # backfill version targets
    db.execute("CREATE TEMP TABLE bv AS SELECT DISTINCT v.setid, v.version, v.state, v.selected_sha256, v.id AS vid "
               "FROM versions v WHERE v.id IN (SELECT row_id FROM row_provenance WHERE entity=%d AND table_id IN %s)"
               % (ENTITY_CODE["versions"], batch_tids), ("versions",))
    out["backfill_version_rows_distinct_content"] = q("SELECT count(*) FROM bv")[0][0]
    out["backfill_targets_distinct_setid_version"] = q("SELECT count(*) FROM (SELECT DISTINCT setid, version FROM bv)")[0][0]
    out["backfill_state_counts_by_pair"] = dict((str(k), v) for k, v in q(
        "SELECT state, count(*) FROM (SELECT setid, version, max(state) AS state FROM bv GROUP BY 1,2) GROUP BY 1"))
    out["backfill_pairs_with_multiple_states"] = q(
        "SELECT count(*) FROM (SELECT setid, version FROM bv GROUP BY 1,2 HAVING count(DISTINCT state)>1)")[0][0]
    manifest = None
    if raw_manifest and os.path.exists(raw_manifest):
        m = sqlite3.connect("file:%s?mode=ro" % _uri_quote(raw_manifest), uri=True)
        manifest = {r[0] for r in m.execute("SELECT sha256 FROM objects WHERE kind='zip_spl'")}
        out["raw_manifest_zip_spl_objects"] = len(manifest)
    # downloaded pairs: artifact sha present
    dl = q("SELECT DISTINCT setid, version, selected_sha256 FROM bv WHERE state='downloaded' AND selected_sha256 IS NOT NULL")
    out["downloaded_pairs_with_selected_sha"] = len({(a, b) for a, b, _ in dl})
    if manifest is not None:
        out["downloaded_pairs_sha_missing_in_manifest"] = len({(a, b) for a, b, c in dl if c not in manifest})
    # retained-parent raw sha
    rp = q("SELECT DISTINCT b.setid, b.version, b.reference_json_without_parse_runs FROM backfill_parent_refs b")
    rps = {}
    for s, v, rj in rp:
        try:
            raw_path = json.loads(rj).get("raw_path")
        except Exception:
            raw_path = None
        rps[(s, v)] = os.path.basename(raw_path) if raw_path else None
    out["retained_parent_pairs"] = len(rps)
    if manifest is not None:
        out["retained_parent_missing_in_manifest"] = sum(1 for sha in rps.values() if sha not in manifest)
    # unavailable: pairs with no selected sha
    un = q("SELECT DISTINCT setid, version FROM bv WHERE state NOT IN ('downloaded','retained-parent')")
    out["non_downloaded_non_parent_pairs"] = len(un)
    reasons = {}
    for s, v in un:
        rows = q("SELECT kind, status, state, error FROM retrievals WHERE setid=? AND version=? AND kind IN ('getFile_zip','archive_route') ",
                 s, v)
        key = tuple(sorted({(k, st, stt) for k, st, stt, _e in rows}))
        reasons[str(key)] = reasons.get(str(key), 0) + 1
    out["non_downloaded_reasons"] = reasons
    # release1 control targets (aux_rows from cohorts/release1/control.sqlite)
    tids = "(SELECT st.id FROM source_tables st JOIN source_dbs s ON s.id=st.source_id " \
           "WHERE s.path LIKE '%/cohorts/release1/control.sqlite' AND st.table_name='targets')"
    rows = q("SELECT a.row_json FROM aux_rows a WHERE a.id IN (SELECT row_id FROM row_provenance WHERE entity=%d AND table_id IN %s)"
             % (ENTITY_CODE["aux_rows"], tids))
    st_counts, routes, nosha = {}, {}, 0
    for (rj,) in rows:
        j = json.loads(rj)
        st_counts[str(j["state"])] = st_counts.get(str(j["state"]), 0) + 1
        routes[str(j["route"])] = routes.get(str(j["route"]), 0) + 1
        if not j.get("raw_sha256"):
            nosha += 1
    out["release1_targets"] = dict(total=len(rows), by_state=st_counts, by_route=routes, without_raw_sha=nosha)
    # live ledger
    lt = q("SELECT st.id, st.source_rows, st.imported_rows FROM source_tables st JOIN source_dbs s ON s.id=st.source_id "
           "WHERE s.path LIKE '%/hot/objects/cb640aec%' AND st.table_name='requests'")
    out["live_ledger_requests"] = [dict(table_id=a, source_rows=b, imported_rows=c) for a, b, c in lt]
    if lt:
        out["live_ledger_provenance_rows"] = q("SELECT count(*) FROM row_provenance WHERE table_id=?", lt[0][0])[0][0]
        out["live_ledger_distinct_entity_rows"] = q("SELECT count(DISTINCT row_id) FROM row_provenance WHERE table_id=?", lt[0][0])[0][0]
        w = q("SELECT wal_bytes FROM source_dbs s JOIN source_tables st ON st.source_id=s.id WHERE st.id=?", lt[0][0])
        out["live_ledger_wal_bytes_read"] = w[0][0] if w else None
    out["bulk_member_sources_rows"] = q("SELECT count(*) FROM bulk_member_sources")[0][0]
    out["bulk_member_sources_distinct_retrieval_keys"] = q(
        "SELECT count(DISTINCT retrieval_key) FROM bulk_member_sources")[0][0]
    # retrieval natural-key conflicts
    out["retrieval_keys_with_multiple_contents"] = q(
        "SELECT count(*) FROM (SELECT observation_key FROM retrievals GROUP BY 1 HAVING count(*)>1)")[0][0]
    out["retrieval_dup_by_kind"] = dict((str(k), v) for k, v in q(
        "SELECT kind, count(*) FROM retrievals WHERE observation_key IN "
        "(SELECT observation_key FROM retrievals GROUP BY 1 HAVING count(*)>1) GROUP BY 1"))
    return out


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def _read_list(path):
    return [l for l in open(path).read().split("\n") if l.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="gitrx_core.evidence")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--sources-list", required=True)
    b.add_argument("--out", required=True)
    b.add_argument("--work-dir", required=True)
    b.add_argument("--hash-cache")
    b.add_argument("--doc-store-glob", action="append", default=[])
    b.add_argument("--doc-register-glob", action="append", default=[])
    b.add_argument("--max-store-bytes", type=int, default=20 * 1024 * 1024)
    v = sub.add_parser("verify")
    v.add_argument("--db", required=True)
    v.add_argument("--work-dir", required=True)
    v.add_argument("--raw-manifest")
    v.add_argument("--report", required=True)
    v.add_argument("--real-cross-checks", action="store_true")
    v.add_argument("--build-info")
    a = ap.parse_args(argv)
    if a.cmd == "build":
        docs = collect_docs(a.doc_store_glob, a.doc_register_glob, a.max_store_bytes)
        info = build(_read_list(a.sources_list), a.out, a.work_dir, docs, a.hash_cache,
                     log=lambda m: print(m, flush=True))
        print(json.dumps(info))
        return 0
    rep = verify(a.db, a.work_dir, log=lambda m: print(m, flush=True))
    if a.real_cross_checks:
        rep["cross_checks"] = real_cross_checks(a.db, a.raw_manifest)
    if a.build_info and os.path.exists(a.build_info):
        rep["build_info"] = json.load(open(a.build_info))
    rep["db_path"] = a.db
    rep["db_bytes"] = os.path.getsize(a.db)
    with open(a.report, "w") as f:
        json.dump(rep, f, indent=1, sort_keys=True)
    print("verify ok=%s problems=%d" % (rep["ok"], len(rep["problems"])))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
