#!/bin/sh
# Build the loader, and stage it into every add-on that runs on it.
#
# **There is one loader in this repository and there will only ever be one.**
# It is the same program for Tiger and for Leopard -- the same Mach-O loader,
# the same shims, the same AAC decoder -- pointed at a 10.4 tree or a 10.5 one.
# Leopard needed three things Tiger did not (an optional third image for
# libstdc++, 16-byte stack alignment at every entry, and the Accelerate
# routines behind Alex's WSOLA), and all three live here because Tiger
# benefits from two of them and is unharmed by the third.
#
# A second copy would mean fixing every future bug twice and discovering the
# divergence months later, through a voice that sounds wrong in one add-on and
# right in the other.  Each add-on gets the binary staged under a name that
# says what it is locally; they are the same bytes.
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

# The same program again, as a DLL, for NVDA's secure screens.
#
# NVDA's config._setSystemConfig drops every file ending .exe when it copies
# the user configuration to systemConfig, so an add-on that ships one has no
# engine on the sign-in desktop or on a UAC prompt -- the user is handed a
# different synthesizer at exactly the moment a password is being typed.  A
# .dll is copied like any other file, and NVDA's own 32-bit bridge provides
# the 32-bit process to load it into.
#
# PT_DLL swaps main() for src/tiger_host_api.c and nothing else: the loader,
# the shims and the request loop are the same source, compiled twice.  Both
# halves are built here, every time, so a change that breaks one is a build
# failure rather than a discovery weeks later on a screen nobody tests on.
#
# **No /LARGEADDRESSAWARE.**  It is a property of the executable, so on a DLL
# it would be meaningless in any case -- but the host this one is loaded into
# is nvda_synthDriverHost.exe, which is not large-address-aware, and the whole
# 4 GB question is settled there rather than here.  All four generations were
# measured rendering inside 2 GB, Leopard's 669 MB Alex included, because the
# loader relocates when a prebound base is unavailable (tiger_host_macho.c).
mkdir -p "$OUT/dll"          # -Fo will not create it, and says so obscurely
eval "\"$CL\" -nologo -O2 -MT -W3 -DPT_DLL $INC \"$ROOT/src/tiger_host.c\" \
    -Fe\"$OUT/tiger_host.dll\" -Fo\"$OUT/dll/\" \
    -LD -link $LIB winmm.lib ole32.lib mfuuid.lib" > "$OUT/build-dll.log" 2>&1 || {
        echo "DLL build failed:"; tail -40 "$OUT/build-dll.log"; exit 1; }

echo "  -> build/tiger_host.dll"

# Stage them into the add-on immediately.  A driver loads its own copy, not
# this one, and a stale copy there presents as "the fix did not work" -- which
# cost a confusing test failure once already.
#
# One line per add-on, now that Tiger and Leopard are one: there used to be
# two, staging the same bytes twice under two names.  Add a line here if a
# second add-on ever joins.  Missing one is silent: the add-on simply keeps
# running last month's loader.
stage() {                          # <add-on folder> <_private folder> <name>
    dest="$ROOT/$1/addon/synthDrivers/$2/$3"
    [ -d "$ROOT/$1" ] || return 0  # not checked out; nothing to stage into
    mkdir -p "$(dirname "$dest")"
    cp "$OUT/tiger_host.exe" "$dest"
    cp "$OUT/tiger_host.dll" "${dest%.exe}.dll"
    echo "  -> $1/addon/synthDrivers/$2/$3 (+ .dll)"
}

stage panthera _panthera panthera_host.exe
