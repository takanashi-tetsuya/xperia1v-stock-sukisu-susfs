#!/usr/bin/env python3
"""Finalize this candidate only after its build, package and host evidence passes."""
import argparse
import io
import json
import os
from pathlib import Path, PurePosixPath
import posixpath
import re
import shutil
import sys
import tarfile
import tempfile

from pipeline_common import (atomic_json, cli_error, git, load_lock, require,
                             safe_relative, sha, verify_sources)
from verify_build import verify_completion

OFFICIAL_APK = 'SukiSU_v4.2.0_40900-release.apk'
OFFICIAL_SHA = '4ca9810e6355fbff0bbe4bf5ce159808e64a40d85987a11894030cd990a6bdf3'
SUSFS_COMMIT = '7af04b08f86a5f811cbea28805f96d52368e005f'
ORIGINAL_HASHES = {
    'Image': '31612a88cd898eab9bb379a1f07361430f6fee32b05f60e3b59453f980f7e0cb',
    'boot_XQ-DQ72_67.2.A.3.178_sukisu40922.img': '5369d90ccdea043cbe00657a4a733aef183c7ac230bc2d55894cc0aeeb91c3a8',
    'AnyKernel3_Xperia1V_SukiSU40922_SUSFS2.3_20260920.zip': 'c9751a3f2859b7add985ed97db94acfe7f959c22479226f4de2233631cf66bae',
}
EVIDENCE = (
    'logs/build_completion.json', 'logs/build_verification.json',
    'logs/packaging_verification.json', 'logs/vendor_audit/summary.json',
    'logs/vendor_audit/protection.json', 'logs/source_reconstruction_check.json',
    'logs/vendor_audit/vermagic_check.json',
    'logs/anykernel_host_test.json', 'logs/userspace_ctime_test.json',
    'logs/userspace_wire_test.json', 'logs/packaging_final_tests.txt',
    'logs/previous40900_unchanged.json',
    'tests/susfs_namei_candidate.json', 'tests/susfs_namei_baseline.json',
    'tests/susfs_40900_preserved.json', 'tests/susfs_source_hashes.json',
    'tests/susfs_repair_report.txt', 'tests/suki_security_results.json',
    'tests/suki_security_review.txt',
)
VERSION_EVIDENCE = ('version-verification.json', 'apk-inspection.json',
                    'apk-signature-verification.json')


def read(path):
    require(path.is_file() and not path.is_symlink(), f'Missing regular evidence file: {path}')
    return json.loads(path.read_text())


def match(path, expected):
    require(isinstance(expected, str) and re.fullmatch('[0-9a-f]{64}', expected),
            f'Missing/invalid SHA256 for {path}')
    require(path.is_file() and not path.is_symlink(), f'Missing regular file: {path}')
    require(sha(path) == expected, f'SHA256 mismatch: {path}')


def relative_file(root, value):
    path = root / safe_relative(value)
    require(path.resolve().is_relative_to(root.resolve()), f'Path escapes root: {value}')
    require(path.is_file() and not path.is_symlink(), f'Not a regular file: {value}')
    return path


def check_source_hashes(base, hashes):
    require(isinstance(hashes, dict) and hashes, 'Missing source digest map')
    for name, expected in hashes.items():
        match(relative_file(base, name), expected)


def check_count(report, count, label):
    require(report.get('tests') == count and report.get('passed') == count,
            f'{label}: expected {count} passed tests')
    if 'results' in report:
        require(len(report['results']) == count and
                all(case.get('passed') is True for case in report['results']),
                f'{label}: individual results disagree')


def verify_inputs(root, research, original):
    lock_path = root / 'source-lock.json'
    lock = load_lock(lock_path)
    require(lock['build']['ksu_version'] == 40900 and lock['build']['ksu_uapi'] == 2,
            'This delivery requires the reviewed official 40900 / UAPI 2 generation')
    sources = verify_sources(root, lock)
    completion = verify_completion(root, lock_path)
    reports = {name: read(root / name) for name in EVIDENCE if name.endswith('.json')}
    build = reports['logs/build_verification.json']
    package = reports['logs/packaging_verification.json']
    require(build.get('status') == 'PASS_OFFLINE_BUILD_CHECKS', 'Build verification did not pass')
    require(package.get('status') == 'PASS_OFFLINE_PACKAGING_CHECKS', 'Packaging verification did not pass')
    for label, report in [('build', build), ('package', package)]:
        require(report.get('source_lock_sha256') == sha(lock_path), f'{label}: source lock changed')
        match(root / 'dist/Image', report.get('kernel_sha256'))
    require(build.get('source_inputs') == sources, 'Build source snapshots differ')
    require(build.get('artifact_sha256') == completion['artifact_sha256'], 'Build artifact set differs')
    match(root / 'logs/build_completion.json', build.get('build_completion_sha256'))
    require(build.get('kernel_release') == lock['build']['kernel_release'], 'Kernel release differs')
    require(build.get('sukisu_version_code') == 40900 and build.get('ksu_uapi') == 2,
            'Compiled version generation differs')
    require(build.get('embedded_config_matches') is True and build.get('phone_runtime') == 'NOT_RUN',
            'Unexpected config/runtime attestation')
    boot = relative_file(root, package['boot_path'])
    zipfile = relative_file(root, package['anykernel_path'])
    require(boot.parent == zipfile.parent and boot.parent.parent == root / 'dist/packages',
            'Package paths must share the published dist/packages bundle')
    match(boot, package.get('boot_sha256'))
    match(zipfile, package.get('anykernel_sha256'))
    match(boot.parent / 'packaging_verification.json', sha(root / 'logs/packaging_verification.json'))
    for field in ('stock_boot_unchanged', 'boot_header_other_fields_match_stock',
                  'boot_and_zip_kernel_payloads_match_Image'):
        require(package.get(field) is True, f'Packaging check missing: {field}')
    require(package.get('stock_sha256') == lock['stock']['sha256'] and
            package.get('avb_hash_check') == 'PASS' and
            package.get('phone_boot_and_installer_execution') == 'NOT_RUN', 'Unexpected package evidence')

    vendor = reports['logs/vendor_audit/summary.json']
    require(vendor.get('status') == 'PASS_STATIC', 'Vendor ABI audit did not pass')
    for field in ('kernel_crc_mismatch_count', 'candidate_crc_mismatch_count',
                  'peer_crc_mismatch_count', 'unresolved_reference_count',
                  'unresolved_strong_unversioned_reference_count'):
        require(vendor.get(field) == 0, f'Vendor ABI check failed/missing: {field}')
    require(vendor.get('module_count', 0) > 0, 'Empty vendor audit')
    vermagic = reports['logs/vendor_audit/vermagic_check.json']
    require(vermagic.get('status') == 'PASS_STATIC' and vermagic.get('mismatches') == [] and
            vermagic.get('module_count') == vendor['module_count'] and
            vermagic.get('modules_with_crc_records') == vendor['module_count'] and
            vermagic.get('runtime_loading') == 'NOT_RUN', 'Module vermagic audit did not pass')
    match(root / 'sources/common/kernel/module.c', vermagic.get('loader_source_sha256'))
    match(root / 'out/Module.symvers', vendor.get('symvers_sha256'))
    match(root / 'dist/Module.symvers', vendor.get('symvers_sha256'))
    protection = reports['logs/vendor_audit/protection.json']
    require(protection.get('status') == 'PASS_STATIC_NO_TRUST_NEEDED', 'Module protection audit did not pass')
    require(protection.get('protected_import_table_entries') == 0 and
            protection.get('protected_export_table_entries') == 0 and
            protection.get('untrusted_module_protection_collisions') == [], 'Unexpected module protection tables')
    config_text = (root / 'out/.config').read_text()
    config = dict(re.findall(r'^(CONFIG_\w+)=(.*)$', config_text, re.M))
    config.update({name: 'n' for name in re.findall(r'^# (CONFIG_\w+) is not set$', config_text, re.M)})
    expected_config = {'CONFIG_MODULE_SIG': 'y', 'CONFIG_MODULE_SIG_FORCE': 'n',
                       'CONFIG_MODULE_SIG_PROTECT': 'y', 'CONFIG_MODVERSIONS': 'y',
                       'CONFIG_MODULE_FORCE_LOAD': 'n'}
    require(protection.get('config') == expected_config, 'Module protection configuration differs')
    require(all(config.get(k, 'n') == v for k, v in expected_config.items()), 'Audit config differs from build')
    for table in ('protected', 'exported'):
        header = (root / f'out/kernel/gki_module_{table}.h').read_text()
        array = re.search(r'gki_' + table + r'_symbols\[.*?=\s*\{(.*?)\};', header, re.S)
        require(array is not None and not array.group(1).strip(),
                f'Current generated GKI {table} table is not empty')

    candidate = reports['tests/susfs_namei_candidate.json']
    legacy = reports['tests/susfs_40900_preserved.json']
    check_count(candidate, 215, 'SUSFS pathname/stat')
    check_count(legacy, 63, 'SUSFS retained fixes')
    for report in (candidate, legacy):
        require(Path(report['source']).resolve() == root / 'sources/common', 'SUSFS test targets another tree')
    check_source_hashes(root / 'sources/common', candidate.get('sha256'))
    susfs_hashes = reports['tests/susfs_source_hashes.json']
    check_source_hashes(root / 'sources/common', {k: v['after'] for k, v in susfs_hashes.items()})
    security = reports['tests/suki_security_results.json']
    require(security.get('status') == 'PASS', 'SukiSU host security regression did not pass')
    check_count(security, 59, 'SukiSU security')
    check_source_hashes(root / 'sources/SukiSU-Ultra', security.get('source_sha256'))
    match(root / 'tests/suki_security_test.py', security.get('test_sha256'))
    require(security.get('official_apk_sha256') == OFFICIAL_SHA, 'Security tests used another APK')
    cli = read(root / 'userspace/manifest.json')
    require(cli.get('status') == 'ANDROID_ARM64_COMPILED_HOST_PAYLOAD_TESTS_PASS_NOT_DEVICE_TESTED' and
            cli.get('upstream_commit') == SUSFS_COMMIT and cli.get('abi') == 'arm64-v8a', 'CLI identity differs')
    match(root / 'userspace/bin/ksu_susfs', cli.get('binary_sha256'))
    match(root / 'userspace/ctime-flag.patch', cli.get('patch_sha256'))
    ctime = reports['logs/userspace_ctime_test.json']
    require(ctime.get('status') == 'PASS' and ctime.get('tests') == 9 and cli.get('feature_tests') == ctime,
            'CLI payload test did not pass or manifest differs')
    match(root / 'userspace/upstream-susfs/ksu_susfs/jni/features/sus_kstat.c', ctime.get('source_sha256'))
    wire = reports['logs/userspace_wire_test.json']
    require(wire.get('status') == 'PASS' and wire.get('sizeof_matches') is True and
            wire.get('architecture') == 'AArch64 LP64' and wire.get('field_offsets', 0) > 0 and
            wire.get('flags', 0) > 0 and cli.get('wire_abi_verification') == wire, 'CLI wire ABI test differs')
    check_source_hashes(root, wire.get('source_sha256'))
    ak3 = reports['logs/anykernel_host_test.json']
    require(ak3.get('status') == 'PASS' and ak3.get('tests') == 20 and len(ak3.get('results', [])) == 20,
            'AnyKernel function host test did not pass')
    match(root / 'sources/AnyKernel3/anykernel.sh', ak3.get('anykernel_sha256'))

    reconstruction = reports['logs/source_reconstruction_check.json']
    require(reconstruction.get('status') == 'PASS_NETWORK_RECONSTRUCTION_AND_BUILD' and
            reconstruction.get('source_trees_match') is True and
            reconstruction.get('source_lock_sha256') == sha(lock_path), 'Network source reconstruction incomplete')
    network = reconstruction['network_reconstruction']
    require(network.get('status') == 'PASS' and network.get('local_mirror_used') is False and
            network.get('source_inputs') == sources, 'Network reconstruction source inputs differ')
    require(network.get('remote_urls') == {k: v['url'] for k, v in lock['repositories'].items()},
            'Network source URLs differ from lock')
    rebuilt = reconstruction['build']
    require(rebuilt.get('status') == 'PASS_OFFLINE_BUILD_CHECKS' and rebuilt.get('clean_output_directory') is True,
            'Reconstructed tree clean build did not pass')
    match(root / 'logs/build_completion.json', rebuilt.get('build_completion_sha256'))
    match(root / 'logs/build_verification.json', rebuilt.get('build_verification_sha256'))
    match(root / 'dist/Image', rebuilt.get('kernel_sha256'))
    # This text is a current log, not an inherited assertion of success.
    test_log = (root / 'logs/packaging_final_tests.txt').read_text()
    require(re.search(r'^OK\s*$', test_log, re.M) and not re.search(r'^(FAILED|ERROR:)\b', test_log, re.M),
            'Packaging host test log lacks a passing unittest result')

    versions = {name: read(research / name) for name in VERSION_EVIDENCE}
    inspected = versions['apk-inspection.json']
    require(inspected.get('sha256') == OFFICIAL_SHA and
            inspected['manifest_attributes']['versionCode'] == 40900 and
            inspected['manifest_attributes']['package'] == 'com.sukisu.ultra', 'Official Manager identity differs')
    verified = versions['version-verification.json']
    require(verified.get('apk_digest_matches_api') is True and
            verified['latest_apk_asset']['digest'] == 'sha256:' + OFFICIAL_SHA, 'Official release digest differs')
    signature = versions['apk-signature-verification.json']
    signers = signature.get('v2_signers', [])
    require(signature.get('sha256') == OFFICIAL_SHA and signers and
            all(s.get(k) == 'PASS' for s in signers for k in
                ('signed_data_signature', 'protected_content_digest', 'kernel_manager_certificate_match')),
            'Official APK signature checks incomplete')
    match(research / OFFICIAL_APK, OFFICIAL_SHA)
    for name, expected in ORIGINAL_HASHES.items():
        match(original / name, expected)
    previous = reports['logs/previous40900_unchanged.json']
    require(previous.get('status') == 'PASS' and len(previous.get('files', {})) == 3,
            'Previous 40900 preservation evidence incomplete')
    check_source_hashes(root.parent / 'build_sukisu_20260930/dist', previous['files'])
    for name in EVIDENCE:
        require((root / name).is_file() and not (root / name).is_symlink(), f'Missing evidence: {name}')
    return lock, sources, reports, cli


def collect_tree(root, relative, allow_git=False):
    """Only named trees are traversed; symlinks are recorded, never followed."""
    start = root / relative
    require(start.exists(), f'Missing source-bundle input: {relative}')
    if start.is_file() or start.is_symlink():
        return [start]
    result = []
    for directory, dirs, files in os.walk(start, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d != '__pycache__' and (allow_git or d != '.git'))
        for name in list(dirs):
            path = Path(directory) / name
            if path.is_symlink():
                result.append(path)
                dirs.remove(name)
        for name in sorted(files):
            if name.endswith(('.pyc', '.pyo')) or name == 'core':
                continue
            if not allow_git and name == '.git':
                continue
            path = Path(directory) / name
            require(path.is_file() or path.is_symlink(), f'Unsupported source-bundle entry: {path}')
            result.append(path)
    return result


def small_inputs(root, lock):
    files = sorted(root.glob('*.py')) + sorted(root.glob('*.sh'))
    for name in ('source-lock.json', 'patches', 'added', 'licenses', 'docs', 'README.md', 'LICENSE',
                 '.gitignore', '.github', 'tests', 'userspace/README.txt', 'userspace/manifest.json',
                 'userspace/ctime-flag.patch', 'userspace/upstream-susfs/ksu_susfs/jni'):
        files.extend(collect_tree(root, name))
    for repo, spec in lock['repositories'].items():
        match(relative_file(root, spec['patch_file']), spec['patch_sha256'])
        for relative, expected in spec['untracked'].items():
            path = root / 'added' / repo / safe_relative(relative)
            if expected['kind'] == 'symlink':
                require(path.is_symlink() and os.readlink(path) == expected['target'], f'Added symlink differs: {path}')
            else:
                match(path, expected['sha256'])
                require(path.stat().st_mode & 0o777 == expected['mode'], f'Added file mode differs: {path}')
    return sorted(set(files))


def check_link(name, target):
    require(not target.startswith('/'), f'Absolute archive link: {name}')
    normalized = posixpath.normpath(posixpath.join(posixpath.dirname(name), target))
    require(normalized != '..' and not normalized.startswith('../'), f'Escaping archive link: {name}')
    require(normalized.split('/')[0] == name.split('/')[0], f'Link escapes its archive tree: {name}')


def git_inputs(root, repo, spec):
    repository = root / 'sources' / repo
    metadata = repository / '.git'
    require(metadata.is_dir() and not metadata.is_symlink(), f'Expected standalone Git directory: {repo}')
    allowed = {'core.repositoryformatversion': '0', 'core.filemode': 'true', 'core.bare': 'false',
               'core.logallrefupdates': 'true', 'core.autocrlf': 'false',
               'remote.origin.url': spec['url'], 'remote.origin.fetch': '+refs/heads/*:refs/remotes/origin/*'}
    for entry in filter(None, git(repository, 'config', '--local', '--null', '--list').split('\0')):
        key, value = entry.split('\n', 1)
        require(key in allowed and allowed[key] == value, f'Unexpected local Git config key/value: {repo}/{key}')
    files = []
    for name in ('HEAD', 'index', 'shallow', 'packed-refs', 'objects', 'refs'):
        path = metadata / name
        if path.exists():
            # Git requires objects/ and refs/ even in a detached shallow checkout
            # whose refs tree has no files. Preserve these empty directories.
            if name in ('objects', 'refs'):
                require(path.is_dir() and not path.is_symlink(), f'Invalid Git directory: {repo}/{name}')
                files.append(path)
            files.extend(collect_tree(root, str(path.relative_to(root)), allow_git=True))
    require((metadata / 'HEAD').is_file() and (metadata / 'index').is_file(), f'Incomplete Git metadata: {repo}')
    require(not (metadata / 'objects/info/alternates').exists(), f'External Git object store: {repo}')
    require(all(not p.is_symlink() for p in files), f'Symlinked Git metadata: {repo}')
    config = ('[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = false\n'
              '\tlogallrefupdates = true\n\tautocrlf = false\n[remote "origin"]\n'
              f'\turl = {spec["url"]}\n\tfetch = +refs/heads/*:refs/remotes/origin/*\n')
    return files, config.encode()


def archive_files(path, root, files, epoch, generated=None):
    with tarfile.open(path, 'w:gz', compresslevel=6, dereference=False) as archive:
        for source in sorted(set(files)):
            name = source.relative_to(root).as_posix()
            info = archive.gettarinfo(str(source), arcname=name)
            require(info.isfile() or info.isdir() or info.issym() or info.islnk(), f'Unsupported archive member: {name}')
            if info.issym():
                check_link(name, info.linkname)
            if info.islnk():
                safe_relative(info.linkname)
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            info.mtime = epoch
            if info.isfile():
                with source.open('rb') as stream:
                    archive.addfile(info, stream)
            else:
                archive.addfile(info)
        for name, data in sorted((generated or {}).items()):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.mtime = len(data), 0o644, epoch
            archive.addfile(info, io.BytesIO(data))


def verify_offline_archive(archive_path, lock, sources):
    with tempfile.TemporaryDirectory(prefix='delivery-offline-check-', dir=archive_path.parent) as temp:
        target = Path(temp)
        with tarfile.open(archive_path, 'r:gz') as archive:
            members = archive.getmembers()
            seen = set()
            symlinks = set()
            for item in members:
                safe_relative(item.name)
                require(item.name not in seen, f'Duplicate archive member: {item.name}')
                seen.add(item.name)
                require(item.isfile() or item.isdir() or item.issym() or item.islnk(),
                        f'Special archive entry: {item.name}')
                require(not item.mode & 0o7000, f'Privileged archive mode: {item.name}')
                if item.issym():
                    check_link(item.name, item.linkname)
                    symlinks.add(item.name)
                if item.islnk():
                    safe_relative(item.linkname)
            for item in members:
                parents = {str(p) for p in PurePosixPath(item.name).parents}
                require(not parents & symlinks, f'Archive writes through symlink: {item.name}')
                if item.islnk():
                    require(item.linkname in seen and item.linkname not in symlinks,
                            f'Invalid archive hardlink: {item.name}')
            # All entries were checked above. Preserve 0664 added-file modes:
            # Python's data_filter would otherwise strip group write bits.
            archive.extractall(target, members=members, filter='fully_trusted')
        require(load_lock(target / 'source-lock.json') == lock, 'Archived lock differs')
        require(verify_sources(target, lock) == sources, 'Offline archive reconstruction differs')
    return {'status': 'PASS_EXTRACTED_SOURCE_LOCK_CHECK', 'network_used': False,
            'kernel_rebuilt_from_archive': False,
            'scope': 'Fresh extraction; Git identities, patches, untracked hashes and modes verified'}


def finalize(root, research, original):
    dist = root / 'dist'
    require(dist.is_dir() and not dist.is_symlink(), 'Missing regular dist directory')
    require(all(not path.is_symlink() for path in dist.rglob('*')),
            'Refusing symlinked delivery files or directories')
    # Never retain a prior successful delivery attestation after a failed rerun.
    (dist / 'manifest.json').unlink(missing_ok=True)
    (dist / 'SHA256SUMS').unlink(missing_ok=True)
    lock, sources, reports, cli = verify_inputs(root, research, original)
    small = small_inputs(root, lock)
    full = list(small)
    generated = {}
    for repo, spec in lock['repositories'].items():
        full.extend(collect_tree(root, 'sources/' + repo))
        metadata, config = git_inputs(root, repo, spec)
        full.extend(metadata)
        generated[f'sources/{repo}/.git/config'] = config
    evidence_hashes = {name: sha(root / name) for name in EVIDENCE}
    with tempfile.TemporaryDirectory(prefix='.delivery-', dir=root) as temp:
        stage = Path(temp)
        def copy(source, relative):
            destination = stage / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            match(destination, sha(source))
        for name in EVIDENCE:
            copy(root / name, 'verification/' + name)
        for name in VERSION_EVIDENCE:
            copy(research / name, 'verification/versions/' + name)
        copy(research / OFFICIAL_APK, OFFICIAL_APK)
        for name in ('README.txt', 'manifest.json', 'ctime-flag.patch'):
            copy(root / 'userspace' / name, 'userspace/' + name)
        copy(root / 'userspace/bin/ksu_susfs', 'userspace/ksu_susfs')
        copy(root / 'docs/修復報告_20261013.txt', '修復報告_20261013.txt')
        copy(root / 'README.md', 'README.md')
        copy(root / 'LICENSE', 'LICENSE')
        copy(root / 'licenses/NOTICE.txt', 'LICENSE_NOTICE.txt')
        epoch = lock['build']['source_date_epoch']
        print('Creating the patch/source bundle...', flush=True)
        archive_files(stage / 'source_changes.tar.gz', root, small, epoch)
        print('Creating the complete source/Git bundle...', flush=True)
        archive_files(stage / 'full_source.tar.gz', root, full, epoch, generated)
        print('Extracting and verifying the complete source bundle offline...', flush=True)
        offline = verify_offline_archive(stage / 'full_source.tar.gz', lock, sources)
        atomic_json(stage / 'verification/offline_archive_check.json', offline)
        require(verify_sources(root, lock) == sources, 'Sources changed while archiving')
        for name, expected in evidence_hashes.items():
            match(root / name, expected)
        # Recheck completed build and payloads immediately before publishing.
        verify_inputs(root, research, original)
        package = reports['logs/packaging_verification.json']
        manifest = {
            'schema_version': 1, 'status': 'OFFLINE_VERIFIED_CANDIDATE',
            'device': 'Sony Xperia 1 V XQ-DQ72 / pdx234', 'firmware': '67.2.A.3.178', 'android': 15,
            'runtime': {'candidate': 'NOT_DEVICE_TESTED', 'flash': 'NOT_FLASHED',
                        'installer': 'AK3_NOT_RUN', 'boot': 'NOT_RUN', 'module_loading': 'NOT_RUN',
                        'applications': 'APPLICATION_FIXES_NOT_CLAIMED'},
            'phone_modified': False, 'application_fixes_claimed': False,
            'kernel_release': reports['logs/build_verification.json']['kernel_release'],
            'source_lock_sha256': sha(root / 'source-lock.json'), 'source_inputs': sources,
            'manager': {'version_code': 40900, 'uapi': 2, 'official_unchanged_apk': OFFICIAL_APK,
                        'sha256': OFFICIAL_SHA, 'source_commit': lock['repositories']['SukiSU-Ultra']['paired_commit']},
            'userspace_cli': cli,
            'build': reports['logs/build_verification.json'], 'packaging': package,
            'vendor_module_abi': reports['logs/vendor_audit/summary.json'],
            'module_protection': reports['logs/vendor_audit/protection.json'],
            'module_vermagic': reports['logs/vendor_audit/vermagic_check.json'],
            'host_tests': {'susfs_namei': 215, 'susfs_preserved': 63, 'sukisu_security': 59,
                           'cli_payload': 9, 'anykernel_functions': 20,
                           'cli_wire_abi': reports['logs/userspace_wire_test.json'],
                           'scope': 'Host/static evidence; not candidate device or installer execution'},
            'source_reconstruction': reports['logs/source_reconstruction_check.json'],
            'source_bundles': {
                'source_changes.tar.gz': {'sha256': sha(stage / 'source_changes.tar.gz'),
                    'contents': 'Locked patches, added sources, scripts, tests, CLI source and licenses',
                    'base_sources': 'Requires exact upstream bases from network or a local source cache'},
                'full_source.tar.gz': {'sha256': sha(stage / 'full_source.tar.gz'),
                    'contents': 'Same materials plus all three source trees and essential sanitized Git metadata',
                    'offline_verification': offline,
                    'excluded': ['toolchains', 'build outputs', 'private diagnostics', 'Git reflogs and hooks'],
                    'external_requirements': 'Pinned compiler/host tools and matching original firmware still required'},
            },
            'original_40922_unchanged_sha256': ORIGINAL_HASHES,
            'evidence_sha256': evidence_hashes,
            'license_distribution_status': 'UNRESOLVED_NO_PUBLIC_DISTRIBUTION_CLEARANCE',
            'license_note': 'Pinned SUSFS GPL-3.0 and Linux GPL-2.0-only binary redistribution compatibility remains unresolved; notices do not resolve it.',
        }
        (stage / 'README_繁體中文.txt').write_text(
            'Xperia 1 V / XQ-DQ72 — SukiSU 官方 40900 / UAPI 2 修復候選（2026-10-13）\n\n'
            '適用目標：Android 15、韌體 67.2.A.3.178。已完成的離線建置、封裝及主機測試見 manifest.json。\n'
            '本候選尚未刷入、尚未開機或驗證模組載入，AnyKernel3 安裝器未實際執行；不宣稱已修復任何 App。\n\n'
            f'boot 映像：{package["boot_path"].removeprefix("dist/")}\n'
            f'安裝 ZIP：{package["anykernel_path"].removeprefix("dist/")}\n'
            '兩者是替代方式，目標為 boot，不是 init_boot。安裝前提、備份、還原、OTA 與 init_boot 狀態請讀 README.md。\n'
            'Manager APK 為未修改官方 v4.2.0 / 40900。userspace/ksu_susfs 為本次匹配 bit 8 的 ARM64 CLI；詳見其 README。\n\n'
            'source_changes.tar.gz 是小型重建材料，需另取得鎖定 Git 基底。full_source.tar.gz 包含三棵完整來源與\n'
            '必要 Git 資料，可離線核對 source-lock；本程式已解壓到暫存目錄完成來源驗證，沒有在該解壓目錄重新編譯。\n'
            '完整包仍不含編譯器、主機工具或 Sony 原廠韌體；請依 README.md 準備。\n'
            '核驗結果見 verification/，逐項修復與保留限制見 修復報告_20261013.txt。SHA256SUMS 涵蓋全部交付檔。\n\n'
            'SUSFS GPL-3.0 與 Linux GPL-2.0-only 整合的二進位公開散布授權問題尚未解決；本地成功建置不代表可公開散布。\n'
            '原可用 40922 的三個交付檔已重新核對雜湊，未被本次流程改動。\n', encoding='utf-8')
        # Only the current package and explicitly selected delivery material belong here.
        atomic_json(stage / 'manifest.json', manifest)
        staged = {p.relative_to(stage).as_posix(): p for p in stage.rglob('*') if p.is_file()}
        package_dir = Path(package['boot_path']).parent.relative_to('dist').as_posix()
        package_names = {Path(package['boot_path']).name, Path(package['anykernel_path']).name,
                         'packaging_verification.json', 'stock_avb.txt', 'stock_avb_verify.txt',
                         'candidate_avb.txt', 'candidate_avb_verify.txt', 'anykernel_contents.txt'}
        allowed = set(staged) | {'Image', 'kernel.config', 'Module.symvers', 'kernel.release'} | {
            package_dir + '/' + name for name in package_names}
        current = {p.relative_to(dist).as_posix(): p for p in dist.rglob('*') if p.is_file()}
        require(set(current) <= allowed, f'Unexpected delivery files: {sorted(set(current) - allowed)}')
        final_files = current | staged
        (stage / 'SHA256SUMS').write_text(''.join(f'{sha(path)}  {name}\n'
                                                for name, path in sorted(final_files.items())))
        # Manifest is installed last; no successful attestation describes a partial stage.
        for path in sorted(stage.rglob('*')):
            if path.is_file() and path != stage / 'manifest.json':
                destination = dist / path.relative_to(stage)
                require(not destination.is_symlink(), f'Refusing symlink destination: {destination}')
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(path, destination)
        os.replace(stage / 'manifest.json', dist / 'manifest.json')
    print(json.dumps({'status': manifest['status'], 'runtime': manifest['runtime'],
                      'source_lock_sha256': manifest['source_lock_sha256'], 'files': len(final_files)}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--research', type=Path, help='Directory containing the three approved version JSONs and official APK')
    parser.add_argument('--original-dist', type=Path, help='Original working 40922 dist directory (read only)')
    args = parser.parse_args()
    root = args.root.resolve()
    research = (args.research or root.parent / 'research_versions_20261013').resolve()
    original = (args.original_dist or root.parent / 'build_sukisu_20260920/dist').resolve()
    finalize(root, research, original)


if __name__ == '__main__':
    sys.exit(cli_error(main))
