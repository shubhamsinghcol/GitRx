"""Versioned configuration; all data paths resolve relative to its location."""
import json
from pathlib import Path
from .readers import SQLiteReader, GitReader

def open_reader(config_path,backend=None):
    path=Path(config_path).resolve();config=json.loads(path.read_bytes())
    if config.get('format')!='gitrx-backend-config-1':raise ValueError('Unsupported configuration format; no automatic migration')
    if set(config)-{'format','backend','release_root','export_root','meta_commit','snapshot','scope_path','cache_families','search_index','search_index_sha256','evidence_metadata','evidence_metadata_sha256','rebuild_root','rebuild_complete_sha256','names_index','names_index_sha256','verify_shards','release_notes'}:raise ValueError('Unknown configuration field')
    if ('search_index' in config)!=('search_index_sha256' in config):raise ValueError('Search projection requires both path and SHA-256')
    if ('evidence_metadata' in config)!=('evidence_metadata_sha256' in config):raise ValueError('Evidence metadata requires both path and SHA-256')
    backend=backend or config.get('backend','sqlite')
    def data(name):return (path.parent/config[name]).resolve()
    if backend=='rebuild' or config.get('backend')=='rebuild':
        if backend!='rebuild':raise ValueError('This configuration pins a rebuild release; backend override is not allowed')
        for key in ('rebuild_root','rebuild_complete_sha256','names_index','names_index_sha256'):
            if key not in config:raise ValueError('Rebuild configuration requires '+key)
        if config['snapshot']!=config['rebuild_complete_sha256']:raise ValueError('Snapshot must equal the pinned COMPLETE.json SHA-256')
        from .rebuild_reader import RebuildReader
        return RebuildReader(data('rebuild_root'),config['rebuild_complete_sha256'],data('names_index'),config['names_index_sha256'],
                             config.get('verify_shards','sha256'),config.get('release_notes'))
    if backend=='sqlite':reader=SQLiteReader(data('release_root'))
    elif backend=='git':reader=GitReader(data('export_root'),config['meta_commit'],config['snapshot'],data('scope_path'),config.get('cache_families',32))
    else:raise ValueError('Backend must be sqlite or git')
    if reader.snapshot!=config['snapshot']:
        reader.close();raise ValueError('Configured snapshot mismatch')
    reader.search_index_sha256=config.get('search_index_sha256')
    if 'search_index' in config:
        from .search_index import Projection
        try:projection=Projection(data('search_index'),config['search_index_sha256'],reader.snapshot)
        except BaseException:
            reader.close();raise
        reader.search_candidates=projection.candidates
        close=reader.close
        def close_all():
            try:projection.close()
            finally:close()
        reader.close=close_all
    if 'evidence_metadata' in config:
        from .evidence_metadata import Projection as EvidenceProjection
        try: evidence = EvidenceProjection(data('evidence_metadata'),config['evidence_metadata_sha256'],reader.snapshot)
        except BaseException:
            reader.close();raise
        reader._evidence_metadata = evidence
        reader.evidence_metadata_available = True
        reader.evidence_metadata_sha256 = config['evidence_metadata_sha256']
        previous_close = reader.close
        def close_evidence():
            try:evidence.close()
            finally:previous_close()
        reader.close = close_evidence
    return reader
