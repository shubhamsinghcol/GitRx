"""Evidence-only protection policy; fixed user-approved metadata exclusions."""
from fnmatch import fnmatchcase
from pathlib import PurePosixPath, Path
from .release1_http import hash_file, Stop

# Fixed approval in Block 3c-2. No additional wildcard/extension exclusions.
METADATA_EXCLUSIONS = (
    '.DS_Store', '._*', '.Spotlight-V100', '.fseventsd', '.Trashes',
    'Thumbs.db', 'desktop.ini', '__pycache__', '*.pyc', '.pytest_cache',
)

def exclusion_pattern(path):
    """A metadata component also excludes its contents, never sibling paths."""
    for component in PurePosixPath(str(path).replace('\\', '/')).parts:
        for pattern in METADATA_EXCLUSIONS:
            if fnmatchcase(component, pattern):
                return pattern
    return None

def filter_inventory(hashes):
    return {path: sha for path, sha in hashes.items() if exclusion_pattern(path) is None}

def audit_inventory(root, hashes):
    root = Path(root)
    included = filter_inventory(hashes)
    excluded = [{'path': p, 'pattern': exclusion_pattern(p), 'old_sha256': h}
                for p, h in hashes.items() if exclusion_pattern(p) is not None]
    changes = []
    for path, expected in included.items():
        p = root / path
        actual = hash_file(p) if p.is_file() else None
        if actual != expected:
            changes.append({'path': path, 'expected_sha256': expected, 'actual_sha256': actual})
    return {'old_inventory_count': len(hashes), 'protected_count': len(included),
            'excluded_count': len(excluded), 'excluded': excluded, 'changes': changes,
            'verified_count': len(included) - len(changes)}

def verify_inventory(root, hashes):
    included = filter_inventory(hashes)
    for path, expected in included.items():
        p = Path(root) / path
        if not p.is_file() or hash_file(p) != expected:
            raise Stop('PROTECTED_HASH_CHANGED:' + str(path))
    return len(included)
