#!/bin/sh
# Build libpanthera.so for the Android app: the engine (tiger_host.c, one TU,
# TIGER_UC + TIGER_NO_AAC + TIGER_JNI) plus the JNI bridge, linked with the
# cross-built Unicorn into a shared library, dropped into the app's jniLibs so
# Gradle just packages it.
#
# Prebuilding the .so this way -- rather than driving CMake from Gradle -- keeps
# the APK build free of the Cygwin/QEMU-configure dance Unicorn's own CMake
# needs (see android/harness/build_unicorn_cyg.sh); Gradle never touches the
# NDK toolchain, it just bundles the finished .so.
#
#   ./build_jni_so.sh            # armeabi-v7a
#
# armeabi-v7a only, for the same reason build_ndk.sh is: guest==host address is
# a 32-bit identity.  A second ABI is a separate design.
set -e
export MSYS2_ARG_CONV_EXCL="*"
export MSYS_NO_PATHCONV=1

ROOT="$(cd "$(dirname "$0")" && pwd -W 2>/dev/null || pwd)"
ABI="armeabi-v7a"
OUT="$ROOT/src/platforms/android/app/src/main/jniLibs/$ABI"

NDK="${ANDROID_NDK:-C:/Android/Sdk/ndk/27.2.12479018}"
API="${ANDROID_API:-26}"
TARGET="armv7-none-linux-androideabi${API}"
CLANG="$NDK/toolchains/llvm/prebuilt/windows-x86_64/bin/clang.exe"
CLANGXX="$NDK/toolchains/llvm/prebuilt/windows-x86_64/bin/clang++.exe"

UC_ARM="$ROOT/android/harness/unicorn/build-$ABI"
UC_STATIC="$UC_ARM/libunicorn-static.a"
UC_SOFTMMU="$UC_ARM/libx86_64-softmmu.a"
UC_COMMON="$UC_ARM/libunicorn-common.a"
UC_INC="$ROOT/android/harness/unicorn/include"

[ -x "$CLANG" ] || { echo "no NDK clang at $CLANG"; exit 1; }
[ -f "$UC_STATIC" ] || { echo "no cross-built Unicorn ($UC_STATIC); run android/harness/build_unicorn_cyg.sh $ABI build"; exit 1; }
[ -f "$UC_INC/unicorn/unicorn.h" ] || { echo "no unicorn headers at $UC_INC"; exit 1; }

BUILD="$ROOT/build/jni"
mkdir -p "$BUILD" "$OUT"

CFLAGS="-O2 -fPIC -DTIGER_UC -DTIGER_AAC_NDK -DTIGER_JNI -Wno-macro-redefined -I\"$UC_INC\""

echo "compiling engine TU (TIGER_JNI)"
eval "\"$CLANG\" --target=$TARGET $CFLAGS -c \"$ROOT/src/tiger_host.c\" -o \"$BUILD/tiger_host.o\"" \
    > "$BUILD/build.log" 2>&1 || { echo "engine compile failed:"; tail -40 "$BUILD/build.log"; exit 1; }

echo "compiling JNI bridge"
eval "\"$CLANGXX\" --target=$TARGET -O2 -fPIC -std=c++17 -I\"$ROOT/src\" \
    -c \"$ROOT/src/platforms/android/app/src/main/cpp/panthera_jni.cpp\" -o \"$BUILD/panthera_jni.o\"" \
    >> "$BUILD/build.log" 2>&1 || { echo "jni compile failed:"; tail -40 "$BUILD/build.log"; exit 1; }

echo "linking libpanthera.so"
# -static-libstdc++: the JNI bridge is the only C++ here and uses no STL, so
# statically link the tiny bit of C++ runtime it needs rather than depend on
# libc++_shared.so, which the APK would otherwise have to ship too.
eval "\"$CLANGXX\" --target=$TARGET -shared -fPIC -static-libstdc++ -o \"$OUT/libpanthera.so\" \
    \"$BUILD/tiger_host.o\" \"$BUILD/panthera_jni.o\" \
    -Wl,--start-group \"$UC_STATIC\" \"$UC_SOFTMMU\" \"$UC_COMMON\" -Wl,--end-group \
    -llog -lm -ldl -lmediandk -Wl,-z,max-page-size=16384 -Wl,--no-undefined" \
    >> "$BUILD/build.log" 2>&1 || { echo "link failed:"; tail -40 "$BUILD/build.log"; exit 1; }

echo "  -> $OUT/libpanthera.so ($(wc -c < "$OUT/libpanthera.so") bytes)"
