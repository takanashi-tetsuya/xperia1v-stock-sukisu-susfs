#!/usr/bin/env python3
"""Offline fixtures for complete draft Release verification; never contact GitHub."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('github_sync_verifier', ROOT / 'verify_github_sync.py')
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)
COMMIT = 'a' * 40
REPO = 'example/kernel'
TAG = '40900-repair-r2'


def expected_assets():
    names = ['RELEASE_SHA256SUMS'] + [f'asset-{i:02d}' for i in range(20)]
    return {name: {'size': len(name), 'sha256': hashlib.sha256(name.encode()).hexdigest()} for name in names}


class FakeGitHub:
    def __init__(self):
        self.expected = expected_assets()
        self.main = COMMIT
        self.release = {'id': 123, 'tag_name': TAG, 'draft': True, 'target_commitish': COMMIT,
                        'html_url': 'https://github.com/example/kernel/releases/tag/untagged-deadbeef',
                        'private_diagnostic': '/home/private-user/private-token'}
        self.assets = [{'id': i + 1, 'name': name, 'size': item['size'], 'state': 'uploaded',
                        'digest': 'sha256:' + item['sha256']} for i, (name, item) in enumerate(self.expected.items())]
        self.runs = [{'id': 5, 'run_number': 1, 'run_attempt': 1, 'head_sha': 'b' * 40,
                      'head_branch': 'main', 'event': 'push', 'status': 'completed', 'conclusion': 'failure'},
                     {'id': 6, 'run_number': 2, 'run_attempt': 1, 'head_sha': COMMIT,
                      'head_branch': 'main', 'event': 'push', 'status': 'completed', 'conclusion': 'success'}]
        self.downloads = []
        self.calls = []
        self.change_main_after_first_read = False
        self.change_assets_after_first_read = False
        self.main_reads = self.asset_reads = 0

    def object(self, endpoint):
        self.calls.append(endpoint)
        if endpoint.endswith('/git/ref/heads/main'):
            self.main_reads += 1
            sha = 'c' * 40 if self.change_main_after_first_read and self.main_reads > 1 else self.main
            return {'object': {'type': 'commit', 'sha': sha}}
        if endpoint == 'repos/example/kernel/releases/123':
            return copy.deepcopy(self.release)
        raise AssertionError('Unexpected fixture endpoint: ' + endpoint)

    def pages(self, endpoint):
        self.calls.append(endpoint)
        if endpoint == 'repos/example/kernel/releases?per_page=100':
            return [[], [copy.deepcopy(self.release)]]
        if '/releases/123/assets?' in endpoint:
            self.asset_reads += 1
            assets = copy.deepcopy(self.assets)
            if self.change_assets_after_first_read and self.asset_reads > 1:
                assets[0]['id'] += 100
            return [assets[:5], assets[5:]]
        if '/actions/workflows/verify.yml/runs?' in endpoint:
            return [{'workflow_runs': copy.deepcopy(self.runs[:1])},
                    {'workflow_runs': copy.deepcopy(self.runs[1:])}]
        raise AssertionError('Unexpected fixture endpoint: ' + endpoint)

    def asset_bytes(self, endpoint):
        self.downloads.append(endpoint)
        asset = next(item for item in self.assets if endpoint.endswith('/' + str(item['id'])))
        expected = self.expected[asset['name']]
        return expected['sha256'], expected['size']


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeGitHub()

    def run_verification(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return verify.verify(self.api, REPO, TAG, self.api.expected, COMMIT, 'verify.yml')

    def test_complete_21_assets_from_all_pages_and_public_receipt(self):
        report = self.run_verification()
        self.assertEqual(report['status'], 'PASS_COMPLETE_DRAFT_RELEASE_SYNC')
        self.assertEqual(report['asset_count'], 21)
        self.assertEqual(report['missing_assets'], [])
        self.assertTrue(report['release_draft'])
        self.assertFalse(report['github_mutations_performed'])
        self.assertEqual(report['runtime'], 'NOT_DEVICE_TESTED')
        self.assertFalse(report['kernel_full_build_verified_by_this_tool'])
        self.assertEqual(report['ci']['run_id'], 6)
        self.assertEqual(report['release_url'], self.api.release['html_url'])
        self.assertNotIn('/home/', json.dumps(report))
        self.assertNotIn('private-token', json.dumps(report))
        self.assertEqual(self.api.downloads, [])
        self.assertEqual(self.api.asset_reads, 2)

    def test_missing_asset_rejected(self):
        self.api.assets.pop()
        with self.assertRaisesRegex(verify.VerificationError, 'Asset name mismatch'):
            self.run_verification()

    def test_draft_lookup_uses_paginated_list_and_numeric_id_snapshot(self):
        self.run_verification()
        self.assertIn('repos/example/kernel/releases?per_page=100', self.api.calls)
        self.assertIn('repos/example/kernel/releases/123', self.api.calls)
        self.assertFalse(any('/releases/tags/' in endpoint for endpoint in self.api.calls))

    def test_release_url_rejects_wrong_host_scheme_or_repository(self):
        for value in ('http://github.com/example/kernel/releases/tag/test',
                      'https://example.org/example/kernel/releases/tag/test',
                      'https://github.com@evil.example/example/kernel/releases/tag/test',
                      'https://github.com/other/kernel/releases/tag/test',
                      'https://github.com/example/kernel/releases/tag/%2e%2e/secret',
                      'https://github.com/example/kernel/releases/tag/test?private=value'):
            with self.subTest(value=value), self.assertRaisesRegex(verify.VerificationError, 'outside the expected'):
                verify.release_url({'html_url': value}, REPO)

    def test_extra_asset_rejected(self):
        self.api.assets.append({'id': 100, 'name': 'extra', 'size': 1, 'state': 'uploaded'})
        with self.assertRaisesRegex(verify.VerificationError, 'Asset name mismatch'):
            self.run_verification()

    def test_duplicate_remote_names_rejected_across_pages(self):
        self.api.assets.append(copy.deepcopy(self.api.assets[0]))
        with self.assertRaisesRegex(verify.VerificationError, 'Duplicate remote asset name'):
            self.run_verification()

    def test_duplicate_remote_ids_rejected(self):
        self.api.assets[1]['id'] = self.api.assets[0]['id']
        with self.assertRaisesRegex(verify.VerificationError, 'Duplicate remote asset ID'):
            self.run_verification()

    def test_asset_incomplete_rejected(self):
        self.api.assets[0]['state'] = 'starter'
        with self.assertRaisesRegex(verify.VerificationError, 'not uploaded'):
            self.run_verification()

    def test_wrong_size_rejected(self):
        self.api.assets[0]['size'] += 1
        with self.assertRaisesRegex(verify.VerificationError, 'size differs'):
            self.run_verification()

    def test_bad_digest_rejected_without_download(self):
        self.api.assets[0]['digest'] = 'sha256:' + '0' * 64
        with self.assertRaisesRegex(verify.VerificationError, 'digest differs'):
            self.run_verification()
        self.assertEqual(self.api.downloads, [])

    def test_missing_digest_downloads_only_that_asset(self):
        self.api.assets[0]['digest'] = None
        report = self.run_verification()
        self.assertEqual(self.api.downloads, ['repos/example/kernel/releases/assets/1'])
        self.assertEqual(report['assets']['RELEASE_SHA256SUMS']['verification'], 'DOWNLOADED_SHA256')
        self.assertEqual(sum(a['verification'] == 'GITHUB_SHA256_DIGEST' for a in report['assets'].values()), 20)

    def test_missing_digest_download_mismatch_rejected(self):
        self.api.assets[0].pop('digest')
        self.api.asset_bytes = lambda _: ('0' * 64, 1)
        with self.assertRaisesRegex(verify.VerificationError, 'Downloaded asset checksum or size differs'):
            self.run_verification()

    def test_published_release_rejected(self):
        self.api.release['draft'] = False
        with self.assertRaisesRegex(verify.VerificationError, 'remain a draft'):
            self.run_verification()

    def test_release_tag_mismatch_rejected(self):
        self.api.release['tag_name'] = 'other'
        with self.assertRaisesRegex(verify.VerificationError, 'exactly one accessible Release'):
            self.run_verification()

    def test_floating_release_target_rejected(self):
        self.api.release['target_commitish'] = 'main'
        with self.assertRaisesRegex(verify.VerificationError, 'exact source commit'):
            self.run_verification()

    def test_remote_main_mismatch_rejected(self):
        self.api.main = 'b' * 40
        with self.assertRaisesRegex(verify.VerificationError, 'Remote main differs'):
            self.run_verification()

    def test_main_changed_during_download_rejected(self):
        self.api.change_main_after_first_read = True
        with self.assertRaisesRegex(verify.VerificationError, 'Remote main differs'):
            self.run_verification()

    def test_assets_changed_during_verification_rejected(self):
        self.api.change_assets_after_first_read = True
        with self.assertRaisesRegex(verify.VerificationError, 'assets changed'):
            self.run_verification()

    def test_newer_failed_run_not_hidden_by_old_success(self):
        self.api.runs.append({**self.api.runs[-1], 'id': 7, 'run_number': 3, 'conclusion': 'failure'})
        with self.assertRaisesRegex(verify.VerificationError, 'not completed successfully'):
            self.run_verification()

    def test_in_progress_latest_run_rejected(self):
        self.api.runs[-1]['status'] = 'in_progress'
        self.api.runs[-1]['conclusion'] = None
        with self.assertRaisesRegex(verify.VerificationError, 'not completed successfully'):
            self.run_verification()

    def test_other_branch_ci_does_not_count(self):
        self.api.runs[-1]['head_branch'] = 'other'
        with self.assertRaisesRegex(verify.VerificationError, 'No workflow run'):
            self.run_verification()

    def test_expected_manifest_must_have_exactly_21(self):
        self.api.expected.pop('asset-19')
        with self.assertRaisesRegex(verify.VerificationError, 'exactly 21'):
            self.run_verification()
        self.assertEqual(self.api.calls, [])

    def test_expected_name_must_be_plain_filename(self):
        self.api.expected['../bad'] = self.api.expected.pop('asset-19')
        with self.assertRaisesRegex(verify.VerificationError, 'plain filenames'):
            self.run_verification()


class ApiAndCliTests(unittest.TestCase):
    def test_concatenated_array_and_object_pages_parse(self):
        self.assertEqual(verify.parse_pages(' [1]\n[2,3]\n'), [[1], [2, 3]])
        self.assertEqual(verify.parse_pages('{"workflow_runs":[]}\n{"workflow_runs":[1]}'),
                         [{'workflow_runs': []}, {'workflow_runs': [1]}])

    def test_invalid_or_empty_pages_rejected(self):
        for text in ('', ' \n', '[1]\nnot-json'):
            with self.subTest(text=text), self.assertRaises(verify.VerificationError):
                verify.parse_pages(text)

    def test_paginated_api_is_get_without_slurp(self):
        api = verify.GitHub()
        with patch.object(verify.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='[]\n[]')) as call:
            self.assertEqual(api.pages('repos/example/kernel/releases/123/assets?per_page=100'), [[], []])
        command = call.call_args.args[0]
        self.assertIn('--paginate', command)
        self.assertNotIn('--slurp', command)
        self.assertEqual(command[command.index('--method') + 1], 'GET')
        self.assertEqual(command[command.index('--hostname') + 1], 'github.com')

    def test_streamed_asset_hashes_actual_bytes(self):
        payload = b'real downloaded fixture bytes'
        def response(command, **kwargs):
            self.assertEqual(command[command.index('--method') + 1], 'GET')
            self.assertIn('Accept: application/octet-stream', command)
            kwargs['stdout'].write(payload)
            return SimpleNamespace(returncode=0)
        with patch.object(verify.subprocess, 'run', side_effect=response):
            self.assertEqual(verify.GitHub().asset_bytes('repos/example/kernel/releases/assets/1'),
                             (hashlib.sha256(payload).hexdigest(), len(payload)))

    def test_gh_error_does_not_echo_private_stderr(self):
        response = SimpleNamespace(returncode=1, stdout='', stderr='token and /home/private-user')
        with patch.object(verify.subprocess, 'run', return_value=response):
            with self.assertRaises(verify.VerificationError) as raised:
                verify.GitHub().object('repos/example/kernel/git/ref/heads/main')
        self.assertNotIn('token', str(raised.exception))
        self.assertNotIn('/home/', str(raised.exception))

    def test_cli_requires_explicit_parameters_without_traceback(self):
        process = subprocess.run([sys.executable, str(ROOT / 'verify_github_sync.py')],
                                 text=True, capture_output=True)
        self.assertEqual(process.returncode, 2)
        self.assertNotIn('Traceback', process.stderr)
        for name in ('--repo', '--tag', '--expected-assets-json', '--source-commit', '--workflow', '--output'):
            self.assertIn(name, process.stderr)

    def test_atomic_receipt_has_no_workstation_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'nested/receipt.json'
            verify.atomic_report(path, {'status': 'PASS', 'runtime': 'NOT_DEVICE_TESTED'})
            self.assertEqual(json.loads(path.read_text())['status'], 'PASS')
            self.assertNotIn(temporary, path.read_text())


if __name__ == '__main__':
    unittest.main()
