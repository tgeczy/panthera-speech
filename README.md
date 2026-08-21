# panthera-speech

**Apple's Mac OS X speech engines, running as native code on Windows.** No
virtual machine, no emulator, no CPU translation — a small 32-bit host process
maps Apple's Intel `MacinTalk` into memory, fills the pointer slots `dyld`
would have filled, and calls the engine directly. An utterance costs about
twelve milliseconds.

Two add-ons for NVDA so far, one per engine generation:

| | | voices |
|---|---|---|
| [**tigerspeech**](tiger/README.md) | Mac OS X 10.4 Tiger | twenty-three, including Fred as he sounded in 2005 |
| [**leopardspeech**](leopard/README.md) | Mac OS X 10.5 Leopard | twenty-four, including **Alex** |

## Why the cats

*Panthera* is the genus: tiger, leopard, snow leopard and lion are all in it.
The cat that is not is the **mountain lion** — a puma — and 10.8 Mountain Lion
is exactly where this work stops, because it is the first release with no i386
slice at all. The name draws its own scope line, including the far edge of it.

**64-bit is not under consideration**, and that is a decision about people
rather than about difficulty: NVDA has a large base of users on builds where a
64-bit-only engine simply would not load. A 32-bit engine reaches all of them.

## One loader

**There is one loader in this repository and there will only ever be one.** It
lives in `src/`; `sh build.sh` builds it and stages it into every add-on, so
all of them ship the same binary under different local names.

That is deliberate, and it paid for itself twice in a single day. The defect
that made Alex crackle was in a decoder path **only Leopard's engine takes** —
Tiger's goes through a different API entirely — and fixing it left Tiger's
renders byte-for-byte identical. One loader, per-engine paths inside it.
Forked, that fix would have had to be found twice.

Each lineage added since has pushed more of the host behind an interface
rather than adding a branch to it, and the ones still to come — Snow Leopard
and Lion — are expected to modularise it further. The aim is a host that can
serve any 32-bit MacinTalk generation, with what differs between them stated
once, in one place, per generation.

```
src/          the loader, the shims, the host
build.sh      builds it and stages it into every add-on below
tools/        Mach-O dissection: machosyms, machodis, cfstrings, render_once
bridge/       the original QEMU bridge, kept as an oracle
docs/         engine notes that are not specific to one generation
tiger/        the tigerspeech add-on: driver, tests, extractor, package.py
leopard/      the leopardspeech add-on, likewise
```

An add-on folder is self-contained apart from the loader: its own `tests/`,
its own extractor, its own `package.py`. `py -3 -m pytest tests/ -q` from
inside one runs that add-on's suite.

## Releases

Each add-on versions and ships on its own, so **tags carry the add-on name** —
`tigerspeech/v0.7.9`, `leopardspeech/v0.7.4` — and each release attaches only
its own `.nvda-addon`. A bare `vN.N.N` tag here means nothing any more; the
ones that predate the merge are Tiger's and are left alone.

Releases published before this repository was renamed are still reachable:
the old `tiger-speech` URL redirects, and `leopard-speech` is archived with
its own six releases intact.

## Nothing of Apple's is here

**No engine and no voice ships from this repository**, and none is committed
to it. Each add-on has an extractor that pulls the engine out of an install
image *you own*; `package.py` refuses to build a release containing engine
data, and `tools/check_clean.py` refuses one carrying a developer's paths.

That is the whole arrangement, and it is not a formality: what is being
licensed here is the work of making Apple's engine run somewhere it was never
built to run, and nothing else.

## Sibling project

[**outspoken-nvda**](https://github.com/tgeczy/outspoken-nvda) does the same
job for the generation before this one — MacinTalk 1, 2, 3 and Pro, from 1984
to 1994 — but by a completely different route: real 68k code under the Musashi
CPU emulator. Different host, different era, its own repository.

## Licence

**MIT** — see `LICENSE`. It covers the loader, the drivers, the shims and the
tools. It does not and cannot cover Apple's engine, which is not distributed
here.
