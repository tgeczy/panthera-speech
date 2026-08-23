# Running Lion's MacinTalk as native code

Notes from making Apple's Mac OS X 10.7 speech engine run in a Windows
process. **Read `macintalk-3.3.md` first** — the loader, the twelve-function
interface and the fake AUGraph are all described there and are not repeated.
This is what 10.7 does differently, and every difference here cost at least one
debugging round.

MacinTalk 4.0 is the **last** MacinTalk Apple shipped. 10.8 Mountain Lion has
no i386 slice of it at all, which is where this line of work stops.

---

## 0. Three engines, side by side

Everything below was read out of the binaries in
`%APPDATA%\nvda\macintalk\<generation>`, not remembered.

| | Tiger 3.3 | Leopard 3.6 | **Lion 4.0** |
|---|---|---|---|
| i386 slice | 524 KB | 803 KB | **938 KB** |
| architectures in the fat file | 2 | 4 | 2 |
| `__TEXT,__text` | 218 KB | 427 KB | **489 KB** |
| `__DATA,__cfstring` | none | 4528 | **5008** |
| undefined symbols | 216 | 292 | **364** |
| libdispatch imports | 0 | 0 | **15** |
| Multiprocessing imports | 8 | 8 | **0** |
| vDSP / vecLib imports | 0 | 5 | **14** |
| CoreFoundation imports | 26 | 36 | 40 |
| Sound Manager imports | 7 | 0 | 0 |

The build path is in the binary and names the version:

```
10.5   /SourceCache/SpeechSynthesis/SpeechSynthesis-3.6.59/Synthesizers/MacinTalk/…
10.7   /SourceCache/SpeechSynthesis_MacInTalk/SpeechSynthesis-4.0.74/Synthesizers/MacinTalk/…
```

Note the **project rename**. By 10.7 MacinTalk had been split out of
`SpeechSynthesis` into `SpeechSynthesis_MacInTalk` — a project of its own,
which is not what a codebase about to be abandoned usually looks like.

Two rows above are the whole story of this document. **Multiprocessing went to
zero and libdispatch went to fifteen**: 10.7 rewrote the engine's threading
onto Grand Central Dispatch. **vDSP went from five to fourteen**, including an
FFT: 10.7 rewrote the rate-change search into the frequency domain.

---

## 1. Loading: the classic relocation tables are empty

Tiger and Leopard carry `LC_DYSYMTAB` relocations. From 10.6 those tables are
empty and two **bytecode streams** carry the same information —
`LC_DYLD_INFO_ONLY`, with a rebase opcode stream and a bind opcode stream. A
loader that only reads relocations loads Lion's engine and gets a binary with
every imported pointer still null.

This is the single thing that blocked every generation after Leopard, and
`src/tiger_host_dyldinfo.c` is the interpreter for it.

Four ways a bind is not a relocation, each of which cost a round:

* **It is a stream, not a table.** Opcodes set a segment, an offset, a library
  ordinal and a symbol name, and then `BIND_OPCODE_DO_BIND` applies the current
  state and *advances*. State persists across opcodes.
* **`BIND_OPCODE_SET_ADDEND_SLEB` is signed.** Read as unsigned it is
  enormous, and the fixup lands nowhere near the image.
* **Rebase and bind are separate streams** with separate opcode sets that
  happen to share encodings. Running one interpreter over both looks like it
  works for a while.
* **The lazy stream binds through stubs**, so a missing lazy bind does not
  fault at load; it faults the first time that function is called, which may be
  three utterances later.

It was built test-first against a standalone Python oracle before being pointed
at the engine at all. That is worth copying: the failure mode of a bad
interpreter is a plausible-looking image, not a crash.

### `N_INDR` is a name, not an address

The most expensive bug of that work and the only silent one. Lion's
`libstdc++.6.0.9.dylib` has **150 indirect symbols** — `n_type & N_TYPE ==
N_INDR` — where `n_value` is *an index into the string table naming another
symbol*, not an address.

Read as definitions, they resolved 23 imports to garbage. Nothing crashed at
load. The oracle had the identical bug, which is the argument for an oracle
written from the spec rather than from the same reading of it.

### Symbols are renamed between releases, and the stub returns 0

Four separate crashes turned out to be one bug: **a missing import falls
through to a stub that returns 0, and 0 is `noErr`.** The engine carries on
with a wrong answer.

The renames to expect:

| suffix | what it means |
|---|---|
| `$UNIX2003` | the SUSv3-conformant variant — `_regcomp$UNIX2003` |
| `$INODE64` | 64-bit inode `stat`/`readdir` family |
| `_v2`, `64` | a second ABI for the same call |
| `_l` | the locale-taking variant |

`src/tiger_host_shimtab.c` reports an unresolved import by name now rather than
silently thunking it, which turns all four of those into one log line.

---

## 2. CoreFoundation, and a table name that is a format string

Lion asks CF for more than Leopard does — 40 imports against 36 — and one
difference matters more than the count.

**Lion *formats* its dictionary table names.** Where Leopard asks for a table
by literal name, Lion builds it: `CFStringCreateWithFormat` with `%@Eng` and a
language object. A `CFStringCreateWithFormat` that only handles `%s` returns
something plausible, and two of the six tables arrive while four do not — the
dictionary then builds, `SEOpenSpeechChannel` returns `noErr`, and the engine
mispronounces everything that needed the missing four.

`sh_CFDictionaryGetValue` is still a stub returning NULL. That is survivable
for reading, and it is what blocks §5.

---

## 3. Rate and pitch moved to a new API

10.7 kept `SESetSpeechInfo` but stopped answering `'rate'` and `'pbas'` on it.
The working call is:

```c
SESetSpeechProperty(chan, CFSTR("rate"), CFNumberRef)
```

* the values are still **`Fixed`** — 16.16, not float, not integer
* the property **constants are ours to define**: the engine compares CFString
  keys, so the names are what matter
* rate is honoured from **80 to 500 wpm**, and above that it keeps going
  without complaint

`CFNumberCreate` being stubbed is why pitch looked unsupported while rate
appeared to work: the rate path had a second route in and the pitch path did
not, so one of them silently received nothing.

`'pbas'` remains a **musical scale**, twelve units to the octave, not hertz —
see `pbas-is-a-musical-scale` in the engine notes. Measured: 40 → 109 Hz,
50 → 193 Hz.

### The status selector is refused

`SEGetSpeechInfo(chan, 'stat', &info)` — whose first long is `outputBusy` —
answers on Tiger and Leopard and is **refused by Lion, 32 times out of 32**
(`TIGER_STATUS=1`). 10.7 evidently moved status the same way it moved rate,
onto `SECopySpeechProperty` returning a CFDictionary. That needs the CF
dictionary support §2 does not have yet, and §5 is what it costs.

---

## 4. Grand Central Dispatch, and the timer that stops the audio graph

Fifteen libdispatch imports where Leopard has none. `src/tiger_host_gcd.c` is
just enough of it: sources, queues, `dispatch_once`, blocks. Two notes.

**Everything runs the block on the calling thread**, `dispatch_async` included.
That is a real semantic change and the right first answer — this host renders
one utterance at a time — but if something ever depends on async being
asynchronous it will deadlock rather than go quiet, which is the failure worth
having. A stubbed `dispatch_sync` is *not* harmless: the work is in the block,
so the call returns having done nothing and the engine carries on as though it
had. That is how a render reached its `Samples` stage and produced a 46-byte
wav with no error anywhere.

**And the timer that matters:**

> Five seconds after it stops speaking, the engine arms a GCD one-shot on
> `_MTBEAudioUnitDeferredStopAudioGraph`. It calls `AUGraphStop` and moves
> `MTBEAudioUnitSoundOutput` into its stopped state. **After it has fired, the
> next `SESpeakBuffer` never returns.**

No audio, no error, no answer at all. On a Mac this is good manners — it hands
the audio device back. Here it is a wedge, and it shipped in 0.95.0: the
symptom is "if I stop using it for a minute, the next thing I ask for is
silent". Leopard cannot do this, because 10.5's MacinTalk imports no
libdispatch at all.

The host refuses to arm that one source, identified by symbol name once when
the handler is set. The graph it is being polite about does not exist.

Found on the way: **a `start` of `DISPATCH_TIME_FOREVER` means disarm**, and is
`~0ull` where the *interval*'s forever is `INT64_MAX`. Read as a delay and
clamped, "never fire this" becomes "fire this in a minute".
`MTBEAudioUnitSoundOutput::StartAudioGraph` disarms the deferred stop exactly
that way.

---

## 5. The engine never stops its own audio graph

Tiger and Leopard start and stop the graph once per utterance — measured, 92 of
92 and 96 of 96 — so `AUGraphStop` is a clean end-of-utterance signal.

**Lion starts the graph once for the whole session and never stops it: 0 of
96.** There is nothing obvious to end an utterance on, so the host used to fall
back to a quiet period — 300 ms with no new slice, on the end of every single
render. That is why Lion measured 16× real time where Leopard measured 87×.
(Lion measures 64× on Alex now, and Leopard 36×, for the two reasons below.)

It cost more than the time. While the wait ran the driver still counted the
utterance as rendering, so a keystroke arriving inside that window could retire
the whole host process — audible as the engine restarting on every arrow key.

Shortening the window is not the answer, and was tried: the widest silence
between two slices of one utterance is 40.2 ms across 284 utterances, so 150 ms
looks like three times the worst case — until an unbroken 370-character token
gives Alex enough morphology to go quiet for longer than that in the middle of
one utterance.

### It does signal the end. It arms a *deferred* stop.

The signal was already passing through this host and being discarded. 10.7
schedules `_MTBEAudioUnitDeferredStopAudioGraph` on a GCD one-shot when an
utterance finishes, five seconds out; §4 is about why that timer must never be
allowed to fire. **The moment it is armed is the engine saying it has
finished**, and refusing the timer is where that can be seen.

Measured before being trusted, with a temporary `TIGER_TAIL_LOG` instrument
that printed the engine’s activity per tick and was removed once it had done
its job:

| | last slice | deferred stop armed |
|---|---|---|
| one sentence, Fred | 33 ms | 30 ms |
| three sentences, Alex | 323 ms | 320 ms |
| an unbroken 370-character token, Alex | 1598 ms | 1573 ms |

**Armed exactly once per utterance in all three**, and within 25 ms of the last
slice — including the long-token case that rules out every fixed timeout. Over
the 300 ms that used to follow, the engine made **zero `gettimeofday` calls and
armed zero timers**: it was finished, not thinking.

Slices still in flight are not a risk, because scheduling ending was never the
same as the audio having been read — the pacer drain after the wait loop is
what covers that, and it is unchanged.

Tiger and Leopard are untouched by construction: neither arms this timer
(Leopard imports no libdispatch at all), so the counter stays zero and they
leave on `AUGraphStop` exactly as before.

`kSpeechStatusOutputBusy` via `SECopySpeechProperty` would also work and is
still the tidier answer, but it needs a real `CFDictionary` (§2) and this needs
nothing at all.

### And the streamed tail has to be released early

The host holds a margin of frames back so a slice landing behind the frontier
can still overwrite audio nobody has heard, and releases it when the response
ends — which on Lion is 300 ms after the audio is finished. At ordinary rates
nobody notices. With NVDA's rate boost the whole utterance is shorter than that
delay:

```
lion  640 wpm, the letter "O"   100 ms of audio, player dry 247 ms, then 23 ms
lion 1200 wpm, the letter "O"    57 ms of audio, player dry 293 ms, then 23 ms
```

The output stream drains and is started again for a fragment, which sounds
exactly like an old synthesizer closing its wave socket. The margin is released
once slices stop arriving instead.

---

## 6. The clock 10.7 reads is not the one 10.5 reads

The whole host rests on the engine's clock running fast: playback is faked, so
time is scaled by `g_speed` (128) and the engine renders as quickly as it can.

**10.7 moved the worker's clock from `UpTime` to `gettimeofday`**, and only
`UpTime` was scaled. The engine then polled a clock that never ran fast —
**12.5 million times for one sentence** — and Lion rendered at *nine tenths of
real time*.

It surfaced as something else entirely. The serve loop allows an utterance nine
seconds of wall clock, and at 0.9× the singing voices reach that inside one
ordinary sentence, so seventeen voices in a row rendered nothing. The test that
names this is a speed floor, not a voice count.

Scaling an epoch rather than the raw value:

```c
if (!epoch_us) { epoch_us = t; start_us = qpc_us(); }
t = epoch_us + (unsigned long long)((double)(qpc_us() - start_us) * g_speed);
```

Do not assume the amplified clock explains idle-time bugs: the wedge in §4
fires after the same five *real* seconds at `TIGER_SPEED=16` as at 128, because
GCD deadlines are computed from `GetTickCount64` and never scaled.

---

## 7. WSOLA in the frequency domain

Changing a concatenative voice's rate means time-scaling recorded speech, and
`MTMBModRateWsola::ModifyRate` does it by searching for the best overlap.
Leopard searches in the time domain with four vDSP calls. **Lion correlates in
the frequency domain**, which is why the import list grows to fourteen:

| | Leopard 3.6 | Lion 4.0 |
|---|---|---|
| | `hann_window` `svemg` `vmma` `vmsb` (+ `vmul`) | those, plus `maxvi` `sve` `vclip` `vma` `vramp` `zvcmul` `create_fftsetup` `destroy_fftsetup` `fft_zrip` |

So Lion needs a real FFT. `src/tiger_host_accel.c` has one, built against a
numpy oracle. Two conventions are where it would go wrong:

* **`fft_zrip` is packed real.** After a forward transform `realp[0]` is DC and
  `imagp[0]` is Nyquist — both real, sharing the slot that would hold bin 0's
  imaginary part, which is always zero for real input.
* **vDSP's real forward transform is scaled by 2** against the textbook DFT.
  Invisible downstream, because the result feeds `vDSP_maxvi` and a uniform
  scale cannot move an argmax — but wrong is wrong.

`vDSP_zvcmul` computes **`conj(A) * B`**. Apple's *vDSP Vector-to-Vector
Arithmetic Operations Reference* (2009-01-06): *"Multiplies vector B by the
complex conjugates of vector A."* Getting it backwards mirrors the correlation
and puts the peak at minus the lag; it does not crash.

Two traps for anyone measuring this:

* **Alex reaches the FFT path only above about 250 wpm.** At 180 the engine
  does no rate scaling and the whole file is dead code. Reproductions need
  NVDA's rate boost.
* **Vicki calls `zvcmul` at 180 wpm with no FFT at all.** "No `fft_zrip` in the
  `TIGER_ACCEL_DEBUG` log" does not mean Accelerate is idle.

And a warning about how to judge a change here. Flipping the conjugate makes
Alex's amplitude envelope *smoother* — 0.673 down to 0.533 on letter tails —
which reads as an improvement and is the opposite: **a WSOLA splicing at the
wrong lag re-emits material it has already used, and repeated material has a
flatter envelope than speech.** Do not rank these by envelope statistics.

### It is also the most expensive thing in the host

About **1,400 transforms for one ordinary post**, and until 0.98.0 they were
**141 ms of a 210 ms Alex render — two thirds of it, none of it Apple's code.**

Two ordinary mistakes, both in `fft_complex`:

* The twiddle factors were computed **inside the block loop**, where they
  depend only on the two outer indices. A 512-point transform evaluated 2,304
  sine-cosine pairs where 511 would do.
* Every call allocated and freed **six buffers**, and the forward branch
  computed a second sine and cosine per bin to recombine with.

The angles and the scratch belong in the `FFTSetup`, which is created once with
the size and had been carrying nothing but that size. **141 ms became 18.**

The constraint that shapes the fix: **not one sample may move.** So the
expressions are kept exactly as they were rather than being simplified —
`cos(2·π·k/len)` for a table indexed by stage, not one table for the largest
stage indexed by a stride, because those are mathematically equal and *not*
bit-equal. Stages of a smaller transform are a prefix of a larger one's, which
is what lets one table serve both the n/2-point forward and the n-point
inverse; the recombination angles divide by `n` and are *not* a prefix, so they
are used only at the size the setup was built for.

Leopard has the same shape of cost in the time domain: **205,000 calls into
`svemg` and `vmsb` for one post, 74% of the render.** Same treatment — the
engine only ever passes unit strides, and saying so lets the compiler do the
obvious thing. `svemg` keeps its accumulation order, because it sums floats and
a different order is a different number; `vmsb` is elementwise and safe to
widen.

Verify with hashes, not with listening: every voice on all three generations,
rendered before and after. Tiger and Leopard are byte-identical, and so are
Lion's Alex and Vicki.

**Lion's mtk3 voices are not byte-reproducible run to run** — Fred renders the
same text to two different results at the same frame count, with an unmodified
host. Tiger and Leopard are exactly reproducible. Do not use a Lion mtk3 hash
as a regression check.

---

## 8. The voice data, and the dictionary

**Alex is `meow` 2.0.0 on Lion and 1.x on Leopard**, and the sample banks are
different recordings:

| | Leopard | Lion |
|---|---|---|
| `Alex.SpeechVoice/…/PCMWave` | 669.2 MB | **402.5 MB** |

Lion's is the smaller, later recording and measures *cleaner*. It also says
"Dropbox" without much of the P, which Leopard's does not — the same word, two
banks, and not a fault in the loader. The engine still reads a `meow` 1.0.6
bank and has branches written for it, so Lion's engine will load Leopard's Alex
if you point it at one.

**Lion's `SpeechDictionary` is a different animal:**

| | Leopard | Lion |
|---|---|---|
| i386 slice | 202 KB | **616 KB** |
| undefined symbols | 81 | 134 |
| SQLite imports | 8 | 8 |
| POSIX regex imports | `_regcomp$UNIX2003` `_regexec` `_regfree` | **none** |

Three times the size and **no regular expressions at all**. Leopard's
rule-driven abbreviation expansion goes through `regexec`, and the host's own
`regcomp` is what lets "expand abbreviations" be a setting there. On Lion that
lever does not exist, because those rules are not regular expressions any more.

A stub `regexec` returning 0 means *match*, which is why numbers came out spelt
out before the real one was written.

Both generations expand a second class of abbreviation from a table **inside
MacinTalk itself** — `DRIVE`, `DOCTOR`, `SAINT`, `STREET`, `FEET` sit in a row
at `MacinTalk + 0x702c4` — which no engine parameter reaches and no dictionary
rule explains.

### Where the files are

Lion's engine, dictionary, both C++ runtimes and Fred are all in
**`BaseSystemBinaries.pkg`**. `BaseSystem.dmg` also contains copies and they
are useless: thin x86_64.

Lion needs **two** Apple libraries where Leopard needs one —
`libstdc++.6.0.9.dylib` and `libc++abi.dylib` — because 10.7 moved the C++ ABI
into a library of its own. Without either, nothing loads.

---

## 9. What is not done

* **`SECopySpeechProperty` / `kSpeechStatusOutputBusy`.** Needs a real
  `CFDictionary`. Would remove the 300 ms in §5 and most of what it costs.
* **`sh_CFDictionaryGetValue` is a stub returning NULL.** Survivable for
  reading; the blocker above.
* **The phrasing table.** Lion's `TuplesEng` is Mountain Lion's, so it is not a
  drop-in for Leopard — but the encoding is a widening of the same format and
  the bit rule is recovered, and the translated table sounds better.
* **10.8 and later.** No i386 slice of MacinTalk exists. This is the end of the
  line, not a gap.

---

## Instruments

| switch | what it prints |
|---|---|
| `MTX_DEBUG=1`, `MEOW_DEBUG=1` | the engine's own narration: every word, unit and silence. Read by the engine's own `getenv`, not by the host -- these are Apple's switches, left in the shipping binary |
| `TIGER_FLOAT_STATS=1` | slice timeline, decoder profile, clock counts, AU calls |
| `TIGER_ACCEL_DEBUG=1` | every vDSP call, and the overlap-add weights |
| `TIGER_GCD_LOG=1` | every GCD handler by name, and each stage of a request |
| `TIGER_PARAMS=Name=Value;...` | set the engine's own tuning parameters |
| `TIGER_PREF_LOG=1` | every tuning parameter and dictionary rule asked for |
| `TIGER_STATUS=1` | ask the channel whether it is still speaking |
| `TIGER_SPEED=<n>` | the clock scale; 128 is the default |
| `TIGER_DEFERRED_STOP=1` | put the §4 wedge back, to watch it happen |

`tools/machodis.py <binary> <symbol|0xADDR> [len]` disassembles, and names the
symbol at each call site. Remember the PIC base: a `call` to the very next
instruction followed by `pop esi` is not a call, it is how the engine learns
its own address, and every `lea eax, [esi + N]` after it is relative to that.
