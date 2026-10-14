"""New candidate guard tests, entirely local fixtures; not kernel/device tests."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline_common import CheckError, sha
from r3_delivery_checks import RUNTIME_TESTS, patch_without_defconfig, verify_runtime_report


class RuntimeBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'source-lock.json').write_text('{}\n')
        self.build = {'kernel_sha256': '1' * 64}
        self.package = {'boot_sha256': '2' * 64, 'anykernel_sha256': '3' * 64}
        self.cli = {'binary_sha256': '4' * 64}
        self.report = {
            'schema_version': 1, 'scope': 'MAINTAINER_REVIEWED_PRIVATE_DEVICE_EVIDENCE',
            'source_lock_sha256': sha(self.root / 'source-lock.json'),
            'kernel_sha256': self.build['kernel_sha256'], **self.package,
            'cli_sha256': self.cli['binary_sha256'], 'phone_modified': False,
            'application_fixes_claimed': False,
            'tests': {k: 'NOT_RUN' for k in RUNTIME_TESTS},
            'private_evidence_sha256': {k: [] for k in RUNTIME_TESTS},
        }

    def verify(self):
        return verify_runtime_report(self.root, self.report, self.build, self.package, self.cli)

    def test_not_run_remains_not_run(self):
        self.assertEqual(self.verify(), self.report)
        self.assertEqual(set(self.report['tests'].values()), {'NOT_RUN'})

    def test_only_explicit_reviewed_result_is_retained(self):
        self.report['tests']['boot'] = 'PASS'
        self.report['private_evidence_sha256']['boot'] = ['5' * 64]
        self.report['phone_modified'] = True
        self.assertEqual(self.verify()['tests']['boot'], 'PASS')
        self.assertEqual(self.verify()['tests']['installer'], 'NOT_RUN')

    def test_wrong_artifact_binding_rejected(self):
        original = copy.deepcopy(self.report)
        for key in ('source_lock_sha256', 'kernel_sha256', 'boot_sha256', 'anykernel_sha256', 'cli_sha256'):
            with self.subTest(key=key):
                self.report = copy.deepcopy(original)
                self.report[key] = 'f' * 64
                with self.assertRaisesRegex(CheckError, 'binding'):
                    self.verify()

    def test_pass_without_evidence_rejected(self):
        self.report['tests']['boot'] = 'PASS'
        with self.assertRaisesRegex(CheckError, 'needs evidence'):
            self.verify()

    def test_private_unrecognized_field_rejected(self):
        self.report['serial'] = 'private fixture'
        with self.assertRaisesRegex(CheckError, 'fields'):
            self.verify()

    def test_missing_test_or_unrecognized_state_rejected(self):
        del self.report['tests']['ota']
        with self.assertRaisesRegex(CheckError, 'keys or states'):
            self.verify()
        self.report['tests']['ota'] = 'SKIPPED_ASSUME_FAIL'
        with self.assertRaisesRegex(CheckError, 'keys or states'):
            self.verify()

    def test_non_hash_or_duplicate_evidence_rejected(self):
        for hashes in ([{}], ['not a digest'], ['5' * 64, '5' * 64]):
            with self.subTest(hashes=hashes):
                self.report['private_evidence_sha256']['boot'] = hashes
                with self.assertRaisesRegex(CheckError, 'digest'):
                    self.verify()

    def test_runtime_cannot_claim_phone_unmodified(self):
        self.report['tests']['boot'] = 'FAIL'
        self.report['private_evidence_sha256']['boot'] = ['5' * 64]
        with self.assertRaisesRegex(CheckError, 'not modified'):
            self.verify()

    def test_non_defconfig_changes_remain_visible(self):
        prefix = 'diff --git a/arch/arm64/configs/gki_defconfig b/arch/arm64/configs/gki_defconfig\n'
        a = prefix + 'old header\n' + 'diff --git a/fs/namei.c b/fs/namei.c\n+good\n'
        b = prefix + 'new header\n' + 'diff --git a/fs/namei.c b/fs/namei.c\n+bad\n'
        self.assertNotEqual(patch_without_defconfig(a), patch_without_defconfig(b))
        with self.assertRaisesRegex(CheckError, 'exactly one'):
            patch_without_defconfig('diff --git a/other b/other\n')


if __name__ == '__main__':
    unittest.main()
