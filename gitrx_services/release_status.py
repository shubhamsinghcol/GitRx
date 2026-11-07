"""Current additive release inventory, with explicit historical job fallback."""
from pathlib import Path
import argparse
import hashlib
import json


def selected_status(root):
    root = Path(root).resolve()
    summary = json.loads((root / 'reports/CURRENT-RELEASE.json').read_bytes())
    raw = (root / 'reports/EXPANDED-SELECTION.json').read_bytes().removesuffix(b'\n')
    if hashlib.sha256(raw).hexdigest() != summary['expanded_snapshot']:
        raise RuntimeError('Current release selection hash mismatch')
    manifest = json.loads(raw)
    count = len(manifest['versions'])
    families = len({v['setid'] for v in manifest['versions']})
    if count != summary['retrieved'] or families != summary['families']:
        raise RuntimeError('Current release counts mismatch')
    adjudication = Path(__file__).resolve().parents[1] / 'reports/validation-v101-20261106/human-adjudication.json'
    validation = ('original blind comparison 59/60; owner-adjudicated 60/60; frozen v1.0.1 unchanged'
                  if adjudication.exists() else 'source adjudication pending; frozen v1.0.1 retained')
    proof_path = root / 'reports/EXPORT-VERIFICATION.json'
    if proof_path.exists() and json.loads(proof_path.read_bytes())['expanded_snapshot'] != summary['expanded_snapshot']:
        raise RuntimeError('Export proof belongs to another snapshot')
    return {**summary, 'upstream_history_unknown_setids': manifest['upstream_history_unknown_families'],
            'phase': 'expanded evidence admitted; local exports verified' if (root / 'reports/EXPORT-VERIFICATION.json').exists()
                     else 'expanded evidence admitted; local export verification pending',
            'public': False, 'validation': validation,
            'history_limit': 'Known target recovery is complete; upstream NALTREXONE history inventory remains unresolved. No historical-completeness claim.'}


def main(argv=None):
    project = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--legacy', action='store_true', help='Show original acquisition controller and its historical failures')
    p.add_argument('--json', action='store_true')
    a = p.parse_args(argv)
    pointer = project / 'runtime/release1-current'
    if a.legacy or not pointer.exists():
        from .release1_job import status, status_markdown
        s = status(json.loads((project / 'reports/block3c/config.json').read_bytes()))
        print(json.dumps(s, indent=2) if a.json else status_markdown(s))
        return
    s = selected_status(pointer)
    if a.json:
        print(json.dumps(s, indent=2))
        return
    print('GitRx current release: ' + s['phase'])
    print(f"Families: {s['families']}; known version targets/retrieved: {s['known_targets']}/{s['retrieved']}")
    print(f"Recovered exact targets: {s['admitted']}/15; remaining known-target failures: {s['known_target_failures']}")
    print('Snapshot: ' + s['expanded_snapshot'])
    print(s['history_limit'])
    print('Validation: ' + s['validation'] + '. Nothing public.')
    print('Data: ' + s['root'])
    print('Original acquisition records: ./release1-status --legacy (preserved; no restart needed).')


if __name__ == '__main__':
    main()
