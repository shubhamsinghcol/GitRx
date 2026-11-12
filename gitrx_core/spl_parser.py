"""SPL section parser spl-sections-0.2.2 (pure; derived from frozen spl-sections-0.1.0).

parse(xml_bytes) -> canonical document dict. No DB, no file/network I/O, no clock.
Identical input yields byte-identical canonical_json(). Build-failing invariants
(text conservation, order, no double assignment, reference resolution) are checked on
every parse and raise InvariantViolation.

Differences from 0.1.0 are listed in CHANGES_FROM_0_1_0.
"""
import hashlib
import json
import re
import unicodedata

from lxml import etree

from .normalize import WHITE_SPACE, normalize

PARSER_VERSION = "spl-sections-0.2.4"
NORMALIZATION = "literal-v1"
HL7 = "urn:hl7-org:v3"
NS = {"s": HL7}
LOINC = "2.16.840.1.113883.6.1"
CHANGES_FROM_0_1_0 = (
    "status model replaced (document: complete/complete_with_source_gaps/identification_incomplete/"
    "extraction_defect/no_label_body; section: ok/empty_in_source/uncoded plus separate diagnostics)",
    "documents without SPL sections are no_label_body, not exceptions",
    "section id/code/displayName/number/path/keys captured; registry hint is inferred and separate",
    "product_data, media_text and unassigned buckets added; titles/excerpts/narrative unchanged",
    "[MEDIA:id] marker is now emitted for renderMultiMedia that wraps a caption (0.1.0 lost it)",
    "blocks gate on non-whitespace characters, not normalize() truthiness (soft-hyphen-only text kept)",
    "table caption text is also emitted as a table_caption block (not part of own_text)",
    "per-table/section source_xml replaced by sha256; normalized_text per block dropped (derivable)",
    "empty IDREF / referencedObject counts as unresolved_ref",
)

CHANGES_FROM_0_2_0 = (
    "0.2.1: text inside a renderMultiMedia (e.g. its caption) is emitted after the [MEDIA:id] marker "
    "in table cells, titles and other inline contexts; 0.2.0 classified it as narrative but dropped it, "
    "raising a conservation InvariantViolation (304 of 487,201 versions). Empty wrappers are unchanged. "
    "No invariant was changed.",
)

CHANGES_FROM_0_2_1 = (
    "0.2.2: rowspan=\"0\" (span to the end of the row group, HTML) and colspan=\"0\" (treated as 1, HTML5) "
    "are valid and no longer invalid_table_span; ASCII whitespace around a span integer is ignored. "
    "Negative, non-numeric, empty, fractional, percentage, rowspan > 65534 and colspan > 1000 stay defects. "
    "Cells that carry a span attribute gain effective_rowspan/effective_colspan.",
)

CHANGES_FROM_0_2_2 = (
    "0.2.3: table span attributes follow the HTML rules for parsing non-negative integers: leading ASCII "
    "whitespace skipped, leading digits parsed (\"100%\" -> 100, \"1.5\" -> 1, \"2'\" -> 2), no leading digits "
    "(empty, \"-1\", \"NaN\", non-ASCII digits) -> attribute ignored, default 1; rowspan 0 -> to the end of the row "
    "group; colspan 0 -> 1; rowspan clamped to 65534 and colspan to 1000. No span value is an extraction "
    "defect any more; a value that was not a clean integer within range is recorded as an info diagnostic "
    "nonstandard_table_span (raw value kept) and as cell attribute span_raw.",
)

BLOCKS = {"paragraph", "list", "item", "table", "footnote", "caption"}
KNOWN_NARRATIVE = BLOCKS | {"text", "content", "linkHtml", "br", "sub", "sup", "footnoteRef",
    "renderMultiMedia", "thead", "tbody", "tfoot", "tr", "th", "td", "col", "colgroup"}
DEFECT_CODES = {"unknown_tag", "unresolved_ref", "table_has_no_rows",
                "section_outside_body", "xml_error", "document_identity_missing"}
DERIVED_KINDS = {"table_flat"}
SIDE_KINDS = {"table_cell", "table_caption"}  # not in own_text (table_flat is)
BUCKETS = ("title", "narrative", "excerpt", "product_data", "media_text", "unassigned")
_WS_DEL = {ord(c): None for c in WHITE_SPACE}
_TQ = lambda n: "{%s}%s" % (HL7, n)  # noqa: E731


class InvariantViolation(RuntimeError):
    """A build-failing parser invariant was violated."""


def nonws_len(text):
    return len(text.translate(_WS_DEL)) if text else 0


def local(node):
    return etree.QName(node).localname if isinstance(node.tag, str) else "<comment>"


def is_el(node):
    return isinstance(node.tag, str)


def xml_sha(node):
    return hashlib.sha256(etree.tostring(node, encoding="utf-8", with_tail=False)).hexdigest()


def strip_ws(text):
    return text.strip(WHITE_SPACE)


class Acc:
    """Text atoms (node, 'text'|'tail') and reference markers consumed by one emitted piece."""
    __slots__ = ("atoms", "markers")

    def __init__(self):
        self.atoms, self.markers = [], []


def inline_text(node, acc=None):
    """Keep text/tails across inline tags; never fuse around media/footnote markers."""
    tag = local(node)
    if tag == "br":
        return "\n"
    if tag == "renderMultiMedia":
        marker = f" [MEDIA:{node.get('referencedObject', '')}] "
        if acc is not None:
            acc.markers.append(("MEDIA", node.get("referencedObject", ""), marker))
        # 0.2.1: a renderMultiMedia may wrap a caption. Inside table cells/titles that text
        # was classified as narrative but never emitted (conservation failure). Emit it after
        # the marker, but only when it carries non-whitespace text (so empty wrappers are
        # byte-identical to 0.2.0).
        inner = Acc()
        body = _inline_content(node, inner)
        if nonws_len(body):
            if acc is not None:
                acc.atoms.extend(inner.atoms)
                acc.markers.extend(inner.markers)
            return marker + "\n" + body + "\n"
        return marker
    if tag == "footnoteRef":
        marker = f" [FOOTNOTE:{node.get('IDREF', '')}] "
        if acc is not None:
            acc.markers.append(("FOOTNOTE", node.get("IDREF", ""), marker))
        return marker
    if tag == "table":
        marker = " [NESTED-TABLE] "
        if acc is not None:
            acc.markers.append(("NESTED-TABLE", "", marker))
        return marker
    return _inline_content(node, acc)


def _inline_content(node, acc):
    result = node.text or ""
    if result and acc is not None:
        acc.atoms.append((node, "text"))
    for child in node:
        if not is_el(child):
            tail = child.tail or ""
            if tail and acc is not None:
                acc.atoms.append((child, "tail"))
            result += tail
            continue
        rendered = inline_text(child, acc)
        if local(child) in {"paragraph", "item", "footnote", "caption"}:
            result += "\n" + rendered + "\n"
        else:
            result += rendered
        tail = child.tail or ""
        if tail and acc is not None:
            acc.atoms.append((child, "tail"))
        result += tail
    return result


_SPAN_RE = re.compile(r"[ \t\n\f\r]*(\+?)([0-9]+)")
_SPAN_MAX = {"rowspan": 65534, "colspan": 1000}


def parse_span(attr, value):
    """HTML rules for parsing non-negative integers, applied to a rowspan/colspan value.

    Returns (n, clean): n is the effective integer (0 only for rowspan, meaning "to the end of the row group";
    colspan 0 -> 1; no leading digits -> 1; clamped to the HTML maximum); clean is False when the raw value was
    not exactly an ASCII integer (optionally space-padded) within range, i.e. HTML repaired it."""
    m = _SPAN_RE.match(value)
    if not m:
        return 1, False
    n = int(m.group(2))
    clean = m.end() == len(value.rstrip(" \t\n\f\r")) and not m.group(1) and n <= _SPAN_MAX[attr]
    n = min(n, _SPAN_MAX[attr])
    if attr == "colspan" and n == 0:
        n = 1
    return n, clean


def escaped_cell(text):
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class Narrative:
    """Faithful narrative extraction (0.1.0 core) with atom/marker bookkeeping."""

    def __init__(self, tree):
        self.tree = tree
        self.blocks, self.tables, self.diagnostics = [], [], []
        self.acc = Acc()
        self.texts = []

    def _take(self, node, kind):
        text = node.text if kind == "text" else node.tail
        if text:
            self.acc.atoms.append((node, kind))
            return text
        return ""

    def block(self, kind, text, node, **meta):
        acc, self.acc = self.acc, Acc()
        if nonws_len(text):
            self.blocks.append({"kind": kind, "text": text, "source_xpath": self.tree.getpath(node),
                                "_atoms": acc.atoms, "_markers": acc.markers, **meta})

    def derived_block(self, kind, text, node, **meta):
        if nonws_len(text):
            self.blocks.append({"kind": kind, "text": text, "source_xpath": self.tree.getpath(node),
                                "_atoms": [], "_markers": [], **meta})

    def table(self, table, outer=None):
        ordinal = len(self.tables) + 1
        outer = outer or ordinal
        item = {"ordinal": ordinal, "source_xpath": self.tree.getpath(table),
                "source_xml_sha256": xml_sha(table), "attributes": dict(table.attrib),
                "caption": [], "columns": [], "rows": [], "diagnostics": []}
        self.tables.append(item)
        captions = []
        for child in table:
            if not is_el(child):
                continue
            if local(child) == "caption":
                acc = Acc()
                text = inline_text(child, acc)
                item["caption"].append({"text": text})
                captions.append((child, text, acc))
            elif local(child) in {"col", "colgroup"}:
                item["columns"].append({"tag": local(child), "attributes": dict(child.attrib)})
        flat = ["<CAPTION>" + escaped_cell(normalize(c["text"])) + "</CAPTION>" for c in item["caption"]]
        cell_pieces = []
        zero_rowspans, row_groups = [], []
        for row in table.xpath(".//s:tr", namespaces=NS):
            if next(row.iterancestors("{urn:hl7-org:v3}table"), None) is not table:
                continue
            row_number = len(item["rows"]) + 1
            group_node = next((a for a in row.iterancestors() if local(a) in {"thead", "tbody", "tfoot"}), None)
            group = local(group_node) if group_node is not None else None
            row_groups.append(group_node)
            record = {"ordinal": row_number, "group": group, "attributes": dict(row.attrib), "cells": []}
            flat.append("<ROW>")
            for cell in row:
                if not is_el(cell) or local(cell) not in {"td", "th"}:
                    continue
                cell_number = len(record["cells"]) + 1
                acc = Acc()
                text = inline_text(cell, acc)
                cell_record = {"ordinal": cell_number, "tag": local(cell), "attributes": dict(cell.attrib),
                               "text": text, "source_xpath": self.tree.getpath(cell),
                               "footnotes": [], "nested_tables": []}
                for attr in ("rowspan", "colspan"):
                    value = cell.get(attr)
                    if value is None:
                        continue
                    n, clean = parse_span(attr, value)
                    if not clean:
                        item["diagnostics"].append({"code": "nonstandard_table_span", "attribute": attr, "value": value})
                    if attr == "colspan":
                        cell_record["effective_colspan"] = n
                    elif n:
                        cell_record["effective_rowspan"] = n
                    else:
                        zero_rowspans.append((cell_record, row_number, group_node))
                for note in cell.xpath(".//s:footnote", namespaces=NS):
                    cell_record["footnotes"].append({"attributes": dict(note.attrib), "text": inline_text(note)})
                record["cells"].append(cell_record)
                cell_pieces.append((row_number, cell_number, cell_record, acc))
                flat.append("<CELL>" + escaped_cell(normalize(text)) + "</CELL>")
            flat.append("</ROW>")
            item["rows"].append(record)
        for cell_record, start, group_node in zero_rowspans:  # rowspan=0: to the end of the row group
            cell_record["effective_rowspan"] = sum(1 for g in row_groups[start - 1:] if g is group_node)
        item["flattened_text"] = "\n".join(flat)
        self.derived_block("table_flat", item["flattened_text"], table, table_ordinal=ordinal, top_table=outer)
        for node, text, acc in captions:
            if nonws_len(text):
                self.blocks.append({"kind": "table_caption", "text": text, "source_xpath": self.tree.getpath(node),
                                    "table_ordinal": ordinal, "top_table": outer,
                                    "_atoms": acc.atoms, "_markers": acc.markers})
        for row_number, cell_number, cell_record, acc in cell_pieces:
            self.blocks.append({"kind": "table_cell", "text": cell_record["text"],
                                "source_xpath": cell_record["source_xpath"], "table_ordinal": ordinal,
                                "top_table": outer, "row_ordinal": row_number, "cell_ordinal": cell_number,
                                "_atoms": acc.atoms, "_markers": acc.markers})
        for child in table.xpath(".//s:table", namespaces=NS):
            if next(child.iterancestors("{urn:hl7-org:v3}table"), None) is table:
                nested_ordinal = self.table(child, outer)
                child_path = self.tree.getpath(child)
                for row in item["rows"]:
                    for cell in row["cells"]:
                        if child_path.startswith(cell["source_xpath"] + "/"):
                            cell["nested_tables"].append(nested_ordinal)
        if not item["rows"]:
            item["diagnostics"].append({"code": "table_has_no_rows"})
        for d in item["diagnostics"]:
            self.diagnostics.append({**d, "table_ordinal": ordinal})
        return ordinal

    def walk(self, node):
        if local(node) == "table":
            self.table(node)
            return
        buffer = self._take(node, "text")
        for child in node:
            if not is_el(child):
                buffer += self._take(child, "tail")
                continue
            if local(child) in BLOCKS:
                self.block(local(node), buffer, node)
                buffer = ""
                self.walk(child)
                buffer = self._take(child, "tail")
            elif any(local(d) in BLOCKS for d in child.iterdescendants()):
                # Inline wrappers containing block descendants must not hide a table.
                if local(child) == "renderMultiMedia":
                    buffer += inline_text_marker(child, self.acc)
                self.block(local(node), buffer, node)
                self.walk(child)
                buffer = self._take(child, "tail")
            else:
                buffer += inline_text(child, self.acc)
                buffer += self._take(child, "tail")
        self.block(local(node), buffer, node)

    def extract(self, text):
        self.texts.append(text)
        for node in text.iter():
            if is_el(node) and (local(node) not in KNOWN_NARRATIVE or etree.QName(node).namespace != HL7):
                self.diagnostics.append({"code": "unknown_tag", "tag": node.tag, "xpath": self.tree.getpath(node)})
        self.walk(text)
        return self

    def own_text(self):
        return "\n\n".join(b["text"] for b in self.blocks if b["kind"] not in SIDE_KINDS)


def inline_text_marker(node, acc):
    marker = f" [MEDIA:{node.get('referencedObject', '')}] "
    acc.markers.append(("MEDIA", node.get("referencedObject", ""), marker))
    return marker
_REGISTRY_TITLES = {
    "abuse": "34086-9",
    "accessories": "60555-0",
    "adverse reactions": "34084-4",
    "alarms": "69761-5",
    "animal pharmacology and or toxicology": "34091-9",
    "assembly or installation instructions": "60556-8",
    "boxed warning": "34066-1",
    "calibration instructions": "60557-6",
    "carcinogenesis and mutagenesis and impairment of fertility": "34083-6",
    "cleaning, disinfecting, and sterilization instructions": "60558-4",
    "clinical pharmacology": "34090-1",
    "clinical studies": "34092-7",
    "clinical trials experience": "90374-0",
    "compatible accessories": "69760-7",
    "components": "60559-2",
    "contraindications": "34070-3",
    "controlled substance": "34085-1",
    "dependence": "34087-7",
    "description": "34089-3",
    "diagram of device": "69758-1",
    "disposal and waste handling": "69763-1",
    "dosage and administration": "34068-7",
    "dosage forms and strengths": "43678-2",
    "drug abuse and dependence": "42227-9",
    "drug and or laboratory test interactions": "34074-5",
    "drug interactions": "34073-7",
    "environmental warning": "50742-6",
    "females and males of reproductive potential": "77291-3",
    "food safety warning": "50743-4",
    "general precautions": "34072-9",
    "geriatric use": "34082-8",
    "guaranteed analysis of feed": "50740-0",
    "health care provider letter": "71744-7",
    "health claim": "69719-3",
    "hepatic impairment subsection": "88829-7",
    "how supplied": "34069-5",
    "immunogenicity": "88830-5",
    "inactive ingredient": "51727-6",
    "indications and usage": "34067-9",
    "information for owners/caregivers": "50744-2",
    "information for patients": "34076-0",
    "instructions for use": "59845-8",
    "intended use of the device": "60560-0",
    "labor and delivery": "34079-4",
    "laboratory tests": "34075-2",
    "lactation": "77290-5",
    "mechanism of action": "43679-0",
    "microbiology": "49489-8",
    "nonclinical toxicology": "43680-8",
    "nonteratogenic effects": "34078-6",
    "nursing mothers": "34080-2",
    "otc - active ingredient": "55106-9",
    "otc - ask doctor": "50569-3",
    "otc - ask doctor/pharmacist": "50568-5",
    "otc - do not use": "50570-1",
    "otc - keep out of reach of children": "50565-1",
    "otc - pregnancy or breast feeding": "53414-9",
    "otc - purpose": "55105-1",
    "otc - questions": "53413-1",
    "otc - stop use": "50566-9",
    "otc - when using": "50567-7",
    "other safety information": "60561-8",
    "overdosage": "34088-5",
    "package label.principal display panel": "51945-4",
    "patient counseling information": "88436-1",
    "patient medication information": "68498-5",
    "pediatric use": "34081-0",
    "pharmacodynamics": "43681-6",
    "pharmacogenomics": "66106-6",
    "pharmacokinetics": "43682-4",
    "postmarketing experience": "90375-7",
    "precautions": "42232-9",
    "pregnancy": "42228-7",
    "recent major changes": "43683-2",
    "references": "34093-5",
    "rems addressed risk": "100382-1",
    "rems administrative information": "87523-7",
    "rems applicant requirements": "87526-0",
    "rems communication": "82344-3",
    "rems elements": "82348-4",
    "rems elements to assure safe use": "82345-0",
    "rems goals": "82349-2",
    "rems implementation system": "82350-0",
    "rems material": "82346-8",
    "rems medication guide": "82598-4",
    "rems participant requirements": "87525-2",
    "rems requirements": "87524-5",
    "rems summary": "82347-6",
    "rems timetable for submission assessments": "82352-6",
    "renal impairment subsection": "88828-9",
    "residue warning": "53412-3",
    "risks": "69759-9",
    "route, method and frequency of administration": "60562-6",
    "safe handling warning": "50741-8",
    "spl indexing data elements": "48779-3",
    "spl medguide": "42231-1",
    "spl patient package insert": "42230-3",
    "spl product data elements": "48780-1",
    "statement of identity": "69718-5",
    "storage and handling": "44425-7",
    "summary of safety and effectiveness": "60563-4",
    "teratogenic effects": "34077-8",
    "troubleshooting": "69762-3",
    "use in specific populations": "43684-0",
    "user safety warnings": "54433-8",
    "veterinary indications": "50745-9",
    "warnings": "34071-1",
    "warnings and precautions": "43685-7",
}


def _title_key(text):
    t = normalize(text).casefold()
    t = re.sub(r"\s*&\s*", " and ", t)
    return re.sub(r"\s+", " ", t).strip(" .:;")


_NUMBER_RE = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2}){0,3})(?:\.(?!\d))?(?=\s|$)\s*")


def split_section_number(title_normalized):
    m = _NUMBER_RE.match(title_normalized or "")
    if not m or len(title_normalized) == m.end():
        return None, title_normalized or ""
    return m.group(1), title_normalized[m.end():]


def registry_hint(title_rest, code):
    """Inferred title -> registry hint. Never replaces the recorded code."""
    key = _title_key(title_rest)
    key = re.sub(r"\s+section$", "", key)
    inferred, rule = _REGISTRY_TITLES.get(key), "registry_name"
    if inferred is None and key.startswith("boxed warning"):
        inferred, rule = "34066-1", "title_prefix"
    if inferred is None:
        return None
    return {"inferred": True, "rule": rule, "loinc": inferred, "agrees_with_code": inferred == code}


# --------------------------------------------------------------------------- source scan

def _child_ctx(el, parent, ctx, ordinals):
    tag = el.tag
    if tag == _TQ("section"):
        return ("unassigned", ordinals.get(el))
    if ctx[0] != "unassigned":
        return ctx
    ptag = parent.tag
    if ptag == _TQ("section"):
        if tag == _TQ("title"):
            return ("title", ctx[1])
        if tag == _TQ("text"):
            return ("narrative", ctx[1])
        if tag == _TQ("subject"):
            return ("product_data", ctx[1])
    elif tag == _TQ("text") and ptag == _TQ("highlight"):
        ex = parent.getparent()
        if ex is not None and ex.tag == _TQ("excerpt") and ex.getparent() is not None \
                and ex.getparent().tag == _TQ("section"):
            return ("excerpt", ctx[1])
    if tag == _TQ("observationMedia"):
        return ("media_text", ctx[1])
    return ctx


def scan_source(roots, ordinals):
    """Classify every text atom (element text / tail) of the document body into one bucket.
    Returns (atoms, refs): atoms [(node, kind, (bucket, owner_ordinal))]; refs [(node, ctx)]."""
    out, refs = [], []
    ref_tags = (_TQ("footnoteRef"), _TQ("renderMultiMedia"))

    def visit(el, ctx):
        if el.tag in ref_tags:
            refs.append((el, ctx))
        if el.text:
            out.append((el, "text", ctx))
        for c in el:
            if is_el(c):
                visit(c, _child_ctx(c, el, ctx, ordinals))
            if c.tail:
                out.append((c, "tail", ctx))

    for r in roots:
        visit(r, ("unassigned", None))
    return out, refs


def _fail(msg):
    raise InvariantViolation(msg)


def _atom_text(atom):
    node, kind = atom
    return node.text if kind == "text" else node.tail


def _nw_atoms(atoms):
    return [a for a in atoms if nonws_len(_atom_text(a))]


def _check_sequence(items, what):
    last = -1
    for lo, hi in items:
        if lo <= last:
            _fail(f"order violation in {what}: range {lo}-{hi} does not follow {last}")
        last = hi


def _positions(atoms, pos, what):
    idx = []
    for a in _nw_atoms(atoms):
        if a not in pos:
            _fail(f"{what}: consumed text atom outside the document body")
        idx.append(pos[a])
    return idx


def check_order(narr, pos, what):
    """Pieces must follow source order. Top-level blocks and whole outermost tables form a
    sequence; inside a table, the cells of one table level appear in row-major order
    (nested-table content sits between a cell's own pieces and is checked at its own level)."""
    items, cur_top, cur = [], None, []
    levels = {}

    def close():
        if cur:
            items.append((min(cur), max(cur)))

    for b in narr.blocks:
        if b["kind"] == "table_flat":
            continue
        idx = _positions(b["_atoms"], pos, what)
        if idx != sorted(idx) or len(set(idx)) != len(idx):
            _fail(f"order violation inside one block of {what}")
        top = b.get("top_table")
        if top is None:
            close()
            cur, cur_top = [], None
            if idx:
                items.append((idx[0], idx[-1]))
        else:
            if cur_top != top:
                close()
                cur, cur_top = [], top
            cur.extend(idx)
            if b["kind"] == "table_cell" and idx:
                levels.setdefault(b["table_ordinal"], []).append((idx[0], idx[-1]))
    close()
    _check_sequence(items, what + " blocks")
    for t, ranges in sorted(levels.items()):
        _check_sequence(ranges, f"{what} table {t} cells")


# --------------------------------------------------------------------------- document

def read_xml(body):
    # 0.2.4: huge_tree=True (libxml2 XML_PARSE_HUGE) lifts only libxml2's size/depth safety limits (element nesting
    # of 256, 10 MB text nodes, name/dictionary sizes) so legitimately deep SPL parses.  It does not enable entity
    # or DTD processing: resolve_entities=False (no XML_PARSE_NOENT, so entity references are never expanded and
    # no entity text is substituted), load_dtd=False and no_network=True (no external DTD/entity fetch, hence no
    # XXE and no network access) stay as in 0.2.3, and the check below still rejects any DTD/entity-bearing
    # document before it is interpreted.  The only exposure is memory use on pathological input, which is
    # bounded by the retained raw file size.
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False,
                             recover=False, remove_blank_text=False, huge_tree=True)
    root = etree.fromstring(body, parser=parser)
    if root.getroottree().docinfo.doctype or any(isinstance(x, etree._Entity) for x in root.iter()):
        raise ValueError("DTD/entity-bearing XML is retained but not interpreted")
    return root


def canonical_json(doc):
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def content_hash(doc):
    body = {k: v for k, v in doc.items() if k != "content_sha256"}
    return hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def environment():
    """Runtime facts that can influence output; deliberately outside the hashed document."""
    return {"unicode_version": unicodedata.unidata_version, "lxml_version": list(etree.LXML_VERSION),
            "libxml_version": list(etree.LIBXML_VERSION)}


def _attr(el, name, default=None):
    return el.get(name, default) if el is not None else default


def _first(el, path):
    r = el.find(path, NS)
    return r


def _code_dict(c):
    if c is None:
        return None
    return {"code": c.get("code"), "codeSystem": c.get("codeSystem"), "displayName": c.get("displayName")}


def _products(root, ordinals):
    out = []
    for outer in root.xpath(".//s:section/s:subject/s:manufacturedProduct", namespaces=NS):
        sec = outer.getparent().getparent()
        inner = outer.find("s:manufacturedProduct", NS)
        base = inner if inner is not None else outer
        name = base.find("s:name", NS)
        approvals = []
        for ap in outer.xpath(".//s:approval", namespaces=NS):
            ident = ap.find("s:id", NS)
            approvals.append({"application_number": _attr(ident, "extension"), "id_root": _attr(ident, "root"),
                              "category": _code_dict(ap.find("s:code", NS))})
        out.append({"section_ordinal": ordinals.get(sec),
                    "product_codes": [_code_dict(c) for c in base.findall("s:code", NS)],
                    "name": strip_ws("".join(name.itertext())) if name is not None else None,
                    "form": _code_dict(base.find("s:formCode", NS)), "approvals": approvals})
    return out


def _header(root, sha):
    def val(path, attr):
        return _attr(root.find(path, NS), attr)
    sid = val("s:setId", "root")
    ver = val("s:versionNumber", "value")
    title = root.find("s:title", NS)
    org = root.xpath("./s:author/s:assignedEntity/s:representedOrganization/s:name", namespaces=NS)
    ttl = inline_text(title) if title is not None else None
    return {
        "parser_version": PARSER_VERSION, "normalization": NORMALIZATION, "source_sha256": sha,
        "document_id": val("s:id", "root"), "setid": sid.lower() if sid else None,
        "version": int(ver) if ver and ver.strip().isdigit() else None, "version_raw": ver,
        "effective_time": val("s:effectiveTime", "value"),
        "document_type": _code_dict(root.find("s:code", NS)),
        "title": ttl, "title_normalized": normalize(ttl) if ttl is not None else None,
        "author_organization": strip_ws("".join(org[0].itertext())) if org else None,
    }


def _empty_doc(head, status, diagnostics, reasons):
    doc = {**head, "status": status, "status_reasons": reasons, "body_kind": "none", "sections": [],
           "products": [], "application_numbers": [], "marketing_categories": [], "unassigned": [],
           "references": [], "diagnostics": diagnostics,
           "buckets": {b: 0 for b in BUCKETS}, "bucket_total": 0,
           "counts": {"sections": 0, "tables": 0, "blocks": 0}}
    doc["content_sha256"] = content_hash(doc)
    return doc


def parse(xml_bytes):
    """Pure: bytes -> canonical document dict. Raises InvariantViolation on parser invariant breach."""
    if not isinstance(xml_bytes, (bytes, bytearray)):
        raise TypeError("parse() takes bytes")
    xml = bytes(xml_bytes)
    sha = hashlib.sha256(xml).hexdigest()
    try:
        root = read_xml(xml)
    except (etree.XMLSyntaxError, ValueError) as exc:
        head = {"parser_version": PARSER_VERSION, "normalization": NORMALIZATION, "source_sha256": sha}
        return _empty_doc(head, "extraction_defect",
                          [{"code": "xml_error", "severity": "defect", "error": f"{type(exc).__name__}: {exc}"}],
                          ["xml_error"])
    head = _header(root, sha)
    if root.tag != _TQ("document"):
        return _empty_doc(head, "extraction_defect",
                          [{"code": "xml_error", "severity": "defect", "error": "root element is not hl7 document"}],
                          ["xml_error"])
    return _parse_tree(root, head)


def _parse_tree(root, head):
    tree = root.getroottree()
    docidx = {el: i for i, el in enumerate(root.iter())}
    sections = root.xpath(".//s:section", namespaces=NS)
    ordinals = {el: i for i, el in enumerate(sections, 1)}
    body_roots = [b for b in root.xpath(".//s:structuredBody | .//s:nonXMLBody", namespaces=NS)]
    body_set = set(body_roots)
    body_roots = [b for b in body_roots if not any(a in body_set for a in b.iterancestors())]
    doc_diag = []
    rootset = set(body_roots)
    outside = [s for s in sections if not any(a in rootset for a in s.iterancestors())]
    outside_set = set(outside)
    for s in outside:
        if not any(a in outside_set for a in s.iterancestors()):
            body_roots.append(s)
        doc_diag.append({"code": "section_outside_body", "severity": "defect", "section": ordinals[s]})
    body_roots.sort(key=lambda e: docidx[e])

    atoms, refs = scan_source(body_roots, ordinals)
    pos = {(n, k): i for i, (n, k, _c) in enumerate(atoms)}
    by_ctx = {}
    for n, k, ctx in atoms:
        if nonws_len(n.text if k == "text" else n.tail):
            by_ctx.setdefault(ctx, []).append((n, k))

    ids = {n.get("ID") for n in root.iter() if is_el(n) and n.get("ID")}
    internals, secs = {}, []
    key_seen, match_seen = {}, {}
    for element in sections:
        ordinal = ordinals[element]
        sec, st = _build_section(element, ordinal, ordinals, tree, key_seen, match_seen)
        secs.append(sec)
        internals[ordinal] = st

    # source-side buckets for non-narrative content
    prod_by, media_by, unassigned = {}, {}, []
    for n, k, ctx in atoms:
        text = n.text if k == "text" else n.tail
        if not nonws_len(text):
            continue
        holder = n if k == "text" else n.getparent()
        tag = local(holder) if holder is not None else "<root>"
        bucket, owner = ctx
        if bucket == "product_data":
            prod_by.setdefault(owner, []).append([tag, strip_ws(text)])
        elif bucket == "media_text":
            media_by.setdefault(owner, []).append([tag, strip_ws(text)])
        elif bucket == "unassigned":
            unassigned.append({"section": owner, "tag": tag, "text": strip_ws(text),
                               "xpath": tree.getpath(holder) if holder is not None else None})
    for sec in secs:
        o = sec["ordinal"]
        sec["product_data"] = prod_by.get(o, [])
        sec["media_text"] = media_by.get(o, [])

    # references
    references, expected_markers = [], []
    for node, ctx in refs:
        kind = "MEDIA" if node.tag == _TQ("renderMultiMedia") else "FOOTNOTE"
        target = node.get("referencedObject", "") if kind == "MEDIA" else node.get("IDREF", "")
        resolved = bool(target) and target in ids
        references.append({"kind": kind, "target": target, "resolved": resolved, "section": ctx[1],
                           "bucket": ctx[0], "xpath": tree.getpath(node)})
        if ctx[0] in ("title", "narrative", "excerpt"):
            expected_markers.append((kind, target))
        if not resolved:
            d = {"code": "unresolved_ref", "severity": "defect", "kind": kind, "target": target,
                 "xpath": tree.getpath(node)}
            doc_diag.append({**d, "section": ctx[1]})
            if ctx[1] is not None:
                secs[ctx[1] - 1]["diagnostics"].append(d)

    _check_invariants(root, atoms, pos, by_ctx, internals, expected_markers, references, doc_diag, secs, body_roots)

    for sec in secs:
        _finish_section(sec, internals[sec["ordinal"]])
    for sec in secs:
        for d in sec["diagnostics"]:
            if d.get("code") != "unresolved_ref":
                doc_diag.append({**d, "section": sec["ordinal"]})
    buckets = {b: 0 for b in BUCKETS}
    for n, k, ctx in atoms:
        buckets[ctx[0]] += nonws_len(n.text if k == "text" else n.tail)
    products = _products(root, ordinals)
    apps = sorted({a["application_number"] for p in products for a in p["approvals"] if a["application_number"]})
    cats = sorted({(a["category"] or {}).get("code") or "" for p in products for a in p["approvals"]} - {""})
    cat_names = sorted({json.dumps(a["category"], sort_keys=True) for p in products for a in p["approvals"] if a["category"]})

    kinds = {local(b) for b in body_roots}
    if not secs:
        body_kind = "non_xml_body" if "nonXMLBody" in kinds else ("empty_structured_body" if kinds else "none")
    else:
        body_kind = "structured"
    status, reasons = _status(head, secs, doc_diag, body_kind)
    doc = {**head, "status": status, "status_reasons": reasons, "body_kind": body_kind, "sections": secs,
           "products": products, "application_numbers": apps,
           "marketing_categories": [json.loads(c) for c in cat_names],
           "unassigned": unassigned, "references": references, "diagnostics": doc_diag,
           "buckets": buckets, "bucket_total": sum(buckets.values()),
           "counts": {"sections": len(secs), "blocks": sum(len(s["narrative"]["blocks"]) for s in secs),
                      "tables": sum(len(s["narrative"]["tables"]) for s in secs)}}
    doc["content_sha256"] = content_hash(doc)
    return doc


def _status(head, secs, diag, body_kind):
    reasons = []
    if not secs:
        return "no_label_body", [body_kind]
    defects = sorted({d["code"] for d in diag if d.get("severity") == "defect"})
    if head.get("setid") is None or head.get("version") is None:
        defects = sorted(set(defects) | {"document_identity_missing"})
    if defects:
        return "extraction_defect", defects
    if any(s["state"] == "uncoded" for s in secs):
        return "identification_incomplete", ["uncoded_sections"]
    if any(s["state"] == "empty_in_source" for s in secs):
        return "complete_with_source_gaps", ["empty_in_source_sections"]
    return "complete", reasons


def _build_section(element, ordinal, ordinals, tree, key_seen, match_seen):
    parents = [a for a in reversed(list(element.iterancestors())) if a in ordinals]
    code_path = [(p.find("s:code", NS).get("code") if p.find("s:code", NS) is not None and
                  p.find("s:code", NS).get("code") else "<uncoded>") for p in parents]
    code_el = element.find("s:code", NS)
    attrs = dict(code_el.attrib) if code_el is not None else {}
    code = attrs.get("code") or "<uncoded>"
    id_el = element.find("s:id", NS)
    titles = element.findall("s:title", NS)
    diag = []
    title_acc = Acc()
    title = inline_text(titles[0], title_acc) if titles else None
    extra, extra_accs = [], []
    for t in titles[1:]:
        acc = Acc()
        extra.append(inline_text(t, acc))
        extra_accs.append(acc)
    if extra:
        diag.append({"code": "multiple_titles", "severity": "info", "count": len(titles)})
    title_norm = normalize(title) if title is not None else None
    number, rest = split_section_number(title_norm) if title_norm else (None, "")
    sid = (id_el.get("root") or "").lower() if id_el is not None else ""
    ext = id_el.get("extension") if id_el is not None else None
    base_key = "|".join(code_path + [code]) + "#" + (sid + ("/" + ext if ext else "") if sid else
        "t" + hashlib.sha1(_title_key(rest).encode("utf-8")).hexdigest()[:10])
    n = key_seen.get(base_key, 0)
    key_seen[base_key] = n + 1
    section_key = base_key if n == 0 else f"{base_key}~{n}"
    mbase = "|".join(code_path + [code]) + "|" + (number or "") + "|" + \
        hashlib.sha1(_title_key(rest).encode("utf-8")).hexdigest()[:10]
    m = match_seen.get(mbase, 0)
    match_seen[mbase] = m + 1
    narr = Narrative(tree)
    text_els = element.findall("s:text", NS)
    for te in text_els:
        narr.extract(te)
    excerpts = []
    for ex in element.xpath("./s:excerpt/s:highlight/s:text", namespaces=NS):
        en = Narrative(tree).extract(ex)
        excerpts.append(en)
    ex_flag = any(local(a) == "excerpt" for a in element.iterancestors())
    has_narr = any(nonws_len(b["text"]) for b in narr.blocks if b["kind"] not in DERIVED_KINDS)
    has_ex = any(nonws_len(b["text"]) for en in excerpts for b in en.blocks if b["kind"] not in DERIVED_KINDS)
    container = bool(element.xpath("./s:component/s:section", namespaces=NS))
    data = bool(element.xpath("./s:subject/s:manufacturedProduct", namespaces=NS))
    media = bool(element.xpath("./s:component/s:observationMedia", namespaces=NS)) or \
        bool(element.xpath(".//s:renderMultiMedia", namespaces=NS))
    coded = bool(attrs.get("code")) and attrs.get("codeSystem") == LOINC
    if not text_els:
        diag.append({"code": "no_text_element", "severity": "info"})
    elif not has_narr:
        diag.append({"code": "empty_text_element", "severity": "info"})
    for d in narr.diagnostics:
        diag.append({**d, "severity": "defect" if d["code"] in DEFECT_CODES else "info"})
    for i, en in enumerate(excerpts, 1):
        for d in en.diagnostics:
            diag.append({**d, "scope": "excerpt", "excerpt_ordinal": i,
                         "severity": "defect" if d["code"] in DEFECT_CODES else "info"})
    if has_narr or has_ex:
        state = "ok" if coded else "uncoded"
    elif container or data or media or code == "48780-1":
        state = "ok"
    else:
        state = "empty_in_source"
    if not has_narr and has_ex:
        kind = "excerpt_only" if not ex_flag else "excerpt"
    else:
        kind = "excerpt" if ex_flag else "full"
    sec = {
        "ordinal": ordinal, "parent_ordinal": ordinals[parents[-1]] if parents else None,
        "depth": len(parents), "code_path": code_path,
        "section_id": {"root": id_el.get("root"), "extension": ext} if id_el is not None else None,
        "code": code, "code_system": attrs.get("codeSystem"), "display_name": attrs.get("displayName"),
        "code_state": "coded" if coded else "uncoded",
        "title": title, "title_normalized": title_norm, "extra_titles": extra,
        "section_number": number, "section_key": section_key, "match_key": mbase if m == 0 else f"{mbase}~{m}",
        "registry_hint": registry_hint(rest, code) if title_norm else None,
        "kind": kind, "state": state,
        "source_xpath": tree.getpath(element), "source_line": element.sourceline,
        "narrative_xml_sha256": [xml_sha(t) for t in text_els] if text_els else None,
        "narrative": {"own_text": narr.own_text(), "blocks": narr.blocks, "tables": narr.tables},
        "excerpts": [{"own_text": en.own_text(), "blocks": en.blocks, "tables": en.tables,
                      "source_xpath": tree.getpath(en.texts[0])} for en in excerpts],
        "diagnostics": diag,
    }
    st = {"title_acc": title_acc, "extra_accs": extra_accs, "narr": narr, "excerpts": excerpts,
          "text_els": text_els}
    return sec, st


def _finish_section(sec, st):
    for part in [sec["narrative"]] + sec["excerpts"]:
        for b in part["blocks"]:
            b.pop("_atoms", None)
            b.pop("_markers", None)


# --------------------------------------------------------------------------- invariants

def _counter(atoms):
    c = {}
    for a in atoms:
        c[a] = c.get(a, 0) + 1
    return c


def _check_invariants(root, atoms, pos, by_ctx, internals, expected_markers, references, doc_diag,
                      secs, body_roots):
    # --- conservation: independent source total vs. bucket totals
    source_total = sum(nonws_len(str(t)) for r in body_roots for t in r.xpath(".//text()"))
    scan_total = sum(nonws_len(_atom_text((n, k))) for n, k, _c in atoms)
    if source_total != scan_total:
        _fail(f"conservation: source text {source_total} != classified text {scan_total}")
    bucket_sum = {}
    for n, k, ctx in atoms:
        bucket_sum[ctx[0]] = bucket_sum.get(ctx[0], 0) + nonws_len(_atom_text((n, k)))
    if sum(bucket_sum.values()) != source_total:
        _fail("conservation: bucket totals do not sum to source total")

    # --- conservation: extracted atoms == classified atoms, exactly once, per (bucket, section)
    registered = {}
    markers_found, all_registered = [], []

    def reg(bucket, ordinal, atom_lists):
        got = registered.setdefault((bucket, ordinal), [])
        for al in atom_lists:
            got.extend(_nw_atoms(al))

    for ordinal, st in internals.items():
        reg("title", ordinal, [st["title_acc"].atoms] + [a.atoms for a in st["extra_accs"]])
        markers_found.extend(m for a in [st["title_acc"]] + st["extra_accs"] for m in a.markers)
        for b in st["narr"].blocks:
            _check_block(b, f"section {ordinal} narrative")
            reg("narrative", ordinal, [b["_atoms"]])
            markers_found.extend(b["_markers"])
        for i, en in enumerate(st["excerpts"], 1):
            for b in en.blocks:
                _check_block(b, f"section {ordinal} excerpt {i}")
                reg("excerpt", ordinal, [b["_atoms"]])
                markers_found.extend(b["_markers"])
    for key in ("title", "narrative", "excerpt"):
        keys = {k for k in registered if k[0] == key} | {k for k in by_ctx if k[0] == key}
        for k in sorted(keys, key=lambda x: (x[1] or 0)):
            got, want = _counter(registered.get(k, [])), _counter(by_ctx.get(k, []))
            dup = [a for a, c in got.items() if c > 1]
            if dup:
                _fail(f"text assigned more than once in {k}: {len(dup)} atoms, e.g. {_atom_text(dup[0])[:60]!r}")
            if got != want:
                missing = [a for a in want if a not in got]
                extra = [a for a in got if a not in want]
                bad = missing[0] if missing else extra[0]
                _fail(f"conservation: {k} extracted/classified atoms differ "
                      f"(missing {len(missing)}, extra {len(extra)}), e.g. {_atom_text(bad)[:60]!r}")
        all_registered.extend(a for k, v in registered.items() if k[0] == key for a in v)
    seen = _counter(all_registered)
    if any(c > 1 for c in seen.values()):
        _fail("conservation: a text node was assigned to more than one bucket")

    # --- no narrative element assigned twice
    used = [id(t) for st in internals.values() for t in st["text_els"]]
    used += [id(t) for st in internals.values() for en in st["excerpts"] for t in en.texts]
    if len(used) != len(set(used)):
        _fail("section narrative element assigned twice")

    # --- order
    ranges = []
    for ordinal, st in sorted(internals.items()):
        check_order(st["narr"], pos, f"section {ordinal} narrative")
        for i, en in enumerate(st["excerpts"], 1):
            check_order(en, pos, f"section {ordinal} excerpt {i}")
        idx = [pos[a] for b in st["narr"].blocks if b["kind"] != "table_flat" for a in _nw_atoms(b["_atoms"])]
        if idx:
            ranges.append((min(idx), max(idx)))
    _check_sequence(ranges, "section narrative sequence")

    # --- references
    real = sorted((k, t) for k, t, _m in markers_found if k in ("MEDIA", "FOOTNOTE"))
    if real != sorted(expected_markers):
        _fail("references: emitted [MEDIA]/[FOOTNOTE] markers do not match source reference nodes")
    diag_paths = {d.get("xpath") for d in doc_diag if d.get("code") == "unresolved_ref"}
    for r in references:
        if not r["resolved"] and r["xpath"] not in diag_paths:
            _fail("references: unresolved reference without diagnostic")


def _check_block(b, what):
    if b["kind"] in DERIVED_KINDS:
        return
    want = sum(nonws_len(_atom_text(a)) for a in b["_atoms"]) + sum(nonws_len(m[2]) for m in b["_markers"])
    if nonws_len(b["text"]) != want:
        _fail(f"conservation: {what} block text has {nonws_len(b['text'])} non-whitespace chars "
              f"but consumed atoms+markers account for {want}")
