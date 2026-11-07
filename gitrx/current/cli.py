"""Current local CLI; immutable legacy modules remain replayable."""
import argparse,json,sys
from pathlib import Path
from . import query
from .tooling import DEFAULT_ROOT,family_id
from .db import connect

def main(argv=None):
 argv=list(sys.argv[1:] if argv is None else argv)
 root_parser=argparse.ArgumentParser(add_help=False);root_parser.add_argument('--root',type=Path,default=DEFAULT_ROOT);root,remaining=root_parser.parse_known_args(argv)
 if remaining and remaining[0] in ('show','diff'):
  from .tooling import main as tools_main
  return tools_main(['--root',str(root.root),*remaining])
 if remaining in ([],['--help'],['-h']):
  print('GitRx current: show FAMILY --date YYYY-MM-DD; diff FAMILY vA vB [--section ALIAS]; first-occurrence; section-first-present. All support section scoping; --phrase repeats or --any-of FILE.json supply literal alternatives. Default root: '+str(DEFAULT_ROOT));return
 normalized=[];i=0
 while i<len(remaining):
  if remaining[i]=='--any-of':
   values=json.loads(Path(remaining[i+1]).read_bytes())
   if not isinstance(values,list) or not values or any(not isinstance(s,str) or not s.strip() for s in values):raise ValueError('--any-of requires nonempty JSON string array')
   for value in values:normalized+=['--phrase',value]
   i+=2
  else:normalized.append(remaining[i]);i+=1
 remaining=normalized
 if len(remaining)>1 and not remaining[1].startswith('-'):
  c=connect(root.root/'data/gitrx.sqlite')
  try:remaining[1]=family_id(c,remaining[1])[0]
  finally:c.close()
 if remaining and remaining[0]=='section-first-present' and '--phrase' in remaining:
  p=argparse.ArgumentParser();p.add_argument('command');p.add_argument('family');p.add_argument('--section',required=True);p.add_argument('--scope',default='full-label');p.add_argument('--snapshot');p.add_argument('--phrase',action='append');p.add_argument('--ignore-case',action='store_true');p.add_argument('--json',action='store_true');p.add_argument('--format',choices=['text','json'],default='text');a=p.parse_args(remaining)
  c=connect(root.root/'data/gitrx.sqlite');snapshot=a.snapshot or (root.root/'reports/QUERY-SNAPSHOT-ID.txt').read_text().strip()
  try:
   from .section_history import section_first_present,render_section
   structural=section_first_present(c,snapshot,a.family,a.section,a.scope);literal=query.first_occurrence(c,snapshot,a.family,a.section,a.phrase,a.scope,a.ignore_case)
  finally:c.close()
  output={'family':structural['family_title'],'setid':a.family,'structural_existence':structural,'literal_any_of_presence':literal}
  print(json.dumps(output,ensure_ascii=False,indent=2) if a.json or a.format=='json' else render_section(structural)+'\nLiteral any-of presence (separate from structural existence):\n'+query.render(literal),end='\n' if a.json or a.format=='json' else '')
  return
 old=sys.argv;sys.argv=[old[0],'--root',str(root.root),*remaining]
 try:return query.main()
 finally:sys.argv=old
if __name__=='__main__':main()
