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

# The Android SDK: ANDROID_HOME or ANDROID_SDK_ROOT if set, else the SDK's
# own standard install folder.  Nothing here names one machine.
SDK="${ANDROID_HOME:-${ANDROID_SDK_ROOT:-${LOCALAPPDATA:-$HOME}/Android/Sdk}}"
NDK="${ANDROID_NDK:-$SDK/ndk/27.2.12479018}"
API="${ANDROID_API:-26}"
TARGET="armv7-none-linux-androideabi${API}"
CLANG="$NDK/toolchains/llvm/prebuilt/windows-x86_64/bin/clang.exe"

# Unicorn: the ARM static libs for linking, and headers for compiling.  The
# static build produces three archives with circular references between them
# (the API, the x86 TCG backend, and QEMU's common util), so they link inside a
# --start-group.  The headers are architecture-independent, so a compile-only
# pass can borrow them from the 32-bit Windows wheel before the libs are built.
UC_ARM="$ROOT/android/harness/unicorn/build-armeabi-v7a"
UC_STATIC="$UC_ARM/libunicorn-static.a"
UC_SOFTMMU="$UC_ARM/libx86_64-softmmu.a"
UC_COMMON="$UC_ARM/libunicorn-common.a"
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
    [ -f "$UC_STATIC" ] || {
        echo "no cross-built Unicorn yet; run:"
        echo "  android/harness/build_unicorn_cyg.sh armeabi-v7a build"
        echo "(under Cygwin, see that script's header), then retry"; exit 1; }
    echo "uc libs: $UC_ARM/{libunicorn-static,libx86_64-softmmu,libunicorn-common}.a"
    eval "\"$CLANG\" --target=$TARGET $CFLAGS -fPIE -pie \
        \"$ROOT/src/tiger_host.c\" -o \"$OUT/tiger_host\" \
        -Wl,--start-group \"$UC_STATIC\" \"$UC_SOFTMMU\" \"$UC_COMMON\" -Wl,--end-group \
        -lm -ldl -llog" \
        > "$OUT/build.log" 2>&1 || { echo "link failed:"; tail -60 "$OUT/build.log"; exit 1; }
    echo "  -> build/ndk/tiger_host (armeabi-v7a)"
else
    eval "\"$CLANG\" --target=$TARGET $CFLAGS \
        -c \"$ROOT/src/tiger_host.c\" -o \"$OUT/tiger_host.o\"" \
        > "$OUT/build.log" 2>&1 || { echo "compile failed:"; tail -80 "$OUT/build.log"; exit 1; }
    echo "  -> build/ndk/tiger_host.o (compile-only; run with 'build' to link)"
fi
