"""Synthetic-only study tests: never read study candidates or held-out answers."""
import json
from pathlib import Path
import pytest
from test_backend_portable import release, SID
from gitrx_backend import validation_study as study


def plan_for(release, tmp_path):
    candidates = []
    for i, group in enumerate([g for g, n in study.STRATA.items() for _ in range(n)]):
        candidates.append({'id': f'case{i:03}', 'setid': SID if i == 0 else f'{i:08x}-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
            'stratum': group, 'mapping_status': 'verified', 'mapping_evidence': ['synthetic source'],
            'source_refs': ['synthetic reference'], 'code': '34066-1', 'phrases': ['changed']})
    evidence = tmp_path/'source-frame.txt'; evidence.write_text('independent synthetic evidence')
    plan = {'format': study.FORMAT, 'config': str(release), 'snapshot': 'f'*64, 'backend': 'sqlite',
        'seed': 12, 'start': '2016-01-01', 'end': '2025-12-31', 'followup_end': '2026-12-31',
        'quotas': study.STRATA, 'timeout_seconds': 30, 'max_pages': 100, 'max_versions': 1000,
        'candidates': candidates, 'evidence_files': [str(evidence)]}
    path = tmp_path/'plan.json'; study.write_new(path, plan)
    manifest = tmp_path/'manifest.json'; study.freeze(path, manifest)
    return path, manifest


def test_init_only_and_no_overwrite(tmp_path):
    result = study.init(tmp_path/'templates')
    assert result['study_started'] is False
    assert not list((tmp_path/'templates').glob('task-*'))
    with pytest.raises(FileExistsError): study.init(tmp_path/'templates')


def test_freeze_is_deterministic_and_refuses_drift(release, tmp_path, monkeypatch):
    plan, manifest = plan_for(release, tmp_path)
    other = tmp_path/'other.json'; study.freeze(plan, other)
    assert study.read(other) == study.read(manifest)
    assert len(study.load_manifest(manifest)['body']['cases']) == 100
    study.review_forms(manifest, tmp_path/'forms')
    form = study.read(tmp_path/'forms/reviewer-A.json')
    assert len(form['cases']) == 100
    assert all('stratum' not in c and 'phrases' not in c for c in form['cases'])
    monkeypatch.setattr(study, 'software', lambda: {'changed': 'code'})
    with pytest.raises(ValueError, match='Software drift'): study.load_manifest(manifest)


def test_resume_real_synthetic_reader_and_score(release, tmp_path):
    _, manifest = plan_for(release, tmp_path); output = tmp_path/'out'
    answer = study.run(manifest, output, max_cases=1)
    assert answer['families'] == {'complete': 1, 'pending': 99}
    task = output/'case000/task-version-2.json'
    assert study.read(task)['result']['sections'][0]['text'] == 'renal warning changed'
    before = task.read_bytes()
    # No more work is requested here; completed checkpoints retain their bytes.
    assert study.study_status(output, study.load_manifest(manifest))['evaluation_complete'] is False
    reference = {'format': study.FORMAT, 'manifest_sha256': study.read(manifest)['sha256'], 'cases': [{
        'id': 'case000', 'adjudicated': True, 'reviewers': ['A', 'B'], 'source_refs': ['independent synthetic original'],
        'fidelity': [{'version': 2, 'ordinal': 1, 'expected_text': 'renal warning changed', 'expected_title': 'Warning'},
                     {'version': 1, 'ordinal': 2, 'expected_text': 'incorrect transcription'}],
        'event_counts': {'status': 'evaluable', 'tp': 1, 'fp': 1, 'fn': 0},
        'srlc_counts': {'status': 'evaluable', 'matched': 1, 'unmatched_reference': 0, 'unmatched_gitrx': 1, 'unknown': 0},
        'timing': [{'status': 'evaluable', 'reference_version': 2, 'observed_version': 2,
                    'reference_date': '2026-01-01', 'observed_date': '2026-01-01',
                    'regulatory_date': '2026-01-03', 'uncertainty': 'first_observed', 'source_refs': ['source']}]}]}
    ref = tmp_path/'reference.json'; study.write_new(ref, reference)
    report = tmp_path/'report.json'; study.score(manifest, output, ref, report)
    data = study.read(report)
    assert data['by_stratum']['srlc_positive']['event_precision_descriptive'] == .5
    assert data['by_stratum']['srlc_positive']['family_all_audited_fields_agree']['estimate'] == 0
    assert data['cases'][0]['timing'][0]['daily_med_minus_regulatory_days'] == -2
    assert task.read_bytes() == before
    with pytest.raises(FileExistsError): study.score(manifest, output, ref, report)
    corrupted = study.read(task); corrupted['result']['sections'][0]['text'] = 'tampered'
    task.write_text(json.dumps(corrupted))
    with pytest.raises(ValueError, match='corruption'): study.run(manifest, output, max_cases=1)


def test_pagination_bounds_and_section_text_reassembly():
    class Query:
        def show(self, **args):
            text = 'abcdefgh'; offset = args.get('offset', 0)
            end = min(offset+3, len(text))
            return {'sections': [{'ordinal': 1, 'text': text[offset:end], 'text_characters': len(text),
                    'next_offset': end if end < len(text) else None, 'truncated': end < len(text)}], 'next_section_offset': None}
    result = study.full_version(Query(), SID, 1, {'max_pages': 10, 'timeout_seconds': 1})
    assert result['sections'][0]['text'] == 'abcdefgh'
    with pytest.raises(ValueError, match='Incomplete section'): study.full_version(Query(), SID, 1, {'max_pages': 1, 'timeout_seconds': 1})
    class Repeats:
        def versions(self, **args): return {'versions': [], 'next_offset': 1}
    with pytest.raises(ValueError, match='Non-advancing'): study.pages(Repeats(), 'versions', {}, 'versions', 'next_offset', 'offset', 3, 1)


def test_plan_rejects_duplicates_missing_sources_and_unpinned_release(release, tmp_path):
    path, _ = plan_for(release, tmp_path); plan = study.read(path)
    plan['candidates'][1]['setid'] = SID
    with pytest.raises(ValueError, match='Duplicate'): study.validate_plan(plan)
    plan = study.read(path); plan['candidates'][0]['source_refs'] = []
    with pytest.raises(ValueError, match='evidence'): study.validate_plan(plan)
    plan = study.read(path); plan['snapshot'] = 'latest'
    with pytest.raises(ValueError, match='Pinned'): study.validate_plan(plan)


def test_interrupted_case_resumes_checkpoints_without_rewriting(release, tmp_path, monkeypatch):
    _, manifest = plan_for(release, tmp_path); output = tmp_path/'out'
    original = study.collect_case
    def interrupted(query, case, plan, checkpoint):
        checkpoint('inventory', lambda: study.pages(query, 'versions', {'sid': case['setid']},
                    'versions', 'next_offset', 'offset', plan['max_pages'], plan['timeout_seconds']))
        raise RuntimeError('synthetic interruption')
    monkeypatch.setattr(study, 'collect_case', interrupted)
    assert study.run(manifest, output, max_cases=1)['families']['error'] == 1
    file = output/'case000/task-inventory.json'; original_bytes = file.read_bytes()
    monkeypatch.setattr(study, 'collect_case', original)
    assert study.run(manifest, output, max_cases=1, retry_errors=True)['families']['complete'] == 1
    assert file.read_bytes() == original_bytes
    (output/'case000/task-version-1.json').unlink()
    with pytest.raises(ValueError, match='Missing'): study.run(manifest, output, max_cases=1)


def test_source_frame_drift_is_refused(release, tmp_path):
    _, manifest = plan_for(release, tmp_path)
    (tmp_path/'source-frame.txt').write_text('changed')
    with pytest.raises(ValueError, match='evidence drift'): study.load_manifest(manifest)


def test_srlc_only_requires_verified_documented_membership(release, tmp_path):
    path, _ = plan_for(release, tmp_path); plan = study.read(path)
    plan['quotas'] = study.SRLC_ONLY
    for candidate in plan['candidates']:
        candidate['stratum'] = 'srlc_positive'
        candidate['srlc_entry'] = {'Application Number': '000001', 'Link': 'https://example.invalid/synthetic'}
    study.validate_plan(plan)
    plan['candidates'][0]['mapping_status'] = 'ambiguous'
    with pytest.raises(ValueError, match='SrLC-only'): study.validate_plan(plan)
    plan['candidates'][0]['mapping_status'] = 'verified'
    plan['candidates'][0].pop('srlc_entry')
    with pytest.raises(ValueError, match='SrLC-only'): study.validate_plan(plan)
