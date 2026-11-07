from gitrx_services.section_diff_v2 import diff_rows,match_sections,text

def row(code='34071-1',title='Warnings',body='Do not use.',pos=1):return {'loinc_code':code,'code_system':'2.16.840.1.113883.6.1','title':title,'own_text':body,'ordinal':pos}
def test_heading_renamed():
 r=diff_rows([row()],[row(title='WARNINGS AND PRECAUTIONS')]);assert len(r)==1 and not r[0]['content_changed'] and r[0]['heading_changed']
def test_section_moves():
 r=diff_rows([row()],[row(pos=22)]);assert not r[0]['content_changed'] and r[0]['position_changed']
def test_whitespace_and_markup_neutral_authored_text():
 # Parser-flattened authored text is the input; differing source markup is not consumed as prose.
 a=row(body='Do  not\nuse.');a['source_xml']='<text>Do <b>not</b> use.</text>'
 b=row(body='Do not use.');b['source_xml']='<text>Do not use.</text>'
 r=diff_rows([a],[b]);assert not r[0]['content_changed'] and r[0]['raw_text_changed']
def test_repeated_codes_reorder_without_coalescing():
 a=[row(body='Alpha',pos=1),row(body='Beta',pos=2)];b=[row(body='Beta',pos=1),row(body='Alpha',pos=2)]
 r=diff_rows(a,b);assert len(r)==2 and all(not x['content_changed'] for x in r)
def test_real_punctuation_case_and_unit_changes_remain():
 for a,b in [('10 mg','100 mg'),('use.','use?'),('Do not use','do not use'),('≥5','>5')]:assert diff_rows([row(body=a)],[row(body=b)])[0]['content_changed']
def test_uncoded_heading_rename_equal_text():
 r=diff_rows([row(code='',title='Old')],[row(code='',title='New')]);assert len(r)==1 and not r[0]['content_changed']
def test_ambiguous_repeated_changes_not_guessed():
 a=[row(title='',body='A'),row(title='',body='B')];b=[row(title='',body='C'),row(title='',body='D')]
 r=diff_rows(a,b);assert len(r)==4 and all(x['content_changed'] for x in r)
def test_duplicate_multiplicity_preserved():
 r=diff_rows([row(),row()],[row()]);assert len(r)==2 and sum(x['after_index'] is None for x in r)==1

def test_uncoded_sentinel_uses_conservative_fallback_not_sole_code_pair():
 r=diff_rows([row(code='<uncoded>',title='Old',body='Alpha')],[row(code='<uncoded>',title='New',body='Beta')]);assert len(r)==2 and all(x['presence_changed'] for x in r)
