#!/usr/bin/env bash
# Cross-compile the NDK-only probes (memprobe, jitprobe) for both ABIs.
# Static-ish, self-contained; one pushable binary per ABI. No Unicorn needed.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
BIN="C:/Android/Sdk/ndk/27.2.12479018/toolchains/llvm/prebuilt/windows-x86_64/bin"
OUT="$HERE/build"
mkdir -p "$OUT"

# API 26 = minSdk of the reuse TTS shell; watches are >= 26.
declare -A CC=(
  [arm64-v8a]="$BIN/aarch64-linux-android26-clang"
  [armeabi-v7a]="$BIN/armv7a-linux-androideabi26-clang"
)

for ABI in "${!CC[@]}"; do
  for P in memprobe jitprobe; do
    echo "== $P ($ABI) =="
    "${CC[$ABI]}" -O2 -Wall -o "$OUT/${P}.$ABI" "$HERE/src/${P}.c"
  done
done
echo "== built =="
ls -la "$OUT"
