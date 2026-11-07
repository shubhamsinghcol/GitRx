"""Typed persistence adapter around the byte-preserved SPL extraction engine.

CONFIG.parser_source_sha256 identifies spl_parser_v1.py (the unchanged
extraction/segmentation engine). This adapter only registers artifact roles
and enforces the raw-evidence boundary. It does not reparse the real corpus.
"""
from .spl_parser_v1 import *
from . import spl_parser_v1 as _engine
from .archive import evidence_bytes


def load_registry(conn, root):
    registry = json.loads((Path(root) / "research/section-code-registry.json").read_text())
    source_body = (Path(root) / "research/evidence/fda-section-codes.html").read_bytes()
    if hashlib.sha256(source_body).hexdigest() != registry["source_sha256"]:
        raise RuntimeError("FDA section registry no longer matches retained source")
    put_bytes(conn, root, source_body, artifact_class='retrieved-evidence')
    for filename in ("spl_parser_v1.py", "parser.py", "normalize.py", "archive.py", "db.py"):
        put_bytes(conn, root, Path(__file__).with_name(filename).read_bytes(), artifact_class='pipeline-provenance')
    conn.executemany("INSERT OR IGNORE INTO section_code_registry VALUES(?,?,?)",
                     [(r["code"], r["name"], registry["source_url"]) for r in registry["codes"]])
    conn.commit()
    return {r[0] for r in conn.execute("SELECT code FROM section_code_registry")}



def persist_document(conn, root, version, registry):
    evidence_bytes(conn,root,version['selected_sha256'])
    return _engine.persist_document(conn,root,version,registry)


def main():
    args = argparse.ArgumentParser(description=__doc__)
    args.add_argument("--root", type=Path, default=Path.cwd())
    opts = args.parse_args()
    root = opts.root.resolve()
    conn = connect(root / "data/gitrx.sqlite")
    if conn.execute("SELECT COUNT(*) FROM ingest_runs WHERE completed_at IS NOT NULL").fetchone()[0] == 0:
        raise RuntimeError("Complete Step B before starting Step C")
    registry = load_registry(conn, root)
    versions = conn.execute("SELECT * FROM versions WHERE state='downloaded' ORDER BY setid,version").fetchall()
    for i, version in enumerate(versions, 1):
        persist_document(conn, root, version, registry)
        if i % 10 == 0 or i == len(versions):
            print(dumps({"stage": "parse", "processed": i, "total": len(versions)}), flush=True)
    print(dumps({"stage": "parse_complete", "statuses": dict(conn.execute("SELECT status,COUNT(*) FROM parse_runs WHERE parser_version=? GROUP BY status", (PARSER_VERSION,)))}), flush=True)


if __name__ == "__main__":
    main()
