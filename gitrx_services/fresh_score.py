"""One-time fresh tranche1 scoring; never executes or tunes a classifier."""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess

from tools.score_e1g_once import (
    CONTEXTS, UNSCORABLE, score as legacy_metrics, subset_report, validate_labels,
)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode()


def decision(errors):
    if errors == 0:
        return {'branch': 'zero-errors', 'sample_gate': 'passed',
                'action': 'Retain independent score; continue local release engineering.'}
    if errors == 1:
        return {'branch': 'one-error', 'sample_gate': 'repair-required',
                'action': 'Specific regression/fix, freeze v1.0.2, verify guards, goldens, integrity and structural recurrence; used cases are development data.'}
    return {'branch': 'two-or-more-errors', 'sample_gate': 'stopped',
            'action': 'Repair and freeze a new version, then draw a fresh independent sample; no release.'}


def score_fresh(labels, answers):
    ids = {x['id'] for x in answers['answers']}
    validate_labels(labels, answers['packet_id'], ids)
    if len(answers['answers']) != len(ids) or len(ids) != 60:
        raise ValueError('Exactly 60 distinct frozen answers required')
    normalized = []
    for row in answers['answers']:
        normalized.append({
            'id': row['id'], 'prediction': row['prediction'],
            'inclusion_probability': row['conditional_inclusion_probability'],
            'audit_flags': {'non_plr': row['form'] == 'non-plr',
                            'outside_body_coded': False, 'body_boxed': False},
            'structural_cluster': str(row['cluster']), 'text_cluster': row['id'],
        })
    result = legacy_metrics(labels, {'answers': normalized})
    result.pop('by_stratum')
    result.pop('by_text_cluster')
    humans = {x['id']: x for x in labels['labels']}
    rows = [dict(id=x['id'], human=humans[x['id']]['my_label'],
                 predicted=x['prediction']['section_context'],
                 confidence=x['prediction']['context_confidence'],
                 pi=x['conditional_inclusion_probability'], source=x)
            for x in answers['answers']]
    result['by_form'] = {str(k): subset_report([r for r in rows if r['source']['form'] == k])
                         for k in sorted({r['source']['form'] for r in rows})}
    result['by_labeler'] = {str(k): subset_report([r for r in rows if r['source']['labeler'] == k])
                            for k in sorted({r['source']['labeler'] for r in rows})}
    result['by_audit_flag'] = {
        key: subset_report([r for r in rows if r['source'].get(key)])
        for key in ('non_plr_precautions', 'lookalike', 'audit', 'ifu', 'leaflet_container')}
    result['case_comparison'] = [
        {'id': r['id'], 'human_label': r['human'], 'frozen_prediction': r['predicted'],
         'confidence': r['confidence'],
         'agreement': r['human'] == r['predicted'] if r['human'] in CONTEXTS else None,
         'human_notes': humans[r['id']].get('notes', '')} for r in rows]
    result['decision'] = decision(result['overall']['anchored_errors'])
    result['frozen_version'] = 'gitrx-rules-v1.0.1'
    result['sampling_limits'] = (
        'Anchored-only tranche1 sample; conditional inclusion weights; not global recall '
        'or release1-wide validation. Conditional binomial intervals and weighted point '
        'estimates are separate. Audit groups overlap.')
    return result


def immutable(path, body):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != body:
            raise RuntimeError('Immutable output conflict: ' + str(path))
        return
    with path.open('xb') as f:
        f.write(body)
        f.flush()
        os.fsync(f.fileno())
    path.chmod(0o444)


def run(root, labels_path, output):
    root, labels_path, output = Path(root), Path(labels_path), Path(output)
    public = root / 'reports/block2-validation'
    manifest = json.loads((public / 'manifest.json').read_bytes())
    freeze = json.loads((root / 'reports/block1c/RULE-FREEZE.json').read_bytes())
    bundle = Path(freeze['repository'])
    recipe = json.loads((bundle / 'manifest.json').read_bytes())
    if hashlib.sha256(canonical(recipe)).hexdigest() != freeze['manifest_sha256']:
        raise RuntimeError('Frozen rule manifest changed')
    if any(sha(bundle / p) != h for p, h in recipe['files'].items()):
        raise RuntimeError('Frozen rule bundle changed')
    commit = subprocess.check_output(['git', '-C', str(bundle), 'rev-parse', freeze['tag']], text=True).strip()
    if commit != freeze['commit'] or manifest['rules_manifest_sha256'] != freeze['manifest_sha256']:
        raise RuntimeError('Frozen rule identity mismatch')
    sealed = root / 'reports/block2-sealed/answers.json'
    for path, expected in [(public / 'items.json', manifest['source_items_sha256']),
                           (public / 'index.html', manifest['page_sha256']),
                           (sealed, manifest['sealed_answers_sha256'])]:
        if sha(path) != expected:
            raise RuntimeError('Validation input hash changed: ' + str(path))
    provenance = json.loads((public / 'input-provenance.json').read_bytes())
    if sha(public / 'manifest.json') != provenance['packet_manifest_sha256']:
        raise RuntimeError('Public manifest commitment changed')
    if sha(root / 'reports/block2/PARSER-SELECTION.json') != provenance['parser_selection_sha256']:
        raise RuntimeError('Parser selection changed')
    for asset, expected in manifest['assets'].items():
        if sha(public / asset) != expected:
            raise RuntimeError('Source image changed')
    raw = labels_path.read_bytes()
    labels = json.loads(raw)
    ids = {x['id'] for x in json.loads((public / 'labels-template.json').read_bytes())['labels']}
    validate_labels(labels, manifest['packet_id'], ids)
    label_hash = hashlib.sha256(raw).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    lock, result_path = output / 'SCORE-ONCE.json', output / 'results.json'
    if lock.exists():
        recorded = json.loads(lock.read_bytes())
        if recorded['labels_sha256'] != label_hash or not result_path.exists():
            raise RuntimeError('Existing/incomplete one-time score requires audit; no rescore')
        return json.loads(result_path.read_bytes())
    stamp = datetime.now(timezone.utc).isoformat()
    immutable(output / 'original-human-export.json', raw)
    record = {'at': stamp, 'labels_sha256': label_hash, 'source_export': str(labels_path),
              'packet_id': manifest['packet_id'], 'rules_tag': freeze['tag'],
              'rules_manifest_sha256': freeze['manifest_sha256'],
              'sealed_answers_sha256': manifest['sealed_answers_sha256'],
              'scorer_sha256': sha(__file__),
              'prior_comparison': 'Other chat unsealed after completed labels, reporting 59/60 and one discrepancy; no rule changes since. This is the formal immutable score of those fixed inputs.'}
    immutable(lock, canonical(record) + b'\n')
    # Only after complete human validation, immutable preservation and locking.
    answers = json.loads(sealed.read_bytes())
    if answers['packet_id'] != manifest['packet_id'] or answers['draw_hash'] != manifest['draw_hash']:
        raise RuntimeError('Sealed packet identity mismatch')
    result = score_fresh(labels, answers)
    result['provenance'] = record
    immutable(result_path, json.dumps(result, ensure_ascii=False, indent=2).encode() + b'\n')
    lines = ['id,human_label,frozen_prediction,confidence,agreement']
    for row in result['case_comparison']:
        lines.append(','.join(str(row[k]) for k in ('id', 'human_label', 'frozen_prediction', 'confidence', 'agreement')))
    immutable(output / 'case-comparison.csv', ('\n'.join(lines) + '\n').encode())
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument('--labels', type=Path)
    p.add_argument('--output', type=Path)
    a = p.parse_args()
    result = run(a.root, a.labels or a.root / 'reports/block2-validation/gitrx-tranche1-human-labels.json',
                 a.output or a.root / 'reports/validation-v101-20261106')
    print(json.dumps({'overall': {k: result['overall'][k] for k in (
        'sampled', 'scorable', 'agreements', 'anchored_scorable', 'anchored_errors', 'excluded')},
        'decision': result['decision'], 'disagreements': result['disagreements']}))


if __name__ == '__main__':
    main()
