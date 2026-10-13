#!/usr/bin/env python3
"""Check missing-input diagnostics, stale-result removal and portable test paths."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_support import TestInputError, project_path, run_cli, source_label

TESTS = Path(__file__).resolve().parent
SCRIPTS = (
    ('anykernel_test.py', ['--source', 'missing'], '--output'),
    ('userspace_wire_test.py', ['--kernel-header', 'missing/susfs.h'], '--output'),
    ('susfs_namei_regression.py', ['--source', 'missing'], '--output'),
    ('susfs_legacy_regression.py', ['missing'], '--output'),
    ('suki_security_test.py', ['--source', 'missing', '--apk', 'missing.apk'], '--json-output'),
    ('userspace_ctime_test.py', ['--jni', 'missing'], '--output'),
)


class CliInputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='host-cli-inputs-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'checkout'
        self.tests = self.root / 'tests'
        self.tests.mkdir(parents=True)
        self.caller = Path(self.temporary.name) / 'unrelated-cwd'
        self.caller.mkdir()
        for filename in ['test_support.py', *[row[0] for row in SCRIPTS]]:
            shutil.copyfile(TESTS / filename, self.tests / filename)

    def run_script(self, name, *arguments):
        return subprocess.run([sys.executable, *(['-O'] if sys.flags.optimize else []),
                               str(self.tests / name), *arguments], cwd=self.caller,
                              text=True, capture_output=True, timeout=30)

    def test_missing_sources_are_friendly_and_remove_stale_pass(self):
        for script, arguments, output_flag in SCRIPTS:
            with self.subTest(script=script):
                output = self.root / 'old-pass.json'
                output.write_text('{"status":"PASS"}\n')
                result = self.run_script(script, *arguments, output_flag, 'old-pass.json')
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('error: Missing required input:', result.stderr)
                self.assertNotIn('Traceback', result.stderr)
                self.assertIn(str(self.root / 'missing'), result.stderr)
                self.assertNotIn(str(self.caller / 'missing'), result.stderr)
                self.assertFalse(output.exists(), 'A failed rerun retained stale PASS evidence')

    def test_security_requires_explicit_official_apk(self):
        result = self.run_script('suki_security_test.py')
        self.assertEqual(result.returncode, 2)
        self.assertIn('--apk', result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_security_missing_apk_has_no_traceback(self):
        source = self.root / 'sources/SukiSU-Ultra/kernel'
        for filename in ('manager/apk_sign.c', 'selinux/sepolicy.c', 'runtime/ksud.c',
                         'sulog/event.c', 'sulog/event.h', 'Makefile'):
            path = source / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('/* input preflight only */\n')
        result = self.run_script('suki_security_test.py', '--apk', 'missing.apk')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('Pass --apk', result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_bad_apk_checksum_is_checked_under_optimization(self):
        spec = importlib.util.spec_from_file_location('security_input_fixture', TESTS / 'suki_security_test.py')
        security = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(security)
        invalid = self.root / 'invalid.apk'
        invalid.write_bytes(b'not the official APK')
        with self.assertRaisesRegex(TestInputError, 'checksum differs'):
            security.apk_cases(self.root, invalid)

    @unittest.skipUnless(shutil.which('clang'), 'clang is required for the wire fixture')
    def test_wire_relative_paths_work_from_unrelated_cwd(self):
        text = '''struct st_susfs_sus_kstat {
    unsigned long inode;
    long seconds;
};
#define KSTAT_SPOOF_CTIME (1 << 8)
'''
        for filename in ('fixtures/susfs.h', 'fixtures/sus_kstat.c'):
            path = self.root / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        result = self.run_script('userspace_wire_test.py', '--kernel-header', 'fixtures/susfs.h',
                                 '--cli-source', 'fixtures/sus_kstat.c', '--output', 'result/wire.json')
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((self.root / 'result/wire.json').read_text())
        self.assertEqual(report['status'], 'PASS')
        self.assertEqual(set(report['source_sha256']), {'fixtures/susfs.h', 'fixtures/sus_kstat.c'})
        self.assertNotIn(str(self.root), json.dumps(report))
        self.assertFalse((self.caller / 'result').exists())

    def test_project_paths_resolve_without_callers_cwd(self):
        self.assertEqual(project_path('sources/common', self.root), self.root / 'sources/common')
        absolute = self.caller / 'source'
        self.assertEqual(project_path(absolute, self.root), absolute)

    def test_report_paths_are_project_relative(self):
        self.assertEqual(source_label(self.root / 'sources/common', self.root, 'baseline/common'),
                         'sources/common')

    def test_external_report_paths_use_semantic_label(self):
        external = self.caller / 'private-user-checkout'
        self.assertEqual(source_label(external, self.root, 'susfs-baseline/common'),
                         'external/susfs-baseline/common')

    def test_expected_input_error_is_friendly(self):
        def missing():
            raise TestInputError('input missing')
        output = io.StringIO()
        with contextlib.redirect_stderr(output), self.assertRaises(SystemExit) as error:
            run_cli(missing)
        self.assertEqual(error.exception.code, 2)
        self.assertEqual(output.getvalue(), 'error: input missing\n')

    def test_real_test_failures_are_not_swallowed(self):
        def failure():
            raise AssertionError('real regression')
        with self.assertRaisesRegex(AssertionError, 'real regression'):
            run_cli(failure)


if __name__ == '__main__':
    unittest.main()
