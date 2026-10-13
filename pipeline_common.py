"""Fail-closed shared checks for the offline kernel pipeline (also under python -O)."""
from pathlib import Path
import hashlib
import json
import os
import re
import subprocess
import tempfile


class CheckError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise CheckError(str(message))


def run(*args, text=True):
    try:
        return subprocess.check_output([str(a) for a in args], text=text, stderr=subprocess.PIPE)
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, 'stderr', '') or str(exc)
        if isinstance(detail, bytes):
            detail = detail.decode(errors='replace')
        raise CheckError(f"Command failed ({args[0]}): {detail.strip()}") from exc


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f'.{path.name}.', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_lock(path):
    with Path(path).open() as stream:
        lock = json.load(stream)
    require(lock.get('schema_version') == 1, 'Unsupported source-lock schema (expected 1)')
    require(isinstance(lock.get('frozen'), bool), 'source-lock frozen must be boolean')
    require(set(lock.get('repositories', {})) == {'common', 'SukiSU-Ultra', 'AnyKernel3'},
            'source-lock must define common, SukiSU-Ultra and AnyKernel3')
    for name, spec in lock['repositories'].items():
        require(re.fullmatch(r'[0-9a-f]{40}', spec.get('commit', '')), f'Invalid commit lock: {name}')
        if lock['frozen']:
            require(re.fullmatch(r'[0-9a-f]{64}', spec.get('patch_sha256') or ''), f'Missing patch lock: {name}')
            require(isinstance(spec.get('untracked'), dict), f'Missing added file lock: {name}')
    return lock


def safe_relative(value):
    path = Path(value)
    require(not path.is_absolute() and '..' not in path.parts and value not in ('', '.'),
            f'Unsafe relative path: {value}')
    return path


def git(path, *args, text=True):
    return run('git', '-c', 'core.fsmonitor=false', '-c', 'core.filemode=true',
               '-c', 'core.autocrlf=false', '-C', path, *args, text=text)


def source_patch(path):
    return git(path, 'diff', '--no-ext-diff', '--no-textconv', '--binary', '--full-index',
               '--src-prefix=a/', '--dst-prefix=b/', 'HEAD', '--', text=False)


def snapshot_repo(path):
    path = Path(path)
    require(path.is_dir() and not path.is_symlink(), f'Source repository missing or symlinked: {path}')
    # Index flags can hide local edits from git diff; they are not permitted.
    entries = git(path, 'ls-files', '-v', '-z').split('\0')
    require(all(not entry or (entry[0].isupper() and entry[0] != 'S') for entry in entries),
            f'Unsupported assume-unchanged/skip-worktree index entries: {path}')
    untracked = {}
    # Include ignored files: an ignored generated header can still change a build.
    for name in filter(None, git(path, 'ls-files', '--others', '-z').split('\0')):
        relative = safe_relative(name)
        candidate = path / relative
        if candidate.is_symlink():
            untracked[name] = {'kind': 'symlink', 'target': os.readlink(candidate)}
        else:
            require(candidate.is_file(), f'Unsupported added source: {candidate}')
            untracked[name] = {'kind': 'file', 'sha256': sha(candidate),
                               'mode': candidate.stat().st_mode & 0o777}
    return {'commit': git(path, 'rev-parse', 'HEAD').strip(),
            'patch_sha256': digest(source_patch(path)), 'untracked': untracked}


def verify_sources(root, lock):
    require(lock['frozen'], 'Source lock is not frozen; review sources and run freeze_sources.py explicitly')
    snapshots = {}
    for name, expected in lock['repositories'].items():
        path = Path(root) / 'sources' / name
        current = snapshot_repo(path)
        for field in ('commit', 'patch_sha256', 'untracked'):
            require(current[field] == expected[field], f'Source lock mismatch: {name} {field}')
        if expected.get('paired_ref'):
            require(git(path, 'rev-parse', expected['paired_ref']).strip() == expected['paired_commit'],
                    f'Paired release ref mismatch: {name}')
        snapshots[name] = current
    link = Path(root) / 'sources/common/drivers/kernelsu'
    require(link.is_symlink() and link.resolve() == (Path(root) / 'sources/SukiSU-Ultra/kernel').resolve(),
            'drivers/kernelsu must resolve to the locked SukiSU kernel directory')
    return snapshots


def verify_image(image, minimum_size=10000000):
    require(len(image) > minimum_size and image[56:60] == b'ARMd',
            'Invalid Image: expected an uncompressed ARM64 kernel larger than 10 MB')


def cli_error(function):
    """Give CLI diagnostics, keeping modules importable without environment variables."""
    try:
        return function()
    except (CheckError, OSError, ValueError, KeyError, TypeError) as exc:
        import sys
        print(f'error: {exc}', file=sys.stderr)
        return 1
