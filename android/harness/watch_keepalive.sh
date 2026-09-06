#!/bin/sh
# Make a Wear OS watch stay reachable over adb, and pin it to a fixed port.
#
# Wireless debugging on Wear OS drops constantly and then needs a full reboot to
# come back, which is not a small annoyance when every measurement means a push
# and a run.  Two separate things cause it:
#
#  - **Wear OS turns Wi-Fi off on purpose.**  Google Play Services drops the
#    Wi-Fi connection whenever Bluetooth to the phone can carry the traffic, to
#    save battery.  There is a Developer options toggle for this -- "Turn off
#    automatic Wi-Fi" -- and it has to be set ON THE WATCH; no adb command
#    substitutes for it.  Set Wi-Fi to "Always on" rather than "Automatic" while
#    you are at it (Settings > Connectivity > Wi-Fi).
#
#  - **The port moves.**  Android 11+ wireless debugging picks a random port in
#    the 3xxxx-4xxxx range every time it restarts, which is why `adb mdns
#    services` keeps showing a new one -- sometimes with no address at all.
#    `adb tcpip 5555` switches the daemon to the classic fixed port, so
#    reconnecting is `adb connect <ip>:5555` forever after rather than a hunt.
#    It does not survive a watch reboot; run this again after one.
#
# What is left over is sleep, so the settings below keep the screen awake while
# charging and stop the display timing out.  Debug on the charger.
#
#   ./watch_keepalive.sh [serial-or-ip:port]
set -e

ADB="${ADB:-C:/Android/Sdk/platform-tools/adb.exe}"
DEV="$1"
if [ -n "$DEV" ]; then SEL="-s $DEV"; else SEL=""; fi

# shellcheck disable=SC2086
run() { "$ADB" $SEL "$@"; }

run get-state >/dev/null 2>&1 || { echo "no device (pass a serial or ip:port)"; exit 1; }

echo "== device =="
run shell "getprop ro.product.model; getprop ro.product.cpu.abilist"

echo "== keeping it awake =="
# 3 = stay on for AC and USB charging.  The screen staying on is what keeps the
# radio up; a watch that dims is a watch about to vanish off adb.
run shell "settings put global stay_on_while_plugged_in 3" || true
run shell "settings put system screen_off_timeout 1800000" || true
# Legacy on modern Android and harmless where it is ignored; 2 is "never sleep".
run shell "settings put global wifi_sleep_policy 2" || true

echo "== current Wi-Fi address =="
IP=$(run shell "ip -f inet addr show wlan0 2>/dev/null | sed -n 's/.*inet \\([0-9.]*\\).*/\\1/p'" | tr -d '\r')
echo "  ${IP:-（no wlan0 address -- is Wi-Fi on?）}"

echo "== pinning adb to port 5555 =="
run tcpip 5555 || true
sleep 2
if [ -n "$IP" ]; then
    "$ADB" connect "$IP:5555" || true
    echo
    echo "From now on:  adb connect $IP:5555"
    echo "After a watch REBOOT, run this script again -- tcpip does not persist."
fi

cat <<'NOTE'

Still to do on the watch itself, and adb cannot do these for you:
  Developer options  -> "Turn off automatic Wi-Fi"   ENABLE   (the big one)
  Settings > Connectivity > Wi-Fi -> "Always on"     (not "Automatic")
  When the debug prompt appears     -> "Always allow from this computer"
NOTE
