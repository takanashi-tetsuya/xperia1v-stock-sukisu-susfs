# Xperia 1 V stock kernel — SukiSU / SUSFS

Release preparation for XQ-DQ72 (pdx234), Sony stock Android 15 build
67.2.A.3.178, security patch 2026-06-01. Initial release status: Beta.
Other variants and ROM builds are untested. This is an unofficial integration,
not an official Sony, SukiSU or SUSFS release.

Kernel: 5.15.189-android13-8-sukisu40922. SukiSU UAPI 2, SUSFS 2.3,
inline hooks, Full LTO and CFI. BBR is available; CUBIC remains the default.
KPM is disabled. The android13 kernel branch does not mean the ROM is Android 13.

## Test evidence

The device owner reports normal device functionality and ReZygisk 1.0.0 enabled
and working. Zygisk Next installation was confirmed, but its exact version and
separate runtime result are not recorded unambiguously. Shamiko was not tested.
No runtime logs were independently collected. See manifest.json for historical
offline verification and the separate owner report. Do not infer compatibility
with all modules, all banking apps or Play Integrity from these results.

## Rebuilding

Use Linux x86-64 with Git, make, Python 3, a host C/C++ compiler, flex, bison,
bc, OpenSSL development headers, libelf development headers and pahole 1.31.
Install host dependencies using your distribution's package manager.

1. For a Git checkout, run `bash prepare-sources.sh`. The complete-source release
   archive already includes prepared sources and Git history; skip this step there. It checks out exact upstream commits and applies
   all local patches. Do not replace pinned SukiSU main history with current main:
   its 3737 commits determine the genuine version code 40000 + 3737 - 2815 = 40922.
2. Obtain Android Clang r450784e from the pinned Google source below. Extract into
   `toolchains/clang-r450784e`, or set TOOLCHAIN_DIR to its extracted directory.
3. Run `PAHOLE=/path/to/pahole BUILD_JOBS=12 bash build.sh`.
   Outputs appear in dist/. Full LTO requires substantial RAM and disk space.
4. To package: set STOCK_BOOT to your original matching boot image and AVBTOOL
   to avbtool 1.3.0, then run `python3 package.py`. Stock boot SHA-256 must be
   06dbe42eccb4ea14a53e7b34d1f509bc7b176776db89d3280adba44c4b8b3731.

Clang archive:
https://android.googlesource.com/platform/prebuilts/clang/host/linux-x86/+archive/7c3dd2ed6a61f781016dc231e6589118a61aa59f/clang-r450784e.tar.gz

Archive SHA-256: 521cba0af6586658c2476f35782bc619d5886eb6f7908bfdd59bc4929ed1d095

A clean rebuild is not guaranteed byte-identical (for example, generated module
signing keys). The release binaries are the original device-tested build;
source reconstruction is checked separately. The portable scripts have not yet
undergone a full clean compile on another machine.

## Installation and recovery

Use an unlocked bootloader and the exact supported firmware. Save your original
boot image and verify that fastboot recovery access works. The kernel belongs in
boot, not init_boot. The AnyKernel3 ZIP and boot image are alternative installation
forms; the ZIP is not a normal KernelSU module. Use an AnyKernel3-capable installer.
Do not describe a particular installer as tested without recording its version.

For fastboot, first check `fastboot getvar current-slot` and identify the active
slot. Flash the release boot image only to that slot's boot partition (boot_a or
boot_b as appropriate), then reboot. To roll back, flash your original matching
boot to the same partition. Do not relock the bootloader with this custom image.
The custom AVB footer uses algorithm NONE; it is not Sony-signed.

Use the paired official SukiSU Manager 4.2.0 (40900), not an arbitrary main/CI build.
Use only one Zygisk implementation at a time. Record exact module versions when
reporting issues. Attach relevant logs with personal identifiers removed.

## Source and credits

Linux/Android Common Kernel: https://android.googlesource.com/kernel/common
KernelSU: https://github.com/tiann/KernelSU
SukiSU Ultra: https://github.com/SukiSU-Ultra/SukiSU-Ultra
SUSFS source commit: 7af04b08f86a5f811cbea28805f96d52368e005f
AnyKernel3: https://github.com/osm0sis/AnyKernel3

Retain upstream copyright and licensing notices. COPYING and licenses/ contain
license material; each upstream file retains its own applicable license.
All final local changes and added files are included. A full reconstructed source
archive should accompany the first release so users can inspect it directly.

This is repository documentation, not an XDA forum post.
