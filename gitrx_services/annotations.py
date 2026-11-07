"""Offline annotation CI: schema, URL and exact pinned snapshot target checks."""
from pathlib import Path
from datetime import date
import argparse, hashlib, json, sys
from .schema import errors

ROOT = Path(__file__).resolve().parents[1]

def validate(annotation, schema, catalog):
    problems = errors(annotation, schema)
    if problems:
        return problems
    target = annotation['target']
    key = (target['snapshot_id'], target['setid'], target['version'], target['raw_sha256'])
    record = next((x for x in catalog['versions'] if (x['snapshot_id'], x['setid'], x['version'], x['raw_sha256']) == key), None)
    if record is None or target['section_instance'] not in record['section_instances']:
        problems.append('Target does not exist in the pinned evidence/parse inventory')
    if not annotation['claim'].strip(): problems.append('Claim is blank')
    if not annotation['author'].strip(): problems.append('Author is blank')
    if annotation['relation'] == 'caused' and not annotation.get('source_statement', '').strip():
        problems.append('Caused requires the explicit causal statement from the cited source')
    review = annotation['review']
    if review['status'] == 'approved':
        if not review['reviewer'].strip() or review['reviewer'].strip().casefold() == annotation['author'].strip().casefold() or not review['independent']:
            problems.append('Approval requires one independent named reviewer other than the author')
        try:
            if date.fromisoformat(review['review_date']).isoformat() != review['review_date']: raise ValueError()
        except ValueError:
            problems.append('Approval requires an ISO review date')
    return problems

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--schema', type=Path, default=ROOT / 'annotations/schema.json')
    p.add_argument('--catalog', type=Path, default=ROOT / 'annotations/catalog.json')
    p.add_argument('--pin', type=Path, default=ROOT / 'annotations/catalog-pin.json')
    p.add_argument('paths', nargs='*', type=Path)
    a = p.parse_args()
    pin = json.loads(a.pin.read_bytes())
    for file, field in [(a.catalog, 'catalog_sha256'), (a.schema, 'schema_sha256')]:
        if hashlib.sha256(file.read_bytes()).hexdigest() != pin[field]:
            raise SystemExit('Pinned catalog/schema hash mismatch')
    schema = json.loads(a.schema.read_bytes()); catalog = json.loads(a.catalog.read_bytes())
    paths = a.paths or sorted((ROOT / 'annotations/records').glob('*.json'))
    failures = []
    ids = set()
    for path in paths:
        try:
            value = json.loads(path.read_bytes())
            problems = validate(value, schema, catalog)
            if isinstance(value, dict) and isinstance(value.get('id'), str):
                if value['id'] in ids: problems.append('Duplicate annotation id')
                ids.add(value['id'])
        except (ValueError, OSError) as exc:
            problems = [type(exc).__name__]
        if problems: failures.append({'path': str(path), 'errors': problems})
    print(json.dumps({'annotations': len(paths), 'failures': failures, 'catalog_snapshot_ids': catalog['snapshots']}, indent=2))
    if failures: raise SystemExit(1)

if __name__ == '__main__':
    main()
