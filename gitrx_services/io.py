"""Read-only snapshot access and conservative available-space guards."""
from pathlib import Path
import json
import shutil
import sqlite3

FLOOR = 20 * 1024**3

def ensure_space(path, additional_bytes=0):
    path = Path(path).absolute()
    while not path.exists():
        path = path.parent
    if shutil.disk_usage(path).free - additional_bytes < FLOOR:
        raise RuntimeError('STOP_FREE_SPACE_20_GiB')

def write_json(path, value):
    path = Path(path)
    body = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()
    ensure_space(path, len(body) + 1024 * 1024)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)

def readonly(path):
    from gitrx.current.content_storage import ContentConnection
    c = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, factory=ContentConnection)
    c.row_factory = sqlite3.Row
    if c.execute("SELECT 1 FROM sqlite_master WHERE name='content_store_meta'").fetchone():
        c.enable_content_layout()
    c.execute('PRAGMA query_only=ON')
    return c

def retrieved_by(version, kind):
    return any(r['kind'] == kind and r['status'] == 200 and r['state'] in ('available', 'downloaded')
               and r['artifact_sha256'] == version['selected_sha256'] for r in version['retrievals'])
