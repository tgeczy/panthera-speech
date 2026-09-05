#!/bin/sh
# Push the armeabi-v7a tiger_host and Fred's engine files to a connected
# Android device (the watch), render "Hello there." on-device, pull the WAV
# back, and byte-diff it against the desktop-UC reference.
#
# The point of the byte-diff: the device and the desktop both run the SAME
# Unicorn/TCG over the SAME i386 engine, so Fred -- pure formant synthesis, all
# in TCG's portable softfloat, none of it on the host's own FP -- should come
# back byte-for-byte identical.  That is a far stronger check than "it sounds
# right": a single differing sample says the emulation diverged, and where.
#
#   ./push_and_run.sh                 # uses the default engine paths below
#   ENGINE=D:/speech-tiger/x86 ./push_and_run.sh
#
# Nothing of Apple's is shipped anywhere: these files go from Tomi's own disk to
# Tomi's own watch, and the WAV is his own render on his own device.
set -e
export MSYS_NO_PATHCONV=1

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BIN="$ROOT/build/ndk/tiger_host"
ORACLE="$ROOT/build/ndk/fred-desktop-uc.wav"

ENGINE="${ENGINE:-D:/speech-tiger/x86}"
MT="$ENGINE/Speech/Synthesizers/MacinTalk.SpeechSynthesizer/Contents/MacOS/MacinTalk"
SD="$ENGINE/SpeechDictionary.framework/Versions/A/SpeechDictionary"  # not the symlink
FV="$ENGINE/Speech/Voices/Fred.SpeechVoice"

ADB="${ADB:-C:/Android/Sdk/platform-tools/adb.exe}"
DEV=/data/local/tmp/panthera

for f in "$BIN" "$MT" "$SD" "$FV"; do
    [ -e "$f" ] || { echo "missing: $f"; exit 1; }
done
[ -f "$ORACLE" ] || { echo "no oracle at $ORACLE; render it on the desktop first"; exit 1; }
[ -x "$ADB" ] || { echo "no adb at $ADB"; exit 1; }

echo "== device =="
"$ADB" get-state >/dev/null 2>&1 || { echo "no device; connect and enable ADB, then retry"; exit 1; }
ABIS="$("$ADB" shell getprop ro.product.cpu.abilist | tr -d '\r')"
echo "  abilist: $ABIS"
case "$ABIS" in
    *armeabi-v7a*) : ;;
    *) echo "  WARNING: this device does not list armeabi-v7a; the binary may not run"; ;;
esac

echo "== push =="
"$ADB" shell "mkdir -p $DEV/Fred.SpeechVoice" >/dev/null
"$ADB" push "$BIN" "$DEV/tiger_host" | tail -1
"$ADB" push "$MT"  "$DEV/MacinTalk" | tail -1
"$ADB" push "$SD"  "$DEV/SpeechDictionary" | tail -1
"$ADB" push "$FV/." "$DEV/Fred.SpeechVoice" | tail -1
"$ADB" shell "chmod 755 $DEV/tiger_host"

echo "== render Fred on device (TIGER_SPEED=1, honest realtime) =="
# Speed 1 is true real time: it separates "is the emulation correct" from "can
# the watch keep up", which are the two different questions.  The desktop
# oracle is byte-identical at speed 1 and 128, so the byte-diff below is valid
# at speed 1.  Re-run without TIGER_SPEED (defaults to 128) afterwards for the
# perf number -- how much faster than real time this watch renders Fred.
"$ADB" shell "cd $DEV && TIGER_SPEED=1 ./tiger_host ./MacinTalk ./SpeechDictionary ./Fred.SpeechVoice > run.log 2>&1; echo exit=\$?"
echo "  --- last lines of on-device log ---"
"$ADB" shell "tail -6 $DEV/run.log" | sed 's/^/  /'

echo "== pull and compare =="
OUT="$ROOT/build/ndk/fred-device.wav"
"$ADB" pull "$DEV/tiger-out.wav" "$OUT" | tail -1 || { echo "no WAV produced; see run.log above"; exit 1; }
echo "  oracle: $(md5sum "$ORACLE" | cut -d' ' -f1)  $(wc -c < "$ORACLE") bytes"
echo "  device: $(md5sum "$OUT"    | cut -d' ' -f1)  $(wc -c < "$OUT") bytes"
if cmp -s "$ORACLE" "$OUT"; then
    echo "  RESULT: BYTE-IDENTICAL -- Fred on the watch matches the desktop emulation exactly."
else
    echo "  RESULT: DIFFER -- the device render is not byte-identical."
    echo "  first difference:"; cmp "$ORACLE" "$OUT" | sed 's/^/    /'
    echo "  (Fred is pure TCG softfloat; a difference points at a plat-layer bug in"
    echo "   the engine's file load or memory mapping, not at rounding.)"
fi
