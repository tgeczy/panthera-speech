#!/bin/sh
# Build the host as a native Linux binary, so Linux users stop running it
# under Wine.
#
# The native ELF provides file rendering and streaming through the same pipe
# protocol as the Windows host.
#
# **i686 is the fast target and the default.**  Apple's MacinTalk is i386 code,
# so on 32-bit x86 this process can simply call it: no Unicorn, no emulation,
# the same arrangement the 32-bit Windows build has always had.  x86-64 and
# aarch64 need Unicorn to run the guest, which costs real time -- on x86-64
# that would be SLOWER than the Wine setup it replaces, so it is offered for
# completeness (and for arm64 Linux, where there is no alternative) rather than
# recommended.
#
#   ./build_linux.sh              # i686, native, no Unicorn
#   ./build_linux.sh x86_64       # needs Unicorn
#   ./build_linux.sh aarch64      # needs Unicorn
#
# On a 64-bit distribution the i686 build needs the 32-bit toolchain and libs:
#
#   Debian/Ubuntu:  apt-get install gcc-multilib libfaad-dev:i386
#   Fedora:         dnf install glibc-devel.i686 libstdc++-devel.i686 faad2-devel.i686
#
# AAC decoder.  FAAD2 is the default.  The experimental MIT decoder (Glint,
# see tools/aac_experiment/README.md) is selected with
#
#   AAC=glint GLINT_SOURCE=/path/to/glint-clone ./build_linux.sh i686
#
# which needs python3, a C++17 compiler for the target (g++-multilib on a
# 64-bit Debian/Ubuntu) and a local clone of the pinned Glint repository.
# Nothing of Glint is committed here: the pinned decoder is extracted from
# that clone into the build directory at build time, exactly as the Windows
# experiment does.
#
# Nothing of Apple's is fetched, built or shipped by this script.  The engine
# data comes from the user's own Macintosh, exactly as everywhere else.
set -e

ARCH="${1:-i686}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
CC="${CC:-cc}"
CXX="${CXX:-c++}"
AAC="${AAC:-faad2}"
# The default decoder builds where it always has; another decoder gets its own
# directory, so the two can be compared side by side.
OUT="$ROOT/build/linux-$ARCH"
[ "$AAC" = faad2 ] || OUT="$OUT-$AAC"
mkdir -p "$OUT"

# _GNU_SOURCE for off64_t and mmap64, which glibc hides behind a feature macro
# and bionic declares unconditionally -- so this is invisible until the first
# build against glibc, where it is nine errors in tiger_plat_posix.c.  It has
# to be a compiler flag rather than a #define in the source: the platform seam
# is included well after the system headers.
CFLAGS="-O2 -fno-strict-aliasing -Wno-deprecated-declarations -D_GNU_SOURCE"
CXXFLAGS="-O2 -fno-strict-aliasing -std=c++17 -fPIC"
LDFLAGS="-lpthread -ldl -lm"
# Where floating point happens on 32-bit x86, which matters below.
FPFLAGS=""

case "$ARCH" in
  i686)
    # -m32 both ways: the compiler and the linker each need telling.
    CFLAGS="$CFLAGS -m32"
    CXXFLAGS="$CXXFLAGS -m32"
    LDFLAGS="-m32 $LDFLAGS"
    # gcc's i386 default is x87, whose 80-bit intermediates round differently
    # from the SSE2 doubles MSVC uses on Windows.  The host's own float math
    # never showed the difference (Fred is byte-identical either way), but an
    # all-double AAC decoder does -- see AAC=glint below.
    FPFLAGS="-msse2 -mfpmath=sse"
    # No TIGER_UC.  The guest is i386 and so is this process, so the engine's
    # own instructions are the host's -- call_aligned* become plain calls,
    # because gcc and clang keep the sixteen-byte stack boundary that Mach-O
    # wants and MSVC does not.  See tiger_host_shims.c.
    ;;
  x86_64|aarch64)
    CFLAGS="$CFLAGS -DTIGER_UC"
    UC="${UNICORN_DIR:-$ROOT/android/harness/unicorn}"
    [ -d "$UC/include" ] || { echo "no Unicorn at $UC (set UNICORN_DIR)"; exit 1; }
    CFLAGS="$CFLAGS -I$UC/include"
    LDFLAGS="${UNICORN_LIBRARY:-$UC/build/libunicorn.a} $LDFLAGS"
    ;;
  *) echo "unknown arch '$ARCH' (i686, x86_64 or aarch64)"; exit 1 ;;
esac

# The host source.  Normally the checked-in tree; the Glint build compiles a
# staged copy of it instead, so the backend include can be patched in without
# touching src/.
HOST_SRC="$ROOT/src/tiger_host.c"

case "$AAC" in
  faad2)
    # AAC: FAAD2 in-process, which is the portable backend and the one Android
    # already ships.  Media Foundation is Windows-only and is what the selector
    # falls back to when nothing else is named, so naming one is not optional.
    if [ -n "$FAAD2_DIR" ]; then
        CFLAGS="$CFLAGS -DTIGER_AAC_FAAD -I$FAAD2_DIR/include"
        LDFLAGS="$FAAD2_DIR/libfaad/.libs/libfaad.a $LDFLAGS"
    elif pkg-config --exists faad2 2>/dev/null; then
        CFLAGS="$CFLAGS -DTIGER_AAC_FAAD $(pkg-config --cflags faad2)"
        LDFLAGS="$(pkg-config --libs faad2) $LDFLAGS"
    elif [ -f /usr/include/neaacdec.h ]; then
        CFLAGS="$CFLAGS -DTIGER_AAC_FAAD"
        LDFLAGS="-lfaad $LDFLAGS"
    else
        echo "== no FAAD2 found: building WITHOUT the AAC voices =="
        echo "   Fred, Bruce, Kathy and the rest still speak; Vicki and Alex do"
        echo "   not, because their sample bank is AAC.  Install libfaad-dev, or"
        echo "   set FAAD2_DIR, to include them."
        CFLAGS="$CFLAGS -DTIGER_NO_AAC"
    fi
    ;;
  glint)
    # The experimental MIT decoder.  tools/aac_experiment/glint.py extracts
    # the pinned decoder from a local clone into the build directory and
    # patches a *copy* of src/ to include the Glint backend, exactly as the
    # Windows experiment builds it.  The clone and the checked-in sources are
    # both left as they were.
    GLINT_SOURCE="${GLINT_SOURCE:-$ROOT/build/research/glint}"
    [ -d "$GLINT_SOURCE/.git" ] || { echo "no Glint clone at $GLINT_SOURCE (set GLINT_SOURCE)"; exit 1; }
    STAGE="$OUT/glint"
    rm -rf "$STAGE"
    mkdir -p "$STAGE/host"
    for f in "$ROOT"/src/*; do [ -f "$f" ] && cp "$f" "$STAGE/host/"; done
    python3 -c "import sys; sys.path.insert(0, '$ROOT/tools/aac_experiment')
from pathlib import Path
import glint
glint.prepare(Path('$GLINT_SOURCE'), Path('$STAGE'), Path('$STAGE/host'))"
    # The decoder is all double arithmetic and the bridge quantises to int16
    # exactly once at the end, so where the doubles are rounded decides whether
    # this host and the Windows one agree to the byte.  They do with SSE2; they
    # would not on x87.  The host is built the same way so its own float math
    # cannot reintroduce the difference around the decoder.
    CXXFLAGS="$CXXFLAGS $FPFLAGS -I$STAGE/glint/src"
    CFLAGS="$CFLAGS $FPFLAGS -DTIGER_AAC_GLINT"
    echo "== glint ($GLINT_SOURCE) =="
    echo "   c++:     $CXX"
    echo "   cxxflags: $CXXFLAGS"
    $CXX $CXXFLAGS -c "$STAGE/glint/src/aac_decoder.cpp" -o "$OUT/glint_decoder.o"
    $CXX $CXXFLAGS -c "$STAGE/glint/glint_bridge.cpp" -o "$OUT/glint_bridge.o"
    # libstdc++ linked statically, so the binary needs no 32-bit C++ runtime
    # on the machine it runs on -- only the compiler needs it.
    LDFLAGS="$OUT/glint_decoder.o $OUT/glint_bridge.o -Wl,-Bstatic -lstdc++ -Wl,-Bdynamic $LDFLAGS"
    HOST_SRC="$STAGE/host/tiger_host.c"
    ;;
  *) echo "unknown AAC backend '$AAC' (faad2 or glint)"; exit 1 ;;
esac

echo "== $ARCH =="
echo "   cc:      $CC"
echo "   cflags:  $CFLAGS"
# One translation unit, as on every other platform: tiger_host.c includes the
# rest.  A second object would mean a second copy of every static in it.
$CC $CFLAGS -o "$OUT/tiger_host" "$HOST_SRC" $LDFLAGS
echo "   -> $OUT/tiger_host ($(du -h "$OUT/tiger_host" | cut -f1))"

# The same synthesis API Android uses, with an explicit export list. Clients
# load one engine generation per process and own playback themselves.
$CC $CFLAGS -fPIC -shared -DTIGER_LIB -DTIGER_SHARED \
    -Wl,-soname,libpanthera.so.0 -Wl,--version-script="$ROOT/src/panthera.exports" \
    -o "$OUT/libpanthera.so.0" "$HOST_SRC" $LDFLAGS
ln -sf libpanthera.so.0 "$OUT/libpanthera.so"
mkdir -p "$OUT/include"
cp "$ROOT/src/tiger_host_jni.h" "$OUT/include/panthera.h"
echo "   -> $OUT/libpanthera.so.0 (+ include/panthera.h)"

# The self-tests that need no engine data, which is all of them.  This matters
# for CI in particular: no Apple or Berkeley data may ever go near a build
# server, so the checks that can run there are exactly the ones that carry
# their own inputs -- the number rules, the regex engine and the AAC framing.
echo "== self-tests =="
"$OUT/tiger_host" --numbers-check
"$OUT/tiger_host" --regex-check | tail -3
echo "Built.  Point it at a MacinTalk tree of your own to hear anything:"
echo "  $OUT/tiger_host <MacinTalk> <SpeechDictionary> <Voice.SpeechVoice>"
