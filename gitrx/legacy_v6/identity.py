"""Versioned, explicit heading/context membership fallback; original codes stay intact."""
import hashlib
import json
from pathlib import Path
import re

from .archive import LOINC, now
from .db import dumps
from .normalize import normalize

POLICY_VERSION='section-identity-0.1.0'
ROOT_ALIASES={'patient information leaflet':'42230-3'}


def heading(text):
    return normalize(text or '',ignore_case=True).strip().rstrip('.:')


def setup(conn):
    conn.executescript('''CREATE TABLE IF NOT EXISTS identity_policies (
      id TEXT PRIMARY KEY, version TEXT NOT NULL, created_at TEXT NOT NULL, policy_json TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS section_identities (
      policy_id TEXT NOT NULL REFERENCES identity_policies(id), section_id INTEGER NOT NULL REFERENCES sections(id),
      canonical_code TEXT, section_confidence TEXT NOT NULL, rule_id TEXT NOT NULL,
      normalized_heading TEXT NOT NULL, evidence_json TEXT NOT NULL,
      PRIMARY KEY(policy_id,section_id));''')


def propose_reviewed_rules(conn,root):
    """Capture exact observed heading/context pairs, reviewed against source hierarchy.

    These are subsection-membership rules, not invented fine-grained LOINC codes.
    No arbitrary new heading is accepted when the policy is later applied.
    """
    pairs={}
    rows=conn.execute('''SELECT id,parse_run_id,ordinal,parent_ordinal,loinc_code,code_system,title,
      LENGTH(TRIM(own_text)) AS narrative_chars FROM sections ORDER BY parse_run_id,ordinal''').fetchall()
    by_run={}
    for r in rows:by_run.setdefault(r['parse_run_id'],{})[r['ordinal']]=r
    for r in rows:
        if r['loinc_code']!='<uncoded>' or not r['narrative_chars']:continue
        title=heading(r['title'])
        if not title:continue
        parent=r['parent_ordinal'];anchor=None
        while parent is not None:
            a=by_run[r['parse_run_id']][parent]
            if a['code_system']==LOINC and a['loinc_code']!='<uncoded>':anchor=a;break
            parent=a['parent_ordinal']
        if anchor is not None:
            pairs.setdefault(anchor['loinc_code'],set()).add(title)
    policy={'version':POLICY_VERSION,'root_aliases':ROOT_ALIASES,
      'context_headings':{code:sorted(values) for code,values in sorted(pairs.items())},
      'root_position_rule':'patient information leaflet must be a top-level trailing section after how-supplied',
      'subsection_rule':'exact reviewed normalized heading under the nearest coded ancestor, or a resolved trailing patient leaflet; canonical code denotes ancestor membership',
      'numbered_rule':'if both parent and child headings carry section numbers, their major section number must agree',
      'no_heading_rule':'no heading means unknown; do not infer from content'}
    # Explicit legacy patient-leaflet children were reviewed as leaflet content,
    # never reclassified as warnings or indications from their subject matter.
    leaflet=[]
    for r in rows:
        if r['loinc_code']=='<uncoded>' and r['narrative_chars'] and heading(r['title']):
            a=r;parent=r['parent_ordinal']
            while parent is not None:
                a=by_run[r['parse_run_id']][parent]
                if heading(a['title'])=='patient information leaflet':leaflet.append(heading(r['title']));break
                parent=a['parent_ordinal']
    policy['context_headings'].setdefault('42230-3',[])
    policy['context_headings']['42230-3']=sorted(set(policy['context_headings']['42230-3'])|set(leaflet))
    path=Path(root)/'research/section-identity-policy.json'
    if not path.exists():path.write_text(json.dumps(policy,ensure_ascii=False,indent=2)+'\n')
    return json.loads(path.read_text())


def apply(conn,root,parse_ids=None,parent_policy=None):
    setup(conn)
    if parent_policy:
        policy=json.loads(conn.execute('SELECT policy_json FROM identity_policies WHERE id=?',(parent_policy,)).fetchone()[0])
        policy={**policy,'parent_policy':parent_policy,'admitted_parse_ids':sorted(parse_ids)}
    else:
        policy=propose_reviewed_rules(conn,root)
    payload=dumps(policy);policy_id=hashlib.sha256(payload.encode()).hexdigest()
    if parse_ids is None and parent_policy is None:
        pinned=conn.execute("SELECT 1 FROM snapshots WHERE json_extract(manifest_json,'$.identity_policy')=? LIMIT 1",(policy_id,)).fetchone()
        missing=conn.execute('''SELECT 1 FROM sections s LEFT JOIN section_identities i
          ON i.section_id=s.id AND i.policy_id=? WHERE i.section_id IS NULL LIMIT 1''',(policy_id,)).fetchone()
        if pinned and missing:
            raise ValueError('Pinned identity policy cannot gain rows; use scoped parent_policy admission')
    conn.execute('INSERT OR IGNORE INTO identity_policies VALUES(?,?,?,?)',(policy_id,POLICY_VERSION,now(),payload))
    if parent_policy:
        conn.execute('''INSERT OR IGNORE INTO section_identities
          SELECT ?,section_id,canonical_code,section_confidence,rule_id,normalized_heading,evidence_json
          FROM section_identities WHERE policy_id=?''',(policy_id,parent_policy))
    runs=conn.execute('SELECT id FROM parse_runs ORDER BY id').fetchall()
    for run in runs:
        if parse_ids is not None and run['id'] not in parse_ids:continue
        rows=conn.execute('''SELECT id,parse_run_id,ordinal,parent_ordinal,loinc_code,code_system,title
          FROM sections WHERE parse_run_id=? ORDER BY ordinal''',(run['id'],)).fetchall()
        resolved={};source={r['ordinal']:r for r in rows}
        for r in rows:
            title=heading(r['title']);canonical=None;confidence='unknown';rule='unknown/no-supported-rule';evidence={}
            if r['code_system']==LOINC and r['loinc_code']!='<uncoded>':
                canonical=r['loinc_code'];confidence='coded';rule='coded/source-loinc'
            elif title in policy['root_aliases'] and r['parent_ordinal'] is None:
                previous=[p for p in rows if p['ordinal']<r['ordinal'] and p['parent_ordinal'] is None]
                if previous and previous[-1]['loinc_code']=='34069-5':
                    canonical=policy['root_aliases'][title];confidence='inferred';rule='heading/trailing-patient-leaflet'
                    evidence={'previous_root_ordinal':previous[-1]['ordinal'],'previous_root_code':'34069-5'}
            elif title:
                parent=r['parent_ordinal'];anchor=None
                while parent is not None:
                    identity=resolved[parent]
                    if identity['canonical_code'] and (identity['section_confidence']=='coded' or identity['rule_id']=='heading/trailing-patient-leaflet'):
                        anchor=source[parent];break
                    parent=source[parent]['parent_ordinal']
                if anchor is not None:
                    code=resolved[anchor['ordinal']]['canonical_code']
                    parent_number=re.match(r'^(\d+)(?:\.|\s)',heading(anchor['title']))
                    child_number=re.match(r'^(\d+)\.',title)
                    compatible=not(parent_number and child_number) or parent_number[1]==child_number[1]
                    if compatible and title in policy['context_headings'].get(code,[]):
                        canonical=code;confidence='inferred';rule='heading/exact-reviewed-ancestor-membership'
                        evidence={'anchor_ordinal':anchor['ordinal'],'anchor_title':anchor['title'],
                                  'anchor_confidence':resolved[anchor['ordinal']]['section_confidence'],
                                  'membership_only':True,'numbering_compatible':compatible}
            value={'canonical_code':canonical,'section_confidence':confidence,'rule_id':rule}
            resolved[r['ordinal']]=value
            conn.execute('INSERT OR IGNORE INTO section_identities VALUES(?,?,?,?,?,?,?)',
                         (policy_id,r['id'],canonical,confidence,rule,title,dumps(evidence)))
    conn.commit()
    (Path(root)/'reports/IDENTITY-POLICY-ID.txt').write_text(policy_id+'\n')
    return policy_id
