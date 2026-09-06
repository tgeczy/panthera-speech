#!/bin/sh
# Load the Tiger engine data into the installed Panthera Speech app so it can
# render on the watch -- the developer stand-in for a user importing data.
#
# It goes into the app's INTERNAL files dir (/data/data/<pkg>/files), not the
# external one: on Android 11+ an app cannot read files adb-pushed into its
# Android/data external dir (they land shell-owned and the FUSE view denies the
# app), so the reliable path is to hand the bytes to the app itself.  We do that
# by streaming a tar through `run-as`, base64-encoded so adb's line-ending
# translation cannot corrupt the binary, decoded to a file on-device and
# extracted there.  PantheraEngine looks in the internal root as well as the
# external one, so this Just Works.
#
# Nothing of Apple's is redistributed: the bytes go from Tomi's own disk into an
# app sandbox only this device can read.  Vicki is skipped (her bank is AAC,
# which the Fred-first build stubs out).
#
# Any generation, not just Tiger:
#
#   ./push_apk_data.sh                                    # tiger, the default
#   GEN=leopard ENGINE=D:/speech-leopard ./push_apk_data.sh
#
# Generations are added rather than replaced, so pushing Leopard leaves Tiger
# standing -- the app looks each one up separately and the user picks between
# them.  SKIP names voice bundles to leave behind: Vicki and Alex are both AAC
# (they share the `meow` engine) and the Fred-first build stubs that out, and
# Alex is 670 MB besides, which is not something to move twice by accident.
set -e
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL="*"

ADB="${ADB:-C:/Android/Sdk/platform-tools/adb.exe}"
PKG="com.pantheraspeech.tts"
GEN="${GEN:-tiger}"
ENGINE="${ENGINE:-D:/speech-tiger/x86}"
SKIP="${SKIP:-Vicki.SpeechVoice Alex.SpeechVoice}"
MT="$ENGINE/Speech/Synthesizers/MacinTalk.SpeechSynthesizer/Contents/MacOS/MacinTalk"
SDVER="$ENGINE/SpeechDictionary.framework/Versions/A"
VOICES="$ENGINE/Speech/Voices"

"$ADB" get-state >/dev/null 2>&1 || { echo "no device"; exit 1; }

STAGE="$(mktemp -d)/panthera-stage"
TAR="$STAGE.tar"
trap 'rm -rf "$STAGE" "$TAR"' EXIT

echo "== staging $GEN/ layout from $ENGINE =="
[ -f "$MT" ] || { echo "no MacinTalk at $MT"; exit 1; }
mkdir -p "$STAGE/$GEN/Voices" "$STAGE/$GEN/SpeechDictionary.framework/Versions/A/Resources"
cp "$MT" "$STAGE/$GEN/MacinTalk"
cp "$SDVER/SpeechDictionary" "$STAGE/$GEN/SpeechDictionary.framework/Versions/A/SpeechDictionary"
cp "$SDVER/Resources/"* "$STAGE/$GEN/SpeechDictionary.framework/Versions/A/Resources/" 2>/dev/null || true
# The C++ runtime, where the generation has one.  Leopard and later import
# GCC's libstdc++ for std::string, the list helpers and -- the one that bites --
# __dynamic_cast and the RTTI that makes it answer anything but null.  Without
# it that symbol falls through to a stub returning 0, the engine takes the null
# for a cast result and dereferences it, and the worker dies reading 0x1c.
# Tiger imports none of this, which is why nobody missed it until Leopard.
for lib in libc++abi.dylib libstdc++.6.0.9.dylib libstdc++.6.0.4.dylib            libstdc++.6.dylib; do
    for src in "$ENGINE/$lib" "$ENGINE/../$lib" "$ENGINE/usr/lib/$lib"; do
        [ -f "$src" ] || continue
        cp "$src" "$STAGE/$GEN/$lib"
        echo "  runtime: $lib"
        break
    done
done

n=0
for v in "$VOICES"/*.SpeechVoice; do
    name="$(basename "$v")"
    skip=""
    for s in $SKIP; do [ "$name" = "$s" ] && skip=1; done
    [ -n "$skip" ] && continue
    cp -r "$v" "$STAGE/$GEN/Voices/$name"
    n=$((n + 1))
done
echo "  $n voices, $(du -sh "$STAGE" | cut -f1)"

echo "== tar -> base64 -> run-as -> extract into the app's internal files =="
tar --force-local -cf "$TAR" -C "$STAGE" "$GEN"
base64 "$TAR" | "$ADB" shell "run-as $PKG sh -c 'base64 -d > t.tar && mkdir -p files/panthera-data && rm -rf files/panthera-data/$GEN && tar -x -f t.tar -C files/panthera-data && rm t.tar && echo EXTRACTED'"

echo "== verify (app-side) =="
"$ADB" shell "run-as $PKG sh -c 'echo generations: \$(ls files/panthera-data); echo voices: \$(ls files/panthera-data/$GEN/Voices | wc -l); md5sum files/panthera-data/$GEN/MacinTalk'"
echo "Now: open Panthera Speech, tap Check Engine, tap Speak a sample."
