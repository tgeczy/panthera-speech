#!/usr/bin/env bash
# facts.sh -- zero-build device facts. Run first, the instant a device connects.
# Leads with the ABI list (Wear OS on A53 has shipped 32-bit userspace; never
# assume arm64), then CPU identity, clocks, RAM, zram/swap config, and a
# baseline thermal reading before any load.  Everything the memory and
# emulation projections need that costs nothing to get.
#
#   bash facts.sh [serial]
set -u
ADB="adb"
[ $# -ge 1 ] && ADB="adb -s $1"

sh() { $ADB shell "$@"; }

echo "================ DEVICE FACTS ================"
echo "-- model / build --"
sh getprop ro.product.manufacturer
sh getprop ro.product.model
sh getprop ro.build.version.release
sh getprop ro.build.version.sdk
sh getprop ro.build.characteristics      # 'watch' for Wear OS

echo "-- ABI (decides which probe binary to push) --"
sh getprop ro.product.cpu.abilist
sh getprop ro.product.cpu.abi

echo "-- SoC / board --"
sh getprop ro.soc.manufacturer
sh getprop ro.soc.model
sh getprop ro.board.platform

echo "-- CPU topology (0xd03=A53, 0xd05=A55, 0xd0b=A78, 0xd44=X2 ...) --"
sh "cat /proc/cpuinfo | grep -E 'processor|CPU part|CPU implementer|Features' "
echo "-- core count --"
sh "cat /sys/devices/system/cpu/possible"

echo "-- max clock per core (kHz) --"
sh "for c in /sys/devices/system/cpu/cpu[0-9]*; do \
      echo -n \"\$c: \"; cat \$c/cpufreq/cpuinfo_max_freq 2>/dev/null || echo n/a; done"

echo "-- RAM / swap (the Alex-bank question) --"
sh "cat /proc/meminfo | grep -E 'MemTotal|MemAvailable|SwapTotal|SwapFree'"
echo "-- zram --"
sh "cat /sys/block/zram0/disksize 2>/dev/null; cat /sys/block/zram0/mm_stat 2>/dev/null"

echo "-- memory pressure (idle baseline) --"
sh "cat /proc/pressure/memory 2>/dev/null"

echo "-- thermal (baseline, before load) --"
sh "for z in /sys/class/thermal/thermal_zone*; do \
      t=\$(cat \$z/type 2>/dev/null); v=\$(cat \$z/temp 2>/dev/null); \
      echo \"\$z \$t \$v\"; done"

echo "-- writable exec dir for pushes --"
sh "ls -ld /data/local/tmp"
echo "=============================================="
