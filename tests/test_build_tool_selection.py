#!/usr/bin/env python3
"""Exercise pipeline pahole selection with real GNU make and recursive make.

This uses a tiny make fixture instead of compiling a kernel. Source/toolchain
identity validation is stubbed; tool invocation and make precedence are real.
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

PIPELINE = Path(os.environ.get('BUILD_PIPELINE_UNDER_TEST',
                             Path(__file__).resolve().parents[1] / 'build_pipeline.py')).resolve()
sys.path.insert(0, str(PIPELINE.parent))
spec = importlib.util.spec_from_file_location('build_pipeline_tool_selection', PIPELINE)
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)


@unittest.skipUnless(shutil.which('make'), 'GNU make is required')
class PaholeSelection(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pahole-selection-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'project'
        self.common = self.root / 'sources/common'
        self.common.mkdir(parents=True)
        self.host_bin = self.root / 'host-tools/bin'
        self.host_bin.mkdir(parents=True)
        self.external_bin = self.base / 'external-bin'
        self.external_bin.mkdir()
        self.trace = self.base / 'pahole.trace'
        self.make_tool(self.external_bin / 'pahole', 'verified-external')
        self.make_tool(self.host_bin / 'pahole', 'shadow-tool')
        self.lock = {'build': {'build_timestamp': '2026-10-13 00:00:00 UTC',
                               'source_date_epoch': 1791849600}}
        (self.root / 'source-lock.json').write_text(json.dumps(self.lock))
        (self.common / 'Makefile').write_text('''PAHOLE = pahole
.PHONY: gki_defconfig Image modules
gki_defconfig:
\t@$(PAHOLE) --version
\t@printf 'CONFIG_FIXTURE=y\\n' > $(O)/.config
Image:
\t@$(MAKE) --no-print-directory -f Recursive.mk O=$(O) compile
modules:
\t@printf 'fixture\\n' > $(O)/Module.symvers
''')
        (self.common / 'Recursive.mk').write_text('''PAHOLE = pahole
.PHONY: compile
compile:
\t@$(PAHOLE) --version
\t@mkdir -p $(O)/arch/arm64/boot $(O)/include/config $(O)/drivers/kernelsu
\t@printf 'fixture Image\\n' > $(O)/arch/arm64/boot/Image
\t@printf 'fixture vmlinux\\n' > $(O)/vmlinux
\t@printf 'fixture release\\n' > $(O)/include/config/kernel.release
\t@printf 'fixture command\\n' > $(O)/drivers/kernelsu/.ksu.o.cmd
''')

    @staticmethod
    def make_tool(path, label):
        path.write_text('#!/bin/sh\n'
                        f"printf '%s\\n' '{label}' >> \"$PAHOLE_TRACE\"\n"
                        "printf '%s\\n' 'v1.31'\n")
        path.chmod(0o755)

    def exercise(self, arguments, expected, environment_pahole=None):
        environment = {**os.environ,
                       'PATH': f'{self.external_bin}:{os.environ.get("PATH", "")}',
                       'PAHOLE_TRACE': str(self.trace)}
        environment.pop('PAHOLE', None)
        if environment_pahole is not None:
            environment['PAHOLE'] = environment_pahole
        checked = []

        def preflight(root, lock, clang_dir, pahole):
            # Invoke the selected tool just as production preflight does.
            checked.append(pahole)
            self.assertEqual(pipeline.run(pahole, '--version').strip(), 'v1.31')
            return {'pahole': 'v1.31', 'sources': {}}

        argv = [str(PIPELINE), '--root', str(self.root), '--jobs', '1', *arguments]
        with patch.dict(os.environ, environment, clear=True), \
                patch.object(sys, 'argv', argv), \
                patch.object(pipeline, 'load_lock', return_value=self.lock), \
                patch.object(pipeline, 'preflight', side_effect=preflight), \
                patch.object(pipeline, 'verify_sources', return_value={}), \
                contextlib.redirect_stdout(io.StringIO()):
            pipeline.main()
        # One preflight, one config invocation, one nested build invocation.
        self.assertEqual(self.trace.read_text().splitlines(), [expected] * 3)
        self.assertTrue(Path(checked[0]).is_absolute(), 'Preflight must bind an absolute tool path')
        completion = json.loads((self.root / 'logs/build_completion.json').read_text())
        self.assertEqual(completion['status'], 'BUILD_COMPLETED')

    def test_explicit_absolute_path_beats_makefile_assignment(self):
        self.exercise(['--pahole', str(self.external_bin / 'pahole')], 'verified-external')

    def test_bare_name_is_bound_before_host_tools_path_is_prepended(self):
        self.exercise(['--pahole', 'pahole'], 'verified-external')

    def test_environment_selection_uses_the_same_checked_tool(self):
        self.exercise([], 'verified-external', environment_pahole=str(self.external_bin / 'pahole'))

    def test_default_host_tool_still_works(self):
        self.exercise([], 'shadow-tool')


if __name__ == '__main__':
    unittest.main()
