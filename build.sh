#!/usr/bin/env bash
set -euo pipefail

TASK_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$TASK_DIR/host-tools"
KERNEL_DIR="$TASK_DIR/sources/common"
KSU_DIR="$TASK_DIR/sources/SukiSU-Ultra"
TOOLCHAIN_DIR="${TOOLCHAIN_DIR:-$TASK_DIR/toolchains/clang-r450784e}"
BUILD_OUT="$TASK_DIR/out"
test -x "$TOOLCHAIN_DIR/bin/clang"
test "$(git -C "$KERNEL_DIR" rev-parse HEAD)" = 51bba4309aac6b0889f1546c72865851cba20612
test "$(git -C "$KSU_DIR" rev-parse HEAD)" = b20dee702035af09cb2ecb5f35443bbc1747f3e6
test "$(git -C "$KSU_DIR" rev-parse main)" = 7755cdb36f63945f286d7b1cab662b42b18f2789
test "$(readlink -f "$KERNEL_DIR/drivers/kernelsu")" = "$KSU_DIR/kernel"
mkdir -p "$BUILD_OUT" "$TASK_DIR/logs" "$TASK_DIR/dist"

export PATH="$BASE_DIR/bin:$TOOLCHAIN_DIR/bin:$PATH"
export ARCH=arm64 SUBARCH=arm64 LLVM=1 LLVM_IAS=1
export CROSS_COMPILE=aarch64-linux-gnu-
export PAHOLE="${PAHOLE:-pahole}"
test "$("$PAHOLE" --version)" = v1.31
export LOCALVERSION=""
export KBUILD_BUILD_USER=builder KBUILD_BUILD_HOST=xperia1v
export KBUILD_BUILD_VERSION=1
export KBUILD_BUILD_TIMESTAMP='Sun Sep 20 00:00:00 UTC 2026'
export SOURCE_DATE_EPOCH=1789862400
export KCFLAGS=-D__ANDROID_COMMON_KERNEL__

{
  date -Iseconds
  clang --version
  "$PAHOLE" --version
  git -C "$KERNEL_DIR" rev-parse HEAD
  git -C "$KSU_DIR" rev-parse HEAD main
  git -C "$KSU_DIR" rev-list --count main
} > "$TASK_DIR/logs/environment.log"

make -C "$KERNEL_DIR" O="$BUILD_OUT" gki_defconfig 2>&1 | tee "$TASK_DIR/logs/config.log"
cp "$BUILD_OUT/.config" "$TASK_DIR/dist/kernel.config"
make -C "$KERNEL_DIR" O="$BUILD_OUT" -j"${BUILD_JOBS:-12}" Image modules 2>&1 | tee "$TASK_DIR/logs/build.log"
cp "$BUILD_OUT/arch/arm64/boot/Image" "$TASK_DIR/dist/Image"
cp "$BUILD_OUT/Module.symvers" "$TASK_DIR/dist/Module.symvers"
cp "$BUILD_OUT/include/config/kernel.release" "$TASK_DIR/dist/kernel.release"
date -Iseconds > "$TASK_DIR/logs/build_completed_at.txt"
