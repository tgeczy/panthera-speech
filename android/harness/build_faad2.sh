#!/bin/sh
# Fetch and cross-compile FAAD2 for the Android build, so the AAC voices decode
# in this process instead of over Binder.
#
# Why at all: Vicki and Alex share the `meow` engine, whose sample bank is AAC.
# AMediaCodec decodes it correctly and out of process, and Alex is a
# unit-selection voice -- one ordinary sentence decoded 1630 access units and
# spent 4837 ms doing it, for 5.1 seconds of audio.  That is realtime in the
# decoder alone.  Every one of those units is a handful of Binder round trips to
# mediaswcodec, and no amount of tuning the polling gets past a per-buffer IPC.
# It also means an AOSP build with no AAC codec has no Vicki and no Alex; with
# this it does.
#
# Nothing of FAAD2 is committed here, exactly as nothing of Unicorn is (see
# build_unicorn_cyg.sh): the source is fetched at a pinned tag and gitignored,
# so this repository carries no third-party tree it would then have to maintain.
#
# Licence: FAAD2 is GPLv2, and the APK is GPLv2 already because of Unicorn, so
# the two agree.  That is the whole reason this is FAAD2 and not one of the
# permissive decoders with a patent grant nobody can read.
#
#   ./build_faad2.sh          # objects land in android/harness/faad2-obj
set -e
# No MSYS_NO_PATHCONV here, unlike the adb scripts: git and clang are Windows
# binaries, and a POSIX /c/... path handed to them unconverted is read relative
# to the current drive root -- the clone lands in C:\c\git\... and the build
# then cannot find a single source file.

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$HERE/faad2"
TAG="${FAAD2_TAG:-2.11.2}"
ABI="${1:-armeabi-v7a}"
OBJ="$HERE/faad2-obj-$ABI"

NDK="${ANDROID_NDK:-C:/Android/Sdk/ndk/27.2.12479018}"
API="${ANDROID_API:-26}"
case "$ABI" in
  armeabi-v7a) TARGET="armv7-none-linux-androideabi${API}" ;;
  arm64-v8a)   TARGET="aarch64-none-linux-android${API}" ;;
  *) echo "unknown ABI '$ABI' (armeabi-v7a or arm64-v8a)"; exit 1 ;;
esac
CLANG="$NDK/toolchains/llvm/prebuilt/windows-x86_64/bin/clang.exe"
[ -x "$CLANG" ] || { echo "no NDK clang at $CLANG"; exit 1; }

if [ ! -d "$SRC" ]; then
    echo "== fetching FAAD2 $TAG =="
    # core.autocrlf=false: the sources are compiled by a cross-compiler that
    # does not care, but a checkout with CRLF makes every later diff unreadable.
    git -c core.autocrlf=false -c core.eol=lf clone --depth 1 --branch "$TAG" \
        https://github.com/knik0/faad2.git "$SRC"
fi

# HAVE_STDINT_H matters: without it common.h defines its own uint32_t as
# `unsigned long`, which on this target is a different type from the sysroot's
# and every translation unit fails on the redefinition.
echo "== compiling libfaad for $TARGET =="
mkdir -p "$OBJ"
n=0
for f in "$SRC"/libfaad/*.c; do
    o="$OBJ/$(basename "$f" .c).o"
    # Not through eval: PACKAGE_VERSION has to reach the compiler still wearing
    # its quotes, and a round trip through eval takes them off -- the error is
    # `#define PACKAGE_VERSION 2.11.2`, which is not a string and not valid.
    "$CLANG" --target=$TARGET -O2 -fPIC \
        -I"$SRC/include" -I"$SRC/libfaad" \
        -DHAVE_STDINT_H=1 -DHAVE_STRING_H=1 -DHAVE_MEMCPY=1 \
        -DPACKAGE_VERSION="\"$TAG\"" -Wno-everything \
        -c "$f" -o "$o" || { echo "failed: $f"; exit 1; }
    n=$((n + 1))
done
echo "  $n objects in $OBJ ($(du -sh "$OBJ" | cut -f1))"
echo "Now run build_jni_so.sh, which links them in."
