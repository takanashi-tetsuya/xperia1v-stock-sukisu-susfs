#!/usr/bin/env python3
"""Package and verify a completed candidate, then atomically publish its directory."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import struct
import sys
import tempfile
import zipfile
from pipeline_common import atomic_json, cli_error, digest, load_lock, require, run, sha, verify_image, verify_sources
from verify_build import verify_completion


def parse_avb(info):
    """Parse fields needed to preserve the stock boot hash descriptor and properties."""
    def field(label, indent=''):
        matches = re.findall(r'^' + re.escape(indent + label) + r':\s*(.*?)\s*$', info, re.M)
        require(len(matches) == 1, f'Expected one AVB {label}, got {len(matches)}')
        return matches[0]
    require(info.count('    Hash descriptor:') == 1, 'Expected exactly one boot hash descriptor')
    descriptors = re.findall(r'^    (.*descriptor):$', info, re.M)
    require(descriptors == ['Hash descriptor'], f'Unsupported stock AVB descriptors: {descriptors}')
    properties = re.findall(r"^    Prop: (.*?) -> '(.*)'$", info, re.M)
    require(properties, 'Stock AVB build properties are missing')
    result = {
        'algorithm': field('Algorithm'),
        'rollback_index': int(field('Rollback Index')),
        'rollback_index_location': int(field('Rollback Index Location')),
        'flags': int(field('Flags')),
        'partition_name': field('Partition Name', '      '),
        'hash_algorithm': field('Hash Algorithm', '      '),
        'salt': field('Salt', '      '),
        'hash_flags': int(field('Flags', '      ')),
        'properties': [list(item) for item in properties],
    }
    require(result['partition_name'] == 'boot', 'Stock AVB partition must be boot')
    require(result['hash_algorithm'] == 'sha256', 'Stock AVB hash must be sha256')
    require(re.fullmatch(r'[0-9a-f]+', result['salt']) and len(result['salt']) % 2 == 0,
            'Invalid stock AVB salt')
    require(result['flags'] == 0 and result['hash_flags'] == 0,
            'Expected stock AVB verification and hash flags to be zero')
    return result


def verify_stock(original, spec):
    require(digest(original) == spec['sha256'], 'Unexpected stock firmware SHA256; use the locked stock boot image')
    require(len(original) == spec['size'] and len(original) >= 4096, 'Unexpected stock partition size')
    require(original[:8] == b'ANDROID!', 'Invalid stock Android boot magic')
    require(struct.unpack_from('<I', original, 40)[0] == spec['header_version'] == 4, 'Expected boot header v4')
    require(struct.unpack_from('<I', original, 20)[0] == 1584, 'Unexpected stock boot header size')
    require(struct.unpack_from('<I', original, 12)[0] == spec['ramdisk_size'] == 0, 'Expected an empty boot ramdisk')
    require(struct.unpack_from('<I', original, 1580)[0] == spec['signature_size'] == 0, 'Unexpected boot signature payload')
    kernel_size = struct.unpack_from('<I', original, 8)[0]
    require(0 < kernel_size < len(original) - 4096, 'Invalid stock kernel size')
    verify_image(original[4096:4096 + kernel_size])


def validate_build(root, lock_path, lock):
    sources = verify_sources(root, lock)
    completion = verify_completion(root, lock_path)
    verified = root / 'logs/build_verification.json'
    require(verified.is_file(), 'Build verification required; run verify_build.py')
    report = json.loads(verified.read_text())
    require(report.get('status') == 'PASS_OFFLINE_BUILD_CHECKS', 'Build verification did not pass')
    require(report.get('source_lock_sha256') == sha(lock_path), 'Verified source lock changed')
    require(report.get('source_inputs') == sources, 'Source inputs changed after verification')
    require(report.get('build_completion_sha256') == sha(root / 'logs/build_completion.json'),
            'Build completion changed after verification')
    require(report.get('artifact_sha256') == completion['artifact_sha256'], 'Verified build artifact list changed')
    require(report.get('kernel_sha256') == sha(root / 'dist/Image'), 'Verified Image changed')
    return report


def pack_zip(ak3, package, kernel):
    paths = [ak3 / 'anykernel.sh', ak3 / 'LICENSE']
    for directory in ('META-INF', 'tools'):
        require((ak3 / directory).is_dir() and not (ak3 / directory).is_symlink(), f'Missing AK3 directory: {directory}')
        for path in (ak3 / directory).rglob('*'):
            require(not path.is_symlink(), f'Unexpected package symlink: {path}')
            if path.is_file():
                paths.append(path)
    with zipfile.ZipFile(package, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as out:
        for path in sorted(paths):
            require(path.is_file() and not path.is_symlink(), f'Invalid package file: {path}')
            info = zipfile.ZipInfo(str(path.relative_to(ak3)), (2026, 10, 13, 0, 0, 0))
            info.create_system = 3
            info.external_attr = path.stat().st_mode << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            out.writestr(info, path.read_bytes())
        info = zipfile.ZipInfo('Image', (2026, 10, 13, 0, 0, 0))
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        out.writestr(info, kernel)
    with zipfile.ZipFile(package) as archived:
        require(archived.testzip() is None, 'ZIP CRC validation failed')
        require(archived.read('Image') == kernel, 'ZIP Image payload mismatch')
        names = archived.namelist()
        require(len(names) == len(set(names)) and names.count('Image') == 1, 'Duplicate ZIP entries')
        require(not any(n.startswith(('modules/', 'ramdisk/', '.git/')) for n in names), 'Forbidden ZIP payload')
        script = archived.read('anykernel.sh')
        require(b'NO_MAGISK_CHECK=1' in script and b'BLOCK=boot;' in script, 'Unexpected AK3 target configuration')
        return names


def package(root, lock_path, stock, avbtool):
    logs = root / 'logs'
    logs.mkdir(parents=True, exist_ok=True)
    (logs / 'packaging_verification.json').unlink(missing_ok=True)
    lock = load_lock(lock_path)
    original = stock.read_bytes()
    verify_stock(original, lock['stock'])
    validate_build(root, lock_path, lock)
    kernel = (root / 'dist/Image').read_bytes()
    verify_image(kernel)
    require(avbtool.is_file(), f'avbtool missing: {avbtool}; use --avbtool or AVBTOOL')
    version = lock['build']['ksu_version']
    boot_name = f'boot_XQ-DQ72_67.2.A.3.178_sukisu{version}.img'
    zip_name = f'AnyKernel3_Xperia1V_SukiSU{version}_SUSFS2.3_20261013.zip'
    packages = root / 'dist/packages'
    packages.mkdir(parents=True, exist_ok=True)
    # A single directory rename publishes a complete verified bundle. No final image
    # or ZIP exists before all checks have passed; exceptions clean the staging tree.
    with tempfile.TemporaryDirectory(prefix='.package-', dir=root / 'dist') as temporary:
        stage = Path(temporary)
        stock_info = run(sys.executable, avbtool, 'info_image', '--image', stock)
        metadata = parse_avb(stock_info)
        with tempfile.TemporaryDirectory(prefix='stock-avb-', dir=stage) as check:
            reference = Path(check) / 'boot.img'
            reference.symlink_to(stock.resolve())
            stock_verify = run(sys.executable, avbtool, 'verify_image', '--image', reference).replace(str(reference), 'stock/boot.img')
        header = bytearray(original[:4096])
        struct.pack_into('<I', header, 8, len(kernel))
        raw = bytes(header) + kernel
        raw += bytes((-len(raw)) % 4096)
        require(len(raw) < lock['stock']['size'], 'Image does not fit boot partition')
        boot = stage / boot_name
        boot.write_bytes(raw)
        command = [sys.executable, avbtool, 'add_hash_footer', '--image', boot,
                   '--partition_size', lock['stock']['size'], '--partition_name', metadata['partition_name'],
                   '--algorithm', 'NONE', '--hash_algorithm', metadata['hash_algorithm'],
                   '--salt', metadata['salt'], '--rollback_index', metadata['rollback_index'],
                   '--rollback_index_location', metadata['rollback_index_location'], '--flags', metadata['flags']]
        for key, value in metadata['properties']:
            command.extend(['--prop', f'{key}:{value}'])
        run(*command)
        candidate_info = run(sys.executable, avbtool, 'info_image', '--image', boot)
        candidate = parse_avb(candidate_info)
        require(candidate['algorithm'] == 'NONE', 'Custom boot must be explicitly unsigned')
        require({k: v for k, v in candidate.items() if k != 'algorithm'} ==
                {k: v for k, v in metadata.items() if k != 'algorithm'}, 'Candidate AVB metadata differs from stock')
        with tempfile.TemporaryDirectory(prefix='candidate-avb-', dir=stage) as check:
            reference = Path(check) / 'boot.img'
            reference.symlink_to(boot.resolve())
            avb_verify = run(sys.executable, avbtool, 'verify_image', '--image', reference).replace(str(reference), 'candidate/boot.img')
        package_file = stage / zip_name
        names = pack_zip(root / 'sources/AnyKernel3', package_file, kernel)
        packed = boot.read_bytes()
        require(len(packed) == lock['stock']['size'], 'Packaged boot partition size mismatch')
        require(packed[4096:4096 + len(kernel)] == kernel, 'Boot Image payload mismatch')
        checked_header = bytearray(packed[:4096])
        struct.pack_into('<I', checked_header, 8, struct.unpack_from('<I', original, 8)[0])
        require(checked_header == original[:4096], 'Boot header fields differ from stock')
        require(sha(stock) == lock['stock']['sha256'], 'Stock image changed during packaging')
        validate_build(root, lock_path, lock)
        identifier = digest(kernel)[:16] + '-' + sha(lock_path)[:12]
        destination = packages / identifier
        # Store evidence alongside the payload so its atomic publication is shared.
        report = {'status': 'PASS_OFFLINE_PACKAGING_CHECKS', 'kernel_sha256': digest(kernel),
                  'source_lock_sha256': sha(lock_path), 'boot_sha256': digest(packed),
                  'anykernel_sha256': sha(package_file),
                  'boot_path': str((destination / boot_name).relative_to(root)),
                  'anykernel_path': str((destination / zip_name).relative_to(root)),
                  'stock_boot_unchanged': True, 'stock_sha256': lock['stock']['sha256'],
                  'boot_header_version': 4, 'boot_partition_size': lock['stock']['size'],
                  'boot_ramdisk_size': 0, 'avb_algorithm': 'NONE (unsigned custom image)',
                  'stock_avb_metadata': metadata, 'candidate_avb_metadata': candidate,
                  'avb_hash_check': 'PASS', 'boot_header_other_fields_match_stock': True,
                  'boot_and_zip_kernel_payloads_match_Image': True,
                  'phone_boot_and_installer_execution': 'NOT_RUN'}
        atomic_json(stage / 'packaging_verification.json', report)
        for filename, value in (('stock_avb.txt', stock_info), ('stock_avb_verify.txt', stock_verify),
                                ('candidate_avb.txt', candidate_info), ('candidate_avb_verify.txt', avb_verify),
                                ('anykernel_contents.txt', '\n'.join(names) + '\n')):
            (stage / filename).write_text(value)
        if destination.exists():
            require(destination.is_dir() and not destination.is_symlink(), 'Existing bundle destination is invalid')
            existing_names = {p.name for p in destination.iterdir()}
            require(existing_names == {p.name for p in stage.iterdir()}, 'Existing bundle has unexpected files')
            require(all(p.is_file() and not p.is_symlink() and sha(p) == sha(stage / p.name)
                        for p in destination.iterdir()), 'Existing bundle differs; refusing to overwrite it')
        else:
            os.rename(stage, destination)
        atomic_json(logs / 'packaging_verification.json', report)
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument('--lock', type=Path)
    parser.add_argument('--stock-boot', type=Path, default=os.environ.get('STOCK_BOOT'),
                        help='Stock boot image (or STOCK_BOOT environment variable)')
    parser.add_argument('--avbtool', type=Path, default=os.environ.get('AVBTOOL'),
                        help='Path to the Python avbtool script (or AVBTOOL)')
    args = parser.parse_args()
    root = args.root.resolve()
    stock = args.stock_boot or root.parent / 'XQ-DQ72_init_boot/boot_X-FLASH-ALL-25B1.img'
    require(stock.is_file(), f'Stock boot image missing: {stock}; use --stock-boot or STOCK_BOOT')
    avbtool = (args.avbtool or root / 'host-tools/bin/avbtool').resolve()
    print(json.dumps(package(root, args.lock or root / 'source-lock.json', stock.resolve(), avbtool), indent=2))


if __name__ == '__main__':
    sys.exit(cli_error(main))
