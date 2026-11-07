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

from .archive import now, put_bytes
from .db import connect, dumps
from .normalize import normalize
from .parser import Narrative, CONFIG as PARSER_CONFIG
from .archive import read_xml
from .locations import LOCATIONS, cell_pieces, classify

ENGINE_VERSION='phrase-history-0.2.0'
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
      JOIN sections s ON s.id=t.section_id WHERE s.parse_run_id=? ORDER BY s.ordinal,t.ordinal''')]
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


def freeze_query_snapshot(conn,root):
    root=Path(root)
    archive=(root/'reports/SNAPSHOT-ID.txt').read_text().strip()
    base=conn.execute('SELECT manifest_json FROM snapshots WHERE id=?',(archive,)).fetchone()
    if not base or hashlib.sha256(base[0].encode()).hexdigest()!=archive:raise ValueError('Archive manifest integrity failure')
    policy=(root/'reports/IDENTITY-POLICY-ID.txt').read_text().strip()
    p=conn.execute('SELECT policy_json FROM identity_policies WHERE id=?',(policy,)).fetchone()
    if not p or hashlib.sha256(p[0].encode()).hexdigest()!=policy:raise ValueError('Identity policy integrity failure')
    rows=dumps(identity_rows(conn,policy)).encode();ids_hash=put_bytes(conn,root,rows)
    code_hash=put_bytes(conn,root,Path(__file__).read_bytes())
    identity_code=put_bytes(conn,root,Path(__file__).with_name('identity.py').read_bytes())
    locations_hash=put_bytes(conn,root,Path(__file__).with_name('locations.py').read_bytes())
    normalizer_hash=put_bytes(conn,root,Path(__file__).with_name('normalize.py').read_bytes())
    archive_records=json.loads(base[0])['versions']
    payloads={str(p['id']):payload_digest(conn,p['id']) for v in archive_records for p in v['parse_runs']}
    manifest={'format':'gitrx-query-snapshot-v2','archive_snapshot':archive,
      'locations_source_sha256':locations_hash,
      'source_quirks':[dict(r) for r in conn.execute('SELECT * FROM source_quirks ORDER BY id')],
      'family_names':{r['setid']:r['name'] for r in conn.execute('SELECT setid,name FROM families ORDER BY setid')},
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
    if manifest.get('format')!='gitrx-query-snapshot-v2':raise ValueError('Expected a v2 query snapshot; legacy snapshots use query_v1')
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
    return manifest,json.loads(raw[0])


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


def evaluate(conn,version,policy,codes,scope,phrases,ignore_case,include_headings,locations=None,excluded=(),quirks=()):
    result={'version':version['version'],'setid':version['setid'],'state':'unknown','hits':[],
      'raw_sha256':version['selected_sha256'],'spl_effective_date':effective_date(version['effective_date']),
      'spl_effective_date_raw':version['effective_date'],
      'date_anomaly':bool(version['date_anomaly']),'warnings':[],'reasons':[],'inferred_sections':[],
      'section_instances':[]}
    pubs=version['publication_observations'];dates=sorted({p['published_date'] for p in pubs if p['published_date']})
    result['published_date']=dates[0] if len(dates)==1 else None
    result['publication_observations']=pubs
    if result['date_anomaly']:result['warnings'].append('date anomaly')
    if len(dates)!=1:result['warnings'].append('publication date missing or conflicting')
    if version['state']!='downloaded':
        result['reasons'].append('unavailable in this archive snapshot' if version['state']=='unavailable' else version['state'])
        result['retrieval_evidence']=[{'retrieval_id':r['id'],'status':r['status'],'retrieved_at':r['retrieved_at'],
          'url':r['url'],'response_sha256':r['artifact_sha256'],'state':r['state']} for r in version['retrievals']]
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
    unknown=[]
    if codes is not None and not roots and any(s['section_confidence']=='inferred' and s['text_chars'] for s in rows):
        unknown.append('inferred ancestor membership does not establish absence of an unrepresented finer-grained section')
    for s in rows:
        if codes is not None and s['section_confidence']=='unknown' and s['text_chars']:
            # A known ancestor can bound an otherwise unnamed subsection.
            parent=s['parent_ordinal'];known=False
            while parent is not None:
                if by_ordinal[parent]['canonical_code']:known=True;break
                parent=by_ordinal[parent]['parent_ordinal']
            if not known:unknown.append(f"unidentified narrative section instance {s['ordinal']}")
    for s in selected_rows:
        diagnostics=_issues(s)
        actual_scope='excerpt' if s['kind']=='excerpt' else 'full'
        relevant_scope=actual_scope in allowed_scopes
        has_excerpt=conn.execute('SELECT 1 FROM section_excerpts WHERE section_id=? LIMIT 1',(s['id'],)).fetchone() is not None
        if codes is not None and s['kind']=='excerpt_only' and scope=='full-label':
            unknown.append(f"section instance {s['ordinal']}: source supplies highlights only, no full-label narrative")
        if scope=='highlights' and not has_excerpt and s['kind']!='excerpt':continue
        instance={'section_id':s['id'],'instance_index':s['ordinal'],'ancestry_path':s['ancestry_path'],
          'source_code':s['loinc_code'],'canonical_code':s['canonical_code'],'confidence':s['section_confidence'],
          'first_200_characters':s['own_text'][:200],
          'source_quirks':[q for q in quirks if q['section_instance']==s['id']], 'matching_hits':0}
        result['section_instances'].append(instance)
        if relevant_scope and s['kind']!='excerpt_only':
            for d in diagnostics:
                if d['code'] in ('narrative_extraction_error','unresolved_reference','source_leaf_has_no_narrative','empty_leaf_narrative'):
                    unknown.append(f"section instance {s['ordinal']}: {d['code']}")
                elif d['code']=='invalid_table_span':result['warnings'].append(f"section {s['ordinal']}: invalid table span; cell text searched, layout not inferred")
        if s['section_confidence']=='inferred':
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
            first=min(text.find(needle) for original,needle in phrases if original in alternatives)
            result['hits'].append({'section_ordinal':s['ordinal'],'ancestry_path':s['ancestry_path'],
              'location':location,'structural_location':structural,'location_rule':rule,
              'exact_text_with_context':candidate['text'],
              'normalized_matching_text':text,
              'relative_piece_xpath':candidate.get('relative_piece_xpath'),
              'instance_first_200_characters':instance['first_200_characters'],
              'source_quirks':instance['source_quirks'],
              'original_code':s['loinc_code'],'canonical_code':s['canonical_code'],
              'section_confidence':s['section_confidence'],'identity_rule':s['rule_id'],
              'selected_root_ordinal':anchors.get(s['ordinal']),
              'scope':'highlights' if candidate['scope']=='excerpt' else 'full-label',
              'matched_alternatives':alternatives,'kind':candidate['kind'],
              'representation':candidate.get('representation','section-heading'),
              'block_id':candidate['block_id'],'source_xpath':candidate['source_xpath'],
              'cell_segment':candidate.get('cell_segment'),
              'snippet':text[max(0,first-65):first+180]})
            instance['matching_hits']+=1
    result['reasons']=sorted(set(unknown));result['warnings']=sorted(set(result['warnings']))
    result['checked_complete']=not unknown
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
                     locations,excluded,manifest['source_quirks']) for v in records]
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
        else:qualification=f"present in the earliest available version containing the phrase (v{first['version']}); addition not established"
    intervals=[]
    for run in runs:
        intervals.append({'versions':[v['version'] for v in run],
          'published_endpoints':[run[0]['published_date'],run[-1]['published_date']],
          'spl_effective_endpoints':[run[0]['spl_effective_date'],run[-1]['spl_effective_date']],
          'matched_alternatives':sorted({a for v in run for h in v['hits'] for a in h['matched_alternatives']}),
          'open_at_snapshot_end':run[-1]['version']==states[-1]['version'],
          'continuity_claim':'observed consecutive numeric versions only; not a legal/calendar interval'})
    return {'engine':ENGINE_VERSION,'family':setid.lower(),'family_title':manifest['family_names'][setid.lower()],
      'section':section,'loinc_codes':codes,'locations':sorted(locations) if locations else None,'excluded_locations':sorted(excluded),
      'scope':scope,'phrases':needles,'earliest':first,'qualification':qualification,
      'integer_predecessor':predecessor,
      'next_lower_listed_predecessor':next((v for v in reversed(states) if first and v['version']<first['version']),None),
      'intervals':intervals,'unknown_versions':missing,'unlisted_integer_gaps':ranges(gaps),
      'coverage':{'listed':len(states),'downloaded':sum(v['state']=='downloaded' for v in records),
                  'parsed':sum(bool(v['parse_runs']) for v in records),'section_checkable':sum(v.get('checked_complete',False) for v in states),
                  'matching':sum(v['state']=='match' for v in states),'unknown':len(missing)},
      'versions':states,'archive_snapshot':snapshot,'source_archive_snapshot':manifest['archive_snapshot'],
      'identity_policy':manifest['identity_policy'],
      'normalization':{'profile':'literal-v1','ignore_case':ignore_case,'unicode_version':unicodedata.unidata_version,
        'include_headings':include_headings,'match':'literal substring; any-of OR; no stemming/synonyms',
        'generated_reference_markers':'excluded from authored text; boundary retained',
        'boundaries':'no cross-paragraph/list-item/cell/section matches; flat cells decoded; delimiter tokens excluded'},
      'limitations':['Earliest is by numeric version, not publication date or FDA announcement.',
        'Section codes are retained as published; semantically miscoded instances are not auto-corrected.',
        'Inline-heading tags use visible structural rules, not clinical interpretation; ambiguous styling remains body.',
        'Available machine-readable text only; no image OCR or legal-applicability claim.',
        'Unavailable means unavailable in this snapshot through its approved ingest route, not proven absent from DailyMed.',
        'Inferred section membership is explicitly identified; it is not an original source code.',
        'Source-empty sections and unidentified relevant scope can prevent a reliable negative result.']}


def render(result):
    lines=[f"Family: {result['family_title']} (setid {result['family']})",f"Section: {result['section']} ({result['loinc_codes'] or 'all sections'})",
      f"Scope: {result['scope']}; headings included: {result['normalization']['include_headings']}",
      f"Locations: {result['locations'] or 'all'}; excluded locations: {result['excluded_locations']}",
      f"Phrases (any-of): {json.dumps(result['phrases'],ensure_ascii=False)}"]
    first=result['earliest']
    if first:lines.append(f"Earliest occurrence found in the available history: version {first['version']}, DailyMed publication date {first['published_date']} (SPL effective date {first['spl_effective_date']}).")
    else:lines.append('Earliest occurrence: cannot determine (no verified match; see coverage and unknowns).' if result['unknown_versions'] else 'Earliest occurrence: not found in checked versions.')
    lines.append('Qualification: '+result['qualification'])
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
    lines.append('Coverage: '+json.dumps(result['coverage'],sort_keys=True))
    lines.append('Per-version evidence (all checked versions cited):')
    for v in result['versions']:
        lines.append(f"  v{v['version']}: {v['state']}; DailyMed publication date {v['published_date']}; SPL effective date {v['spl_effective_date']}; {'DATE ANOMALY' if v['date_anomaly'] else 'no date anomaly'}; parse {v.get('document_parse_status','unavailable')}")
        if v['raw_sha256']:lines.append(f"    Cite: {v['setid']} + {v['version']} + {v['raw_sha256']}")
        for evidence in v.get('retrieval_evidence',[]):
            lines.append(f"    Retrieval {evidence['retrieval_id']}: HTTP {evidence['status']}, {evidence['retrieved_at']}, {evidence['url']}; response SHA-256 {evidence['response_sha256']} (not label content)")
        for reason in v['reasons']:lines.append('    Uncertainty: '+reason)
        for warning in v['warnings']:lines.append('    Warning: '+warning)
        for inferred in v['inferred_sections']:lines.append('    Inferred section consulted: '+json.dumps(inferred,sort_keys=True))
        for instance in v['section_instances']:
            lines.append(f"    Instance {instance['instance_index']}: source code={instance['source_code']}; canonical={instance['canonical_code']}; confidence={instance['confidence']}; matching hits={instance['matching_hits']}")
            lines.append('      First 200 characters: '+json.dumps(instance['first_200_characters'],ensure_ascii=False))
            for quirk in instance['source_quirks']:lines.append('      Human-confirmed source quirk: '+json.dumps(quirk,ensure_ascii=False,sort_keys=True))
        for hit in v['hits']:
            lines.append(f"    Hit: scope={hit['scope']}; location={hit['location']}; structural location={hit['structural_location']}; location rule={hit['location_rule']}; instance={hit['section_ordinal']}; ancestry={hit['ancestry_path']}; original={hit['original_code']}; canonical={hit['canonical_code']}; confidence={hit['section_confidence']}; rule={hit['identity_rule']}; kind={hit['kind']}; representation={hit['representation']}; alternatives={hit['matched_alternatives']}")
            lines.append('      Exact extracted text with block context: '+json.dumps(hit['exact_text_with_context'],ensure_ascii=False))
            lines.append('      Normalized matching text: '+json.dumps(hit['normalized_matching_text'],ensure_ascii=False))
            if hit['source_xpath']:lines.append('      XPath: '+hit['source_xpath'])
            if hit['relative_piece_xpath']:lines.append('      Relative table piece XPath: '+hit['relative_piece_xpath'])
    lines += ['Archive snapshot: '+result['archive_snapshot'],'Source archive snapshot: '+result['source_archive_snapshot'],
      'Identity policy: '+result['identity_policy'],'Normalization: '+json.dumps(result['normalization'],ensure_ascii=False,sort_keys=True),
      'Limitations:']+[f'  - {x}' for x in result['limitations']]
    return '\n'.join(lines)+'\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path.cwd())
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('first-occurrence')
    p.add_argument('setid');p.add_argument('--section',required=True)
    p.add_argument('--phrase',action='append',required=True)
    p.add_argument('--scope',choices=('full-label','highlights','both'),default='full-label')
    p.add_argument('--ignore-case',action='store_true');p.add_argument('--no-headings',action='store_true')
    p.add_argument('--location',choices=LOCATIONS,action='append')
    p.add_argument('--exclude-location',choices=LOCATIONS,action='append',default=[])
    p.add_argument('--snapshot');p.add_argument('--format',choices=('text','json'),default='text')
    opts=parser.parse_args();root=opts.root.resolve()
    conn=connect(root/'data/gitrx.sqlite')
    snapshot=opts.snapshot or (root/'reports/QUERY-SNAPSHOT-ID.txt').read_text().strip()
    row=conn.execute('SELECT manifest_json FROM snapshots WHERE id=?',(snapshot,)).fetchone()
    if row and json.loads(row[0]).get('query_engine')=='phrase-history-0.1.0':
        if opts.location or opts.exclude_location:parser.error('Location filters require a v2 query snapshot')
        from .query_v1 import main as legacy_main
        return legacy_main()
    try:result=first_occurrence(conn,snapshot,opts.setid,opts.section,opts.phrase,opts.scope,opts.ignore_case,not opts.no_headings,opts.location,opts.exclude_location)
    except ValueError as exc:parser.error(str(exc))
    print(json.dumps(result,ensure_ascii=False,indent=2) if opts.format=='json' else render(result),end='\n' if opts.format=='json' else '')


if __name__=='__main__':main()
