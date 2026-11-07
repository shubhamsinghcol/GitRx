"""Locked validation packets and human-label scoring; no model ground truth."""
import hashlib
import json
from pathlib import Path

from .contexts import CONTEXTS
from .db import dumps

LABELS=(*CONTEXTS,'uncertain','unreadable')


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def load_packet(path):
    path=Path(path);wrapper=json.loads((path/'manifest.json').read_text());manifest=wrapper['manifest']
    if digest(manifest)!=wrapper['packet_id']:raise ValueError('Packet manifest hash changed')
    raw=(path/'items.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=manifest['items_sha256']:raise ValueError('Packet items hash changed')
    if hashlib.sha256((path/'classifier.py').read_bytes()).hexdigest()!=manifest['classifier_sha256']:raise ValueError('Frozen classifier changed')
    items=json.loads(raw)
    if len(items)!=60 or len({i['section_id'] for i in items})!=60:raise ValueError('Expected 60 distinct native owning instances')
    if {split:sum(i['split']==split for i in items) for split in ('development','held-out')}!={'development':30,'held-out':30}:raise ValueError('Validation split changed')
    for item in items:
        if digest(item['source'])!=item['source_sha256'] or digest(item['prediction'])!=item['prediction_sha256']:raise ValueError('Source/prediction hash changed')
    return wrapper['packet_id'],items


def metrics(rows,anchored_only=False):
    matrix={truth:{p:0 for p in (*CONTEXTS,'abstain')} for truth in CONTEXTS}
    excluded=[]
    for item,label in rows:
        if label not in CONTEXTS:
            excluded.append({'id':item['id'],'label':label});continue
        prediction=item['prediction']
        pred=prediction['section_context'] if not anchored_only or prediction['context_confidence']=='anchored' else 'abstain'
        matrix[label][pred]+=1
    result={}
    for context in CONTEXTS:
        tp=matrix[context][context];actual=sum(matrix[context].values());predicted=sum(r[context] for r in matrix.values())
        result[context]={'true_positive':tp,'predicted_positive':predicted,'actual_positive':actual,
          'precision':tp/predicted if predicted else None,'recall':tp/actual if actual else None}
    return {'mode':'anchored-only; default/unknown abstain' if anchored_only else 'all assigned contexts, including defaults/unknowns',
      'scorable':sum(sum(row.values()) for row in matrix.values()),'excluded_uncertain_unreadable':excluded,
      'confusion_matrix':matrix,'per_context':result,
      'abstentions':sum(row['abstain'] for row in matrix.values()),'undefined_metrics':'null means denominator zero, not 0% or 100%'}


def score_packet(path,labels_path):
    packet_id,items=load_packet(path);labels=json.loads(Path(labels_path).read_text())
    if labels.get('packet_id')!=packet_id:raise ValueError('Labels belong to a different packet')
    entries=labels.get('labels',[])
    if len(entries)!=60 or len({r['id'] for r in entries})!=60:raise ValueError('Expected 60 unique label entries; development labels may be blank')
    by_id={r['id']:r.get('my_label','') for r in entries}
    if set(by_id)!={i['id'] for i in items}:raise ValueError('Missing or unexpected label IDs')
    for item in items:
        label=by_id[item['id']]
        if label and label not in LABELS:raise ValueError('Invalid human label: '+label)
        if item['split']=='held-out' and not label:raise ValueError('Held-out labels incomplete; no precision/recall can be computed')
    held=[(i,by_id[i['id']]) for i in items if i['split']=='held-out']
    return {'packet_id':packet_id,'evaluated_split':'held-out','held_out_cases':30,
      'rule_tuning':'none authorized from held-out outcomes; baseline classifier/predictions are frozen',
      'assignment_metrics':metrics(held),'fail_closed_metrics':metrics(held,True),
      'limitations':['Stratified, within-corpus sample; not a prevalence or unseen-corpus estimate.',
        'Context labels do not validate whether LOINC codes or clinical text are correct.',
        'Human labels are shown predictions by the requested UI; this is not blinded validation.']}


def main():
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('packet',type=Path);p.add_argument('labels',type=Path);p.add_argument('--output',type=Path)
    args=p.parse_args()
    try:result=score_packet(args.packet,args.labels)
    except (ValueError,KeyError) as e:p.error(str(e))
    text=json.dumps(result,ensure_ascii=False,indent=2)+'\n'
    if args.output:args.output.write_text(text)
    else:print(text,end='')


if __name__=='__main__':main()
