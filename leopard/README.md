# Leopard-speech

**Alex speaks, and he speaks cleanly.** Mac OS X 10.5 Leopard's speech engine
running as native x86 code inside NVDA — no emulator, no virtual machine — and
with it **Alex**, which is the voice people actually want.

Its sibling [tiger-speech](https://github.com/tgeczy/tiger-speech) does the
same job for 10.4: all twenty-three Tiger voices, natively, about twelve
milliseconds an utterance. The loader there is the starting point here, and
most of it transfers unchanged.

## How the two repositories fit together

**There is one loader, and it lives in tiger-speech.** `build.sh` here builds
that sibling checkout and copies the result in as `leopard_host.exe`, so the
two add-ons ship the same binary under different names.

That is deliberate, and it paid for itself twice in a single day. The defect
that made Alex crackle was in a decoder path **only Leopard's engine takes** —
Tiger's engine goes through a different API entirely — and fixing it left
Tiger's renders byte-for-byte identical. One loader, per-engine paths inside
it. Forked, that fix would have had to be found twice.

| | lives in |
|---|---|
| the loader, the shims, the host | **tiger-speech**, `src/` |
| the Mach-O dissection tools | **tiger-speech**, `tools/` |
| Tiger's driver, voice list and extractor | tiger-speech |
| Leopard's driver, voice list and extractor | here, `tools/extract_leopard.py` |

Clone them as siblings, because `build.sh` looks next door for the loader:

```
C:\git\tiger-speech
C:\git\leopard-speech
```

The dissection tools take a symbol name or an address and work on either
engine, which is how nearly everything below was established:

```
py -3 ..\tiger-speech\tools\machosyms.py <binary>
py -3 ..\tiger-speech\tools\machodis.py <binary> <symbol-or-0xADDR> [length]
py -3 ..\tiger-speech\tools\render_once.py "text" Alex 180 out.wav
```

`render_once.py` renders exactly one utterance in a fresh host. That matters
more than it sounds: with a resident host every render after the first is
measured in a warmed process, and giving each one its own process is what
turned "sometimes it sounds wrong" into two exact frame counts alternating run
to run — which is a thing you can chase.

## Why Leopard at all, when Tiger works

Because Alex only exists on Leopard, and **Tiger's engine cannot read him.**

That was measured, not assumed. Alex's sample bank is a `meow` container like
Vicki's, but a later version of it:

| voice | magic | version | header |
|---|---|---|---|
| Vicki, Tiger | `meow` | 1.0.4 | 0x28 |
| Vicki, Leopard | `meow` | 1.0.4 | 0x28 — byte-identical to Tiger's |
| **Alex** | `meow` | **1.0.6** | **0x30** |

Eight more bytes of header. Tiger's engine reads the 1.0.4 layout, so its
cursor into Alex's demi table is eight bytes out and it walks into nothing.
`SEUseVoice` and `SESpeakBuffer` both return `noErr` first, which is the engine
accepting the voice and then misreading it — an encouraging-looking dead end.

## What already works

Carried over from tiger-speech, all of it measured against Leopard's own
binaries:

- **The images load.** Leopard's `MacinTalk` and `SpeechDictionary` map,
  relocate and bind.
- **`__IMPORT,__jump_table` binding.** Leopard's engine is not PIC: it has
  five-byte stubs that arrive as `0xf4` padding for dyld to overwrite with
  `jmp rel32`. Binding only the pointer sections leaves them, and the first
  call executes a privileged instruction. 284 stubs now bind.
- **`$UNIX2003` symbols.** Leopard's libSystem publishes conformance variants
  (`_open$UNIX2003` and twelve more); the shim lookup falls back to the name
  before the `$`.
- **`operator new`.** A thunk returns 0, the engine writes through it, and it
  dies in `SEOpenSpeechChannel`. Written out along with `_List_node_base`.
- **All five initializers run.**
- **`SEOpenSpeechChannel` returns `noErr`.** All four dictionaries the channel
  manager wants -- `PrefixDictionary`, `CartNames`, `CartLite` and
  `SymbolDictionary` -- map to their own files. See below for what stood in the
  way of that for a day.
- **`SEUseVoice` and `SESpeakBuffer` return `noErr`**, and the engine reads the
  voice's `VoiceDescription`.
- **The engine runs.** It builds an `AUGraph`, negotiates a stream format of
  22050 Hz mono float, starts it, and spins up its own worker threads, which
  tick through `Parse`, `Audio?`, `Samples` and `Ping`.
- **AudioConverter**, which is how Alex decodes where Vicki uses the Sound
  Manager. Implemented and flushed for the Windows 7 decoder quirk.

## The bug that looked like Apple's and was ours

Worth writing down, because it wasted a day and every hypothesis it generated
was wrong in the same way.

`SEOpenSpeechChannel` crashed inside `SLCartDict::SLCartDict`, reading
15,336,982 bytes past a 1,638,242-byte mapping of `PrefixDictionary`. The
arithmetic matched the faulting address to the byte, which made the conclusion
irresistible: the engine had built the wrong class over the wrong file, and the
question was why Apple's own code would do that.

It doesn't. Disassembling `SpeechChannelManager::ISpeechChannelManager` shows
six `CFBundleCopyResourceURL` calls, and the two dictionaries it wraps in
`SLCartDict` are `CartNames` and `CartLite` -- never `PrefixDictionary`. Those
two are then merged into an `SLSplitCartDict`. The engine was right all along.

The real cause was in `SLMMapCache::Map(const char *)`, which nothing had
looked at because it appeared to be working. It stats the path and then walks
its cache list comparing exactly the first eight bytes of the stat buffer --
`st_dev` and `st_ino` -- and nothing else. The loader's `stat` shim zeroed the
whole buffer and filled in only `st_size`, so **every file on disk answered to
the same key**. `PrefixDictionary` mapped correctly; the six after it were
served its bytes straight from the cache, without ever reaching `open()`.

The clue was there the whole time and read backwards: one `open()` for seven
resources looks like six lookups failing, when it was six cache hits
succeeding. Every one of the four hypotheses ruled out below was ruled out
correctly. The cause was somewhere nobody had thought to suspect, because it
was the part that was *not* failing.

Two smaller traps came with it:

- **An anonymous local function inherits the previous global symbol's name.**
  The crash frame read `SpeechChannelManager::UseVoice + 0x6fe`, and `UseVoice`
  really is the nearest preceding symbol -- but the function at that address
  starts at `+0x6c8`, after a complete epilogue, and is a static the linker
  never named. A day of reading the wrong function.
- **A spilled PIC base looks exactly like a return address**, which is already
  written down in the loader's notes and caught nobody by surprise this time.

## Where it is now: Alex speaks, cleanly

Confirmed by ear on 2026-08-18, first that he spoke at all and then — after
the two bugs below — that he was clean. Numbers are spoken as numbers, a
telephone number is read digit by digit the way Apple intended, and the
phrasing dictionary is live.

Getting there needed four things, and three of them were the loader's fault
rather than the engine's.

**Accelerate.** Alex is concatenative, so changing his rate means time-scaling
recorded speech rather than re-running a model. `MTMBModRateWsola` does it with
WSOLA — waveform similarity overlap-add — and WSOLA is a search. Apple sent the
search to vDSP, and with those stubbed Alex ran to completion and produced one
frame of nothing. Nothing failed; the only evidence was a counter reading
58,186,903 calls into an empty function. The signatures were counted off the
call sites rather than remembered, which mattered: `vDSP_vmsb` takes nine
arguments and computes `A*B − C`, `vDSP_vmma` takes eleven and computes
`A*B + C*D`, and `vmul` is vecLib's older seven-argument spelling.

**Alex is AAC**, like Vicki — but fetched completely differently. The engine
maps only the first 77,114,248 bytes of his 701 MB bank, which is the index and
is exactly the value at `+0x28` of the `meow` header, then `pread`s each
waveform grain out of the remaining 624 MB. That is Apple loading it
chunk-by-chunk, visible in the log.

**So something written for Vicki is wrong for him.** `aac_flush_delay`
re-feeds the last packet to shake loose the frame Windows 7's decoder holds
back; on one long stream the duplicates land past the end, but on Alex the
packet *is* the payload and it arrived three times over.

The priming needed the opposite correction, and getting that backwards is what
made him crackle for a day — see below.

**And the decoder has to stay open.** `aac_begin()` sends `COMMAND_FLUSH`, and
AAC frames overlap: a frame is not finished until the next one's window is
added to it. Flushing between packets left a seam at every 1024-sample
boundary, which is audibly a stutter — one gap per frame. One packet in also
yields nothing out, so a refill has to keep pulling until the decoder gives
something back.

### The crackle, and what it actually was

For a day Alex was perfectly intelligible — Whisper transcribed him without a
mistake — and sounded like a skipping CD. It was **two** defects, neither of
them anywhere near the codec, and the elimination list is kept here because
every item on it cost hours:

Not a framing seam (the largest sample-to-sample jumps are not periodic at 229,
256, 512, 1024 or 2048). Not clipping (peak 13638 of 32767). Not the output
format. Not a `pread` race, made atomic anyway. Not nondeterministic. **Not
WSOLA, and not any vDSP shim** — Vicki makes zero vDSP calls and was broken in
exactly the same way. Not the engine's clock. Not `AudioUnitGetProperty`, which
Leopard's engine imports and never calls.

**One: the timeline restarts.** The engine schedules an utterance in *epochs*,
and every epoch starts its sample clock again at zero. We placed each slice at
its absolute sample time, so the second epoch was written straight over the
first and whole words disappeared. It only happened about half the time — the
same sentence came back either as one continuous timeline of 98900 frames or as
two epochs of 48505 and 50395, run to run, and only the long one had all the
words in it. That is why it read as memory corruption rather than as a bug with
a shape.

**Two: the AAC priming was never dropped.** There are two decoder drivers over
one decode core. Tiger's engine drives `SoundConverter`, which decodes a
self-contained unit at a time and takes 2112 samples of codec delay off each
one; that path has been byte-perfect for months. Leopard's engine drives
`AudioConverter`, which streams, and that path trimmed nothing — so every unit
reached the engine 96 ms late, twenty-three of them in one Vicki utterance.
Words survive that individually, which is exactly why it stayed intelligible
and merely sounded like it was skipping.

The comment justifying the missing trim said Apple sets
`kAudioConverterPrimeMethod` to None, so there is no priming to drop. That
conflates two things: `'prmm'` None describes what *Apple's* decoder does, while
Media Foundation's emits the delay regardless — and the engine sizes its output
buffer at `frameCount * 1024 - 2112`, which is Apple's priming written into the
arithmetic. The other half of that comment was right, and is why the fix has a
shape rather than being a revert: 2112 cannot come off a 1024-sample refill
without deleting it. The trim belongs to the **stream**, carried across refills
until spent.

**What found it** was neither reading nor reasoning. It was a listener saying
"vicki-leopard-engine.wav is very garbled" — which killed the theory that this
was Alex or his container — and then "Bruce speaks 'one, two, three. ch api
version 4'", which proved whole *words* were missing. Bruce is a formant voice,
so AAC, the sample bank and Accelerate were all irrelevant at a stroke.

### Numbers, phrasing, and the dictionary

Leopard's `SpeechDictionary` needs two things Tiger's does not.

**POSIX regex.** It compiles exactly one pattern at channel open —
`^[[:digit:]]{7,}$`, a telephone number, to be read digit by digit rather than
as a quantity. **`regexec` returns 0 for a match**, so a stub returning zero
told the dictionary that every word was a phone number, and every number in
every utterance was spelt out: "one, two, three" where Tiger says "one hundred
twenty three". What is implemented reads only the shape this framework actually
contains and refuses anything else out loud.

**SQLite.** `Resources/Tuples` is a real SQLite 3 database, 628736 bytes, one
table of 12891 multi-word keys — the phrasing table. No copy of SQLite is
carried: Windows has shipped `winsqlite3.dll` since 10 1803 and there is a
32-bit build in SysWOW64, which is this process's bitness because Apple's engine
is i386. The trap is that Microsoft's build declares `SQLITE_APICALL` as
`__stdcall` where upstream SQLite is cdecl, so the first version of that shim
was worse than none at all.

Both are instances of one lesson: **a stub returning 0 is not neutral.** For
`sqlite3_step`, `regexec` and `AudioUnitGetProperty` alike, zero means *success*
or *match*, so an unimplemented function confidently asserts something false.

## The stack alignment nobody can skip

Worth its own heading, because it is invisible until it is not, and it applies
to any Darwin i386 code run on Windows.

**Darwin's i386 ABI requires ESP to be 16-byte aligned at every call
instruction. Windows requires four.** Apple's compiler used the guarantee:
`MTBEWorker::Timestamp` stores a pair of doubles into its own frame with
`movapd`, which faults outright when the address is not 16-byte aligned.

Inside the engine it can never break, because every frame preserves the
alignment it was given. It breaks wherever the host hands control over, and a
thread entry point is the worst case -- the alignment a Windows thread starts
with is neither ours to choose nor the same on every run. Leopard's engine
starts two MP tasks and died at the first `movapd` one of them reached, with
`ebp-0x18` sitting at 8 mod 16.

Tiger never showed this. That is not evidence it was safe; it is one compiler
declining to vectorise one function.

## What is still unshimmed

Leopard's binaries import a great deal more than Tiger's, but the *linked*
surface badly overstates the *executed* one — Tiger's engine linked 44
undefined symbols and called six. So the host reports which stubs were actually
**reached** at exit, and that list is the one worth working from.

Since done: sqlite3, POSIX regex, the Accelerate routines, and
`AudioUnitGetProperty` — which turned out never to be called, and is
implemented anyway because "succeeds and returns garbage" is the failure mode
that cost the most time here.

Still outstanding if the engine ever reaches them: a CoreFoundation collection
subset, `sgesvd_` from Accelerate, AudioFile, and Mach messaging.

**libstdc++ is not shimmed and must not be.** Leopard's engine links against
`/usr/lib/libstdc++.6.dylib` and the loader maps Apple's own copy as a third
image, because GCC 4.0.1's copy-on-write `basic_string` layout has to match
exactly and the engine inlines code that touches it.

## What the NVDA driver does with a speech sequence

Worth stating, because three separate user reports in one morning all came back
to the same misunderstanding: **adjacent strings in a speech sequence are not
separate utterances.** NVDA inserts an `IndexCommand` only where a callback sits
or an utterance genuinely ends, so a line of a web page with a link in it
arrives as several plain strings with nothing between them. Rendering each one
alone gives every fragment the falling intonation of a finished sentence, which
is heard as the synthesizer pausing before every link. They are joined here up
to the next index, which costs nothing in index accuracy and leaves say-all
alone.

Also honoured: `BreakCommand`, and `PitchCommand` — which is how NVDA expresses
"capital pitch change percentage", and dropping it made that setting inert at
any value. Volume is the engine's own `[[volm]]` command rather than gain
applied to the samples afterwards: measured, it is exactly linear on both
engines, so the synthesizer does the arithmetic before it quantises.

Text reaches the engine as **MacRoman, not UTF-8**. Its front end reads a
single-byte Mac encoding, and sent as UTF-8 an em dash arrived as three bytes
and was read a character at a time — a tester heard Alex say "AI" wherever a
story used one. MacRoman already has the em dash, the curly quotes and the
ellipsis, so encoding properly is the whole fix.

## Getting the engine

The same rule as tiger-speech, and it is not a formality: **no part of Apple's
software will ever be distributed here.** You supply your own Leopard install
disc and the extractor takes the engine out of it:

```
py -3 tools/extract_leopard.py "Mac OS X Leopard Install DVD.iso"
```

That writes to `%APPDATA%\nvda\leopard-data`, which is where the add-on looks.
It takes about ten seconds and produces **all twenty-four voices**, Alex
included. `--no-voices` gets you the engine, the dictionary and Fred in a few
megabytes, which is enough to confirm it works before committing to Alex's
701 MB. `--out` puts it somewhere else.

Verified the strict way: a tree extracted straight from the DVD renders
**byte-for-byte identical audio** to one assembled by hand.

### Why that is not four lines of 7-Zip

The install DVD hides its filesystem behind a small ISO9660 boot partition, so
a plain listing shows only the Boot Camp documentation. The real one is an APM
partition map:

```
7z l -tapm "Mac OS X Leopard Install DVD.iso"
  Apple.Apple_partition_map            30,720
  Macintosh.Apple_Driver_ATAPI    421,261,312
  Mac_OS_X.hfs                  7,634,907,136   <- everything is in here
```

Inside that HFS volume the engine, the dictionary and **Fred** are live, so
the smallest useful extraction needs no package handling at all:

```
System/Library/Speech/Synthesizers/MacinTalk.SpeechSynthesizer/
System/Library/Speech/Voices/Fred.SpeechVoice/
System/Library/PrivateFrameworks/SpeechDictionary.framework/
usr/lib/libstdc++.6.0.4.dylib
```

7-Zip can list that partition map, and it can read an HFS volume that is a
file on its own — but it cannot be pointed at a partition *inside* another
file, and it will not read HFS from a pipe. The alternative was to copy 7.6 GB
out to a temporary file first, so the extractor reads the HFS+ catalogue
itself, at the offset the partition map gives, and touches only the bytes it
needs. The catalogue is a B-tree; rather than implement HFS+'s
case-insensitive Unicode key ordering, which is fiddly and easy to get subtly
wrong, it walks every leaf node and builds the tree in memory. About 49,000
records, under a second.

The remaining voices come out of packages, and they are split across two of
them in a way the names actively mislead about:

| package | holds |
|---|---|
| `AdditionalSpeechVoices.pkg` | Alex and Vicki only — but 707 MB of them |
| `Essentials.pkg` | the twenty-two classic MacinTalk 3 voices |

Take only the first, which its name invites, and you get Alex while losing
Agnes, Bruce, Victoria and every singing voice. That was found by reading each
package's bill of materials — a small bzip2 member in the same archive, with
the voice names as plain strings inside it — rather than by streaming all
forty of them.

Leopard uses **flat** packages, unlike Tiger's bundles: a `.pkg` is a `xar`
archive whose `Payload` member is a gzip stream, and inside that is **cpio**,
in the old `070707` portable ASCII format. Not tar — `tarfile` rejects it
outright, and that is the trap that caught the Tiger extractor first.

The add-on offers to open the data folder for you if it cannot find one, and
says exactly which piece is missing rather than simply not appearing in the
synthesizer list.

## Licence

MIT, as with tiger-speech and outspoken-nvda. It covers the driver, the loader
and the shims — the work of making Apple's engine run somewhere it was never
built to run — and **not the engine itself**, which is Apple's and is never
distributed here.
