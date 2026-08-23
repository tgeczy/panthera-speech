# Running Leopard's MacinTalk as native code

Notes from making Apple's Mac OS X 10.5 speech engine run in a Windows
process. **Read `macintalk-3.3.md` first** — the loader, the twelve-function
interface and the fake AUGraph are described there. This is what 10.5 does
differently, and `macintalk-4.0.md` carries the same for 10.7.

10.5 is where **Alex** arrives, and almost everything below follows from that
one fact: a concatenative voice is not a formant voice with better tables, it
is a different machine.

---

## 0. What changed from Tiger

Read out of the binaries in `%APPDATA%\nvda\macintalk\<generation>`.

| | Tiger 3.3 | **Leopard 3.6** | Lion 4.0 |
|---|---|---|---|
| i386 slice | 524 KB | **803 KB** | 938 KB |
| `__TEXT,__text` | 218 KB | **427 KB** | 489 KB |
| `__DATA,__cfstring` | none | **4528** | 5008 |
| undefined symbols | 216 | **292** | 364 |
| Sound Manager imports | 7 | **0** | 0 |
| AudioToolbox imports | 16 | **19** | 19 |
| vDSP / vecLib imports | 0 | **5** | 14 |
| Multiprocessing imports | 8 | **8** | 0 |

```
/SourceCache/SpeechSynthesis/SpeechSynthesis-3.6.59/Synthesizers/MacinTalk/…
```

Three rows carry the story. **Sound Manager went to zero and AudioToolbox
grew**: audio decoding moved from `SoundConverter` to `AudioConverter`.
**`__cfstring` appears from nothing**: 283 named tuning parameters exist where
Tiger had none. **vDSP appears**: Alex needs vector maths that Fred does not.

Threading is unchanged — still Multiprocessing Services, `MPCreateTask` and
`MPWaitOnQueue`. That is the one place Lion diverges hardest.

---

## 1. Alex is a 669 MB memory-mapped sample bank

`Alex.SpeechVoice/Contents/Resources/PCMWave` is **669.2 MB**, and it is not
PCM: it is AAC. The engine memory-maps it and pages in the units it needs, so
the size costs address space rather than a wait — a short sentence touches
about a tenth of it.

Consequences worth knowing before you debug anything else here:

* **Do not read the bank.** Anything that does is a 669 MB read to fetch a few
  hundred kilobytes.
* **`soReset` reloads it.** Interrupting an utterance and resetting the channel
  measured **2887 ms** afterwards. The host settles the channel instead; see §6.
* Nineteen of Leopard's twenty-four voices are still MacinTalk 3 formant
  voices, three are MacinTalk Pro, and **two are concatenative** — Alex and
  Vicki. Only those two run any of §4 or §5.

---

## 2. AudioConverter, and the priming Tiger never had to think about

Tiger decodes Vicki through `SoundConverter`, one self-contained unit at a
time, and the result is byte-perfect. **Leopard drives `AudioConverter`, which
streams**, and three things follow.

### Open the stream once, not once per refill

AAC frames overlap: each one's samples are finished by the next, because the
codec adds consecutive MDCT windows together. `aac_begin()` sends
`COMMAND_FLUSH`, which throws that carry-over away — so calling it per refill
puts a seam at every 1024-sample boundary. Alex counted to seven correctly and
stuttered all the way there, which is what a gap every 1024 samples sounds
like.

The engine pulls one utterance through one converter. It is a stream; treat it
as one.

### Fill the whole request

A decoded AAC packet is 1024 samples. Return after a single decode round and
you hand back 1024 frames of a request for more, and the rest is whatever the
buffer already held.

Leopard's engine asks again when it is short-changed, so it survives this.
**Lion's does not** — the same defect cost two thirds of Alex there, 335 of 508
slices coming back entirely silent at full duration. Fill the request.

### 2112

Media Foundation's decoder emits AAC's **2112-sample codec delay** whatever
Apple's API was told, and the engine sizes its buffer at
`frameCount * 1024 - 2112` — Apple's priming written into the arithmetic. Every
unit arrived 96 ms late, twenty-three units to an utterance.

The argument for leaving it was that Apple sets `kAudioConverterPrimeMethod` to
None on this converter. That describes what *Apple's* decoder does, not what
Windows' does.

**The trim belongs to the stream, not the refill.** Taking 2112 off a
1024-sample refill deletes it outright; carry the debt across refills until it
is used up. And note that **Tiger's byte-perfect renders validate none of
this**: Tiger's `SoundConverter` path always dropped the priming, so it never
had the bug and never could have caught it.

---

## 3. The slice timeline restarts at zero

The engine schedules an utterance in **epochs**, and each epoch starts its
sample clock again at zero.

A real `ScheduledSoundPlayer` does not care — it plays into a device that keeps
running, and `AudioUnitReset` plus a fresh schedule simply means "now play
this". The host accumulates into one buffer instead, so an epoch that restarts
at zero **lands on top of everything collected so far**.

That is where the missing words went. "One, two, three." was complete, and then
the second sentence was written over it from sample zero.

It only bit half the time, which is what made it look like corruption rather
than a bug: the same sentence came back as one continuous timeline of 98900
frames, or as two epochs of 48505 and 50395, run to run. Only the continuous
one had all the words. Rebasing on the clock going backwards makes the two
identical.

Detect it by the clock going backwards rather than by `AudioUnitReset` — the
restart is what actually breaks you and it is visible where the audio arrives.

**And read the slice at completion, not at scheduling.**
`kAudioUnitProperty_ScheduleAudioSlice` means "play this buffer at this time";
the engine is free to fill it *after* scheduling, and its worker does exactly
that. Copying at schedule time captures the previous slice's audio — heard as
sounds inserted where none belong.

---

## 4. vDSP: WSOLA in the time domain

Five imports, and `MTMBModRateWsola::ModifyRate` is what uses them:

```
_vDSP_hann_window   _vDSP_svemg   _vDSP_vmma   _vDSP_vmsb   _vmul
```

Stubbed out, Alex opens, takes the voice, accepts the text, runs to completion
and produces one frame of nothing. The counters say why:

```
58186903 x _vDSP_svemg
58189323 x _vDSP_vmsb
  524218 x _vDSP_vmma
  524241 x _vmul
```

Fifty-eight million calls into empty functions.

**Every signature was read out of the binary, not remembered.** Argument counts
come from counting stack slots at the call sites: `vDSP_vmsb` takes nine
arguments and `vDSP_vmma` eleven, which is easy to get backwards, and `vmul` is
the older vecLib spelling with seven rather than the vDSP one with five. A
wrong guess does not crash — it quietly produces the wrong sound.

Strides are signed and may run backwards, so walk pointers rather than index.

`vDSP_vmma` is the cross-fade, and its two weights are the same Hann window
read from two places. They must sum to one at every sample or every join gets
an amplitude step. Measured on both generations: **1.0000, zero deviation**.

### And it is where a Leopard render's time goes

**205,000 calls into `svemg` and `vmsb` for one ordinary post, 74% of an Alex
render.** That is the time-domain search doing exactly what it is supposed to;
it just does a great deal of it.

The engine only ever passes **unit strides**, and the general form pays a
multiply per element for the stride it never uses. Saying so took Alex from
524 ms to 356 for a long post, and Bruce from 273 to 141.

Two rules for touching anything in here, and they are not the same rule:

* `vmsb` is elementwise, so each output depends on one input triple. Widening
  it is safe and the result is bit-identical.
* `svemg` is a **running sum of floats**. Its accumulation order is part of the
  answer — reassociate it and the numbers change, the WSOLA search scores
  differently, and grains join in different places. Keep the order.

Verify by hashing every voice before and after, not by listening. Tiger and
Leopard are exactly reproducible run to run; **Lion's mtk3 voices are not**, so
do not use one as a check.

Lion moves this whole search into the frequency domain — see `macintalk-4.0.md`
§7, where the same lesson cost more.

---

## 5. SpeechDictionary: SQLite and POSIX regex

Leopard's front end is a separate binary, and it wants two things Tiger's did
not:

| | Leopard | Lion |
|---|---|---|
| i386 slice | 202 KB | 616 KB |
| undefined symbols | 81 | 134 |
| SQLite | 8 imports | 8 |
| POSIX regex | `_regcomp$UNIX2003` `_regexec` `_regfree` | **none** |

Note `$UNIX2003`: the SUSv3-conformant variant of `regcomp`, a suffix that will
bite anywhere else in this codebase too.

**A stub `regexec` returning 0 means *match*.** That is why numbers were spelt
out digit by digit before a real implementation existed — every rule matched
everything.

### The unterminated word buffer

The hardest bug in this generation, and four wrong theories preceded it.

Abbreviations fired **only sometimes**. "the file is 5KB" rendered as "five
kilobytes" (30800 frames) or as "five K B" (27440), with nothing touched in
between, depending on what had been spoken before it.

The dictionary passes `regexec` a pointer into an **unterminated** word buffer
with `REG_STARTEND` and the bounds in `pmatch[0]`. An implementation that reads
to the first NUL matches the word plus whatever follows it in memory — and
every one of these patterns is anchored with `$`, so the rubbish decides:

```
[re] exec 5KBE            -> MATCH   (the next byte happened to be 'E')
[re] exec 5KBE<binary>    -> no      (the next run, it did not)
```

Honour `REG_STARTEND`.

### A signed byte that overflows

`SLPrefixMorph::AddAffix` keeps a saved word's length in a **signed byte** and
adds each affix to it unchecked. A long run of one letter is what makes that
reachable: every position in the run offers the morphology the same prefix
match, the decompositions multiply, and the byte climbs past 127 and reads back
negative.

Twenty x's followed by "the" is enough, and it fails two ways depending on how
far it gets: a `memmove` of four gigabytes, or a quieter overrun of one record
into the next that surfaces later as a null dereference in synthesis. The
concatenative voices crash, Pro wedges, and MacinTalk 3 shrugs — so a test
written around Fred proves nothing.

---

## 6. Interrupting

Where nearly every audio fault in this generation lived.

* **The engine keeps the text you stopped.** `SEStopSpeechAt(kImmediate)`
  returns before the channel is idle, and text handed to a still-busy channel
  queues behind what is already in it. Proved with Whisper: interrupt a
  sentence, ask for the next, and the engine speaks the remainder of the
  abandoned text first. Wait for `outputBusy` to clear — which on this
  generation you *can* ask for; `'stat'` answers here and is refused on Lion.
* **The sample clock does not restart** between utterances when the channel was
  stopped mid-sentence rather than reset. Treating the slice time as absolute
  prepends however far the clock had run — seconds of silence before the next
  thing the user asked for. Take the origin from the first slice of each
  utterance.
* **`soReset` reloads Alex's 669 MB.** Do not use it to flush.

---

## 7. Prosody: what is tunable and what is not

283 named parameters exist in `__cfstring`, and 82 of them are live —
`docs/engine-tunables.md` has the list. Two findings are worth having in front
of you before you go looking.

**`Boundaries.SilThreshold = 0` removes the mid-clause pauses.** Confirmed by
ear. Not the dictionary, not the phrasing table, not VoiceOver. And it is a
trade: the same setting costs the breaths, turning seven breath-length pauses
into twelve clips.

**Alex breathes at sentence boundaries and nowhere else.** N sentences in one
utterance give N−1 breaths. A single sentence can never breathe — so how the
screen reader chunks text decides whether the voice breathes at all, and no
engine parameter can override that. The driver holds a finished sentence
briefly and speaks it with the next for exactly this reason.

**There is no prosody layer above MacinTalk to reach for.** The de-accenting
that makes Alex say "cologne" for "colon" whenever a word follows it was
attacked with eleven tuning parameters and all but one left the render
byte-for-byte identical. It is structural, and the repair is in the text.

---

## 8. Volume is per-voice, and per-driver

Alex is **8 dB below Bruce**, and Alex is the default voice. Around fifteen
complaints about volume were all describing that one fact.

The host does not send the engine a volume command for this — the levelling is
folded into the 8-to-16-bit widening on the driver side, per voice, measured on
Leopard's own recordings. There is **no headroom to normalise into**, so the
table brings the loud voices down rather than the quiet ones up.

Do not copy the table to another generation. Twenty-three of Lion's voice names
are the same and its Alex is a different recording; Leopard's figure would ask
it for 3.6 dB it does not have.

---

## 9. It matches a real Leopard

The host was checked against 10.5 running in a VM on the same text and voice:
**219184 frames on both sides.**

That is worth stating plainly, because it settles which complaints are ours.
Phrasing that sounds wrong, a pause in an odd place, a word mispronounced — on
this generation those are Apple's engine or the text the screen reader sent,
not the loader.

---

## Instruments

| switch | what it prints |
|---|---|
| `MEOW_DEBUG=1`, `MTX_DEBUG=1` | the engine's own narration: every word, unit and silence. Read by the engine's own `getenv`, not by the host -- these are Apple's switches, left in the shipping binary |
| `TIGER_FLOAT_STATS=1` | slice timeline, AAC session profile, AU calls |
| `TIGER_ACCEL_DEBUG=1` | every vDSP call, and the overlap-add weights |
| `TIGER_PARAMS=Name=Value;...` | set the engine's own tuning parameters |
| `TIGER_PREF_LOG=1` | every tuning parameter and every dictionary rule compiled |
| `TIGER_PCM_DUMP=<path>` | the decoder's own output, before the engine touches it |
| `TIGER_RESET=1` | put `soReset` back on the interrupt path, to measure it |
| `TIGER_NO_ABBREV=1` | refuse to compile the dictionary's abbreviation rules |

`tools/render_once.py` renders one utterance in a fresh host and exits — which
is what caught the timeline-epoch bug, because the coin flip only looks like a
coin flip when each render has a process to itself.
