#!/usr/bin/env python3
"""Preflight and build with pinned tools, reviewed sources and output attestation."""
import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
from pipeline_common import atomic_json, cli_error, load_lock, require, run, safe_relative, sha, verify_sources

ARTIFACTS = ('out/.config', 'out/arch/arm64/boot/Image', 'out/vmlinux',
             'out/include/config/kernel.release', 'out/drivers/kernelsu/.ksu.o.cmd',
             'dist/Image', 'dist/kernel.config', 'dist/Module.symvers', 'dist/kernel.release',
             'logs/build.log')


def preflight(root, lock, clang_dir, pahole):
    require(clang_dir.is_dir(), f'Clang directory missing: {clang_dir}; use --clang-dir or CLANG_DIR')
    clang = clang_dir / 'bin/clang'
    require(clang.is_file() and os.access(clang, os.X_OK), f'Clang is not executable: {clang}')
    compiler = run(clang, '--version')
    spec = lock['toolchain']
    require(re.search(r'\bclang version ' + re.escape(spec['clang_version']) + r'\b', compiler)
            and re.search(r'\b' + re.escape(spec['clang_revision']) + r'\b', compiler),
            f"Wrong clang: require {spec['clang_version']} {spec['clang_revision']}; got {compiler.strip()}")
    version = run(pahole, '--version').strip()
    require(version == 'v' + spec['pahole_version'],
            f"Wrong pahole: require v{spec['pahole_version']}; got {version}")
    binary_hashes = spec.get('binary_sha256', {})
    require({'bin/clang', 'bin/clang-14', 'bin/ld.lld', 'bin/llvm-ar', 'bin/llvm-objcopy'} <= set(binary_hashes),
            'Toolchain lock is missing required binary SHA256 values')
    for relative, expected in binary_hashes.items():
        binary = clang_dir / safe_relative(relative)
        require(binary.is_file(), f'Toolchain binary missing: {binary}')
        require(sha(binary) == expected, f'Toolchain binary SHA256 mismatch: {relative}')
    sources = verify_sources(root, lock)
    return {'compiler': compiler.strip(), 'pahole': version, 'toolchain_binary_sha256': binary_hashes, 'sources': sources}


def logged(command, path, env):
    with path.open('w') as stream:
        process = subprocess.Popen([str(v) for v in command], stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, env=env)
        for line in process.stdout:
            stream.write(line)
            print(line, end='', flush=True)
        require(process.wait() == 0, f'Build command failed; see {path}')


def main():
    import shutil
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--lock', type=Path)
    parser.add_argument('--clang-dir', type=Path, default=os.environ.get('CLANG_DIR'))
    parser.add_argument('--pahole', default=os.environ.get('PAHOLE'))
    parser.add_argument('--jobs', type=int, default=os.environ.get('BUILD_JOBS', '12'))
    parser.add_argument('--preflight-only', action='store_true')
    args = parser.parse_args()
    require(args.jobs > 0, '--jobs must be positive')
    root = args.root.resolve()
    lock_path = (args.lock or root / 'source-lock.json').resolve()
    lock = load_lock(lock_path)
    clang_dir = (args.clang_dir or root / 'toolchains/clang-r450784e').resolve()
    pahole = str(Path(args.pahole).resolve()) if args.pahole and '/' in args.pahole else (
        args.pahole or str((root / 'host-tools/bin/pahole').resolve()))
    logs = root / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    evidence = preflight(root, lock, clang_dir, pahole)
    evidence['source_lock_sha256'] = sha(lock_path)
    atomic_json(logs / 'preflight.json', evidence)
    if args.preflight_only:
        print('PASS: source and toolchain preflight (no compilation requested)')
        return
    for filename in ('build_completion.json', 'build_completed_at.txt', 'build_verification.json',
                     'packaging_verification.json'):
        (logs / filename).unlink(missing_ok=True)
    atomic_json(logs / 'build_inputs.json', evidence)
    out = root / 'out'
    dist = root / 'dist'
    out.mkdir(parents=True, exist_ok=True)
    dist.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update(PATH=f"{root / 'host-tools/bin'}:{clang_dir / 'bin'}:{env.get('PATH', '')}",
               ARCH='arm64', SUBARCH='arm64', LLVM='1', LLVM_IAS='1',
               CROSS_COMPILE='aarch64-linux-gnu-', PAHOLE=pahole, LOCALVERSION='',
               KBUILD_BUILD_USER='builder', KBUILD_BUILD_HOST='xperia1v', KBUILD_BUILD_VERSION='1',
               KBUILD_BUILD_TIMESTAMP=lock['build']['build_timestamp'],
               SOURCE_DATE_EPOCH=str(lock['build']['source_date_epoch']),
               KCFLAGS='-D__ANDROID_COMMON_KERNEL__')
    make = ['make', '-C', root / 'sources/common', f'O={out}']
    logged(make + ['gki_defconfig'], logs / 'config.log', env)
    logged(make + [f'-j{args.jobs}', 'Image', 'modules'], logs / 'build.log', env)
    verify_sources(root, lock)
    require(sha(lock_path) == evidence['source_lock_sha256'], 'Source lock changed during build')
    for source, destination in (('arch/arm64/boot/Image', 'Image'), ('.config', 'kernel.config'),
                                ('Module.symvers', 'Module.symvers'),
                                ('include/config/kernel.release', 'kernel.release')):
        shutil.copy2(out / source, dist / destination)
    completion = {'status': 'BUILD_COMPLETED', 'source_lock_sha256': sha(lock_path),
                  'artifact_sha256': {name: sha(root / name) for name in ARTIFACTS}}
    atomic_json(logs / 'build_completion.json', completion)
    (logs / 'build_completed_at.txt').write_text('See build_completion.json for content hashes.\n')
    print('Build completed; run verify_build.py before packaging.')


if __name__ == '__main__':
    sys.exit(cli_error(main))
