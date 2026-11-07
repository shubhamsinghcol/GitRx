"""Five local side-by-side manual-audit samples; no network or web service."""
import argparse
import html
import io
import json
from pathlib import Path
import zipfile

from .archive import artifact_bytes, read_xml
from .db import connect
from .parser import PARSER_VERSION


def escape(value):
    return html.escape(str(value))


def rich_inline(node):
    """Safe inline projection retaining superscripts, subscripts, lists and footnotes."""
    tag=node.tag.rsplit('}',1)[-1] if isinstance(node.tag,str) else ''
    if tag=='br':return '<br>'
    if tag=='renderMultiMedia':return '<span>[MEDIA:'+escape(node.get('referencedObject',''))+']</span>'
    if tag=='footnoteRef':return '<sup>[footnote:'+escape(node.get('IDREF',''))+']</sup>'
    body=escape(node.text or '')
    for child in node:
        if isinstance(child.tag,str):body+=rich_inline(child)
        body+=escape(child.tail or '')
    mapped={'sup':'sup','sub':'sub','paragraph':'p','list':'ul','item':'li','footnote':'aside','caption':'div'}.get(tag)
    if tag=='content':
        styles=node.get('styleCode','').lower()
        mapped='sup' if 'sup' in styles else 'sub' if 'sub' in styles else 'strong' if 'bold' in styles else 'em' if 'ital' in styles else None
    return f'<{mapped}>{body}</{mapped}>' if mapped else body


def table_html(table):
    structure=json.loads(table['structured_json'])
    if structure.get('diagnostics'):
        out=['<div class="unsafe-table"><strong>Source layout is invalid. Row/cell sequence below does not infer column spans.</strong>']
        for row in structure['rows']:
            out.append(f'<h5>Source row {row["ordinal"]}</h5><ol>')
            for cell in row['cells']:
                out.append('<li><strong>Cell '+str(cell['ordinal'])+'; attributes '+escape(cell['attributes'])+'</strong>'+rich_inline(read_xml(cell['source_xml'].encode()))+'</li>')
            out.append('</ol>')
        return ''.join(out)+'</div>'
    out=['<table class="source-table">']
    if structure['caption']:
        out.append('<caption>'+escape('\n'.join(c['text'] for c in structure['caption']))+'</caption>')
    for row in structure['rows']:
        out.append('<tr>')
        for cell in row['cells']:
            tag='th' if cell['tag']=='th' else 'td'
            attrs=''
            for name in ('rowspan','colspan'):
                raw=cell['attributes'].get(name)
                if raw and raw.isdigit() and int(raw)>0:
                    attrs+=f' {name}="{int(raw)}"'
            out.append(f'<{tag}{attrs}>'+rich_inline(read_xml(cell['source_xml'].encode()))+f'</{tag}>')
        out.append('</tr>')
    out.append('</table>')
    return ''.join(out)


def latest_version(conn,setid,number=None):
    return conn.execute('SELECT * FROM versions WHERE setid=? AND state=\'downloaded\' '+
                        ('AND version=? ' if number is not None else '')+'ORDER BY version DESC LIMIT 1',
                        (setid,number) if number is not None else (setid,)).fetchone()


def section_pick(conn,version,code,offset=0):
    row=conn.execute('''SELECT s.* FROM sections s JOIN parse_runs p ON p.id=s.parse_run_id
      WHERE s.version_id=? AND s.loinc_code=? AND s.kind='full' AND p.parser_version=?
      ORDER BY ordinal LIMIT 1 OFFSET ?''',(version['id'],code,PARSER_VERSION,offset)).fetchone()
    if row is None:
        raise RuntimeError(f"Audit section missing: {version['setid']} v{version['version']} {code}")
    return row


def build(conn,root,output):
    output.mkdir(parents=True,exist_ok=True)
    originals=output/'source-documents';originals.mkdir(exist_ok=True)
    keytruda=latest_version(conn,'9333c79b-d487-4538-a9f0-71b91a02b287')
    heavy=conn.execute('''SELECT s.*,SUM(json_array_length(t.structured_json,'$.rows')) AS rows_in_tables
      FROM sections s JOIN parse_runs p ON p.id=s.parse_run_id
      JOIN narrative_tables t ON t.section_id=s.id AND t.scope='full'
      WHERE s.version_id=? AND s.kind='full' AND p.parser_version=?
      GROUP BY s.id ORDER BY rows_in_tables DESC,s.ordinal LIMIT 1''',(keytruda['id'],PARSER_VERSION)).fetchone()
    selections=[('Table-heavy KEYTRUDA section',keytruda,heavy),
      ('Repeated boxed-warning code: CIPRO instance 1 of 3',latest_version(conn,'888dc7f9-ad9c-4c00-8d50-8ddfd9bd27c0',1),None),
      ('Historical 45129-4 document: AMBIEN indications',latest_version(conn,'c36cadf4-65a4-4466-b409-c82020b42452',1),None),
      ('Full warning with separately retained highlights: montelukast',latest_version(conn,'c43d1edb-c162-4317-9a89-d84e4eff5512'),None),
      ('Malformed source table span: HUMALOG patient labeling, instance 1 of 3',latest_version(conn,'c8ecbd7a-0e22-4fc7-a503-faa58c1b6f3f',1),None)]
    codes=[None,'34066-1','34067-9','34066-1','42230-3']
    snapshot=(output.parent/'SNAPSHOT-ID.txt').read_text().strip()
    cards=[];samples=[]
    for i,(label,version,selected) in enumerate(selections,1):
        section=selected if selected is not None else section_pick(conn,version,codes[i-1])
        slug=f'{i:02d}-{version["setid"]}-v{version["version"]}-{section["loinc_code"]}-instance-{section["ordinal"]}'
        source_file=slug+'.source.xml';extracted_file=slug+'.extracted.txt';tables_file=slug+'.tables.json'
        (output/source_file).write_text(section['source_xml'])
        original_name=f'{version["setid"]}-v{version["version"]}.xml'
        with zipfile.ZipFile(io.BytesIO(artifact_bytes(conn,root,version['selected_sha256']))) as z:
            original_xml=z.read(version['xml_member'])
            (originals/original_name).write_bytes(original_xml)
        original_doc=read_xml(original_xml)
        # Preorder section instances retain nested narratives separately instead of
        # copying child text into the parent's stored narrative.
        all_sections=conn.execute('SELECT * FROM sections WHERE parse_run_id=? ORDER BY ordinal',(section['parse_run_id'],)).fetchall()
        chosen={section['ordinal']};subtree=[]
        for s in all_sections:
            if s['parent_ordinal'] in chosen:
                chosen.add(s['ordinal'])
            if s['ordinal'] in chosen:
                subtree.append(s)
        pieces=[];right=[];structured=[]
        for s in subtree:
            if s['kind']=='excerpt':
                continue
            pieces.append(f"[instance {s['ordinal']}; {s['loinc_code']}; ancestry {s['ancestry_path']}; {s['status']}]\n{s['title'] or ''}\n{s['own_text']}")
            right.append(f'<h4>Instance {s["ordinal"]} · {escape(s["loinc_code"])} · {escape(s["title"] or "untitled")}</h4>')
            blocks=conn.execute("SELECT * FROM narrative_blocks WHERE section_id=? AND scope='full' ORDER BY ordinal",(s['id'],)).fetchall()
            for b in blocks:
                if b['kind']=='table_cell':
                    continue
                if b['kind']=='table_flat':
                    t=conn.execute("SELECT * FROM narrative_tables WHERE section_id=? AND ordinal=? AND scope='full'",(s['id'],b['table_ordinal'])).fetchone()
                    right.append(table_html(t))
                    right.append('<details><summary>Searchable table with explicit delimiters</summary><pre>'+escape(t['flattened_text'])+'</pre></details>')
                else:
                    nodes=original_doc.xpath(b['source_xpath'],namespaces={'s':'urn:hl7-org:v3'})
                    node=nodes[0] if nodes else None
                    if node is not None and node.tag.rsplit('}',1)[-1] in ('paragraph','item','footnote','caption') and not node.xpath('.//s:paragraph | .//s:list | .//s:table | .//s:footnote',namespaces={'s':'urn:hl7-org:v3'}):
                        right.append('<div class="narrative">'+rich_inline(node)+'</div>')
                    else:
                        right.append('<p class="narrative">'+escape(b['text']).replace('\n','<br>')+'</p>')
            for t in conn.execute('SELECT * FROM narrative_tables WHERE section_id=? ORDER BY ordinal',(s['id'],)):
                structured.append({'instance_ordinal':s['ordinal'],'scope':t['scope'],**json.loads(t['structured_json'])})
            excerpts=conn.execute('SELECT * FROM section_excerpts WHERE section_id=? ORDER BY ordinal',(s['id'],)).fetchall()
            for excerpt in excerpts:
                pieces.append('[HIGHLIGHTS EXCERPT — separate from full narrative]\n'+excerpt['extracted_text'])
                right.append('<details class="excerpt"><summary>Highlights excerpt — excluded from default full-narrative search</summary><pre>'+escape(excerpt['extracted_text'])+'</pre></details>')
        extracted='\n\n'.join(pieces)
        (output/extracted_file).write_text(extracted+'\n')
        (output/tables_file).write_text(json.dumps(structured,ensure_ascii=False,indent=2)+'\n')
        repeated=[dict(r) for r in conn.execute("SELECT ordinal,ancestry_path,title,status FROM sections WHERE parse_run_id=? AND loinc_code=? AND kind='full' ORDER BY ordinal",(section['parse_run_id'],section['loinc_code']))]
        # Save every repeated-code peer so reviewers can verify they were not merged.
        peers=[]
        for peer in repeated:
            row=conn.execute('SELECT source_xml,own_text FROM sections WHERE parse_run_id=? AND ordinal=?',(section['parse_run_id'],peer['ordinal'])).fetchone()
            name=slug+f'.peer-{peer["ordinal"]}.xml'
            (output/name).write_text(row['source_xml']);peers.append({'ordinal':peer['ordinal'],'source_file':name})
        publication=[dict(r) for r in conn.execute('SELECT published_raw,published_date,retrieval_id FROM history_entries WHERE version_id=? ORDER BY run_id DESC,retrieval_id LIMIT 1',(version['id'],))]
        run=conn.execute('SELECT * FROM parse_runs WHERE id=?',(section['parse_run_id'],)).fetchone()
        metadata={'sample':i,'label':label,'setid':version['setid'],'version':version['version'],
          'raw_sha256':version['selected_sha256'],'xml_member':version['xml_member'],
          'publication':publication,'spl_effective_date_raw':version['effective_date'],
          'date_anomaly':bool(version['date_anomaly']),'snapshot':snapshot,'parser_version':PARSER_VERSION,
          'parser_configuration':json.loads(run['config_json']),'loinc_code':section['loinc_code'],
          'instance_ordinal':section['ordinal'],'ancestry_path':section['ancestry_path'],
          'source_xpath':section['source_xpath'],'source_line':section['source_line'],
          'section_status':section['status'],'section_diagnostics':json.loads(section['diagnostics_json']),
          'document_status':run['status'],'document_diagnostics':json.loads(run['diagnostics_json']),
          'same_code_full_instances':repeated,'repeated_code_peer_sources':peers,
          'source_file':source_file,'extracted_file':extracted_file,'tables_file':tables_file,
          'original_document_file':'source-documents/'+original_name,'manual_review':'pending'}
        samples.append(metadata)
        (output/(slug+'.metadata.json')).write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
        peer_links=' · '.join(f'<a href="{escape(p["source_file"])}">instance {p["ordinal"]}</a>' for p in peers)
        cards.append(f'''<article id="sample-{i}"><h2>{i}. {escape(label)}</h2>
<p><code>{escape(version['setid'])}</code> · version {version['version']} · code {escape(section['loinc_code'])} · ordinal {section['ordinal']} · ancestry {escape(section['ancestry_path'])}</p>
<p>Raw SHA-256: <code>{version['selected_sha256']}</code><br>Source line {section['source_line']}; XPath <code>{escape(section['source_xpath'])}</code><br>Parser {PARSER_VERSION}; document {run['status']}; selected instance {section['status']}.</p>
<p><a href="{source_file}">Source fragment XML</a> · <a href="source-documents/{original_name}">Original whole XML member</a> · <a href="{extracted_file}">Extracted text</a> · <a href="{tables_file}">Structured tables JSON</a> · <a href="{slug}.metadata.json">All provenance / diagnostics</a></p>
<p>Every full-narrative instance of this code in this version: {peer_links}.</p>
<p>Invalid source rowspan/colspan values are retained and flagged in JSON/XML; malformed tables are shown as explicitly numbered source rows/cells, without inferring column alignment.</p>
<div class="pair"><section><h3>Source XML fragment</h3><div class="pane"><pre>{escape(section['source_xml'])}</pre></div></section>
<section><h3>Extracted narrative and tables</h3><div class="pane">{''.join(right)}</div></section></div>
<details><summary>Selected-instance diagnostics</summary><pre>{escape(json.dumps(metadata['section_diagnostics'],indent=2))}</pre></details></article>''')
    header='''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>GitRx — five Step C audit samples</title>
<style>body{font:15px/1.5 system-ui,sans-serif;margin:24px;color:#18212c;background:#f5f7fa}main{max-width:1600px;margin:auto}article{background:white;padding:24px;margin:28px 0;border:1px solid #c9d3df;border-radius:8px}.pair{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:16px}.pane{height:680px;overflow:auto;border:1px solid #c9d3df;padding:16px;background:#fff}pre{font:12px/1.5 ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere}code{overflow-wrap:anywhere}h3{font-size:16px}.source-table{border-collapse:collapse;width:100%;font-size:12px;margin:20px 0}.source-table td,.source-table th{border:1px solid #8093aa;padding:6px;text-align:left;vertical-align:top}details{margin:12px 0}.excerpt{border-left:4px solid #c07c10;padding-left:12px}.narrative{white-space:pre-wrap}@media(max-width:800px){.pair{grid-template-columns:1fr}.pane{height:480px}body{margin:8px}article{padding:12px}}</style></head><body><main>
<h1>Five Step C extraction samples for manual audit</h1>
<p>Local static review artifact. Left: serialized source section XML. Right: extracted direct and descendant narratives, rendered structured tables, delimited search text, and separately retained highlights. Media markers are references; no OCR was performed. Rendering is a review aid, not a reproduction of DailyMed’s stylesheet.</p>
<p>The original whole XML member is also linked for exact source bytes. No sample has been marked human-reviewed. Check section order, inline word boundaries, omitted text, table spans/header associations, repeated-code separation, and highlights versus full narrative.</p>'''
    (output/'index.html').write_text(header+f'<p>Archive snapshot: <code>{snapshot}</code></p>'+''.join(cards)+'</main></body></html>')
    (output/'SAMPLES.json').write_text(json.dumps(samples,ensure_ascii=False,indent=2)+'\n')
    checklist=['# Manual extraction audit','',f'Archive snapshot: `{snapshot}`. All five samples await human review.','',
      '| Sample | Source/extraction reviewed | Missing or changed text | Table/ordering concerns | Reviewer/date |','|---|---|---|---|---|']
    checklist += [f"| {s['sample']}. {s['label']} | pending | | | |" for s in samples]
    (output/'CHECKLIST.md').write_text('\n'.join(checklist)+'\n')
    print(f'Wrote five source/extraction pairs to {output}/index.html')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path.cwd())
    opts=parser.parse_args();root=opts.root.resolve()
    conn=connect(root/'data/gitrx.sqlite')
    build(conn,root,root/'reports/audit')


if __name__=='__main__':main()
