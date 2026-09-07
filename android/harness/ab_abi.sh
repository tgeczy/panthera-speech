#!/bin/sh
# Run the device suite twice on ONE device, once per ABI.
#
# Every other comparison this project can make confounds the ABI with the CPU:
# a 32-bit Cortex-A53 watch against a 64-bit Cortex-X4 phone says nothing about
# the port, only about the silicon.  A dual-ABI device is the only control
# there is -- same core, same clocks, same thermal state, two instruction sets
# -- and the Galaxy S22 (Snapdragon 8 Gen 1) is one:
#
#     ro.product.cpu.abilist = arm64-v8a,armeabi-v7a,armeabi
#
# Note the order.  arm64 is FIRST, which is the package manager's preference,
# which is why a release APK carrying a half-finished arm64 library would take
# the working 32-bit one away from exactly this phone.  That is the fact this
# script exists to keep honest.
#
# The install is forced per ABI with `--abi`, and the app is removed between
# runs rather than replaced: switching ABI under `-r` leaves the previously
# extracted native library in place on some builds, and a measurement of the
# wrong library is worse than no measurement.  Removing it takes the pushed
# engine data with it, so the data goes back each time.
#
#   ANDROID_SERIAL=R5CT20CQE3N ./ab_abi.sh [tiger|leopard]
set -e
export MSYS_NO_PATHCONV=1

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
ADB="${ADB:-C:/Android/Sdk/platform-tools/adb.exe}"
PKG="com.pantheraspeech.tts"
GEN="${1:-tiger}"
APKDIR="$ROOT/src/platforms/android/app/build/outputs/apk"
APP="$APKDIR/debug/app-debug.apk"
TEST="$APKDIR/androidTest/debug/app-debug-androidTest.apk"

[ -f "$APP" ]  || { echo "no app APK; run gradle assembleDebug"; exit 1; }
[ -f "$TEST" ] || { echo "no test APK; run gradle assembleDebugAndroidTest"; exit 1; }
# MSYS_NO_PATHCONV is set above because the `adb shell` arguments below must
# reach the device unconverted -- but adb itself is a WINDOWS binary, and a
# POSIX path handed to `adb install` is then read relative to the current
# drive root and simply not found.  So the two file arguments, and only those,
# are converted back.
APP="$(cygpath -w "$APP")"
TEST="$(cygpath -w "$TEST")"
"$ADB" get-state >/dev/null 2>&1 || { echo "no device (set ANDROID_SERIAL)"; exit 1; }

echo "== device =="
"$ADB" shell "getprop ro.product.model; getprop ro.product.cpu.abilist"

case "$GEN" in
  tiger)   ENGINE="D:/speech-tiger/x86" ;;
  leopard) ENGINE="D:/speech-leopard"   ;;
  *) echo "unknown generation '$GEN'"; exit 1 ;;
esac

for ABI in arm64-v8a armeabi-v7a; do
    echo
    echo "############ $ABI ############"
    "$ADB" uninstall "$PKG" >/dev/null 2>&1 || true
    "$ADB" uninstall "$PKG.test" >/dev/null 2>&1 || true
    "$ADB" install --abi "$ABI" "$APP"  >/dev/null
    "$ADB" install "$TEST" >/dev/null
    # Which library the loader actually chose -- the only proof that `--abi`
    # was honoured, and cheap enough to check every time.
    echo -n "  loaded: "
    "$ADB" shell "ls /data/app/*/$PKG-*/lib/ 2>/dev/null | head -1" | tr -d '\r'

    GEN="$GEN" ENGINE="$ENGINE" SKIP="" "$HERE/push_apk_data.sh" >/dev/null 2>&1 \
        || { echo "  data push failed"; continue; }
    "$ADB" shell "run-as $PKG sh -c 'mkdir -p shared_prefs && cat > shared_prefs/pantheraspeech.xml'" <<XML
<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <boolean name="engine_verified" value="true" />
    <string name="engine_generation">$GEN</string>
</map>
XML

    "$ADB" logcat -c
    START=$(date +%s)
    RESULT=$("$ADB" shell "am instrument -w $PKG.test/$PKG.EngineSmokeTest" 2>&1 | tail -3)
    END=$(date +%s)
    echo "  result: $RESULT"
    echo "  wall:   $((END - START))s"
    echo "  frames and hashes:"
    "$ADB" logcat -d -s PantheraTest 2>&1 | grep -E "file 1:|through AAC|generation under" \
        | sed 's/^/    /'
    echo "  engine timings:"
    "$ADB" logcat -d 2>&1 | grep -oE "utterance done slices=[0-9]+ frames=[0-9]+ first=[0-9]+ms aac=[0-9]+ms" \
        | head -4 | sed 's/^/    /'
done
