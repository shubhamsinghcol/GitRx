"""Source-native change observations, with bounded pairing and literal edit search."""
import base64
from collections import Counter
from datetime import date
import difflib
import hashlib
import json
import re
import unicodedata
from gitrx_services.section_diff_v2 import match_sections, word_runs

POLICY = 'gitrx-native-changes-1'
ALIGNMENT_PRODUCT = 250000
MAX_ALIGNMENT_CHARACTERS = 200000


def prepare(rows, code=None):
    """Same code/system and coded-ancestry input preparation as native compare."""
    by_ordinal={r['ordinal']:r for r in rows}; output=[]
    for row in rows:
        if code is not None and row.get('loinc_code')!=code:continue
        parents=[];parent=row.get('parent_ordinal');seen=set()
        while parent is not None and parent not in seen:
            seen.add(parent);p=by_ordinal.get(parent)
            if p is None:break
            if p.get('loinc_code'):parents.append((p.get('code_system'),p['loinc_code']))
            parent=p.get('parent_ordinal')
        output.append(dict(row,coded_ancestry=tuple(reversed(parents))))
    return output


def pairs(reader,sid,a,b,code=None):
    left,ca=reader.read(sid,a);right,cb=reader.read(sid,b)
    left,right=prepare(left,code),prepare(right,code)
    return left,right,match_sections(left,right),[ca,cb]


def _location(row):
    return None if row is None else {k:row.get(k) for k in ('ordinal','loinc_code','code_system','title','parent_ordinal','ancestry_path')}


def _flags(one,two):
    a=(one or {}).get('own_text') or '';b=(two or {}).get('own_text') or ''
    source_known=one is not None and two is not None and isinstance(one.get('source_xml'),str) and isinstance(two.get('source_xml'),str)
    return {'content_changed':' '.join(a.split())!=' '.join(b.split()),
            'raw_text_changed':a!=b,
            'heading_changed':one is not None and two is not None and one.get('title')!=two.get('title'),
            'position_changed':one is not None and two is not None and any(one.get(k)!=two.get(k) for k in ('ordinal','parent_ordinal','ancestry_path')),
            'source_representation_changed':one.get('source_xml')!=two.get('source_xml') if source_known else None,
            'unmatched_or_ambiguous':one is None or two is None}


def _bound_text(runs,budget):
    output=[];truncated=False
    for run in runs:
        item=dict(run)
        for key in ('before','after'):
            value=item[key];item[key]=value[:budget];budget-=len(item[key])
            truncated=truncated or len(item[key])<len(value)
        output.append(item)
        if truncated:break
    return output,budget,truncated


def _code(code):
    if code is not None and (not isinstance(code,str) or not code or len(code)>500):raise ValueError('Invalid source code selector')


def details(query,sid,version,previous=None,code=None,limit=50,offset=0,max_chars=16000):
    _code(code)
    if type(version) is not int or version<1 or previous is not None and (type(previous) is not int or previous<1 or previous>=version):raise ValueError('Use a positive version and an earlier predecessor')
    if type(limit) is not int or not 1<=limit<=200 or type(offset) is not int or offset<0 or type(max_chars) is not int or not 1<=max_chars<=200000:raise ValueError('Invalid change-detail pagination bounds')
    sid=query._sid(sid);versions=query.reader.versions(sid);by_version={v['version']:v for v in versions}
    if version not in by_version:raise ValueError('Version outside selected release')
    if previous is None:previous=max((n for n in by_version if n<version),default=None)
    if previous is None:
        _,citation=query.reader.read(sid,version)
        return query.envelope(setid=sid,version=version,previous_version=None,baseline=True,changes=[],summary={},citations=[citation],next_offset=None,
            meaning='Earliest captured version has no captured predecessor; it is a baseline, not proof of addition.')
    if previous not in by_version:raise ValueError('Predecessor outside selected release')
    left,right,matched,citations=pairs(query.reader,sid,previous,version,code)
    summary=Counter();rows=[];budget=max_chars
    for number,(i,j,rule) in enumerate(matched):
        one=left[i] if i is not None else None;two=right[j] if j is not None else None
        flags=_flags(one,two)
        for key,value in flags.items():
            if value is True:summary[key]+=1
        if not offset<=number<offset+limit:continue
        a=(one or {}).get('own_text') or '';b=(two or {}).get('own_text') or ''
        runs,budget,truncated=_bound_text(word_runs(a,b),budget)
        rows.append({'before':_location(one),'after':_location(two),'match_rule':rule,**flags,'word_diff':runs,'truncated':truncated,
            'before_text_sha256':hashlib.sha256(a.encode()).hexdigest(),'after_text_sha256':hashlib.sha256(b.encode()).hexdigest(),
            'before_source_xml_sha256':hashlib.sha256(one['source_xml'].encode()).hexdigest() if one is not None and isinstance(one.get('source_xml'),str) else None,
            'after_source_xml_sha256':hashlib.sha256(two['source_xml'].encode()).hexdigest() if two is not None and isinstance(two.get('source_xml'),str) else None})
    end=offset+len(rows)
    intermediate=sum(previous<n<version for n in by_version)
    missing=version-previous-1-intermediate
    return query.envelope(setid=sid,version=version,previous_version=previous,baseline=False,
        predecessor_selection='greatest lower captured version unless explicit predecessor supplied',
        version_metadata=by_version[version],previous_metadata=by_version[previous],citations=citations,
        numeric_gap={'after_version':previous,'before_version':version,'unobserved_integer_count':missing} if missing else None,
        intermediate_captured_version_count=intermediate,
        summary=dict(summary,total_comparisons=len(matched)),changes=rows,total_comparisons=len(matched),offset=offset,next_offset=end if end<len(matched) else None,
        meaning='Observed native endpoint changes. Unmatched occurrences are ambiguous presence, not proven additions/removals. Token equality is not clinical equivalence; incomplete extraction and gaps limit conclusions.')


def _spans(a,b):
    """Exact edit ranges; trim equal surroundings before bounded alignment."""
    if a==b:return [],[]
    if len(a)+len(b)>MAX_ALIGNMENT_CHARACTERS:return None
    if not a:return [(0,0)],[(0,len(b))]
    if not b:return [(0,len(a))],[(0,0)]
    prefix=0
    for x,y in zip(a,b):
        if x!=y:break
        prefix+=1
    suffix=0
    while suffix<min(len(a),len(b))-prefix and a[len(a)-suffix-1]==b[len(b)-suffix-1]:suffix+=1
    x=a[prefix:len(a)-suffix if suffix else len(a)]
    y=b[prefix:len(b)-suffix if suffix else len(b)]
    aa=list(re.finditer(r'\s+|\S+',x));bb=list(re.finditer(r'\s+|\S+',y))
    if len(aa)*len(bb)>ALIGNMENT_PRODUCT:return None
    sa,sb=[],[]
    for op,i,j,k,l in difflib.SequenceMatcher(None,[m.group() for m in aa],[m.group() for m in bb],autojunk=False).get_opcodes():
        if op=='equal':continue
        a0=aa[i].start() if i<len(aa) else len(x);a1=aa[j-1].end() if j>i else a0
        b0=bb[k].start() if k<len(bb) else len(y);b1=bb[l-1].end() if l>k else b0
        one,two=x[a0:a1],y[b0:b1]
        if len(one)*len(two)>ALIGNMENT_PRODUCT:return None
        for sub,mi,mj,nk,nl in difflib.SequenceMatcher(None,one,two,autojunk=False).get_opcodes():
            if sub=='equal':continue
            sa.append((prefix+a0+mi,prefix+a0+mj))
            sb.append((prefix+b0+nk,prefix+b0+nl))
    return sa,sb


def _matches(text,phrase,ignore_case,spans):
    folded=text.casefold() if ignore_case else text;needle=phrase.casefold() if ignore_case else phrase
    found=[];count=0;at=0;source_index=0;folded_position=0
    while True:
        i=folded.find(needle,at)
        if i<0:break
        j=i+len(needle)
        if ignore_case:
            # Streaming codepoint map; do not allocate one integer per text character.
            while source_index<len(text) and folded_position+len(text[source_index].casefold())<=i:
                folded_position+=len(text[source_index].casefold());source_index+=1
            start=source_index;end=start;through=folded_position
            while end<len(text) and through<j:through+=len(text[end].casefold());end+=1
        else:start,end=i,j
        if any((start<x<end if x==y else start<y and end>x) for x,y in spans):
            count+=1
            if len(found)<20:
                excerpt=max(0,start-120);found.append({'match_start':start,'match_end':end,'excerpt_offset':excerpt,'text_excerpt':text[excerpt:excerpt+500]})
        at=i+1
    return found,count


def _day(value):
    if value is None:return None
    try:
        result=date.fromisoformat(value)
        if result.isoformat()!=value:raise ValueError()
        return result
    except (ValueError,TypeError):raise ValueError('Use YYYY-MM-DD date bounds') from None


def _token(binding,transition,pair):
    state={'binding':hashlib.sha256(json.dumps(binding,sort_keys=True).encode()).hexdigest(),'transition':transition,'pair':pair}
    return base64.urlsafe_b64encode(json.dumps(state,separators=(',',':')).encode()).decode()


def search(query,sid,phrase,code=None,ignore_case=True,start=None,end=None,limit=25,cursor=None,max_transitions=50):
    _code(code);lo,hi=_day(start),_day(end)
    if lo and hi and lo>hi:raise ValueError('Start must not follow end')
    if not isinstance(phrase,str) or not phrase.strip() or len(phrase)>500 or type(ignore_case) is not bool:raise ValueError('Invalid literal phrase/case selection')
    if type(limit) is not int or not 1<=limit<=200 or type(max_transitions) is not int or not 1<=max_transitions<=200:raise ValueError('Invalid transition search bounds')
    sid=query._sid(sid);versions=query.reader.versions(sid)
    if len(versions)>10000:raise ValueError('Family exceeds 10000 captured versions')
    binding={'policy':POLICY,'snapshot':query.reader.snapshot,'metadata':getattr(query.reader,'evidence_metadata_sha256',None),
             'unicode':unicodedata.unidata_version,'setid':sid,'phrase':phrase,'code':code,'ignore_case':ignore_case,'start':start,'end':end}
    ti,pi=0,0
    if cursor is not None:
        try:
            if not isinstance(cursor,str) or len(cursor)>4000:raise ValueError()
            state=json.loads(base64.b64decode(cursor.encode('ascii'),altchars=b'-_',validate=True))
            ti,pi=state['transition'],state['pair']
            if state!=json.loads(base64.urlsafe_b64decode(_token(binding,ti,pi))) or type(ti) is not int or type(pi) is not int or min(ti,pi)<0 or ti>max(0,len(versions)-1):raise ValueError()
        except (ValueError,TypeError,KeyError,UnicodeError,RecursionError):raise ValueError('Invalid changed-text cursor or query/release/metadata mismatch') from None
    hits=[];scanned=0;unknown_dates=0;outside_dates=0;unexamined=0;extraction_uncertain=0;total=max(0,len(versions)-1)
    while ti<total and scanned<max_transitions:
        before,after=versions[ti],versions[ti+1];dates=[]
        for value in after.get('publication_dates',[]):
            try:dates.append(_day(value))
            except ValueError:pass
        dates=[d for d in dates if d is not None]
        if lo or hi:
            if not dates:unknown_dates+=1;ti+=1;pi=0;scanned+=1;continue
            if not any((lo is None or d>=lo) and (hi is None or d<=hi) for d in dates):outside_dates+=1;ti+=1;pi=0;scanned+=1;continue
        left,right,matched,citations=pairs(query.reader,sid,before['version'],after['version'],code)
        if any(c.get('evidence',{}).get('extraction',{}).get('status')!='complete' for c in citations):extraction_uncertain+=1
        if pi>len(matched):raise ValueError('Cursor pair is outside transition')
        scanned+=1
        while pi<len(matched):
            i,j,rule=matched[pi];one=left[i] if i is not None else None;two=right[j] if j is not None else None
            a=(one or {}).get('own_text') or '';b=(two or {}).get('own_text') or ''
            flags=_flags(one,two)
            if not flags['raw_text_changed']:pi+=1;continue
            ranges=_spans(a,b)
            if ranges is None:unexamined+=1;pi+=1;continue
            matches=[];count=0
            for side,text,spans in [('before',a,ranges[0]),('after',b,ranges[1])]:
                result,n=_matches(text,phrase,ignore_case,spans);count+=n
                matches.extend(dict(m,side=side) for m in result[:max(0,20-len(matches))])
            if count:
                if len(hits)>=limit:break
                hits.append({'previous_version':before['version'],'version':after['version'],'publication_dates':after.get('publication_dates',[]),
                    'before':_location(one),'after':_location(two),'match_rule':rule,**flags,'citations':citations,
                    'gap':after['version']-before['version']>1,'unobserved_integer_count':max(0,after['version']-before['version']-1),
                    'matches':matches,'matching_spans':count,'matches_truncated':count>len(matches)})
            pi+=1
        if pi<len(matched):break
        ti+=1;pi=0
    partial=ti<total
    return query.envelope(setid=sid,phrase=phrase,source_code=code,ignore_case=ignore_case,start=start,end=end,
        policy=POLICY,hits=hits,transitions_scanned=scanned,total_captured_transitions=total,
        partial=partial,continuation=_token(binding,ti,pi) if partial else None,
        date_unknown_transitions_excluded=unknown_dates,date_outside_transitions_excluded=outside_dates,
        unexamined_occurrences=unexamined,extraction_uncertain_transitions=extraction_uncertain,
        changed_text_coverage_complete=cursor is None and not partial and unexamined==0 and unknown_dates==0 and extraction_uncertain==0,
        selected_extracted_text_scan_complete=cursor is None and not partial and unexamined==0 and unknown_dates==0,
        page_changed_text_coverage_complete=unexamined==0 and unknown_dates==0 and extraction_uncertain==0,coverage_scope='Exclusion counts cover this page only. A continuation does not attest earlier pages; combine all pages and exclusions.',
        scope='One family; consecutive captured transitions, excluding baseline. Date filters use any valid observed publication date of the later endpoint, inclusively.',
        meaning='Literal source-text spans intersecting aligned character edits, not commit notes or unchanged context. Ambiguous unmatched occurrences are not proven additions/removals. Gaps, partial extraction and unexamined large alignments limit conclusions. Coverage counters apply to this page only.')
