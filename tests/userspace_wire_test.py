#!/usr/bin/env python3
"""Compile AArch64 static assertions over the real CLI/kernel wire declarations."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from test_support import project_path, require_command, require_files, run_cli, source_label

ROOT = Path(__file__).resolve().parents[1]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kernel-header', type=Path, default=Path('sources/common/include/linux/susfs.h'))
    parser.add_argument('--cli-source', type=Path, default=Path('userspace/upstream-susfs/ksu_susfs/jni/features/sus_kstat.c'))
    parser.add_argument('--output', type=Path, default=Path('logs/userspace_wire_test.json'))
    parser.epilog = 'All relative input and output paths are relative to the project root.'
    args = parser.parse_args()
    paths = [project_path(args.kernel_header, ROOT), project_path(args.cli_source, ROOT)]
    output = project_path(args.output, ROOT)
    output.unlink(missing_ok=True)
    require_files(paths, 'Run bash prepare-sources.sh first and retain the userspace source tree, or pass explicit source paths.')
    sources = [p.read_text() for p in paths]
    structs = [re.search(r'struct st_susfs_sus_kstat\s*\{.*?\n\};',s,re.S).group(0) for s in sources]
    fields = [re.findall(r'\b(\w+)(?:\[[^]]+\])?\s*;',s) for s in structs]
    if fields[0] != fields[1]: raise RuntimeError('Kernel/CLI field sequence mismatch')
    c = ['typedef _Bool bool;', '#define SUSFS_MAX_LEN_PATHNAME 256',
         structs[0].replace('st_susfs_sus_kstat','kernel_wire'),
         structs[1].replace('st_susfs_sus_kstat','cli_wire'),
         '_Static_assert(__SIZEOF_LONG__ == 8, "LP64 required");',
         '_Static_assert(sizeof(struct kernel_wire) == sizeof(struct cli_wire), "wire size");']
    for field in fields[0]:
        c.append(f'_Static_assert(__builtin_offsetof(struct kernel_wire, {field}) == '
                 f'__builtin_offsetof(struct cli_wire, {field}), "{field}");')
    flags = [dict(re.findall(r'^#define\s+(KSTAT_SPOOF_\w+)\s+([^\n]+)',s,re.M)) for s in sources]
    if flags[0].keys() != flags[1].keys(): raise RuntimeError('Kernel/CLI flag set mismatch')
    for name in flags[0]:
        c.append(f'_Static_assert(({flags[0][name]}) == ({flags[1][name]}), "{name}");')
    compiler = os.environ.get('WIRE_CC') or shutil.which('clang')
    require_command(compiler, 'Install clang or set WIRE_CC to its executable.')
    with tempfile.TemporaryDirectory(prefix='susfs-wire-') as temp:
        src,obj=Path(temp)/'wire.c',Path(temp)/'wire.o'
        src.write_text('\n'.join(c)+'\n')
        subprocess.run([compiler,'--target=aarch64-linux-gnu','-std=c11','-c',str(src),'-o',str(obj)],check=True)
        result={'status':'PASS','architecture':'AArch64 LP64','field_offsets':len(fields[0]),
                'flags':len(flags[0]),'sizeof_matches':True,
                'source_sha256':{source_label(p, ROOT, label):hashlib.sha256(p.read_bytes()).hexdigest()
                                 for p, label in zip(paths, ('kernel/susfs.h', 'cli/sus_kstat.c'))},
                'scope':'Compiled real kernel/CLI declarations, no runtime syscalls'}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__':
    run_cli(main)
