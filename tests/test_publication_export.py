#!/usr/bin/env python3
"""Publication must preserve raw evidence and rebind only exported references."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from export_publication import PublicExport
from finalize_delivery import (archive_files, validate_external_inputs, ORIGINAL_HASHES,
                               VERSION_EVIDENCE, OFFICIAL_APK)
from pipeline_common import CheckError


def digest(data):
    return hashlib.sha256(data).hexdigest()


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'project'
        self.root.mkdir()

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode() if isinstance(content, str) else content)
        return path

    def test_recursive_references_and_raw_evidence_preserved(self):
        raw = (json.dumps({'source': str(self.root / 'sources/common')}) + '\n').encode()
        source = self.write('logs/source.json', raw)
        check = self.write('logs/check.json', json.dumps({'report_sha256': digest(raw)}))
        top = self.write('logs/top.json', json.dumps({'check_sha256': digest(check.read_bytes())}))
        before = {p: p.read_bytes() for p in (source, check, top)}
        exporter = PublicExport(self.root)
        for path in before:
            exporter.add(path, str(path.relative_to(self.root)))
        result = exporter.render()
        self.assertNotIn(str(self.root).encode(), result['logs/source.json'])
        self.assertEqual(json.loads(result['logs/check.json'])['report_sha256'], digest(result['logs/source.json']))
        self.assertEqual(json.loads(result['logs/top.json'])['check_sha256'], digest(result['logs/check.json']))
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        out = self.base / 'public'
        exporter.write(out)
        receipt = json.loads((out / 'publication_export.json').read_text())
        self.assertFalse(receipt['raw_inputs_modified'])
        for name, entry in receipt['files'].items():
            self.assertEqual(entry['public_sha256'], digest((out / name).read_bytes()))

    def test_json_text_and_external_metadata_redacted(self):
        external = self.base / 'research'
        home = '/' + '/'.join(('home', 'private-user', 'private-build', 'report.json'))
        temporary = '/' + '/'.join(('tmp', 'private-random', 'file'))
        data = f'first {external}/apk.json\nsecond {home}\nthird {temporary}\n'
        path = self.write('review.txt', data)
        exporter = PublicExport(self.root, [('official-release', external)])
        exporter.add(path, 'review.txt')
        public = exporter.render()['review.txt'].decode()
        self.assertIn('external/official-release/apk.json', public)
        self.assertNotIn('private-user', public)
        self.assertNotIn('private-random', public)
        self.assertEqual(path.read_text(), data)

    def test_preservation_record_keeps_historical_raw_hash(self):
        record = self.write('report.json', json.dumps({'path': str(self.root)}))
        historical = self.write('prior_unchanged.json', json.dumps({'files': {'report.json': digest(record.read_bytes())}}))
        exporter = PublicExport(self.root)
        exporter.add(record, 'report.json')
        exporter.add(historical, 'prior_unchanged.json')
        public = exporter.render()
        self.assertNotEqual(digest(public['report.json']), digest(record.read_bytes()))
        self.assertEqual(json.loads(public['prior_unchanged.json'])['files']['report.json'], digest(record.read_bytes()))

    def test_inline_report_uses_exported_hash(self):
        report = self.write('report.txt', str(self.root / 'sources'))
        exporter = PublicExport(self.root)
        exporter.add(report, 'report.txt')
        public = exporter.rewrite_object({'sha256': digest(report.read_bytes()), 'path': str(self.root)})
        self.assertEqual(public['sha256'], exporter.public_sha('report.txt'))
        self.assertEqual(public['path'], '.')

    def test_binary_unchanged(self):
        binary = self.write('binary', b'\0' + str(self.root).encode())
        exporter = PublicExport(self.root)
        exporter.add(binary, 'binary')
        self.assertEqual(exporter.render()['binary'], binary.read_bytes())

    def test_binary_digest_references_never_rebound(self):
        report = self.write('report.txt', str(self.root))
        reference = digest(report.read_bytes()).encode()
        exporter = PublicExport(self.root)
        exporter.add(report, 'report.txt')
        for name, raw in (('nul.log', b'\0' + reference), ('invalid.log', b'\xff' + reference)):
            exporter.add_bytes(raw, name)
        public = exporter.render()
        self.assertNotEqual(digest(public['report.txt']), reference.decode())
        self.assertEqual(public['nul.log'], b'\0' + reference)
        self.assertEqual(public['invalid.log'], b'\xff' + reference)

    def test_real_ci_and_script_paths_are_byte_preserved(self):
        exporter = PublicExport(ROOT)
        for name in ('.github/workflows/verify.yml', 'finalize_delivery.py', 'export_publication.py',
                     'source-lock.json'):
            exporter.add(ROOT / name, name)
        for name, data in exporter.render().items():
            self.assertEqual(data, (ROOT / name).read_bytes())

    def test_private_path_in_executable_is_rejected_not_rewritten(self):
        source = self.write('script.py', f'ROOT = {str(self.root)!r}\n')
        exporter = PublicExport(self.root)
        exporter.add(source, 'script.py')
        with self.assertRaisesRegex(CheckError, 'immutable public input'):
            exporter.render()
        self.assertIn(str(self.root), source.read_text())

    def test_changed_input_rejected(self):
        record = self.write('record.txt', 'first')
        exporter = PublicExport(self.root)
        exporter.add(record, 'record.txt')
        record.write_text('second')
        with self.assertRaisesRegex(CheckError, 'input changed'):
            exporter.render()

    def test_changed_input_rejected_after_cached_render(self):
        record = self.write('record.txt', 'first')
        exporter = PublicExport(self.root)
        exporter.add(record, 'record.txt')
        exporter.render()
        record.write_text('second')
        with self.assertRaisesRegex(CheckError, 'input changed'):
            exporter.write(self.base / 'public')

    def test_symlink_and_escape_rejected(self):
        target = self.write('record.txt', 'data')
        link = self.root / 'link'
        link.symlink_to(target)
        exporter = PublicExport(self.root)
        with self.assertRaises(CheckError):
            exporter.add(link, 'link')
        with self.assertRaises(CheckError):
            exporter.add(target, '../escape')

    def test_nonempty_destination_untouched(self):
        target = self.write('record.txt', 'original')
        exporter = PublicExport(self.root)
        exporter.add(target, 'record.txt')
        with self.assertRaisesRegex(CheckError, 'must be empty'):
            exporter.write(self.root)
        self.assertEqual(target.read_text(), 'original')

    def test_source_archive_uses_public_copies_and_preserves_modes(self):
        source = self.write('added/header.h', '/* source must be byte identical */\n')
        source.chmod(0o664)
        report = self.write('tests/report.json', json.dumps({'source': str(self.root / 'sources')}))
        exporter = PublicExport(self.root)
        exporter.add(source, 'added/header.h')
        exporter.add(report, 'tests/report.json')
        archive_path = self.base / 'source.tar.gz'
        archive_files(archive_path, self.root, [source, report], 1, exporter.render())
        with tarfile.open(archive_path) as archive:
            self.assertEqual(len(archive.getnames()), 2)
            self.assertEqual(archive.extractfile('added/header.h').read(), source.read_bytes())
            self.assertEqual(archive.getmember('added/header.h').mode, 0o664)
            self.assertNotIn(str(self.root).encode(), archive.extractfile('tests/report.json').read())
        self.assertIn(str(self.root), report.read_text())


class MaintainerArgumentsTests(unittest.TestCase):
    def test_required_arguments_friendly_under_both_modes(self):
        for optimization in ([], ['-O']):
            process = subprocess.run([sys.executable, *optimization, str(ROOT / 'finalize_delivery.py')],
                                     text=True, capture_output=True)
            self.assertEqual(process.returncode, 2)
            self.assertNotIn('Traceback', process.stderr)
            for flag in ('--research', '--original-dist', '--previous-40900-dist', '--cli-binary'):
                self.assertIn(flag, process.stderr)

    def test_missing_inputs_do_not_delete_attestation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'dist').mkdir()
            manifest = root / 'dist/manifest.json'
            manifest.write_text('previous delivery')
            process = subprocess.run([sys.executable, str(ROOT / 'finalize_delivery.py'), '--root', str(root),
                                      '--research', str(root / 'missing'), '--original-dist', str(root),
                                      '--previous-40900-dist', str(root), '--cli-binary', str(root / 'cli')],
                                     text=True, capture_output=True)
            self.assertNotEqual(process.returncode, 0)
            self.assertNotIn('Traceback', process.stderr)
            self.assertIn('--research', process.stderr)
            self.assertEqual(manifest.read_text(), 'previous delivery')

    def test_explicit_input_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            research, original, previous = (root / name for name in ('research', 'original', 'previous'))
            for directory in (research, original, previous):
                directory.mkdir()
            for name in VERSION_EVIDENCE + (OFFICIAL_APK,):
                (research / name).write_text('{}')
            for name in ORIGINAL_HASHES:
                (original / name).write_bytes(b'placeholder')
            (root / 'logs').mkdir()
            names = ('Image', 'boot.img', 'install.zip')
            for name in names:
                (previous / name).write_bytes(b'placeholder')
            (root / 'logs/previous40900_unchanged.json').write_text(json.dumps({'files': dict.fromkeys(names, 'placeholder')}))
            cli = root / 'cli'
            cli.write_bytes(b'placeholder')
            # Existence validation is deliberately distinct from verify_inputs,
            # which must still authenticate every artifact before finalization.
            validate_external_inputs(root, research, original, previous, cli)
            cli.unlink()
            with self.assertRaisesRegex(CheckError, '--cli-binary'):
                validate_external_inputs(root, research, original, previous, cli)


if __name__ == '__main__':
    unittest.main()
