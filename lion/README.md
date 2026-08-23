# lionspeech

**The last MacinTalk Apple ever shipped, running as native x86 code inside
NVDA.** Mac OS X 10.7 Lion's speech engine — no emulator, no virtual machine —
and with it a second Alex: a later, smaller recording than Leopard's, and one
that measures cleaner.

One add-on of several in [panthera-speech](../README.md). Its siblings
[tigerspeech](../tiger/README.md) and [leopardspeech](../leopard/README.md) do
the same job for 10.4 and 10.5, and the loader is shared with both.

**The engineering notes are in
[`docs/macintalk-4.0.md`](../docs/macintalk-4.0.md)** — compressed dyld info,
Grand Central Dispatch, the property API, the FFT — and they are not repeated
here. This file is about *getting* the engine, and about what makes 10.7
different to live with.

## Why Lion, when Leopard already speaks

Three reasons, and the first is the honest one.

**It is the end of the line, and worth reaching.** 10.8 Mountain Lion has no
i386 slice of MacinTalk at all, so 10.7 is the last generation this approach
can run. Apple's own build path says `SpeechSynthesis-4.0.74` where Leopard
says `3.6.59` — and by 10.7 MacinTalk had been split into a project of its
own, `SpeechSynthesis_MacInTalk`, which is not what an abandoned codebase
usually looks like.

**A second Alex.** Leopard's sample bank is 669 MB; Lion's is 403. It is a
different recording, not a compression of the same one, and you can now put the
two side by side and hear what Apple changed in 2011 — on a machine Apple never
shipped either of them for.

**Everything Leopard offers.** Twenty-four voices, rate 80 to 500 wpm, pitch,
volume, inflection, phrase breaks and the dictionary front end. 10.7 moved rate
and pitch onto a different API and the loader follows it there.

## Getting the engine

The same rule as every add-on here, and it is not a formality: **no part of
Apple's software will ever be distributed from this project.** You supply your
own Lion installer and the extractor takes the engine out of it.

The easy way is the **Tools menu → Mac OS X speech data**, which reads the
image and installs it with no command line at all. The extractor is still there
for anyone who wants it:

```
py -3 tools/extract_lion.py "Lion.iso"
py -3 tools/extract_lion.py Lion.iso --no-voices    (engine and Fred only)
py -3 tools/extract_lion.py Lion.iso --out <folder>
```

It writes to `%APPDATA%\nvda\macintalk\lion`, which is where the add-on looks.

Verified the strict way: a tree extracted straight from the image is
**98 of 98 files byte-identical** to one assembled by hand.

### Lion's installer is not shaped like Leopard's DVD

Leopard's DVD has a live filesystem — the engine, the dictionary and Fred can
be copied straight out of it. **Lion's installer has nothing live.** Its HFS+
volume holds an installer application, a `BaseSystem.dmg` and a `Packages`
folder, and getting anything out means going through all three:

```
Lion.iso                       HFS+ (inside an APM partition map)
  Packages/BaseSystemBinaries.pkg    xar -> gzip -> cpio   <- everything is here
  BaseSystem.dmg                     UDIF -> HFS+          <- thin x86_64, useless
```

**Read that second line twice.** `BaseSystem.dmg` does contain a MacinTalk and
a SpeechDictionary, and they are the wrong ones: thin x86_64 binaries with no
i386 slice, which this project cannot run. The engine, the dictionary, *both*
C++ runtimes and Fred are all in `BaseSystemBinaries.pkg`. An earlier version
of this note said the opposite and it cost an evening.

### Two libraries, not one

Lion needs **`libstdc++.6.0.9.dylib` and `libc++abi.dylib`**, where Leopard
needs only the first. 10.7 moved the C++ ABI into a library of its own, and
without either one nothing loads at all. The extractor takes both.

### What is deliberately left behind

The multilingual **Compact** voices on that disc are Nuance Vocalizer — a
different synthesizer with a commercial lineage that is still live. They are
neither extracted nor listed, and that is a decision rather than an oversight.

## What is different to live with

Three things surface as behaviour rather than as code, and all three are
measured in [`docs/macintalk-4.0.md`](../docs/macintalk-4.0.md):

* **10.7 never stops its own audio graph.** Tiger and Leopard stop it once per
  utterance, 92 of 92 and 96 of 96; Lion does it 0 times in 96. There is
  nothing to end an utterance on, so the host waits out 300 ms of silence — a
  fixed cost on every Lion render, and the reason Lion measures 16× real time
  where Leopard measures 87×.
* **A timer stops the graph five seconds after you stop talking**, and once it
  has fired the next request never returns. The host refuses to arm it. This
  shipped in 0.95.0 and is what "if I leave it a minute, the next thing is
  silent" was.
* **Alex reaches the frequency-domain rate search only above about 250 wpm.**
  Below that the FFT is dead code, so anything you are trying to reproduce in
  that path needs NVDA's rate boost turned on.

## Alex, twice

`meow` 2.0.0 here against 1.x on Leopard, and the banks are different
recordings:

| | Leopard | Lion |
|---|---|---|
| `Alex.SpeechVoice/…/PCMWave` | 669.2 MB | 402.5 MB |

Lion's is smaller and cleaner. It also says **"Dropbox"** without much of the
P, where Leopard's says it properly — the same word, two banks, and not a fault
in the loader. Patching that back in would make a nicer synthesizer and a worse
record of one.

The engine still reads a `meow` 1.0.6 bank and has branches written for it, so
Lion's engine will load Leopard's Alex if you point it at one. Dropping a
second copy into the voices folder under another folder name gives you both at
once.
