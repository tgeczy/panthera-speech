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
#   ./push_apk_data.sh
set -e
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL="*"

ADB="${ADB:-C:/Android/Sdk/platform-tools/adb.exe}"
PKG="com.pantheraspeech.tts"
ENGINE="${ENGINE:-D:/speech-tiger/x86}"
MT="$ENGINE/Speech/Synthesizers/MacinTalk.SpeechSynthesizer/Contents/MacOS/MacinTalk"
SDVER="$ENGINE/SpeechDictionary.framework/Versions/A"
VOICES="$ENGINE/Speech/Voices"

"$ADB" get-state >/dev/null 2>&1 || { echo "no device"; exit 1; }

STAGE="$(mktemp -d)/panthera-stage"
TAR="$STAGE.tar"
trap 'rm -rf "$STAGE" "$TAR"' EXIT

echo "== staging tiger/ layout =="
mkdir -p "$STAGE/tiger/Voices" "$STAGE/tiger/SpeechDictionary.framework/Versions/A/Resources"
cp "$MT" "$STAGE/tiger/MacinTalk"
cp "$SDVER/SpeechDictionary" "$STAGE/tiger/SpeechDictionary.framework/Versions/A/SpeechDictionary"
cp "$SDVER/Resources/"* "$STAGE/tiger/SpeechDictionary.framework/Versions/A/Resources/" 2>/dev/null || true
n=0
for v in "$VOICES"/*.SpeechVoice; do
    name="$(basename "$v")"
    [ "$name" = "Vicki.SpeechVoice" ] && continue
    cp -r "$v" "$STAGE/tiger/Voices/$name"
    n=$((n + 1))
done
echo "  $n voices, $(du -sh "$STAGE" | cut -f1)"

echo "== tar -> base64 -> run-as -> extract into the app's internal files =="
tar --force-local -cf "$TAR" -C "$STAGE" tiger
base64 "$TAR" | "$ADB" shell "run-as $PKG sh -c 'base64 -d > t.tar && rm -rf files/panthera-data && mkdir -p files/panthera-data && tar -x -f t.tar -C files/panthera-data && rm t.tar && echo EXTRACTED'"

echo "== verify (app-side) =="
"$ADB" shell "run-as $PKG sh -c 'echo voices: \$(ls files/panthera-data/tiger/Voices | wc -l); md5sum files/panthera-data/tiger/MacinTalk'"
echo "Now: open Panthera Speech, tap Check Engine, tap Speak a sample."
