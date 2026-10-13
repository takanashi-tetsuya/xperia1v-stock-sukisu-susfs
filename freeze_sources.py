#!/usr/bin/env python3
"""Explicit maintainer action: record reviewed sources; never invoked by verification."""
import argparse
import os
from pathlib import Path
import shutil
import sys
from pipeline_common import atomic_json, cli_error, digest, git, load_lock, require, safe_relative, snapshot_repo, source_patch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--lock', type=Path)
    parser.add_argument('--confirm-reviewed-sources', action='store_true', required=True,
                        help='Confirm that you reviewed these changes and intend to establish a new baseline')
    args = parser.parse_args()
    root = args.root.resolve()
    lock_path = args.lock or root / 'source-lock.json'
    lock = load_lock(lock_path)
    snapshots = {}
    for name, spec in lock['repositories'].items():
        repository = root / 'sources' / name
        snap = snapshot_repo(repository)
        require(snap['commit'] == spec['commit'], f'Cannot freeze unexpected HEAD: {name}')
        if spec.get('paired_ref'):
            require(git(repository, 'rev-parse', spec['paired_ref']).strip() == spec['paired_commit'],
                    f'Cannot freeze unexpected paired ref: {name}')
        snapshots[name] = snap
    for name, snapshot in snapshots.items():
        spec = lock['repositories'][name]
        patch_path = root / safe_relative(spec['patch_file'])
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        patch = source_patch(root / 'sources' / name)
        require(digest(patch) == snapshot['patch_sha256'], f'Sources changed while freezing: {name}')
        patch_path.write_bytes(patch)
        for relative, details in snapshot['untracked'].items():
            source = root / 'sources' / name / safe_relative(relative)
            destination = root / 'added' / name / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.is_symlink() or destination.exists():
                require(not destination.is_dir(), f'Refusing to replace directory: {destination}')
                destination.unlink()
            if details['kind'] == 'symlink':
                os.symlink(details['target'], destination)
            else:
                shutil.copy2(source, destination)
        spec.update(snapshot)
    for name, snapshot in snapshots.items():
        require(snapshot_repo(root / 'sources' / name) == snapshot, f'Sources changed while freezing: {name}')
    lock['frozen'] = True
    atomic_json(lock_path, lock)
    print(f'Frozen reviewed source identity: {lock_path}')


if __name__ == '__main__':
    sys.exit(cli_error(main))
