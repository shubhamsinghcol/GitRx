"""Plain read-only browser application for retained native label-history releases."""
import argparse
import gzip
import hashlib
import html
import json
import os
import re
from pathlib import Path
from http.server import HTTPServer,BaseHTTPRequestHandler
from urllib.parse import parse_qs,urlencode,urlsplit
from datetime import datetime
from .config import open_reader
from .query import Query
from .limits import deadline
from .readers import confined,MAX_BLOB

E=lambda x:html.escape(str(x if x is not None else ''),quote=True)
def day(value):
    if not value:return 'Not recorded'
    if len(value)==8 and value.isdigit():return value[:4]+'-'+value[4:6]+'-'+value[6:]
    return value

def table_html(table):
    try:
        structure=table.get('structure') or json.loads(table['structured_json'])
        rows=structure['rows'];parts=['<table class="source-table"><caption>'+E(' '.join(c.get('text','') if isinstance(c,dict) else str(c) for c in structure.get('caption',[])))+'</caption>']
        for row in rows:
            parts.append('<tr>')
            for cell in row['cells']:
                tag='th' if cell.get('tag')=='th' else 'td';attrs=cell.get('attributes',{})
                span=''
                for key in ('colspan','rowspan'):
                    value=str(attrs.get(key,''))
                    if value.isdigit() and 1<=int(value)<=1000:span+=' '+key+'="'+value+'"'
                parts.append('<'+tag+span+'>'+E(cell.get('text','')))
                for nested in cell.get('nested_tables',[]):
                    if isinstance(nested,dict):parts.append(table_html({'structure':nested}))
                    else:parts.append('<p class="notice">Nested table layout is unavailable; its text is retained in this cell.</p>')
                parts.append('</'+tag+'>')
            parts.append('</tr>')
        parts.append('</table>')
        if structure.get('diagnostics'):parts.append('<p class="notice">Some table layout could not be preserved. Check the original label before relying on alignment.</p>')
        return ''.join(parts)
    except (KeyError,ValueError,TypeError):
        return '<p class="notice">The original table layout is unavailable. Extracted table text follows.</p><pre>'+E(clean_text(table.get('flattened_text') or 'No table text was recovered.'))+'</pre>'

def clean_text(text):
    # Internal flattened-table delimiters are not reader-facing label prose.
    text=re.sub(r'\[MEDIA:[^\]]*\]','[Image not included in extracted text]',text or '')
    return text.replace('<ROW>','').replace('</ROW>','\n').replace('<CELL>','').replace('</CELL>','\t').replace('<CAPTION>','').replace('</CAPTION>','\n')

def label_html(text,tables):
    parts=[];remaining=text
    for table in tables:
        flat=table.get('flattened_text') or ''
        index=remaining.find(flat) if flat else -1
        if index>=0:
            parts.append('<div class="label-text">'+E(clean_text(remaining[:index]))+'</div>')
            parts.append(table_html(table));remaining=remaining[index+len(flat):]
        else:
            parts.append(table_html(table))
    parts.append('<div class="label-text">'+E(clean_text(remaining))+'</div>')
    return ''.join(parts)

def publisher_html(evidence):
    parts=[]
    for ref in evidence.get('publisher_references',[]):
        url=ref.get('url','')
        try:parsed=urlsplit(url)
        except ValueError:continue
        if parsed.scheme!='https' or not (parsed.hostname=='nlm.nih.gov' or (parsed.hostname or '').endswith('.nlm.nih.gov')) or parsed.username or parsed.password:continue
        kind=ref.get('kind','unavailable')
        label={'bulk-member':'Publisher bulk archive','exact-version':'Publisher version source','current-only':'Publisher current label'}.get(kind,'Publisher source')
        if not ref.get('observed'):label+=' (link not verified by retained retrieval)'
        parts.append('<p><a href="'+E(url)+'" rel="noreferrer">'+E(label)+'</a>')
        if ref.get('member'):parts.append(' · Bulk member '+E(ref['member']))
        parts.append('</p>')
    return ''.join(parts)

class App:
    def __init__(self,config,registry):
        self.registry=Path(registry).resolve();self.registry.mkdir(parents=True,exist_ok=True)
        path=Path(config).resolve();cfg=json.loads(path.read_bytes())
        for key in ('release_root','export_root','scope_path','search_index','evidence_metadata'):
            if key in cfg:cfg[key]=str((path.parent/cfg[key]).resolve())
        self.current=cfg['snapshot'];saved=self.registry/(self.current+'.json')
        if saved.exists():
            old=json.loads(saved.read_bytes())
            if old['snapshot']!=self.current:raise ValueError('Release registry conflict')
            if 'evidence_metadata' in cfg:
                if 'evidence_metadata' in old and old.get('evidence_metadata_sha256')!=cfg.get('evidence_metadata_sha256'):
                    raise ValueError('Saved release already has a different evidence metadata pin; use a separate registry')
                if 'evidence_metadata' not in old:
                    # Add verified metadata to this exact release without losing its former config.
                    with open_reader(path,'sqlite') as checked:
                        if checked.snapshot!=self.current:raise ValueError('Release metadata mismatch')
                    legacy=saved.with_name(self.current+'.legacy.json')
                    if not legacy.exists():legacy.write_bytes(saved.read_bytes())
                    temporary=saved.with_suffix('.json.tmp')
                    temporary.write_text(json.dumps(cfg,indent=2)+'\n');os.replace(temporary,saved)
        else:saved.write_text(json.dumps(cfg,indent=2)+'\n')
        self.readers={};self.status={}
    def release(self,snapshot):
        if len(snapshot)!=64 or any(c not in '0123456789abcdef' for c in snapshot):raise ValueError('Unknown release')
        if snapshot not in self.readers:
            path=self.registry/(snapshot+'.json')
            if not path.exists():raise ValueError('This saved release is not available here')
            reader=open_reader(path,'sqlite')
            cfg=json.loads(path.read_bytes());root=Path(cfg['release_root'])
            status_file=self.registry/(snapshot+'.coverage.json')
            pin_file=self.registry/(snapshot+'.coverage.sha256')
            if cfg.get('evidence_metadata'):
                self.status[snapshot]={}
            elif status_file.exists() and pin_file.exists():
                raw=status_file.read_bytes()
                if hashlib.sha256(raw).hexdigest()!=pin_file.read_text().strip():raise ValueError('Coverage metadata changed')
                data=json.loads(raw)
                if data['snapshot']!=snapshot:raise ValueError('Coverage release mismatch')
                self.status[snapshot]={(sid,int(version)):state for sid,version,state in data['versions']}
            else:
                raw=(root/'reports/EXPANDED-SELECTION.json').read_bytes().removesuffix(b'\n')
                if hashlib.sha256(raw).hexdigest()!=snapshot:reader.close();raise ValueError('Release evidence mismatch')
                manifest=json.loads(raw)
                self.status[snapshot]={(v['setid'],v['version']):('partial' if any(p['status']!='complete' for p in v['parse_runs']) else 'complete') for v in manifest['versions']}
                raw=json.dumps({'snapshot':snapshot,'versions':[[sid,v,state] for (sid,v),state in self.status[snapshot].items()]},separators=(',',':')).encode()
                status_file.write_bytes(raw);pin_file.write_text(hashlib.sha256(raw).hexdigest()+'\n')
            self.readers[snapshot]=(reader,root)
        return self.readers[snapshot]
    @staticmethod
    def payload(reader,root,sid,version):
        stored=reader.conn.execute('SELECT * FROM versions WHERE setid=? AND version=?',(sid,version)).fetchone()
        if stored is None:raise ValueError('Unknown version')
        path=confined(root/'export-payloads',stored['payload_path'])
        with gzip.open(path,'rb') as f:raw=f.read(MAX_BLOB+1)
        if len(raw)>MAX_BLOB or hashlib.sha256(raw).hexdigest()!=stored['payload_sha256']:raise ValueError('Source text could not be verified')
        return json.loads(raw)
    def page(self,p,origin):
        snapshot=p.get('release',self.current);reader,root=self.release(snapshot);q=Query(reader)
        base={'release':snapshot}
        def url(**values):return '/?'+urlencode(dict(base,**values))
        def link(label,**values):return '<a href="'+E(url(**values))+'">'+E(label)+'</a>'
        def form(fields,controls):
            return '<form method="get">'+''.join('<input type="hidden" name="'+E(k)+'" value="'+E(v)+'">' for k,v in dict(base,**fields).items())+controls+'</form>'
        body=['<header><h1>'+link('GitRx')+'</h1><span>DailyMed label history</span></header>']
        body.append(form({},'<label>Find a label family <input name="q" value="'+E(p.get('q',''))+'" placeholder="Drug name or label family ID" required></label> <button>Find families</button>'))
        sid=p.get('family');view=p.get('view','read')
        if not sid:
            if p.get('q'):
                offset=max(0,int(p.get('start',0)));found=q.families(p['q'],limit=50,offset=offset)
                body.append('<h2>Label families</h2><p>'+str(found['total_matches'])+' matches</p><table><thead><tr><th>Label family</th><th>Family ID</th></tr></thead><tbody>')
                for f in found['families']:body.append('<tr><td>'+link(f['title'],family=f['setid'])+'</td><td>'+E(f['setid'])+'</td></tr>')
                body.append('</tbody></table>')
                if found['next_offset'] is not None:body.append(link('More families',q=p['q'],start=found['next_offset']))
                if not found['families']:body.append('<p>No matching label families.</p>')
            else:body.append('<p>Find a label family to read its versions, compare sections, or look up wording.</p>')
            return self.document('GitRx — label history',body)
        resolved=q.resolve(sid)
        if not resolved['selected']:raise ValueError('Choose a label family from search results')
        family=resolved['selected'];sid=family['setid'];base['family']=sid
        versions=[];version_offset=0
        while True:
            page=q.versions(sid,offset=version_offset);versions.extend(page['versions'])
            version_offset=page['next_offset']
            if version_offset is None:break
        nums=[v['version'] for v in versions]
        selected=int(p.get('version',nums[-1]));date_selection=None
        if p.get('on_date'):
            dated=q.show_date(sid,p['on_date'],max_chars=1);date_selection=dated['date_selection']
            if date_selection['selected_version'] is not None:selected=date_selection['selected_version']
        body.append('<h2>'+E(family['title'])+'</h2><p class="muted">Family '+E(sid)+' · '+str(len(versions))+' captured versions</p>')
        body.append('<nav>'+ ' | '.join(link(label,view=value,version=selected) for label,value in [('Read label','read'),('Compare versions','compare'),('Find wording','phrase'),('Wording history','history'),('Version changes','changes'),('Search changes','change-search')])+'</nav>')
        coverage=q.coverage(sid)
        if coverage['missing_version_count']:body.append('<p class="notice">Not captured: '+E(', '.join(map(str,coverage['missing_version_numbers'])))+'. Changes across these gaps cannot be dated precisely.</p>')
        if coverage['versions_without_publication_dates']:body.append('<p class="notice">Publication date not recorded for '+E(', '.join('v'+str(v) for v in coverage['versions_without_publication_dates']))+'.</p>')
        evidence_by_version={v['version']:v.get('evidence') for v in versions}
        def evidence(v):
            value=evidence_by_version.get(v)
            if value is None and hasattr(reader,'evidence'):value=reader.evidence(sid,v)
            return value or {}
        def badge(v):
            value=evidence(v)
            if value.get('metadata_status')=='pinned':
                state=value.get('extraction',{}).get('status','unknown')
                return {'complete':'Text extracted','partial':'Incomplete text','failed':'Extraction failed'}.get(state,'Extraction status unknown')
            return 'Incomplete text' if self.status[snapshot].get((sid,v))=='partial' else 'Text extracted'
        inventory=coverage.get('inventory') or {}
        if inventory.get('unknown_upstream_history') is True:
            body.append('<p class="notice">The upstream family history inventory remains unresolved. Captured versions do not establish a complete family history.</p>')
        def opts(chosen):return ''.join('<option value="'+str(v['version'])+'"'+(' selected' if v['version']==chosen else '')+'>v'+str(v['version'])+' · Published '+E(', '.join(v['publication_dates']) or 'not recorded')+'</option>' for v in reversed(versions))
        def citation(v,ordinal=None,label='Copy reference'):
            meta=next(x for x in versions if x['version']==v)
            target=dict(view='read',version=v)
            if ordinal is not None:target['section']=ordinal
            exact=origin+url(**target)
            text='GitRx · '+family['title']+' · Family '+sid+' · Version '+str(v)+(' · Section occurrence '+str(ordinal) if ordinal is not None else '')+'\nDailyMed publication: '+(', '.join(meta['publication_dates']) or 'not recorded')+'; SPL effective date: '+day(meta['effective_date'])+'\nRelease: '+snapshot+'\nSource SHA-256: '+meta['raw_sha256']+'\n'+exact
            references=evidence(v).get('publisher_references',[])
            for ref in references:
                address=ref.get('url','')
                try:parsed=urlsplit(address)
                except ValueError:continue
                if parsed.scheme!='https' or not (parsed.hostname=='nlm.nih.gov' or (parsed.hostname or '').endswith('.nlm.nih.gov')) or parsed.username or parsed.password:continue
                kind=ref.get('kind','unavailable')
                publisher_label={'bulk-member':'Publisher bulk archive','exact-version':'Publisher version source','current-only':'Publisher current label'}.get(kind,'Publisher source')
                if not ref.get('observed'):publisher_label+=' (link not verified by retained retrieval)'
                text+='\n'+publisher_label+': '+address
                if ref.get('member'):text+='\nBulk member: '+str(ref['member'])
            return '<details class="reference"><summary>Reference</summary><textarea readonly rows="6" aria-label="Exact label reference">'+E(text)+'</textarea><button type="button" class="copy">'+E(label)+'</button> <a href="'+E(exact)+'">Open exact label</a><span class="copy-status" role="status"></span></details>'
        if view=='read':
            body.append(form({'view':'read'},'<label>Version <select name="version">'+opts(selected)+'</select></label> <button>Read version</button>'))
            body.append(form({'view':'read'},'<label>Published by <input type="date" name="on_date" value="'+E(p.get('on_date',''))+'" required></label> <button>Read by date</button>'))
            if date_selection:
                if date_selection['selected_version'] is None:
                    body.append('<p class="notice">No captured version has a recorded publication date on or before '+E(p['on_date'])+'.</p>')
                    return self.document(family['title'],body)
                body.append('<p>Published by '+E(p['on_date'])+': version '+str(selected)+'.</p>')
                if date_selection['publication_order_conflict']:body.append('<p class="notice">Publication dates and version numbers are out of order. This is the highest captured version published by the selected date.</p>')
            meta=next(v for v in versions if v['version']==selected)
            body.append('<div class="reading-meta"><strong>Version '+str(selected)+'</strong> · Published '+E(', '.join(meta['publication_dates']) or 'not recorded')+' · SPL effective '+E(day(meta['effective_date']))+' · '+badge(selected)+'</div>')
            body.append(publisher_html(evidence(selected)))
            if badge(selected)=='Incomplete text':body.append('<p class="notice">Some source content could not be fully extracted. Missing wording here does not establish absence from the original label.</p>')
            sections,_=reader.read(sid,selected);ordinal=int(p.get('section',next((s['ordinal'] for s in sections if (s['own_text'] or '').strip()),sections[0]['ordinal'] if sections else 0)))
            body.append(form({'view':'read','version':selected},'<label>Section <select name="section">'+''.join('<option value="'+str(s['ordinal'])+'"'+(' selected' if s['ordinal']==ordinal else '')+'>'+E(s['title'] or 'Untitled section')+' · occurrence '+str(s['ordinal'])+'</option>' for s in sections)+'</select></label> <button>Read section</button>'))
            row=next((s for s in sections if s['ordinal']==ordinal),None)
            if row:
                body.append('<h3>'+E(row['title'] or 'Untitled section')+'</h3><p class="muted">Occurrence '+str(ordinal)+' · Source section code '+E(row['loinc_code'] or 'not recorded')+'</p>')
                repeats=sum(s['loinc_code']==row['loinc_code'] for s in sections)
                if repeats>1:body.append('<p class="notice">'+str(repeats)+' occurrences share this source code. Choose a section occurrence above to read each separately.</p>')
                text=row['own_text'] or 'No separate text was extracted for this section. Select a subsection to continue.'
                # Tables are obtained from the same verified payload as section text.
                tables=[t for t in self.payload(reader,root,sid,selected).get('tables',[]) if t['section_ordinal']==ordinal]
                body.append(label_html(text,tables))
                body.append(citation(selected,ordinal))
            else:body.append('<p class="notice">This section occurrence is not available in the selected version.</p>')
            body.append('<details><summary>Version timeline</summary><table><thead><tr><th>Version</th><th>DailyMed publication</th><th>SPL effective date</th><th>Text coverage</th></tr></thead><tbody>')
            for v in reversed(versions):body.append('<tr><td>'+link('v'+str(v['version']),view='read',version=v['version'])+'</td><td>'+E(', '.join(v['publication_dates']) or 'Not recorded')+'</td><td>'+E(day(v['effective_date']))+'</td><td>'+badge(v['version'])+'</td></tr>')
            body.append('</tbody></table></details>')
        elif view=='compare':
            a=int(p.get('before',nums[0]));b=int(p.get('after',nums[-1]));code=p.get('code') or None
            body.append(form({'view':'compare'},'<label>Earlier version <select name="before">'+opts(a)+'</select></label> <label>Later version <select name="after">'+opts(b)+'</select></label> '+self.code_select(code)+' <button>Compare</button>'))
            comparison=q.compare(sid,a,b,code=code,limit=200,max_chars=200000,timeout_seconds=20)
            all_rows=list(comparison['comparisons']);offset=comparison['next_offset']
            while offset is not None:
                page=q.compare(sid,a,b,code=code,limit=200,max_chars=200000,offset=offset,timeout_seconds=20);all_rows+=page['comparisons'];offset=page['next_offset']
            body.append('<p>v'+str(a)+' · '+badge(a)+' → v'+str(b)+' · '+badge(b)+'</p>')
            if badge(a)=='Incomplete text' or badge(b)=='Incomplete text':body.append('<p class="notice">Some source content is missing from the extracted text. This comparison may omit changes.</p>')
            body.append('<p><span class="removed">Removed wording</span> · <span class="added">Added wording</span></p>')
            sources={v:self.payload(reader,root,sid,v) for v in (a,b)}
            for row in all_rows:
                if not row['content_changed'] and row['before'] and row['after']:continue
                loc=row['after'] or row['before'];body.append('<article><h3>'+E(loc['title'] or 'Untitled section')+'</h3>')
                if row['match_rule']=='unmatched-or-ambiguous':body.append('<p class="notice">These occurrences could not be paired confidently. Text shown on one side is not proof that a section was added or removed.</p>')
                body.append('<p>'+('Earlier occurrence '+str(row['before']['ordinal']) if row['before'] else 'No confident earlier match')+' · '+('Later occurrence '+str(row['after']['ordinal']) if row['after'] else 'No confident later match')+'</p><div class="label-text">')
                has_tables=any(row[side] and any(t['section_ordinal']==row[side]['ordinal'] for t in sources[v].get('tables',[])) for side,v in (('before',a),('after',b)))
                if has_tables:
                    body.append('<div class="pair">')
                    for side,v in (('before',a),('after',b)):
                        body.append('<div><h4>Version '+str(v)+'</h4>')
                        if row[side]:
                            ordinal=row[side]['ordinal'];original=next(s for s in sources[v]['sections'] if s['ordinal']==ordinal)
                            body.append(label_html(original['own_text'] or '',[t for t in sources[v].get('tables',[]) if t['section_ordinal']==ordinal]))
                        else:body.append('<p>No confident section match.</p>')
                        body.append('</div>')
                    body.append('</div>')
                else:
                    for run in row['word_diff']:
                        op=run['operation']
                        if op=='equal':body.append(E(clean_text(run['before']))+' ')
                        else:
                            if run['before']:body.append('<del>'+E(clean_text(run['before']))+'</del> ')
                            if run['after']:body.append('<ins>'+E(clean_text(run['after']))+'</ins> ')
                body.append('</div>')
                if row['truncated']:body.append('<p class="notice">Some comparison text is not shown. Open either label to read the full section.</p>')
                for side,v in (('before',a),('after',b)):
                    if row[side]:body.append(link('Read v'+str(v)+' occurrence '+str(row[side]['ordinal']),view='read',version=v,section=row[side]['ordinal']))
                body.append('</article>')
            if not any(r['content_changed'] or not r['before'] or not r['after'] for r in all_rows):body.append('<p>No wording differences in the selected sections.</p>')
            body.append(citation(a)+citation(b))
        elif view=='changes':
            code=p.get('code') or None;previous=int(p['previous']) if p.get('previous') else None
            offset=max(0,int(p.get('change_offset',0)))
            prior_options='<option value="">Greatest lower captured version</option>'+''.join('<option value="'+str(v)+'"'+(' selected' if v==previous else '')+'>v'+str(v)+'</option>' for v in reversed(nums) if v<selected)
            body.append(form({'view':'changes'},'<label>Version <select name="version">'+opts(selected)+'</select></label> <label>Compare with <select name="previous">'+prior_options+'</select></label> '+self.code_select(code)+' <button>Inspect version changes</button>'))
            result=q.version_changes(sid,selected,previous=previous,code=code,offset=offset,limit=50,max_chars=16000,timeout_seconds=25)
            before=result['previous_version'];after=result['version']
            body.append('<h3>Captured version changes</h3>')
            if result['baseline']:
                body.append('<p class="notice">This is the earliest captured version and has no captured predecessor. It is a starting record, not proof that this label or its sections were introduced then.</p>'+citation(after))
            else:
                body.append('<p>'+link('Read v'+str(before),view='read',version=before)+' → '+link('Read v'+str(after),view='read',version=after)+'</p><p class="notice">This compares two captured endpoints. Equal wording does not establish that nothing changed in between. Unmatched occurrences are ambiguous, not proven section additions or removals.</p>')
                if badge(before)=='Incomplete text' or badge(after)=='Incomplete text':body.append('<p class="notice">Some source text could not be fully extracted; this comparison may omit changes.</p>')
                gap=result.get('numeric_gap')
                if gap:body.append('<p class="notice">'+E(gap['unobserved_integer_count'])+' unobserved version positions lie between these endpoints. Changes across this gap cannot be dated precisely.</p>')
                labels=[('total_comparisons','Compared occurrence pairs'),('content_changed','Wording changes'),('raw_text_changed','Raw text differences'),('heading_changed','Heading changes'),('position_changed','Position changes'),('source_representation_changed','Source representation changes'),('unmatched_or_ambiguous','Unmatched or ambiguous occurrences')]
                body.append('<p>Counts reflect recorded differences. Unavailable source representation does not establish equality.</p><table><caption>Selected comparison summary</caption><tbody>'+''.join('<tr><th>'+label+'</th><td>'+E(result['summary'].get(key,0))+'</td></tr>' for key,label in labels)+'</tbody></table>')
                for row in result['changes']:
                    loc=row.get('after') or row.get('before') or {}
                    body.append('<article><h4>'+E(loc.get('title') or 'Untitled section')+'</h4>')
                    if row.get('unmatched_or_ambiguous'):body.append('<p class="notice">These source occurrences could not be paired confidently. One-sided text is not proof that a section was added or removed.</p>')
                    flags=[label for key,label in labels[1:] if row.get(key)]
                    body.append('<p>'+E(', '.join(flags) or 'No recorded text or structure differences')+'</p><div class="label-text">')
                    for run in row.get('word_diff',[]):
                        if run['operation']=='equal':body.append(E(clean_text(run.get('before','')))+' ')
                        else:
                            if run.get('before'):body.append('<del>'+E(clean_text(run['before']))+'</del> ')
                            if run.get('after'):body.append('<ins>'+E(clean_text(run['after']))+'</ins> ')
                    body.append('</div>')
                    if row.get('truncated'):body.append('<p class="notice">Some comparison text is not shown. Open the source occurrences to read it.</p>')
                    for side,v in (('before',before),('after',after)):
                        if row.get(side):body.append('<p>'+link('Read v'+str(v)+' occurrence '+str(row[side]['ordinal']),view='read',version=v,section=row[side]['ordinal'])+'</p>')
                    body.append('</article>')
                if result.get('next_offset') is not None:body.append('<p>'+link('More compared occurrences',view='changes',version=after,previous=before,code=code or '',change_offset=result['next_offset'])+'</p>')
                body.append(citation(before)+citation(after))
        elif view=='change-search':
            phrase=p.get('phrase','');code=p.get('code') or None
            start=p.get('changed_from') or None;end=p.get('changed_to') or None
            body.append(form({'view':'change-search'},'<label>Literal wording (case insensitive) <input name="phrase" value="'+E(phrase)+'" required></label> '+self.code_select(code)+' <label>Later version published from <input type="date" name="changed_from" value="'+E(start)+'"></label> <label>Through <input type="date" name="changed_to" value="'+E(end)+'"></label> <button>Search changed text</button>'))
            if phrase:
                result=q.search_changes(sid,phrase,code=code,start=start,end=end,cursor=p.get('cursor') or None,limit=25,max_transitions=50,timeout_seconds=25)
                body.append('<h3>Changed-text matches</h3><p class="notice">Search covers consecutive captured transitions in this family, excluding its starting record. A hit intersects an aligned text edit; unchanged surrounding wording alone is not a match. Matches are not proven clinical additions or removals.</p>')
                body.append('<p>This page checked '+E(result['transitions_scanned'])+' transitions. Date filters use any recorded publication date of the later endpoint, including both bounds.</p>')
                uncertain=result.get('extraction_uncertain_transitions',0)
                if uncertain:body.append('<p class="notice">'+E(uncertain)+' examined transitions on this page have incomplete or unknown extraction. Missing matches do not establish source absence.</p>')
                unknown=result.get('date_unknown_transitions_excluded',0)
                if unknown:body.append('<p class="notice">'+E(unknown)+' transitions on this page were left out because the later version has no usable publication date.</p>')
                outside=result.get('date_outside_transitions_excluded',0)
                if outside:body.append('<p>'+E(outside)+' transitions on this page fell outside the selected publication-date window.</p>')
                if result.get('unexamined_occurrences',0):body.append('<p class="notice">Some occurrence alignments could not be examined within the matching bounds. A negative result is incomplete for those occurrences.</p>')
                for hit in result['hits']:
                    before=hit['previous_version'];after=hit['version']
                    loc=hit.get('after') or hit.get('before') or {}
                    body.append('<article><h4>v'+E(before)+' → v'+E(after)+' · '+E(loc.get('title') or 'Untitled section')+'</h4><p>Later version published '+E(', '.join(hit.get('publication_dates',[])) or 'not recorded')+'</p>')
                    if hit.get('unmatched_or_ambiguous'):body.append('<p class="notice">Occurrences are unmatched or ambiguous; one-sided wording is not proof of a section addition or removal.</p>')
                    if hit.get('gap'):body.append('<p class="notice">'+E(hit.get('unobserved_integer_count',0))+' unobserved version positions interrupt this transition. Its precise change date is unknown.</p>')
                    for match in hit.get('matches',[]):
                        side=match['side'];v=before if side=='before' else after
                        body.append('<p><strong>'+('Earlier' if side=='before' else 'Later')+' captured text</strong></p><p class="label-text">'+E(clean_text(match['text_excerpt']))+'</p>')
                        if hit.get(side):body.append('<p>'+link('Read v'+str(v)+' occurrence '+str(hit[side]['ordinal']),view='read',version=v,section=hit[side]['ordinal'])+'</p>')
                    if hit.get('matches_truncated'):body.append('<p>Further matching spans exist; open the occurrences to inspect them.</p>')
                    body.append(citation(before)+citation(after)+'</article>')
                if not result['hits']:body.append('<p>No changed-text matches in the examined transitions on this page.</p>')
                if result.get('continuation'):body.append('<p>'+link('Continue changed-text search',view='change-search',phrase=phrase,code=code or '',changed_from=start or '',changed_to=end or '',cursor=result['continuation'])+'</p>')
                if result.get('partial'):body.append('<p class="notice">The scan is incomplete. Continue even when this page has no hits.</p>')
        elif view=='history':
            phrase=p.get('phrase','');code=p.get('code') or None
            body.append(form({'view':'history'},'<label>Literal wording (case insensitive) <input name="phrase" value="'+E(phrase)+'" required></label> '+self.code_select(code)+' <button>Read wording history</button>'))
            if phrase:
                result=q.phrase_history(sid,phrase,code=code,cursor=p.get('cursor') or None,limit=50,timeout_seconds=25)
                body.append('<h3>Captured wording history</h3><p class="notice">These are observations of selected extracted text in captured version order, not proven addition or removal dates. Gaps and incomplete extraction weaken negative results.</p>')
                labels={'present_at_record_start':'Present at start of captured record','nonmatch_at_record_start':'No match at start of captured record','unknown_at_record_start':'Unknown at start of captured record','continued_presence':'Match continues','continued_nonmatch':'Extracted-text nonmatch continues','observed_nonmatch_to_match':'Match after preceding captured nonmatch','observed_return':'Previously observed wording returns','observed_match_to_nonmatch':'No match after preceding captured match','continuity_unknown':'Continuity unknown','present_after_unknown':'Match after an unknown interval'}
                for row in result['observations']:
                    v=row['version'];state=row['evidence_state']
                    description={'present':'Match in extracted text','checked_nonmatch':'No match in extracted text','unknown':'No match observed; evidence incomplete or unknown'}.get(state,'Evidence status unknown')
                    body.append('<article><h4>'+link('Version '+str(v),view='read',version=v)+'</h4><p>'+E(description)+' · Published '+E(', '.join(row.get('publication_dates',[])) or 'not recorded')+' · SPL effective '+E(day(row.get('effective_date')))+'</p><p>'+E(labels.get(row['transition'],row['transition'].replace('_',' ')))+'</p>')
                    gap=row.get('gap_from_previous')
                    if gap:body.append('<p class="notice">Unobserved version positions between v'+E(gap['after_version'])+' and v'+E(gap['before_version'])+': '+E(gap['unobserved_integer_count'])+'. Continuity across this interval is unknown.</p>')
                    for match in row.get('matches',[]):
                        excerpt=match.get('excerpt',match.get('text_excerpt',''))
                        body.append('<p class="label-text">'+E(clean_text(excerpt))+'</p>'+link('Read occurrence '+str(match['ordinal']),view='read',version=v,section=match['ordinal']))
                    if row.get('matches_truncated'):body.append('<p>More matching occurrences are present; open the version to inspect them.</p>')
                    body.append(citation(v)+'</article>')
                if result.get('continuation'):
                    body.append('<p>'+link('More wording history',view='history',phrase=phrase,code=code or '',cursor=result['continuation'])+'</p>')
                if result.get('partial'):body.append('<p class="notice">This page does not contain the whole captured history. Follow More wording history to continue.</p>')
        elif view=='phrase':
            phrase=p.get('phrase','');code=p.get('code') or None
            body.append(form({'view':'phrase'},'<label>Exact wording <input name="phrase" value="'+E(phrase)+'" required></label> '+self.code_select(code)+' <button>Find first occurrence</button>'))
            if phrase:
                result=q.first_observed(sid,phrase,code=code,max_versions=10000,timeout_seconds=25)
                first=result['first_observed'];body.append('<h3>First observed wording</h3>')
                if first:
                    v=first['version'];body.append('<p><strong>Version '+str(v)+'</strong> · Published '+E(', '.join(first['publication_dates']) or 'not recorded')+' · SPL effective '+E(day(first['effective_date']))+' · '+badge(v)+'</p>')
                    for match in first['matches']:
                        body.append('<article><h4>'+E(match['title'] or 'Untitled section')+' · occurrence '+str(match['ordinal'])+'</h4><p class="label-text">'+E(clean_text(match['text_excerpt']))+'</p>'+link('Read this occurrence',view='read',version=v,section=match['ordinal'])+citation(v,match['ordinal'])+'</article>')
                    prev=result['previous_captured_version']
                    if prev:body.append('<p>No match in the selected extracted text of preceding captured version '+str(prev['version'])+' · '+badge(prev['version'])+'.</p>'+citation(prev['version']))
                    body.append('<p class="notice">This is the earliest match in captured version order, not a proven addition date. Earlier missing or incompletely extracted text may contain the wording.</p>')
                else:body.append('<p>No match in the selected extracted text across '+str(len(result['versions_scanned']))+' captured versions. Missing or incompletely extracted content may contain the wording.</p>')
        else:raise ValueError('Unknown view')
        body.append('<footer>Release '+E(snapshot)+' · Source sections are shown separately; section codes do not establish their clinical context.</footer>')
        return self.document(family['title']+' — GitRx',body)
    @staticmethod
    def code_select(selected):
        choices=[('', 'All source sections'),('34066-1','Boxed warning'),('43685-7','Warnings and precautions'),('34071-1','Warnings'),('34070-3','Contraindications'),('34067-9','Indications and usage'),('34068-7','Dosage and administration')]
        return '<label>Source section <select name="code">'+''.join('<option value="'+code+'"'+(' selected' if code==(selected or '') else '')+'>'+label+'</option>' for code,label in choices)+'</select></label>'
    @staticmethod
    def document(title,parts):return '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>'+E(title)+'</title><link rel="stylesheet" href="/app.css"><script src="/app.js" defer></script></head><body><main>'+''.join(parts)+'</main></body></html>'

CSS='''body{margin:0;color:#202020;background:#fff;font:16px/1.5 system-ui,sans-serif}main{max-width:1200px;margin:auto;padding:20px}header{display:flex;align-items:baseline;gap:20px;border-bottom:1px solid #aaa}h1{font-size:24px}h2{font-size:21px}h3{font-size:18px}a{color:#164e8a}form{margin:14px 0;display:flex;gap:10px;align-items:end;flex-wrap:wrap}label{display:flex;gap:5px;flex-direction:column}input,select,button{font:inherit;padding:6px;border:1px solid #888;border-radius:2px;background:white;color:inherit}input{min-width:250px}select{max-width:100%}button{cursor:pointer;background:#f2f2f2}button:hover{background:#e4e4e4}nav{padding:8px 0;border-bottom:1px solid #bbb}.notice{padding:8px;border-left:3px solid #946600;background:#fff8e8}.muted,footer{color:#555;font-size:14px;overflow-wrap:anywhere}.reading-meta{border-bottom:1px solid #bbb;padding:10px 0}.label-text,pre{white-space:pre-line;overflow-wrap:anywhere}.label-text{max-width:100%;margin:16px 0}table{border-collapse:collapse;width:100%;margin:12px 0;overflow-wrap:anywhere}th,td{border:1px solid #bbb;text-align:left;vertical-align:top;padding:6px}th{background:#f5f5f5}.source-table{width:auto;max-width:100%}.pair{display:grid;grid-template-columns:1fr 1fr;gap:18px}.pair>div{min-width:0}.source-table td{white-space:pre-wrap}details{margin:16px 0}summary{cursor:pointer}textarea{width:100%;box-sizing:border-box;font:13px/1.5 monospace}footer{border-top:1px solid #aaa;margin-top:28px;padding-top:12px}article{border-bottom:1px solid #bbb;padding:10px 0}del,.removed{background:#ffe4e4;text-decoration:line-through}ins,.added{background:#dcf0dc;text-decoration:none}.copy-status{padding-left:8px}@media(max-width:650px){.pair{display:block}main{padding:12px}form,label{max-width:100%;width:100%}input{min-width:0}header{display:block}table{font-size:13px}}'''
JS='''document.addEventListener('click',async e=>{if(!e.target.matches('.copy'))return;let box=e.target.parentElement.querySelector('textarea'),status=e.target.parentElement.querySelector('.copy-status');try{await navigator.clipboard.writeText(box.value);status.textContent='Copied';}catch{box.focus();box.select();status.textContent='Reference selected. Copy with your usual keyboard shortcut.';}});'''

class LocalServer(HTTPServer):
    def get_request(self):
        connection,address=super().get_request()
        connection.settimeout(1)
        return connection,address

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        host=self.headers.get('Host','')
        if host not in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'):
            self.send_error(403);return
        path=urlsplit(self.path)
        try:
            if path.path=='/app.css':body=CSS;mime='text/css'
            elif path.path=='/app.js':body=JS;mime='text/javascript'
            elif path.path=='/favicon.ico':self.send_response(204);self.end_headers();return
            elif path.path=='/':
                params={k:v[-1] for k,v in parse_qs(path.query,keep_blank_values=True).items()}
                with deadline(35):body=self.server.app.page(params,'http://'+host)
                mime='text/html'
            else:self.send_error(404);return
            code=200
        except Exception:
            body=App.document('Could not open label',['<h1>Could not open this view</h1><p>The selected label or saved release could not be read. Check your selection and try again.</p><p><a href="/">Find a label family</a></p>']);mime='text/html';code=400
        encoded=body.encode();self.send_response(code)
        for k,v in [('Content-Type',mime+'; charset=utf-8'),('Content-Length',str(len(encoded))),('Cache-Control','no-store'),('X-Content-Type-Options','nosniff'),('Content-Security-Policy',"default-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),('Referrer-Policy','no-referrer')]:self.send_header(k,v)
        self.end_headers();self.wfile.write(encoded)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--registry',default='runtime/web/releases');p.add_argument('--port',type=int,default=8766)
    a=p.parse_args();server=LocalServer(('127.0.0.1',a.port),Handler);server.app=App(a.config,a.registry)
    # Validate/load the selected archive before announcing readiness.
    server.app.release(server.app.current)
    print(f'GitRx browser: http://127.0.0.1:{server.server_port}',flush=True)
    try:server.serve_forever()
    finally:server.server_close()
if __name__=='__main__':main()
