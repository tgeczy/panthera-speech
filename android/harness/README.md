# Panthera Android — Tier-1 on-device measurement harness

Answers the two questions that gate the Android port, with hard numbers per
real device instead of literature guesses:

1. **Does a watch hold Alex's sample bank?** (`memprobe`) — the sleeper gate.
2. **Can the device JIT at all?** (`jitprobe`) — the architecture gate; the whole
   hybrid design rests on Unicorn/TCG, which writes and runs code.

Plus `facts.sh` — the zero-build CPU/RAM/thermal snapshot every projection needs.

The emulation-throughput *multiplier* (`emubench`) is **Tier-1b, deferred** — see
below.

## Run it

```
bash run_probes.sh [serial]      # facts + jitprobe + memprobe, end to end
```
Results land in `results/watch-<stamp>.txt`. Watches connect over Wi-Fi
(`adb connect <ip>:5555`) or USB; phones over USB.

## What each probe measures

### facts.sh  (no build)
Leads with `ro.product.cpu.abilist` — **Wear OS on A53 has historically shipped a
32-bit userspace, so we never assume arm64**; the ABI list picks the binary.
Then SoC/board, per-core max clock, core topology (CPU-part hex: 0xd03=A53…),
RAM/Swap, zram config, and a **baseline thermal reading before any load**.

### jitprobe  (NDK only)
Tries two ways to run freshly-written native code and reports which works:
- **RWX** — map writable+executable at once (often denied on modern Android).
- **RW→RX** — map RW, write, `mprotect` to R+X (the W^X dance TCG supports).

A *fail* is decisive: if even a shell process can't, the app never will, and the
port would need a slow interpreter fallback on that device.

### memprobe  (NDK only)
The engine memory-maps its voice data (`tiger_host_files.c` `sh_mmap`:
`CreateFileMapping`+`MapViewOfFile`, read-only, demand-paged). So "700 MB Alex"
is 700 MB of *address space* — resident pages arrive as touched, clean file
pages evict under pressure **without** reaching zram. memprobe measures both the
real case and the malloc fallback:
- `mmap … touchall` — read every page once: can the device reach that resident
  size, and how fast does it page it in (flash bandwidth)?
- `mmap … sparse`   — random 32 KB runs for N s: sustained-speech **major-fault
  rate** (flash latency in the speech path) and the resident plateau.
- `heap … touchall` — the fallback: anonymous pages that DO compress into zram
  and feed lmkd. Pessimistic bound; may get the process killed (that's a result).

Reports RSS / peak RSS, major+minor faults, zram swap-in/out + mem_used deltas,
and the `/proc/pressure/memory` line.

## Caveats baked in (upper bounds, stated on purpose)
- **SELinux domain.** Shell-spawned processes run in the permissive `shell`
  domain, not `untrusted_app`. Passing here proves the CPU/kernel allow it; the
  app-sandbox verdict needs a real APK.
- **lmkd registration.** A shell process isn't registered with lmkd
  (oom_score_adj 0), so it survives pressure the real TTS service wouldn't. If a
  run vanishes, `adb logcat -d -s lowmemorykiller lmkd` says why.
- **Cache warmth.** memprobe `posix_fadvise(DONTNEED)`s the backing file before
  mapping so major-faults are genuine flash reads, not cache hits.

## emubench (Tier-1b) — BUILT
Runs one representative i386 workload (`src/workload.c` → flat `build/workload.bin`)
three ways so `device_emu ÷ desktop_native` replaces the TCG-penalty (~5–15×) and
phone-factor (~3–4×) guesses with one measured number:

- **native i386 on desktop** — `src/native_ref.c`, MSVC Hostx64/x86 → `native_ref.exe`
- **Unicorn on desktop** — `uc_ref.py`, Python `unicorn` 2.1.4
- **Unicorn on device** — `src/embench_driver.c` statically linked against the
  NDK-cross-built Unicorn, one binary per ABI (`embench.arm64-v8a` / `.armeabi-v7a`)

All three run the identical blob and produce the identical result word
(`0x6ea01020`) — a built-in check that the device's TCG computed faithfully.

**Desktop reference (40 M steps, cache-missing pointer-chase):**
native i386 **95.6 Msteps/s**, Unicorn **23.5 Msteps/s** → **TCG penalty ≈ 4.07×**
(low end of the literature range, as expected for memory-latency-bound code where
the miss dominates both native and emulated). Divide the device's Msteps/s into
95.6 for the combined multiplier to apply to the engine's measured 27 ms slice.
The workload is integer/pointer/branch only (no FP — FP traps to native NEON in
the design); clang lowered the if/else to `cmov`, so hard-to-predict branches are
slightly understated while the dominant cache-miss chain is exact.

**How the Unicorn cross-build was unblocked** (it had failed on the Windows host):
Unicorn's CMake shells `execute_process(COMMAND sh …/qemu/configure)`. Three fixes,
run under **Cygwin** for a real POSIX `sh`:
1. **CRLF** — `git autocrlf` had mangled `qemu/configure` to CRLF (`$'\r'` errors);
   re-clone with `-c core.autocrlf=false -c core.eol=lf`.
2. **`strings`** — Cygwin has no binutils; shim → NDK `llvm-strings` (`shims/strings`,
   converts args via `cygpath -w`).
3. **`pkg-config`** — Unicorn bundles `glib_compat` and never pkg-configs glib;
   configure only checks the binary exists → benign stub (`shims/pkg-config`).
Run the whole build from a *clean Cygwin PATH* (`/usr/local/bin:/usr/bin:/bin` +
shims) so PATH translates both ways across the native cmake — see
`build_unicorn_cyg.sh` (`… <abi> build`). Both ABIs build in ~1 min each.

## Files
- `facts.sh`, `run_probes.sh` — orchestration
- `src/memprobe.c`, `src/jitprobe.c` — the probes
- `build/` — cross-compiled per ABI (`*.arm64-v8a`, `*.armeabi-v7a`)
- `build_probes.sh` — NDK cross-compile
- `build_unicorn.sh`, `build_unicorn.log` — the deferred emubench build
