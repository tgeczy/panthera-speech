# Running Snow Leopard's MacinTalk as native code

Notes from making Apple's Mac OS X 10.6 speech engine run in a Windows
process. **Read `macintalk-3.3.md` first** — the loader, the twelve-function
interface and the fake AUGraph are described there and are not repeated —
and then `macintalk-3.6.md` and `macintalk-4.0.md`, because this document is
almost entirely about which of those two 10.6 is behaving like at any given
moment.

MacinTalk **3.10** is what the binary calls itself:

```
10.5   /SourceCache/SpeechSynthesis/SpeechSynthesis-3.6.59/…
10.6   /SourceCache/SpeechSynthesis/SpeechSynthesis-3.10.35/…
10.7   /SourceCache/SpeechSynthesis_MacInTalk/SpeechSynthesis-4.0.74/…
```

Note what has *not* happened yet in the middle line: 10.6 is still inside the
`SpeechSynthesis` project. The split into `SpeechSynthesis_MacInTalk` is 10.7's,
and it lines up with the version number going to 4.0. So Apple's own numbering
agrees with everything measured below — 10.6 is a late 3.x that had begun
moving, and 10.7 is the rewrite.

---

## 0. Snow Leopard is a hybrid, and that is the whole document

Everything here was read out of the binaries, not remembered.

| | Tiger 3.3 | Leopard 3.6 | **Snow Leopard 3.10** | Lion 4.0 |
|---|---|---|---|---|
| binding | relocations | relocations | **compressed** | compressed |
| architectures in the fat file | 2 | 4 | **3** | 2 |
| i386 slice | 524 KB | 803 KB | **879 KB** | 938 KB |
| `__TEXT,__text` | 218 KB | 427 KB | **441 KB** | 489 KB |
| `__DATA,__cfstring` | none | 4528 | **4704** | 5008 |
| undefined symbols | 216 | 292 | **301** | 364 |
| `_dispatch_*` imports | 0 | 0 | **11** | 15 |
| Multiprocessing imports | 8 | 8 | **4** | 0 |
| vecLib imports (`_vDSP_*`, `_cblas_*`, `_catlas_*`) | 0 | 10 | **13** | 18 |
| speaks through | `SESpeakBuffer` | `SESpeakBuffer` | **`SESpeakBuffer`** | `SESpeakCFString` |
| sets rate through | `SESetSpeechInfo` | `SESetSpeechInfo` | **`SESetSpeechInfo`** | `SESetSpeechProperty` |
| ends an utterance with | `AUGraphStop` | `AUGraphStop` | **`AUGraphStop`** | a deferred stop |

Read down the Snow Leopard column and it is Lion above the line and Leopard
below it. **10.6 loads like 10.7 and talks like 10.5.**

The `Multiprocessing imports` row is the migration caught in the act: eight on
10.5, **four** on 10.6, none on 10.7. The threading moved to Grand Central
Dispatch one release before the speech API moved to CFStrings, and 10.6 is the
release where you can see both halves at once.

Its worker is `_MTBEWorkerExecuteTask` — **singular**, where Lion's is
`MTBEWorkerExecuteTasks`. Direct ancestor.

### What that meant in practice

**Nothing in the host was written for this generation.** The compressed dyld
info interpreter was written for Lion, the speech path and the vDSP shims for
Leopard, and the libdispatch layer and scaled `gettimeofday` for Lion again.
Pointed at a 10.6 tree with no changes at all, the loader mapped the engine,
**bound 1,170 symbols with none unresolved**, ran the initializers and got
`noErr` from `SEOpenSpeechChannel`.

What it would not do is make a sound, and both reasons were in code written
for one neighbour that had never met the other. They are sections 2 and 3.

---

## 1. The C++ runtime: one version number, two libraries

10.6's engine wants `libstdc++.6.0.9.dylib`. So does 10.7's. **They are not
the same file and not the same library.**

| | Snow Leopard 10.6 | Lion 10.7 |
|---|---|---|
| `libstdc++.6.0.9.dylib` | 2,439,888 bytes | 1,595,728 bytes |
| names `libc++abi` internally | no | yes |
| implements the C++ ABI | itself | re-exports it |
| second library needed | none | `libc++abi.dylib` |

From 10.7 the ABI — `__dynamic_cast`, the `__cxa_*` family, the guards around
function-local statics — lives in `libc++abi.dylib`, and libstdc++ carries a
hundred and fifty indirect symbols pointing at it. 10.6's 6.0.9 is a
self-contained library in the older style, like Leopard's 6.0.4.

This matters because **nothing downstream can tell them apart by name**, and
the wrong one does not fail cleanly. It loads. Then RTTI objects get null
vptrs and the engine misbehaves somewhere else entirely — the failure mode
`n-indr-is-a-name` describes at length.

In practice each generation's folder holds its own copy and the host searches
outward from the engine, so they never meet. The exposure is somebody
assembling a tree by hand from two discs, which is why the extractor, the
Tools menu and `explain()` all say which disc to take it from.

`pantherasnowleopard.find_libcxxabi` deliberately **does not exist**. Lion's
`usable()` refuses a tree without one; requiring the same here would refuse
every correctly extracted 10.6 tree there is, and there is a test asserting
the absence so nobody adds it for symmetry.

---

## 2. `dispatch_walltime` is on the wall clock

**The bug that stood between loading and speaking**, and the more interesting
of the two.

10.7 builds its GCD deadlines with `dispatch_time`, which this host answers
with `GetTickCount64` — a monotonic uptime, around 3.5 × 10¹⁴ ns.
**10.6 builds its own with `dispatch_walltime`**, and a walltime is exactly
what the name says: 1.79 × 10¹⁸ ns for 2026. Four orders of magnitude apart.

Two faults compounded:

* `sh_dispatch_walltime` returned an *uptime* plus the delta. The engine
  passes the absolute deadline in `delta` with a zeroed `timespec`, so the
  start came back as **uptime + wall clock** — about four days into the
  future.
* `sh_dispatch_source_set_timer` then measured that against the monotonic
  clock, found it more than a day out, and read it as
  `DISPATCH_TIME_FOREVER`. **The worker was never armed once.**

Which presents as a synthesizer that opens a channel, takes a voice, accepts
text, answers `noErr` — and returns **one frame of silence**, then `-231` to
everything after, because the channel is left mid-utterance.

### Decide by proximity to a clock, not by magnitude

The obvious fix is a threshold: anything above 2⁶⁰ is a wall-clock time. It is
wrong, and wrong in the one place that matters. `DISPATCH_TIME_FOREVER` is
9.22 × 10¹⁸ — the *same order of magnitude* as a 2026 wall clock, because both
are large 64-bit numbers and neither is large for a different reason.

So:

```c
#define DISPATCH_NEAR ((unsigned __int64)3600 * 24 * 1000000000ULL)
    /* Disarm first: ~0ull and INT64_MAX still mean never. */
    unsigned __int64 wall = wall_us_scaled() * 1000ULL;
    int near_wall = (start < wall + DISPATCH_NEAR) && (start + DISPATCH_NEAR > wall);
    int near_mono = (start < now + DISPATCH_NEAR);
```

Within a day of wall-now, it is a wall-clock deadline. Within a day of
mono-now, monotonic. Neither, disarm. A deadline is a *time*, so the test that
identifies it should be about *when*, not about how big the number is.

The wall-clock delay is divided by `g_speed`, because the engine read the
scaled clock — the same arithmetic `sh_uptime` does for 10.5, one step further
out.

### One anchor, not two

`sh_gettimeofday` held its epoch and start as function statics. A second copy
inside `set_timer` would look identical and be wrong: the two anchors are
taken at different moments, and the gap is multiplied by `g_speed` = 128, so a
tenth of a second between them becomes **thirteen seconds** of disagreement
about when a deadline lands. Factored out as `wall_us_scaled()` and called
from both.

---

## 3. 10.6 makes a dispatch source per unit of work

The second bug, in the same file as the first, and it is the one that shows
how far apart two generations can be while using the same API.

`g_sources` held **sixteen** slots and `g_nsources` only ever counted up. That
was written for 10.7, which creates two or three sources and keeps them for
the whole session.

**10.6 creates one per unit of work and cancels it again.** Measured, with
`TIGER_FLOAT_STATS`:

| | one short sentence | one long sentence |
|---|---|---|
| Leopard 10.5 | 0 sources | 0 sources |
| **Snow Leopard 10.6** | **9–14** | **57, of which 54 reused a slot** |
| Lion 10.7 | 1–2 | 0 more |

So the first thing ever said spent the table, and every source after it was a
bare handle no timer and no event handler could attach to: an utterance with
no worker, which is one frame and then `-231` for ever.

Slots are reclaimed on cancel now, once the thread has actually gone, and the
table is 256 — **sized from the measurement rather than chosen**, and note
that what it has to cover is how many are alive *at once*, not the total.
Nearly all of them reuse a slot.

Two wrong turns on the way, both worth keeping:

* **Waiting for `dispatch_release` before reusing a slot is the principled
  rule and does not work here.** This engine never releases them, so nothing
  was ever reclaimed: it went from flaky to one voice in twenty-four, five
  runs out of five.
* **Reusing the oldest retired slot rather than the first changed nothing** —
  which is what said the trouble was not a stale write racing the reuse.

It presented as a voice-switching fault and is not one. The same voice, long
text, eight times over fails just as well; short text never fails at any
number of voices; and the tell was that turning logging on made it pass.

What found it was the diagnostic added for the failure I *expected* — "all 64
dispatch sources are in use; this utterance has no worker and will be silent".
It fired on three runs in six. **The silent version of that same condition was
the bug all along**, which is this project's most-repeated lesson: a table
that quietly hands back something other than what was asked for reads as a
dead engine.

### The honest shape, not yet done

Each dispatch source gets a Windows thread, so a long 10.6 utterance creates
about ninety-five threads. It works and it is not the bottleneck — 10.6
renders at 53× to 112× real time — but a small worker pool is what this should
be.

---

## 4. It stops its own audio graph, so there is no fixed tail

10.7 never calls `AUGraphStop` — zero times in ninety-six utterances — which
is why a Lion response had nothing to end on but a silence window, at a flat
300 ms every time until 0.98.0 found the deferred-stop arm it does give.

**10.6 stops the graph, once per utterance, exactly as 10.5 does.** Measured
here on the same three texts, one host each, `[au] start N, stop N`:

| | Leopard | **Snow Leopard** | Lion |
|---|---|---|---|
| `AUGraphStart` / `AUGraphStop` per utterance | 1 / 1 | **1 / 1** | 1 / 0 |
| deferred stop armed | 0 | **0** | 1 |
| ticks waited after the audio | 1–3 | **1–5** | 1–5 (since 0.98.0) |

And what it costs, wall clock against audio produced, one host, Fred:

| text | audio | wall | tail |
|---|---|---|---|
| `a` | 290 ms | 30 ms | none |
| `Hello.` | 483 ms | 14 ms | none |
| a 54-character sentence | 3155 ms | 16 ms | none |

There is nothing to remove because there is nothing there. `tests/snowleopard`
pins it at under 150 ms for a one-letter utterance, which is watching for a
regression to the 300 ms floor rather than measuring anything.

---

## 5. The voice data

Twenty-four voices — the same set as Leopard and Lion. Two `meow`, three
`gala`, nineteen `mtk3`.

**Alex is 400,851,191 bytes**: the rebuilt bank, not Leopard's 669 MB
original. Lion's is 402.5 MB, and the two are close enough that 10.6 is where
the rebuild happened rather than 10.7.

That has one consequence worth stating because it nearly caused a mistake:
**the per-voice volume table is its own.** Twenty-three of these names are in
Leopard's table and twenty-four in Lion's, so copying either would have looked
entirely reasonable, and both are wrong about the voice people install this
for:

| | Leopard | **Snow Leopard** | Lion |
|---|---|---|---|
| Alex | 1.80 | **1.46** | 1.19 |
| Vicki | 1.20 | **1.19** | 1.17 |
| Bruce, the loudest | 1.00 | **1.00** | 1.00 |
| spread after normalisation, excl. Whisper | 6.6 dB | **6.3 dB** | 5.3 dB |

Vicki barely moves across the three and Bruce not at all, which is the trap:
almost every row of a copied table would have been right.

Leopard's factor asks Snow Leopard's Alex for gain the recording does not
have; Lion's leaves nearly 2 dB of it unused. The table is measured with
`tools/volume_table.py snowleopard`, worst case across five probe texts.

### Where the files are

10.6's install DVD has a live filesystem, the way 10.4's and 10.5's do and
10.7's does not — no `BaseSystem.dmg` archaeology:

```
System/Library/Speech/Synthesizers/       MacinTalk (fat: i386, x86_64, ppc)
System/Library/PrivateFrameworks/SpeechDictionary.framework
System/Library/PrivateFrameworks/SPSupport.framework
usr/lib/libstdc++.6.0.9.dylib
System/Installation/Packages/Essentials.pkg
                                          every classic voice
System/Installation/Packages/AdditionalSpeechVoices.pkg
                                          Alex and Vicki, and nothing else
```

`AdditionalSpeechVoices.pkg` misleads exactly as it does on 10.5 and 10.7:
taking only the package whose name says "speech voices" gets Alex and loses
Agnes, Bruce, Victoria and every novelty voice.

The extracted tree is byte-identical to one assembled by hand — engine,
dictionary and runtime hashed, all twenty-four voices present at the same
sizes.

---

## 6. What is not done

* **A worker pool** instead of a thread per dispatch source. Section 3.
* **Nobody has listened to it properly yet.** Everything above was found by
  measurement, and every bug this project has shipped was found by ear. 10.6
  has had no equivalent of the week Lion spent in front of listeners, and the
  faults that turns up — a tail arriving late, a wedge after five seconds of
  silence, a paragraph cut at 511 characters — are not ones a render test
  notices.

## Instruments

The same as the other generations, and two that matter most here:

```
TIGER_FLOAT_STATS=1     [gcd] N source(s) made, M of them reused slots
                        [au]  start N, stop N, ... widest gap between slices
                        [au]  the engine armed its deferred graph stop N time(s)
TIGER_HOST_VERBOSE=1    every bind, every shim, every slice
```

The `[gcd]` line is the one that sized the source table and diagnosed it in
one reading. It exists because of this generation.
