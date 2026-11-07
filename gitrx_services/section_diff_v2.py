"""Separate conservative section pairing; no archive mutation or context inference."""
from collections import defaultdict
import re,hashlib,difflib,json
SCHEMA='gitrx-section-diff-v2'
def text(value):return ' '.join(re.findall(r'\S+',value or ''))
def digest(value):return hashlib.sha256(value.encode()).hexdigest()
def group(row):
 code=row.get('loinc_code')
 return ('coded',row.get('code_system') or '',code) if code and code!='<uncoded>' else ('uncoded',tuple(row.get('coded_ancestry',())))
def match_sections(before,after):
 left=defaultdict(list);right=defaultdict(list);pairs=[]
 for i,r in enumerate(before):left[group(r)].append(i)
 for i,r in enumerate(after):right[group(r)].append(i)
 for key in sorted(left.keys()|right.keys(),key=str):
  a=list(left[key]);b=list(right[key])
  def pair_unique(fn,reason,allow_duplicates=False):
   aa=defaultdict(list);bb=defaultdict(list)
   for i in a:
    v=fn(before[i])
    if v is not None:aa[v].append(i)
   for j in b:
    v=fn(after[j])
    if v is not None:bb[v].append(j)
   for v in sorted(aa.keys()&bb.keys(),key=str):
    if not allow_duplicates and (len(aa[v])!=1 or len(bb[v])!=1):continue
    for i,j in zip(aa[v],bb[v]):
     pairs.append((i,j,reason+('-interchangeable' if len(aa[v])>1 or len(bb[v])>1 else '')));a.remove(i);b.remove(j)
  pair_unique(lambda r:text(r['own_text']) if key[0]=='coded' or text(r['own_text']) else None,'equal-normalized-text',True)
  pair_unique(lambda r:text(r.get('title')) or None,'unique-heading')
  if key[0]=='coded' and len(a)==len(b)==1:pairs.append((a.pop(),b.pop(),'sole-coded-residual'))
  pairs.extend((i,None,'unmatched-or-ambiguous') for i in a);pairs.extend((None,j,'unmatched-or-ambiguous') for j in b)
 return pairs

def diff_rows(before,after):
 result=[]
 for i,j,why in match_sections(before,after):
  a=before[i] if i is not None else None;b=after[j] if j is not None else None
  raw_a=(a or {}).get('own_text','');raw_b=(b or {}).get('own_text','');aa=text(raw_a);bb=text(raw_b)
  result.append({'before_index':i,'after_index':j,'match_rule':why,'group':group(a or b),'content_changed':aa!=bb,'presence_changed':a is None or b is None,'source_representation_changed':a is not None and b is not None and a.get('source_xml_sha256')!=b.get('source_xml_sha256'),'raw_text_changed':raw_a!=raw_b,'heading_changed':a is not None and b is not None and a.get('title')!=b.get('title'),'position_changed':a is not None and b is not None and a['ordinal']!=b['ordinal'],'before_text_sha256':digest(raw_a),'after_text_sha256':digest(raw_b),'before_normalized_sha256':digest(aa),'after_normalized_sha256':digest(bb)})
 return result

def word_runs(a,b):
 a=re.findall(r'\S+',a or '');b=re.findall(r'\S+',b or '')
 if len(a)*len(b)>250000:return [{'operation':'replace','before':' '.join(a),'after':' '.join(b),'bounded_fallback':True}]
 return [{'operation':op,'before':' '.join(a[i:j]),'after':' '.join(b[k:l])} for op,i,j,k,l in difflib.SequenceMatcher(None,a,b,autojunk=False).get_opcodes()]
