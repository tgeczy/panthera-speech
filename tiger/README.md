# tigerspeech

NVDA speaking with the voices Apple shipped in **Mac OS X 10.4 Tiger**,
including Fred as he sounded in 2005 — which is not the Fred any current
system will give you.

**Apple's engine runs as native code on your machine.** No virtual machine, no
emulator, no CPU translation. A small 32-bit host process maps Tiger's Intel
`MacinTalk` and `SpeechDictionary` into memory, fills the pointer slots `dyld`
would have filled, and calls `SESpeakBuffer` directly.

An utterance costs about **twelve milliseconds**.

One add-on of several in [panthera-speech](../README.md). The loader it runs on is shared, and lives at the repository root in `src/`;
`sh ../build.sh` builds it and stages it into every add-on here.

## Licence, and what is being licensed

**MIT** — see `LICENSE`.

What that covers is the loader, the driver, the shims and the tools: the work
of making Apple's engine run somewhere it was never built to run. It does not
and cannot cover Apple's engine, which is not distributed here and is not
anyone's to relicense. You bring your own copy of software Apple stopped
shipping in 2007.

## No Apple bytes here

This repository contains no part of Apple's software, and `package.py`
**refuses to build an add-on that does** — it rejects the engine, the
frameworks, the dictionaries and the voice data by name and by extension. You
supply your own Tiger install.

That refusal is the point, not a formality. Run it and see:

```
$ py -3 ../panthera/package.py
REFUSING to package -- these are not ours to distribute:
   synthDrivers/_panthera/MacinTalk
   synthDrivers/_panthera/VoiceDescription
```

## Getting the engine

### Check your image before anything else

Two things have to be true of the disc, and the second one turns away most
Tiger media in circulation. **Check what you already have first**, and only go
looking elsewhere if it does not match.

**It must be an Intel image.** This loads i386 code, and that is the only
architecture it can load. A PowerPC Tiger disc is not a slower option, it is
not an option: its `MacinTalk` is a thin big-endian PowerPC binary with no
i386 slice in it. Intel discs carry a fat `i386, ppc` binary, which is the one
that works.

**It must carry MacinTalk 3.3, which means the earliest Intel Tiger** — the
10.4.4-era disc that shipped with the first Intel Macs. From 10.4.5 onward
Apple shipped **MacinTalk 3.4**, which calls `___commpage_dsmos`, Apple's
"Don't Steal Mac OS X" routine, from six places in the dictionary and three in
the engine. On a genuine Apple machine a kernel extension answers that from
the SMC. Off it, the call goes nowhere and the engine dies.

**We are not going to answer it.** Providing that routine means shipping a
fake SMC response whose only purpose is to defeat a copy-protection check —
the thing Psystar was sued over — and that is a different category from
shimming `mmap` or `printf`, even though you supply your own licensed disc.
So 3.4 is not a "not yet"; it is a no.

`tools/extract_tiger.py` checks both of these while it extracts and names what
it found, so you learn this in a second rather than from a silent synthesizer.

**If your disc is 10.4.5 or later**, the answer is not a different add-on
setting, it is [leopard-speech][leopard] — Leopard's MacinTalk 3.6 dropped the
check again, and it carries Fred and the whole MacinTalk 3 roster as well as
Alex. What you lose is that Leopard's Fred is a later engine; see
[docs/macintalk-3.3.md](docs/macintalk-3.3.md) for what changed and why 3.3 is
the Fred nearly everyone remembers.

[leopard]: https://github.com/tgeczy/leopard-speech

### Where it lives on the disc

Everything needed is inside one package:

```
Mac OS X 10.4 Tiger for Intel .iso
 └ System/Installation/Packages/Essentials.pkg
    └ Contents/Archive.pax.gz
       └ Archive.pax
          └ ./System/Library/Speech/     <- engine and all 23 voices
```

**There is a tool for this.** It reads your own image, needs 7-Zip, and writes
straight into the right folder:

```
py -3 tools/extract_tiger.py "Mac OS X 10.4 Tiger.iso"
```

It descends into partitions — a retail DMG hides the install filesystem behind
a small ISO9660 boot partition — and it takes **two** packages, because
`Essentials.pkg` holds the engine and 22 voices while `AdditionalSpeechVoices
.pkg` holds Vicki on her own. Taking only the first yields 22 voices, which
looks exactly like a complete extraction.

To do it by hand instead: extract that `Speech` folder plus
`SpeechDictionary.framework`, and drop the directory containing them into:

```
%APPDATA%\nvda\tigerspeech-data\
```

**Not** into the add-on folder — updating an add-on deletes and recreates its
directory, which would take a few hundred megabytes of extracted engine with
it. To keep the tree on another drive instead, write its path into
`tigerspeech-data.txt` beside that folder, or set `TIGER_TREE`.

The driver hides itself when it cannot find a usable tree, so a missing
install means the synthesizer simply is not offered rather than being
selectable and silent.

## Three engines, one bundle

Tiger's `MacinTalk` is not a single synthesiser. Its `SpeechEngineDescription`
is sixteen bytes — `00000002 'mtk3' 'gala' 'meow'` — and every voice names its
engine in the `VoiceDescription` beside it:

| engine | voices |
|---|---|
| `mtk3` — MacinTalk 3 | Fred, Kathy, Princess, Junior, Ralph, Whisper, Zarvox, Trinoids, and the novelty and singing voices (19) |
| `gala` — **MacinTalk Pro** | Bruce, Victoria, Agnes |
| `meow` | Vicki, alone |

**All twenty-three speak**, confirmed by ear. Vicki took the longest: her
29 MB sample bank is **AAC**, decoded through the Sound Manager's
`SoundConverter`, which is why Apple gave her an engine to herself and why she
was silent here until the host learned to answer that call.

It answers it with the AAC decoder Windows already ships, bound at run time so
that a machine without one — a Windows N install with no Media Feature Pack —
loses Vicki rather than the whole synthesizer. The driver checks for the
decoder before offering her at all: choosing a silent voice mutes the screen
reader, and the user then cannot hear the voice list well enough to choose
their way back out, which is a far worse failure here than a missing voice.

**Vicki therefore depends on your copy of Windows having an AAC decoder**, and
that is the one part of this add-on whose behaviour is not ours. Windows 7 and
later ship Media Foundation's AAC decoder; the editions that do not are the
**N** and **KN** ones until the Media Feature Pack is installed. Everything
else here is Apple's code running identically on every machine — Vicki alone
is not.

Decoders differ between versions of Windows, and one difference is audible.
**Windows 7's returns exactly one AAC frame fewer** than Windows 10 and 11 do
— it holds the last frame back rather than dropping the first — and working
the codec delay out from how much arrived therefore gave a different answer
there, starting every unit 1024 samples early. That is what Vicki's syllables
running together was.

The host now pushes each decoder's own latency out of it before draining, by
feeding the last packet through again and discarding the result. Every decoder
then yields at least the whole stream, the priming is 2112 everywhere, and the
version stops mattering. To check a machine:

```
tiger_host.exe --aac-check
```

It needs no engine, no voices and no arguments, and reports whether a decoder
is present and what it offers.

Two things about that path are worth knowing, because both cost a day:

- `SoundConverterFillBuffer` takes **eight** arguments — the engine pushes
  eight and adds 32 to the stack pointer — and the fifth is the output
  buffer's size in bytes. An earlier attempt here wrote it with seven and so
  treated that size as the `actualOutputBytes` pointer, storing through 46976
  as an address. That is how a silent Vicki became a crashing one.
- The compressed data arrives as an `ExtendedSoundComponentData`: the engine
  sets `kExtendedSoundData` in `flags` and declares `sampleCount` **invalid**,
  putting the real description — byte count, access-unit count, and a table of
  per-unit sizes — in the fields past `reserved`.

The engine asks for `frameCount * 1024 - 2112` frames every time, which is
Apple's AAC priming delay stated out loud; the host drops exactly that many.
Decoding one unit through Media Foundation and through an unrelated decoder
gives the same 25600 samples with a best alignment offset of zero.

## Rate, pitch, and embedded commands

Rate spans 80–400 wpm. **Above about 320 wpm MacinTalk 3 divides by zero**
interpolating segment durations — a latent bug that never mattered on PowerPC,
because `divw` does not trap there. The host survives it the way the original
hardware did rather than capping the rate, since fast speech is exactly what
experienced screen reader users want.

Pitch is an **offset in semitones from each voice's own pitch**, not an
absolute value: Fred sits near 127 Hz and Bruce near 135, so an absolute scale
would make the middle of the slider mean something different for each voice.
The host asks the engine for the voice's `'pbas'` and adds to it. The ends of
the slider are an octave either way — measured, 66.4 Hz and 256.4 Hz against
Fred's 126.7 Hz.

The front end also parses **embedded speech commands**, and they all work:

| | |
|---|---|
| `[[slnc 2000]]` | measured +2.06 s of silence |
| `[[rate 100]]`, `[[pbas 80]]`, `[[pmod 2]]` | rate, pitch, inflection |
| `[[volm 0.5]]` | peak 12615 against 25231 — exactly half |
| `[[inpt PHON]]`, **`[[inpt TUNE]]`** | phoneme input, and singing |
| `[[cmnt …]]` | consumed, output byte-identical to plain |

They are **off by default** and removed from the text, because a web page or a
file name containing `[[` could otherwise change how the screen reader sounds.
Turn them on in the synthesizer settings. Independently, the host re-applies
rate and pitch on every utterance, so no command can outlive the sentence it
appeared in.

## Why it exists

Fred's voice file never changed. The 714-byte `VoiceDescription` is
byte-identical from System 7 in 1993 to Leopard in 2007 — same MD5, across a
CPU architecture change and an entirely new operating system. Everything people
remember as sounding different is the **engine** around him.

Nor is any of it architecture-specific: no voice bundle contains executable
code, and all 29,303,452 bytes of Vicki are identical between the PowerPC and
Intel builds of Tiger.

## Speed

The engine schedules against the wall clock, so the host runs its clock 128×
fast and paces slice completions to match. Output is **byte-identical** to a
real-time render for every voice — that equality is what makes the trick safe.

| | |
|---|---|
| startup, including the 2.1 MB dictionary | ~21 ms |
| an utterance | ~12 ms |
| a full sentence, any voice | 34–84 ms |

Set `TIGER_SPEED=1` to render in true real time if you ever need to compare.

## Building

```
sh build.sh                  # -> build/tiger_host.exe   (32-bit, MSVC)
py -3 panthera/package.py    # -> pantheraspeech-N.nvda-addon
```

**The host is 32-bit because Apple's engine is i386, and there is no second
build to make** — a 64-bit process cannot load i386 code at all. Keeping it in
its own process is what makes the add-on indifferent to NVDA's own bitness: the
same binary serves 32-bit NVDA 2023.1 and 64-bit NVDA 2026.1. (The sibling ROM
add-on loads its emulator in-process, so it does ship one DLL per
architecture.) The driver is plain Python and parses under 3.7, which is what
NVDA 2023.1 runs.

Useful while working on it:

```
py -3 tools/speak.py "hello there" Fred 180       # drive the host directly
py -3 tiger/tools/test_driver.py                  # the driver, outside NVDA
```

## Notes for anyone reading the host

A few things cost a debugging round each and are worth knowing:

- `PTHREAD_ONCE_INIT` is **not** zero on Darwin; it is the signature
  `0x30B1BCBA`. Treating that word as a boolean makes every once-routine look
  as though it had already run.
- Relocation addresses are offsets from the first segment — **except** in an
  `MH_SPLIT_SEGS` image, where they are from the first *writable* segment.
- A pointer slot flagged `INDIRECT_SYMBOL_LOCAL` carries no relocation at all;
  it just needs the slide added.
- An image must resolve symbols against **itself** first. PIC code calls its
  own functions through `__picsymbolstub2`.
- `cblas_isamax` and `cblas_sscal` are the engine's gain normalisation. Stub
  them and you get a perfectly synthesised square wave.
- `Sleep(1)` really sleeps 15.6 ms without `timeBeginPeriod(1)`.

## Layout

```
panthera/addon/synthDrivers/tigerspeech.py          the NVDA driver
panthera/addon/synthDrivers/_panthera/pantheratiger.py   finding the user's engine
panthera/addon/synthDrivers/_panthera/panthera_host.exe
panthera/addon/globalPlugins/pantheraData.py        first-run "your engine folder is empty"
panthera/tests/tiger/                               the driver rules, as regressions
panthera/package.py                                 build the .nvda-addon
src/tiger_host.c                                    the loader and the shims
tiger/tools/extract_tiger.py                        get the engine from your own disc
tools/speak.py                                      drive the host from a command line
tools/check_clean.py                                refuse to ship anybody's disk layout
docs/macintalk-3.3.md                               how the engine works
bridge/                                             the original QEMU bridge, kept as an oracle

The driver, the plugin and the host are shared with Leopard: since 0.9.0 both
generations are one add-on declaring two synthesizers. What stays here is
Tiger's extractor and the engine notes.
```

## Status

**0.6** — **all 23 voices speak**, Vicki included, on every version of Windows
that has an AAC decoder. Rate, pitch and embedded commands are working and
confirmed by users; the driver is verified under **Python 3.7** (NVDA 2023.1)
and **32-bit Python** as well as current NVDA.

What 0.6 fixed, both found by users rather than by testing:

- **Windows 7 returns one AAC frame fewer** than later versions, which put
  Vicki's audio 1024 samples out of place. The host now flushes each decoder's
  own latency instead of inferring it, so the version no longer matters.
- **The singing voices went permanently silent on long text.** They render
  several times more audio per character than the rest, so an ordinary long
  message reached a guard meant to catch a stalled pipeline — and tripping it
  stopped the engine's clock. It now counts *empty* slices rather than all of
  them. The same bug had been silently truncating long messages with those
  voices for as long as they have existed.

`pytest tests/` covers the driver rules, that a machine with no AAC decoder
loses Vicki from the list rather than gaining a silent entry, and that a long
utterance cannot leave the engine unable to speak. `tools/check_clean.py`
fails the build if anything shippable mentions a particular machine.
