"""Visible structural location rules; no clinical source-code correction."""
import re

from .archive import NS, read_xml
from .normalize import normalize
from .parser import Narrative, inline_text, local

LOCATIONS=('section-title','inline-heading','body','table-cell','table-header','footnote','highlights')


def resolve(section, source_xpath):
    root=read_xml(section['source_xml'].encode())
    prefix=section['source_xpath']
    if not source_xpath.startswith(prefix):
        raise ValueError('Block XPath is outside its section instance')
    suffix=source_xpath[len(prefix):]
    nodes=root.xpath('.'+suffix,namespaces=NS) if suffix else [root]
    if len(nodes)!=1:raise ValueError('Cannot resolve a unique source block location')
    return nodes[0]


def structural_role(node):
    chain=[node,*node.iterancestors()]
    if any(local(n)=='footnote' for n in chain):return 'footnote','source-footnote'
    cell=next((n for n in chain if local(n) in ('th','td')),None)
    if cell is not None:
        header=local(cell)=='th' or any(local(n)=='thead' for n in cell.iterancestors())
        return ('table-header','source-th-or-thead') if header else ('table-cell','source-td')
    if local(node)=='title':return 'section-title','source-title'
    if local(node)=='caption':return 'inline-heading','source-caption'
    text=normalize(inline_text(node))
    letters=''.join(c for c in text if c.isalpha())
    # Require every non-whitespace text run to have a bold ancestor. Mixed
    # bold/prose paragraphs must remain body; never infer a warning clinically.
    runs=node.xpath('.//text()')
    def bold(run):
        parent=run.getparent()
        if run.is_tail:parent=parent.getparent()
        return parent is not None and any('bold' in n.get('styleCode','').lower().split()
            for n in [parent,*parent.iterancestors()] if n is node or node in n.iterancestors())
    wholly_bold=bool(text) and all(bold(r) for r in runs if str(r).strip())
    heading_style=any(re.search(r'heading|header',n.get('styleCode',''),re.I) for n in [node])
    if local(node)=='paragraph' and ((wholly_bold and letters and letters.isupper()) or heading_style):
        return 'inline-heading','standalone-bold-uppercase-paragraph' if wholly_bold else 'explicit-heading-style'
    return 'body','source-prose-default'


def cell_pieces(xml):
    node=read_xml(xml.encode())
    tree=node.getroottree()
    extracted=Narrative(tree).extract(node)
    for b in extracted.blocks:
        if b['kind'] in ('table_cell','table_flat'):continue
        targets=tree.xpath(b['source_xpath'],namespaces=NS)
        role,rule=structural_role(targets[0])
        yield b['text'],role,rule,b['source_xpath']


def classify(section,candidate):
    if candidate['kind']=='heading':role,rule='section-title','source-title'
    elif candidate.get('structural_location'):
        role,rule=candidate['structural_location'],candidate['location_rule']
    else:role,rule=structural_role(resolve(section,candidate['source_xpath']))
    return ('highlights' if candidate['scope']=='excerpt' else role),role,rule
