SUSFS 2.3 — corrected Android arm64 CLI (2026-10-13)

Source: https://gitlab.com/simonpunk/susfs4ksu
Commit: 7af04b08f86a5f811cbea28805f96d52368e005f
Only source change: ctime-flag.patch changes (1 < 8) to (1 << 8).
The kernel and official SukiSU 40900 ksud already use bit 8 (256).
This is the complete upstream ksu_susfs command, not a reduced replacement.

Binary: arm64-v8a, Android API 35 (this phone's Android 15).
The local build uses bin/ksu_susfs; the finalized delivery contains
userspace/ksu_susfs. A source-only Git checkout does not include the binary.
NDK: r27d / 27.3.13750724; official download/checksum in manifest.json.
The included upstream-susfs/ksu_susfs/jni tree already has the fix. From this
userspace directory, build it with:
  cd upstream-susfs/ksu_susfs
  /path/to/android-ndk-r27d/ndk-build APP_PLATFORM=android-35 -j4
The result is libs/arm64-v8a/ksu_susfs. If obtaining a fresh upstream source
archive instead, first apply ctime-flag.patch with patch -p1 at its root.
Use unzip for NDK extraction, preserving its symlinks and executable modes.

Nine host cases call the actual CLI C parser and inspect a mocked syscall's
payload, including ctime seconds/nanoseconds, INO and automatic masks.
AArch64 static assertions also verify 17 field offsets, structure size and
all 12 flags against the real kernel declaration. The Android executable
was compiled and inspected, not run on the phone.

Installation is deliberately not automated. Preserve the existing tool and
module configuration before replacing any copy. A SUSFS module can carry its
own binary and reinstall/overwrite /data/adb/ksu/bin/ksu_susfs. Every executing
copy must agree with the bit-8 ABI; check the module's own scripts and paths.
No user module settings or phone files were changed during this build.

The source retains its upstream GPL-3.0 license; see ../licenses/SUSFS_LICENSE.
