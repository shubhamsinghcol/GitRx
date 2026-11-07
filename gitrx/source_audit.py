"""Offline completeness checks over persisted ingest evidence; no fetching."""
import io
import json
import zipfile
from lxml import etree

from .archive import LOINC, NS, artifact_bytes, read_xml
from .parser import load_registry


def audit_section_codes(conn, root):
    registry = load_registry(conn, root)
    rows = conn.execute("SELECT * FROM retrievals WHERE kind='label' AND version_id IS NOT NULL AND status=200").fetchall()
    for response in rows:
        try:
            body = artifact_bytes(conn, root, response["artifact_sha256"])
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                for name in archive.namelist():
                    if not name.lower().endswith('.xml'):
                        continue
                    doc = read_xml(archive.read(name))
                    if doc.tag != '{urn:hl7-org:v3}document':
                        continue
                    for ordinal, section in enumerate(doc.xpath('.//s:section', namespaces=NS), 1):
                        element = section.find('s:code', NS)
                        attrs = dict(element.attrib) if element is not None else {}
                        code, system = attrs.get('code', '<uncoded>'), attrs.get('codeSystem')
                        expected = code in registry and system == LOINC
                        conn.execute('INSERT OR IGNORE INTO section_code_observations VALUES(?,?,?,?,?,?)',
                                     (response['id'], name, ordinal, code, system, int(expected)))
        except (ValueError, zipfile.BadZipFile, etree.XMLSyntaxError, KeyError):
            # Validation evidence already records these; no invented code observation.
            continue
    conn.commit()


def verify_parsed_structure(conn, root, parser_version):
    """Every original section/table must have a retained structured representation."""
    results = []
    for version in conn.execute("SELECT v.*,p.id AS parse_id FROM versions v JOIN parse_runs p ON p.version_id=v.id WHERE p.parser_version=?", (parser_version,)):
        with zipfile.ZipFile(io.BytesIO(artifact_bytes(conn, root, version['selected_sha256']))) as archive:
            doc = read_xml(archive.read(version['xml_member']))
        source_sections = len(doc.xpath('.//s:section', namespaces=NS))
        source_tables = len(doc.xpath('.//s:table', namespaces=NS))
        stored_sections = conn.execute('SELECT COUNT(*) FROM sections WHERE parse_run_id=?', (version['parse_id'],)).fetchone()[0]
        stored_tables = conn.execute('SELECT COUNT(*) FROM narrative_tables t JOIN sections s ON s.id=t.section_id WHERE s.parse_run_id=?', (version['parse_id'],)).fetchone()[0]
        if source_sections != stored_sections or source_tables != stored_tables:
            results.append({'setid':version['setid'],'version':version['version'],
                            'source_sections':source_sections,'stored_sections':stored_sections,
                            'source_tables':source_tables,'stored_tables':stored_tables})
    return results
