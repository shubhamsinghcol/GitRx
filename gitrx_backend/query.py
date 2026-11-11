"""Shared bounded native queries, independent of storage and interpretation."""
import difflib
from datetime import date
from itertools import islice
import hashlib
import time
import base64
import json
from .limits import bounded
from gitrx_services.section_diff_v2 import match_sections, word_runs
from .readers import canonical_sid

class Query:
    def __init__(self,reader):self.reader=reader
    @bounded
    def verify_citation(self,sid,version,raw_sha256,snapshot,ordinal=None,quote=None):
        from .research import verify
        return verify(self,sid,version,raw_sha256,snapshot,ordinal,quote)
    @bounded
    def installation_plan(self,setids):
        from .research import installation_plan
        return installation_plan(self,setids)
    @bounded
    def change_records(self,sid,to_version=None,from_version=None,group=None,limit=25,offset=0,max_chars=16000):
        from .change_records import records
        return records(self,sid,to_version,from_version,group,limit,offset,max_chars)
    @bounded
    def inventory_on_date(self,setids=None,on_date=None,limit=50,offset=0,all_families=False):
        from .research import dated_inventory
        return dated_inventory(self,setids,on_date,limit,offset,all_families)
    @bounded
    def token_provenance(self,sid,version,ordinal,limit=100,offset=0):
        from .research import provenance
        return provenance(self,sid,version,ordinal,limit,offset)
    def envelope(self,**fields):
        scope=({'uninstalled_shards':list(self.reader.uninstalled_shards),
                'served_scope_limit':'Registered families in uninstalled shards are unavailable, never zero-result evidence'}
               if getattr(self.reader,'uninstalled_shards',None) else {})
        return {'format':'gitrx-native-v1','snapshot':self.reader.snapshot,**scope,
                'context':'unclassified native source','historical_completeness_claim':False,
                'extraction_completeness':'Not established by native queries; partial parses may omit source content',**fields}
    @bounded
    def describe_release(self):
        from .discovery import describe
        return self.envelope(**describe(self.reader))

    @bounded
    def version_changes(self,sid,version,previous=None,code=None,limit=50,offset=0,max_chars=16000):
        from .changes import details
        return details(self,sid,version,previous,code,limit,offset,max_chars)

    @bounded
    def search_changes(self,sid,phrase,code=None,ignore_case=True,start=None,end=None,limit=25,cursor=None,max_transitions=50):
        from .changes import search
        return search(self,sid,phrase,code,ignore_case,start,end,limit,cursor,max_transitions)

    def evidence(self,sid,version):
        if hasattr(self.reader,'evidence'):
            return self.reader.evidence(sid,version)
        return {'metadata_status':'unavailable','extraction':{'status':'unknown','diagnostics':[]}}

    @bounded
    def phrase_history(self,sid,phrase,code=None,ignore_case=True,limit=50,offset=0,cursor=None):
        from .phrase_history import history
        return history(self,sid,phrase,code,ignore_case,limit,offset,cursor)

    @bounded
    def families(self,search='',limit=25,offset=0):
        if not 1<=limit<=200 or offset<0 or len(search)>200:raise ValueError('Invalid family search bounds')
        rows=[r for r in self.reader.families() if search.casefold() in (r['title']+' '+r['setid']).casefold()]
        page=rows[offset:offset+limit]
        return self.envelope(selection_aid_only=True,families=page,total_matches=len(rows),next_offset=offset+len(page) if offset+len(page)<len(rows) else None)
    @bounded
    def resolve(self,family):
        try:
            sid=canonical_sid(family)
            if hasattr(self.reader,'family'):
                one=self.reader.family(sid);rows=[one] if one else []
            else:rows=[r for r in self.reader.families() if r['setid']==sid]
        except ValueError:
            rows=self.reader.families();exact=[r for r in rows if r['title'].casefold()==family.casefold()]
            rows=exact or [r for r in rows if family.casefold() in r['title'].casefold()]
        if not family.strip():raise ValueError('Provide a family name or setid')
        return self.envelope(candidates=rows[:200],total_candidates=len(rows),selected=rows[0] if len(rows)==1 else None,
                             selection_aid_only=True)
    def _sid(self,family):
        result=self.resolve(family)
        if result['selected'] is None:
            raise ValueError('Family is unknown or ambiguous; choose a setid from resolve: '+str(result['candidates'][:8]))
        return result['selected']['setid']
    @bounded
    def versions(self,sid,limit=200,offset=0):
        if type(limit) is not int or not 1<=limit<=200 or type(offset) is not int or offset<0:
            raise ValueError('Invalid version pagination bounds')
        sid=self._sid(sid);rows=self.reader.versions(sid);page=rows[offset:offset+limit]
        end=offset+len(page)
        page=[dict(row,evidence=self.evidence(sid,row['version'])) for row in page]
        return self.envelope(setid=sid,versions=page,total_captured_versions=len(rows),offset=offset,
                             next_offset=end if end<len(rows) else None)
    @bounded
    def show(self,sid,version,code=None,ordinal=None,max_chars=16000,offset=0,section_limit=100,section_offset=0):
        if type(version) is not int or version<1 or not 1<=max_chars<=200000 or offset<0 or (offset and ordinal is None):raise ValueError('Invalid version/text pagination bounds')
        if type(section_limit) is not int or not 1<=section_limit<=200 or type(section_offset) is not int or section_offset<0:
            raise ValueError('Invalid occurrence pagination bounds')
        sid=self._sid(sid);sections,citation=self.reader.read(sid,version)
        selected=[s for s in sections if (code is None or s['loinc_code']==code) and (ordinal is None or s['ordinal']==ordinal)]
        result=[];budget=max_chars
        for s in selected[section_offset:section_offset+section_limit]:
            text=s['own_text'] or '';start=offset if ordinal is not None else 0;excerpt=text[start:start+budget]
            result.append({'ordinal':s['ordinal'],'source_code':s['loinc_code'],'code_system':s['code_system'],
              'title':s['title'],'parent_ordinal':s['parent_ordinal'],'ancestry':s['ancestry_path'],
              'text':excerpt,'text_characters':len(text),'offset':start,'truncated':start+len(excerpt)<len(text),
              'next_offset':start+len(excerpt) if start+len(excerpt)<len(text) else None,**(s.get('extra') or {})})
            budget-=len(excerpt)
        versions=self.reader.versions(sid);meta=next(r for r in versions if r['version']==version)
        return self.envelope(setid=sid,version=version,citation=citation,sections=result,total_section_instances=len(sections),
          publication_dates=meta['publication_dates'],effective_date=meta['effective_date'],evidence=self.evidence(sid,version),
          matching_section_instances=len(selected),section_offset=section_offset,
          next_section_offset=section_offset+len(result) if section_offset+len(result)<len(selected) else None)
    @bounded
    def diff(self,sid,a,b,ordinal,max_chars=16000):
        if not 1<=max_chars<=200000:raise ValueError('Invalid diff character bound')
        sid=self._sid(sid);left,ca=self.reader.read(sid,a);right,cb=self.reader.read(sid,b)
        one=next((s for s in left if s['ordinal']==ordinal),None);two=next((s for s in right if s['ordinal']==ordinal),None)
        # Streaming output bound; the full native inputs are independently blob-bounded.
        lines=difflib.unified_diff((one['own_text'] or '').splitlines(keepends=True) if one else [],(two['own_text'] or '').splitlines(keepends=True) if two else [],fromfile=str(a),tofile=str(b))
        output='';truncated=False
        for line in lines:
            remaining=max_chars-len(output)
            output+=line[:remaining]
            if len(line)>remaining:truncated=True;break
        return self.envelope(setid=sid,ordinal=ordinal,version_a=a,version_b=b,citations=[ca,cb],
          diagnostic='Same ordinal only; section identity across versions is not asserted',present_a=one is not None,present_b=two is not None,diff=output,truncated=truncated)
    @bounded
    def search(self,phrase,limit=25,family_offset=0,max_families=128,section_offset=0,time_budget_seconds=2,cursor=None):
        if not isinstance(phrase,str) or not phrase or len(phrase)>500 or not 1<=limit<=200 or not 1<=max_families<=7239 or family_offset<0 or section_offset<0:raise ValueError('Invalid literal search bounds')
        if type(time_budget_seconds) not in (int,float) or not .01<=time_budget_seconds<=55:raise ValueError('time_budget_seconds must be 0.01..55')
        if cursor is not None:
            if family_offset or section_offset:raise ValueError('Choose cursor or offsets')
            try:
                if len(cursor)>4000:raise ValueError()
                state=json.loads(base64.urlsafe_b64decode(cursor.encode()))
                if state['snapshot']!=self.reader.snapshot or state['phrase']!=phrase:raise ValueError()
                family_offset,section_offset=state['family'],state['section']
                if type(family_offset) is not int or type(section_offset) is not int or min(family_offset,section_offset)<0:raise ValueError()
            except (ValueError,KeyError,TypeError,UnicodeError):raise ValueError('Invalid cursor or query/release mismatch') from None
        start=time.monotonic();families=self.reader.families();hits=[];scanned=0;reason=None
        fi=family_offset;si=section_offset
        end=min(len(families),family_offset+max_families)
        while fi<end:
            if time.monotonic()-start>=time_budget_seconds:reason='time_budget';break
            sid=families[fi]['setid'];versions=self.reader.versions(sid)
            if not versions:fi+=1;si=0;continue
            version=versions[-1]['version'];scanned+=1
            # A verified optional text projection avoids decompressing nonmatches.
            candidates=self.reader.search_candidates(sid,version,phrase,si) if hasattr(self.reader,'search_candidates') else None
            if candidates==[]:fi+=1;si=0;continue
            sections,cite=self.reader.read(sid,version)
            indices=candidates if candidates is not None else range(si,len(sections))
            stopped=False
            for index in indices:
                si=index
                if time.monotonic()-start>=time_budget_seconds:reason='time_budget';stopped=True;break
                s=sections[index];text=s['own_text'] or ''
                if phrase.casefold() in text.casefold():
                    if len(hits)==limit:reason='result_limit';stopped=True;break
                    hits.append({'setid':sid,'version':version,'ordinal':s['ordinal'],'source_code':s['loinc_code'],
                                 'title':s['title'],'text_preview':text[:500],'citation':cite})
                si=index+1
            if stopped:break
            fi+=1;si=0
        partial=fi<len(families)
        if reason is None and partial:reason='family_limit'
        token=base64.urlsafe_b64encode(json.dumps({'snapshot':self.reader.snapshot,'phrase':phrase,'family':fi,'section':si},separators=(',',':')).encode()).decode() if partial else None
        return self.envelope(scope='Greatest SPL version per selected family; native own_text only',phrase=phrase,hits=hits,
          families_scanned=scanned,total_families=len(families),result_limit_reached=reason=='result_limit',
          next_family_offset=fi if partial else None,partial=partial,next_section_offset=si if partial else 0,
          continuation=token,stop_reason=reason,time_budget_seconds=time_budget_seconds)

    @bounded
    def show_date(self,sid,on_date,code=None,ordinal=None,max_chars=16000,offset=0,section_limit=100,section_offset=0):
        if type(section_limit) is not int or not 1<=section_limit<=200 or type(section_offset) is not int or section_offset<0:
            raise ValueError('Invalid occurrence pagination bounds')
        cutoff=date.fromisoformat(on_date)
        if cutoff.isoformat()!=on_date:raise ValueError('Use YYYY-MM-DD')
        sid=self._sid(sid);versions=self.reader.versions(sid)
        eligible=[];undated=[];invalid=[];observed=[]
        for row in versions:
            valid=[]
            for value in row['publication_dates']:
                try:valid.append(date.fromisoformat(value))
                except (ValueError,TypeError):invalid.append({'version':row['version'],'value':value})
            if not valid:undated.append(row['version'])
            else:
                observed.append((row['version'],min(valid)))
                if min(valid)<=cutoff:eligible.append(row['version'])
        selected=max(eligible) if eligible else None
        result=self.show(sid,selected,code,ordinal,max_chars,offset,section_limit,section_offset) if selected is not None else self.envelope(setid=sid,sections=[])
        result['date_selection']={'cutoff':on_date,'basis':'Recorded DailyMed publication observations; greatest eligible SPL version',
          'selected_version':selected,'eligible_versions':eligible,'versions_without_usable_publication_date':undated,
          'invalid_date_observations':invalid,'publication_order_conflict':any(a[1]>b[1] for a,b in zip(observed,observed[1:])),
          'meaning':'Captured published record, not legal applicability; unknown dates may prevent a definitive selection'}
        return result

    @bounded
    def compare(self,sid,a,b,code=None,max_chars=16000,limit=50,offset=0):
        if type(a) is not int or type(b) is not int or min(a,b)<1 or not 1<=max_chars<=200000 or not 1<=limit<=200 or offset<0:raise ValueError('Invalid comparison bounds')
        sid=self._sid(sid);left,ca=self.reader.read(sid,a);right,cb=self.reader.read(sid,b)
        from .changes import prepare
        left,right=prepare(left,code),prepare(right,code);pairs=match_sections(left,right);output=[];budget=max_chars
        def location(row):
            return None if row is None else {k:row.get(k) for k in ('ordinal','loinc_code','code_system','title','parent_ordinal','ancestry_path')}
        for i,j,rule in pairs[offset:offset+limit]:
            one=left[i] if i is not None else None;two=right[j] if j is not None else None
            text_a=(one or {}).get('own_text') or '';text_b=(two or {}).get('own_text') or ''
            runs=[];truncated=False
            for run in word_runs(text_a,text_b):
                item=dict(run)
                for field in ('before','after'):
                    value=item[field];item[field]=value[:budget];budget-=len(item[field])
                    truncated=truncated or len(item[field])<len(value)
                runs.append(item)
                if truncated:break
            output.append({'before':location(one),'after':location(two),'match_rule':rule,
              'content_changed':' '.join(text_a.split())!=' '.join(text_b.split()),'word_diff':runs,'truncated':truncated,
              'before_text_sha256':hashlib.sha256(text_a.encode()).hexdigest(),'after_text_sha256':hashlib.sha256(text_b.encode()).hexdigest()})
        next_offset=offset+len(output)
        return self.envelope(setid=sid,version_a=a,version_b=b,citations=[ca,cb],comparisons=output,total_comparisons=len(pairs),
          next_offset=next_offset if next_offset<len(pairs) else None,
          scope='Native section pairing by code/system and coded ancestry; ambiguous repeated occurrences remain unmatched')

    @bounded
    def first_observed(self,sid,phrase,code=None,ignore_case=True,max_versions=500):
        if not isinstance(phrase,str) or not phrase.strip() or len(phrase)>500 or not 1<=max_versions<=10000:raise ValueError('Invalid phrase/history bounds')
        sid=self._sid(sid);versions=self.reader.versions(sid);previous=None;first=None;scanned=[]
        needle=phrase.casefold() if ignore_case else phrase
        for row in versions[:max_versions]:
            sections,citation=self.reader.read(sid,row['version']);scanned.append(row['version']);matches=[]
            for s in sections:
                if code is not None and s['loinc_code']!=code:continue
                text=s['own_text'] or ''
                if needle in (text.casefold() if ignore_case else text):
                    folded_index=(text.casefold() if ignore_case else text).find(needle)
                    start=folded_index
                    if ignore_case:
                        position=0
                        for original_index,char in enumerate(text):
                            if position+len(char.casefold())>folded_index:start=original_index;break
                            position+=len(char.casefold())
                    excerpt_start=max(0,start-120)
                    matches.append({'match_start':start,'excerpt_offset':excerpt_start,'text_excerpt':text[excerpt_start:excerpt_start+500],'ordinal':s['ordinal'],'source_code':s['loinc_code'],'code_system':s['code_system'],'title':s['title'],'ancestry':s['ancestry_path'],'text_preview':text[:500]})
            if matches:
                first={'version':row['version'],'publication_dates':row['publication_dates'],'effective_date':row['effective_date'],
                       'citation':citation,'matches':matches[:200],'matching_occurrences':len(matches),'matches_truncated':len(matches)>200}
                break
            previous={'version':row['version'],'citation':citation,'phrase_present_in_selected_native_text':False}
        numbers={r['version'] for r in versions};last=max(numbers) if numbers else 0
        return self.envelope(setid=sid,phrase=phrase,source_code=code,ignore_case=ignore_case,first_observed=first,
          previous_captured_version=previous,versions_scanned=scanned,total_captured_versions=len(versions),
          scan_limited=first is None and len(scanned)<len(versions),
          missing_version_numbers=list(islice((n for n in range(1,last+1) if n not in numbers),1000)),
          meaning='First match in captured SPL-version order; not a proven addition date. Earlier unknown versions or incomplete extraction may contain the wording.')

    @bounded
    def coverage(self,sid):
        sid=self._sid(sid);versions=self.reader.versions(sid);numbers={r['version'] for r in versions};last=max(numbers) if numbers else 0
        return self.envelope(setid=sid,captured_versions=len(versions),lowest_captured_version=min(numbers) if numbers else None,
          highest_captured_version=last,missing_version_count=last-len(numbers),
          integer_gap_count=last-len(numbers),
          missing_version_definition='Unobserved integer positions, not necessarily known DailyMed inventory targets',
          inventory=self.reader.inventory(sid) if hasattr(self.reader,'inventory') else {'status':'unknown','known_target_count':None,'unknown_upstream_history':None},
          missing_version_numbers=list(islice((n for n in range(1,last+1) if n not in numbers),1000)),
          versions_without_publication_dates=[r['version'] for r in versions if not r['publication_dates']],
          scope='Selected native release only; unknown upstream history and extraction completeness are not inferred')

    @bounded
    def section_history(self,sid,code,max_versions=500,offset=0):
        if not code or not 1<=max_versions<=1000 or offset<0:raise ValueError('Invalid section-history bounds')
        sid=self._sid(sid);versions=self.reader.versions(sid);rows=[]
        for v in versions[offset:offset+max_versions]:
            sections,citation=self.reader.read(sid,v['version']);matches=[s for s in sections if s['loinc_code']==code]
            rows.append({'version':v['version'],'publication_dates':v['publication_dates'],'citation':citation,
                         'native_occurrences':len(matches),'locations':[{'ordinal':s['ordinal'],'title':s['title'],'ancestry':s['ancestry_path'],'code_system':s['code_system']} for s in matches[:200]],'locations_truncated':len(matches)>200})
        end=offset+len(rows)
        return self.envelope(setid=sid,source_code=code,history=rows,total_captured_versions=len(versions),next_offset=end if end<len(versions) else None,
          meaning='Captured source-code occurrences, not inferred product-section presence or a proven first-addition date')
