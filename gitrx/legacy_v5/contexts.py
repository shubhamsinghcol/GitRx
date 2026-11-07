"""Ancestry/document-part context overlay. LOINC codes are never inputs."""
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

from .archive import NS,artifact_bytes,read_xml
from .db import dumps
from .normalize import normalize
from .parser import inline_text,local

CONTEXTS=('full-label-body','highlights','medication-guide','patient-information','package-label','other')
SCHEMA='''
CREATE TABLE IF NOT EXISTS context_policies(id TEXT PRIMARY KEY,policy_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS section_contexts(
 policy_id TEXT NOT NULL REFERENCES context_policies(id),
 section_id INTEGER NOT NULL REFERENCES sections(id),
 section_context TEXT NOT NULL,enclosing_context TEXT NOT NULL,
 source_xpath TEXT NOT NULL,raw_ancestry_path TEXT NOT NULL,
 evidence_json TEXT NOT NULL,PRIMARY KEY(policy_id,section_id));
CREATE TABLE IF NOT EXISTS context_confidences(
 policy_id TEXT NOT NULL,section_id INTEGER NOT NULL,
 context_confidence TEXT NOT NULL CHECK(context_confidence IN ('anchored','default','unknown')),
 PRIMARY KEY(policy_id,section_id),
 FOREIGN KEY(policy_id,section_id) REFERENCES section_contexts(policy_id,section_id));
'''


def boundary(node):
    title=node.find('s:title',NS)
    title_text=normalize(inline_text(title)) if title is not None else ''
    if title_text.lower()=='click here to enter text.':title_text=''
    text=node.find('s:text',NS)
    first_nodes=text.xpath('.//*[self::s:paragraph or self::s:caption or self::s:td or self::s:th][not(.//s:paragraph or .//s:td or .//s:th)]',namespaces=NS)[:12] if text is not None else []
    leads=[normalize(re.sub(r'\[(?:MEDIA|FOOTNOTE):[^\]]*\]',' ',inline_text(p)))[:240] for p in first_nodes]
    if text is not None:
        leads.insert(0,normalize(re.sub(r'\[(?:MEDIA|FOOTNOTE):[^\]]*\]',' ',inline_text(text)))[:240])
    if text is not None and normalize(text.text or ''):leads.insert(0,normalize(text.text)[:240])
    # Do not let prose referring to a guide override a numbered PI section.
    candidates=[('title',re.sub(r'^\d+(?:\.\d+)*\s+','',title_text))]
    if not re.match(r'^\d+(?:\.\d+)*\s',title_text):candidates.extend(('leading-heading',lead) for lead in leads)
    matches=[]
    for kind,value in candidates:
        upper=re.sub(r'^-{3,}\s*','',value.upper())
        if re.match(r'^MEDICATION GUIDE\s+(?:FOR PATIENT\s+)?(?:ENCLOSED|INCLUDED)\b',upper):context='package-label'
        elif re.match(r'^(?:RX ONLY\s+)?(?:FDA[- ]APPROVED\s+)?MEDICATION\s+GUIDE\b',upper):context='medication-guide'
        elif (re.match(r'^(?:PACKAGE[/ ]LABEL\s+)?PRINCIPAL\s+DISPLAY\s+PANEL\b|^PACKAGE(?:[/ ]LABEL| CARTON| BOTTLE| BLISTER| VIAL)\b',upper)
          or (not title_text and (re.match(r'^NDC\s+\d|^REPRESENTATIVE PACKAGING\b',upper)
            or re.search(r'ATTENTION PHARMACIST:\s*DISPENSE\b',upper)
            or ('RX ONLY' in upper and 'DISPENSE WITH MEDICATION GUIDE' in upper)))
          or (len(upper)<=160 and re.search(r'\b(?:LABEL|CARTON)$',upper))):context='package-label'
        elif re.match(r'^(?:U-\d+\s+)?(?:FDA[- ]APPROVED\s+)?PATIENT\s+(?:INFORMATION|PACKAGE INSERT|LEAFLET|INSTRUCTIONS)\b|^FDA[- ]APPROVED PATIENT LABELING\b|^INFORMATION FOR (?:THE )?PATIENTS?\b|^INSTRUCTIONS FOR USE\b|^INSTRUCTION LEAFLET\b|^USER MANUAL\b|^DISPOSABLE INSULIN DELIVERY DEVICE\b',upper):context='patient-information'
        else:continue
        matches.append({'context':context,'rule':'document-part-'+kind,'heading':value,
                        'anchor_xpath':node.getroottree().getpath(node)})
    if matches:
        first=matches[0]
        return first['context'],{k:v for k,v in first.items() if k!='context'}|{
          'boundary_candidates':matches,'conflicting_contexts':sorted({m['context'] for m in matches}) if len({m['context'] for m in matches})>1 else []}
    return None,None


def classify_document(doc):
    nodes=doc.xpath('.//s:section',namespaces=NS);ordinals={n:i for i,n in enumerate(nodes,1)};out=[];by_node={}
    for node in nodes:
        ancestors=[a for a in node.iterancestors() if a in ordinals]
        parent=next(iter(ancestors),None)
        inherited=by_node[parent] if parent is not None else None
        own,evidence=boundary(node)
        own_evidence=evidence
        if inherited and inherited['enclosing_context'] not in ('full-label-body','other'):
            part=inherited['enclosing_context'];evidence={**inherited['evidence'],'inherited_from':ordinals[parent]}
        elif own:part=own
        elif inherited:
            part=inherited['enclosing_context'];evidence={**inherited['evidence'],'inherited_from':ordinals[parent]}
        elif node.find('s:subject',NS) is not None and node.find('s:text',NS) is None and not node.xpath('./s:component/s:section',namespaces=NS):
            part='other';evidence={'rule':'structured-product-data-container','anchor_xpath':doc.getroottree().getpath(node)}
        elif node.find('s:text',NS) is not None and node.xpath('./s:text//s:renderMultiMedia',namespaces=NS) and not ''.join(node.find('s:text',NS).itertext()).strip():
            part='other';evidence={'rule':'unnamed-media-only','anchor_xpath':doc.getroottree().getpath(node)}
        elif any(local(a)=='structuredBody' for a in node.iterancestors()):
            part='full-label-body';evidence={'rule':'structuredBody-default','anchor_xpath':doc.getroottree().getpath(node)}
        else:part='other';evidence={'rule':'outside-structuredBody','anchor_xpath':doc.getroottree().getpath(node)}
        high=any(local(a) in ('excerpt','highlight') for a in node.iterancestors())
        confidence='anchored' if evidence.get('heading') else 'default'
        conflicts=list(evidence.get('conflicting_contexts',[]))
        if inherited and inherited['context_confidence']=='unknown':conflicts.append('conflicting ancestor evidence')
        if own and inherited and inherited['enclosing_context'] not in ('full-label-body','other',own):
            conflicts.extend([inherited['enclosing_context'],own])
        if own and evidence is not None:
            conflicts.extend((own_evidence or {}).get('conflicting_contexts',[]))
        if conflicts:
            confidence='unknown';evidence={**evidence,'conflicts':sorted(set(conflicts)),
                'own_boundary_evidence':own_evidence}
        evidence={**evidence,'anchor_text':evidence.get('heading')}
        if high:
            confidence='unknown' if confidence=='unknown' else 'anchored'
            evidence={**evidence,'part_anchor':evidence,'anchor_element':'highlight/excerpt',
              'anchor_text':normalize(inline_text(node))[:200],
              'anchor_xpath':doc.getroottree().getpath(next(a for a in node.iterancestors() if local(a) in ('excerpt','highlight')))}
        item={'ordinal':ordinals[node],'section_context':'highlights' if high else part,'enclosing_context':part,
              'context_confidence':confidence,
              'source_xpath':doc.getroottree().getpath(node),
              'raw_ancestry_path':'/'+('/'.join(str(ordinals[a]) for a in reversed(ancestors))),
              'evidence':evidence}
        by_node[node]=item;out.append(item)
    return out


def context_rows(conn,policy):
    return [dict(r) for r in conn.execute('''SELECT s.*,c.context_confidence FROM section_contexts s
      JOIN context_confidences c USING(policy_id,section_id) WHERE s.policy_id=? ORDER BY s.section_id''',(policy,))]


def apply_contexts(conn,root):
    root=Path(root);conn.executescript(SCHEMA)
    policy={'version':'xml-document-parts-2-confidence','classifier_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            'code_input':False,'contexts':list(CONTEXTS),'source':'original ZIP XML, heading/document-part ancestry'}
    raw=dumps(policy);pid=hashlib.sha256(raw.encode()).hexdigest()
    conn.execute('INSERT OR IGNORE INTO context_policies VALUES(?,?)',(pid,raw))
    checked=0
    for v in conn.execute("SELECT v.*,p.id parse_id FROM versions v JOIN parse_runs p ON p.version_id=v.id WHERE v.state='downloaded' ORDER BY v.id,p.id").fetchall():
        blob=artifact_bytes(conn,root,v['selected_sha256'])
        if hashlib.sha256(blob).hexdigest()!=v['selected_sha256']:raise ValueError('Raw artifact hash changed')
        with zipfile.ZipFile(io.BytesIO(blob)) as z:xml=z.read(v['xml_member'])
        doc=read_xml(xml)
        xml_hash=hashlib.sha256(xml).hexdigest()
        rows=classify_document(doc)
        stored=conn.execute('SELECT id,ordinal,source_xpath,ancestry_path FROM sections WHERE parse_run_id=? ORDER BY ordinal',(v['parse_id'],)).fetchall()
        if len(rows)!=len(stored):raise ValueError('Context/source section inventory differs')
        for s,r in zip(stored,rows):
            if (s['ordinal'],s['source_xpath'],s['ancestry_path'])!=(r['ordinal'],r['source_xpath'],r['raw_ancestry_path']):raise ValueError('Context/source section identity differs')
            evidence={**r['evidence'],'raw_sha256':v['selected_sha256'],'xml_sha256':xml_hash}
            entry=(pid,s['id'],r['section_context'],r['enclosing_context'],r['source_xpath'],r['raw_ancestry_path'],dumps(evidence))
            old=conn.execute('SELECT * FROM section_contexts WHERE policy_id=? AND section_id=?',(pid,s['id'])).fetchone()
            if old and tuple(old)!=entry:raise ValueError('Context overlay conflict; never overwrite')
            conn.execute('INSERT OR IGNORE INTO section_contexts VALUES(?,?,?,?,?,?,?)',entry)
            conn.execute('INSERT OR IGNORE INTO context_confidences VALUES(?,?,?)',(pid,s['id'],r['context_confidence']))
        checked+=1
    conn.commit();(root/'reports/CONTEXT-POLICY-ID.txt').write_text(pid+'\n')
    return pid,checked


def attach_contexts(conn,rows,policy):
    overlay={}
    for offset in range(0,len(rows),500):
        ids=[s['id'] for s in rows[offset:offset+500]]
        overlay.update({r['section_id']:r for r in conn.execute(
          'SELECT s.*,c.context_confidence FROM section_contexts s JOIN context_confidences c USING(policy_id,section_id) WHERE s.policy_id=? AND s.section_id IN ('+','.join('?' for _ in ids)+')',[policy,*ids])})
    for s in rows:
        if s['id'] not in overlay:raise ValueError('Pinned section context missing')
        r=overlay[s['id']]
        if r['source_xpath']!=s['source_xpath'] or r['raw_ancestry_path']!=s['ancestry_path']:raise ValueError('Context section identity mismatch')
        s['section_context']=r['section_context'];s['enclosing_context']=r['enclosing_context'];s['context_rule']=json.loads(r['evidence_json'])
        s['context_confidence']=r['context_confidence']
    return rows


def product_context(s):
    return product_part(s) and s['context_confidence']=='anchored'


def product_part(s):
    return s['section_context'] in ('full-label-body','highlights') and s['enclosing_context']=='full-label-body'


def instance_info(s,version,excerpt=None):
    rule=s['context_rule']
    confidence=s['context_confidence']
    if excerpt is not None:
        confidence='unknown' if confidence=='unknown' else 'anchored'
        rule={'rule':'source-excerpt-highlights','anchor_element':'excerpt/highlight',
              'anchor_xpath':excerpt['source_xpath'],'anchor_text':excerpt['extracted_text'][:200],
              'enclosing_part_rule':rule}
    return {'version':version,'instance_index':s['ordinal'],'section_context':'highlights' if excerpt is not None else s['section_context'],
      'context_confidence':confidence,'enclosing_context_confidence':s['context_confidence'],
      'enclosing_context':s['enclosing_context'],'ancestry_path':s['ancestry_path'],
      'source_xpath':excerpt['source_xpath'] if excerpt is not None else s['source_xpath'],
      'first_200_characters':(excerpt['extracted_text'] if excerpt is not None else s['own_text'])[:200],
      'context_rule':rule,'representation':'excerpt' if excerpt is not None else 'section'}
