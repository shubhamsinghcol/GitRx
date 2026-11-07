import json
from pathlib import Path
import sqlite3

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS document_type_allowlist (
 code TEXT PRIMARY KEY, name TEXT NOT NULL, source_url TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS section_code_registry (
 code TEXT PRIMARY KEY, name TEXT NOT NULL, source_url TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS families (
 setid TEXT PRIMARY KEY, name TEXT NOT NULL, metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS family_label_status (
 setid TEXT PRIMARY KEY REFERENCES families(setid),
 label_status TEXT NOT NULL DEFAULT 'unknown' CHECK(label_status IN ('unknown','active','archived')),
 evidence_json TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS ingest_runs (
 id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, completed_at TEXT,
 histories_complete INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS versions (
 id INTEGER PRIMARY KEY, setid TEXT NOT NULL REFERENCES families(setid),
 version INTEGER NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
 selected_sha256 TEXT, xml_member TEXT, effective_date TEXT,
 date_anomaly INTEGER NOT NULL DEFAULT 0,
 diagnostics_json TEXT NOT NULL DEFAULT '[]', UNIQUE(setid,version)
);
CREATE TABLE IF NOT EXISTS artifacts (
 sha256 TEXT PRIMARY KEY, bytes INTEGER NOT NULL, path TEXT NOT NULL,
 artifact_class TEXT NOT NULL DEFAULT 'pipeline-provenance'
 CHECK(artifact_class IN ('retrieved-evidence','pipeline-provenance','derived'))
);
CREATE TABLE IF NOT EXISTS retrievals (
 id INTEGER PRIMARY KEY, observation_key TEXT UNIQUE NOT NULL,
 run_id INTEGER REFERENCES ingest_runs(id), version_id INTEGER REFERENCES versions(id),
 kind TEXT NOT NULL, url TEXT NOT NULL, final_url TEXT, retrieved_at TEXT NOT NULL,
 status INTEGER, headers_json TEXT NOT NULL, state TEXT NOT NULL,
 artifact_sha256 TEXT REFERENCES artifacts(sha256), error TEXT,
 attempt INTEGER NOT NULL, next_retry_at TEXT, validation_json TEXT
);
CREATE INDEX IF NOT EXISTS retrieval_version ON retrievals(version_id,id);
CREATE TABLE IF NOT EXISTS archive_route_sources (
 retrieval_id INTEGER PRIMARY KEY REFERENCES retrievals(id),
 archive_id TEXT NOT NULL, listing_retrieval_id INTEGER NOT NULL REFERENCES retrievals(id),
 listed_setid TEXT NOT NULL, listed_version INTEGER NOT NULL,
 provenance_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS history_entries (
 run_id INTEGER NOT NULL REFERENCES ingest_runs(id),
 version_id INTEGER NOT NULL REFERENCES versions(id),
 retrieval_id INTEGER NOT NULL REFERENCES retrievals(id),
 published_raw TEXT NOT NULL, published_date TEXT,
 PRIMARY KEY(run_id,version_id,retrieval_id)
);
CREATE TABLE IF NOT EXISTS history_pages (
 run_id INTEGER NOT NULL REFERENCES ingest_runs(id),
 setid TEXT NOT NULL REFERENCES families(setid), page INTEGER NOT NULL,
 retrieval_id INTEGER NOT NULL REFERENCES retrievals(id), total_elements INTEGER NOT NULL,
 total_pages INTEGER NOT NULL, PRIMARY KEY(run_id,setid,page)
);
CREATE TABLE IF NOT EXISTS date_anomalies (
 run_id INTEGER NOT NULL REFERENCES ingest_runs(id),
 lower_version_id INTEGER NOT NULL REFERENCES versions(id),
 higher_version_id INTEGER NOT NULL REFERENCES versions(id),
 lower_published TEXT NOT NULL, higher_published TEXT NOT NULL,
 PRIMARY KEY(run_id,lower_version_id,higher_version_id)
);
CREATE TABLE IF NOT EXISTS document_type_observations (
 retrieval_id INTEGER NOT NULL REFERENCES retrievals(id),
 xml_member TEXT NOT NULL, code TEXT NOT NULL, code_system TEXT,
 display_name TEXT, allowed INTEGER NOT NULL, xml_setid TEXT, xml_version TEXT,
 PRIMARY KEY(retrieval_id,xml_member)
);
CREATE TABLE IF NOT EXISTS identity_conflicts (
 retrieval_id INTEGER NOT NULL REFERENCES retrievals(id),
 xml_member TEXT NOT NULL, requested_setid TEXT, requested_version INTEGER,
 observed_setid TEXT, observed_version TEXT, reason TEXT NOT NULL,
 PRIMARY KEY(retrieval_id,xml_member)
);
CREATE TABLE IF NOT EXISTS section_code_observations (
 retrieval_id INTEGER NOT NULL REFERENCES retrievals(id), xml_member TEXT NOT NULL,
 ordinal INTEGER NOT NULL, code TEXT NOT NULL, code_system TEXT, expected INTEGER NOT NULL,
 PRIMARY KEY(retrieval_id,xml_member,ordinal)
);
CREATE TABLE IF NOT EXISTS parse_runs (
 id INTEGER PRIMARY KEY, version_id INTEGER NOT NULL REFERENCES versions(id),
 artifact_sha256 TEXT NOT NULL REFERENCES artifacts(sha256),
 parser_version TEXT NOT NULL, config_json TEXT NOT NULL, created_at TEXT NOT NULL,
 status TEXT NOT NULL, diagnostics_json TEXT NOT NULL,
 section_count INTEGER NOT NULL DEFAULT 0,
 UNIQUE(version_id,artifact_sha256,parser_version,config_json)
);
CREATE TABLE IF NOT EXISTS sections (
 id INTEGER PRIMARY KEY, parse_run_id INTEGER NOT NULL REFERENCES parse_runs(id),
 version_id INTEGER NOT NULL REFERENCES versions(id), parser_version TEXT NOT NULL,
 loinc_code TEXT NOT NULL, code_system TEXT, ordinal INTEGER NOT NULL,
 ancestry_path TEXT NOT NULL, parent_ordinal INTEGER, source_xpath TEXT NOT NULL,
 source_line INTEGER, title TEXT, kind TEXT NOT NULL, status TEXT NOT NULL,
 source_xml TEXT NOT NULL, narrative_xml TEXT, own_text TEXT NOT NULL,
 diagnostics_json TEXT NOT NULL,
 UNIQUE(parse_run_id,version_id,loinc_code,ordinal,ancestry_path)
);
CREATE INDEX IF NOT EXISTS section_lookup ON sections(version_id,loinc_code,parse_run_id);
CREATE TABLE IF NOT EXISTS source_quirks (
 id INTEGER PRIMARY KEY,
 setid TEXT NOT NULL, version INTEGER NOT NULL,
 section_instance INTEGER NOT NULL REFERENCES sections(id),
 quirk_type TEXT NOT NULL CHECK(length(trim(quirk_type))>0),
 evidence TEXT NOT NULL CHECK(length(trim(evidence))>0),
 confirmed_by TEXT NOT NULL CHECK(length(trim(confirmed_by))>0),
 confirmed_date TEXT NOT NULL CHECK(length(trim(confirmed_date))>0),
 FOREIGN KEY(setid,version) REFERENCES versions(setid,version)
);
CREATE TRIGGER IF NOT EXISTS quirk_identity_insert BEFORE INSERT ON source_quirks
WHEN NOT EXISTS (SELECT 1 FROM sections s JOIN versions v ON v.id=s.version_id
 WHERE s.id=new.section_instance AND v.setid=new.setid AND v.version=new.version)
BEGIN SELECT RAISE(ABORT,'quirk section identity mismatch'); END;
CREATE TRIGGER IF NOT EXISTS quirk_identity_update BEFORE UPDATE ON source_quirks
WHEN NOT EXISTS (SELECT 1 FROM sections s JOIN versions v ON v.id=s.version_id
 WHERE s.id=new.section_instance AND v.setid=new.setid AND v.version=new.version)
BEGIN SELECT RAISE(ABORT,'quirk section identity mismatch'); END;
CREATE TABLE IF NOT EXISTS narrative_blocks (
 id INTEGER PRIMARY KEY, section_id INTEGER NOT NULL REFERENCES sections(id),
 ordinal INTEGER NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
 normalized_text TEXT NOT NULL, source_xpath TEXT NOT NULL,
 table_ordinal INTEGER, row_ordinal INTEGER, cell_ordinal INTEGER,
 UNIQUE(section_id,ordinal)
);
CREATE VIRTUAL TABLE IF NOT EXISTS blocks_fts USING fts5(text,content='narrative_blocks',content_rowid='id');
CREATE TRIGGER IF NOT EXISTS blocks_insert AFTER INSERT ON narrative_blocks BEGIN
 INSERT INTO blocks_fts(rowid,text) VALUES(new.id,new.normalized_text);
END;
CREATE TRIGGER IF NOT EXISTS blocks_delete AFTER DELETE ON narrative_blocks BEGIN
 INSERT INTO blocks_fts(blocks_fts,rowid,text) VALUES('delete',old.id,old.normalized_text);
END;
CREATE TABLE IF NOT EXISTS narrative_tables (
 id INTEGER PRIMARY KEY, section_id INTEGER NOT NULL REFERENCES sections(id),
 ordinal INTEGER NOT NULL, source_xpath TEXT NOT NULL,
 source_xml TEXT NOT NULL, structured_json TEXT NOT NULL, flattened_text TEXT NOT NULL,
 diagnostics_json TEXT NOT NULL, UNIQUE(section_id,ordinal)
);
CREATE TABLE IF NOT EXISTS section_excerpts (
 id INTEGER PRIMARY KEY, section_id INTEGER NOT NULL REFERENCES sections(id),
 ordinal INTEGER NOT NULL, source_xpath TEXT NOT NULL, source_xml TEXT NOT NULL,
 extracted_text TEXT NOT NULL, status TEXT NOT NULL, diagnostics_json TEXT NOT NULL,
 UNIQUE(section_id,ordinal)
);
CREATE TABLE IF NOT EXISTS snapshots (
 id TEXT PRIMARY KEY, created_at TEXT NOT NULL, manifest_json TEXT NOT NULL
);
CREATE VIEW IF NOT EXISTS latest_publications AS
 SELECT DISTINCT h.run_id,v.id AS version_id,v.setid,v.version,h.published_raw,
 h.published_date,v.state,v.date_anomaly,v.selected_sha256,v.effective_date
 FROM versions v JOIN history_entries h ON h.version_id=v.id
 WHERE h.run_id=(SELECT MAX(id) FROM ingest_runs WHERE histories_complete=1);
"""


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    probe=sqlite3.connect(path,timeout=60)
    migrated=probe.execute("SELECT 1 FROM sqlite_master WHERE name='content_store_meta'").fetchone()
    probe.close()
    if migrated:
        from gitrx.db import connect as current_connect
        return current_connect(path)
    conn = sqlite3.connect(path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    if 'artifact_class' not in {r[1] for r in conn.execute('PRAGMA table_info(artifacts)')}:
        conn.execute("""ALTER TABLE artifacts ADD COLUMN artifact_class TEXT NOT NULL
          DEFAULT 'pipeline-provenance' CHECK(artifact_class IN
          ('retrieved-evidence','pipeline-provenance','derived'))""")
    conn.executescript(ARTIFACT_SCHEMA)
    # Add representation scope without rewriting earlier raw observations.
    for table in ("narrative_blocks", "narrative_tables"):
        columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "scope" not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN scope TEXT NOT NULL DEFAULT 'full'")
    source = "https://www.fda.gov/industry/structured-product-labeling-resources/document-type-including-content-labeling-type"
    conn.executemany("INSERT OR IGNORE INTO document_type_allowlist VALUES(?,?,?)", [
        ("34391-3", "HUMAN PRESCRIPTION DRUG LABEL", source),
        ("45129-4", "Human prescription drug label with highlights", "https://loinc.org/45129-4/"),
    ])
    conn.commit()
    return conn


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def as_of(conn, setid, date):
    """All published-by-date versions, never a guessed single current version."""
    rows = [dict(row) for row in conn.execute(
        "SELECT * FROM latest_publications WHERE setid=? AND published_date<=? ORDER BY version",
        (setid.lower(), date))]
    for row in rows:
        row["warnings"] = ["date anomaly"] if row["date_anomaly"] else []
    return rows


ARTIFACT_SCHEMA = """
CREATE TABLE IF NOT EXISTS derived_artifacts (
 sha256 TEXT PRIMARY KEY REFERENCES artifacts(sha256), purpose TEXT NOT NULL,
 payload BLOB NOT NULL, provenance_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS history_captures (
 id TEXT PRIMARY KEY, run_id INTEGER NOT NULL REFERENCES ingest_runs(id),
 listed_versions INTEGER NOT NULL, captured_from TEXT NOT NULL,
 captured_through TEXT NOT NULL, manifest_json TEXT NOT NULL
);
CREATE VIEW IF NOT EXISTS retrieved_evidence AS
 SELECT sha256,bytes,path FROM artifacts WHERE artifact_class='retrieved-evidence';
CREATE VIEW IF NOT EXISTS evidence_label_versions AS
 SELECT v.* FROM versions v JOIN retrieved_evidence e ON e.sha256=v.selected_sha256
 WHERE v.state='downloaded';
CREATE VIEW IF NOT EXISTS evidence_history_entries AS
 SELECT h.* FROM history_entries h JOIN retrievals r ON r.id=h.retrieval_id
 JOIN retrieved_evidence e ON e.sha256=r.artifact_sha256;
CREATE TRIGGER IF NOT EXISTS artifact_evidence_references BEFORE UPDATE OF artifact_class ON artifacts
WHEN new.artifact_class!='retrieved-evidence' AND (
 EXISTS(SELECT 1 FROM retrievals WHERE artifact_sha256=old.sha256) OR
 EXISTS(SELECT 1 FROM versions WHERE selected_sha256=old.sha256) OR
 EXISTS(SELECT 1 FROM parse_runs WHERE artifact_sha256=old.sha256))
BEGIN SELECT RAISE(ABORT,'referenced evidence cannot be reclassified'); END;
CREATE TRIGGER IF NOT EXISTS derived_class_insert BEFORE INSERT ON derived_artifacts
WHEN NOT EXISTS(SELECT 1 FROM artifacts WHERE sha256=new.sha256 AND artifact_class='derived')
BEGIN SELECT RAISE(ABORT,'derived payload requires derived registry class'); END;
"""

# Both inserts and updates are guarded: old replay engines read the same selected
# identities, so these invariants also protect their citations without changing
# frozen output or opening any validation material.
for _table, _column in [('retrievals','artifact_sha256'), ('versions','selected_sha256'),
                        ('parse_runs','artifact_sha256')]:
    for _event in ('INSERT','UPDATE'):
        ARTIFACT_SCHEMA += f"""
CREATE TRIGGER IF NOT EXISTS {_table}_evidence_{_event.lower()} BEFORE {_event} ON {_table}
WHEN new.{_column} IS NOT NULL AND NOT EXISTS
 (SELECT 1 FROM retrieved_evidence WHERE sha256=new.{_column})
BEGIN SELECT RAISE(ABORT,'retrieved-evidence required'); END;
"""
