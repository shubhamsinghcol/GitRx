"""Loss-aware SPL section-instance/narrative/table extraction for Step C."""
import argparse
from collections import Counter
import copy
import hashlib
import io
import json
from pathlib import Path
import unicodedata
import zipfile

from lxml import etree

from .archive import LOINC, NS, artifact_bytes, now, put_bytes, read_xml
from .db import connect, dumps
from .normalize import normalize

PARSER_VERSION = "spl-sections-0.1.0"
CONFIG = {"normalization": "literal-v1", "unicode_version": unicodedata.unidata_version,
          "excerpts": "separate", "tables": "structured+delimited-flat+cells", "network": False,
          "parser_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
          "normalizer_source_sha256": hashlib.sha256(Path(__file__).with_name("normalize.py").read_bytes()).hexdigest(),
          "lxml_version": etree.LXML_VERSION, "libxml_version": etree.LIBXML_VERSION}
BLOCKS = {"paragraph", "list", "item", "table", "footnote", "caption"}
KNOWN_NARRATIVE = BLOCKS | {"text", "content", "linkHtml", "br", "sub", "sup", "footnoteRef",
    "renderMultiMedia", "thead", "tbody", "tfoot", "tr", "th", "td", "col", "colgroup"}


def local(node):
    return etree.QName(node).localname if isinstance(node.tag, str) else "<comment>"


def source_xml(node):
    return etree.tostring(node, encoding="unicode", with_tail=False)


def inline_text(node):
    """Keep text/tails across inline tags; never fuse around media/footnote markers."""
    tag = local(node)
    if tag == "br":
        return "\n"
    if tag == "renderMultiMedia":
        return f" [MEDIA:{node.get('referencedObject', '')}] "
    if tag == "footnoteRef":
        return f" [FOOTNOTE:{node.get('IDREF', '')}] "
    if tag == "table":
        return " [NESTED-TABLE] "
    result = node.text or ""
    for child in node:
        if not isinstance(child.tag, str):
            result += child.tail or ""
            continue
        rendered = inline_text(child)
        if local(child) in {"paragraph", "item", "footnote", "caption"}:
            result += "\n" + rendered + "\n"
        else:
            result += rendered
        result += child.tail or ""
    return result


def escaped_cell(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Narrative:
    def __init__(self, tree):
        self.tree = tree
        self.blocks = []
        self.tables = []
        self.diagnostics = []

    def block(self, kind, text, node, **metadata):
        if normalize(text):
            self.blocks.append({"kind": kind, "text": text, "normalized_text": normalize(text),
                                "source_xpath": self.tree.getpath(node), **metadata})

    def table(self, table):
        ordinal = len(self.tables) + 1
        item = {"ordinal": ordinal, "source_xpath": self.tree.getpath(table),
                "source_xml": source_xml(table), "attributes": dict(table.attrib),
                "caption": [], "columns": [], "rows": [], "diagnostics": []}
        self.tables.append(item)
        for child in table:
            if local(child) == "caption":
                item["caption"].append({"text": inline_text(child), "source_xml": source_xml(child)})
            elif local(child) in {"col", "colgroup"}:
                item["columns"].append({"tag": local(child), "attributes": dict(child.attrib), "source_xml": source_xml(child)})
        flat = []
        for caption in item["caption"]:
            flat.append("<CAPTION>" + escaped_cell(normalize(caption["text"])) + "</CAPTION>")
        for row in table.xpath(".//s:tr", namespaces=NS):
            if next(row.iterancestors("{urn:hl7-org:v3}table"), None) is not table:
                continue
            row_number = len(item["rows"]) + 1
            group = next((local(a) for a in row.iterancestors() if local(a) in {"thead", "tbody", "tfoot"}), None)
            record = {"ordinal": row_number, "group": group, "attributes": dict(row.attrib), "cells": []}
            flat.append("<ROW>")
            for cell in row:
                if local(cell) not in {"td", "th"}:
                    continue
                cell_number = len(record["cells"]) + 1
                text = inline_text(cell)
                cell_record = {"ordinal": cell_number, "tag": local(cell), "attributes": dict(cell.attrib),
                               "text": text, "source_xml": source_xml(cell),
                               "source_xpath": self.tree.getpath(cell), "footnotes": [], "nested_tables": []}
                for attr in ("rowspan", "colspan"):
                    value = cell.get(attr, "1")
                    if not value.isdigit() or int(value) < 1:
                        item["diagnostics"].append({"code": "invalid_table_span", "attribute": attr, "value": value})
                for note in cell.xpath(".//s:footnote", namespaces=NS):
                    cell_record["footnotes"].append({"attributes": dict(note.attrib), "text": inline_text(note), "source_xml": source_xml(note)})
                record["cells"].append(cell_record)
                flat.append("<CELL>" + escaped_cell(normalize(text)) + "</CELL>")
            flat.append("</ROW>")
            item["rows"].append(record)
        item["flattened_text"] = "\n".join(flat)
        self.block("table_flat", item["flattened_text"], table, table_ordinal=ordinal)
        for row in item["rows"]:
            for cell in row["cells"]:
                self.blocks.append({"kind": "table_cell", "text": cell["text"], "normalized_text": normalize(cell["text"]),
                                    "source_xpath": cell["source_xpath"], "table_ordinal": ordinal,
                                    "row_ordinal": row["ordinal"], "cell_ordinal": cell["ordinal"]})
        for child in table.xpath(".//s:table", namespaces=NS):
            if next(child.iterancestors("{urn:hl7-org:v3}table"), None) is table:
                nested_ordinal = self.table(child)
                for row in item["rows"]:
                    for cell in row["cells"]:
                        if self.tree.getpath(child).startswith(cell["source_xpath"] + "/"):
                            cell["nested_tables"].append(nested_ordinal)
        if not item["rows"]:
            item["diagnostics"].append({"code": "table_has_no_rows"})
        self.diagnostics.extend(item["diagnostics"])
        return ordinal

    def walk(self, node):
        if local(node) == "table":
            self.table(node)
            return
        buffer = node.text or ""
        for child in node:
            if not isinstance(child.tag, str):
                buffer += child.tail or ""
                continue
            if local(child) in BLOCKS:
                self.block(local(node), buffer, node)
                buffer = ""
                self.walk(child)
                buffer = child.tail or ""
            else:
                # Inline wrappers containing block descendants must not hide a table.
                if any(local(d) in BLOCKS for d in child.iterdescendants()):
                    self.block(local(node), buffer, node)
                    self.walk(child)
                    buffer = child.tail or ""
                else:
                    buffer += inline_text(child) + (child.tail or "")
        self.block(local(node), buffer, node)

    def extract(self, text):
        for node in text.iter():
            if isinstance(node.tag, str) and (local(node) not in KNOWN_NARRATIVE or etree.QName(node).namespace != NS["s"]):
                self.diagnostics.append({"code": "unexpected_narrative_element", "tag": node.tag, "xpath": self.tree.getpath(node)})
        self.walk(text)
        return self


def parse_document(xml, registry):
    doc = read_xml(xml)
    tree = doc.getroottree()
    elements = doc.xpath(".//s:section", namespaces=NS)
    if not elements:
        raise ValueError("No SPL sections; cannot establish section absence")
    ordinals = {element: i for i, element in enumerate(elements, 1)}
    rows, diagnostics = [], []
    for element in elements:
        ordinal = ordinals[element]
        parents = list(reversed([a for a in element.iterancestors() if a in ordinals]))
        ancestry = "/" + "/".join(str(ordinals[a]) for a in parents)
        parent = ordinals[parents[-1]] if parents else None
        code = element.find("s:code", NS)
        attrs = dict(code.attrib) if code is not None else {}
        loinc_code = attrs.get("code", "<uncoded>")
        title = element.find("s:title", NS)
        text = element.find("s:text", NS)
        kind = "excerpt" if any(local(a) == "excerpt" for a in element.iterancestors()) else "full"
        diag = []
        if attrs.get("codeSystem") != LOINC or loinc_code not in registry:
            diag.append({"code": "unexpected_section_code", "value": loinc_code, "code_system": attrs.get("codeSystem")})
        narrative = Narrative(tree)
        excerpts = []
        for excerpt in element.xpath("./s:excerpt", namespaces=NS):
            for excerpt_text in excerpt.xpath("./s:highlight/s:text", namespaces=NS):
                extracted = Narrative(tree).extract(excerpt_text)
                excerpts.append({"source_xpath": tree.getpath(excerpt_text), "source_xml": source_xml(excerpt_text),
                                 "narrative": extracted})
        status = "complete"
        if text is not None and normalize(inline_text(text)) and (loinc_code == "<uncoded>" or attrs.get("codeSystem") != LOINC):
            status = "partial"
            diag.append({"code": "uncoded_narrative_section"})
        if text is None:
            # Data-only/container sections are preserved and positively identified.
            # An empty leaf label-text section is unknown, not absent/empty success.
            is_container = bool(element.xpath("./s:component/s:section", namespaces=NS))
            is_data_only = bool(element.xpath("./s:subject/s:manufacturedProduct", namespaces=NS))
            if excerpts:
                if kind == "full":
                    kind = "excerpt_only"
                diag.append({"code": "excerpt_only_section"})
            elif loinc_code == "48780-1" or is_container or is_data_only:
                diag.append({"code": "no_own_narrative", "container": is_container})
            else:
                status = "source_empty"
                diag.append({"code": "source_leaf_has_no_narrative"})
        else:
            try:
                narrative.extract(text)
                diag.extend(narrative.diagnostics)
                if narrative.diagnostics:
                    status = "partial"
                if not narrative.blocks and not element.xpath("./s:component/s:section", namespaces=NS):
                    if excerpts:
                        if kind == "full":
                            kind = "excerpt_only"
                        diag.append({"code": "excerpt_only_section"})
                    else:
                        diag.append({"code": "empty_leaf_narrative"})
                        status = "partial"
            except (ValueError, TypeError, RecursionError) as exc:
                status = "extraction_failed"
                diag.append({"code": "narrative_extraction_error", "error": str(exc)})
        for excerpt_index, excerpt in enumerate(excerpts, 1):
            if excerpt["narrative"].diagnostics:
                diag.extend({"scope": "excerpt", "excerpt_ordinal": excerpt_index, **d}
                            for d in excerpt["narrative"].diagnostics)
                if status == "complete":
                    status = "partial"
        rows.append({"loinc_code": loinc_code, "code_system": attrs.get("codeSystem"), "ordinal": ordinal,
                     "ancestry_path": ancestry, "parent_ordinal": parent, "source_xpath": tree.getpath(element),
                     "source_line": element.sourceline, "title": inline_text(title) if title is not None else None,
                     "kind": kind, "status": status, "source_xml": source_xml(element),
                     "narrative_xml": source_xml(text) if text is not None else None,
                     "own_text": "\n\n".join(b["text"] for b in narrative.blocks if b["kind"] != "table_cell"),
                     "diagnostics": diag, "blocks": narrative.blocks, "tables": narrative.tables,
                     "excerpts": excerpts})
        for d in diag:
            diagnostics.append({"ordinal": ordinal, **d})
    # Broken narrative references make negative search conclusions unsafe.
    ids = {n.get("ID") for n in doc.iter() if isinstance(n.tag, str) and n.get("ID")}
    for node in doc.xpath(".//s:footnoteRef | .//s:renderMultiMedia", namespaces=NS):
        target = node.get("IDREF") or node.get("referencedObject")
        if target and target not in ids:
            diagnostics.append({"code": "unresolved_reference", "target": target, "xpath": tree.getpath(node)})
            section = next((a for a in node.iterancestors() if a in ordinals), None)
            if section is not None:
                r = rows[ordinals[section] - 1]
                r["diagnostics"].append(diagnostics[-1])
                if r["status"] == "complete":
                    r["status"] = "partial"
    return rows, diagnostics


def load_registry(conn, root):
    registry = json.loads((Path(root) / "research/section-code-registry.json").read_text())
    source_body = (Path(root) / "research/evidence/fda-section-codes.html").read_bytes()
    if hashlib.sha256(source_body).hexdigest() != registry["source_sha256"]:
        raise RuntimeError("FDA section registry no longer matches retained source")
    put_bytes(conn, root, source_body)
    for filename in ("parser.py", "normalize.py", "archive.py", "db.py"):
        put_bytes(conn, root, Path(__file__).with_name(filename).read_bytes())
    conn.executemany("INSERT OR IGNORE INTO section_code_registry VALUES(?,?,?)",
                     [(r["code"], r["name"], registry["source_url"]) for r in registry["codes"]])
    conn.commit()
    return {r[0] for r in conn.execute("SELECT code FROM section_code_registry")}


def persist_document(conn, root, version, registry):
    existing = conn.execute("""SELECT id FROM parse_runs WHERE version_id=? AND artifact_sha256=?
      AND parser_version=? AND config_json=?""", (version["id"], version["selected_sha256"], PARSER_VERSION, dumps(CONFIG))).fetchone()
    if existing:
        return existing[0]
    rows, diagnostics = [], []
    try:
        body = artifact_bytes(conn, root, version["selected_sha256"])
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            xml = archive.read(version["xml_member"])
        # Recheck identity at the parser boundary, even after ingest validation.
        doc = read_xml(xml)
        sid, number = doc.find("s:setId", NS), doc.find("s:versionNumber", NS)
        if sid is None or number is None or sid.get("root", "").lower() != version["setid"] or int(number.get("value")) != version["version"]:
            raise ValueError("Parser-boundary XML identity mismatch")
        rows, diagnostics = parse_document(xml, registry)
        status = "partial" if any(r["status"] != "complete" for r in rows) else "complete"
    except (etree.XMLSyntaxError, ValueError, TypeError, KeyError, zipfile.BadZipFile, OSError) as exc:
        status = "extraction_failed"
        diagnostics.append({"code": "document_extraction_error", "error": f"{type(exc).__name__}: {exc}"})
    with conn:
        run_id = conn.execute("""INSERT INTO parse_runs
          (version_id,artifact_sha256,parser_version,config_json,created_at,status,diagnostics_json,section_count)
          VALUES(?,?,?,?,?,?,?,?)""", (version["id"], version["selected_sha256"], PARSER_VERSION,
                                      dumps(CONFIG), now(), status, dumps(diagnostics), len(rows))).lastrowid
        for row in rows:
            section_id = conn.execute("""INSERT INTO sections
              (parse_run_id,version_id,parser_version,loinc_code,code_system,ordinal,ancestry_path,
               parent_ordinal,source_xpath,source_line,title,kind,status,source_xml,narrative_xml,own_text,diagnostics_json)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (run_id, version["id"], PARSER_VERSION, row["loinc_code"], row["code_system"], row["ordinal"],
               row["ancestry_path"], row["parent_ordinal"], row["source_xpath"], row["source_line"], row["title"],
               row["kind"], row["status"], row["source_xml"], row["narrative_xml"], row["own_text"], dumps(row["diagnostics"]))).lastrowid
            narratives = [("excerpt" if row["kind"] == "excerpt" else "full", row["blocks"], row["tables"])]
            for i, excerpt in enumerate(row["excerpts"], 1):
                extracted = excerpt["narrative"]
                text = "\n\n".join(b["text"] for b in extracted.blocks if b["kind"] != "table_cell")
                conn.execute("INSERT INTO section_excerpts(section_id,ordinal,source_xpath,source_xml,extracted_text,status,diagnostics_json) VALUES(?,?,?,?,?,?,?)",
                             (section_id, i, excerpt["source_xpath"], excerpt["source_xml"], text,
                              "partial" if extracted.diagnostics else "complete", dumps(extracted.diagnostics)))
                narratives.append(("excerpt", extracted.blocks, extracted.tables))
            block_ordinal, table_offset = 0, 0
            for scope, blocks, tables in narratives:
                for block in blocks:
                    block_ordinal += 1
                    table_ordinal = block.get("table_ordinal")
                    conn.execute("""INSERT INTO narrative_blocks
                      (section_id,ordinal,kind,text,normalized_text,source_xpath,table_ordinal,row_ordinal,cell_ordinal,scope)
                      VALUES(?,?,?,?,?,?,?,?,?,?)""", (section_id, block_ordinal, block["kind"], block["text"], block["normalized_text"],
                        block["source_xpath"], table_ordinal + table_offset if table_ordinal else None,
                        block.get("row_ordinal"), block.get("cell_ordinal"), scope))
                for table in tables:
                    stored_table = copy.deepcopy(table)
                    stored_table["ordinal"] += table_offset
                    for stored_row in stored_table["rows"]:
                        for stored_cell in stored_row["cells"]:
                            stored_cell["nested_tables"] = [n + table_offset for n in stored_cell["nested_tables"]]
                    conn.execute("""INSERT INTO narrative_tables
                      (section_id,ordinal,source_xpath,source_xml,structured_json,flattened_text,diagnostics_json,scope)
                      VALUES(?,?,?,?,?,?,?,?)""", (section_id, table["ordinal"] + table_offset, table["source_xpath"], table["source_xml"],
                        dumps(stored_table), table["flattened_text"], dumps(table["diagnostics"]), scope))
                table_offset += len(tables)
    return run_id


def section_state(conn, version_id, loinc_code, parse_run_id=None):
    """Extraction-state primitive for reports/future queries; no phrase inference."""
    version = conn.execute("SELECT * FROM versions WHERE id=?", (version_id,)).fetchone()
    if version["state"] != "downloaded":
        return {"state": "artifact_unavailable" if version["state"] == "unavailable" else "conflict", "instances": []}
    if parse_run_id is None:
        run = conn.execute("SELECT * FROM parse_runs WHERE version_id=? AND parser_version=? ORDER BY id DESC LIMIT 1", (version_id, PARSER_VERSION)).fetchone()
    else:
        run = conn.execute("SELECT * FROM parse_runs WHERE id=? AND version_id=?", (parse_run_id, version_id)).fetchone()
    if run is None or run["status"] == "extraction_failed":
        return {"state": "extraction_failed", "instances": []}
    instances = conn.execute("SELECT * FROM sections WHERE parse_run_id=? AND loinc_code=? AND kind IN ('full','excerpt_only') ORDER BY ordinal", (run["id"], loinc_code)).fetchall()
    if not instances:
        identification_incomplete = any(d["code"] == "uncoded_narrative_section"
                                        for d in json.loads(run["diagnostics_json"]))
        return {"state": "extraction_failed" if identification_incomplete else "absent", "instances": [],
                "reason": "uncoded narrative could not be assigned to requested scope" if identification_incomplete
                          else "complete structural scan found no requested code"}
    if all(s["kind"] == "excerpt_only" for s in instances):
        return {"state": "excerpt_only", "instances": [r["id"] for r in instances]}
    # Include descendant extraction failures without merging section instance rows.
    descendants = conn.execute("SELECT ordinal,parent_ordinal,status FROM sections WHERE parse_run_id=? AND kind='full' ORDER BY ordinal", (run["id"],)).fetchall()
    selected = {s["ordinal"] for s in instances}
    status = "present"
    for s in descendants:
        if s["parent_ordinal"] in selected:
            selected.add(s["ordinal"])
        if s["ordinal"] in selected and s["status"] != "complete":
            status = "extraction_failed"
    return {"state": status, "instances": [r["id"] for r in instances]}


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
