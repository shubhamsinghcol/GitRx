"""Snapshot-pinned literal phrase history. GitRx Step D, no medical inference."""
import argparse
from datetime import datetime
from collections import Counter
import hashlib
import html
import json
from pathlib import Path
import re
import sys
import unicodedata

from .archive import now, put_bytes, require_evidence_references
from .db import connect, dumps
from .normalize import normalize
from .parser import Narrative, CONFIG as PARSER_CONFIG
from .archive import read_xml
from .locations import LOCATIONS, cell_pieces, classify
from .contexts import attach_contexts,context_rows,instance_info,product_context,product_part
from .availability import family_statuses,route_failure,state_text
from .presentation import readable,family_title,status_text,retrieval_text,coverage_text
from .query_contract import possible_warnings,inventory_absence,headline_instances

ENGINE_VERSION='phrase-history-0.6.0'
ALIASES={'boxed-warning':['34066-1'],'indications':['34067-9'],'dosage':['34068-7'],
 'contraindications':['34070-3'],'warnings':['34071-1'],'warnings-and-precautions':['43685-7'],
 'precautions':['42232-9'],'adverse-reactions':['34084-4'],'drug-interactions':['34073-7'],
 'medication-guide':['42231-1']}


def identity_rows(conn,policy):
    return [dict(r) for r in conn.execute('SELECT * FROM section_identities WHERE policy_id=? ORDER BY section_id',(policy,))]


def payload_digest(conn,parse_id):
    """Checksum query inputs, not just their inventory; no storage redesign."""
    digest=hashlib.sha256()
    selections=[('sections','''SELECT id,ordinal,parent_ordinal,ancestry_path,title,kind,status,loinc_code,
      code_system,source_xpath,diagnostics_json,own_text,source_xml FROM sections WHERE parse_run_id=? ORDER BY ordinal'''),
      ('blocks','''SELECT b.id,b.section_id,b.ordinal,b.kind,b.text,b.scope,b.source_xpath,b.table_ordinal,b.row_ordinal,b.cell_ordinal
      FROM narrative_blocks b JOIN sections s ON s.id=b.section_id WHERE s.parse_run_id=? ORDER BY s.ordinal,b.ordinal'''),
      ('tables','''SELECT t.id,t.section_id,t.ordinal,t.scope,t.flattened_text,t.structured_json FROM narrative_tables t
      JOIN sections s ON s.id=t.section_id WHERE s.parse_run_id=? ORDER BY s.ordinal,t.ordinal'''),
      ('excerpts','''SELECT e.* FROM section_excerpts e JOIN sections s ON s.id=e.section_id
      WHERE s.parse_run_id=? ORDER BY s.ordinal,e.ordinal''')]
    for tag,sql in selections:
        digest.update(tag.encode()+b'\n')
        for row in conn.execute(sql,(parse_id,)):
            digest.update(dumps(list(row)).encode());digest.update(b'\n')
    return digest.hexdigest()


def cell_segments(xml):
    """Keep paragraph/list-item/footnote boundaries inside a single table cell."""
    node=read_xml(xml.encode())
    extracted=Narrative(node.getroottree()).extract(node)
    return [b['text'] for b in extracted.blocks if b['kind'] not in ('table_cell','table_flat')]


def freeze_query_snapshot(conn,root,parent_snapshot=None):
    root=Path(root)
    archive=(root/'reports/SNAPSHOT-ID.txt').read_text().strip()
    base=conn.execute('SELECT manifest_json FROM snapshots WHERE id=?',(archive,)).fetchone()
    if not base or hashlib.sha256(base[0].encode()).hexdigest()!=archive:raise ValueError('Archive manifest integrity failure')
    policy=(root/'reports/IDENTITY-POLICY-ID.txt').read_text().strip()
    p=conn.execute('SELECT policy_json FROM identity_policies WHERE id=?',(policy,)).fetchone()
    if not p or hashlib.sha256(p[0].encode()).hexdigest()!=policy:raise ValueError('Identity policy integrity failure')
    rows=dumps(identity_rows(conn,policy)).encode();ids_hash=put_bytes(conn,root,rows,artifact_class='derived',purpose='section identity snapshot',provenance={'identity_policy':policy})
    code_hash=put_bytes(conn,root,Path(__file__).read_bytes())
    identity_code=put_bytes(conn,root,Path(__file__).with_name('identity.py').read_bytes())
    locations_hash=put_bytes(conn,root,Path(__file__).with_name('locations.py').read_bytes())
    section_history_hash=put_bytes(conn,root,Path(__file__).with_name('section_history.py').read_bytes())
    context_source_hash=put_bytes(conn,root,Path(__file__).with_name('contexts.py').read_bytes())
    context_policy=(root/'reports/CONTEXT-POLICY-ID.txt').read_text().strip()
    context_raw=conn.execute('SELECT policy_json FROM context_policies WHERE id=?',(context_policy,)).fetchone()
    if not context_raw or hashlib.sha256(context_raw[0].encode()).hexdigest()!=context_policy:raise ValueError('Context policy integrity failure')
    if json.loads(context_raw[0])['classifier_sha256']!=context_source_hash:raise ValueError('Context overlay uses a different classifier; recompute explicitly')
    context_hash=hashlib.sha256(dumps(context_rows(conn,context_policy)).encode()).hexdigest()
    normalizer_hash=put_bytes(conn,root,Path(__file__).with_name('normalize.py').read_bytes())
    archive_records=json.loads(base[0])['versions']
    require_evidence_references(conn,archive_records)
    inherited={}
    if parent_snapshot:
        previous=conn.execute('SELECT manifest_json FROM snapshots WHERE id=?',(parent_snapshot,)).fetchone()
        if not previous or hashlib.sha256(previous[0].encode()).hexdigest()!=parent_snapshot:raise ValueError('Parent query snapshot integrity failure')
        inherited=json.loads(previous[0])['parse_payload_digests']
    payloads={str(p['id']):inherited[str(p['id'])] if str(p['id']) in inherited else payload_digest(conn,p['id'])
              for v in archive_records for p in v['parse_runs']}
    manifest={'format':'gitrx-query-snapshot-v5','archive_snapshot':archive,
      'context_policy':context_policy,'context_payload_sha256':context_hash,'context_source_sha256':context_source_hash,
      'section_history_source_sha256':section_history_hash,
      'locations_source_sha256':locations_hash,
      'source_quirks':[dict(r) for r in conn.execute('SELECT * FROM source_quirks ORDER BY id')],
      'query_contract_source_sha256':put_bytes(conn,root,Path(__file__).with_name('query_contract.py').read_bytes()),
      'body_evidence_source_sha256':json.loads(context_raw[0]).get('body_evidence_source_sha256'),
      'presentation_source_sha256':put_bytes(conn,root,Path(__file__).with_name('presentation.py').read_bytes()),
      'family_names':{r['setid']:r['name'] for r in conn.execute('SELECT setid,name FROM families ORDER BY setid')},
      'family_label_status':family_statuses(conn),
      'availability_source_sha256':put_bytes(conn,root,Path(__file__).with_name('availability.py').read_bytes()),
      'identity_policy':policy,'identity_payload_sha256':ids_hash,'identity_code_sha256':identity_code,
      'query_engine':ENGINE_VERSION,'query_source_sha256':code_hash,'normalizer_source_sha256':normalizer_hash,
      'normalization_profile':'literal-v1','unicode_version':unicodedata.unidata_version,
      'xml_segmenter':{k:PARSER_CONFIG[k] for k in ('parser_source_sha256','lxml_version','libxml_version')},
      'parse_payload_digests':payloads}
    text=dumps(manifest);sid=hashlib.sha256(text.encode()).hexdigest()
    conn.execute('INSERT OR IGNORE INTO snapshots VALUES(?,?,?)',(sid,now(),text));conn.commit()
    (root/'reports'/f'query-snapshot-{sid}.json').write_text(text+'\n')
    (root/'reports/QUERY-SNAPSHOT-ID.txt').write_text(sid+'\n')
    return sid


def load_snapshot(conn,snapshot):
    row=conn.execute('SELECT manifest_json FROM snapshots WHERE id=?',(snapshot,)).fetchone()
    if row is None or hashlib.sha256(row[0].encode()).hexdigest()!=snapshot:raise ValueError('Unknown or corrupt query snapshot')
    manifest=json.loads(row[0])
    if manifest.get('format')!='gitrx-query-snapshot-v5':raise ValueError('Expected a v5 query snapshot; legacy snapshots use pinned replay engines')
    if manifest.get('query_contract_source_sha256')!=hashlib.sha256(Path(__file__).with_name('query_contract.py').read_bytes()).hexdigest():raise ValueError('Snapshot pins different inventory contract')
    body=manifest.get('body_evidence_source_sha256')
    if body and body!=hashlib.sha256(Path(__file__).with_name('body_evidence.py').read_bytes()).hexdigest():raise ValueError('Frozen body rules changed')
    if manifest.get('presentation_source_sha256')!=hashlib.sha256(Path(__file__).with_name('presentation.py').read_bytes()).hexdigest():raise ValueError('Snapshot pins different presentation rules')
    if manifest.get('availability_source_sha256')!=hashlib.sha256(Path(__file__).with_name('availability.py').read_bytes()).hexdigest():raise ValueError('Snapshot pins different availability wording')
    if manifest['context_source_sha256']!=hashlib.sha256(Path(__file__).with_name('contexts.py').read_bytes()).hexdigest():raise ValueError('Snapshot pins a different context classifier')
    if hashlib.sha256(dumps(context_rows(conn,manifest['context_policy'])).encode()).hexdigest()!=manifest['context_payload_sha256']:raise ValueError('Pinned context overlay changed')
    if manifest['section_history_source_sha256']!=hashlib.sha256(Path(__file__).with_name('section_history.py').read_bytes()).hexdigest():
        raise ValueError('Snapshot pins different section-existence rules')
    if manifest['locations_source_sha256']!=hashlib.sha256(Path(__file__).with_name('locations.py').read_bytes()).hexdigest():
        raise ValueError('Snapshot pins different location rules')
    if manifest['unicode_version']!=unicodedata.unidata_version:raise ValueError('Snapshot pins a different Unicode normalization database')
    if dumps(manifest.get('xml_segmenter'))!=dumps({k:PARSER_CONFIG[k] for k in ('parser_source_sha256','lxml_version','libxml_version')}):
        raise ValueError('Snapshot pins a different table-cell XML segmenter')
    if manifest['query_source_sha256']!=hashlib.sha256(Path(__file__).read_bytes()).hexdigest():
        raise ValueError('This snapshot pins a different query engine; restore that engine to reproduce it')
    if manifest['normalizer_source_sha256']!=hashlib.sha256(Path(__file__).with_name('normalize.py').read_bytes()).hexdigest():
        raise ValueError('This snapshot pins a different normalizer')
    if hashlib.sha256(dumps(identity_rows(conn,manifest['identity_policy'])).encode()).hexdigest()!=manifest['identity_payload_sha256']:
        raise ValueError('Identity annotations differ from the pinned snapshot')
    raw=conn.execute('SELECT manifest_json FROM snapshots WHERE id=?',(manifest['archive_snapshot'],)).fetchone()
    if not raw or hashlib.sha256(raw[0].encode()).hexdigest()!=manifest['archive_snapshot']:raise ValueError('Archive snapshot integrity failure')
    archive=json.loads(raw[0])
    require_evidence_references(conn,archive['versions'])
    return manifest,archive


def ranges(numbers):
    values=sorted(set(numbers));out=[]
    for n in values:
        if out and out[-1][1]+1==n:out[-1][1]=n
        else:out.append([n,n])
    return [str(a) if a==b else f'{a}-{b}' for a,b in out]


def _issues(section):
    return json.loads(section['diagnostics_json'])


def effective_date(raw):
    if raw and re.fullmatch(r'\d{8}',raw):
        try:return datetime.strptime(raw,'%Y%m%d').date().isoformat()
        except ValueError:return raw+' (invalid source date)'
    return raw+' (source precision retained)' if raw else None


def retrieval_summary(version):
    """Selected-identity observations; route provenance is not family market status."""
    return [{'retrieval_id':r['id'],'retrieval_type':r['kind'],
             'wording':'retrieved by archive route' if r['kind']=='archive_route' else 'retrieved by '+r['url'].split('?')[0].rsplit('/',1)[-1],
             'url':r['url'],'retrieved_at':r['retrieved_at'],'raw_sha256':r['artifact_sha256']}
            for r in version['retrievals'] if version['selected_sha256'] and
            r['artifact_sha256']==version['selected_sha256'] and r['status']==200 and r['state']=='available']


def evaluate(conn,version,policy,codes,scope,phrases,ignore_case,include_headings,locations=None,excluded=(),quirks=(),context_policy=None):
    result={'version':version['version'],'setid':version['setid'],'state':'unknown','hits':[],
      'raw_sha256':version['selected_sha256'],'spl_effective_date':effective_date(version['effective_date']),
      'spl_effective_date_raw':version['effective_date'],
      'date_anomaly':bool(version['date_anomaly']),'warnings':[],'reasons':[],'inferred_sections':[],
      'section_instances':[],'same_code_other_contexts':[],'other_context_hits':[],
      'unclassified_context':[],'unclassified_context_hits':[]}
    result['retrieval_routes']=retrieval_summary(version)
    pubs=version['publication_observations'];dates=sorted({p['published_date'] for p in pubs if p['published_date']})
    result['published_date']=dates[0] if len(dates)==1 else None
    result['publication_observations']=pubs
    if result['date_anomaly']:result['warnings'].append('date anomaly')
    if len(dates)!=1:result['warnings'].append('publication date missing or conflicting')
    if version['state']!='downloaded':
        result['reasons'].append(route_failure(version) if version['state']=='unavailable' else version['state'])
        result['retrieval_evidence']=[{'retrieval_id':r['id'],'status':r['status'],'retrieved_at':r['retrieved_at'],
          'url':r['url'],'response_sha256':r['artifact_sha256'],'state':state_text(r['state'])} for r in version['retrievals']]
        return result
    parses=version['parse_runs']
    if len(parses)!=1:
        result['reasons'].append('snapshot does not pin exactly one parse run');return result
    parse=parses[0];result['parser_version']=parse['parser_version'];result['parser_configuration']=json.loads(parse['config_json'])
    result['document_parse_status']=parse['status']
    result['document_diagnostic_counts']=dict(Counter(d['code'] for d in json.loads(parse['diagnostics_json'])))
    if parse['status']=='partial':result['warnings'].append('document parse partial; requested scope assessed independently')
    if parse['status']=='extraction_failed':result['reasons'].append('document extraction failed');return result
    rows=[dict(r) for r in conn.execute('''SELECT s.id,s.ordinal,s.parent_ordinal,s.ancestry_path,s.title,s.kind,s.status,
      s.loinc_code,s.code_system,s.source_xpath,s.source_xml,s.own_text,s.diagnostics_json,LENGTH(TRIM(s.own_text)) AS text_chars,
      i.canonical_code,i.section_confidence,i.rule_id,i.evidence_json FROM sections s
      JOIN section_identities i ON i.section_id=s.id AND i.policy_id=? WHERE s.parse_run_id=? ORDER BY s.ordinal''',(policy,parse['id']))]
    if len(rows)!=parse['section_count']:raise ValueError('Pinned section inventory has changed')
    attach_contexts(conn,rows,context_policy)
    possible=possible_warnings(rows) if codes and '34066-1' in codes else []
    result['possible_unverified_warnings']=possible
    coded_absence=inventory_absence(rows,codes,parse['status'],possible)
    by_ordinal={s['ordinal']:s for s in rows}
    roots=set(s['ordinal'] for s in rows if codes is None or s['canonical_code'] in codes or s['loinc_code'] in codes)
    selected=set(roots);anchors={o:o for o in roots}
    for s in rows:
        if s['parent_ordinal'] in selected:
            selected.add(s['ordinal']);anchors[s['ordinal']]=anchors[s['parent_ordinal']]
    selected_rows=[s for s in rows if s['ordinal'] in selected]
    table_cache={}
    def get_table(section_id,ordinal):
        key=(section_id,ordinal)
        if key not in table_cache:
            t=conn.execute('SELECT flattened_text,structured_json FROM narrative_tables WHERE section_id=? AND ordinal=?',key).fetchone()
            table_cache[key]={'structure':json.loads(t['structured_json']),
              'projected':{normalize(html.unescape(x),ignore_case) for x in re.findall(r'<CELL>(.*?)</CELL>',t['flattened_text'],re.S)}}
        return table_cache[key]
    allowed_scopes={'full'} if scope=='full-label' else {'excerpt'} if scope=='highlights' else {'full','excerpt'}
    excerpt_owners={r[0] for r in conn.execute('SELECT e.section_id FROM section_excerpts e JOIN sections s ON s.id=e.section_id WHERE s.parse_run_id=?',(parse['id'],))}
    unknown=[]
    if codes is not None and not any(product_context(s) and s['ordinal'] in roots for s in rows) and any(product_context(s) and s['section_confidence']=='inferred' and s['text_chars'] for s in rows):
        unknown.append('inferred ancestor membership does not establish absence of an unrepresented finer-grained section')
    for s in rows:
        if codes is not None and product_part(s) and s['id'] in excerpt_owners and scope!='full-label' and (s['section_confidence']=='unknown' or s['context_confidence']=='unknown'):
            unknown.append('unclassified Highlights identity/context in instance '+str(s['ordinal']))
        if codes is not None and s['context_confidence']!='anchored' and product_part(s) and s['text_chars'] and scope!='highlights':
            unknown.append('unclassified context in narrative instance '+str(s['ordinal']))
        if codes is not None and product_context(s) and s['section_confidence']=='unknown' and s['text_chars']:
            # A known ancestor can bound an otherwise unnamed subsection.
            parent=s['parent_ordinal'];known=False
            while parent is not None:
                if by_ordinal[parent]['canonical_code']:known=True;break
                parent=by_ordinal[parent]['parent_ordinal']
            if not known:unknown.append(f"unidentified narrative section instance {s['ordinal']}")
    for s in selected_rows:
        eligible=codes is None or product_context(s)
        diagnostics=_issues(s)
        actual_scope='excerpt' if s['kind']=='excerpt' else 'full'
        relevant_scope=actual_scope in allowed_scopes
        excerpts=[dict(e) for e in conn.execute('SELECT * FROM section_excerpts WHERE section_id=? ORDER BY ordinal',(s['id'],))]
        has_excerpt=bool(excerpts)
        if eligible and codes is not None and s['kind']=='excerpt_only' and scope=='full-label':
            unknown.append(f"section instance {s['ordinal']}: source supplies highlights only, no full-label narrative")
        if scope=='highlights' and not has_excerpt and s['kind']!='excerpt':continue
        instance={**instance_info(s,version['version']),'section_id':s['id'],
          'source_code':s['loinc_code'],'canonical_code':s['canonical_code'],'confidence':s['section_confidence'],
          'source_quirks':[q for q in quirks if q['section_instance']==s['id']], 'matching_hits':0}
        reps=[]
        if actual_scope in allowed_scopes:reps.append((None,instance))
        if 'excerpt' in allowed_scopes:
            reps.extend((e,{**instance,**instance_info(s,version['version'],e),'matching_hits':0}) for e in excerpts)
        for _,rep in reps:
            key='section_instances' if codes is None or product_context(rep) else 'unclassified_context' if rep['context_confidence']!='anchored' else 'same_code_other_contexts'
            result[key].append(rep)
            if key=='unclassified_context':unknown.append(f"section instance {s['ordinal']}: context {rep['context_confidence']}; not a checked absence")
        if eligible and relevant_scope and s['kind']!='excerpt_only':
            for d in diagnostics:
                if d['code'] in ('narrative_extraction_error','unresolved_reference','source_leaf_has_no_narrative','empty_leaf_narrative'):
                    unknown.append(f"section instance {s['ordinal']}: {d['code']}")
                elif d['code']=='invalid_table_span':result['warnings'].append(f"section {s['ordinal']}: invalid table span; cell text searched, layout not inferred")
        if eligible and s['section_confidence']=='inferred':
            result['inferred_sections'].append({'ordinal':s['ordinal'],'canonical_code':s['canonical_code'],'rule':s['rule_id']})
        candidates=[]
        if include_headings and s['title'] and actual_scope in allowed_scopes and s['kind']!='excerpt_only':
            candidates.append({'text':s['title'],'kind':'heading','scope':actual_scope,'source_xpath':s['source_xpath']+'/s:title','block_id':None})
        for b in conn.execute('SELECT * FROM narrative_blocks WHERE section_id=? ORDER BY ordinal',(s['id'],)):
            if b['scope'] not in allowed_scopes:continue
            if b['kind']=='table_flat':
                # Scan authored cell/caption projections, never synthetic delimiter tokens.
                # Cells are searched through their separate blocks below; add captions here.
                structured=get_table(s['id'],b['table_ordinal'])['structure']
                for caption in structured['caption']:
                    for piece,role,rule,path in cell_pieces(caption['source_xml']):
                        candidates.append({'text':piece,'kind':'table_caption','scope':b['scope'],
                          'source_xpath':b['source_xpath'],'block_id':b['id'],'representation':'structured-caption + table-flat/caption',
                          'structural_location':role,'location_rule':rule,'relative_piece_xpath':path})
                continue
            item=dict(b);item['block_id']=item.pop('id')
            if b['kind']=='table_cell':
                table=get_table(s['id'],b['table_ordinal'])
                if normalize(b['text'],ignore_case) not in table['projected']:raise ValueError('Structured and flat table cell representations disagree')
                item['representation']='structured-cell + decoded-table-flat-cell'
                cell=table['structure']['rows'][b['row_ordinal']-1]['cells'][b['cell_ordinal']-1]
                for i,(piece,role,rule,path) in enumerate(cell_pieces(cell['source_xml']),1):
                    # Standalone cell extraction loses thead ancestry: the
                    # persisted row group supplies it without layout guessing.
                    if role=='table-cell' and table['structure']['rows'][b['row_ordinal']-1]['group']=='thead':
                        role,rule='table-header','source-thead'
                    candidates.append({**item,'text':piece,'cell_segment':i,
                      'structural_location':role,'location_rule':rule,'relative_piece_xpath':path})
                continue
            else:item['representation']='narrative'
            candidates.append(item)
        for candidate in candidates:
            authored=candidate['text']
            if candidate['kind']!='heading':
                authored=re.sub(r'\[(?:FOOTNOTE|MEDIA|NESTED-TABLE):?[^\]]*\]','\ufffc',authored)
            text=normalize(authored,ignore_case)
            alternatives=[original for original,needle in phrases if needle in text]
            if not alternatives:continue
            location,structural,rule=classify(s,candidate)
            if not include_headings and structural in ('section-title','inline-heading'):continue
            if locations and location not in locations:continue
            if location in excluded:continue
            representation=next((rep for e,rep in reps if (e is None and candidate['scope']==actual_scope) or
              (e is not None and candidate['scope']=='excerpt' and candidate['source_xpath'].startswith(e['source_xpath']))),None)
            if representation is None:raise ValueError('Hit has no context representation')
            first=min(text.find(needle) for original,needle in phrases if original in alternatives)
            hit={'section_ordinal':s['ordinal'],'ancestry_path':s['ancestry_path'],
              'version':version['version'],'section_context':representation['section_context'],
              'context_confidence':representation['context_confidence'],
              'enclosing_context':s['enclosing_context'],'context_rule':representation['context_rule'],
              'location':location,'structural_location':structural,'location_rule':rule,
              'exact_text_with_context':candidate['text'],
              'normalized_matching_text':text,
              'relative_piece_xpath':candidate.get('relative_piece_xpath'),
              'instance_first_200_characters':representation['first_200_characters'],
              'source_quirks':instance['source_quirks'],
              'original_code':s['loinc_code'],'canonical_code':s['canonical_code'],
              'section_confidence':s['section_confidence'],'identity_rule':s['rule_id'],
              'selected_root_ordinal':anchors.get(s['ordinal']),
              'scope':'highlights' if candidate['scope']=='excerpt' else 'full-label',
              'matched_alternatives':alternatives,'kind':candidate['kind'],
              'representation':candidate.get('representation','section-heading'),
              'block_id':candidate['block_id'],'source_xpath':candidate['source_xpath'],
              'cell_segment':candidate.get('cell_segment'),
              'snippet':text[max(0,first-65):first+180]}
            key='hits' if codes is None or product_context(representation) else 'unclassified_context_hits' if representation['context_confidence']!='anchored' else 'other_context_hits'
            result[key].append(hit)
            representation['matching_hits']+=1
    result['reasons']=sorted(set(unknown));result['warnings']=sorted(set(result['warnings']))
    result['checked_complete']=not unknown
    result['unclassified_context_counts']={'instances':len(result['unclassified_context']),'hits':len(result['unclassified_context_hits'])}
    result['heading_only']=bool(result['hits']) and all(
        h['structural_location'] in ('section-title','inline-heading') for h in result['hits'])
    result['heading_only_notice']=None
    if result['heading_only']:
        body_checked=(not locations or 'body' in locations) and 'body' not in excluded
        if scope!='full-label':body_checked=body_checked and (not locations or 'highlights' in locations) and 'highlights' not in excluded
        if result['checked_complete'] and body_checked:
            result['heading_only_notice']='Matched headings only; body text does not contain this exact phrase in the searched scope. Try an explicit any-of list.'
        else:
            result['heading_only_notice']='Matched headings only; body presence was not established under these filters or extraction coverage. Try an explicit any-of list including wording from the source body.'
    if coded_absence and not result['hits']:
        result['coded_inventory_check']=coded_absence;result['non_coded_scope_uncertainties']=result['reasons']
        result['reasons']=[];result['checked_complete']=True;unknown=[]
    if result['hits']:result['state']='match'
    elif unknown:result['state']='unknown'
    else:result['state']='checked-nonmatching'
    return result


def first_occurrence(conn,snapshot,setid,section,phrases,scope='full-label',ignore_case=False,include_headings=True,locations=None,excluded=()):
    if not phrases or any(not normalize(p,ignore_case) for p in phrases):raise ValueError('Provide nonempty literal phrases')
    if scope not in ('full-label','highlights','both'):raise ValueError('Unknown scope')
    if (set(locations or ())|set(excluded))-set(LOCATIONS):raise ValueError('Unknown hit location')
    if section=='whole-document':codes=None
    elif section in ALIASES:codes=ALIASES[section]
    elif re.fullmatch(r'\d+-\d',section):codes=[section]
    else:raise ValueError('Unknown section alias; use a LOINC code or whole-document')
    manifest,base=load_snapshot(conn,snapshot)
    records=[v for v in base['versions'] if v['setid']==setid.lower()]
    if not records:raise ValueError('Family is not in this snapshot')
    records.sort(key=lambda v:v['version'])
    for version in records:
        for parse in version['parse_runs']:
            if payload_digest(conn,parse['id'])!=manifest['parse_payload_digests'].get(str(parse['id'])):
                raise ValueError(f"Pinned query payload changed for version {version['version']}")
    needles=list(dict.fromkeys(phrases));normalized=[(p,normalize(p,ignore_case)) for p in needles]
    states=[evaluate(conn,v,manifest['identity_policy'],codes,scope,normalized,ignore_case,include_headings,
                     locations,excluded,manifest['source_quirks'],manifest['context_policy']) for v in records]
    first=next((v for v in states if v['state']=='match'),None);runs=[]
    for v in states:
        if v['state']!='match':continue
        previous=runs[-1][-1] if runs else None
        if previous and v['version']==previous['version']+1:runs[-1].append(v)
        else:runs.append([v])
    numbers={v['version'] for v in states};gaps=sorted(set(range(1,max(numbers)+1))-numbers)
    missing=[{'version':v['version'],'position':'oldest' if v is states[0] else 'newest' if v is states[-1] else 'middle',
              'reasons':v['reasons']} for v in states if v['state']=='unknown']
    predecessor=None;qualification='cannot determine' if missing else 'not found in checked versions'
    if first:
        predecessor=next((v for v in states if v['version']==first['version']-1),None)
        if predecessor and predecessor['state']=='checked-nonmatching' and predecessor.get('checked_complete'):
            qualification=f"added in version {first['version']} (observed text transition in this family and scope)"
        elif first['version']==states[0]['version']:qualification=f"present in the earliest available version (v{first['version']}); addition not established"
        else:qualification=f"present in the earliest available version containing the phrase (v{first['version']}); addition not established"
    intervals=[]
    for run in runs:
        intervals.append({'versions':[v['version'] for v in run],
          'published_endpoints':[run[0]['published_date'],run[-1]['published_date']],
          'spl_effective_endpoints':[run[0]['spl_effective_date'],run[-1]['spl_effective_date']],
          'matched_alternatives':sorted({a for v in run for h in v['hits'] for a in h['matched_alternatives']}),
          'open_at_snapshot_end':run[-1]['version']==states[-1]['version'],
          'continuity_claim':'observed consecutive numeric versions only; not a legal/calendar interval'})
    only_headings=bool(first) and all(v.get('heading_only',False) for v in states if v['hits'])
    notices=[v['heading_only_notice'] for v in states if v.get('heading_only_notice')]
    heading_notice=next((n for n in notices if 'not established' in n),notices[0] if notices else None) if only_headings else None
    return {'engine':ENGINE_VERSION,'family':setid.lower(),'family_title':manifest['family_names'][setid.lower()],
      'family_label_status':manifest['family_label_status'][setid.lower()],
      'headline_instances':headline_instances(first,'section_instances'),
      'section':section,'loinc_codes':codes,'locations':sorted(locations) if locations else None,'excluded_locations':sorted(excluded),
      'scope':scope,'phrases':needles,'earliest':first,'qualification':qualification,
      'heading_only':only_headings,'heading_only_notice':heading_notice,
      'integer_predecessor':predecessor,
      'next_lower_listed_predecessor':next((v for v in reversed(states) if first and v['version']<first['version']),None),
      'intervals':intervals,'unknown_versions':missing,'unlisted_integer_gaps':ranges(gaps),
      'coverage':{'listed':len(states),'downloaded':sum(v['state']=='downloaded' for v in records),
                  'parsed':sum(bool(v['parse_runs']) for v in records),'section_checkable':sum(v.get('checked_complete',False) for v in states),
                  'matching':sum(v['state']=='match' for v in states),'unknown':len(missing)},
      'versions':states,'archive_snapshot':snapshot,'source_archive_snapshot':manifest['archive_snapshot'],
      'identity_policy':manifest['identity_policy'],
      'context_policy':manifest['context_policy'],'section_definition':'code plus anchored full-label-body/highlights context only; unclassified evidence is separate; whole-document retains all contexts',
      'normalization':{'profile':'literal-v1','ignore_case':ignore_case,'unicode_version':unicodedata.unidata_version,
        'include_headings':include_headings,'match':'literal substring; any-of OR; no stemming/synonyms',
        'generated_reference_markers':'excluded from authored text; boundary retained',
        'boundaries':'no cross-paragraph/list-item/cell/section matches; flat cells decoded; delimiter tokens excluded'},
      'limitations':['Earliest is by numeric version, not publication date or FDA announcement.',
        'Section codes are retained as published; semantically miscoded instances are not auto-corrected.',
        'Inline-heading tags use visible structural rules, not clinical interpretation; ambiguous styling remains body.',
        'Available machine-readable text only; no image OCR or legal-applicability claim.',
        'Route failures mean not retrievable by the named route in this snapshot; other routes remain unknown.',
        'Inferred section membership is explicitly identified; it is not an original source code.',
        'Source-empty sections and unidentified relevant scope can prevent a reliable negative result.']}


def render(result):
    lines=[f"Family: {family_title(result['family_title'])} (setid {result['family']})",f"Section: {result['section']} ({result['loinc_codes'] or 'all sections'})",
      f"Scope: {result['scope']}; headings included: {result['normalization']['include_headings']}",
      f"Locations: {result['locations'] or 'all'}; excluded locations: {result['excluded_locations']}",
      f"Phrases (any-of): {readable(result['phrases'],ensure_ascii=False)}"]
    lines.insert(1,'Family label status: '+status_text(result['family_label_status'])+'; archive membership alone does not establish inactivity.')
    if result['heading_only_notice']:lines.append('Notice: '+result['heading_only_notice'])
    first=result['earliest']
    if first:lines.append(f"Earliest occurrence found in the available history: version {first['version']}, DailyMed publication date {first['published_date']} (SPL effective date {first['spl_effective_date']}).")
    else:lines.append('Earliest occurrence: cannot determine (no verified match; see coverage and unknowns).' if result['unknown_versions'] else 'Earliest occurrence: not found in checked versions.')
    lines.append('Qualification: '+result['qualification'])
    for i in result.get('headline_instances',[]):lines.append(f"Headline instance {i['instance_index']} ({i['ancestry_path']}): first 200 characters: {i['first_200_characters']}")
    p=result['integer_predecessor'];prev=result['next_lower_listed_predecessor']
    if p:lines.append(f"Integer predecessor v{p['version']}: {p['state']}; {p['reasons'] or 'checked under the same settings'}.")
    elif first:lines.append('Integer predecessor: not listed / no earlier listed version; no content inferred.')
    if prev:lines.append(f"Next-lower listed predecessor: v{prev['version']}, {prev['state']}.")
    lines.append('All observed presence runs (numeric order; gaps split runs):')
    for interval in result['intervals']:
        lines.append(f"  versions {','.join(map(str,interval['versions']))}; DailyMed publication date endpoints {interval['published_endpoints']}; SPL effective date endpoints {interval['spl_effective_endpoints']}; alternatives {interval['matched_alternatives']}; open at snapshot end {interval['open_at_snapshot_end']}")
    if not result['intervals']:lines.append('  none established')
    lines.append('Unknown versions: '+('; '.join(f"v{v['version']} ({v['position']}): {', '.join(v['reasons'])}" for v in result['unknown_versions']) or 'none'))
    lines.append('Unlisted integer gaps: '+(', '.join(result['unlisted_integer_gaps']) or 'none'))
    lines.append('Coverage: '+coverage_text(result))
    lines.append('Per-version evidence (all checked versions cited):')
    for v in result['versions']:
        lines.append(f"  v{v['version']}: {state_text(v['state'])}; DailyMed publication date {v['published_date']}; SPL effective date {v['spl_effective_date']}; {'DATE ANOMALY' if v['date_anomaly'] else 'no date anomaly'}; parse {v.get('document_parse_status','not retrieved in this snapshot')}")
        if v['raw_sha256']:lines.append(f"    Cite: {v['setid']} + {v['version']} + {v['raw_sha256']}")
        for route in v['retrieval_routes']:
            lines.append('    Retrieval: '+retrieval_text(route))
        for evidence in v.get('retrieval_evidence',[]):
            lines.append(f"    Retrieval {evidence['retrieval_id']}: HTTP {evidence['status']}, {evidence['retrieved_at']}, {evidence['url']}; response SHA-256 {evidence['response_sha256']} (not label content)")
        if v.get('coded_inventory_check'):lines.append('    Coded inventory: checked-absent; uncoded clinical warning absence not established.')
        for possible in v.get('possible_unverified_warnings',[]):lines.append('    Styled warning: possible, unverified; instance '+str(possible['instance_index'])+'; first 200 characters: '+possible['first_200_characters'])
        for reason in v['reasons']:lines.append('    Uncertainty: '+reason)
        for warning in v['warnings']:lines.append('    Warning: '+warning)
        if v.get('heading_only_notice'):lines.append('    Notice: '+v['heading_only_notice'])
        for inferred in v['inferred_sections']:lines.append('    Inferred section consulted: '+readable(inferred,sort_keys=True))
        for instance in v['section_instances']:
            lines.append(f"    Instance {instance['instance_index']}: version={instance['version']}; context={instance['section_context']}; context confidence={instance['context_confidence']}; enclosing context={instance['enclosing_context']}; ancestry={instance['ancestry_path']}; source code={instance['source_code']}; canonical={instance['canonical_code']}; confidence={instance['confidence']}; matching hits={instance['matching_hits']}")
            lines.append('      First 200 characters: '+readable(instance['first_200_characters'],ensure_ascii=False))
            lines.append('      Context rule: '+readable(instance['context_rule'],ensure_ascii=False,sort_keys=True))
            for quirk in instance['source_quirks']:lines.append('      Human-confirmed source quirk: '+readable(quirk,ensure_ascii=False,sort_keys=True))
        for instance in v['same_code_other_contexts']:
            lines.append('    Same code in '+instance['section_context']+' (excluded from main answer): '+readable(instance,ensure_ascii=False,sort_keys=True))
        lines.append('    Unclassified context counts: '+readable(v.get('unclassified_context_counts',{'instances':0,'hits':0}),sort_keys=True))
        for instance in v['unclassified_context']:lines.append('      Unclassified context instance (excluded from main answer): '+readable(instance,ensure_ascii=False,sort_keys=True))
        for hit in v['hits']+v['other_context_hits']+v['unclassified_context_hits']:
            prefix='Hit' if hit in v['hits'] else 'Unclassified context hit (excluded from main answer)' if hit in v['unclassified_context_hits'] else 'Same-code other-context hit (excluded from main answer)'
            lines.append(f"    {prefix}: version={hit['version']}; context={hit['section_context']}; enclosing context={hit['enclosing_context']}; scope={hit['scope']}; location={hit['location']}; structural location={hit['structural_location']}; location rule={hit['location_rule']}; instance={hit['section_ordinal']}; ancestry={hit['ancestry_path']}; original={hit['original_code']}; canonical={hit['canonical_code']}; confidence={hit['section_confidence']}; rule={hit['identity_rule']}; kind={hit['kind']}; representation={hit['representation']}; alternatives={hit['matched_alternatives']}")
            lines.append('      First 200 characters: '+readable(hit['instance_first_200_characters'],ensure_ascii=False))
            lines.append('      Context confidence: '+hit['context_confidence'])
            lines.append('      Exact extracted text with block context: '+readable(hit['exact_text_with_context'],ensure_ascii=False))
            lines.append('      Normalized matching text: '+readable(hit['normalized_matching_text'],ensure_ascii=False))
            if hit['source_xpath']:lines.append('      XPath: '+hit['source_xpath'])
            if hit['relative_piece_xpath']:lines.append('      Relative table piece XPath: '+hit['relative_piece_xpath'])
    lines += ['Archive snapshot: '+result['archive_snapshot'],'Source archive snapshot: '+result['source_archive_snapshot'],
      'Identity policy: '+result['identity_policy'],'Context policy: '+result['context_policy'],
      'Section definition: '+result['section_definition'],'Normalization: '+readable(result['normalization'],ensure_ascii=False,sort_keys=True),
      'Limitations:']+[f'  - {x}' for x in result['limitations']]
    return '\n'.join(lines)+'\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path.cwd())
    sub=parser.add_subparsers(dest='command',required=True)
    for command in ('first-occurrence','section-first-present'):
        p=sub.add_parser(command)
        p.add_argument('setid');p.add_argument('--section',required=True)
        p.add_argument('--scope',choices=('full-label','highlights','both'),default='full-label')
        p.add_argument('--snapshot');p.add_argument('--format',choices=('text','json'),default='text');p.add_argument('--json',dest='format',action='store_const',const='json')
        if command=='first-occurrence':
            p.add_argument('--phrase',action='append',required=True)
            p.add_argument('--ignore-case',action='store_true');p.add_argument('--no-headings',action='store_true')
            p.add_argument('--location',choices=LOCATIONS,action='append')
            p.add_argument('--exclude-location',choices=LOCATIONS,action='append',default=[])
    opts=parser.parse_args();root=opts.root.resolve()
    conn=connect(root/'data/gitrx.sqlite')
    snapshot=opts.snapshot or (root/'reports/QUERY-SNAPSHOT-ID.txt').read_text().strip()
    row=conn.execute('SELECT manifest_json FROM snapshots WHERE id=?',(snapshot,)).fetchone()
    engine=json.loads(row[0]).get('query_engine') if row else None
    if '--json' in sys.argv:
        i=sys.argv.index('--json');sys.argv[i:i+1]=['--format','json']
    if engine=='phrase-history-0.5.5':
        from .legacy_v9.query import main as legacy_main
        return legacy_main()
    if engine=='phrase-history-0.5.4':
        from .legacy_v8.query import main as legacy_main
        return legacy_main()
    if engine=='phrase-history-0.5.3':
        from .legacy_v7.query import main as legacy_main
        return legacy_main()
    if engine=='phrase-history-0.5.2':
        from .legacy_v6.query import main as legacy_main
        return legacy_main()
    if engine=='phrase-history-0.5.0':
        from .legacy_v5.query import main as legacy_main
        return legacy_main()
    if engine=='phrase-history-0.4.0':
        from .legacy_v4.query import main as legacy_main
        return legacy_main()
    if engine=='phrase-history-0.3.0':
        from .legacy_v3.query import main as legacy_main
        return legacy_main()
    if engine in ('phrase-history-0.1.0','phrase-history-0.2.0'):
        if opts.command!='first-occurrence':parser.error('Section existence requires a new query snapshot')
        if engine=='phrase-history-0.1.0':
            if opts.location or opts.exclude_location:parser.error('Location filters require a v2 query snapshot')
            from .query_v1 import main as legacy_main
        else:from .query_v2 import main as legacy_main
        return legacy_main()
    try:
        if opts.command=='section-first-present':
            from .section_history import section_first_present,render_section
            result=section_first_present(conn,snapshot,opts.setid,opts.section,opts.scope)
        else:result=first_occurrence(conn,snapshot,opts.setid,opts.section,opts.phrase,opts.scope,opts.ignore_case,not opts.no_headings,opts.location,opts.exclude_location)
    except ValueError as exc:parser.error(str(exc))
    renderer=render_section if opts.command=='section-first-present' else render
    print(json.dumps(result,ensure_ascii=False,indent=2) if opts.format=='json' else renderer(result),end='\n' if opts.format=='json' else '')


if __name__=='__main__':main()
