#!/usr/bin/env bash
# run_probes.sh -- push the ABI-matched probes and run the Tier-1 battery.
# Detects the ABI, pushes, runs jitprobe (architecture gate) and memprobe
# (the Alex-bank question) for both Lion (700 MB) and Leopard (422 MB) sizes,
# in the engine's real mmap case and the malloc-fallback heap case.
#
#   bash run_probes.sh [serial]
set -u
export MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*'   # stop Git Bash rewriting /data/local/tmp
HERE="$(cd "$(dirname "$0")" && pwd)"
HERE_W="$(cd "$(dirname "$0")" && pwd -W)"           # Windows-form for adb push SOURCE
SEL=""; [ $# -ge 1 ] && SEL="-s $1"
ADB="adb $SEL"
TMP=/data/local/tmp
BACK=$TMP/alexbank.bin       # zero-filled stand-in; paging behaviour is content-free
RESULTS="$HERE/results"; mkdir -p "$RESULTS"
STAMP=$(date +%Y%m%d-%H%M%S)
LOG="$RESULTS/watch-$STAMP.txt"

log(){ echo "$@" | tee -a "$LOG"; }
dev(){ $ADB shell "$@"; }

log "#### Tier-1 run $STAMP ####"
ABILIST=$($ADB shell getprop ro.product.cpu.abilist | tr -d '\r')
log "abilist: $ABILIST"
case "$ABILIST" in
  *arm64-v8a*) ABI=arm64-v8a ;;
  *armeabi-v7a*) ABI=armeabi-v7a ;;
  *) log "no arm ABI in '$ABILIST' -- stop"; exit 1 ;;
esac
log "using ABI: $ABI"

for P in jitprobe memprobe embench; do
  $ADB push "$HERE_W/build/$P.$ABI" "$TMP/$P" >/dev/null
  dev "chmod 755 $TMP/$P"
done
$ADB push "$HERE_W/build/workload.bin" "$TMP/workload.bin" >/dev/null

log ""; log "======== FACTS ========"
bash "$HERE/facts.sh" ${1:-} 2>&1 | tee -a "$LOG"

log ""; log "======== JITPROBE (architecture gate) ========"
dev "$TMP/jitprobe" 2>&1 | tee -a "$LOG"

log ""; log "======== EMUBENCH (i386-under-Unicorn speed) ========"
log "desktop refs (same blob): native-i386 95.6 Msteps/s, Unicorn 23.5 Msteps/s"
log "(divide device Msteps/s into 95.6 for the multiplier on the engine's 27ms slice)"
dev "$TMP/embench $TMP/workload.bin 40000000 3" 2>&1 | tee -a "$LOG"

log ""; log "======== backing file ($BACK) ========"
have=$(dev "ls -l $BACK 2>/dev/null | awk '{print \$5}'" | tr -d '\r')
if [ "${have:-0}" -lt 734003200 ]; then
  log "creating 700 MB backing file (one-time)…"
  dev "dd if=/dev/zero of=$BACK bs=1048576 count=700 2>&1 | tail -1"
else
  log "backing file present ($have bytes)"
fi

run_mem() {   # mode size_mb pattern secs
  log ""; log "-------- memprobe $1 ${2}MB $3 --------"
  if [ "$1" = mmap ]; then
    dev "$TMP/memprobe mmap $BACK $2 $3 ${4:-20}" 2>&1 | tee -a "$LOG"
  else
    dev "$TMP/memprobe heap $2 $3 ${4:-20}" 2>&1 | tee -a "$LOG"
  fi
}

log ""; log "======== MEMPROBE: the engine's real case (file-backed mmap) ========"
run_mem mmap 700 touchall            # Lion Alex: can we even reach 700 MB resident?
run_mem mmap 700 sparse 20           # sustained speech: major-fault rate + plateau
run_mem mmap 422 touchall            # Leopard Alex
run_mem mmap 422 sparse 20

log ""; log "======== MEMPROBE: the malloc-fallback case (anon heap -> zram) ========"
log "(this one CAN trip lmkd; if the run vanishes, that's the finding)"
run_mem heap 422 touchall
run_mem heap 700 touchall

log ""; log "#### done -> $LOG ####"
echo "results: $LOG"
