"""Finite, resumable official DailyMed listing census; never promotes a release.

A completed listing is a current discovery frame, not an archived-only census.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import time
from urllib.parse import urlencode
from uuid import UUID

from .jobs import HTTP, atomic, sha
from gitrx_services.io import ensure_space

BASE = 'https://dailymed.nlm.nih.gov/dailymed/services/v2/'


def validate_page(value, page, expected=None):
    meta = value['metadata']
    total, pages = int(meta['total_elements']), max(1, int(meta['total_pages']))
    if int(meta['current_page']) != page or total < 0 or pages > 20000:
        raise ValueError('Invalid listing pagination')
    if expected is not None and (total, pages) != expected:
        raise ValueError('Listing changed during census; retain this run and begin a new run')
    rows = value['data']
    if not isinstance(rows, list) or (not rows and total):
        raise ValueError('Empty or malformed listing page')
    for row in rows:
        if not isinstance(row,dict):raise ValueError('Malformed listing row')
        # A discovery census preserves upstream anomalies. Positive version
        # identity is enforced later, before any ZIP is admitted as a label.
        int(row['spl_version'])
    return total, pages


class Census:
    def __init__(self, config):
        self.config_path = Path(config).absolute()
        self.config_bytes = self.config_path.read_bytes()
        self.config = json.loads(self.config_bytes)
        if self.config['format'] != 'gitrx-census-1':
            raise ValueError('Unsupported census configuration')
        self.root = Path(self.config['workspace']).absolute()
        self.volume = Path(self.config['volume']).absolute()
        self.check_volume()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = (self.root/'writer.lock').open('a+')
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pin = self.root/'config-pin.json'
        if pin.exists() and pin.read_bytes() != self.config_bytes:
            raise ValueError('Configuration changed; use a separate workspace')
        if not pin.exists():
            with pin.open('xb') as f:
                f.write(self.config_bytes); f.flush(); os.fsync(f.fileno())
        self.c = sqlite3.connect(self.root/'census.sqlite')
        self.c.row_factory = sqlite3.Row
        self.c.executescript('''PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL;
          CREATE TABLE IF NOT EXISTS pages(page INTEGER PRIMARY KEY,sha256 TEXT,path TEXT,total INTEGER,pages INTEGER,rows INTEGER);
          CREATE TABLE IF NOT EXISTS families(setid TEXT PRIMARY KEY,page INTEGER,row_json TEXT);
          CREATE TABLE IF NOT EXISTS listing_anomalies(page INTEGER,ordinal INTEGER,reason TEXT,row_json TEXT,PRIMARY KEY(page,ordinal));
        ''')
        if self.c.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('Census database integrity check failed')
        self.http = HTTP(self.root/'network', self.config['request_cap'], 2,
                         guard=self.check_volume, max_bytes=self.config.get('max_bytes', 2*1024**3))

    def check_volume(self):
        # A vanished /Volumes mount must never become a directory on the laptop.
        if not self.volume.is_mount() or os.stat(self.volume).st_dev == os.stat('/').st_dev:
            raise RuntimeError('External volume unavailable')
        marker = self.volume/self.config['volume_marker']
        if marker.read_text().strip() != self.config['volume_token']:
            raise RuntimeError('Wrong external volume')
        if not self.root.resolve().is_relative_to(self.volume.resolve()):
            raise ValueError('Workspace must be on configured external volume')
        ensure_space(self.volume, 256*1024**2)

    def status(self):
        first = self.c.execute('SELECT * FROM pages ORDER BY page LIMIT 1').fetchone()
        return {'phase': 'current-listing-census',
                'pages_saved': self.c.execute('SELECT COUNT(*) FROM pages').fetchone()[0],
                'pages_expected': first['pages'] if first else None,
                'families_seen': self.c.execute('SELECT COUNT(*) FROM families').fetchone()[0],
                'listing_rows_seen': self.c.execute('SELECT COALESCE(SUM(rows),0) FROM pages').fetchone()[0],
                'invalid_setid_rows': self.c.execute('SELECT COUNT(*) FROM listing_anomalies').fetchone()[0],
                'nonpositive_listed_versions': self.c.execute("SELECT COUNT(*) FROM families WHERE CAST(json_extract(row_json,'$.spl_version') AS INTEGER)<1").fetchone()[0],
                'families_expected': first['total'] if first else None,
                'requests_used': self.http.c.execute('SELECT COUNT(*) FROM requests').fetchone()[0],
                'complete': (self.root/'manifest.json').exists(),
                'archived_only_inventory': 'unknown', 'updated_epoch': time.time()}

    def run(self, max_pages=None):
        # Verify retained evidence before trusting page checkpoints on every restart.
        for row in self.c.execute('SELECT * FROM pages'):
            if sha(row['path']) != row['sha256']:
                raise RuntimeError('Retained census page hash changed')
        if (self.root/'manifest.json').exists():
            manifest=json.loads((self.root/'manifest.json').read_bytes())
            if manifest['config_sha256']!=sha(self.config_path):raise RuntimeError('Census configuration pin changed')
            if manifest.get('census_index_sha256') and manifest['census_index_sha256']!=sha(self.root/'census.sqlite'):
                raise RuntimeError('Frozen census index hash changed')
            return self.status()
        saved = self.c.execute('SELECT * FROM pages ORDER BY page LIMIT 1').fetchone()
        expected = (saved['total'], saved['pages']) if saved else None
        page = self.c.execute('SELECT COALESCE(MAX(page),0)+1 FROM pages').fetchone()[0]
        processed = 0
        while expected is None or page <= expected[1]:
            if max_pages is not None and processed >= max_pages:
                break
            self.check_volume()
            if (self.root/'STOP').exists():
                break
            url = BASE+'spls.json?'+urlencode({'pagesize':100, 'page':page})
            path = self.http.get(url, reuse=True)
            value = json.loads(path.read_bytes())
            expected = validate_page(value, page, expected)
            with self.c:
                for ordinal,row in enumerate(value['data']):
                    try:sid=str(UUID(row['setid']))
                    except (ValueError,TypeError,KeyError,AttributeError):
                        self.c.execute('INSERT INTO listing_anomalies VALUES(?,?,?,?)',
                                       (page,ordinal,'invalid_setid',json.dumps(row,ensure_ascii=False)))
                        continue
                    # Duplicates indicate pagination drift, never silently collapse them.
                    self.c.execute('INSERT INTO families VALUES(?,?,?)',
                                   (sid, page, json.dumps(row, ensure_ascii=False)))
                self.c.execute('INSERT INTO pages VALUES(?,?,?,?,?,?)',
                               (page, sha(path), str(path), *expected, len(value['data'])))
            page += 1; processed += 1
            atomic(self.root/'status.json', self.status())
        if expected and page > expected[1]:
            count = self.c.execute('SELECT COUNT(*) FROM families').fetchone()[0]
            anomalies=self.c.execute('SELECT COUNT(*) FROM listing_anomalies').fetchone()[0]
            if count+anomalies != expected[0]:
                raise ValueError('Listing count mismatch')
            self.c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            manifest = {'format':'gitrx-current-census-1', 'config_sha256':sha(self.config_path),
                        'families':count, 'scope':'Unfiltered current DailyMed SPL listing',
                        'listed_rows':expected[0], 'invalid_setid_rows':anomalies,
                        'census_index_sha256':sha(self.root/'census.sqlite'),
                        'discovery_rules':'Retain malformed setids separately; never repair or merge them. Nonpositive version numbers retained as source observations.',
                        'archived_only_inventory':'unknown', 'history_inventory':'not yet captured',
                        'consistency':'Paginated observations over time; not an atomic upstream snapshot. Count agreement does not rule out same-count listing changes.',
                        'pages':[dict(r) for r in self.c.execute('SELECT * FROM pages ORDER BY page')],
                        'completed_epoch':time.time()}
            atomic(self.root/'manifest.json', manifest)
        atomic(self.root/'status.json', self.status())
        return self.status()

    def close(self):
        self.http.close(); self.c.close(); self.lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--max-pages', type=int)
    args = parser.parse_args()
    job = Census(args.config)
    try:
        print(json.dumps(job.run(args.max_pages)), flush=True)
    except Exception as exc:
        job.check_volume()
        atomic(job.root/'last-error.json', {'error':str(exc), 'type':type(exc).__name__,
                                          'at_epoch':time.time(), 'resumable':True})
        raise
    finally:
        job.close()


if __name__ == '__main__':
    main()
