"""Read-only report of what the recurring update jobs have recorded (never collects, never infers).

Sources: the weekly DailyMed job (<data root>/updates/dailymed: updates.sqlite, WATERMARK.json) and the
monthly SrLC job (<data root>/srlc/updates/state.sqlite).  Databases are opened immutable and read-only,
so a read never creates journal files, takes locks or contacts the network.  Anything missing,
unreadable or unconfigured is reported as 'unknown' with the reason, never guessed.  The release snapshot
(pinned label data) and these source checks are different things: a check says what an update job last
looked at upstream, not that the release contains it.
"""
import json
import sqlite3
import time
from pathlib import Path

from .readers import canonical_sid

MAX_LOOKUPS = 20000
RUN_FIELDS = ('run_id', 'started_at', 'finished_at', 'status', 'error')
UNKNOWN = 'unknown'


def _data_root(root):
    if root is not None:
        return Path(root)
    from gitrx_core.dataroot import data_root
    value = data_root()
    return Path(value) if value else None


def _open(path):
    return sqlite3.connect('file:%s?immutable=1' % path, uri=True)


def _run(row, columns, keep):
    d = dict(zip(columns, row))
    out = {k: d.get(k) for k in RUN_FIELDS}
    out.update({k: d[k] for k in keep if k in d})
    if out.get('error'):
        out['error'] = str(out['error'])[:200]
    return out


def _runs(conn, keep):
    cur = conn.execute('SELECT * FROM runs ORDER BY started_at DESC, run_id DESC')
    columns = [c[0] for c in cur.description]
    return [_run(r, columns, keep) for r in cur.fetchall()]


def _unknown(reason):
    return {'state': UNKNOWN, 'reason': reason, 'last_attempt': UNKNOWN, 'last_success': UNKNOWN}


def _release_versions(reader, setids, budget_seconds=2.0):
    """setid -> greatest version in the pinned release (None: family absent; 'unchecked': out of time budget)."""
    out = {}
    start = time.monotonic()
    if hasattr(reader, 'family'):
        present = {}
        for sid in setids:
            try:
                present[sid] = reader.family(canonical_sid(sid)) is not None
            except ValueError:
                present[sid] = False
    else:
        known = {f['setid'] for f in reader.families()}
        present = {sid: sid in known for sid in setids}
    for sid in setids:
        if not present[sid]:
            out[sid] = None
        elif time.monotonic() - start > budget_seconds:
            out[sid] = 'unchecked'
        else:
            out[sid] = max((v['version'] for v in reader.versions(canonical_sid(sid))), default=0)
    return out


def dailymed_status(reader, root):
    base = root / 'updates' / 'dailymed'
    db = base / 'updates.sqlite'
    if not db.is_file():
        return _unknown('no DailyMed update state found (updates.sqlite absent); the job may never have run here')
    try:
        conn = _open(db)
    except sqlite3.Error as exc:
        return _unknown('DailyMed update state unreadable (%s)' % type(exc).__name__)
    try:
        runs = _runs(conn, ('mode', 'watermark_in', 'watermark_out', 'cutoff', 'listing_rows', 'candidate_setids',
                            'changed_setids', 'new_versions', 'downloads', 'unavailable'))
        wm = None
        try:
            wm = json.loads((base / 'WATERMARK.json').read_text()).get('watermark')
        except (OSError, ValueError):
            pass
        attempts = runs[0] if runs else UNKNOWN
        ok = [r for r in runs if r['status'] == 'complete' and r.get('mode') != 'smoke']
        out = {'state': 'recorded' if runs else UNKNOWN, 'runs_recorded': len(runs),
               'last_attempt': attempts, 'last_success': ok[0] if ok else UNKNOWN,
               'last_success_note': None if ok else 'No complete (non-smoke) run is recorded; smoke or partial runs do not count as a successful check.',
               'watermark': wm if wm else UNKNOWN,
               'watermark_meaning': 'Newest publication date WATERMARK.json records as fully covered; the job advances it only after a '
                                    'complete non-smoke run. Its origin is not verified here.'}
        if wm and not ok:
            out['watermark_note'] = 'A watermark is present but no complete non-smoke run is recorded, so how it was set is unknown.'
        out['new_since_release'] = _dailymed_new(conn, reader)
        return out
    except sqlite3.Error as exc:
        return _unknown('DailyMed update state unreadable (%s)' % type(exc).__name__)
    finally:
        conn.close()


def _dailymed_new(conn, reader):
    note = ('Counts of what update jobs observed or retrieved compared with the pinned release; observed, not a complete '
            'upstream listing. Retrieved files are not parsed or in any release until an explicit rebuild.')
    listed = conn.execute('SELECT setid, MAX(listed_version) FROM listing_observations WHERE listed_version IS NOT NULL '
                          'GROUP BY setid').fetchall()
    files = conn.execute('SELECT DISTINCT setid, version FROM version_files WHERE status=200 AND zip_ok=1').fetchall()
    sids = {s for s, _ in listed} | {s for s, _ in files}
    if reader is None or len(sids) > MAX_LOOKUPS:
        return {'state': UNKNOWN, 'reason': 'release comparison not performed (%s)' % (
            'no reader' if reader is None else 'more than %d setids observed' % MAX_LOOKUPS), 'meaning': note}
    held = _release_versions(reader, sorted(sids))
    new_setids = sum(1 for s, _ in listed if held[s] is None)
    unchecked = {s for s, h in held.items() if h == 'unchecked'}
    newer = sum(1 for s, v in listed if isinstance(held[s], int) and v > held[s])
    retrieved = sum(1 for s, v in files if held[s] is None or (isinstance(held[s], int) and v > held[s]))
    return {'state': 'counted', 'setids_observed_in_listings': len(listed),
            'observed_setids_absent_from_release': new_setids,
            'observed_setids_with_newer_listed_version_than_release': newer,
            'retrieved_versions_not_in_release': retrieved,
            'retrieved_versions_total': len(files),
            'version_comparison': {'complete': not unchecked, 'setids_not_compared_in_time_budget': len(unchecked),
                                   'note': 'Newer-version and retrieved counts exclude setids not compared; they are lower bounds when incomplete.'},
            'meaning': note}


def srlc_status(root):
    db = root / 'srlc' / 'updates' / 'state.sqlite'
    if not db.is_file():
        return _unknown('no SrLC update state found (state.sqlite absent); the job may never have run here')
    try:
        conn = _open(db)
    except sqlite3.Error as exc:
        return _unknown('SrLC update state unreadable (%s)' % type(exc).__name__)
    try:
        # Counts of the sealed prospective holdout are deliberately not read or reported.
        runs = _runs(conn, ('enum_rows', 'drugs_seen', 'drugs_changed', 'details_fetched', 'csvs_fetched', 'windows_json'))
        for r in runs:
            if r.get('windows_json'):
                try:
                    w = json.loads(r['windows_json'])
                    r['windows'] = w if isinstance(w, list) else UNKNOWN
                except ValueError:
                    r['windows'] = UNKNOWN
            r.pop('windows_json', None)
        ok = [r for r in runs if r['status'] == 'complete']
        out = {'state': 'recorded' if runs else UNKNOWN, 'runs_recorded': len(runs),
               'last_attempt': runs[0] if runs else UNKNOWN, 'last_success': ok[0] if ok else UNKNOWN,
               'last_success_note': None if ok else 'No complete run is recorded.',
               'watermark': UNKNOWN,
               'watermark_meaning': 'The SrLC job keeps no watermark; it re-enumerates recent monthly windows.'}
        last = runs[0] if runs else None
        out['new_since_release'] = {
            'state': 'counted' if last else UNKNOWN,
            'drugs_changed_in_last_attempt': last.get('drugs_changed') if last else UNKNOWN,
            'meaning': 'Drug entries new or changed against the frozen SrLC gradebook baseline in the last attempted run '
                       '(a partial run counts only what it reached); not compared with the label release.'}
        return out
    except sqlite3.Error as exc:
        return _unknown('SrLC update state unreadable (%s)' % type(exc).__name__)
    finally:
        conn.close()


def freshness(reader, root=None):
    """The freshness block for describe_release. Read-only; absent or unreadable state is 'unknown'."""
    base = {'mode': 'snapshot',
            'release_snapshot': {'identifier': getattr(reader, 'snapshot', UNKNOWN),
                                 'built_at': UNKNOWN,
                                 'meaning': 'The pinned release content is frozen; its identifier, not a date, is the snapshot.'},
            'meaning': 'source_checked_at is the finish time of the last complete weekly DailyMed check recorded on this '
                       'machine, or null when none is recorded (unknown). It is not the release date, and a check does not '
                       'mean the release contains what was seen. Reads never collect data; consult sources for details.'}
    try:
        base_root = _data_root(root)
    except Exception:
        base_root = None
    if base_root is None or not base_root.is_dir():
        reason = 'data root not configured or unavailable (external volume absent?); update state cannot be read'
        sources = {'dailymed': _unknown(reason), 'srlc': _unknown(reason)}
    else:
        sources = {'dailymed': dailymed_status(reader, base_root), 'srlc': srlc_status(base_root)}
    last = sources['dailymed'].get('last_success')
    base['source_checked_at'] = last.get('finished_at') if isinstance(last, dict) else None
    base['source_checked_state'] = 'recorded' if base['source_checked_at'] else UNKNOWN
    base['sources'] = sources
    return base
