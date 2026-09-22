#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
mkdir -p "$ROOT/sources"
fetch_source() {
  local name="$1" url="$2" commit="$3" mode="$4"
  test ! -e "$ROOT/sources/$name" || { echo "Source directory already exists: $name" >&2; exit 1; }
  if [[ -n "${SOURCE_MIRROR:-}" ]]; then url="$SOURCE_MIRROR/$name"; fi
  git init -q "$ROOT/sources/$name"
  git -C "$ROOT/sources/$name" remote add origin "$url"
  if [[ "$mode" == full ]]; then
    git -C "$ROOT/sources/$name" fetch origin "$commit" 7755cdb36f63945f286d7b1cab662b42b18f2789
    git -C "$ROOT/sources/$name" update-ref refs/heads/main 7755cdb36f63945f286d7b1cab662b42b18f2789
  else
    git -C "$ROOT/sources/$name" fetch --depth=1 origin "$commit"
  fi
  git -C "$ROOT/sources/$name" checkout --detach "$commit"
  git -C "$ROOT/sources/$name" apply --check "$ROOT/patches/final_$name.patch"
  git -C "$ROOT/sources/$name" apply "$ROOT/patches/final_$name.patch"
}
fetch_source common https://android.googlesource.com/kernel/common 51bba4309aac6b0889f1546c72865851cba20612 shallow
fetch_source SukiSU-Ultra https://github.com/SukiSU-Ultra/SukiSU-Ultra.git b20dee702035af09cb2ecb5f35443bbc1747f3e6 full
fetch_source AnyKernel3 https://github.com/osm0sis/AnyKernel3.git 020dfeccf9d7e962a48400fc94d3e451df92eead shallow
cp -a "$ROOT/added/common/." "$ROOT/sources/common/"
ln -s ../../SukiSU-Ultra/kernel "$ROOT/sources/common/drivers/kernelsu"
test "$(git -C "$ROOT/sources/SukiSU-Ultra" rev-list --count main)" = 3737
echo 'Pinned source trees and release patches prepared.'
