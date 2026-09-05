# Panthera on Android — Phase 0 feasibility (measured)

**Verdict: GO.** The two fears that could have killed an Android port were tested
on real hardware, not reasoned about, and both came back fine. This document is
the decision record and the numbers behind it.

## The plan in one paragraph

The Mac speech engines are i386 machine code. On Windows and (natively) on x86
Linux the host *calls them directly*. On ARM it can't, so the engine runs inside
the [Unicorn](https://www.unicorn-engine.org/) CPU emulator (QEMU TCG) while the
~200 host shims and the heavy DSP (AAC decode, FFT/vDSP) stay **native ARM** —
the "hybrid" architecture. Two questions gate it: **can an Android device JIT at
all** (Unicorn needs to write and run code), and **does Alex's large sample bank
fit** a memory-constrained watch. Phase 0 answered both by cross-compiling
microbenchmarks and running them on three devices.

## The three devices

| Device | SoC | Cores | RAM | ABI |
|---|---|---|---|---|
| Fossil Gen 6 | SDW4100-class | 4× A53 @ 2.0 GHz (in-order) | 1.0 GB | armeabi-v7a (32-bit) |
| Wear "elite" (SM-L350) | "vienna" | 1× A78 @ 2.1 + 4× A55 | 1.7 GB | armeabi-v7a (32-bit) |
| Galaxy S22 (SM-S901U1) | SD 8 Gen 1 | 1× X2 @ 3.0 + 3× A710 @ 2.5 + 4× A510 | 7.4 GB | arm64-v8a |

Note both watches ship a **32-bit userspace** even on 64-bit-capable silicon —
the port must build `armeabi-v7a`, not assume `arm64`.

## Gate 1 — can it JIT?  YES, on all three

`jitprobe` maps freshly written native code two ways (RWX, and the W^X
`RW→mprotect(R+X)` dance TCG uses) and runs it. **Both modes succeeded on every
device**, including the 32-bit A53 watch. Unicorn/TCG can run. (Caveat: probes
run in the permissive `shell` SELinux domain; the production app runs in
`untrusted_app` and must be confirmed with a real APK — but a *shell* failure
would have been decisive, and there was none.)

## Gate 2 — does Alex's bank fit?  YES — it never needed to

The engine memory-maps its voice data read-only (`tiger_host_files.c`), so a
"700 MB Alex" is 700 MB of *address space*, demand-paged. `memprobe` mmap'd a
700 MB bank and swept it:

| Device | peak resident | zram swap-out | notes |
|---|---|---|---|
| Fossil Gen 6 (1 GB) | **258 MB** | 5,269 pages | clean pages evicted; no OOM |
| Wear elite (1.7 GB) | 319 MB | **0** | full headroom |
| Galaxy S22 (7.4 GB) | 703 MB (fully resident) | 0 | plenty of RAM |

On the **1 GB** watch, touching all 700 MB peaked at ~¼ GB resident — clean
file pages evict under pressure instead of accumulating, so the bank never has to
be RAM-resident. The 700-MB-vs-1-GB fear is simply not a wall. The residual cost
is flash major-faults in the speech path, which warm out with locality.

## The emulation speed curve — and the one finding that matters

Same i386 workload (a cache-missing pointer chase, integer/branch — a faithful
proxy for the engine's emulated control code), run under Unicorn on each core.
All produced the identical result word, confirming the emulation is faithful.

| Core | µarch | Clock | Msteps/s | ×desktop-native |
|---|---|---|---|---|
| S22 X2 | out-of-order prime | 3.0 GHz | ~6+ (inferred*) | ~15× |
| S22 A710 | out-of-order mid | 2.5 GHz | **5.0** | 19× |
| Elite A78 | out-of-order big | 2.1 GHz | **2.9** | 33× |
| Gen 6 A53 | **in-order** | 2.0 GHz | **1.4** | 68× |
| S22 A510 | **in-order** little | 1.79 GHz | **1.0** | 96× |

*Reference: desktop x86 native 95.6 Msteps/s, desktop Unicorn 23.5 (TCG penalty
only ~4× on this memory-bound code). *The X2 pins offline from a shell (idle
hot-unplug); the real foreground app gets it, so 5.0 is a conservative phone
floor.

**The finding: emulation speed is governed by microarchitecture, not clock.** At
near-identical clocks the out-of-order A78 (2.9) doubles the in-order A53 (1.4),
and the A710 (5.0) is five times the A510 (1.0) — because out-of-order execution
hides exactly the branchy dispatch TCG is built on. Every out-of-order core clears
Alex-class emulation with realtime margin; every in-order core sits near
1 Msteps/s no matter its GHz.

## What that means for the voices

- **Phones** (any recent big core): Alex renders **~5–10× realtime**. Easy.
- **Big-core watches** (A78-class): Alex **~2–5× realtime**. Comfortable.
- **In-order watches** (A53-class): Alex **~1.2–3× realtime** — borderline but
  viable, tighter under sustained load / thermal.
- **Formant voices (Fred and the gala family)** — no AAC, no FFT, kilobytes of
  data — are **trivially realtime everywhere**, watches included.

## Note for users (ships in the app)

> Panthera's Mac voices are emulated on your phone or watch — the engine is
> Intel code running on an ARM chip. That emulation has a fixed overhead we can't
> optimize away beyond what we already do. What decides whether Alex keeps up is
> the *kind* of CPU core, more than its speed or your RAM: modern out-of-order
> cores (most phones, and higher-end watches) run Alex comfortably in real time;
> older in-order cores — common on smaller and budget watches — carry more of
> that overhead and may lag on Alex, especially on long passages. If Alex lags on
> your device, you have two good options: switch to a **formant voice** (Fred and
> friends), which is light and fast everywhere, or use a **smaller Alex bank**
> (Leopard's ~422 MB instead of Lion's ~700 MB). RAM itself is rarely the
> problem — a 1 GB watch held the full bank fine in testing.

## Reproducing this

The harness is in [`android/harness/`](../android/harness/). It needs the Android
NDK, and Unicorn is cross-built under Cygwin (a real POSIX `sh` for QEMU's
`configure`; see `build_unicorn_cyg.sh`). `run_probes.sh <serial>` runs the whole
battery — `facts` → `jitprobe` → `emubench` → `memprobe` — and auto-detects the
ABI. Two Git-Bash traps that also bite the Windows host build: keep
`MSYS_NO_PATHCONV=1` set (so `/data/local/tmp` isn't rewritten) and use `pwd -W`
for `adb push` sources.

## Next: Tier-2

The microbench is the last proxy. Tier-2 renders the **real engine** on-device
for a true per-voice number. The base is the **native i386 ELF host** — the same
deliverable as native Linux support ([Devin's pack](https://github.com/devinprater/retro-tts-pack)
consumes it): serve mode is nearly OS-clean, so the port surface is
`VirtualAlloc→mmap`, Win threads→pthreads, Media Foundation AAC→libavcodec/
AMediaCodec. On x86 Linux the engine runs native; on Android ARM the same host
runs it under Unicorn with native shim traps. First increment: the Unicorn loader
executing the real **Fred** engine's init (formant, no AAC/vDSP — isolates the
emulation seam) on the desktop, then cross-compiled.
