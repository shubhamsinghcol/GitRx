"""Local observed-publication views; frozen context assignments are read only."""
import argparse,difflib,json,re,sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from .db import connect
from .query import load_snapshot,ALIASES,effective_date,retrieval_summary,payload_digest
from .contexts import attach_contexts
from .normalize import normalize
from .presentation import family_title,retrieval_text
from .availability import route_failure

DEFAULT_ROOT=Path(__file__).resolve().parents[2]/'runtime/current'

def family_id(conn,value):
 rows=[dict(r) for r in conn.execute('SELECT setid,name FROM families ORDER BY setid')]
 exact=[r for r in rows if r['setid'].casefold()==value.casefold() or family_title(r['name']).casefold()==value.casefold()]
 matches=exact or [r for r in rows if family_title(r['name']).casefold().startswith(value.casefold()+' ') or family_title(r['name']).casefold().startswith(value.casefold()+' /')]
 if len(matches)!=1:raise ValueError('Family must match one family name or setid; matches: '+str(len(matches)))
 return matches[0]['setid'],family_title(matches[0]['name'])

def phrases_from(args):
 values=list(args.phrase or [])
 for name in args.any_of or []:
  x=json.loads(Path(name).read_bytes())
  if not isinstance(x,list) or not x or any(not isinstance(v,str) or not normalize(v) for v in x):raise ValueError('--any-of requires a nonempty JSON array of literal strings')
  values.extend(x)
 if any(not normalize(v) for v in values):raise ValueError('Empty literal phrase')
 return list(dict.fromkeys(values))

def date_info(v):
 return {'version':v['version'],'DailyMed_publication_dates':sorted({p['published_date'] for p in v['publication_observations'] if p['published_date']}),'SPL_effective_date':effective_date(v['effective_date']),'date_anomaly':bool(v['date_anomaly']),'retrievals':retrieval_summary(v),'raw_sha256':v['selected_sha256']}

def read_sections(conn,manifest,v,scope='full-label',sections=None,phrases=(),ignore_case=False):
 if v['state']!='downloaded':return [],route_failure(v)
 if len(v['parse_runs'])!=1:return [],'Parse inventory is ambiguous; no section text selected'
 parse=v['parse_runs'][0]
 if payload_digest(conn,parse['id'])!=manifest['parse_payload_digests'][str(parse['id'])]:raise RuntimeError('STOP_PINNED_PAYLOAD_HASH')
 rows=[dict(r) for r in conn.execute('''SELECT s.*,i.canonical_code FROM sections s LEFT JOIN section_identities i ON i.section_id=s.id AND i.policy_id=? WHERE s.parse_run_id=? ORDER BY s.ordinal''',(manifest['identity_policy'],parse['id']))]
 attach_contexts(conn,rows,manifest['context_policy']);selected=set();requested=set()
 for section in ([] if 'whole-document' in (sections or []) else sections or []):
  if section=='whole-document':continue
  if section in ALIASES:requested.update(ALIASES[section])
  elif re.fullmatch(r'\d+-\d',section):requested.add(section)
  else:raise ValueError('Unknown section scope: '+section)
 by_ordinal={s['ordinal']:s for s in rows}
 for s in rows:
  parent=s['parent_ordinal']
  if not requested or s['loinc_code'] in requested or s['canonical_code'] in requested or parent in selected:selected.add(s['ordinal'])
 result=[]
 for s in rows:
  if s['ordinal'] not in selected:continue
  reps=[]
  if scope in ('full-label','both'):reps.append(('native section',s['own_text'],s['source_xpath'],s['section_context'],s['context_confidence']))
  if scope in ('highlights','both'):
   for e in conn.execute('SELECT * FROM section_excerpts WHERE section_id=? ORDER BY ordinal',(s['id'],)):
    confidence='unknown' if s['context_confidence']=='unknown' else 'anchored'
    reps.append(('source excerpt '+str(e['ordinal']),e['extracted_text'],e['source_xpath'],'highlights',confidence))
  for representation,text,xpath,context,confidence in reps:
   if phrases and not any(normalize(p,ignore_case) in normalize(text,ignore_case) for p in phrases):continue
   result.append({'instance_index':s['ordinal'],'title':s['title'],'source_code':s['loinc_code'],'canonical_code':s['canonical_code'],'ancestry':s['ancestry_path'],'source_xpath':xpath,'representation':representation,'context':context,'confidence':confidence,'text':text,'parse_status':s['status']})
 return result,None

def base(conn,root,snapshot,family):
 snapshot=snapshot or (Path(root)/'reports/QUERY-SNAPSHOT-ID.txt').read_text().strip();manifest,archive=load_snapshot(conn,snapshot);sid,name=family_id(conn,family);versions=sorted((v for v in archive['versions'] if v['setid']==sid),key=lambda v:v['version'])
 if not versions:raise ValueError('Family has no versions in this snapshot')
 return {'family':name,'setid':sid,'query_snapshot':snapshot,'observation':'DailyMed-published record observed in the captured listing; historical completeness and FDA approval are not established'},manifest,versions

def show(conn,root,family,as_of,*,snapshot=None,scope='full-label',sections=None,phrases=(),ignore_case=False):
 cutoff=date.fromisoformat(as_of).isoformat();result,manifest,versions=base(conn,root,snapshot,family)
 eligible=[v for v in versions if any(p['published_date'] and p['published_date']<=cutoff for p in v['publication_observations'])]
 selected=eligible[-1] if eligible else None;maximum=selected['version'] if selected else 0;listed={v['version'] for v in versions}
 result.update(command='show',DailyMed_publication_cutoff=cutoff,selection_rule='Highest numeric version among records with an observed DailyMed publication date on or before cutoff; all eligible versions remain listed',published_by_date=[date_info(v) for v in eligible],missing_publication_dates=[v['version'] for v in versions if not any(p['published_date'] for p in v['publication_observations'])],unlisted_version_numbers=[n for n in range(1,maximum+1) if n not in listed],date_anomaly_versions=[v['version'] for v in versions if v['date_anomaly']],selected=date_info(selected) if selected else None,scope=scope,section_scopes=sections or ['whole-document'],any_of=list(phrases),sections=[],unanchored_results=[],retrieval_failures=[])
 for v in eligible:
  if v['state']!='downloaded':result['retrieval_failures'].append({'version':v['version'],'reason':route_failure(v)})
 if selected:
  rows,error=read_sections(conn,manifest,selected,scope,sections,phrases,ignore_case)
  if error:result['retrieval_failures'].append({'version':selected['version'],'reason':error})
  for row in rows:result['sections' if row['confidence']=='anchored' else 'unanchored_results'].append(row)
 return result

def word_diff(a,b):
 left=re.findall(r'\S+',a);right=re.findall(r'\S+',b);out=[]
 for op,a1,a2,b1,b2 in difflib.SequenceMatcher(None,left,right,autojunk=False).get_opcodes():
  out.append({'operation':op,'before':' '.join(left[a1:a2]),'after':' '.join(right[b1:b2])})
 return out

def diff(conn,root,family,a,b,*,snapshot=None,scope='full-label',sections=None,phrases=(),ignore_case=False):
 result,manifest,versions=base(conn,root,snapshot,family);sides=[]
 for number in (a,b):
  v=next((v for v in versions if v['version']==number),None)
  if v is None:sides.append((None,[],'not retrievable by history listing: v'+str(number)+' is unlisted'))
  else:
   rows,error=read_sections(conn,manifest,v,scope,sections,(),ignore_case);sides.append((v,rows,error))
 result.update(command='diff',version_A=a,version_B=b,dates_A=date_info(sides[0][0]) if sides[0][0] else None,dates_B=date_info(sides[1][0]) if sides[1][0] else None,scope=scope,section_scopes=sections or ['whole-document'],any_of=list(phrases),pairing_rule='Source/canonical code (otherwise normalized title) and representation, paired in document occurrence order; not asserted as durable section identity',sections=[],unanchored_results=[],retrieval_failures=[{'version':number,'reason':side[2]} for number,side in zip((a,b),sides) if side[2]],unlisted_version_numbers=[n for n in range(min(a,b),max(a,b)+1) if n not in {v['version'] for v in versions}])
 if result['retrieval_failures']:
  result['comparison_status']='declined: a complete version-side inventory is not retrievable or not established'
  return result
 def index(rows):
  indexed={};count=defaultdict(int)
  for s in rows:
   root=(s['canonical_code'] or s['source_code'] or normalize(s['title'] or ''),s['representation']);count[root]+=1;indexed[(*root,count[root])]=s
  return indexed
 left=index(sides[0][1]);right=index(sides[1][1])
 for key in sorted(left.keys()|right.keys(),key=str):
  x=left.get(key);y=right.get(key);texts=((x or {}).get('text',''),(y or {}).get('text',''))
  if phrases and not any(normalize(p,ignore_case) in normalize(text,ignore_case) for p in phrases for text in texts):continue
  entry={'pair_key':list(key),'before':x,'after':y,'word_diff':word_diff(*texts),'changed':texts[0]!=texts[1]}
  anchored=all(s['confidence']=='anchored' for s in (x,y) if s is not None)
  result['sections' if anchored else 'unanchored_results'].append(entry)
 return result

def render(result):
 lines=[result['family']+' | setid '+result['setid'],result['observation']]
 if result['command']=='show':
  lines+=['DailyMed publication cutoff: '+result['DailyMed_publication_cutoff'],result['selection_rule'],'DailyMed-published versions by cutoff: '+(', '.join('v'+str(v['version']) for v in result['published_by_date']) or 'none'),'Date-anomaly versions: '+(', '.join(map(str,result['date_anomaly_versions'])) or 'none'),'Missing DailyMed publication dates: '+(', '.join(map(str,result['missing_publication_dates'])) or 'none')]
  records=[result['selected']] if result['selected'] else []
 else:lines.append('Word diff: v'+str(result['version_A'])+' → v'+str(result['version_B']));records=[v for v in (result['dates_A'],result['dates_B']) if v]
 for v in records:
  lines.append('v'+str(v['version'])+' | DailyMed publication date(s): '+(', '.join(v['DailyMed_publication_dates']) or 'not recorded')+' | SPL effective date: '+str(v['SPL_effective_date'])+' | '+('DATE ANOMALY' if v['date_anomaly'] else 'no date anomaly'))
  for route in v['retrievals']:lines.append('Retrieval: '+retrieval_text(route))
 lines.append('Unlisted version numbers: '+(', '.join(map(str,result['unlisted_version_numbers'])) or 'none'))
 for failure in result['retrieval_failures']:lines.append('v'+str(failure['version'])+': '+failure['reason'])
 for name,key in [('Anchored results','sections'),('Unanchored/default results — excluded from headline answers','unanchored_results')]:
  lines.append(name+':')
  if not result[key]:lines.append('  none')
  for row in result[key]:
   if result['command']=='show':lines+=['  Instance '+str(row['instance_index'])+' | '+str(row['title'] or '(untitled)')+' | '+row['context']+' / '+row['confidence']+' | ancestry '+row['ancestry']+' | '+row['representation'],row['text']]
   else:
    x=row['before'];y=row['after'];label=y or x;lines.append('  '+str(label['title'] or '(untitled)')+' | instances '+str(x['instance_index'] if x else 'absent')+' → '+str(y['instance_index'] if y else 'absent')+' | '+label['representation'])
    if not row['changed']:lines.append('    unchanged')
    for op in row['word_diff']:
     if op['operation']=='equal':lines.append('    '+op['before'])
     else:
      if op['before']:lines.append('  - '+op['before'])
      if op['after']:lines.append('  + '+op['after'])
 lines.append('Query snapshot: '+result['query_snapshot']);return '\n'.join(lines)+'\n'

def parser():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,default=DEFAULT_ROOT);sub=p.add_subparsers(dest='command',required=True)
 for command in ('show','diff'):
  s=sub.add_parser(command);s.add_argument('family');s.add_argument('--snapshot');s.add_argument('--section',action='append');s.add_argument('--scope',choices=['full-label','highlights','both'],default='full-label');s.add_argument('--phrase',action='append');s.add_argument('--any-of',action='append');s.add_argument('--ignore-case',action='store_true');s.add_argument('--json',action='store_true')
  if command=='show':s.add_argument('--date',required=True)
  else:s.add_argument('vA',type=lambda x:int(x.lstrip('vV')));s.add_argument('vB',type=lambda x:int(x.lstrip('vV')))
 return p

def main(argv=None):
 p=parser();a=p.parse_args(argv);conn=connect(a.root/'data/gitrx.sqlite')
 try:
  options={'snapshot':a.snapshot,'scope':a.scope,'sections':a.section,'phrases':phrases_from(a),'ignore_case':a.ignore_case}
  result=show(conn,a.root,a.family,a.date,**options) if a.command=='show' else diff(conn,a.root,a.family,a.vA,a.vB,**options)
 except ValueError as exc:p.error(str(exc))
 finally:conn.close()
 print(json.dumps(result,ensure_ascii=False,indent=2) if a.json else render(result),end='\n' if a.json else '')
if __name__=='__main__':main()
