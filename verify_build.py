#!/usr/bin/env python3
"""Verify source lock, completed build, embedded config, BTF and binary identity."""
import argparse
import json
import os
from pathlib import Path
import re
import sys
from build_pipeline import ARTIFACTS
from pipeline_common import atomic_json, cli_error, load_lock, require, run, sha, verify_image, verify_sources

REQUIRED_BTF = ['__add_to_page_cache_locked', 'dentry_open', 'filp_close', 'migrate_disable',
                'migrate_enable', 'should_fail_alloc_page', 'should_failslab', 'tcp_cong_avoid_ai',
                'tcp_reno_cong_avoid', 'tcp_reno_ssthresh', 'tcp_reno_undo_cwnd', 'tcp_slow_start',
                'vfs_fallocate', 'vfs_getattr', 'vfs_truncate']


def verify_completion(root, lock_path):
    completion = json.loads((root / 'logs/build_completion.json').read_text())
    require(completion.get('status') == 'BUILD_COMPLETED', 'Build has not completed')
    require(completion.get('source_lock_sha256') == sha(lock_path), 'Source lock changed since build')
    expected = completion.get('artifact_sha256', {})
    require(set(expected) == set(ARTIFACTS), 'Incomplete build artifact attestation')
    for relative, checksum in expected.items():
        require(sha(root / relative) == checksum, f'Build artifact changed: {relative}')
    return completion


def verify(root, lock_path, bpftool):
    logs = root / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    # A failed new verification must not leave a previous PASS report in place.
    (logs / 'build_verification.json').unlink(missing_ok=True)
    lock = load_lock(lock_path)
    sources = verify_sources(root, lock)
    completion = verify_completion(root, lock_path)
    out, dist = root / 'out', root / 'dist'
    cfg_text = (out / '.config').read_text()
    cfg = dict(re.findall(r'^(CONFIG_\w+)=(.*)$', cfg_text, re.M))
    cfg.update({name: 'n' for name in re.findall(r'^# (CONFIG_\w+) is not set$', cfg_text, re.M)})
    required = {'CONFIG_KSU': 'y', 'CONFIG_KSU_SUSFS': 'y', 'CONFIG_LTO_CLANG_FULL': 'y',
                'CONFIG_CFI_CLANG': 'y', 'CONFIG_MODULES': 'y', 'CONFIG_MODVERSIONS': 'y',
                'CONFIG_MODULE_SIG_PROTECT': 'y', 'CONFIG_TCP_CONG_BBR': 'y',
                'CONFIG_DEFAULT_TCP_CONG': '"cubic"', 'CONFIG_IKCONFIG': 'y',
                'CONFIG_IKCONFIG_PROC': 'n', 'CONFIG_KSU_SUSFS_ENABLE_LOG': 'n',
                'CONFIG_KSU_SUSFS_SUS_SU': 'n', 'CONFIG_KSU_DEBUG': 'n',
                'CONFIG_KSU_FEATURE_ADBROOT': 'n', 'CONFIG_KPM': 'n', 'CONFIG_DEBUG_INFO_BTF': 'y'}
    # Unknown optional symbols may be absent in this kernel, which also means disabled.
    forbidden = ('CONFIG_CFI_PERMISSIVE', 'CONFIG_KPM', 'CONFIG_MODULE_SIG_FORCE',
                 'CONFIG_KSU_SUSFS_ENABLE_LOG', 'CONFIG_IKCONFIG_PROC', 'CONFIG_KSU_DEBUG',
                 'CONFIG_ADBROOT', 'CONFIG_KSU_ADBROOT', 'CONFIG_KSU_FEATURE_ADBROOT')
    for name in ('SUS_PATH', 'SUS_MOUNT', 'SUS_KSTAT', 'SPOOF_UNAME', 'HIDE_KSU_SUSFS_SYMBOLS',
                 'SPOOF_CMDLINE_OR_BOOTCONFIG', 'OPEN_REDIRECT', 'SUS_MAP'):
        required['CONFIG_KSU_SUSFS_' + name] = 'y'
    for key, value in required.items():
        require(cfg.get(key, 'n') == value, f'Config mismatch: {key}={cfg.get(key)}; expected {value}')
    for key in forbidden:
        require(cfg.get(key, 'n') == 'n', f'Forbidden config enabled: {key}')
    compiler = cfg.get('CONFIG_CC_VERSION_TEXT', '')
    require('clang version ' + lock['toolchain']['clang_version'] in compiler and
            lock['toolchain']['clang_revision'] in compiler, 'Config compiler identity mismatch')
    release = (out / 'include/config/kernel.release').read_text().strip()
    require(release == lock['build']['kernel_release'], f'Kernel release mismatch: {release}')
    require((dist / 'kernel.config').read_text() == cfg_text, 'dist config differs from built config')
    extracted = run('bash', root / 'sources/common/scripts/extract-ikconfig', dist / 'Image')
    require(extracted == cfg_text, 'Embedded Image config differs from built config')
    image = (dist / 'Image').read_bytes()
    verify_image(image)
    require(f'Linux version {release} '.encode() in image, 'Image kernel release mismatch')
    require(lock['build']['ksu_full_version'].encode() in image, 'Image SukiSU full version mismatch')
    require(sha(out / 'arch/arm64/boot/Image') == sha(dist / 'Image'), 'dist Image differs from built Image')
    cmd = (out / 'drivers/kernelsu/.ksu.o.cmd').read_text()
    codes = set(re.findall(r'-DKSU_VERSION=(\d+)\b', cmd))
    require(codes == {str(lock['build']['ksu_version'])}, f'Unexpected compiled KSU_VERSION: {codes}')
    log = (logs / 'build.log').read_text()
    require('using SUSFS_INLINE_HOOK' in log, 'Inline SUSFS hook confirmation missing from build log')
    require('WARN: resolve_btfids: unresolved symbol' not in log, 'Unresolved required BTF symbols')
    btf = run(bpftool, 'btf', 'dump', 'file', out / 'vmlinux')
    evidence = [line for line in btf.splitlines() if any(f"FUNC '{name}' " in line for name in REQUIRED_BTF)]
    missing = [name for name in REQUIRED_BTF if not any(f"FUNC '{name}' " in line for line in evidence)]
    require(not missing, f'Required BTF functions missing: {missing}')
    (logs / 'btf_required_functions.txt').write_text('\n'.join(evidence) + '\n')
    report = {'status': 'PASS_OFFLINE_BUILD_CHECKS', 'source_lock_sha256': sha(lock_path),
              'build_completion_sha256': sha(logs / 'build_completion.json'),
              'artifact_sha256': completion['artifact_sha256'],
              'kernel_sha256': sha(dist / 'Image'), 'kernel_release': release,
              'sukisu_version_code': lock['build']['ksu_version'], 'ksu_uapi': lock['build']['ksu_uapi'],
              'compiler': compiler, 'required_config': required, 'embedded_config_matches': True,
              'btf_required_function_count': len(REQUIRED_BTF), 'source_inputs': sources,
              'built_modules': len(list(out.rglob('*.ko'))), 'phone_runtime': 'NOT_RUN'}
    atomic_json(logs / 'build_verification.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--lock', type=Path)
    parser.add_argument('--bpftool', default=os.environ.get('BPFTOOL', 'bpftool'))
    args = parser.parse_args()
    root = args.root.resolve()
    print(json.dumps(verify(root, args.lock or root / 'source-lock.json', args.bpftool), indent=2))


if __name__ == '__main__':
    sys.exit(cli_error(main))
