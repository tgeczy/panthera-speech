#!/bin/sh
# Build tiger_host.exe.
#
# 32-bit, always.  Apple's MacinTalk is i386 code and this process has to be
# able to call it, so the bitness is not a preference.  NVDA is 64-bit, which
# is why this is an executable talking over a pipe rather than a DLL.
#
# /LARGEADDRESSAWARE matters: SpeechDictionary is prebound at 0x96d0c000 with
# its __DATA at 0xa6d0c000, both above the 2 GB line.  A 32-bit process on
# 64-bit Windows gets the full 4 GB only if it says it can handle it, and
# mapping the library at its own base is what lets us skip 793 relocations.
set -e

export MSYS2_ARG_CONV_EXCL="*"
export MSYS_NO_PATHCONV=1

ROOT="$(cd "$(dirname "$0")" && pwd -W 2>/dev/null || cygpath -m "$(pwd)")"
OUT="$ROOT/build"

newest() { for p in "$@"; do [ -e "$p" ] && echo "$p"; done | sort -V | tail -1; }

MSVC="$(newest "C:/Program Files (x86)/Microsoft Visual Studio"/*/*/VC/Tools/MSVC/* \
               "C:/Program Files/Microsoft Visual Studio"/*/*/VC/Tools/MSVC/*)"
SDK="C:/Program Files (x86)/Windows Kits/10"
SDKV="$(newest "$SDK/Include"/* | sed 's#.*/##')"

[ -n "$MSVC" ] || { echo "no MSVC toolchain found"; exit 1; }
[ -n "$SDKV" ] || { echo "no Windows SDK found"; exit 1; }
echo "MSVC: $MSVC"
echo "SDK:  $SDKV"

mkdir -p "$OUT"

INC="-I\"$MSVC/include\" -I\"$SDK/Include/$SDKV/ucrt\" -I\"$SDK/Include/$SDKV/um\" -I\"$SDK/Include/$SDKV/shared\""
LIB="-LIBPATH:\"$MSVC/lib/x86\" -LIBPATH:\"$SDK/lib/$SDKV/ucrt/x86\" -LIBPATH:\"$SDK/lib/$SDKV/um/x86\""
CL="$MSVC/bin/Hostx64/x86/cl.exe"

# /MT for the same reason the sibling project uses it: a /MD build needs a
# redistributable that is present on this machine and absent on a clean one.
eval "\"$CL\" -nologo -O2 -MT -W3 $INC \"$ROOT/src/tiger_host.c\" \
    -Fe\"$OUT/tiger_host.exe\" -Fo\"$OUT/\" \
    -link $LIB winmm.lib ole32.lib mfuuid.lib -LARGEADDRESSAWARE" > "$OUT/build.log" 2>&1 || {
        echo "build failed:"; tail -40 "$OUT/build.log"; exit 1; }

echo "  -> build/tiger_host.exe"

# Stage it into the add-on immediately.  The driver loads the add-on's copy,
# not this one, and a stale copy there presents as "the fix did not work" --
# which cost a confusing test failure once already.
cp "$OUT/tiger_host.exe" "$ROOT/addon/synthDrivers/_tigerspeech/tiger_host.exe"
echo "  -> addon/synthDrivers/_tigerspeech/tiger_host.exe"
