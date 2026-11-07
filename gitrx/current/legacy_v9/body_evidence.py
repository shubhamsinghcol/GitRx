"""Unadopted development-evidence candidate. No held-out/E1g rule inputs."""
import hashlib
from pathlib import Path
import re
from .archive import NS,LOINC
from .contexts import classify_document as base_classify
from .normalize import normalize
from .parser import inline_text,local

VERSION='body-evidence-dev-candidate-1'
# Dev case headings and their displayed ancestors, not clinical section codes.
HEADINGS={'INDICATIONS AND USAGE','DOSAGE AND ADMINISTRATION','CONTRAINDICATIONS',
 'WARNINGS AND PRECAUTIONS','WARNINGS','PRECAUTIONS','ADVERSE REACTIONS',
 'CLINICAL PHARMACOLOGY','PHARMACODYNAMICS','PHARMACOKINETICS'}

def heading(value):
    return re.sub(r'^\d+(?:\.\d+)*\s+','',normalize(value)).strip(' :').upper()

def positive_body(node):
    if not any(local(a)=='structuredBody' for a in node.iterancestors()):return None
    title=node.find('s:title',NS);text=node.find('s:text',NS)
    title_text=normalize(inline_text(title)) if title is not None else ''
    if heading(title_text) in HEADINGS or re.match(r'^WARNING\s*:',title_text,re.I):
        return {'rule':'positive-pi-title','heading':title_text,'anchor_text':title_text,
                'anchor_xpath':node.getroottree().getpath(title),'evidence_tier':'anchored'}
    if title_text or text is None:return None
    for styled in text.xpath('.//s:content[@styleCode]',namespaces=NS):
        value=normalize(inline_text(styled));style=styled.get('styleCode','').lower()
        if len(value)>240 or not ('italic' in style or 'bold' in style):continue
        if not normalize(inline_text(text)).startswith(value):continue
        if heading(value) in HEADINGS or re.match(r'^WARNING\s*:',value,re.I):
            return {'rule':'positive-leading-styled-pi-heading','heading':value,
                'anchor_text':value,'anchor_xpath':node.getroottree().getpath(styled),
                'evidence_tier':'anchored','clinical_section_identity_inferred':False}
        break
    return None

def positive_media_package(node):
    code=node.find('s:code',NS);text=node.find('s:text',NS)
    media=node.xpath('./s:text//s:renderMultiMedia',namespaces=NS)
    if code is None or code.get('code')!='51945-4' or code.get('codeSystem')!=LOINC:return None
    if text is None or not media or ''.join(text.itertext()).strip():return None
    return {'rule':'published-package-code-with-media','anchor_element':'renderMultiMedia',
        'anchor_xpath':node.getroottree().getpath(media[0]),'anchor_text':None,
        'published_document_part_code':'51945-4','evidence_tier':'anchored','OCR':False}

def classify_document(doc):
    rows=base_classify(doc);nodes=doc.xpath('.//s:section',namespaces=NS)
    by_node={};ordinal={n:i for i,n in enumerate(nodes,1)}
    for node,row in zip(nodes,rows):
        row={**row,'evidence':dict(row['evidence'])}
        parent=next((p for p in node.iterancestors() if p in by_node),None)
        inherited=by_node.get(parent)
        if row['context_confidence']=='unknown':
            row['evidence']['evidence_tier']='unknown'
        elif row['section_context']=='highlights':
            row['evidence']['evidence_tier']=row['context_confidence']
        elif row['enclosing_context'] not in ('full-label-body','other'):
            row['evidence']['evidence_tier']=row['context_confidence']
        elif positive_media_package(node):
            row.update(section_context='package-label',enclosing_context='package-label',
                       context_confidence='anchored',evidence=positive_media_package(node))
        elif row['enclosing_context']=='full-label-body':
            evidence=positive_body(node)
            if evidence:
                row.update(context_confidence='anchored',evidence=evidence)
            elif inherited and inherited['enclosing_context']=='full-label-body' and inherited['context_confidence']=='anchored':
                row.update(context_confidence='anchored',evidence={**inherited['evidence'],'inherited_from':ordinal[parent]})
            else:
                row.update(context_confidence='default',evidence={**row['evidence'],
                    'rule':'body-inferred-by-exclusion','evidence_tier':'inferred-by-exclusion',
                    'positive_body_anchor':False,'headline_eligible':False})
        else:row['evidence']['evidence_tier']=row['context_confidence']
        by_node[node]=row
    return list(by_node.values())

def source_hash():return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
