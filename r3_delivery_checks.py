"""Candidate maintainer checks for r3. No device access or runtime result inference."""
import json
from pathlib import Path
import re

from pipeline_common import load_lock, require, sha, verify_sources

R2_LOCK_SHA = 'e35a60b113bccfeb1320a2ddc5923dbca23ddacbeebc5e04abf57e1291382d67'
TARGET = 'arch/arm64/configs/gki_defconfig'
RUNTIME_TESTS = ('flash', 'boot', 'installer', 'module_loading', 'ksu', 'ctime',
                 'sus_path', 'zygisk', 'ota', 'wifi', 'camera', 'audio', 'sensors')
RUNTIME_STATES = {'PASS', 'FAIL', 'NOT_RUN', 'INCONCLUSIVE'}


def config_values(path):
    text = path.read_text()
    result = dict(re.findall(r'^(CONFIG_\w+)=(.*)$', text, re.M))
    result.update({name: 'n' for name in re.findall(r'^# (CONFIG_\w+) is not set$', text, re.M)})
    return result


def patch_without_defconfig(data):
    parts = re.split(r'(?=^diff --git )', data, flags=re.M)
    target = f'diff --git a/{TARGET} b/{TARGET}\n'
    require(sum(part.startswith(target) for part in parts) == 1,
            'Expected exactly one defconfig patch section')
    return ''.join(part for part in parts if not part.startswith(target))


def calculate_r3_delta(root, previous_r2_root):
    """Recompute the source/config delta from locked, read-only r2 and r3 trees."""
    parent = Path(previous_r2_root)
    require(parent.is_dir() and not parent.is_symlink(), 'Missing regular previous r2 root')
    require(parent.resolve() != root.resolve(), 'Previous r2 root must be a separate tree')
    require(sha(parent / 'source-lock.json') == R2_LOCK_SHA, 'Previous r2 source lock differs')
    old = load_lock(parent / 'source-lock.json')
    new = load_lock(root / 'source-lock.json')
    old_sources = verify_sources(parent, old)
    new_sources = verify_sources(root, new)
    for repo in ('SukiSU-Ultra', 'AnyKernel3'):
        require(old['repositories'][repo] == new['repositories'][repo], f'{repo} lock changed')
    a = dict(old['repositories']['common'])
    b = dict(new['repositories']['common'])
    a.pop('patch_sha256')
    b.pop('patch_sha256')
    require(a == b, 'Common base, URL or untracked sources changed')
    require(old['stock'] == new['stock'] and old['toolchain'] == new['toolchain'],
            'Stock/toolchain identity changed')
    for key in ('kernel_release', 'ksu_version', 'ksu_uapi', 'ksu_full_version'):
        require(old['build'][key] == new['build'][key], f'Build identity changed: {key}')
    old_patch = (parent / old['repositories']['common']['patch_file']).read_text()
    new_patch = (root / new['repositories']['common']['patch_file']).read_text()
    require(patch_without_defconfig(old_patch) == patch_without_defconfig(new_patch),
            'Non-defconfig kernel source patch changed')
    old_def = parent / 'sources/common' / TARGET
    new_def = root / 'sources/common' / TARGET
    old_text, new_text = old_def.read_text(), new_def.read_text()
    before, after = '# CONFIG_IKCONFIG_PROC is not set\n', 'CONFIG_IKCONFIG_PROC=y\n'
    require(old_text.count(before) == 1 and old_text.replace(before, after) == new_text,
            'Defconfig contains more than the reviewed IKCONFIG_PROC change')
    old_config, new_config = parent / 'dist/kernel.config', root / 'out/.config'
    old_values, new_values = config_values(old_config), config_values(new_config)
    delta = {k: {'r2': old_values.get(k, 'n'), 'r3': new_values.get(k, 'n')}
             for k in sorted(old_values.keys() | new_values.keys())
             if old_values.get(k, 'n') != new_values.get(k, 'n')}
    expected = {'CONFIG_IKCONFIG_PROC': {'r2': 'n', 'r3': 'y'}}
    require(delta == expected, 'Built configuration differs beyond IKCONFIG_PROC')
    require(all(new_values.get(k) == 'y' for k in
                ('CONFIG_IKCONFIG', 'CONFIG_IKCONFIG_PROC', 'CONFIG_VMAP_STACK')),
            'Android config interface prerequisites missing')
    return {
        'schema_version': 1, 'status': 'PASS_SINGLE_IKCONFIG_PROC_CHANGE',
        'source_lock_sha256': sha(root / 'source-lock.json'),
        'parent_source_lock_sha256': R2_LOCK_SHA,
        'parent_source_inputs': old_sources, 'source_inputs': new_sources,
        'changed_source': 'sources/common/' + TARGET,
        'parent_defconfig_sha256': sha(old_def), 'defconfig_sha256': sha(new_def),
        'parent_config_sha256': sha(old_config), 'config_sha256': sha(new_config),
        'config_delta': delta, 'kernel_release': new['build']['kernel_release'],
        'host_tool_changes_excluded_from_kernel_source_claim': True,
        'application_fixes_claimed': False, 'source_audit_device_access': 'NOT_USED',
    }


def verify_r3_delta(root, previous_r2_root, record, config_record):
    current = calculate_r3_delta(root, previous_r2_root)
    require(record == current, 'r3 source delta report differs from current trees/config')
    require(config_record.get('status') == 'PASS_SINGLE_CONFIG_CHANGE' and
            config_record.get('config_delta') == current['config_delta'] and
            config_record.get('source_lock_sha256') == current['source_lock_sha256'] and
            config_record.get('r2_config_sha256') == current['parent_config_sha256'] and
            config_record.get('r3_config_sha256') == current['config_sha256'],
            'r3 build config change evidence differs')


def verify_runtime_report(root, report, build, package, cli):
    """Check private-evidence summary schema/bindings, never synthesize device PASS.

    The maintainer must interpret the private logs before preparing this summary.
    Evidence digests identify retained private records; this function does not
    claim that those records or the device tests were replayed by the finalizer.
    Only fixed enum fields and SHA256s can enter this public object.
    """
    keys = {'schema_version', 'scope', 'source_lock_sha256', 'kernel_sha256',
            'boot_sha256', 'anykernel_sha256', 'cli_sha256', 'phone_modified',
            'application_fixes_claimed', 'tests', 'private_evidence_sha256'}
    require(isinstance(report, dict) and set(report) == keys, 'Unexpected runtime report fields')
    require(report['schema_version'] == 1 and
            report['scope'] == 'MAINTAINER_REVIEWED_PRIVATE_DEVICE_EVIDENCE',
            'Unexpected runtime report scope')
    expected = {'source_lock_sha256': sha(root / 'source-lock.json'),
                'kernel_sha256': build['kernel_sha256'], 'boot_sha256': package['boot_sha256'],
                'anykernel_sha256': package['anykernel_sha256'], 'cli_sha256': cli['binary_sha256']}
    require(all(report.get(k) == v for k, v in expected.items()), 'Runtime report artifact binding differs')
    require(type(report['phone_modified']) is bool and report['application_fixes_claimed'] is False,
            'Invalid runtime scope flags')
    tests, evidence = report['tests'], report['private_evidence_sha256']
    require(isinstance(tests, dict) and set(tests) == set(RUNTIME_TESTS) and
            all(type(v) is str and v in RUNTIME_STATES for v in tests.values()),
            'Runtime test keys or states differ')
    require(isinstance(evidence, dict) and set(evidence) == set(RUNTIME_TESTS),
            'Runtime evidence keys differ')
    for name in RUNTIME_TESTS:
        hashes = evidence[name]
        require(isinstance(hashes, list) and
                all(isinstance(h, str) and re.fullmatch('[0-9a-f]{64}', h) for h in hashes) and
                len(hashes) == len(set(hashes)),
                f'Invalid runtime evidence digest: {name}')
        require((tests[name] == 'NOT_RUN') == (not hashes),
                f'Executed runtime test needs evidence; NOT_RUN cannot have evidence: {name}')
    require(not any(v != 'NOT_RUN' for v in tests.values()) or report['phone_modified'],
            'Device test report incorrectly claims phone was not modified')
    return report


def verify_archive_derived_build(root, previous_r2_root, report, sources):
    """Separate honest offline derivation evidence from a fresh network rebuild."""
    require(report.get('status') == 'PASS_R2_ARCHIVE_DERIVED_R3_BUILD' and
            report.get('network_reconstruction') == 'NOT_RUN_FOR_R3' and
            report.get('source_lock_sha256') == sha(root / 'source-lock.json') and
            report.get('source_inputs') == sources,
            'Offline archive-derived reconstruction identity differs')
    parent = report.get('parent_archive', {})
    require(parent.get('source_lock_sha256') == R2_LOCK_SHA and
            parent.get('sha256') == sha(previous_r2_root / 'dist/full_source.tar.gz'),
            'Offline reconstruction parent archive differs')
    require(report.get('source_delta_sha256') == sha(root / 'logs/r3_source_delta.json'),
            'Offline derivation does not bind the reviewed source change')
    require(report.get('build', {}).get('clean_output_directory') is True and
            report.get('build', {}).get('status') == 'PASS_OFFLINE_BUILD_CHECKS',
            'Archive-derived r3 build was not clean/completed')
    required = ('logs/candidate_inputs.json', 'logs/source-check.log', 'logs/freeze.log')
    records = report.get('derivation_evidence_sha256', {})
    require(isinstance(records, dict) and set(records) == set(required),
            'Archive derivation evidence inventory differs')
    for name in required:
        path = root / name
        require(path.is_file() and not path.is_symlink() and sha(path) == records[name],
                f'Archive derivation evidence differs: {name}')
    inputs = json.loads((root / 'logs/candidate_inputs.json').read_text())
    require(inputs.get('base_archive_sha256') == parent['sha256'] and
            inputs.get('out_initially_absent') is True and
            inputs.get('core_source_files_changed') == ['sources/common/' + TARGET],
            'Contemporaneous candidate input record differs')
    # Build completion, verification and Image digests are checked by finalizer.
    # This is not advertised as a build from the final r3 release archive.
