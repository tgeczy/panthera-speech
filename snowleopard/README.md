# snowleopardspeech

**Mac OS X 10.6's MacinTalk, running as native x86 code inside NVDA.** No
emulator, no virtual machine — and with it the third Alex, which is the second
*recording*: the rebuilt 400 MB bank Apple shipped from 10.6 onward, rather
than Leopard's 669 MB one.

One synthesizer of four in [panthera-speech](../README.md). Its siblings
[tigerspeech](../tiger/README.md), [leopardspeech](../leopard/README.md) and
[lionspeech](../lion/README.md) do the same job for 10.4, 10.5 and 10.7, and
all four share one loader.

**The engineering notes are in
[`docs/macintalk-3.10.md`](../docs/macintalk-3.10.md)** — the hybrid, the two
clocks, the dispatch source per unit of work — and they are not repeated here.
This file is about *getting* the engine, and about what makes 10.6 different
to live with.

## Why Snow Leopard, when Leopard and Lion already speak

**It is the version people actually kept.** 10.6 was the last release for a
lot of Macs and a lot of people, and it is the one whose install DVD is most
likely to be in a drawer. Somebody who has one disc has this one.

**It is the middle Alex, and the middle is not a compromise.** Leopard's Alex
is the big original recording; Lion's is the rebuilt one. Snow Leopard's is
the rebuilt one *first* — the same 400 MB bank Lion has, driven by an engine
that is still a 3.x. Whether that is better than either neighbour is a
question for your ears, and this is what lets you ask it.

**It cost two bug fixes and no new subsystem.** 10.6 binds the way 10.7 does
and speaks the way 10.5 does, so the loader already had every piece. That is
the whole argument for the shape of this project, tested: the engine mapped,
bound 1,170 symbols with none unresolved and opened a speech channel with no
change to the host at all.

## What you get

Twenty-four voices — Alex and Vicki concatenative, Agnes, Bruce and Victoria
from MacinTalk Pro, and nineteen MacinTalk 3 voices including Fred and the
singing ones. Rate, pitch, volume, inflection, phrase breaks, and the
dictionary front end Tiger has no equivalent of.

Speed is Leopard's rather than Lion's, and for a reason worth knowing: 10.6
stops its audio graph when an utterance ends, so the host knows immediately.
10.7 never does, which is why Lion needed a signal found for it.

## Getting the engine

The same rule as every add-on here, and it is not a formality: **no part of
Apple's software is in this repository or in any release of it.** You supply
it, from a disc you own.

**The easy way is NVDA's Tools menu.** "Mac OS X speech data...", then point
it at your 10.6 install image. It reads the image directly — no Python, no
7-Zip, nothing downloaded — and writes into the right folder. That is the
route to use unless you have a reason not to.

On a command line, from a clone of this repository:

```
py -3 snowleopard/tools/extract_snowleopard.py "Mac OS X 10.6.iso"
```

Either way it lands in `%APPDATA%\nvda\macintalk\snowleopard`, which is
NVDA's configuration folder rather than the add-on's — updating an add-on
deletes and recreates its directory, and 450 MB kept inside one would be
destroyed on every upgrade. To keep it elsewhere, put the full path in
`snowleopardspeech-data.txt` in the configuration folder.

## The one trap: `libstdc++.6.0.9.dylib`

Snow Leopard's engine needs Apple's own C++ runtime, and **the version number
is shared with Lion by a file that is not the same library**:

| | Snow Leopard 10.6 | Lion 10.7 |
|---|---|---|
| `libstdc++.6.0.9.dylib` | 2,439,888 bytes | 1,595,728 bytes |
| the C++ ABI | in this file | re-exported from `libc++abi.dylib` |
| second library needed | none | `libc++abi.dylib` |

Take it from the 10.6 disc, and take it from no other. Nothing downstream can
tell the two apart by name, and the wrong one does not fail cleanly — it loads,
resolves `__dynamic_cast` and the `__cxa_*` family to nothing, and then
misbehaves somewhere else entirely.

The extractor and the Tools menu both take the right one, because both read it
off the disc they were given. This only bites somebody assembling a tree by
hand out of two installs.

## What is on the disc

10.6's DVD has a live filesystem, the way 10.4's and 10.5's do and 10.7's does
not:

```
System/Library/Speech/Synthesizers/       MacinTalk itself
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
Agnes, Bruce, Victoria and every novelty voice. Both packages are read.
