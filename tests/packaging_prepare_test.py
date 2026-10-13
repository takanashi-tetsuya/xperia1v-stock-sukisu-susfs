#!/usr/bin/env python3
"""Offline source-preparation tests. Run with python3 -m unittest discover -s tests -p packaging_prepare_test.py."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from pipeline_common import sha, snapshot_repo, source_patch, verify_sources
from prepare_sources import publish_sources


def git(repository: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(repository), *args], check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return result.stdout.strip()


class PrepareSourcesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="packaging-prepare-test-")
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / "project"
        self.root.mkdir()
        (self.root / "patches").mkdir()
        self.mirrors = self.base / "mirrors"
        self.mirrors.mkdir()
        repositories = {}
        for name in ("common", "SukiSU-Ultra", "AnyKernel3"):
            repository = self.mirrors / name
            repository.mkdir()
            git(repository, "init", "-q")
            git(repository, "config", "user.email", "fixture@example.invalid")
            git(repository, "config", "user.name", "Fixture")
            (repository / "tracked.txt").write_text(f"base {name}\n")
            if name == "common":
                (repository / "drivers").mkdir()
                (repository / "drivers" / "Makefile").write_text("# fixture\n")
            if name == "SukiSU-Ultra":
                (repository / "kernel").mkdir()
                (repository / "kernel" / "Makefile").write_text("# fixture\n")
            git(repository, "add", ".")
            git(repository, "commit", "-qm", "base")
            paired_commit = git(repository, "rev-parse", "HEAD")
            if name == "SukiSU-Ultra":
                (repository / "tracked.txt").write_text("second SukiSU commit\n")
                git(repository, "add", "tracked.txt")
                git(repository, "commit", "-qm", "second")
            (repository / "tracked.txt").write_text(f"patched {name}\n")
            if name == "common":
                (repository / "drivers" / "kernelsu").symlink_to("../../SukiSU-Ultra/kernel")
                added_script = repository / "extra.sh"
                added_script.write_text("#!/bin/sh\necho fixture\n")
                added_script.chmod(0o755)
                added_source = repository / "extra.c"
                added_source.write_text("/* non-executable source fixture */\n")
                added_source.chmod(0o664)
            patch_relative = f"patches/final_{name}.patch"
            patch = self.root / patch_relative
            patch.write_bytes(source_patch(repository))
            snapshot = snapshot_repo(repository)
            repositories[name] = {
                "url": f"https://example.invalid/{name}.git",
                "commit": snapshot["commit"],
                "patch_file": patch_relative,
                "patch_sha256": sha(patch),
                "untracked": snapshot["untracked"],
            }
            if name == "SukiSU-Ultra":
                repositories[name]["paired_ref"] = "refs/heads/release-main-40900"
                repositories[name]["paired_commit"] = paired_commit
            for raw_path, descriptor in snapshot["untracked"].items():
                source = repository / raw_path
                target = self.root / "added" / name / raw_path
                target.parent.mkdir(parents=True, exist_ok=True)
                if descriptor["kind"] == "symlink":
                    target.symlink_to(os.readlink(source))
                else:
                    shutil.copy2(source, target)
        self.lock = {
            "schema_version": 1,
            "frozen": True,
            "repositories": repositories,
            "build": {"kernel_release": "6.1.1-fixture", "ksu_version": "40900", "ksu_full_version": "fixture", "ksu_uapi": "1"},
            "stock": {"sha256": "0" * 64, "size": 0},
            "toolchain": {"clang_version": "fixture", "clang_revision": "fixture", "pahole_version": "fixture"},
        }
        self.lock_path = self.root / "source-lock.json"
        self.write_lock()

    def write_lock(self) -> None:
        self.lock_path.write_text(json.dumps(self.lock, indent=2) + "\n")

    def prepare(self, mirror: Path | None = None, *extra: str, use_environment: bool = False) -> subprocess.CompletedProcess:
        environment = dict(os.environ)
        environment.pop("LOCAL_SOURCE_ROOT", None)
        command = ["bash", str(PROJECT / "prepare-sources.sh"), "--root", str(self.root)]
        source = mirror if mirror is not None else self.mirrors
        if use_environment:
            environment["LOCAL_SOURCE_ROOT"] = str(source)
        else:
            command.extend(["--local-source-root", str(source)])
        command.extend(extra)
        return subprocess.run(command, env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def assert_clean_failure(self, result: subprocess.CompletedProcess) -> None:
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertFalse(os.path.lexists(self.root / "sources"))
        self.assertEqual(list(self.root.glob(".prepare-sources-*")), [])

    def test_prepare_and_rerun_preserve_exact_sources(self) -> None:
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        verify_sources(self.root, self.lock)
        sources = self.root / "sources"
        self.assertEqual(os.readlink(sources / "common/drivers/kernelsu"), "../../SukiSU-Ultra/kernel")
        self.assertEqual((sources / "common/extra.sh").stat().st_mode & 0o777, 0o755)
        entry = self.lock["repositories"]["SukiSU-Ultra"]
        self.assertEqual(git(sources / "SukiSU-Ultra", "rev-parse", entry["paired_ref"]), entry["paired_commit"])
        # Paired source identity is pinned; no full-history version counting is needed.
        self.assertEqual(git(sources / "SukiSU-Ultra", "rev-parse", "--is-shallow-repository"), "true")
        marker = sources / "common/.git/existing-work-marker"
        marker.write_text("preserve local metadata\n")
        before = {name: snapshot_repo(sources / name) for name in self.lock["repositories"]}
        rerun = self.prepare(self.base / "nonexistent-mirror")
        self.assertEqual(rerun.returncode, 0, rerun.stderr)
        self.assertIn("nothing changed", rerun.stdout)
        self.assertEqual(marker.read_text(), "preserve local metadata\n")
        self.assertEqual(before, {name: snapshot_repo(sources / name) for name in before})

    def test_failed_fetch_cleans_staging_without_formal_sources(self) -> None:
        shutil.rmtree(self.mirrors / "AnyKernel3")
        result = self.prepare()
        self.assert_clean_failure(result)
        self.assertIn("git fetch failed", result.stderr)

    def test_git_clone_restores_locked_nonexecutable_mode(self) -> None:
        # A real Git checkout with a common umask loses group-write permission.
        git(self.root, "init", "-q")
        git(self.root, "config", "user.email", "fixture@example.invalid")
        git(self.root, "config", "user.name", "Fixture")
        git(self.root, "add", ".")
        git(self.root, "commit", "-qm", "source preparation fixture")
        checkout = self.base / "checkout"
        subprocess.run(["git", "clone", "--quiet", str(self.root), str(checkout)],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       umask=0o022)
        self.root = checkout
        self.lock_path = checkout / "source-lock.json"
        asset = checkout / "added/common/extra.c"
        self.assertEqual(asset.stat().st_mode & 0o777, 0o644)
        self.assertEqual(self.lock["repositories"]["common"]["untracked"]["extra.c"]["mode"], 0o664)
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        verify_sources(checkout, self.lock)
        self.assertEqual((checkout / "sources/common/extra.c").stat().st_mode & 0o777, 0o664)
        self.assertEqual((checkout / "sources/common/extra.sh").stat().st_mode & 0o777, 0o755)
        self.assertEqual(asset.stat().st_mode & 0o777, 0o644)

    def test_changed_added_file_executable_intent_is_rejected(self) -> None:
        for name, changed_mode, original_mode in (("extra.c", 0o744, 0o664),
                                                  ("extra.sh", 0o644, 0o755)):
            with self.subTest(asset=name):
                asset = self.root / "added/common" / name
                asset.chmod(changed_mode)
                result = self.prepare()
                self.assert_clean_failure(result)
                self.assertIn("Added file executable mode mismatch", result.stderr)
                asset.chmod(original_mode)

    def test_existing_incomplete_sources_are_preserved(self) -> None:
        sources = self.root / "sources"
        sources.mkdir()
        marker = sources / "local-work.txt"
        marker.write_text("do not remove\n")
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("preserved", result.stderr)
        self.assertEqual(marker.read_text(), "do not remove\n")
        self.assertEqual(sorted(path.name for path in sources.iterdir()), ["local-work.txt"])
        self.assertEqual(list(self.root.glob(".prepare-sources-*")), [])

    def test_existing_modified_sources_are_preserved(self) -> None:
        initial = self.prepare()
        self.assertEqual(initial.returncode, 0, initial.stderr)
        modified = self.root / "sources/common/tracked.txt"
        modified.write_text("user's uncommitted change\n")
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("preserved", result.stderr)
        self.assertEqual(modified.read_text(), "user's uncommitted change\n")

    def test_tampered_patch_and_added_file_are_rejected(self) -> None:
        patch = self.root / "patches/final_common.patch"
        original = patch.read_bytes()
        patch.write_bytes(original + b"\n# tampered\n")
        result = self.prepare()
        self.assert_clean_failure(result)
        self.assertIn("Patch checksum mismatch", result.stderr)
        patch.write_bytes(original)
        (self.root / "added/common/extra.sh").write_text("tampered\n")
        result = self.prepare()
        self.assert_clean_failure(result)
        self.assertIn("Added file checksum mismatch", result.stderr)

    def test_inapplicable_patch_cleans_staging(self) -> None:
        patch = self.root / "patches/final_common.patch"
        patch.write_text("not a patch\n")
        self.lock["repositories"]["common"]["patch_sha256"] = sha(patch)
        self.write_lock()
        result = self.prepare()
        self.assert_clean_failure(result)
        self.assertIn("git apply failed", result.stderr)

    def test_frozen_lock_is_required(self) -> None:
        self.lock["frozen"] = False
        self.write_lock()
        result = self.prepare()
        self.assert_clean_failure(result)
        self.assertIn("not frozen", result.stderr)

    def test_final_source_verification_failure_cleans_staging(self) -> None:
        link = self.root / "added/common/drivers/kernelsu"
        link.unlink()
        link.symlink_to("missing-target")
        self.lock["repositories"]["common"]["untracked"]["drivers/kernelsu"]["target"] = "missing-target"
        self.write_lock()
        result = self.prepare()
        self.assert_clean_failure(result)
        self.assertIn("drivers/kernelsu must resolve", result.stderr)

    def test_explicit_lock_and_environment_mirror(self) -> None:
        alternate = self.root / "alternate-lock.json"
        self.lock_path.rename(alternate)
        result = self.prepare(None, "--lock", str(alternate), use_environment=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        verify_sources(self.root, self.lock)

    def test_added_path_traversal_is_rejected(self) -> None:
        entry = self.lock["repositories"]["common"]
        entry["untracked"]["../escaped.txt"] = copy.deepcopy(entry["untracked"]["extra.sh"])
        self.write_lock()
        result = self.prepare()
        self.assert_clean_failure(result)
        self.assertIn("Unsafe", result.stderr)

    def test_publish_does_not_replace_existing_empty_directory(self) -> None:
        staging = self.base / "staging"
        staging.mkdir()
        (staging / "fixture.txt").write_text("staged\n")
        destination = self.root / "sources"
        destination.mkdir()
        inode = destination.stat().st_ino
        with self.assertRaisesRegex(RuntimeError, "existing work was preserved"):
            publish_sources(staging, destination)
        self.assertEqual(destination.stat().st_ino, inode)
        self.assertEqual(list(destination.iterdir()), [])
        self.assertTrue((staging / "fixture.txt").is_file())


if __name__ == "__main__":
    unittest.main()
