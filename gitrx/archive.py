"""Raw-first durable storage, HTTP observations, and SPL identity checks."""
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import io
import json
import os
from pathlib import Path
import random
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from lxml import etree

from .db import dumps

NS = {"s": "urn:hl7-org:v3"}
LOINC = "2.16.840.1.113883.6.1"
BASE = "https://dailymed.nlm.nih.gov/dailymed/"
USER_AGENT = "GitRx/0.1 (local DailyMed human-Rx history research archive; sequential 0.8 requests/sec)"


def now():
    return datetime.now(timezone.utc).isoformat()


def xml_parser():
    return etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False,
                           recover=False, remove_blank_text=False, huge_tree=False)


def read_xml(body):
    root = etree.fromstring(body, parser=xml_parser())
    if root.getroottree().docinfo.doctype or any(isinstance(x, etree._Entity) for x in root.iter()):
        raise ValueError("DTD/entity-bearing XML is retained but not interpreted")
    return root


def put_bytes(conn, root, body, artifact_class=None, purpose='generated payload', provenance=None):
    """Fsync an immutable CAS object before a DB reference can become durable."""
    digest = hashlib.sha256(body).hexdigest()
    existing = conn.execute('SELECT artifact_class FROM artifacts WHERE sha256=?', (digest,)).fetchone()
    chosen = artifact_class or (existing['artifact_class'] if existing else 'pipeline-provenance')
    if chosen not in ('retrieved-evidence', 'pipeline-provenance', 'derived'):
        raise ValueError('Unknown artifact class')
    if existing and existing['artifact_class'] != chosen:
        raise ValueError('Artifact class conflict; explicit reviewed reclassification required')
    if chosen == 'derived':
        conn.execute('INSERT OR IGNORE INTO artifacts(sha256,bytes,path,artifact_class) VALUES(?,?,?,?)',
                     (digest,len(body),'sqlite:derived_artifacts:'+digest,chosen))
        conn.execute('INSERT OR IGNORE INTO derived_artifacts VALUES(?,?,?,?)',
                     (digest,purpose,body,dumps(provenance or {})))
        if artifact_bytes(conn,root,digest) != body:
            raise RuntimeError('Derived payload integrity failure')
        return digest
    relative = Path("artifacts/sha256") / digest[:2] / digest
    path = Path(root) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"CAS integrity failure at {path}")
    else:
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".partial-")
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            # Single writer, never replace an existing object.
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.read_bytes() != body:
                    raise RuntimeError("CAS collision or corruption")
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            Path(temporary).unlink(missing_ok=True)
    conn.execute("INSERT OR IGNORE INTO artifacts(sha256,bytes,path,artifact_class) VALUES(?,?,?,?)",
                 (digest, len(body), str(relative), chosen))
    return digest


def artifact_bytes(conn, root, digest):
    row = conn.execute("SELECT path,artifact_class FROM artifacts WHERE sha256=?", (digest,)).fetchone()
    if row is None:
        raise ValueError('Unknown artifact: '+digest)
    if row['artifact_class']=='derived':
        payload=conn.execute('SELECT payload FROM derived_artifacts WHERE sha256=?',(digest,)).fetchone()
        if payload is None: raise ValueError('Missing derived payload: '+digest)
        body=bytes(payload[0])
    else:
        body = (Path(root) / row["path"]).read_bytes()
    if hashlib.sha256(body).hexdigest() != digest:
        raise RuntimeError(f"CAS hash mismatch: {digest}")
    return body


def evidence_bytes(conn, root, digest):
    """Exclusive raw-source reader for export/citations/census; never provenance."""
    if not conn.execute('SELECT 1 FROM retrieved_evidence WHERE sha256=?',(digest,)).fetchone():
        raise ValueError('retrieved-evidence required: '+digest)
    return artifact_bytes(conn,root,digest)


def require_evidence_references(conn, versions):
    """Validate a citation/export manifest without reading section content."""
    references=set()
    for version in versions:
        if version.get('selected_sha256'):references.add(version['selected_sha256'])
        for record in version.get('retrievals',[]):
            if record.get('artifact_sha256'):references.add(record['artifact_sha256'])
        for record in version.get('parse_runs',[]):
            if record.get('artifact_sha256'):references.add(record['artifact_sha256'])
    for sha in references:
        if not conn.execute('SELECT 1 FROM retrieved_evidence WHERE sha256=?',(sha,)).fetchone():
            raise ValueError('Manifest cites non-evidence: '+sha)


def retry_delay(headers, fallback):
    value = next((v for k, v in headers if k.lower() == "retry-after"), None)
    if value is None:
        return fallback
    try:
        return max(fallback, float(value))
    except ValueError:
        try:
            return max(fallback, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError):
            return fallback


class Fetcher:
    def __init__(self, conn, root, run_id, interval=1.25):
        self.conn, self.root, self.run_id = conn, Path(root), run_id
        self.interval = interval
        self.last_request = 0.0

    def fetch(self, url, kind, version_id=None):
        for attempt in range(1, 4):
            time.sleep(max(0, self.interval - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            record = {"url": url, "retrieved_at": now(), "attempt": attempt,
                      "status": None, "headers": [], "final_url": url, "error": None}
            try:
                request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
                with urllib.request.urlopen(request, timeout=60) as response:
                    body = response.read(300 * 1024 * 1024 + 1)
                    record.update(status=response.status, headers=list(response.headers.items()), final_url=response.url)
                    if len(body) > 300 * 1024 * 1024:
                        record["error"] = "response exceeds 300 MiB limit; retained prefix is not a complete artifact"
            except urllib.error.HTTPError as exc:
                body = exc.read()
                record.update(status=exc.code, headers=list(exc.headers.items()), final_url=exc.url, error=str(exc))
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                body = b""
                record["error"] = str(exc)
            rid = self.record(record, body, kind, version_id)
            row = self.conn.execute("SELECT * FROM retrievals WHERE id=?", (rid,)).fetchone()
            if row["state"] == "available":
                return row
            if record["status"] not in (None, 408, 429, 500, 502, 503, 504) or attempt == 3:
                return row
            time.sleep(retry_delay(record["headers"], 2 ** attempt + random.random()))
        raise AssertionError("unreachable")

    def record(self, record, body, kind, version_id=None, imported_key=None):
        digest = put_bytes(self.conn, self.root, body, artifact_class='retrieved-evidence')
        available = record.get("status") == 200 and not record.get("error")
        failures = self.conn.execute(
            "SELECT COUNT(*) FROM retrievals WHERE version_id=? AND state='unavailable'", (version_id,)).fetchone()[0]
        delay = retry_delay(record["headers"], min(7 * 86400, 6 * 3600 * 2 ** min(failures, 5)))
        retry = None if available else (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
        key = imported_key or hashlib.sha256(dumps(record).encode()).hexdigest()
        self.conn.execute("""INSERT OR IGNORE INTO retrievals
          (observation_key,run_id,version_id,kind,url,final_url,retrieved_at,status,
           headers_json,state,artifact_sha256,error,attempt,next_retry_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
          (key, self.run_id, version_id, kind, record["url"], record.get("final_url"),
           record["retrieved_at"], record.get("status"), dumps(record["headers"]),
           "available" if available else "unavailable", digest, record.get("error"),
           record.get("attempt", 1), retry))
        self.conn.commit()
        return self.conn.execute("SELECT id FROM retrievals WHERE observation_key=?", (key,)).fetchone()[0]


def validate_label(conn, root, retrieval_id, requested_setid, requested_version):
    row = conn.execute("SELECT * FROM retrievals WHERE id=?", (retrieval_id,)).fetchone()
    if row["status"] != 200:
        return {"state": "unavailable", "diagnostics": ["HTTP/network retrieval failed"]}
    body = evidence_bytes(conn, root, row["artifact_sha256"])
    result = {"state": "unavailable", "diagnostics": [], "documents": []}
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            members = [m for m in archive.infolist() if m.filename.lower().endswith(".xml")]
            if len(members) > 32 or any(m.file_size > 100 * 1024 * 1024 for m in members):
                raise ValueError("XML member safety limit exceeded")
            for member in members:
                xml = archive.read(member)
                doc = read_xml(xml)
                if doc.tag != "{urn:hl7-org:v3}document":
                    result["diagnostics"].append(f"non-SPL XML member: {member.filename}")
                    continue
                sid, ver, code = (doc.find("s:setId", NS), doc.find("s:versionNumber", NS), doc.find("s:code", NS))
                observed_setid = sid.get("root", "").lower() if sid is not None else ""
                observed_version = ver.get("value", "") if ver is not None else ""
                attrs = dict(code.attrib) if code is not None else {}
                allowed = attrs.get("codeSystem") == LOINC and conn.execute(
                    "SELECT 1 FROM document_type_allowlist WHERE code=?", (attrs.get("code"),)).fetchone() is not None
                conn.execute("INSERT OR IGNORE INTO document_type_observations VALUES(?,?,?,?,?,?,?,?)",
                             (retrieval_id, member.filename, attrs.get("code", "<uncoded>"), attrs.get("codeSystem"),
                              attrs.get("displayName"), int(allowed), observed_setid, observed_version))
                entry = {"member": member.filename, "setid": observed_setid, "version": observed_version,
                         "code": attrs, "allowed": allowed, "xml_sha256": hashlib.sha256(xml).hexdigest()}
                result["documents"].append(entry)
                try:
                    identity_matches = observed_setid == requested_setid.lower() and int(observed_version) == requested_version
                except ValueError:
                    identity_matches = False
                if not identity_matches:
                    conn.execute("INSERT OR IGNORE INTO identity_conflicts VALUES(?,?,?,?,?,?,?)",
                                 (retrieval_id, member.filename, requested_setid, requested_version,
                                  observed_setid, observed_version, "XML identity differs from requested identity"))
                    entry["identity_mismatch"] = True
                if not allowed:
                    result["diagnostics"].append(f"unexpected document type: {attrs}")
            matches = [d for d in result["documents"] if not d.get("identity_mismatch")]
            if any(d.get("identity_mismatch") for d in result["documents"]):
                result["state"] = "identity_mismatch"
            elif len(matches) != 1:
                result["state"] = "identity_mismatch"
                result["diagnostics"].append(f"expected one matching SPL document, found {len(matches)}")
                conn.execute("INSERT OR IGNORE INTO identity_conflicts VALUES(?,?,?,?,?,?,?)",
                             (retrieval_id, "<archive>", requested_setid, requested_version, None, None,
                              "ambiguous or missing SPL document"))
            else:
                match = matches[0]
                doc = read_xml(archive.read(match["member"]))
                effective = doc.find("s:effectiveTime", NS)
                result.update(state="downloaded", xml_member=match["member"],
                              effective_date=effective.get("value") if effective is not None else None)
    except (zipfile.BadZipFile, etree.XMLSyntaxError, ValueError, KeyError, OSError, RuntimeError) as exc:
        result["diagnostics"].append(f"artifact validation failed: {type(exc).__name__}: {exc}")
    if result["state"] == "unavailable":
        retry = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
        conn.execute("UPDATE retrievals SET state='unavailable',error=?,next_retry_at=? WHERE id=?",
                     ("; ".join(result["diagnostics"]), retry, retrieval_id))
    elif result["state"] == "identity_mismatch":
        conn.execute("UPDATE retrievals SET state='identity_conflict' WHERE id=?", (retrieval_id,))
    conn.execute("UPDATE retrievals SET validation_json=? WHERE id=?", (dumps(result), retrieval_id))
    conn.commit()
    return result
