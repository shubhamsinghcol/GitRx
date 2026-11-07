"""Approved development-only body evidence. No held-out or E1g rule inputs."""
import hashlib
from pathlib import Path
import re
from .archive import NS,LOINC
from .contexts import classify_document as base_classify
from .normalize import normalize
from .parser import inline_text,local

VERSION='body-evidence-1.0.0'
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


def apply_contexts(conn,root,version_ids=None):
    """Append one immutable composite policy; authored content is never updated."""
    import io,json,zipfile
    from .archive import artifact_bytes
    from .contexts import SCHEMA,CONTEXTS
    from .db import dumps
    root=Path(root);conn.executescript(SCHEMA)
    base_sha=hashlib.sha256(Path(__file__).with_name('contexts.py').read_bytes()).hexdigest()
    policy={'version':VERSION,'classifier_sha256':base_sha,
        'body_evidence_source_sha256':source_hash(),'contexts':list(CONTEXTS),
        'clinical_section_codes_as_body_anchors':False,
        'media_package_requires_code_and_media_only_structure':True,
        'inferred_by_exclusion_headline_eligible':False}
    raw=dumps(policy);pid=hashlib.sha256(raw.encode()).hexdigest()
    conn.execute('INSERT OR IGNORE INTO context_policies VALUES(?,?)',(pid,raw))
    count=0
    versions=conn.execute("SELECT v.*,p.id parse_id FROM versions v JOIN parse_runs p ON p.version_id=v.id WHERE v.state='downloaded' ORDER BY v.id").fetchall()
    for v in versions:
        if version_ids is not None and v['id'] not in version_ids:continue
        blob=artifact_bytes(conn,root,v['selected_sha256'])
        if hashlib.sha256(blob).hexdigest()!=v['selected_sha256']:raise RuntimeError('STOP_RAW_HASH')
        with zipfile.ZipFile(io.BytesIO(blob)) as z:xml=z.read(v['xml_member'])
        from .archive import read_xml
        doc=read_xml(xml)
        if doc.find('s:setId',NS).get('root').lower()!=v['setid'] or int(doc.find('s:versionNumber',NS).get('value'))!=v['version']:raise RuntimeError('STOP_IDENTITY')
        rows=classify_document(doc)
        stored=conn.execute('SELECT id,ordinal,source_xpath,ancestry_path FROM sections WHERE parse_run_id=? ORDER BY ordinal',(v['parse_id'],)).fetchall()
        if len(stored)!=len(rows):raise RuntimeError('STOP_SECTION_IDENTITY')
        for s,r in zip(stored,rows):
            if (s['ordinal'],s['source_xpath'],s['ancestry_path'])!=(r['ordinal'],r['source_xpath'],r['raw_ancestry_path']):raise RuntimeError('STOP_SECTION_IDENTITY')
            evidence={**r['evidence'],'raw_sha256':v['selected_sha256'],'xml_sha256':hashlib.sha256(xml).hexdigest()}
            entry=(pid,s['id'],r['section_context'],r['enclosing_context'],r['source_xpath'],r['raw_ancestry_path'],dumps(evidence))
            old=conn.execute('SELECT * FROM section_contexts WHERE policy_id=? AND section_id=?',(pid,s['id'])).fetchone()
            if old and tuple(old)!=entry:raise RuntimeError('STOP_DERIVED_POLICY_CONFLICT')
            conn.execute('INSERT OR IGNORE INTO section_contexts VALUES(?,?,?,?,?,?,?)',entry)
            conn.execute('INSERT OR IGNORE INTO context_confidences VALUES(?,?,?)',(pid,s['id'],r['context_confidence']))
        count+=1
    conn.commit();(root/'reports').mkdir(exist_ok=True)
    (root/'reports/CONTEXT-POLICY-ID.txt').write_text(pid+'\n')
    return pid,count
