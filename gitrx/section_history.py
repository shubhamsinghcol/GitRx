"""Structural section existence, independent of wording and narrative fidelity."""
import json
import re

from .db import dumps
from .query import ALIASES,ENGINE_VERSION,effective_date,load_snapshot,payload_digest,ranges,retrieval_summary
from .contexts import attach_contexts,instance_info,product_context,product_part
from .availability import route_failure,state_text


def evaluate_section(conn,version,policy,codes,scope,quirks,context_policy):
    dates=sorted({p['published_date'] for p in version['publication_observations'] if p['published_date']})
    out={'setid':version['setid'],'version':version['version'],'state':'unknown','reasons':[],
      'warnings':[],'instances':[],'same_code_other_contexts':[],'unclassified_context':[],'checked_complete':False,'raw_sha256':version['selected_sha256'],
      'published_date':dates[0] if len(dates)==1 else None,
      'publication_observations':version['publication_observations'],
      'spl_effective_date':effective_date(version['effective_date']),'date_anomaly':bool(version['date_anomaly'])}
    out['retrieval_routes']=retrieval_summary(version)
    if out['date_anomaly']:out['warnings'].append('date anomaly')
    if len(dates)!=1:out['warnings'].append('DailyMed publication date missing or conflicting')
    if version['state']!='downloaded':
        out['state']='not_retrievable' if version['state']=='unavailable' else 'unknown'
        out['reasons'].append(route_failure(version) if out['state']=='not_retrievable' else version['state'])
        out['retrieval_evidence']=[{'retrieval_id':r['id'],'HTTP_status':r['status'],'retrieved_at':r['retrieved_at'],
          'url':r['url'],'response_sha256':r['artifact_sha256']} for r in version['retrievals']]
        return out
    parses=version['parse_runs']
    if len(parses)!=1:
        out['reasons'].append('snapshot does not pin exactly one parse run');return out
    parse=parses[0]
    out.update(parser_version=parse['parser_version'],document_parse_status=parse['status'])
    rows=[dict(r) for r in conn.execute('''SELECT s.*,i.canonical_code,i.section_confidence,i.rule_id
      FROM sections s JOIN section_identities i ON i.section_id=s.id AND i.policy_id=?
      WHERE s.parse_run_id=? ORDER BY s.ordinal''',(policy,parse['id']))]
    if len(rows)!=parse['section_count']:raise ValueError('Pinned section inventory changed')
    attach_contexts(conn,rows,context_policy)
    if not rows:
        out['reasons'].append('no section inventory; cannot establish absence');return out
    unidentified=[]
    for s in rows:
        high=s['kind']=='excerpt'
        excerpts=conn.execute('SELECT COUNT(*) FROM section_excerpts WHERE section_id=?',(s['id'],)).fetchone()[0]
        scopes=(['highlights'] if high else ['full-label']+(['highlights'] if excerpts else []))
        if scope!='both' and scope not in scopes:continue
        if scope!='full-label' and excerpts and product_part(s) and (s['section_confidence']=='unknown' or s['context_confidence']=='unknown'):
            unidentified.append(s['ordinal'])
        if s['context_confidence']!='anchored' and product_part(s) and (s['own_text'].strip() or s['title']) and scope!='highlights':
            unidentified.append(s['ordinal'])
        if s['canonical_code'] not in codes:
            if product_context(s) and s['section_confidence']=='unknown' and (s['own_text'].strip() or s['title']):
                unidentified.append(s['ordinal'])
            continue
        instance={**instance_info(s,version['version']),'section_instance':s['id'],
          'source_code':s['loinc_code'],'canonical_code':s['canonical_code'],
          'confidence':s['section_confidence'],'identity_rule':s['rule_id'],'scopes':scopes,
          'title':s['title'],
          'narrative_status':s['status'],'has_full_label_narrative':not high and s['kind']!='excerpt_only' and bool(s['narrative_xml']),
          'highlights_excerpts':excerpts,'diagnostics':json.loads(s['diagnostics_json']),
          'source_quirks':[q for q in quirks if q['section_instance']==s['id']]}
        representations=[]
        if scope!='highlights' or high:representations.append(instance)
        if scope in ('highlights','both'):
            for e in conn.execute('SELECT * FROM section_excerpts WHERE section_id=? ORDER BY ordinal',(s['id'],)):
                representations.append({**instance,**instance_info(s,version['version'],dict(e))})
        for rep in representations:
            destination=out['instances'] if product_context(rep) else out['unclassified_context'] if rep['context_confidence']!='anchored' else out['same_code_other_contexts']
            destination.append(rep)
            if rep['context_confidence']!='anchored':unidentified.append(s['ordinal'])
    if unidentified:out['reasons'].append('unidentified section instances: '+','.join(map(str,unidentified)))
    # A coarse inferred ancestor cannot exclude an unrepresented finer code.
    if not out['instances'] and any(product_context(s) and s['section_confidence']=='inferred' for s in rows):
        out['reasons'].append('inferred identities cannot exclude an unrepresented finer-grained section')
    if parse['status']=='extraction_failed':out['reasons'].append('document extraction failed; structural positive evidence only')
    out['checked_complete']=not out['reasons']
    out['unclassified_context_count']=len(out['unclassified_context'])
    out['state']='present' if out['instances'] else 'unknown' if out['reasons'] else 'checked-absent'
    return out


def section_first_present(conn,snapshot,setid,section,scope='full-label'):
    if scope not in ('full-label','highlights','both'):raise ValueError('Unknown scope')
    if section in ALIASES:codes=ALIASES[section]
    elif re.fullmatch(r'\d+-\d',section):codes=[section]
    else:raise ValueError('Section existence requires a section alias or LOINC code, not whole-document')
    manifest,base=load_snapshot(conn,snapshot)
    records=sorted((v for v in base['versions'] if v['setid']==setid.lower()),key=lambda v:v['version'])
    if not records:raise ValueError('Family is not in this snapshot')
    for v in records:
        for p in v['parse_runs']:
            if payload_digest(conn,p['id'])!=manifest['parse_payload_digests'].get(str(p['id'])):
                raise ValueError(f"Pinned query payload changed for version {v['version']}")
    states=[evaluate_section(conn,v,manifest['identity_policy'],codes,scope,manifest['source_quirks'],manifest['context_policy']) for v in records]
    first=next((v for v in states if v['state']=='present'),None)
    predecessor=next((v for v in states if first and v['version']==first['version']-1),None)
    predecessor_status=predecessor['state'] if predecessor else 'not-listed' if first else 'not-applicable'
    qualification='not found in checked versions' if all(v['state']=='checked-absent' for v in states) else 'cannot determine historical section appearance'
    if first:
        qualification=(f"observed section appearance in version {first['version']} (this family and scope)"
          if predecessor_status=='checked-absent' else 'present in the earliest available version containing this section; historical appearance unknown')
    runs=[]
    for v in states:
        if v['state']!='present':continue
        if runs and runs[-1][-1]['version']+1==v['version']:runs[-1].append(v)
        else:runs.append([v])
    intervals=[{'versions':[v['version'] for v in run],
      'DailyMed_publication_date_endpoints':[run[0]['published_date'],run[-1]['published_date']],
      'SPL_effective_date_endpoints':[run[0]['spl_effective_date'],run[-1]['spl_effective_date']],
      'open_at_snapshot_end':run[-1]['version']==states[-1]['version'],
      'continuity_claim':'consecutive observed numeric versions only, not a calendar/legal interval'} for run in runs]
    numbers={v['version'] for v in states}
    return {'query':'section-first-present','engine':ENGINE_VERSION,'family':setid.lower(),
      'family_title':manifest['family_names'][setid.lower()],'section':section,'loinc_codes':codes,'scope':scope,
      'family_label_status':manifest['family_label_status'][setid.lower()],
      'earliest':first,'qualification':qualification,'integer_predecessor':predecessor,
      'integer_predecessor_status':predecessor_status,'intervals':intervals,
      'route_failure_versions':[v['version'] for v in states if v['state']=='not_retrievable'],
      'unknown_versions':[v['version'] for v in states if v['state']=='unknown'],
      'unlisted_integer_gaps':ranges(set(range(1,max(numbers)+1))-numbers),
      'coverage':{'listed':len(records),'downloaded':sum(v['state']=='downloaded' for v in records),
        'parsed':sum(bool(v['parse_runs']) for v in records),'section_checkable':sum(v['checked_complete'] for v in states),
        'present':sum(v['state']=='present' for v in states),'not_retrievable_by_recorded_routes':sum(v['state']=='not_retrievable' for v in states),
        'unknown':sum(v['state']=='unknown' for v in states)},'versions':states,
      'archive_snapshot':snapshot,'source_archive_snapshot':manifest['archive_snapshot'],'identity_policy':manifest['identity_policy'],
      'context_policy':manifest['context_policy'],'section_definition':'code plus anchored full-label-body/highlights context only; unclassified evidence reported separately',
      'normalization':{'profile':'literal-v1','applies_to':'text preview only; structural existence has no phrase matching'},
      'limitations':['Product-scoped code/inferred-identity presence, not clinical validation of every section content.',
        'Source miscoding is retained; repeated instances are separate and human quirks do not reclassify them.',
        'A section may exist without extractable narrative; statuses are printed separately.',
        'Versions not retrieved by the recorded routes or not listed have no inferred content; dates are not FDA action or legal-applicability dates.']}


def render_section(result):
    lines=[f"Family: {result['family_title']} (setid {result['family']})",f"Section: {result['section']} ({result['loinc_codes']}); scope: {result['scope']}"]
    lines.insert(1,'Family label status: '+dumps(result['family_label_status'])+'; archive membership alone does not establish inactivity.')
    first=result['earliest']
    if first:lines.append(f"Earliest section presence found in available history: version {first['version']}; DailyMed publication date {first['published_date']}; SPL effective date {first['spl_effective_date']}.")
    else:lines.append('Earliest section presence: '+result['qualification']+'.')
    lines+=['Qualification: '+result['qualification'],
      f"Immediately preceding numeric version {('v'+str(first['version']-1)) if first else '(none)'}: {state_text(result['integer_predecessor_status'])}.",
      'All observed existence intervals (numeric versions):']
    lines += ['  '+json.dumps(i,ensure_ascii=False,sort_keys=True) for i in result['intervals']] or ['  none established']
    lines+=['Versions not retrievable by recorded route(s): '+(ranges(result['route_failure_versions']) and ', '.join('v'+str(v) for v in result['route_failure_versions']) or 'none'),
      'Unknown versions: '+(', '.join('v'+str(v) for v in result['unknown_versions']) or 'none'),
      'Unlisted integer gaps: '+(', '.join(result['unlisted_integer_gaps']) or 'none'),'Coverage: '+dumps({state_text(k):v for k,v in result['coverage'].items()}),
      'Per-version evidence (all retrieved versions cited):']
    for v in result['versions']:
        lines.append(f"  v{v['version']}: {state_text(v['state'])}; DailyMed publication date {v['published_date']}; SPL effective date {v['spl_effective_date']}; {'DATE ANOMALY' if v['date_anomaly'] else 'no date anomaly'}")
        if v['raw_sha256']:lines.append(f"    Cite: {v['setid']} + {v['version']} + {v['raw_sha256']}")
        for route in v['retrieval_routes']:
            lines.append('    Retrieval: '+route['wording']+'; '+dumps(route))
        for reason in v['reasons']:lines.append('    Uncertainty: '+reason)
        for warning in v['warnings']:lines.append('    Warning: '+warning)
        for e in v.get('retrieval_evidence',[]):lines.append('    Retrieval evidence (not document coverage): '+dumps(e))
        for instance in v['instances']:
            lines.append('    Instance: '+dumps(instance))
        for instance in v['same_code_other_contexts']:
            lines.append('    Same code in '+instance['section_context']+' (excluded from main answer): '+dumps(instance))
        lines.append('    Unclassified context count: '+str(v.get('unclassified_context_count',0)))
        for instance in v['unclassified_context']:lines.append('      Unclassified context (excluded from main answer): '+dumps(instance))
    lines+=['Archive snapshot: '+result['archive_snapshot'],'Source archive snapshot: '+result['source_archive_snapshot'],
      'Identity policy: '+result['identity_policy'],'Context policy: '+result['context_policy'],'Normalization: '+dumps(result['normalization']),'Limitations:']
    lines+=['  - '+s for s in result['limitations']]
    return '\n'.join(lines)+'\n'
