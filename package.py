#!/usr/bin/env python3
"""Package the local candidate without modifying any phone or stock image."""
from pathlib import Path
import os
import hashlib
import json
import shutil
import struct
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
STOCK = Path(os.environ["STOCK_BOOT"]).resolve()
AVBTOOL = Path(os.environ["AVBTOOL"]).resolve()
PARTITION_SIZE = 100663296
EXPECTED_STOCK_SHA = "06dbe42eccb4ea14a53e7b34d1f509bc7b176776db89d3280adba44c4b8b3731"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def run(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT)


def main():
    original = STOCK.read_bytes()
    assert digest(original) == EXPECTED_STOCK_SHA, "Unexpected stock firmware"
    assert len(original) == PARTITION_SIZE
    assert original[:8] == b"ANDROID!"
    assert struct.unpack_from("<I", original, 40)[0] == 4
    assert struct.unpack_from("<I", original, 12)[0] == 0
    assert struct.unpack_from("<I", original, 1580)[0] == 0
    kernel = (DIST / "Image").read_bytes()
    assert kernel[56:60] == b"ARMd", "Not an uncompressed ARM64 Image"
    assert len(kernel) > 10000000
    header = bytearray(original[:4096])
    struct.pack_into("<I", header, 8, len(kernel))
    raw = bytes(header) + kernel
    raw += bytes((-len(raw)) % 4096)
    boot = DIST / "boot_XQ-DQ72_67.2.A.3.178_sukisu40922.img"
    boot.write_bytes(raw)
    # A custom kernel cannot retain Sony's signature. Produce an explicitly
    # unsigned AVB footer with a fresh valid hash; do not change vbmeta flags.
    run("python3", str(AVBTOOL), "add_hash_footer", "--image", str(boot),
        "--partition_size", str(PARTITION_SIZE), "--partition_name", "boot",
        "--algorithm", "NONE", "--hash_algorithm", "sha256",
        "--salt", "aee087a5be3b982978c923f566a94613496b417f2af592639bc80d141e34dfe7",
        "--rollback_index", "1780272000",
        "--prop", "com.android.build.boot.os_version:13",
        "--prop", "com.android.build.boot.security_patch:2026-06-01",
        "--prop", "com.android.build.boot.fingerprint:Sony/pdx234/pdx234:13/TKQ1.221114.001/YODO-1.2.0-REL-260518-0520:user/release-keys")
    (ROOT / "logs/candidate_avb.txt").write_text(
        run("python3", str(AVBTOOL), "info_image", "--image", str(boot)))
    # avbtool resolves a hash descriptor's partition name as boot.img.
    with tempfile.TemporaryDirectory(prefix="avb-check-", dir=ROOT) as check_dir:
        check_image = Path(check_dir) / "boot.img"
        check_image.symlink_to(boot)
        (ROOT / "logs/candidate_avb_verify.txt").write_text(
            run("python3", str(AVBTOOL), "verify_image", "--image", str(check_image)))

    package = DIST / "AnyKernel3_Xperia1V_SukiSU40922_SUSFS2.3_20260920.zip"
    ak3 = ROOT / "sources/AnyKernel3"
    paths = [ak3 / "anykernel.sh", ak3 / "LICENSE"]
    for directory in ("META-INF", "tools"):
        paths.extend(p for p in (ak3 / directory).rglob("*") if p.is_file())
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as out:
        for path in sorted(paths):
            assert not path.is_symlink(), f"Unexpected package symlink: {path}"
            info = zipfile.ZipInfo(str(path.relative_to(ak3)), (2026, 9, 20, 0, 0, 0))
            info.create_system = 3
            info.external_attr = path.stat().st_mode << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            out.writestr(info, path.read_bytes())
        info = zipfile.ZipInfo("Image", (2026, 9, 20, 0, 0, 0))
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        out.writestr(info, kernel)
    with zipfile.ZipFile(package) as archived:
        assert archived.testzip() is None
        assert archived.read("Image") == kernel
        assert archived.namelist().count("Image") == 1
        assert not any(n.startswith(("modules/", "ramdisk/", ".git/")) for n in archived.namelist())
        assert b"NO_MAGISK_CHECK=1" in archived.read("anykernel.sh")
        assert b"BLOCK=boot;" in archived.read("anykernel.sh")
        (ROOT / "logs/anykernel_contents.txt").write_text("\n".join(archived.namelist()) + "\n")

    packed = boot.read_bytes()
    assert len(packed) == PARTITION_SIZE
    assert packed[4096:4096 + len(kernel)] == kernel
    checked_header = bytearray(packed[:4096])
    struct.pack_into("<I", checked_header, 8, struct.unpack_from("<I", original, 8)[0])
    assert checked_header == original[:4096]
    assert digest(STOCK.read_bytes()) == EXPECTED_STOCK_SHA
    report = {
        "kernel_sha256": digest(kernel),
        "boot_sha256": digest(packed),
        "anykernel_sha256": digest(package.read_bytes()),
        "stock_boot_unchanged": True,
        "boot_header_version": 4,
        "boot_partition_size": PARTITION_SIZE,
        "boot_ramdisk_size": 0,
        "avb_algorithm": "NONE (unsigned custom image)",
        "avb_hash_check": "PASS",
        "boot_header_other_fields_match_stock": True,
        "boot_and_zip_kernel_payloads_match_Image": True,
        "phone_boot_and_installer_execution": "NOT_RUN",
    }
    (ROOT / "logs/packaging_verification.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
