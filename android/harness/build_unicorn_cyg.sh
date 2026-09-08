#!/bin/bash
# Cross-build Unicorn 2.1.4 (x86 backend) for Android, static, per ABI.
#
# RUN UNDER CYGWIN BASH with a clean Cygwin PATH, e.g.:
#   /c/cygwin64/bin/bash.exe --noprofile --norc -c \
#     'export PATH=/usr/local/bin:/usr/bin:/bin; exec .../build_unicorn_cyg.sh arm64-v8a build'
#
# Why Cygwin: Unicorn's CMake shells out to QEMU's `configure`, a POSIX shell
# script.  Three things trip it on a Windows host, all handled here:
#   1. git's autocrlf mangles configure to CRLF -> we clone with autocrlf=false.
#   2. Cygwin has no binutils `strings` -> shims/strings wraps NDK llvm-strings.
#   3. configure insists a `pkg-config` binary exists (Unicorn bundles
#      glib_compat and never uses it) -> shims/pkg-config is a benign stub.
# A clean Cygwin PATH is required so PATH translates both ways across the
# native cmake and the shims resolve inside the configure subprocess.
set -u
ABI="${1:-arm64-v8a}"
DOBUILD="${2:-}"

ROOTC="$(cd "$(dirname "$0")" && pwd)"          # this dir, Cygwin form
ROOTW="$(cygpath -m "$ROOTC")"                   # ... Windows form (C:/...)
export PATH="$ROOTC/shims:/usr/local/bin:/usr/bin:/bin"

# Toolchain (override via env if your NDK/cmake live elsewhere).
# ANDROID_HOME (Windows form, e.g. C:/Users/you/AppData/Local/Android/Sdk) names the SDK; ANDROID_NDK,
# CMAKE and NINJA override the pieces individually.
SDKW="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-${LOCALAPPDATA:-$HOME}/Android/Sdk}}"
NDKW="${ANDROID_NDK:-$SDKW/ndk/27.2.12479018}"
CMAKE="${CMAKE:-$(cygpath -u "$SDKW")/cmake/3.22.1/bin/cmake.exe}"
NINJA="${NINJA:-$SDKW/cmake/3.22.1/bin/ninja.exe}"

# Clone Unicorn 2.1.4 with LF endings (the autocrlf fix) if not present.
if [ ! -d "$ROOTC/unicorn" ]; then
  echo "== cloning unicorn 2.1.4 (autocrlf=false) =="
  git -c core.autocrlf=false -c core.eol=lf clone --depth 1 --branch 2.1.4 \
      https://github.com/unicorn-engine/unicorn.git "$ROOTC/unicorn"
fi

BDW="$ROOTW/unicorn/build-$ABI"
BDC="$ROOTC/unicorn/build-$ABI"

echo "sh=$(which sh)  strings=$(which strings)  pkg-config=$(which pkg-config)"
rm -rf "$BDC"
"$CMAKE" -S "$ROOTW/unicorn" -B "$BDW" -G Ninja \
  -DCMAKE_MAKE_PROGRAM="$NINJA" \
  -DCMAKE_TOOLCHAIN_FILE="$NDKW/build/cmake/android.toolchain.cmake" \
  -DANDROID_ABI="$ABI" -DANDROID_PLATFORM=android-26 \
  -DCMAKE_BUILD_TYPE=Release \
  -DUNICORN_ARCH=x86 -DBUILD_SHARED_LIBS=OFF -DUNICORN_BUILD_TESTS=OFF 2>&1 | tail -20

echo "==== config-host.h ===="
if [ -f "$BDC/config-host.h" ]; then
  echo "GENERATED OK"
else
  echo "STILL MISSING -- configure failed; stop"
  exit 1
fi

if [ "$DOBUILD" = build ]; then
  echo "==== building unicorn ($ABI) ===="
  "$CMAKE" --build "$BDW" --target unicorn -j 2>&1 | tail -25
  echo "==== artifacts ===="
  find "$BDC" -name 'libunicorn*.a' -o -name 'libunicorn*.so'
fi
