#!/bin/sh
# Build the host, and stage it into the add-on.
#
# **The loader is not duplicated here.**  It is the same program that runs
# Tiger's engine -- the same Mach-O loader, the same shims, the same AAC
# decoder -- pointed at a 10.5 tree instead of a 10.4 one.  Leopard needed
# three things Tiger did not (an optional third image for libstdc++, 16-byte
# stack alignment at every entry, and the Accelerate routines behind Alex's
# WSOLA), and all three are in that one source tree because Tiger benefits from
# two of them and is unharmed by the third.
#
# Keeping a second copy here would mean fixing every future bug twice, and
# discovering the divergence months later through a voice that sounds wrong on
# one add-on and right on the other.  So this builds from the sibling checkout
# and stages the result under a name that says what it is locally.
set -e

ROOT="$(cd "$(dirname "$0")" && pwd -W 2>/dev/null || cygpath -m "$(pwd)")"
HOSTSRC="${LEOPARD_HOST_SRC:-$ROOT/../tiger-speech}"

if [ ! -f "$HOSTSRC/build.sh" ]; then
    echo "The shared loader is not where this expected it:"
    echo "    $HOSTSRC"
    echo
    echo "Clone https://github.com/tgeczy/tiger-speech beside this checkout,"
    echo "or set LEOPARD_HOST_SRC to wherever it lives."
    exit 1
fi

echo "building the shared loader in $HOSTSRC"
( cd "$HOSTSRC" && sh ./build.sh )

mkdir -p "$ROOT/build"
cp "$HOSTSRC/build/tiger_host.exe" "$ROOT/build/leopard_host.exe"
cp "$ROOT/build/leopard_host.exe" \
   "$ROOT/addon/synthDrivers/_leopardspeech/leopard_host.exe"
echo "  -> build/leopard_host.exe"
echo "  -> addon/synthDrivers/_leopardspeech/leopard_host.exe"
