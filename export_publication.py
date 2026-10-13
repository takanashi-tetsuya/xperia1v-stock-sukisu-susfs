#!/usr/bin/env python3
"""Export selected public material without changing local evidence.

Text is redacted before SHA256 references between exported files are recomputed.
This is an export operation, not a substitute for build/source verification.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

from pipeline_common import CheckError, atomic_json, cli_error, require, safe_relative

TEXT_SUFFIXES = {'.json', '.txt', '.md', '.py', '.sh', '.yml', '.yaml', '.patch'}
EVIDENCE_SUFFIXES = {'.json', '.txt', '.md', '.log'}
HEX = re.compile(rb'(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])')
# These patterns match complete home/temporary paths, not arbitrary slash text.
PRIVATE_PATH = re.compile(r'/(?:home|Users)/[^/\s"\'<>():\\]+(?:/[^\s"\'<>():\\]*)?')
TEMP_PATH = re.compile(r'/(?:tmp|var/tmp)/[^\s"\'<>():\\]+')


def digest(data):
    return hashlib.sha256(data).hexdigest()


class PublicExport:
    """A fail-closed, dependency-aware export of immutable input bytes."""
    def __init__(self, root, external_roots=()):
        self.root = Path(root).resolve()
        self.replacements = [(str(self.root), '.')]
        for label, path in external_roots:
            require(re.fullmatch(r'[a-z0-9][a-z0-9_-]*', label), 'Invalid external-root label')
            self.replacements.append((str(Path(path).resolve()), 'external/' + label))
        self.replacements.sort(key=lambda item: len(item[0]), reverse=True)
        self.inputs = {}
        self.origins = {}
        self.rebind = {}
        self.transform = {}
        self.rendered = None

    def redact(self, text):
        for source, replacement in self.replacements:
            text = re.sub(re.escape(source) + r'(?=/|[\s"\'<>():\\]|$)',
                          lambda _: replacement, text)
        text = PRIVATE_PATH.sub('external/redacted-home', text)
        text = TEMP_PATH.sub('external/temporary', text)
        return text

    def add(self, source, name, rebind=None, transform=None):
        name = safe_relative(name).as_posix()
        source = Path(source)
        require(source.is_file() and not source.is_symlink(), f'Export input is not a regular file: {name}')
        data = source.read_bytes()
        require(name not in self.inputs or self.inputs[name] == data, f'Conflicting export destination: {name}')
        self.inputs[name] = data
        self.origins[name] = source
        self.transform[name] = (Path(name).suffix in EVIDENCE_SUFFIXES and
                                Path(name).name != 'source-lock.json') if transform is None else transform
        # Preservation records refer to old immutable products, never the new
        # public copy of a coincidentally identical evidence file.
        self.rebind[name] = (self.transform[name] and not name.endswith('_unchanged.json')) if rebind is None else rebind
        self.rendered = None

    def add_bytes(self, data, name, rebind=True, transform=True):
        name = safe_relative(name).as_posix()
        require(name not in self.inputs, f'Duplicate export destination: {name}')
        self.inputs[name] = data
        self.rebind[name] = rebind
        self.transform[name] = transform
        self.rendered = None

    def render(self):
        if self.rendered is not None:
            self.check_unchanged()
            return self.rendered
        initial = {}
        eligible_text = set()
        for name, data in self.inputs.items():
            try:
                text = data.decode('utf-8')
            except UnicodeDecodeError:
                require(Path(name).suffix not in TEXT_SUFFIXES, f'Invalid UTF-8 public text: {name}')
                initial[name] = data
                continue
            # Binary payloads can contain printable strings. They must never be edited.
            if b'\0' in data:
                initial[name] = data
                continue
            if not self.transform[name]:
                # Never rewrite scripts, CI, patches, source or source-lock:
                # e.g. a legitimate /tmp path in CI must remain executable.
                for source, _ in self.replacements:
                    require(source not in text, f'Private absolute path in immutable public input: {name}; fix its producer')
                initial[name] = data
                continue
            initial[name] = self.redact(text).encode('utf-8')
            eligible_text.add(name)
        # Identical originals must have identical public bytes: otherwise a hash
        # reference alone cannot identify which public target was intended.
        representatives = {}
        for name, raw in self.inputs.items():
            raw_sha = digest(raw)
            if raw_sha in representatives:
                require(initial[name] == initial[representatives[raw_sha]], 'Ambiguous exported digest target')
            else:
                representatives[raw_sha] = name
        rendered, active = {}, set()

        def finish(name):
            if name in rendered:
                return rendered[name]
            require(name not in active, f'Cyclic evidence digest reference: {name}')
            active.add(name)
            data = initial[name]
            replacements = {}
            for token in set(HEX.findall(data)) if self.rebind[name] and name in eligible_text else ():
                referenced = representatives.get(token.decode('ascii'))
                if referenced is not None:
                    replacements[token] = digest(finish(referenced)).encode('ascii')
            data = HEX.sub(lambda match: replacements.get(match.group(0), match.group(0)), data)
            active.remove(name)
            rendered[name] = data
            return data

        for name in initial:
            finish(name)
        # The originals remain the authority for verification before export.
        self.check_unchanged()
        self.rendered = rendered
        return rendered

    def check_unchanged(self):
        for name, origin in self.origins.items():
            require(origin.is_file() and not origin.is_symlink() and
                    origin.read_bytes() == self.inputs[name], f'Export input changed: {name}')

    def public_sha(self, name):
        return digest(self.render()[name])

    def rewrite_object(self, value):
        """Redact inline report copies and bind them to the same public digests."""
        data = self.redact(json.dumps(value, ensure_ascii=False, sort_keys=True)).encode()
        mapping = {digest(raw).encode(): digest(self.render()[name]).encode()
                   for name, raw in self.inputs.items()}
        return json.loads(HEX.sub(lambda m: mapping.get(m.group(0), m.group(0)), data))

    def receipt(self):
        return {'schema_version': 1, 'status': 'PUBLIC_EXPORT_HASHES_REBOUND',
                'raw_inputs_modified': False,
                'scope': 'Redacted copies only; raw SHA256 values identify local verified evidence, not public files.',
                'files': {name: {'raw_sha256': digest(raw), 'public_sha256': self.public_sha(name),
                                 'changed_for_publication': raw != self.render()[name]}
                          for name, raw in sorted(self.inputs.items())}}

    def write(self, destination):
        destination = Path(destination)
        require(not destination.is_symlink(), 'Export destination must not be a symlink')
        destination.mkdir(parents=True, exist_ok=True)
        require(not any(destination.iterdir()), 'Export destination must be empty')
        require('publication_export.json' not in self.inputs, 'Reserved export receipt destination')
        for name, data in self.render().items():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            if name in self.origins:
                target.chmod(self.origins[name].stat().st_mode & 0o777)
        atomic_json(destination / 'publication_export.json', self.receipt())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--destination', type=Path, required=True, help='New/empty staging directory')
    parser.add_argument('--files', type=Path, required=True,
                        help='UTF-8 file containing one selected project-relative regular file per line')
    parser.add_argument('--external-root', action='append', default=[], metavar='LABEL=PATH')
    args = parser.parse_args()
    require(args.root.is_dir(), 'Project root is missing')
    require(args.files.is_file(), 'Selected file list is missing')
    labels = []
    for item in args.external_root:
        require('=' in item, '--external-root must be LABEL=PATH')
        labels.append(item.split('=', 1))
    exporter = PublicExport(args.root, labels)
    for name in args.files.read_text().splitlines():
        if name and not name.startswith('#'):
            relative = safe_relative(name)
            path = args.root / relative
            require(path.resolve().is_relative_to(args.root.resolve()), f'Export path escapes root: {name}')
            exporter.add(path, relative.as_posix())
    exporter.write(args.destination)
    print(f'Exported {len(exporter.inputs)} files; originals unchanged.')


if __name__ == '__main__':
    sys.exit(cli_error(main))
