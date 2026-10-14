# Xperia 1 V — SukiSU 40900 + SUSFS 2.3 repair candidate r3

This local candidate targets **Sony XQ-DQ72 / pdx234, firmware 67.2.A.3.178, Android 15**, with an unlocked bootloader. Build and packaging results are recorded in `dist/manifest.json`; do not interpret host tests as phone testing. No application compatibility improvement is promised.

The GitHub repository snapshot mirrors the candidate's `manifest.json` and `verification/` at its root. Upload completeness is recorded in [`RELEASE_STATUS.json`](https://github.com/takanashi-tetsuya/xperia1v-stock-sukisu-susfs/blob/main/RELEASE_STATUS.json). The r3 draft tag is `40900-repair-20261014-r3`; earlier builds are separate candidates. The release's external `runtime_manifest.json` records phone evidence bound to the exact Image/boot/ZIP hashes. This source archive describes the fixed code and host workflow; its contents are not updated after each phone test.

## Exact versions

As checked on 2026-10-13, the latest official [SukiSU release](https://github.com/SukiSU-Ultra/SukiSU-Ultra/releases/tag/v4.2.0) is **v4.2.0 / 40900**. There is no official 41000 release. Main at `223897c7` calculates 40971 and uses UAPI 5; this candidate retains the release's **UAPI 2**.

- Android common kernel: `51bba4309aac6b0889f1546c72865851cba20612`.
- SukiSU builtin release: `e2912817f4e1b194e582a06e0b5eacf6a3fb7083`.
- Paired official Manager/ksud source: `904c60d18f026340ce38ca2d5a1da529aadcba1a`.
- [SUSFS](https://gitlab.com/simonpunk/susfs4ksu): `7af04b08f86a5f811cbea28805f96d52368e005f`.
- [AnyKernel3](https://github.com/osm0sis/AnyKernel3): `020dfeccf9d7e962a48400fc94d3e451df92eead`.

The official APK is [SukiSU_v4.2.0_40900-release.apk](https://github.com/SukiSU-Ultra/SukiSU-Ultra/releases/download/v4.2.0/SukiSU_v4.2.0_40900-release.apk), SHA-256 `4ca9810e6355fbff0bbe4bf5ce159808e64a40d85987a11894030cd990a6bdf3`. The APK embeds ksud `4.2.0-1-g904c60d1`. The release tag is its immediate predecessor, `85eb4a95`; the intervening commit updates the fallback version string. Builtin and main have different histories. **40900 identifies the paired release; it does not claim this builtin tree includes every main commit.** Local backports remain explicitly identified by source-lock and patches.

## Changes

The candidate retains SUSFS inline/manual integration, CFI and Full LTO, and the earlier capability mask, elevation error, sulog length, inode-key and readlink fixes. It adds the upstream APK signing-block whitelist (#3700), applicable SELinux allocation checks (#3572) and policy serialization buffer fix (#3680). #3594 is already superseded by the existing bounded parser. The UAPI-changing scoped su descriptor feature (#3679) is not transplanted into an incompatible 40900 userspace; restricted profiles still have the older TTY/wrapper limitation.

Hidden inode lookup returns an error instead of substituting a `..5.u.S` sibling name. This intentionally makes creation/rename onto an already hidden inode fail; it must not create or modify a different sibling. Host regressions cover lookup and reference ownership; filesystem and phone runtime validation remain separate.

**r3 restores `CONFIG_IKCONFIG_PROC=y`.** r2 disabled `/proc/config.gz`, but this Sony Android 15 userspace reads it for kernel memory accounting. With root modules disabled, captured r2 logs showed five native `system_server` aborts after this file returned ENOENT. The stack and the Android 15 source identify `Debug.isVmapStack` as the failing check; see `docs/ikconfig_boot_failure_evidence.json` and the r3 repair report. Keeping embedded `CONFIG_IKCONFIG=y` alone does not provide the proc interface. The r3 build verifier now requires both settings. Do not disable the interface to hide kernel configuration.

SUSFS logging and ADB-root support remain disabled. The complete stock kernel release remains `5.15.189-android13-8-00016-g51bba4309aac-ab14546557`. The only kernel source/config change from r2 is restoring IKCONFIG_PROC; SukiSU/SUSFS/AK3 source inputs are unchanged. The build timestamp and epoch are new. The embedded SukiSU full-version label still contains `xperia-20261013`; r3 is identified by its new source lock and artifact hashes, without pretending the SukiSU source changed. No claim of undetectable root follows from these settings. KPM remains disabled.

The host build script also passes the verified pahole executable as a make command-line variable, preserving it in recursive make. This is a build-tool selection repair, separately tested with actual GNU make; it is not the cause of the Android boot failure.

r2 also removes workstation paths from newly exported evidence, preserves their digest references, and gives the host tests clear missing-input errors. Test reports use project-relative paths or named external inputs. The export receipt distinguishes raw local evidence hashes from public-copy hashes. Earlier Git history is retained and may still contain old workstation paths.

The kernel uses the correct ctime-seconds flag, bit 8. **Use the supplied corrected `ksu_susfs` tool when using this CLI**; the original 7af04b0 CLI has `(1 < 8)` and is incompatible for this field. SukiSU's paired ksud already uses bit 8. A module may ship and overwrite its own CLI: updating only `/data/adb/ksu/bin/ksu_susfs` does not update another module's private copy. Do not run multiple copies with different protocols. See `userspace/README.txt` for the exact correction and build recipe.

## Before installation

1. Verify the model, firmware, active slot, all delivered hashes, and ability to enter fastboot. Keep matching original boot and init_boot images and a tested recovery route available.
2. Obtain the **same** Sony firmware package (67.2.A.3.178, XQ-DQ72), extract/decrypt its boot SIN using the Sony firmware extraction tooling used for your package, and check the resulting original boot SHA-256: `06dbe42eccb4ea14a53e7b34d1f509bc7b176776db89d3280adba44c4b8b3731` (100663296 bytes). A differently named file is acceptable only if its bytes match. A firmware version label alone is insufficient. Original firmware is not redistributed here.
3. If init_boot still contains Magisk or KernelSU/SukiSU **LKM** root, restore the matching original init_boot before migrating to this built-in kernel. An already original init_boot requires no change. Preserve your backups; do not mix firmware images or install a second root implementation.
4. The image targets **boot**, not init_boot. The boot image and AK3 ZIP are alternative installation methods, not two required steps. Select the correct current slot deliberately; these scripts do not flash the phone.

AK3's system/vendor patch-level ranges both require June 2026 and its firmware guard requires 67.2.A.3.178. The installer must identify the running device and mount the real vendor partition. In a recovery that cannot provide these properties, it aborts intentionally. `NO_MAGISK_CHECK=1` skips AK3's Magisk-related handling; it is not proof that init_boot is unmodified. Host tests validate its range logic and ZIP payload; an actual AK3 installation is separately recorded as NOT_RUN until tested.

Keep the old working image and the original stock image for rollback. Restore a matching boot image to the affected slot if this candidate fails; restore init_boot too only when it was changed. Do not disable verified boot or change rollback counters as a workaround. Modified partitions can make incremental OTA fail; a successful OTA may replace the kernel and change vendor compatibility. Return to matching stock for the OTA procedure required by that firmware, then reassess and rebuild against the resulting firmware before using a custom kernel again.

## Rebuild

`source-lock.json`, `patches/` and `added/` identify the reviewed sources. Normal builds verify this lock. `freeze_sources.py` is a maintainer operation after review, **not** a way to silence an unexpected integrity failure.

Required Linux tools include git, Python 3, make, clang/LLVM, bison, flex, bc, OpenSSL/libelf development headers, cpio, zip/unzip and bpftool. Use Google's **clang 14.0.7 r450784e** at prebuilt tree `7c3dd2ed6a61f781016dc231e6589118a61aa59f`:

<https://android.googlesource.com/platform/prebuilts/clang/host/linux-x86/+/7c3dd2ed6a61f781016dc231e6589118a61aa59f/clang-r450784e/>

Gitiles archives can have changing tar/gzip metadata. Verify the selected extracted compiler files against `source-lock.json`, not the historical compressed archive SHA. Obtain pahole **1.31** from the [dwarves upstream](https://git.kernel.org/pub/scm/devel/pahole/pahole.git/) tag v1.31 (with its submodules), or a distribution's 1.31 package; provide its executable through `--pahole`. The local host-tools wrapper loads its accompanying extracted runtime libraries. `host-tools/bin` is prepended to PATH for this reason and for avbtool; it is not an implicit replacement compiler. Supply a working Python avbtool with `--avbtool` when packaging.

```sh
bash prepare-sources.sh
bash build.sh --clang-dir /path/to/clang-r450784e --pahole /path/to/pahole --jobs 12
python3 verify_build.py
python3 package.py --stock-boot /path/to/original-boot.img --avbtool /path/to/avbtool
```

Preparation stages downloads and changes in a temporary directory, then publishes a fully checked sources directory atomically. Identical existing sources are accepted; partial or modified existing sources are preserved and rejected. For an offline reconstruction use `--local-source-root /path/to/existing/sources`; that result must be labelled offline. Packaging publishes an atomic `dist/packages/<Image hash>-<source-lock hash>/` directory; `logs/packaging_verification.json` gives its exact paths. Python optimization must not disable checks.

The small `source_changes.tar.gz` requires obtaining its Git bases. The complete `full_source.tar.gz` also contains the working sources and the minimal Git objects, refs and shallow metadata needed to verify them offline. It excludes reflogs, hooks, tools, build output and device logs. After extracting the complete archive into a new directory, source preparation can validate the already present trees without a download; provide the compiler/pahole/avbtool paths as above. The manifest records the archive extraction and source-identity check separately from a full compile. Toolchains and the matching original boot image still need to be supplied.

Source reconstruction, kernel compilation, module CRC analysis, host regressions, phone boot, actual module loading and AK3 installation are different evidence categories. r3 was built from the verified r2 source archive after applying the reviewed configuration change, in a same-host clean container with networking disabled and an initially empty output directory. This is an archive-derived build, not a fresh network reconstruction or an independent third-party build. The final r3 source archive is separately extracted and source-verified; that extraction check is not another kernel compile. Unchanged-source host reports inherited from r2 are explicitly listed with their source/report hashes in `verification/logs/host_evidence_provenance.json`; they are not described as freshly rerun tests.

Read `runtime_manifest.json` for current phone results; an individual NOT_RUN stays untested. Host reports keep their original NOT_RUN runtime fields because those host tools did not operate the phone. GitHub Actions checks source reconstruction and host regressions; it does not compile the kernel. Module signature flags alone cannot establish failure: examine the generated protected-symbol tables. BTF acceptance, module loading, hardware functionality and SUSFS runtime behavior each require their own device evidence.

Rebuildable source does not imply a byte-identical Image: a clean kernel build generates a fresh module-signing key, which can change binary hashes. The fixed source lock, compiler checks, actual config, kernel release, Module.symvers and packaging metadata are verified separately. Generated private signing keys are not included in the source packages.

## Maintainer delivery export

`finalize_delivery.py` assembles this maintainer's release evidence after the ordinary build and package workflow. It is not required to rebuild or package the kernel. It additionally requires the approved release research records, the original preserved 40922 and 40900 products, and the corrected CLI binary built with the recipe in `userspace/README.txt`. Supply these inputs explicitly:

```sh
python3 finalize_delivery.py --root /path/to/checkout \
  --research /path/to/release-research \
  --original-dist /path/to/preserved-40922-dist \
  --previous-40900-dist /path/to/preserved-40900-dist \
  --cli-binary /path/to/ksu_susfs \
  --previous-r2-root /path/to/preserved-r2-checkout
```

The required evidence and its validation are defined in that script and `r3_delivery_checks.py`. The previous r2 root authenticates the single kernel configuration change. `export_publication.py` produces sanitized copies and recomputes references to exported evidence without modifying local input records. Historical input hashes and private-device evidence hashes are preserved and clearly scoped; raw device logs are excluded. Missing preservation or release inputs are errors, not permission to fabricate evidence. Generated `userspace/bin/` files are ignored by Git; the CLI source and correction remain tracked. Binary delivery stays in a draft Release while the licensing and device-validation limits below remain unresolved.

After upload, `verify_github_sync.py` independently reads the draft's complete asset list and checks names, sizes, SHA-256 digests, the exact source commit and its latest successful `verify.yml` run. The repository delivery snapshot supplies `RELEASE_ASSETS.json`; an extracted source-only bundle needs that separate expected-asset list. With authenticated `gh` access to the draft, run:

```sh
python3 verify_github_sync.py \
  --repo takanashi-tetsuya/xperia1v-stock-sukisu-susfs \
  --tag 40900-repair-20261014-r3 \
  --expected-assets-json RELEASE_ASSETS.json \
  --source-commit FULL_40_CHARACTER_COMMIT \
  --workflow verify.yml --output /path/to/github-sync-result.json
```

The output is a remote verification receipt, not evidence of phone testing. A missing GitHub asset digest triggers a download of that asset for hashing. Draft access is limited to authorized repository users.

## Licenses

Project-owned build/verification scripts are under the MIT license in `LICENSE`; this does not relicense third-party code. Kernel and SukiSU license texts are preserved, as is AnyKernel3's notice covering its bundled tools. The pinned SUSFS repository supplies GPL-3.0 while Linux is GPL-2.0-only; this integration's binary redistribution compatibility is **unresolved**, and adding notices does not resolve it. This is a local candidate, not authorization for public distribution. No upstream license change has been obtained. See `licenses/NOTICE.txt`.
