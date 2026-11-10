"""Explicit, offline, resumable validation. Importing this module never starts work."""
import argparse
import csv
from collections import Counter
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import random
import re
import statistics
import sys

from .benchmark import code_hashes
from .config import open_reader
from .query import Query
from .readers import canonical_sid

FORMAT = 'gitrx-validation-study-1'
STRATA = {'srlc_positive': 50, 'comparison': 30, 'challenge': 20}
SRLC_ONLY = {'srlc_positive': 100}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation protects completed results and historical scoring.
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')


def atomic_result(path, value):
    path = Path(path)
    temporary = path.with_suffix('.pending')
    with temporary.open('w') as stream:
        json.dump(value, stream, sort_keys=True, allow_nan=False)
    temporary.replace(path)


def software():
    return code_hashes()


def init(directory):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=False)
    write_new(root/'plan.json', {
        'format': FORMAT, 'config': 'ABSOLUTE/PATH/TO/PINNED/backend.json',
        'snapshot': 'REPLACE_WITH_RELEASE_SHA256', 'backend': 'sqlite', 'seed': 20261108,
        'start': '2016-01-01', 'end': '2025-12-31', 'followup_end': 'REPLACE_YYYY-MM-DD',
        'quotas': STRATA, 'timeout_seconds': 30, 'max_pages': 10000,
        'max_versions': 10000, 'candidates': [],
        'evidence_files': [],
        'instructions': 'Populate candidate frame independently; do not put reference answers here.'})
    write_new(root/'candidate-example.json', {
        'id': 'CASE001', 'setid': 'REPLACE_WITH_VERIFIED_SETID',
        'stratum': 'srlc_positive', 'mapping_status': 'verified',
        'mapping_evidence': ['local evidence file or source URL'],
        'source_refs': ['SrLC event or independently reviewed source reference'],
        'application': 'NDA/BLA number', 'product': 'name/formulation/labeler',
        'code': '34066-1', 'phrases': [],
        'note': 'A phrase list tests literal retrieval only, not clinical equivalence.'})
    write_new(root/'reference-example.json', {
        'format': FORMAT, 'manifest_sha256': 'FROM_FROZEN_MANIFEST', 'cases': [{
            'id': 'CASE001', 'reviewers': ['reviewer-a', 'reviewer-b'],
            'adjudicated': False, 'source_refs': [],
            'fidelity': [{'version': 1, 'ordinal': 1, 'expected_text': 'INDEPENDENT SOURCE TRANSCRIPTION',
                          'expected_title': 'INDEPENDENT SOURCE HEADING'}],
            'event_counts': {'status': 'pending', 'tp': 0, 'fp': 0, 'fn': 0,
                             'basis': 'independently adjudicated original-document transitions'},
            'srlc_counts': {'status': 'pending', 'matched': 0, 'unmatched_reference': 0,
                            'unmatched_gitrx': 0, 'unknown': 0},
            'timing': [{'status': 'pending', 'reference_version': None, 'observed_version': None,
                        'reference_date': None, 'observed_date': None,
                        'regulatory_date': None, 'communication_date': None,
                        'uncertainty': 'unknown', 'source_refs': []}]}]})
    return {'status': 'templates_only', 'directory': str(root.resolve()), 'study_started': False}


def import_frame(plan_path, csv_path, output):
    plan = read(plan_path)
    with Path(csv_path).open(newline='') as stream:
        rows = list(csv.DictReader(stream))
    candidates = []
    for row in rows:
        item = dict(row)
        for key in ('mapping_evidence', 'source_refs', 'phrases'):
            item[key] = json.loads(item.get(key) or '[]')
        candidates.append(item)
    plan['candidates'] = candidates
    # Preserve relative-path meaning when the new plan is written elsewhere.
    base = Path(plan_path).resolve().parent
    plan['config'] = str((base/plan['config']).resolve())
    plan['evidence_files'] = [str((base/p).resolve()) for p in plan.get('evidence_files', [])]
    plan['evidence_files'].append(str(Path(csv_path).resolve()))
    validate_plan(plan)
    write_new(output, plan)
    return {'status': 'frame_imported', 'candidates': len(candidates), 'study_started': False}


def validate_plan(plan):
    if plan.get('format') != FORMAT or plan.get('backend') not in ('sqlite', 'git'):
        raise ValueError('Explicit format and backend required')
    if not re.fullmatch('[0-9a-f]{64}', plan.get('snapshot', '')):
        raise ValueError('Pinned snapshot SHA-256 required')
    if not (date.fromisoformat(plan['start']) <= date.fromisoformat(plan['end']) <= date.fromisoformat(plan['followup_end'])):
        raise ValueError('Invalid study/follow-up dates')
    quotas = plan.get('quotas')
    if quotas not in (STRATA, SRLC_ONLY):
        raise ValueError('Choose the original 50/30/20 design or 100 SrLC-positive families')
    for field, upper in [('max_pages', 10000), ('max_versions', 10000), ('timeout_seconds', 60)]:
        if type(plan.get(field)) is not int or not 1 <= plan[field] <= upper:
            raise ValueError('Invalid '+field)
    if type(plan.get('seed')) is not int:
        raise ValueError('Integer sampling seed required')
    ids, sids = set(), set()
    for item in plan.get('candidates', []):
        if not re.fullmatch('[A-Za-z0-9_-]{1,80}', item.get('id', '')):
            raise ValueError('Safe unique case id required')
        sid = canonical_sid(item['setid'])
        if sid != item['setid'] or sid in sids or item['id'] in ids:
            raise ValueError('Duplicate or noncanonical family/case')
        ids.add(item['id']); sids.add(sid)
        if item.get('stratum') not in STRATA or item.get('mapping_status') not in ('verified', 'ambiguous', 'unresolved'):
            raise ValueError('Explicit stratum and mapping status required')
        if not item.get('mapping_evidence') or not item.get('source_refs'):
            raise ValueError('Source and mapping evidence required')
        if quotas == SRLC_ONLY and (item['stratum'] != 'srlc_positive' or item.get('mapping_status') != 'verified'
                                   or not item.get('srlc_entry')):
            raise ValueError('SrLC-only study requires a documented SrLC entry and verified mapping for every candidate')
        phrases = item.get('phrases', [])
        if not isinstance(phrases, list) or len(phrases) > 20 or any(not isinstance(p, str) or not p.strip() or len(p) > 500 for p in phrases):
            raise ValueError('Invalid literal phrase list')
        if not isinstance(item.get('code'), str) or not item['code']:
            raise ValueError('Explicit target source code required')
    return plan


def freeze(plan_path, output):
    path = Path(plan_path).resolve(); plan = validate_plan(read(path))
    config = (path.parent/plan['config']).resolve()
    cfg = read(config)
    if cfg['snapshot'] != plan['snapshot']:
        raise ValueError('Configured release does not match study snapshot')
    if not plan.get('evidence_files'):
        raise ValueError('Provide local frozen source/frame evidence_files before freezing')
    evidence = {str((path.parent/p).resolve()): hashlib.sha256((path.parent/p).resolve().read_bytes()).hexdigest()
                for p in plan['evidence_files']}
    rng = random.Random(plan['seed']); selected = []
    for stratum, count in plan['quotas'].items():
        frame = sorted((c for c in plan['candidates'] if c['stratum'] == stratum), key=lambda c: c['id'])
        if len(frame) < count:
            raise ValueError('Insufficient independently assembled frame: '+stratum)
        selected.extend(rng.sample(frame, count))
    body = {'format': FORMAT, 'plan': plan, 'config': str(config),
            'config_sha256': hashlib.sha256(config.read_bytes()).hexdigest(),
            'software': software(), 'evidence_files': evidence, 'cases': sorted(selected, key=lambda c: c['id']),
            'scope': 'Selected families in a frozen local release; no collection or clinical inference'}
    write_new(output, {'body': body, 'sha256': digest(body)})
    return {'status': 'frozen', 'families': len(selected), 'manifest': str(output), 'sha256': digest(body)}


def load_manifest(path):
    value = read(path); body = value['body']
    if value['sha256'] != digest(body):
        raise ValueError('Manifest integrity mismatch')
    validate_plan(body['plan'])
    if len(body['cases']) != 100 or Counter(c['stratum'] for c in body['cases']) != Counter(body['plan']['quotas']):
        raise ValueError('Invalid frozen sample')
    if len({c['setid'] for c in body['cases']}) != 100 or len({c['id'] for c in body['cases']}) != 100:
        raise ValueError('Duplicate selected families')
    if any(c not in body['plan']['candidates'] for c in body['cases']):
        raise ValueError('Selected case outside source frame')
    if hashlib.sha256(Path(body['config']).read_bytes()).hexdigest() != body['config_sha256']:
        raise ValueError('Configuration drift')
    if software() != body['software']:
        raise ValueError('Software drift; retain old results and freeze a new study')
    for file, fingerprint in body['evidence_files'].items():
        if hashlib.sha256(Path(file).read_bytes()).hexdigest() != fingerprint:
            raise ValueError('Frozen source/frame evidence drift: '+file)
    return value


def review_forms(manifest_path, output):
    manifest = load_manifest(manifest_path)
    root = Path(output); root.mkdir(parents=True, exist_ok=False)
    form = {'format': FORMAT, 'manifest_sha256': manifest['sha256'], 'cases': []}
    for case in manifest['body']['cases']:
        # Deliberately omit strata, search phrases, and GitRx predictions.
        form['cases'].append({'id': case['id'], 'setid': case['setid'], 'product': case.get('product'),
            'reviewers': [], 'adjudicated': False, 'source_refs': [], 'fidelity': [],
            'event_counts': {'status': 'pending'}, 'srlc_counts': {'status': 'pending'}, 'timing': [],
            'notes': 'Transcribe original sources independently; do not copy GitRx outputs.'})
    for filename in ('reviewer-A.json', 'reviewer-B.json', 'adjudication.json'):
        write_new(root/filename, form)
    return {'status': 'blank_blinded_forms_created', 'cases': len(form['cases']), 'study_started': False}


def pages(query, method, arguments, field, continuation, parameter, max_pages, timeout):
    args = dict(arguments); answers = []; seen = set()
    for _ in range(max_pages):
        answer = getattr(query, method)(**args, timeout_seconds=timeout)
        answers.append(answer)
        token = answer.get(continuation)
        if token is None:
            rows = [r for a in answers for r in a[field]]
            return {'pages': answers, 'rows': rows, 'complete': True,
                    'completion_meaning': 'Pagination complete; not proof of source completeness or full excerpts',
                    'truncated_rows': sum(bool(r.get('truncated') or r.get('matches_truncated')) for r in rows)}
        key = digest(token)
        if key in seen:
            raise ValueError('Non-advancing pagination')
        seen.add(key); args[parameter] = token
    raise ValueError('Incomplete scan: max_pages reached')


def full_version(query, sid, version, plan):
    result = pages(query, 'show', {'sid': sid, 'version': version, 'max_chars': 200000},
                   'sections', 'next_section_offset', 'section_offset', plan['max_pages'], plan['timeout_seconds'])
    sections = []
    for section in result['rows']:
        section = dict(section); chunks = [section['text']]; cursor = section['next_offset']; count = 0
        while cursor is not None:
            count += 1
            if count > plan['max_pages']:
                raise ValueError('Incomplete section text')
            answer = query.show(sid=sid, version=version, ordinal=section['ordinal'], offset=cursor,
                                max_chars=200000, timeout_seconds=plan['timeout_seconds'])
            row = answer['sections'][0]; chunks.append(row['text']); nxt = row['next_offset']
            if nxt is not None and nxt <= cursor:
                raise ValueError('Non-advancing text pagination')
            cursor = nxt
        section.update(text=''.join(chunks), truncated=False, next_offset=None)
        if len(section['text']) != section['text_characters']:
            raise ValueError('Section reconstruction length mismatch')
        sections.append(section)
    # Page-level evidence/date fields retained without duplicating excerpt text.
    headers = [{k: v for k, v in a.items() if k != 'sections'} for a in result['pages']]
    return {'version': version, 'sections': sections, 'metadata_pages': headers, 'complete': True}


def collect_case(query, case, plan, checkpoint):
    sid = case['setid']
    inventory = checkpoint('inventory', lambda: pages(query, 'versions', {'sid': sid}, 'versions',
                           'next_offset', 'offset', plan['max_pages'], plan['timeout_seconds']))
    versions = [r['version'] for r in inventory['rows']]
    if len(versions) > plan['max_versions']:
        raise ValueError('History exceeds frozen max_versions; no complete claim')
    # Preserve all captured versions, including baseline and follow-up; analytic
    # eligibility is adjudicated using the frozen date windows, never guessed here.
    for version in versions:
        checkpoint('version-'+str(version), lambda v=version: full_version(query, sid, v, plan))
    for i, phrase in enumerate(case.get('phrases', [])):
        checkpoint('phrase-'+str(i), lambda p=phrase: pages(query, 'phrase_history',
            {'sid': sid, 'phrase': p, 'code': case['code']}, 'observations', 'continuation', 'cursor',
            plan['max_pages'], plan['timeout_seconds']))
    for previous, version in zip(versions, versions[1:]):
        checkpoint('changes-'+str(previous)+'-'+str(version), lambda a=previous, b=version: pages(
            query, 'version_changes', {'sid': sid, 'version': b, 'previous': a, 'code': case['code'], 'max_chars': 200000},
            'changes', 'next_offset', 'offset', plan['max_pages'], plan['timeout_seconds']))
    return {'captured_versions': len(versions), 'mapping_status': case['mapping_status'],
            'meaning': 'Extraction completed; clinical correctness and source completeness not established'}


def verify_tasks(directory, status):
    expected = status.get('task_hashes', {})
    if not expected or set(expected) != {p.name for p in directory.glob('task-*.json')}:
        raise ValueError('Missing or unexpected completed checkpoints')
    for name, fingerprint in expected.items():
        file = directory/name
        value = read(file)
        if fingerprint != hashlib.sha256(file.read_bytes()).hexdigest() or value['sha256'] != digest(value['result']):
            raise ValueError('Checkpoint corruption: '+str(file))


def run(manifest_path, output, max_cases=None, retry_errors=False):
    manifest = load_manifest(manifest_path); body = manifest['body']; root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    binding = {'manifest_sha256': manifest['sha256'], 'format': FORMAT}
    if (root/'binding.json').exists():
        if read(root/'binding.json') != binding:
            raise ValueError('Output belongs to another frozen study')
    else:
        if any(root.iterdir()):
            raise ValueError('Refusing unbound nonempty output directory')
        write_new(root/'binding.json', binding)
    if max_cases is not None and (type(max_cases) is not int or max_cases < 1):
        raise ValueError('max_cases must be positive')
    handled = 0
    with open_reader(body['config'], body['plan']['backend']) as reader:
        if reader.snapshot != body['plan']['snapshot']:
            raise ValueError('Reader release drift')
        query = Query(reader)
        for case in body['cases']:
            directory = root/case['id']; directory.mkdir(exist_ok=True)
            status_path = directory/'status.json'
            if status_path.exists():
                prior = read(status_path)
                if prior['status'] == 'complete':
                    # Completed checkpoints are verified on every resume.
                    verify_tasks(directory, prior)
                    continue
                if not retry_errors:
                    continue
            if max_cases is not None and handled >= max_cases:
                break
            def checkpoint(name, operation):
                file = directory/('task-'+name+'.json')
                if file.exists():
                    value = read(file)
                    if value['sha256'] != digest(value['result']):
                        raise ValueError('Checkpoint corruption: '+str(file))
                    return value['result']
                answer = operation()
                if software() != body['software'] or hashlib.sha256(Path(body['config']).read_bytes()).hexdigest() != body['config_sha256']:
                    raise ValueError('Software changed during evaluation')
                write_new(file, {'result': answer, 'sha256': digest(answer)})
                return answer
            try:
                summary = collect_case(query, case, body['plan'], checkpoint)
                status = {'status': 'complete', 'summary': summary,
                          'task_hashes': {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                          for p in directory.glob('task-*.json')}}
            except Exception as exc:
                status = {'status': 'error', 'type': type(exc).__name__, 'message': str(exc)}
            atomic_result(status_path, status); handled += 1
    load_manifest(manifest_path)
    return study_status(root, manifest)


def study_status(root, manifest):
    root = Path(root); counts = Counter()
    for case in manifest['body']['cases']:
        path = root/case['id']/'status.json'
        counts[read(path)['status'] if path.exists() else 'pending'] += 1
    return {'format': FORMAT, 'manifest_sha256': manifest['sha256'], 'families': dict(counts),
            'evaluation_complete': counts['complete'] == 100,
            'meaning': 'Collection status only; no validation accuracy claim'}


def rate(success, total):
    if not total:
        return {'numerator': success, 'denominator': total, 'estimate': None, 'wilson_95': None}
    p = success/total; z = 1.959963984540054; denominator = 1+z*z/total
    center = (p+z*z/(2*total))/denominator
    half = z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/denominator
    return {'numerator': success, 'denominator': total, 'estimate': p,
            'wilson_95': [max(0, center-half), min(1, center+half)]}


def normalized(text):
    return ' '.join(text.split())


def score(manifest_path, output, reference_path, destination):
    manifest = load_manifest(manifest_path); root = Path(output); reference = read(reference_path)
    if reference.get('format') != FORMAT or reference.get('manifest_sha256') != manifest['sha256']:
        raise ValueError('Reference must bind to this frozen manifest')
    if read(root/'binding.json')['manifest_sha256'] != manifest['sha256']:
        raise ValueError('Output manifest mismatch')
    cases = {c['id']: c for c in manifest['body']['cases']}; refs = {}
    for row in reference['cases']:
        if row['id'] not in cases or row['id'] in refs:
            raise ValueError('Unknown or duplicate reference case')
        if row.get('adjudicated') is not True or len(set(row.get('reviewers', []))) < 2 or not row.get('source_refs'):
            raise ValueError('Adjudicated independent review and source references required')
        refs[row['id']] = row
    results = []
    for cid, case in cases.items():
        status_file = root/cid/'status.json'
        disposition = read(status_file)['status'] if status_file.exists() else 'pending'
        result = {'id': cid, 'stratum': case['stratum'], 'collection': disposition,
                  'reference': 'available' if cid in refs else 'pending', 'fidelity': [], 'timing': []}
        if cid in refs and disposition == 'complete':
            verify_tasks(root/cid, read(status_file))
            ref = refs[cid]
            for check in ref.get('fidelity', []):
                task = read(root/cid/('task-version-'+str(check['version'])+'.json'))
                if task['sha256'] != digest(task['result']):
                    raise ValueError('Checkpoint corruption')
                if check.get('ordinal') is None:
                    header = task['result']['metadata_pages'][0]
                    found = dict(header, raw_sha256=header['citation']['raw_sha256'],
                                 section_code_counts=dict(Counter(s['source_code'] or '<uncoded>' for s in task['result']['sections'])))
                else:
                    found = next((s for s in task['result']['sections'] if s['ordinal'] == check['ordinal']), None)
                fields = {key.removeprefix('expected_'): value for key, value in check.items() if key.startswith('expected_')}
                allowed = {'text', 'title', 'source_code', 'parent_ordinal', 'ancestry',
                           'publication_dates', 'effective_date', 'raw_sha256', 'section_code_counts', 'version', 'setid'}
                if not fields or set(fields)-allowed:
                    raise ValueError('Invalid independently transcribed fidelity fields')
                agreement = {key: found is not None and (normalized(found[key]) == normalized(value) if key == 'text' else found[key] == value)
                             for key, value in fields.items()}
                result['fidelity'].append({'version': check['version'], 'ordinal': check['ordinal'], 'agreement': agreement})
            for name in ('event_counts', 'srlc_counts'):
                values = ref.get(name, {'status': 'pending'})
                if values.get('status') not in ('pending', 'evaluable', 'unknown'):
                    raise ValueError('Invalid event disposition')
                if values.get('status') == 'evaluable':
                    keys = ('tp', 'fp', 'fn') if name == 'event_counts' else ('matched', 'unmatched_reference', 'unmatched_gitrx', 'unknown')
                    if any(type(values.get(k)) is not int or values[k] < 0 for k in keys):
                        raise ValueError('Nonnegative adjudicated event counts required')
                result[name] = values
            for timing in ref.get('timing', []):
                if timing.get('status') != 'evaluable':
                    result['timing'].append({'status': timing.get('status', 'pending')}); continue
                if not timing.get('source_refs') or timing.get('uncertainty') not in ('bounded', 'first_observed', 'complete_captured'):
                    raise ValueError('Timing requires source references and captured-evidence qualification')
                for key in ('reference_version', 'observed_version'):
                    if type(timing.get(key)) is not int or timing[key] < 1:
                        raise ValueError('Timing version required')
                manual = date.fromisoformat(timing['reference_date']); observed = date.fromisoformat(timing['observed_date'])
                observation = read(root/cid/('task-version-'+str(timing['observed_version'])+'.json'))['result']['metadata_pages'][0]
                if timing['observed_date'] not in observation['publication_dates']:
                    raise ValueError('Observed timing date not supported by captured version; retain uncertainty instead')
                result['timing'].append({'status': 'evaluable',
                    'version_agrees': timing['reference_version'] == timing['observed_version'],
                    'date_agrees': manual == observed, 'absolute_date_error_days': abs((observed-manual).days),
                    'daily_med_minus_regulatory_days': (observed-date.fromisoformat(timing['regulatory_date'])).days if timing.get('regulatory_date') else None,
                    'daily_med_minus_communication_days': (observed-date.fromisoformat(timing['communication_date'])).days if timing.get('communication_date') else None,
                    'uncertainty': timing['uncertainty']})
        results.append(result)
    strata = {}
    for name in manifest['body']['plan']['quotas']:
        group = [r for r in results if r['stratum'] == name]
        audited = [r for r in group if r['fidelity']]
        events = [r['event_counts'] for r in group if r.get('event_counts', {}).get('status') == 'evaluable']
        tp, fp, fn = (sum(e[k] for e in events) for k in ('tp', 'fp', 'fn'))
        timing = [t for r in group for t in r['timing'] if t['status'] == 'evaluable']
        srlc = [r['srlc_counts'] for r in group if r.get('srlc_counts', {}).get('status') == 'evaluable']
        strata[name] = {'selected_families': len(group), 'fidelity_audited_families': len(audited),
            'family_all_audited_fields_agree': rate(sum(all(all(c['agreement'].values()) for c in r['fidelity']) for r in audited), len(audited)),
            'event_evaluable_families': len(events), 'event_counts': {'tp': tp, 'fp': fp, 'fn': fn},
            'event_precision_descriptive': tp/(tp+fp) if tp+fp else None,
            'event_sensitivity_descriptive': tp/(tp+fn) if tp+fn else None,
            'srlc_evaluable_families': len(srlc),
            'srlc_correspondence_counts': {k: sum(v[k] for v in srlc) for k in ('matched', 'unmatched_reference', 'unmatched_gitrx', 'unknown')},
            'timing_evaluable_events': len(timing),
            'timing_version_agreement_descriptive': sum(t['version_agrees'] for t in timing)/len(timing) if timing else None,
            'timing_median_absolute_error_days': statistics.median(t['absolute_date_error_days'] for t in timing) if timing else None}
    report = {'format': FORMAT, 'manifest_sha256': manifest['sha256'],
              'reference_sha256': hashlib.sha256(Path(reference_path).read_bytes()).hexdigest(),
              'collection': study_status(root, manifest), 'by_stratum': strata, 'cases': results,
              'limitations': ['No pooled population accuracy from enriched sampling.',
                'Family Wilson intervals assume independent families; shared applications/class actions can violate this.',
                'Repeated-event metrics are descriptive; no falsely independent event confidence intervals.',
                'Event correspondence and timing entries require human adjudication, not an automatic clinical classifier.',
                'No SrLC entry does not establish no true change; unmatched GitRx changes are not automatically false positives.',
                'Reference presence does not prove reviewer independence; retain source records and review workflow.']}
    write_new(destination, report)
    return {'status': 'scored', 'report': str(destination), 'reviewed_families': len(refs)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('init'); p.add_argument('directory')
    p = commands.add_parser('freeze'); p.add_argument('--plan', required=True); p.add_argument('--output', required=True)
    p = commands.add_parser('import-frame'); p.add_argument('--plan', required=True); p.add_argument('--csv', required=True); p.add_argument('--output', required=True)
    p = commands.add_parser('review-forms'); p.add_argument('--manifest', required=True); p.add_argument('--output', required=True)
    p = commands.add_parser('run'); p.add_argument('--manifest', required=True); p.add_argument('--output', required=True)
    p.add_argument('--max-cases', type=int); p.add_argument('--retry-errors', action='store_true')
    p = commands.add_parser('status'); p.add_argument('--manifest', required=True); p.add_argument('--output', required=True)
    p = commands.add_parser('score'); p.add_argument('--manifest', required=True); p.add_argument('--output', required=True)
    p.add_argument('--reference', required=True); p.add_argument('--report', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'init': answer = init(args.directory)
        elif args.command == 'freeze': answer = freeze(args.plan, args.output)
        elif args.command == 'import-frame': answer = import_frame(args.plan, args.csv, args.output)
        elif args.command == 'review-forms': answer = review_forms(args.manifest, args.output)
        elif args.command == 'run': answer = run(args.manifest, args.output, args.max_cases, args.retry_errors)
        elif args.command == 'status': answer = study_status(args.output, load_manifest(args.manifest))
        else: answer = score(args.manifest, args.output, args.reference, args.report)
    except (ValueError, KeyError, OSError, TypeError) as exc:
        print(json.dumps({'error': str(exc), 'study_success': False}), file=sys.stderr); return 2
    print(json.dumps(answer, indent=2)); return 0


if __name__ == '__main__':
    raise SystemExit(main())
