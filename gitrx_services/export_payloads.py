"""Bounded, offline export payload preparation from immutable source databases."""
from pathlib import Path
import ast
import gzip
import hashlib
import json
import sqlite3

from gitrx.current.db import dumps
from gitrx_services.io import ensure_space
from gitrx_services.recovered_release import sha


def exporter_path(project):
    from gitrx.current import export_git
    local=Path(project) / "gitrx/current/export_git.py"
    return local if local.is_file() else Path(export_git.__file__)


def query_recipe(project):
    tree = ast.parse(exporter_path(project).read_text())
    values = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    return {'sections': next(q for q in values if 'SELECT ordinal,parent_ordinal' in q),
            'tables': next(q for q in values if 'SELECT s.ordinal section_ordinal' in q)}


def prepare(base, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    index_path = output / 'INDEX.json'
    recipe = query_recipe(base.project)
    exporter = sha(exporter_path(base.project))
    if index_path.exists():
        index = json.loads(index_path.read_bytes())
        if index['expanded_snapshot'] != base.snapshot or index['exporter_sha256'] != exporter:
            raise ValueError('Prepared export cache belongs to another recipe')
        return index
    grouped = {}
    for version in base.manifest['versions']:
        key = version['parse_runs'][0]['id']
        prefix, pid = key.split(':', 1)
        grouped.setdefault(prefix, []).append((key, int(pid), base.parse_versions[(prefix, int(pid))]))
    entries = {}
    for prefix, selected in grouped.items():
        source, root = base.sources[prefix]
        size = (root / 'data/gitrx.sqlite').stat().st_size
        if size > 4 * 1024**3:
            raise RuntimeError('Memory-copy bound: source exceeds 4 GiB; specify a new bounded plan')
        ensure_space(output, 1024**3)
        memory = sqlite3.connect(':memory:')
        memory.row_factory = sqlite3.Row
        try:
            # A single sequential page copy avoids per-section cold overflow
            # page I/O. Destination is transient RAM; source is read-only.
            source.backup(memory)
            source.execute('PRAGMA shrink_memory')
            for key, pid, version_id in selected:
                payload = {}
                for name, query in recipe.items():
                    if 'WHERE s.parse_run_id=?' in query:
                        query = query.replace('WHERE s.parse_run_id=?', 'WHERE s.parse_run_id=? AND s.version_id=?')
                    else:
                        query = query.replace('WHERE parse_run_id=?', 'WHERE parse_run_id=? AND version_id=?')
                    payload[name] = [dict(r) for r in memory.execute(query, (pid, version_id))]
                raw = dumps(payload).encode()
                relative = prefix + '/' + str(pid) + '.json.gz'
                target = output / relative
                target.parent.mkdir(exist_ok=True)
                if target.exists():
                    if gzip.decompress(target.read_bytes()) != raw:
                        raise ValueError('Existing prepared payload conflict')
                else:
                    with target.open('xb') as f:
                        f.write(gzip.compress(raw, compresslevel=1, mtime=0))
                entries[key] = {'path': relative, 'sha256': hashlib.sha256(raw).hexdigest()}
        finally:
            memory.close()
            source.execute('PRAGMA shrink_memory')
        print(json.dumps({'prepared_source': prefix, 'versions': len(selected),
                          'total_prepared': len(entries), 'source_database_bytes': size}), flush=True)
    index = {'format': 'gitrx-prepared-export-payloads-1', 'expanded_snapshot': base.snapshot,
             'exporter_sha256': exporter, 'entries': entries,
             'source_databases_modified': False, 'memory_copy_limit_GiB': 4,
             'normalization_or_context_changes': False}
    if len(entries) != len(base.manifest['versions']):
        raise ValueError('Prepared payload inventory differs from selected parses')
    index_path.write_text(dumps(index) + '\n')
    return index


class PayloadCache:
    def __init__(self, root, index):
        self.root, self.index = Path(root), index
        self.key, self.payload = None, None

    def read(self, key, table):
        if self.key != key:
            entry = self.index['entries'][key]
            raw = gzip.decompress((self.root / entry['path']).read_bytes())
            if hashlib.sha256(raw).hexdigest() != entry['sha256']:
                raise ValueError('Prepared payload hash mismatch')
            self.key, self.payload = key, json.loads(raw)
        return iter(self.payload[table])
