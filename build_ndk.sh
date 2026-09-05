#!/bin/sh
# Cross-compile the loader (TIGER_UC) for Android with the NDK.
#
# The same one translation unit as the desktop Unicorn build (build_uc.sh),
# through the POSIX platform seam (src/tiger_plat.h): Win32 names on pthreads,
# mmap and clock_gettime.  Fred first, so AAC is stubbed out (TIGER_NO_AAC) --
# MacinTalk's formant voice never decodes a sample bank.
#
# armeabi-v7a ONLY, and on purpose: the whole emulator design maps guest
# addresses to host addresses as 32-bit numbers (src/tiger_host_uc.c), which is
# exact on a 32-bit ABI and a truncation on a 64-bit one.  v7a is 3 of the 4
# watches measured in Phase 0, and runs on an arm64 phone too.  arm64 is a
# separate, later design.
#
# Two stages, because Unicorn takes a while to cross-build and the host source
# does not:
#   ./build_ndk.sh            # compile the TU to an object; report errors
#   ./build_ndk.sh build      # compile AND link against a cross-built Unicorn
#
# The cross-built Unicorn comes from android/harness/build_unicorn_cyg.sh
# (armeabi-v7a build); until it exists, the compile stage is what iterates.
set -e

export MSYS2_ARG_CONV_EXCL="*"
export MSYS_NO_PATHCONV=1

ROOT="$(cd "$(dirname "$0")" && pwd -W 2>/dev/null || pwd)"
OUT="$ROOT/build/ndk"
DOLINK="${1:-}"

NDK="${ANDROID_NDK:-C:/Android/Sdk/ndk/27.2.12479018}"
API="${ANDROID_API:-26}"
TARGET="armv7-none-linux-androideabi${API}"
CLANG="$NDK/toolchains/llvm/prebuilt/windows-x86_64/bin/clang.exe"

# Unicorn: the ARM static lib for linking, and headers for compiling.  The
# headers are architecture-independent, so a compile-only pass can borrow them
# from the 32-bit Windows wheel before the ARM library is built.
UC_ARM="$ROOT/android/harness/unicorn/build-armeabi-v7a"
UC_LIB="$(ls "$UC_ARM"/libunicorn.a "$UC_ARM"/*/libunicorn.a 2>/dev/null | head -1 || true)"
if [ -d "$ROOT/android/harness/unicorn/include" ]; then
    UC_INC="$ROOT/android/harness/unicorn/include"
else
    UC_INC="${UC_HOME:-C:/Python313-32/Lib/site-packages/unicorn}/include"
fi

[ -x "$CLANG" ] || { echo "no NDK clang at $CLANG"; exit 1; }
[ -f "$UC_INC/unicorn/unicorn.h" ] || { echo "no unicorn headers at $UC_INC"; exit 1; }
echo "NDK:    $NDK"
echo "target: $TARGET"
echo "uc inc: $UC_INC"

mkdir -p "$OUT"

CFLAGS="-O2 -fPIC -DTIGER_UC -DTIGER_NO_AAC -Wno-macro-redefined -I\"$UC_INC\""

if [ "$DOLINK" = build ]; then
    [ -n "$UC_LIB" ] && [ -f "$UC_LIB" ] || {
        echo "no cross-built Unicorn yet; run:"
        echo "  android/harness/build_unicorn_cyg.sh armeabi-v7a build"
        echo "(under Cygwin, see that script's header), then retry"; exit 1; }
    echo "uc lib: $UC_LIB"
    eval "\"$CLANG\" --target=$TARGET $CFLAGS -fPIE -pie \
        \"$ROOT/src/tiger_host.c\" -o \"$OUT/tiger_host\" \
        \"$UC_LIB\" -lm -ldl -llog" \
        > "$OUT/build.log" 2>&1 || { echo "link failed:"; tail -60 "$OUT/build.log"; exit 1; }
    echo "  -> build/ndk/tiger_host (armeabi-v7a)"
else
    eval "\"$CLANG\" --target=$TARGET $CFLAGS \
        -c \"$ROOT/src/tiger_host.c\" -o \"$OUT/tiger_host.o\"" \
        > "$OUT/build.log" 2>&1 || { echo "compile failed:"; tail -80 "$OUT/build.log"; exit 1; }
    echo "  -> build/ndk/tiger_host.o (compile-only; run with 'build' to link)"
fi
