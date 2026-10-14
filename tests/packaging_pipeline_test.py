#!/usr/bin/env python3
"""Offline regression tests: no device, network, or kernel compilation."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import build_pipeline
import package
import pipeline_common as common
import verify_build

AVB_INFO = """Algorithm:                SHA256_RSA4096
Rollback Index:           1780272000
Flags:                    0
Rollback Index Location:  0
Descriptors:
    Hash descriptor:
      Image Size:            10006528 bytes
      Hash Algorithm:        sha256
      Partition Name:        boot
      Salt:                  aabbccdd
      Digest:                aabbccdd
      Flags:                 0
    Prop: com.android.build.boot.os_version -> '13'
    Prop: com.android.build.boot.fingerprint -> 'Sony/test:13/test:user/release-keys'
    Prop: com.android.build.boot.security_patch -> '2026-06-01'
    Prop: com.android.build.boot.security_patch -> '2026-06-01'
"""


def git(path, *args):
    return subprocess.check_output(['git', '-C', str(path), *args], stderr=subprocess.PIPE, text=True).strip()


def executable(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)
    return path


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='packaging-test-')
        self.root = Path(self.temporary.name)
        self.lock = copy.deepcopy(common.load_lock(ROOT / 'source-lock.json'))
        for name, spec in self.lock['repositories'].items():
            repo = self.root / 'sources' / name
            repo.mkdir(parents=True)
            git(repo, 'init', '-q')
            git(repo, 'config', 'user.name', 'Offline Test')
            git(repo, 'config', 'user.email', 'offline@example.invalid')
            (repo / 'tracked').write_text('base\n')
            if name == 'SukiSU-Ultra':
                (repo / 'kernel').mkdir()
                (repo / 'kernel/Kconfig').write_text('test\n')
            if name == 'common':
                (repo / 'drivers').mkdir()
                (repo / 'drivers/Makefile').write_text('test\n')
            if name == 'AnyKernel3':
                (repo / 'anykernel.sh').write_text('NO_MAGISK_CHECK=1\nBLOCK=boot;\n')
                (repo / 'LICENSE').write_text('fixture license\n')
                executable(repo / 'META-INF/com/google/android/update-binary', '#!/bin/sh\nexit 0\n')
                executable(repo / 'tools/tool', '#!/bin/sh\nexit 0\n')
            git(repo, 'add', '.')
            git(repo, 'commit', '-qm', 'fixture')
            (repo / 'tracked').write_text('reviewed patch\n')
            spec['commit'] = git(repo, 'rev-parse', 'HEAD')
            if spec.get('paired_ref'):
                spec['paired_commit'] = spec['commit']
                git(repo, 'update-ref', spec['paired_ref'], spec['paired_commit'])
        (self.root / 'sources/common/drivers/kernelsu').symlink_to('../../SukiSU-Ultra/kernel')
        (self.root / 'sources/common/added.h').write_text('reviewed added header\n')
        for name, spec in self.lock['repositories'].items():
            spec.update(common.snapshot_repo(self.root / 'sources' / name))
        self.lock['frozen'] = True
        self.lock_path = self.root / 'source-lock.json'
        common.atomic_json(self.lock_path, self.lock)
        self.image = bytearray(10000064)
        self.image[56:60] = b'ARMd'
        release = ('Linux version ' + self.lock['build']['kernel_release'] + ' ').encode()
        self.image[128:128 + len(release)] = release
        version = self.lock['build']['ksu_full_version'].encode()
        self.image[512:512 + len(version)] = version
        self.image = bytes(self.image)
        for relative in build_pipeline.ARTIFACTS:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(self.image if relative.endswith('/Image') else b'fixture\n')
        self.stock = self.root / 'stock.img'
        header = bytearray(4096)
        header[:8] = b'ANDROID!'
        struct.pack_into('<I', header, 8, len(self.image))
        struct.pack_into('<I', header, 20, 1584)
        struct.pack_into('<I', header, 40, 4)
        raw = bytes(header) + self.image
        raw += bytes(16 * 1024 * 1024 - len(raw))
        self.stock.write_bytes(raw)
        self.lock['stock']['sha256'] = common.digest(raw)
        self.lock['stock']['size'] = len(raw)
        common.atomic_json(self.lock_path, self.lock)
        self.attest()
        self.avbtool = self.root / 'avbtool'
        self.avbtool.write_text('test fixture placeholder\n')

    def tearDown(self):
        self.temporary.cleanup()

    def attest(self):
        completion = {'status': 'BUILD_COMPLETED', 'source_lock_sha256': common.sha(self.lock_path),
                      'artifact_sha256': {p: common.sha(self.root / p) for p in build_pipeline.ARTIFACTS}}
        common.atomic_json(self.root / 'logs/build_completion.json', completion)
        verification = {'status': 'PASS_OFFLINE_BUILD_CHECKS', 'source_lock_sha256': common.sha(self.lock_path),
                        'build_completion_sha256': common.sha(self.root / 'logs/build_completion.json'),
                        'artifact_sha256': completion['artifact_sha256'],
                        'source_inputs': common.verify_sources(self.root, self.lock),
                        'kernel_sha256': common.sha(self.root / 'dist/Image')}
        common.atomic_json(self.root / 'logs/build_verification.json', verification)

    def cli(self, script, *args, optimized=False):
        return subprocess.run([sys.executable] + (['-O'] if optimized else []) +
                              [str(ROOT / script), '--root', str(self.root), *map(str, args)],
                              text=True, capture_output=True)

    def test_changed_tracked_source_is_rejected_under_optimization(self):
        (self.root / 'sources/common/tracked').write_text('unexpected change\n')
        for optimized in (False, True):
            result = self.cli('package.py', '--stock-boot', self.stock, optimized=optimized)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Source lock mismatch: common patch_sha256', result.stderr)
        self.assertEqual([], list((self.root / 'dist').glob('packages/*')))

    def test_changed_added_source_with_preserved_mtime_is_rejected(self):
        path = self.root / 'sources/common/added.h'
        stamp = path.stat()
        path.write_text('unapproved added header\n')
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        with self.assertRaisesRegex(common.CheckError, 'untracked'):
            common.verify_sources(self.root, self.lock)

    def test_changed_image_with_preserved_mtime_is_rejected_under_optimization(self):
        path = self.root / 'dist/Image'
        stamp = path.stat()
        with path.open('r+b') as stream:
            stream.seek(1024)
            stream.write(b'x')
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        for optimized in (False, True):
            result = self.cli('package.py', '--stock-boot', self.stock, optimized=optimized)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Build artifact changed: dist/Image', result.stderr)

    def test_invalid_stock_is_rejected_under_optimization(self):
        self.stock.write_bytes(b'invalid')
        for optimized in (False, True):
            result = self.cli('package.py', '--stock-boot', self.stock, optimized=optimized)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Unexpected stock firmware SHA256', result.stderr)
            self.assertNotIn('Traceback', result.stderr)
        self.assertEqual([], list((self.root / 'dist').glob('.package-*')))

    def test_invalid_stock_header_rejected_even_with_matching_hash(self):
        original = bytearray(self.stock.read_bytes())
        struct.pack_into('<I', original, 12, 42)
        spec = dict(self.lock['stock'], sha256=common.digest(original))
        with self.assertRaisesRegex(common.CheckError, 'ramdisk'):
            package.verify_stock(original, spec)

    def test_wrong_head_and_ignored_addition_are_rejected(self):
        repo = self.root / 'sources/common'
        git(repo, 'commit', '--allow-empty', '-qm', 'unreviewed head')
        with self.assertRaisesRegex(common.CheckError, 'commit'):
            common.verify_sources(self.root, self.lock)
        git(repo, 'reset', '--soft', 'HEAD~1')
        (repo / '.git/info/exclude').write_text('ignored.h\n')
        (repo / 'ignored.h').write_text('must not be hidden\n')
        with self.assertRaisesRegex(common.CheckError, 'untracked'):
            common.verify_sources(self.root, self.lock)

    def test_verifier_never_updates_lock_and_removes_stale_pass(self):
        previous = self.lock_path.read_bytes()
        (self.root / 'sources/common/added.h').write_text('changed\n')
        result = self.cli('verify_build.py', optimized=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Source lock mismatch', result.stderr)
        self.assertEqual(previous, self.lock_path.read_bytes())
        self.assertFalse((self.root / 'logs/build_verification.json').exists())

    def config_fixture(self):
        enabled = ['KSU', 'KSU_SUSFS', 'LTO_CLANG_FULL', 'CFI_CLANG', 'MODULES', 'MODVERSIONS',
                   'MODULE_SIG_PROTECT', 'TCP_CONG_BBR', 'IKCONFIG', 'IKCONFIG_PROC', 'DEBUG_INFO_BTF']
        enabled += ['KSU_SUSFS_' + key for key in ('SUS_PATH', 'SUS_MOUNT', 'SUS_KSTAT', 'SPOOF_UNAME',
                    'HIDE_KSU_SUSFS_SYMBOLS', 'SPOOF_CMDLINE_OR_BOOTCONFIG', 'OPEN_REDIRECT', 'SUS_MAP')]
        config = ''.join(f'CONFIG_{name}=y\n' for name in enabled)
        config += 'CONFIG_DEFAULT_TCP_CONG="cubic"\n'
        config += 'CONFIG_CC_VERSION_TEXT="Android clang version 14.0.7 (r450784e)"\n'
        for path in ('out/.config', 'dist/kernel.config'):
            (self.root / path).write_text(config)
        (self.root / 'out/include/config/kernel.release').write_text(self.lock['build']['kernel_release'] + '\n')
        (self.root / 'out/drivers/kernelsu/.ksu.o.cmd').write_text('clang -DKSU_VERSION=40900\n')
        (self.root / 'logs/build.log').write_text('using SUSFS_INLINE_HOOK\n')
        self.attest()
        return config

    def test_verifier_preserves_required_btf_and_config_checks(self):
        config = self.config_fixture()
        btf = '\n'.join(f"[1] FUNC '{name}' type_id=1 linkage=global" for name in verify_build.REQUIRED_BTF)
        def answer(*args, **kwargs):
            return config if args[0] == 'bash' else btf
        with patch.object(verify_build, 'run', side_effect=answer):
            report = verify_build.verify(self.root, self.lock_path, 'fixture-bpftool')
            self.assertEqual(15, report['btf_required_function_count'])
            btf = btf.replace("FUNC 'dentry_open' ", "FUNC 'other' ")
            with self.assertRaisesRegex(common.CheckError, 'BTF functions missing'):
                verify_build.verify(self.root, self.lock_path, 'fixture-bpftool')
        # Re-attesting a config is insufficient: the forbidden option remains rejected under -O.
        for option in ('CONFIG_KSU_FEATURE_ADBROOT',):
            with self.subTest(option=option):
                (self.root / 'out/.config').write_text(config + option + '=y\n')
                self.attest()
                result = self.cli('verify_build.py', optimized=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(option, result.stderr)

    def test_verifier_rejects_disabled_proc_config(self):
        config = self.config_fixture().replace('CONFIG_IKCONFIG_PROC=y\n', '# CONFIG_IKCONFIG_PROC is not set\n')
        for relative in ('out/.config', 'dist/kernel.config'):
            (self.root / relative).write_text(config)
        for optimized in (False, True):
            with self.subTest(optimized=optimized):
                self.attest()
                result = self.cli('verify_build.py', optimized=optimized)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Config mismatch: CONFIG_IKCONFIG_PROC=n; expected y', result.stderr)
                self.assertFalse((self.root / 'logs/build_verification.json').exists())

    def test_verifier_rejects_missing_proc_config(self):
        config = self.config_fixture().replace('CONFIG_IKCONFIG_PROC=y\n', '')
        for relative in ('out/.config', 'dist/kernel.config'):
            (self.root / relative).write_text(config)
        for optimized in (False, True):
            with self.subTest(optimized=optimized):
                self.attest()
                result = self.cli('verify_build.py', optimized=optimized)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Config mismatch: CONFIG_IKCONFIG_PROC=None; expected y', result.stderr)
                self.assertFalse((self.root / 'logs/build_verification.json').exists())

    def test_avb_parser_preserves_duplicate_props_and_rejects_flags(self):
        metadata = package.parse_avb(AVB_INFO)
        self.assertEqual(4, len(metadata['properties']))
        self.assertEqual(1780272000, metadata['rollback_index'])
        self.assertEqual('13', metadata['properties'][0][1])
        with self.assertRaisesRegex(common.CheckError, 'flags'):
            package.parse_avb(AVB_INFO.replace('Flags:                    0', 'Flags:                    2'))

    def fake_avb(self, *args, **kwargs):
        self.assertEqual(sys.executable, args[0])
        action = args[2]
        image = Path(args[args.index('--image') + 1])
        if action == 'info_image':
            return AVB_INFO if image == self.stock else AVB_INFO.replace('SHA256_RSA4096', 'NONE')
        if action == 'verify_image':
            return 'fixture verification\n'
        if action == 'add_hash_footer':
            with image.open('ab') as stream:
                stream.truncate(self.lock['stock']['size'])
            return ''
        self.fail(f'Unexpected AVB action: {action}')

    def test_avb_failure_leaves_no_published_boot_or_zip(self):
        def fail_after_raw(*args, **kwargs):
            if args[2] == 'add_hash_footer':
                self.assertTrue(Path(args[args.index('--image') + 1]).is_file())
                raise common.CheckError('injected AVB failure')
            return self.fake_avb(*args, **kwargs)
        with patch.object(package, 'run', side_effect=fail_after_raw):
            with self.assertRaisesRegex(common.CheckError, 'injected AVB failure'):
                package.package(self.root, self.lock_path, self.stock, self.avbtool)
        self.assertEqual([], list((self.root / 'dist/packages').iterdir()))
        self.assertEqual([], list((self.root / 'dist').glob('.package-*')))
        self.assertFalse((self.root / 'logs/packaging_verification.json').exists())

    def test_bundle_publication_and_repeat_are_atomic_and_idempotent(self):
        with patch.object(package, 'run', side_effect=self.fake_avb):
            report = package.package(self.root, self.lock_path, self.stock, self.avbtool)
            again = package.package(self.root, self.lock_path, self.stock, self.avbtool)
        self.assertEqual(report, again)
        destination = self.root / report['boot_path']
        self.assertTrue(destination.is_file())
        self.assertEqual(report['boot_sha256'], common.sha(destination))
        self.assertTrue((destination.parent / 'packaging_verification.json').exists())
        self.assertEqual([], list((self.root / 'dist').glob('.package-*')))

    def test_metadata_mismatch_rejected_before_publication(self):
        def alter_candidate(*args, **kwargs):
            output = self.fake_avb(*args, **kwargs)
            if args[2] == 'info_image' and Path(args[-1]) != self.stock:
                return output.replace("-> '13'", "-> '15'")
            return output
        with patch.object(package, 'run', side_effect=alter_candidate):
            with self.assertRaisesRegex(common.CheckError, 'metadata differs'):
                package.package(self.root, self.lock_path, self.stock, self.avbtool)
        self.assertEqual([], list((self.root / 'dist/packages').iterdir()))

    def test_real_avbtool_roundtrip_when_available(self):
        avbtool = Path(os.environ.get('AVBTOOL', ROOT / 'host-tools/bin/avbtool')).resolve()
        if not avbtool.is_file():
            self.skipTest('Set AVBTOOL to run the real AVB integration test')
        raw = self.stock.read_bytes()[:4096 + len(self.image)]
        raw += bytes((-len(raw)) % 4096)
        self.stock.write_bytes(raw)
        common.run(sys.executable, avbtool, 'add_hash_footer', '--image', self.stock,
                   '--partition_size', self.lock['stock']['size'], '--partition_name', 'boot',
                   '--algorithm', 'NONE', '--hash_algorithm', 'sha256', '--salt', 'aabbccdd',
                   '--rollback_index', 1780272000, '--prop', 'com.android.build.boot.os_version:13',
                   '--prop', 'com.android.build.boot.security_patch:2026-06-01')
        self.lock['stock']['sha256'] = common.sha(self.stock)
        common.atomic_json(self.lock_path, self.lock)
        self.attest()
        first = package.package(self.root, self.lock_path, self.stock, avbtool)
        second = package.package(self.root, self.lock_path, self.stock, avbtool)
        self.assertEqual(first, second)
        self.assertEqual('PASS', first['avb_hash_check'])
        self.assertEqual(first['boot_sha256'], common.sha(self.root / first['boot_path']))

    def test_tool_versions_checked_before_make_and_symlink_invocation(self):
        clang_dir = self.root / 'tools/clang'
        clang = executable(clang_dir / 'bin/clang', '#!/bin/sh\necho WRONG_CLANG\n')
        pahole = executable(self.root / 'tools/pahole', '#!/bin/sh\necho v1.31\n')
        result = self.cli('build_pipeline.py', '--preflight-only', '--clang-dir', clang_dir, '--pahole', pahole)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Wrong clang', result.stderr)
        clang.write_text('#!/bin/sh\necho "Android clang version 14.0.7 (r450784e)"\n')
        pahole.write_text('#!/bin/sh\necho v1.30\n')
        result = self.cli('build_pipeline.py', '--preflight-only', '--clang-dir', clang_dir, '--pahole', pahole)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Wrong pahole', result.stderr)
        pahole.write_text('#!/bin/sh\necho v1.31\n')
        for relative in self.lock['toolchain']['binary_sha256']:
            if relative != 'bin/clang':
                executable(clang_dir / relative, '#!/bin/sh\necho fixture\n')
        self.lock['toolchain']['binary_sha256'] = {
            relative: common.sha(clang_dir / relative) for relative in self.lock['toolchain']['binary_sha256']}
        common.atomic_json(self.lock_path, self.lock)
        # A plausible version string does not let a changed binary through.
        (clang_dir / 'bin/llvm-ar').write_text('#!/bin/sh\necho tampered\n')
        result = self.cli('build_pipeline.py', '--preflight-only', '--clang-dir', clang_dir, '--pahole', pahole)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Toolchain binary SHA256 mismatch: bin/llvm-ar', result.stderr)
        executable(clang_dir / 'bin/llvm-ar', '#!/bin/sh\necho fixture\n')
        alias = self.root / 'build-alias'
        alias.symlink_to(ROOT)
        result = subprocess.run(['bash', str(alias / 'build.sh'), '--root', str(self.root), '--preflight-only',
                                 '--clang-dir', str(clang_dir), '--pahole', str(pahole)], text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('no compilation requested', result.stdout)

    def test_import_without_environment_and_missing_cli_paths_are_clear(self):
        environment = dict(os.environ)
        environment.pop('STOCK_BOOT', None)
        environment.pop('AVBTOOL', None)
        result = subprocess.run([sys.executable, '-c', 'import package; import verify_build'],
                                cwd=ROOT, env=environment, text=True, capture_output=True)
        self.assertEqual(0, result.returncode, result.stderr)
        result = self.cli('package.py', '--stock-boot', self.root / 'missing.img')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('--stock-boot or STOCK_BOOT', result.stderr)
        self.assertNotIn('Traceback', result.stderr)


if __name__ == '__main__':
    unittest.main()
