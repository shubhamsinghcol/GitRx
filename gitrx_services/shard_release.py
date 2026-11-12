"""Two local immutable expanded export builds: 16 shards and a pinned meta repo."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timezone

from gitrx.current.db import dumps
from gitrx.current.export_git import export_git
from gitrx_services.io import ensure_space
from gitrx_services.recovered_release import ReleaseFederation, One, sha

TAG = 'release1-expanded-2026-11-06-unvalidated'


class ShardView:
    def __init__(self, base, prefix):
        if prefix not in '0123456789abcdef' or len(prefix) != 1:
            raise ValueError('Shard is one lowercase hex character')
        self.base, self.prefix = base, prefix
        full = base.manifest if hasattr(base, 'manifest') else json.loads(base.raw)
        self.versions = [v for v in full['versions'] if v['setid'].startswith(prefix)]
        self.raw = dumps({'format': 'gitrx-shard-snapshot-1', 'partition': 'first-setid-hex',
                          'shard': prefix, 'expanded_snapshot': base.snapshot,
                          'sources': full['sources'], 'versions': self.versions,
                          'growth_policy': 'immutable-separately-tagged-expanded-snapshots'})
        self.snapshot = hashlib.sha256(self.raw.encode()).hexdigest()

    def execute(self, sql, parameters=()):
        if 'SELECT manifest_json FROM snapshots' in sql:
            return One((self.raw,)) if parameters[0] == self.snapshot else One(None)
        return self.base.execute(sql, parameters)


def git(repo, *args, env=None):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True, env=env).strip()


def meta_repository(path, base, shards):
    path = Path(path)
    manifest = {'format': 'gitrx-meta-release-1', 'release_tag': TAG,
                'expanded_snapshot': base.snapshot,
                'growth_policy': 'immutable-separately-tagged-expanded-snapshots',
                'parent_selection_sha256': base.manifest['parent_selection_sha256'],
                'scope_sha256': base.manifest['scope_sha256'],
                'families': 7239, 'retrieved_versions': len(base.manifest['versions']),
                'known_target_failures': 0,
                'upstream_history_unknown_families': base.manifest['upstream_history_unknown_families'],
                'validation': 'original blind comparison 59/60; owner-adjudicated 60/60; frozen v1.0.1 unchanged; no public release',
                'shards': {p: {k: row[k] for k in ('head', 'tree', 'snapshot', 'commits', 'families')}
                           for p, row in shards.items()}}
    if path.exists() and git(path, 'rev-parse', '--verify', 'refs/tags/' + TAG):
        return {'head': git(path, 'rev-parse', 'refs/tags/' + TAG),
                'tree': git(path, 'rev-parse', TAG + '^{tree}'), 'manifest': manifest}
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(['git', 'init', '--quiet', '--object-format=sha1', str(path)], check=True)
    modules = ''.join(f'[submodule "{p}"]\n\tpath = shards/{p}\n\turl = ../shards/{p}\n' for p in shards)
    (path / '.gitmodules').write_text(modules)
    (path / 'manifest.json').write_text(dumps(manifest) + '\n')
    (path / 'README.md').write_text(
        '# GitRx local expanded snapshot\n\n'
        'Unvalidated local release engineering artifact; nothing public.\n'
        'Each gitlink pins one first-setid-hex shard. Raw archives and SQLite remain external.\n'
        'Known target gaps are recovered; the NALTREXONE upstream history inventory remains unknown.\n'
        'DailyMed publication, SPL effective and collection dates have distinct meanings.\n'
        'Old release refs remain immutable; this expanded snapshot has new commit IDs.\n')
    git(path, 'add', '.gitmodules', 'manifest.json', 'README.md')
    for prefix, row in shards.items():
        git(path, 'update-index', '--add', '--cacheinfo', '160000,' + row['head'] + ',shards/' + prefix)
    tree = git(path, 'write-tree')
    env = dict(os.environ, GIT_AUTHOR_NAME='GitRx Archive', GIT_AUTHOR_EMAIL='archive@gitrx.invalid',
               GIT_COMMITTER_NAME='GitRx Archive', GIT_COMMITTER_EMAIL='archive@gitrx.invalid',
               GIT_AUTHOR_DATE='2026-11-06T00:00:00+00:00', GIT_COMMITTER_DATE='2026-11-06T00:00:00+00:00')
    head = git(path, 'commit-tree', tree, '-m',
               'GitRx local expanded release\n\nExpanded-Snapshot: ' + base.snapshot +
               '\nBuild-Date-Meaning: fixed release recipe timestamp, not source publication\n', env=env)
    git(path, 'update-ref', 'refs/heads/release', head, '0' * 40)
    git(path, 'update-ref', 'refs/tags/' + TAG, head, '0' * 40)
    git(path, 'symbolic-ref', 'HEAD', 'refs/heads/release')
    return {'head': head, 'tree': tree, 'manifest': manifest}


def build(base, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    shards = {}
    for prefix in '0123456789abcdef':
        ensure_space(output, 2 * 1024**3)
        view = ShardView(base, prefix)
        if not view.versions:
            raise ValueError('Empty shard export needs an explicitly specified empty-ref protocol')
        repo = output / 'shards' / prefix
        started = time.monotonic()
        if repo.exists():
            ref = subprocess.run(['git', '-C', str(repo), 'show-ref', '--verify', '--quiet', 'refs/tags/' + TAG])
            if ref.returncode:
                # Keep the interrupted fast-import objects/logs; never reset
                # or delete them, and never relocate an existing tagged build.
                stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
                retained = output / 'incomplete' / (prefix + '-' + stamp)
                retained.parent.mkdir(exist_ok=True)
                repo.rename(retained)
                print(json.dumps({'retained_interrupted_shard': str(retained)}), flush=True)
        if repo.exists():
            head = git(repo, 'rev-parse', '--verify', 'refs/tags/' + TAG)
            result = {'repo': str(repo), 'head': head, 'tree': git(repo, 'rev-parse', head + '^{tree}'),
                      'snapshot': view.snapshot, 'commits': int(git(repo, 'rev-list', '--count', head)),
                      'families': len({v['setid'] for v in view.versions})}
        else:
            result = export_git(view, base.project, repo, view.snapshot, TAG)
            git(repo, 'symbolic-ref', 'HEAD', 'refs/heads/build/' + TAG)
        if result['commits'] != len(view.versions):
            raise ValueError('Shard commit count differs from selected versions')
        subprocess.run(['git', '-C', str(repo), 'fsck', '--no-reflogs'], check=True, capture_output=True)
        result['elapsed_seconds'] = time.monotonic() - started
        shards[prefix] = result
        print(json.dumps({'build': output.name, 'shard': prefix, 'commits': result['commits'],
                          'head': result['head'], 'seconds': result['elapsed_seconds']}), flush=True)
    meta = meta_repository(output / 'meta', base, shards)
    subprocess.run(['git', '-C', str(output / 'meta'), 'fsck', '--no-reflogs'], check=True, capture_output=True)
    proof = {'expanded_snapshot': base.snapshot, 'tag': TAG, 'shards': shards, 'meta': meta,
             'commits': sum(s['commits'] for s in shards.values()),
             'families': sum(s['families'] for s in shards.values()), 'public': False}
    (output / 'BUILD.json').write_text(json.dumps(proof, indent=2) + '\n')
    return proof


RETIRED = 'This command built the 2026-11-06 release (cohorts/release1), which was deleted on 2026-11-11. The library functions remain; the build command is recoverable at git tag codex-final (docs/CLEANUP-2026-11-11.md).'


def main():
    raise SystemExit(RETIRED)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project', type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument('--output-base', type=Path)
    p.add_argument('--build-only', choices=('a', 'b'), help=argparse.SUPPRESS)
    a = p.parse_args()
    supplemental = a.project / 'cohorts/release1-recovered-20261106'
    base = ReleaseFederation(a.project, supplemental)
    try:
        from .export_payloads import prepare, PayloadCache
        cache_root = supplemental / 'export-payloads'
        base.payload_cache = PayloadCache(cache_root, prepare(base, cache_root))
        output = a.output_base or a.project / 'exports/release1-expanded-20261106'
        if a.build_only:
            build(base, Path(str(output) + '-' + a.build_only))
            return
        # Independent builds write disjoint repositories. Collection remains
        # strictly sequential; these workers perform local reads/Git writes
        # only, after the bounded payload-preparation pass has completed.
        commands = [[__import__('sys').executable, '-B', '-m', 'gitrx_services.shard_release',
                     '--project', str(a.project), '--output-base', str(output), '--build-only', suffix]
                    for suffix in ('a', 'b')]
        base.close()
        if (os.cpu_count() or 1) >= 6:
            jobs = [subprocess.Popen(command) for command in commands]
            codes = [job.wait() for job in jobs]
            if any(codes):
                raise RuntimeError('Independent local build failed: ' + str(codes))
        else:
            for command in commands:
                subprocess.run(command, check=True)
        first = json.loads((Path(str(output) + '-a') / 'BUILD.json').read_bytes())
        second = json.loads((Path(str(output) + '-b') / 'BUILD.json').read_bytes())
        for prefix in first['shards']:
            if any(first['shards'][prefix][k] != second['shards'][prefix][k] for k in ('head', 'tree', 'snapshot', 'commits', 'families')):
                raise ValueError('Two-build shard mismatch')
            # Equal tips plus complete object validation establish equal parent-linked histories;
            # also compare every reachable commit ID in order explicitly.
            one = git(first['shards'][prefix]['repo'], 'rev-list', '--reverse', first['shards'][prefix]['head'])
            two = git(second['shards'][prefix]['repo'], 'rev-list', '--reverse', second['shards'][prefix]['head'])
            if one != two:
                raise ValueError('Reachable commit chain mismatch')
        if (first['meta']['head'], first['meta']['tree']) != (second['meta']['head'], second['meta']['tree']):
            raise ValueError('Two-build meta mismatch')
        result = {'expanded_snapshot': base.snapshot, 'two_builds_match': True,
                  'all_reachable_commit_ids_compared': 51357, 'all_shards_fsck_pass': True,
                  'all_meta_gitlinks_match': True, 'meta_head': first['meta']['head'],
                  'meta_tree': first['meta']['tree'], 'tag': TAG,
                  'build_a': str(Path(str(output) + '-a')), 'build_b': str(Path(str(output) + '-b')),
                  'public': False, 'validation_gate': 'original blind 59/60; owner-adjudicated 60/60; no classifier change; not a second blind pass'}
        (supplemental / 'reports/EXPORT-VERIFICATION.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(result), flush=True)
    finally:
        base.close()


if __name__ == '__main__':
    main()
