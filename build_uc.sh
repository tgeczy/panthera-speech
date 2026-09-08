#!/bin/sh
# Build the loader with the Unicorn seam (TIGER_UC): the same host, but the
# i386 engine runs inside Unicorn (QEMU TCG) instead of being called natively,
# so it can run on ARM.  See src/tiger_host_uc.c and docs/android-phase0.md.
#
# This desktop harness builds a 32-bit Windows host for fast native/UC
# comparisons. The shared source also builds for AArch64 Android: guest
# pointer slots stay four bytes, and guest-visible storage uses the arena.
#
# The 32-bit Unicorn 2.1.4 comes from the pip wheel in a 32-bit Python; it ships
# unicorn.dll and the headers.  We link the *DLL* through an import library we
# generate here, not the wheel's static unicorn.lib: that static lib was built
# against the dynamic CRT, and this host must use the static CRT (/MT), because
# the shim table takes the address of CRT functions -- which under /MD are
# dllimports and not constant initializers.  The DLL carries its own CRT, so
# importing it sidesteps the mismatch entirely.  Iterate on the desktop
# (seconds/build), then cross-compile the same source with the NDK.
set -e

export MSYS2_ARG_CONV_EXCL="*"
export MSYS_NO_PATHCONV=1

ROOT="$(cd "$(dirname "$0")" && pwd -W 2>/dev/null || cygpath -m "$(pwd)")"
OUT="$ROOT/build/uc"

newest() { for p in "$@"; do [ -e "$p" ] && echo "$p"; done | sort -V | tail -1; }

MSVC="$(newest "C:/Program Files (x86)/Microsoft Visual Studio"/*/*/VC/Tools/MSVC/* \
               "C:/Program Files/Microsoft Visual Studio"/*/*/VC/Tools/MSVC/*)"
SDK="C:/Program Files (x86)/Windows Kits/10"
SDKV="$(newest "$SDK/Include"/* | sed 's#.*/##')"

# The 32-bit Unicorn wheel install.  Override UC_HOME if it lives elsewhere.
UC_HOME="${UC_HOME:-C:/Python313-32/Lib/site-packages/unicorn}"
UC_INC="$UC_HOME/include"
UC_DLL="$UC_HOME/lib/unicorn.dll"

[ -n "$MSVC" ]   || { echo "no MSVC toolchain found"; exit 1; }
[ -n "$SDKV" ]   || { echo "no Windows SDK found"; exit 1; }
[ -f "$UC_DLL" ] || { echo "no 32-bit unicorn.dll at $UC_DLL"; exit 1; }
echo "MSVC: $MSVC"
echo "SDK:  $SDKV"
echo "UC:   $UC_HOME"

CL="$MSVC/bin/Hostx64/x86/cl.exe"
DUMPBIN="$MSVC/bin/Hostx64/x64/dumpbin.exe"
LIBEXE="$MSVC/bin/Hostx64/x86/lib.exe"

mkdir -p "$OUT"

# Generate an x86 import library for unicorn.dll from its exported uc_* names.
UC_IMP="$OUT/unicorn_imp.lib"
if [ ! -f "$UC_IMP" ] || [ "$UC_DLL" -nt "$UC_IMP" ]; then
    echo "generating unicorn import lib"
    "$DUMPBIN" /exports "$UC_DLL" 2>/dev/null \
      | awk '/^[[:space:]]+[0-9]+[[:space:]]+[0-9A-Fa-f]+[[:space:]]+[0-9A-Fa-f]{8}[[:space:]]+uc_/ {print $NF}' \
      | sort -u > "$OUT/_uc_names.txt"
    { echo "EXPORTS"; cat "$OUT/_uc_names.txt"; } > "$OUT/unicorn.def"
    "$LIBEXE" /nologo /def:"$OUT/unicorn.def" /machine:x86 /out:"$UC_IMP" \
        > "$OUT/lib.log" 2>&1 || { echo "import lib failed:"; cat "$OUT/lib.log"; exit 1; }
fi

INC="-I\"$MSVC/include\" -I\"$SDK/Include/$SDKV/ucrt\" -I\"$SDK/Include/$SDKV/um\" -I\"$SDK/Include/$SDKV/shared\" -I\"$UC_INC\""
LIB="-LIBPATH:\"$MSVC/lib/x86\" -LIBPATH:\"$SDK/lib/$SDKV/ucrt/x86\" -LIBPATH:\"$SDK/lib/$SDKV/um/x86\""

eval "\"$CL\" -nologo -O2 -MT -W3 -DTIGER_UC $INC \"$ROOT/src/tiger_host.c\" \
    -Fe\"$OUT/tiger_host_uc.exe\" -Fo\"$OUT/\" \
    -link $LIB \"$UC_IMP\" winmm.lib ole32.lib mfuuid.lib -LARGEADDRESSAWARE" \
    > "$OUT/build.log" 2>&1 || {
        echo "build failed:"; tail -50 "$OUT/build.log"; exit 1; }

# unicorn.dll must sit beside the exe (it is not on the system PATH).
cp "$UC_DLL" "$OUT/"

echo "  -> build/uc/tiger_host_uc.exe (+ unicorn.dll)"
