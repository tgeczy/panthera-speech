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
# Nothing of Apple's is fetched, built or shipped by this script.  The engine
# data comes from the user's own Macintosh, exactly as everywhere else.
set -e

ARCH="${1:-i686}"
ROOT="$(cd "$(dirname "$0")" && pwd)"
OUT="$ROOT/build/linux-$ARCH"
CC="${CC:-cc}"
mkdir -p "$OUT"

# _GNU_SOURCE for off64_t and mmap64, which glibc hides behind a feature macro
# and bionic declares unconditionally -- so this is invisible until the first
# build against glibc, where it is nine errors in tiger_plat_posix.c.  It has
# to be a compiler flag rather than a #define in the source: the platform seam
# is included well after the system headers.
CFLAGS="-O2 -fno-strict-aliasing -Wno-deprecated-declarations -D_GNU_SOURCE"
LDFLAGS="-lpthread -ldl -lm"

case "$ARCH" in
  i686)
    # -m32 both ways: the compiler and the linker each need telling.
    CFLAGS="$CFLAGS -m32"
    LDFLAGS="-m32 $LDFLAGS"
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

echo "== $ARCH =="
echo "   cc:      $CC"
echo "   cflags:  $CFLAGS"
# One translation unit, as on every other platform: tiger_host.c includes the
# rest.  A second object would mean a second copy of every static in it.
$CC $CFLAGS -o "$OUT/tiger_host" "$ROOT/src/tiger_host.c" $LDFLAGS
echo "   -> $OUT/tiger_host ($(du -h "$OUT/tiger_host" | cut -f1))"

# The same synthesis API Android uses, with an explicit export list. Clients
# load one engine generation per process and own playback themselves.
$CC $CFLAGS -fPIC -shared -DTIGER_LIB -DTIGER_SHARED \
    -Wl,-soname,libpanthera.so.0 -Wl,--version-script="$ROOT/src/panthera.exports" \
    -o "$OUT/libpanthera.so.0" "$ROOT/src/tiger_host.c" $LDFLAGS
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
