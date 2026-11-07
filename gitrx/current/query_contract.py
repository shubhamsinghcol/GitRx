"""Inventory-bounded negatives and visible, unverified styled-warning evidence."""
import re
from .archive import NS,LOINC,read_xml
from .normalize import normalize
from .parser import inline_text

def possible_warnings(rows):
    by={s['ordinal']:s for s in rows};out=[]
    for s in rows:
        a=s;coded=False
        while a:
            if a['loinc_code']=='34066-1' and a['code_system']==LOINC:coded=True;break
            a=by.get(a['parent_ordinal'])
        if coded:continue
        node=read_xml(s['source_xml'].encode());text=node.find('s:text',NS)
        if text is None:continue
        for styled in text.xpath('.//s:content[@styleCode]',namespaces=NS):
            value=normalize(inline_text(styled));style=styled.get('styleCode','').lower()
            if not ('bold' in style or 'italic' in style) or len(value)>240:continue
            if not normalize(inline_text(text)).startswith(value):continue
            if re.match(r'^(?:BOXED\s+)?WARNING\s*:',value,re.I):
                out.append({'instance_index':s['ordinal'],'ancestry_path':s['ancestry_path'],
                    'first_200_characters':s['own_text'][:200],
                    'status':'possible, unverified','reason':'Leading styled warning with no published boxed-warning code in its ancestor chain; not headline evidence'})
            break
    return out

def inventory_absence(rows,codes,parse_status,possible):
    if set(codes or ())!={'34066-1'} or not rows or parse_status=='extraction_failed' or possible:return None
    if any(s['loinc_code'] in codes or s['canonical_code'] in codes for s in rows):return None
    return {'state':'checked-absent','basis':'Complete native section inventory contains no requested source or canonical code',
        'requested_codes':list(codes),'scope':'coded-section inventory only',
        'uncoded_clinical_warning_absence_established':False}

def headline_instances(first,key):
    if not first:return []
    return [{'instance_index':i['instance_index'],'first_200_characters':i['first_200_characters'],
             'ancestry_path':i['ancestry_path'],'representation':i['representation']}
            for i in first[key] if key=='instances' or i.get('matching_hits',0)>0]
