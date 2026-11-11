"""Bounded research helpers over pinned native records, never clinical claims."""
import difflib
import hashlib
import re
from datetime import date
from collections import Counter
from .changes import prepare
from gitrx_services.section_diff_v2 import match_sections
from .readers import canonical_sid

PROVENANCE_POLICY='native-conservative-token-replay-1'
MAX_TOKENS=10000
MAX_TOKEN_PRODUCT=250000


def verify(query,sid,version,raw_sha256,snapshot,ordinal=None,quote=None):
    if snapshot!=query.reader.snapshot:raise ValueError('Citation release does not match configured reader')
    if not isinstance(raw_sha256,str) or not re.fullmatch('[0-9a-f]{64}',raw_sha256):raise ValueError('Expected raw SHA-256 required')
    if type(version) is not int or version<1 or ordinal is not None and (type(ordinal) is not int or ordinal<1):raise ValueError('Invalid citation location')
    if quote is not None and (not isinstance(quote,str) or not quote or len(quote)>500):raise ValueError('Quote must be 1..500 literal characters')
    sid=query._sid(sid);rows,citation=query.reader.read(sid,version)
    selected=[r for r in rows if ordinal is None or r['ordinal']==ordinal]
    location_exists=ordinal is None or bool(selected)
    return query.envelope(setid=sid,version=version,ordinal=ordinal,citation=citation,
        raw_hash_matches=citation['raw_sha256']==raw_sha256,location_exists=location_exists,
        quote_matches=None if quote is None else any(quote in (r.get('own_text') or '') for r in selected),
        verified=citation['raw_sha256']==raw_sha256 and location_exists and (quote is None or any(quote in (r.get('own_text') or '') for r in selected)),
        meaning='Verifies retained evidence identity and optional literal extracted-text quotation only; does not verify a clinical claim, publisher availability or source fidelity.')


def installation_plan(query,setids):
    if not isinstance(setids,list) or not 1<=len(setids)<=200:raise ValueError('Select 1..200 explicit families')
    selected=sorted({query._sid(s) for s in setids});prefixes=sorted({s[0] for s in selected})
    if hasattr(query.reader,'installation_plan'):
        required,omitted=query.reader.installation_plan(selected)
        return query.envelope(selected_setids=selected,required_shards=required,omitted_shards=omitted,
            meaning='Setid-prefix SQLite shards of the pinned rebuild (file size and SHA-256 are recorded in its COMPLETE.json). A shard contains other families too. No download or network availability claim.')
    shards=getattr(query.reader,'shards',None);root=getattr(query.reader,'root',None)
    return query.envelope(selected_setids=selected,required_shards=[{
        'prefix':p,'commit':shards[p]['head'] if shards else None,
        'installed':(root/'shards'/p).is_dir() if shards and root else None} for p in prefixes],
        omitted_shards=[p for p in '0123456789abcdef' if p not in prefixes],
        meaning='Deterministic first-hex shard selection. A shard contains other families too. Registered families in omitted shards are unavailable, not zero-result evidence. No download or network availability claim.')


def dated_inventory(query,setids,on_date,limit=50,offset=0,all_families=False):
    if not isinstance(on_date,str) or date.fromisoformat(on_date).isoformat()!=on_date:raise ValueError('Use YYYY-MM-DD')
    if type(all_families) is not bool:raise ValueError('all_families must be boolean')
    if all_families:
        if setids is not None:raise ValueError('Choose explicit families or all_families')
        selected=sorted(r['setid'] for r in query.reader.families())
    else:
        if not isinstance(setids,list) or not 1<=len(setids)<=200:raise ValueError('Select 1..200 explicit families or request all_families')
        selected=sorted({query._sid(s) for s in setids})
    if type(limit) is not int or not 1<=limit<=50 or type(offset) is not int or offset<0:raise ValueError('Invalid cohort pagination')
    rows=[]
    # Delegate the date policy rather than introducing a second selection engine.
    for sid in selected[offset:offset+limit]:
        result=query.show_date(sid,on_date,max_chars=1,section_limit=1)
        rows.append({'setid':sid,'date_selection':result['date_selection'],'citation':result.get('citation'),
                     'inventory':query.reader.inventory(sid),'extraction':result.get('evidence',{}).get('extraction')})
    end=offset+len(rows)
    return query.envelope(on_date=on_date,selected_cohort=None if all_families else selected,all_registered_families=all_families,
        rows=rows,total_families=len(selected),offset=offset,
        next_offset=end if end<len(selected) else None,
        meaning='Publication-cutoff readings of an explicitly selected cohort, not a market-wide snapshot or actual clinical applicability. Unknown dates/inventories and publication conflicts remain per family.')


def _tokens(text):return list(re.finditer(r'\S+',text))


def provenance(query,sid,version,ordinal,limit=100,offset=0):
    if any(type(x) is not int or x<1 for x in (version,ordinal,limit)) or limit>200 or type(offset) is not int or offset<0:raise ValueError('Invalid token-provenance bounds')
    sid=query._sid(sid);versions=query.reader.versions(sid)
    if version not in {r['version'] for r in versions}:raise ValueError('Version outside selected release')
    versions=[r for r in versions if r['version']<=version]
    if len(versions)>10000:raise ValueError('History exceeds 10000 versions')
    # Walk backwards through conservative matched occurrences; ordinals are only
    # local locations. A gap, partial extraction or ambiguous pairing ends replay.
    rows,citation=query.reader.read(sid,version);current=next((r for r in rows if r['ordinal']==ordinal),None)
    if current is None:raise ValueError('Occurrence outside captured version')
    target_tokens=_tokens(current.get('own_text') or '')
    if len(target_tokens)>MAX_TOKENS:raise ValueError('Occurrence exceeds 10000-token bound')
    incomplete=query.evidence(sid,version)['extraction']['status']!='complete'
    lineage=[(version,current,citation)];boundary='extraction_unknown' if incomplete else 'record_start';uncertain=incomplete;rules=[]
    for before,after in reversed(list(zip(versions,versions[1:]))):
        if after['version']-before['version']!=1:boundary='numeric_gap';uncertain=True;break
        if query.evidence(sid,after['version'])['extraction']['status']!='complete' or query.evidence(sid,before['version'])['extraction']['status']!='complete':boundary='extraction_unknown';uncertain=True;break
        left,ca=query.reader.read(sid,before['version']);right,_=query.reader.read(sid,after['version'])
        a,b=prepare(left),prepare(right);j=next(i for i,r in enumerate(b) if r['ordinal']==current['ordinal'])
        match=next((x for x in match_sections(a,b) if x[1]==j),None)
        if match is None or match[0] is None or 'interchangeable' in match[2]:boundary='unpaired_occurrence';uncertain=True;break
        current=a[match[0]];rules.append(match[2]);lineage.append((before['version'],current,ca))
    lineage.reverse();origins=[];previous=[];earlier={};citations={}
    for n,row,ca in lineage:
        values=[m.group() for m in _tokens(row.get('own_text') or '')]
        if len(values)>MAX_TOKENS or len(values)*len(previous)>MAX_TOKEN_PRODUCT:raise ValueError('Token alignment exceeds supported bound; no provenance answer')
        citations[n]=ca
        frequencies=Counter(values)
        initial=[{'uninterrupted_from_version':n,'first_observed_token_version':earlier.get(word,n),
                  'token_alignment_ambiguous':frequencies[word]>1,
                  'observed_return':word in earlier,'boundary':boundary if not previous else 'observed_changed_token'} for word in values]
        if previous:
            for op,i,j,k,l in difflib.SequenceMatcher(None,previous,values,autojunk=False).get_opcodes():
                if op=='equal':
                    initial[k:l]=[dict(origin,token_alignment_ambiguous=origin['token_alignment_ambiguous'] or frequencies[word]>1)
                                  for word,origin in zip(values[k:l],origins[i:j])]
        for word in values:earlier.setdefault(word,n)
        origins=initial;previous=values
    output=[]
    for i in range(offset,min(offset+limit,len(target_tokens))):
        token=target_tokens[i];origin=origins[i]
        output.append(dict(token_index=i,text=token.group(),start=token.start(),end=token.end(),**origin,
                           origin_citation=citations[origin['uninterrupted_from_version']],
                           first_observed_token_citation=citations[origin['first_observed_token_version']]))
    end=offset+len(output)
    return query.envelope(setid=sid,version=version,ordinal=ordinal,citation=citation,policy=PROVENANCE_POLICY,
        tokens=output,total_tokens=len(target_tokens),offset=offset,next_offset=end if end<len(target_tokens) else None,
        replay_versions=[n for n,_,_ in lineage],boundary=boundary,earlier_lineage_unknown=uncertain,match_rules=sorted(set(rules)),
        target_extraction=query.evidence(sid,version)['extraction'],
        meaning='Case-sensitive whitespace-token replay through conservatively paired native occurrences. Equal runs inherit captured origins; changed tokens begin a new uninterrupted run. Earlier identical individual tokens mark observed returns, not restored phrases. Repeated tokens have alignment ambiguity. No author, actual introduction date, move identity or clinical meaning is inferred. Boundaries stop claims about earlier origins.')
