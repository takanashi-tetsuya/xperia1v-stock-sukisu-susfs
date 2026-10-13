#!/usr/bin/env python3
"""Reconstruct frozen source trees without replacing existing local work."""

from __future__ import annotations

import argparse
import ctypes
import errno
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile

from pipeline_common import load_lock, require, sha, verify_sources


def relative_path(value: str, label: str) -> Path:
    require(isinstance(value, str) and bool(value), f"Missing {label}")
    path = PurePosixPath(value)
    require(
        not path.is_absolute()
        and bool(path.parts)
        and str(path) == value
        and all(part not in (".", "..", ".git") for part in path.parts),
        f"Unsafe {label}: {value!r}",
    )
    return Path(*path.parts)


def regular_parent_chain(base: Path, relative: Path) -> None:
    """Do not follow directory symlinks while reading or restoring assets."""
    cursor = base
    for part in relative.parts[:-1]:
        cursor /= part
        require(not cursor.is_symlink(), f"Directory symlink is not allowed: {cursor}")
        require(cursor.is_dir(), f"Required directory is missing: {cursor}")


def check_assets(root: Path, lock: dict) -> None:
    require(lock.get("frozen") is True, "Source lock is not frozen; freeze it before preparing sources")
    for name, entry in lock["repositories"].items():
        require(bool(re.fullmatch(r"[A-Za-z0-9_-]+", name)), f"Unsafe repository name: {name!r}")
        require(bool(re.fullmatch(r"[0-9a-fA-F]{40}", entry["commit"])), f"Invalid commit for {name}")
        patch_relative = relative_path(entry["patch_file"], f"patch path for {name}")
        regular_parent_chain(root, patch_relative)
        patch = root / patch_relative
        require(patch.is_file() and not patch.is_symlink(), f"Patch is missing or not a regular file: {patch}")
        require(sha(patch) == entry["patch_sha256"], f"Patch checksum mismatch: {patch}")
        paired_ref = entry.get("paired_ref")
        paired_commit = entry.get("paired_commit")
        require(bool(paired_ref) == bool(paired_commit), f"Incomplete paired ref in {name}")
        if paired_ref:
            require(paired_ref.startswith("refs/heads/"), f"Paired ref must be a branch ref in {name}")
            require(bool(re.fullmatch(r"[0-9a-fA-F]{40}", paired_commit)), f"Invalid paired commit for {name}")
            git("check-ref-format", paired_ref)
        for raw_path, descriptor in entry["untracked"].items():
            relative = Path("added") / name / relative_path(raw_path, f"added path for {name}")
            regular_parent_chain(root, relative)
            asset = root / relative
            kind = descriptor["kind"]
            if kind == "symlink":
                require(asset.is_symlink(), f"Added symlink is missing: {asset}")
                require(os.readlink(asset) == descriptor["target"], f"Added symlink target mismatch: {asset}")
            elif kind == "file":
                require(asset.is_file() and not asset.is_symlink(), f"Added file is missing: {asset}")
                require(sha(asset) == descriptor["sha256"], f"Added file checksum mismatch: {asset}")
                if "mode" in descriptor:
                    # Git preserves executable intent, not rw permissions such as
                    # 0664 versus 0644. Restore the exact locked mode when copying.
                    executable = bool(stat.S_IMODE(asset.stat().st_mode) & 0o111)
                    require(executable == bool(descriptor["mode"] & 0o111), f"Added file executable mode mismatch: {asset}")
            else:
                require(False, f"Unsupported added-file kind for {asset}: {kind!r}")


def git(*args: str, cwd: Path | None = None) -> str:
    environment = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    command = ["git", "-c", "core.autocrlf=false", "-c", "core.filemode=true", "-c", "core.symlinks=true", *map(str, args)]
    result = subprocess.run(command, cwd=cwd, env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"
        raise RuntimeError(f"git {args[0]} failed{f' in {cwd.name}' if cwd else ''}: {detail}")
    return result.stdout.strip()


def restore_added(root: Path, destination: Path, name: str, entries: dict) -> None:
    for raw_path, descriptor in sorted(entries.items()):
        relative = relative_path(raw_path, f"added path for {name}")
        source = root / "added" / name / relative
        target = destination / relative
        cursor = destination
        for component in relative.parts[:-1]:
            cursor /= component
            require(not cursor.is_symlink(), f"Refusing to copy through a symlink: {cursor}")
            cursor.mkdir(exist_ok=True)
        require(not os.path.lexists(target), f"Added file would replace an existing path: {target}")
        if descriptor["kind"] == "symlink":
            target.symlink_to(descriptor["target"])
        else:
            shutil.copy2(source, target, follow_symlinks=False)
            if "mode" in descriptor:
                target.chmod(descriptor["mode"])


def fetch_repository(root: Path, staging_sources: Path, name: str, entry: dict, local_source_root: Path | None) -> None:
    destination = staging_sources / name
    destination.mkdir()
    git("init", "-q", str(destination))
    git("remote", "add", "origin", entry["url"], cwd=destination)
    source = str(local_source_root / name) if local_source_root else "origin"
    revisions = [entry["commit"]]
    paired_commit = entry.get("paired_commit")
    if paired_commit and paired_commit not in revisions:
        revisions.append(paired_commit)
    # Numeric 40900 is pinned: the paired commit is provenance, not a history count.
    depth = ["--depth=1"]
    git("fetch", "--no-tags", *depth, source, *revisions, cwd=destination)
    git("checkout", "--detach", entry["commit"], cwd=destination)
    if paired_commit:
        git("update-ref", entry["paired_ref"], paired_commit, cwd=destination)
    patch = root / relative_path(entry["patch_file"], f"patch path for {name}")
    if patch.stat().st_size:
        git("apply", "--check", "--binary", str(patch), cwd=destination)
        git("apply", "--binary", str(patch), cwd=destination)
    restore_added(root, destination, name, entry["untracked"])


def publish_sources(staging_sources: Path, destination: Path) -> None:
    """Atomically publish on Linux, refusing even an empty existing directory."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    require(rename is not None, "Atomic source publication requires Linux renameat2 support")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(staging_sources), -100, os.fsencode(destination), 1):
        code = ctypes.get_errno()
        if code in (errno.EEXIST, errno.ENOTEMPTY):
            raise RuntimeError(f"Sources appeared during preparation; existing work was preserved: {destination}")
        raise OSError(code, os.strerror(code), str(destination))


def prepare(root: Path, lock_path: Path, local_source_root: Path | None) -> bool:
    lock = load_lock(lock_path)
    require(lock.get("frozen") is True, "Source lock is not frozen; freeze it before preparing sources")
    sources = root / "sources"
    if os.path.lexists(sources):
        require(sources.is_dir() and not sources.is_symlink(), f"Existing sources path is not a regular directory; preserved: {sources}")
        try:
            verify_sources(root, lock)
        except Exception as error:
            raise RuntimeError(f"Existing sources do not match the frozen lock; all existing work was preserved: {error}") from error
        return False
    check_assets(root, lock)
    require(root.is_dir(), f"Project root does not exist: {root}")
    with tempfile.TemporaryDirectory(prefix=".prepare-sources-", dir=root) as temporary:
        staging_root = Path(temporary)
        staging_sources = staging_root / "sources"
        staging_sources.mkdir()
        for name, entry in lock["repositories"].items():
            print(f"Preparing {name} at {entry['commit']}...", flush=True)
            fetch_repository(root, staging_sources, name, entry, local_source_root)
        verify_sources(staging_root, lock)
        publish_sources(staging_sources, sources)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent, help="Project root (default: this script's directory)")
    parser.add_argument("--lock", type=Path, help="Source lock JSON (default: ROOT/source-lock.json)")
    parser.add_argument("--local-source-root", type=Path, default=os.environ.get("LOCAL_SOURCE_ROOT") or None, help="Local repositories named as in the lock (also LOCAL_SOURCE_ROOT)")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    lock_path = args.lock.resolve() if args.lock else root / "source-lock.json"
    local_source_root = Path(args.local_source_root).resolve() if args.local_source_root else None

    def interrupted(_signum: int, _frame: object) -> None:
        raise InterruptedError("Source preparation was interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    try:
        created = prepare(root, lock_path, local_source_root)
    except KeyboardInterrupt:
        print("prepare-sources: interrupted; existing sources were preserved", file=sys.stderr)
        return 130
    except Exception as error:
        print(f"prepare-sources: {error}", file=sys.stderr)
        return 1
    print("Frozen source trees prepared and verified." if created else "Existing source trees already match the frozen lock; nothing changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
