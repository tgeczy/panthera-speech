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

# One native builder is shared by NVDA and SAPI. It links the pinned Glint
# fallback into both EXE and secure-screen DLL, with a static C++ runtime.
python "$ROOT/tools/build_windows.py" --out "$OUT"

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
