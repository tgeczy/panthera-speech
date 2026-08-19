# Running Tiger's MacinTalk as native code

Notes from making Apple's Mac OS X 10.4 speech engine run in a Windows
process. Everything here was measured against the binary rather than
remembered, and each section that reads like a warning is one that cost a
debugging round.

None of this requires an emulator. The engine is i386 code and the host is an
i386 process; what it needs is a *loader*.

---

## 0. Which MacinTalk this is, and why it matters to how Fred sounds

Everything below is **MacinTalk 3.3**, and that is not a detail. Tiger shipped
two different engines, and the difference is audible in principle even though
we cannot demonstrate it.

| | 3.3 | 3.4 | Leopard's 3.6 |
|---|---|---|---|
| source version | 30382 | 30404 | — |
| `__DATA,__cfstring` | **none at all** | **480 bytes, 30 constants** | 4528, 283 |
| `__TEXT,__text` | 223,860 | 206,491 | 438,012 |
| symbols shared with 3.6 | 971 | **1250** | — |
| calls `___commpage_dsmos` | no | **yes** | no |

**3.4 is not 3.3 with a version bump.** It is a rebuild, and it is the turn
toward Leopard.

The `__cfstring` row is the one that carries the argument, because a section
that does not exist cannot be a naming difference or a toolchain artefact.
3.3 has **no tunable parameters whatever** — there is nowhere for them to
live. 3.4 has thirty, and they are recognisably the beginning of the set
Leopard grows to 283: `UnitCost.SpectralWeight`, `UnitCost.AccentCostWeight`,
`UnitCost.DurationWeight`, `UnitCost.VoicedPitchWeight`,
`SegmentAssembly.DurationSlack`, `SegmentAssembly.PitchSlack`,
`PitchDecrease.{Target,Linear,Window,MinWin}`, `PitchIncrease.Window`,
`PitchChange.DetectExcitation`. Those are the knobs of a prosody layer:
how strongly a unit is penalised for the wrong accent, how far a segment's
duration and pitch may be stretched to fit a target, how a pitch fall is
shaped.

`MEOW_DEBUG` and `MTX_DEBUG` are in 3.4 as well, so the engine's own narration
predates Leopard by a generation.

3.4's code is also **smaller** than 3.3's while sharing more symbols with 3.6,
which says rewritten rather than extended.

### What that does and does not license you to say

**It does not prove Fred sounds different in 3.4.** Be careful with the symbol
count in particular: most of the +742 is RTTI and destructors, an artefact of
3.3 having been built with an older GCC that did not emit those into the symbol
table. Do not read a newly *named* function as a new feature.

What is fair to say is this. The machinery that distinguishes Leopard's voice —
a cost model over unit selection, slack on duration and pitch, an explicit
shape for pitch movement — **exists in 3.4 and does not exist in 3.3 at all.**
An engine cannot apply a prosody model it has no parameters for. So 3.4 sits
between the two by construction, and it is likely, though unproven, that its
Fred leans toward Leopard's.

**Why it will probably stay unproven.** 3.4 calls `___commpage_dsmos` — Apple's
"Don't Steal Mac OS X" routine — from six places in the dictionary and three in
the engine, and that call is on the speak path. Off genuine Apple hardware the
engine dies before it produces a sample, so 3.4 cannot be rendered here at all.
Nor can it be heard in emulation: PowerPC Tiger stayed on **3.3** for its whole
life (checked on a real 10.4.11 guest, which reports 3.3), and went straight to
3.6 in Leopard. **3.4 was the Intel port, and Intel-only** — an interlude of
about eighteen months. Only period Intel Apple hardware still running Tiger
could settle it.

### Which is good news, not a consolation

Because PowerPC was Tiger's platform for its entire life, and Intel Tiger
shipped on early Intel Macs for well under two years, **3.3 is not a lucky
variant found on an old disc — it is the Tiger Fred nearly everyone ever
heard.** It is what this project runs, on both architectures, and the engine
described in the rest of this document is the canonical article rather than a
near-miss.

The practical consequence for anyone extracting a disc is in the README: it
must be an **Intel** image (PowerPC discs carry a thin PowerPC MacinTalk with
no i386 slice), and it must be an **early** one, because from 10.4.5 Apple
shipped 3.4. `tools/extract_tiger.py` checks both and names what it found.

---

## 1. What the engine actually is

`MacinTalk.SpeechSynthesizer/Contents/MacOS/MacinTalk` is a **universal**
Mach-O — i386 and PowerPC. Split it before doing anything else. The Intel
slice is 537,388 bytes, `filetype=BUNDLE`.

It is not one synthesiser. `Contents/Resources/SpeechEngineDescription` is
sixteen bytes:

```
00 00 00 02   'mtk3'   'gala'   'meow'
```

and `Info.plist`'s `SpeechEngineTypeArray` lists the same three. Every voice
names its engine in the creator `OSType` at offset 4 of its
`VoiceDescription`:

| creator | engine | voices |
|---|---|---|
| `mtk3` | MacinTalk 3 | Fred, Kathy, Princess, Junior, Ralph, Whisper, Zarvox, Trinoids, plus the novelty and singing voices — 19 |
| `gala` | **MacinTalk Pro** | Bruce, Victoria, Agnes |
| `meow` | a fourth engine | Vicki, alone |

`VoiceDescription` size is a reliable tell: **714** bytes for an `mtk3`
formant voice, **444** for the concatenative ones, and 756–786 for the singing
voices, which carry extra song data. A `PCMWave` sits beside the description
only for voices with a sample bank — Bruce 1.7 MB, Vicki 29.3 MB.

**No voice bundle contains executable code.** They are `Info.plist`,
`VoiceDescription`, `version.plist` and sometimes `PCMWave`. Fred's
`VoiceDescription` is byte-identical from 1993 to 2007, and all 29,303,452
bytes of Vicki are identical between the PowerPC and Intel builds of Tiger. So
voices are portable data; only the engine is architecture-specific.

## 2. The interface is twelve plain C functions

`MacinTalk` exports the Speech Synthesis plugin API by name, unmangled. There
is no Component Manager, no numbered selectors, no resource forks:

| entry | args | |
|---|---|---|
| `SEOpenSpeechChannel` | 1 | `(SpeechChannel *out)` |
| `SECloseSpeechChannel` | 1 | |
| `SESpeakBuffer` | 4 | `(chan, textBuf, byteLen, controlFlags)` |
| `SEUseVoice` | 3 | `(chan, VoiceSpec *, voiceBundle)` |
| `SEUseDictionary` | 3 | |
| `SESetSpeechInfo` / `SEGetSpeechInfo` | 3 | `(chan, selector, info)` |
| `SETextToPhonemes` | 5 | |
| `SEStopSpeechAt` / `SEPauseSpeechAt` | 2 | |
| `SEContinueSpeech` | 1 | |
| `SESpeechStatus` | 2 | |

These are `OSErr` semantics throughout: `SEOpenSpeechChannel` allocates 0x134
bytes and returns **-108** (`memFullErr`) if it cannot.

`SEUseVoice`'s second argument is an 8-byte `VoiceSpec {OSType creator; long
id}`. **The third argument is yours to define** — the engine only ever hands
it back to `CFBundleCopyResourceURL`, so it can simply be the `.SpeechVoice`
directory.

## 3. Loading it

Six steps, and the fifth is the one that looks hard and is not.

1. **Find the i386 slice.** Both `MacinTalk` and `SpeechDictionary` are fat.
2. **Reserve the vm span from `lo & ~0xffff`, commit per segment.** Rounding
   the base down to the 64 KB allocation granularity is what lets a prebound
   library land at its own address. `MacinTalk` is based at vmaddr 0 and must
   always slide — page zero is never mappable.
3. **Apply local relocations** if slid. MacinTalk has 2091 and they all apply
   cleanly.
4. **Apply external relocations.** These are not optional; see below.
5. **Fill the pointer slots.** Walk `__la_sym_ptr2` and `__nl_symbol_ptr` via
   each section's `reserved1` into the indirect symbol table.
6. **Run `__mod_init_func`.** MacinTalk has one.

### Why binding is easy

Calls go through 25-byte PIC stubs in `__picsymbolstub2`, and each one is:

```asm
call  <pic base helper>      ; eax = address of the next instruction
mov   edx, [eax + disp]      ; load the lazy pointer
jmp   edx
lea   eax, [eax + disp]      ; lazy-binding fallback, never reached
push  eax
jmp   <dyld_stub_binding_helper>
```

So writing a real address into the lazy pointer makes the stub jump straight
to it. **There is no lazy binding to implement.**

### The traps

**External relocations carry the addends.** The pointer tables do not cover
them: 48 in MacinTalk, 2 in SpeechDictionary. The stored value *is* the
addend. Skipping them breaks C++ specifically, because the Itanium ABI stores
`&vtable + 8` in an object — word 0 is offset-to-top, word 1 is RTTI, virtuals
start at +8. Without the addend every vptr points eight bytes low and the
first virtual call reads a zero.

**Relocation addresses are relative to the first segment — except in a
`MH_SPLIT_SEGS` image, where they are relative to the first *writable*
segment.** `SpeechDictionary` is split-segment with `__TEXT` at `0x96d0c000`
and `__DATA` at `0xa6d0c000`. Using the `__TEXT` base put its two relocations
at offsets `0x90` and `0x960` — **inside the Mach header and load commands**.
Everything is mapped RWX, so those writes succeed silently.

**A prebound image stores `prebound target + addend`, not the addend.** The
prebound target is in the undefined symbol's `n_value`, so the addend is
`stored - n_value`.

**A slot flagged `INDIRECT_SYMBOL_LOCAL` is not bound to anything.** It
already holds an address inside the image and only needs the slide added.
These deliberately carry **no relocation at all** — 41 such slots in MacinTalk
— so skipping them leaves unslid pointers, and the engine's lookups then
quietly return NULL and fault far away.

**An image must resolve symbols against itself first.** PIC code calls its own
functions through `__picsymbolstub2` so they can be interposed, so a large
share of the slots are intra-image. Binding a library with no dependency list
to search turns *its own* C++ symbols into stubs — vtables included, at which
point the engine uses a stub as a vtable and `vtable+8` lands inside its
instruction bytes.

> Print any unresolved mangled symbol. It is never harmless.

## 4. The support layer

About 96 Apple-specific imports at link time. **Twelve are reached at runtime
for an ordinary utterance** — which is why unresolved symbols should be given
a stub that *records the call and returns*, rather than aborting: one run then
reports the whole set instead of one name per crash.

### CoreFoundation

Only a short chain matters. `SpeechChannelManager::ReadVoiceData` does:

```
CFBundleCopyResourceURL(bundle, CFSTR("VoiceDescription"), 0, 0)
CFURLCopyFileSystemPath(url, kCFURLPOSIXPathStyle)
CFStringGetCStringPtr(path, 0)
open(path, O_RDONLY); fstat; mmap
```

and again for `CFSTR("PCMWave")`.

- A `CFSTR` constant in this binary is `{isa, flags, cstr, length}`. `isa` is
  filled by an external relocation and nothing reads it, so your own strings
  can use the same shape and one accessor serves both.
- Returning non-NULL from `CFStringGetCStringPtr` skips the
  `GetLength`/`GetCString` fallback entirely.
- MacinTalk calls `CFURLCopyFileSystemPath`; **SpeechDictionary calls
  `CFURLCopyPath`**. Both are needed.
- Resources are at `Contents/Resources` in an application-style bundle and at
  `Resources` in a framework version directory. Voices are the first kind,
  SpeechDictionary the second.
- `CFBundleGetBundleWithIdentifier("com.apple.speech.SpeechDictionary")` must
  answer with the framework directory. Return NULL and the dictionary never
  loads, which presents as a synthesiser that runs perfectly and emits nothing.

**`CFRelease` must be forgiving, not correct.** `CFStringGetCStringPtr`
returns a pointer *into* the string, and SpeechDictionary releases the string
before calling `open()` on that pointer. On a real Mac the bug survives because
the block has not been reused yet. Freeing promptly produces an intermittent
`open("g")` — a one-character path salvaged from a recycled allocation —
failing about one run in ten. Retire released objects into a bounded
graveyard instead.

Bundles from `CFBundleGetBundleWithIdentifier` are **not owned** by the
caller, but the engine retains and releases them anyway. Pin them.

### Carbon

- **`AbsoluteDeltaToDuration` takes two `AbsoluteTime`s** — four words.
  Declaring it with one made a worker compute a wake-up 52 hours out and sleep
  through every utterance.
- A `Duration` is **milliseconds when positive and negated microseconds when
  negative**; `kDurationForever` is `0x7fffffff`.
- Multiprocessing Services is real: the back end starts worker tasks and talks
  to them through three-word message queues. Stubbing `MPCreateQueue` returns
  success while handing back a null queue, which is worse than failing.

### libc

- **`PTHREAD_ONCE_INIT` is not zero on Darwin**; it is the signature
  `0x30B1BCBA`. Treating that word as a boolean makes every once-routine look
  as though it had already run.
- `bcopy` and `bzero` take their arguments in the opposite order to
  `memcpy`/`memset`.
- **`__DefaultRuneLocale` is data, not a function.** BSD ctype is a table the
  compiler inlines. The layout is readable straight off the inlined
  `isalpha()`: `__runetype` at 0x34, four bytes per entry, alpha is `0x100`.
- Darwin's `struct stat` is 96 bytes with `st_size` at offset 48.
- `getsectdatafromheader` must answer from the *mapped* image.

### vecLib

`cblas_isamax` finds a block's peak and `cblas_sscal` scales by it. **That
pair is the engine's gain normalisation.** Stub them and the engine renders
perfectly into a clipped channel: peak 32767, RMS 32654 — a square wave that
is exactly the right duration.

## 5. Audio: there is no render callback

`SESpeakBuffer` builds an `AUGraph`:

```
'augn'/'sspl'/'appl'   ScheduledSoundPlayer  --->  'auou'/'def '/'appl'  DefaultOutput
```

A ScheduledSoundPlayer is *given* finished audio, so nothing has to be
rendered on demand. Three properties are set:

| id | property | |
|---|---|---|
| 8 | `StreamFormat` | 22050 Hz, mono, **32-bit float**, packed (`'lpcm'`, flags 0x29) |
| 3301 | `ScheduleStartTimeStamp` | `mSampleTime = -1.0` — start now |
| 3300 | **`ScheduleAudioSlice`** | 92 bytes: the audio |

`ScheduledAudioSlice` on i386: `mTimeStamp` (64 bytes), `mCompletionProc`
(+64), `mCompletionProcUserData` (+68), `mFlags` (+72), `mNumberFrames` (+84),
`AudioBufferList *` (+88). Read the buffer list and you have PCM.
`AUGraphStop` arrives when the utterance is finished, which makes a natural
end-of-speech signal.

### The completion callback is the engine's clock

The slice's completion proc must be called, or the engine never reuses its
buffers. But **calling it synchronously is worse than not calling it**: the
engine is a produce-ahead ring paced by playback, so "that played" arriving
microseconds after scheduling makes it refill before its worker has rendered
anything. It emits an empty slice, gets another instant completion, and spins.

Fire completions from a pacer thread instead, after roughly
`frames / sampleRate`. Arriving on another thread is also closer to the truth
— the real one comes from the render thread, which is why the engine guards
this path with `MPEnterCriticalRegion`.

### And the engine schedules against the wall clock

Pacing completions correctly renders at exactly 1× real time. Completing them
*early* does nothing on its own, because the worker computes its delay from
`UpTime` and simply waits — every pacing factor below 100% produces silence.

**Scale `UpTime` and the duration conversions together** and the whole timeline
compresses. At 128× an utterance takes ~12 ms instead of ~750 ms, and the
output is **byte-identical** to a real-time render for every voice. That
equality is the only reason to trust the trick; re-check it if anything about
pacing changes.

Two Windows details hide this: `Sleep(1)` really sleeps ~15.6 ms without
`timeBeginPeriod(1)`, and once the clock is scaled a per-slice minimum of even
a few milliseconds becomes the entire cost of an utterance.

## 6. Vicki, and the Sound Manager's converter

Vicki's 29 MB sample bank is `mp4a` — **AAC** — decoded through the Sound
Manager:

```
in  format 'mp4a'  1 ch  16 bits  22050 Hz
out format 'NONE'  1 ch  16 bits  22050 Hz
```

That explains both her size and why Apple gave her an engine to herself. Nine
`SoundConverter*` entry points stand between here and her, and the shape of
all of them is legible in `MEOWQTDecoder::Decode(nBytes, data, nFrames, out)`.

### The signature that is not in the header

```c
SoundConverterFillBuffer(sc, upp, refCon, outputPtr, outputByteCount,
                         *actualOutputBytes, *actualOutputFrames, *outputFlags)
```

**Eight** arguments — counted from the eight pushes at `MacinTalk+0x2630b` and
the `add esp, 0x20` that follows the call. Getting the count wrong is worse
than getting nothing: the fifth is a *count*, so an implementation written with
seven treats it as the `actualOutputBytes` pointer and stores through 46976 as
an address, turning a silent voice into a crashing one. The engine's own loop
reads
`kSoundConverterHasLeftOverData` (bit 1) out of `outputFlags` and calls again
while there is budget left and the last call returned something.

### The descriptor is extended, and says so

The fill callback is a static wrapper around
`MEOWQTIterator::FetchData`, which hands over a descriptor with
`kExtendedSoundData` (`1 << 14`) set in `flags`. That bit means the fields past
`reserved` are live:

| offset from `desc` | | |
|---|---|---|
| `+0x1c` | `recordSize` | 68 |
| `+0x20` | `extendedFlags` | 7 — sampleCountNotValid, bufferSizeValid, frameSizesValid |
| `+0x24` | `bufferSize` | compressed bytes |
| `+0x28` | `frameCount` | number of AAC access units |
| `+0x2c` | `frameSizes` | `long[frameCount]` |

So `sampleCount` is zero because the engine **declares it invalid**, not
because a pull failed — which is what it looks like, and what stalled this for
a session. The compressed blob is a big-endian `u16` size table followed by the
payload, and `buffer` already points past the table.

The wrapper returns `desc->buffer != NULL`, and `FetchData` nulls that pointer
on every call after the first: the whole unit arrives in one pull.

Each call is one unit of the voice's database. The sizes sum to `bufferSize`
exactly, which makes the reading self-checking — worth asserting, because a
wrong reading here writes into someone else's memory.

### 2112

The engine sizes its output buffer at `frameCount * 1024 - 2112` frames —
every unit, every time, across seven units of wildly different lengths. That is
Apple's AAC priming delay written into the arithmetic: their converter dropped
the codec delay and returned the rest. Windows' decoder returns everything, so
the host drops the difference.

Decoding one unit twice — once through Media Foundation, once through an
unrelated decoder — gives 25600 samples both times, matching to within a least
significant bit at alignment offset zero.

### The recipe Windows actually wants

The AAC decoder MFT is configured through `MF_MT_USER_DATA`, which by the
documentation carries the `HEAACWAVEINFO` tail *followed by the
AudioSpecificConfig*. Do exactly that and this decoder ignores the sample rate
and channel count it was just given, falls back to 44100 stereo, and then
refuses 22050 mono output — while `SetInputType` still returns `S_OK` and reads
back correctly, so nothing looks wrong until the output type is rejected.

Passing the **bare twelve bytes**, with no config appended, makes it take both
from the media type. Six recipes were tried side by side; that is the one that
works. The config is still parsed here, as a cross-check that the stream really
is AAC-LC at the rate the voice claims.

## 7. What is not done

Nothing in the engine, now. `SoundConverterConvertBuffer` — the fixed-rate
path, taken when the decoder's frame size is positive — is still a logging
stub, because no voice in Tiger has ever reached it.

The 2.1 MB `StdDictionary` handles number expansion, abbreviations and
homographs natively, so nothing above the engine has to normalise text — a
large practical benefit of carrying the whole dictionary rather than
reimplementing the front end.
