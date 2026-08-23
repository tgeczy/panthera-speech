# panthera-speech

**Apple's Mac OS X speech engines, running as native code on Windows.** No
virtual machine, no emulator, no CPU translation — a small 32-bit host process
maps Apple's Intel `MacinTalk` into memory, fills the pointer slots `dyld`
would have filled, and calls the engine directly. An utterance costs about
twelve milliseconds.

**One add-on for NVDA**, `pantheraspeech`, declaring one synthesizer per
engine generation:

| synthesizer | | voices |
|---|---|---|
| [**tigerspeech**](tiger/README.md) | Mac OS X 10.4 Tiger | twenty-three, including Fred as he sounded in 2005 |
| [**leopardspeech**](leopard/README.md) | Mac OS X 10.5 Leopard | twenty-four, including **Alex** |
| [**lionspeech**](lion/README.md) | Mac OS X 10.7 Lion | twenty-four, including a later, smaller **Alex** |

## One add-on, several synthesizers

They were an add-on each until 0.9.0, with an engine selector planned to bring
them together. That was the wrong shape. Changing generation tears down a host
and loads up to 700 MB of samples, and it changes the voice list, the settings
and the pitch scale — which is precisely what NVDA already calls *changing
synthesizer*. Every hard problem in the selector design was created by cramming
two engines into one synth, and stating it the other way round dissolves all of
them: the config spec stays static, no control is inert, and NVDA remembers a
voice per generation because it already stores settings per synth.

What it does not solve is per-language voice mapping *across* synthesizers.
That is a real gap in NVDA and it is worth saying so rather than leaving it to
be discovered.

The driver *module* names — `tigerspeech`, `leopardspeech`, `lionspeech` —
are frozen for good. NVDA keys every speech setting by synth name, so renaming one silently
resets the voice, rate, pitch and volume of everybody who had it selected.

## Why the cats

*Panthera* is the genus: tiger, leopard, snow leopard and lion are all in it.
The cat that is not is the **mountain lion** — a puma — and 10.8 Mountain Lion
is exactly where this work stops, because it is the first release with no i386
slice at all. The name draws its own scope line, including the far edge of it.

**32-bit is not a compatibility concession — it is the cheap path, and 64-bit
is a different project.** This used to say the choice was about reaching users
on 32-bit builds of NVDA. That argument does not survive contact: the engine
runs in its **own process** and talks over a pipe, so NVDA's bitness has never
mattered — the same binary already serves 32-bit NVDA 2023.1 and 64-bit NVDA
2026.1. The only people a 64-bit host would exclude are those on 32-bit
*Windows*, and in 2026 that is a rounding error.

The real reason is the boundary, and it is measurable. Every engine through
Lion ships an i386 slice, and **Darwin i386 and Win32 are both cdecl** —
arguments on the stack, same order, same cleanup. So each of the ~360 symbols
the engine imports can be an ordinary C function that it calls directly, with
no glue at all.

Darwin x86_64 and Windows x64 agree on almost nothing. System V AMD64 passes
in RDI/RSI/RDX/RCX/R8/R9 with a 128-byte red zone below the stack pointer;
Microsoft x64 passes in RCX/RDX/R8/R9 with 32 bytes of shadow space and no red
zone, and the struct and varargs rules differ too. The import count barely
moves — Lion is 364 symbols as i386 and 361 as x86_64, Mountain Lion 359 — but
every one of them would need a translating thunk, in both directions, where
today it needs none. Windows does not honour the red zone either, so the
engine's own leaf functions could be clobbered by anything the OS runs on that
stack.

Then the exceptions. All of these carry `__eh_frame`, `__gcc_except_tab` and
`__unwind_info` — DWARF unwind tables. Windows x64 mandates table-based SEH
with registered function tables, and a foreign DWARF-described frame is simply
not in them.

So the trade is a fundamentally different host in exchange for **one more
generation** — 10.8 and later, which is where Apple stopped shipping i386.
That is a real project someone could do. It is not this one.

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
rather than adding a branch to it. Lion cost the most and gave the most: a
dyld-info interpreter, a libdispatch layer and an FFT, all of them in the host
rather than in a branch. **Snow Leopard is the one still to come**, and it is
the same loader problem exactly — a driver and a table entry, not a new add-on.
The aim is a host that can serve any 32-bit MacinTalk generation, with what
differs between them stated once, in one place, per generation.

```
src/          the loader, the shims, the host
build.sh      builds it and stages it into every add-on below
tools/        Mach-O dissection: machosyms, machodis, cfstrings, render_once
bridge/       the original QEMU bridge, kept as an oracle
docs/         one note per engine generation, plus the tunables
panthera/     the add-on: both drivers, the shared plugin, tests, package.py
tiger/        Tiger's extractor and engine notes
leopard/      Leopard's extractor and engine notes
lion/         Lion's extractor and engine notes
```

`py -3 -m pytest tests/ -q` from inside `panthera/` runs the whole suite; the
NVDA fakes are shared and each generation's engine fixtures live in
`tests/tiger/`, `tests/leopard/` and `tests/lion/`.

The extractors stay one per generation, under `tiger/`, `leopard/` and
`lion/`, because they read different install images by different means — and because the
dialogs that name their URLs have already shipped.

## Releases

**Tags carry the add-on name** — `pantheraspeech/v0.9.0`. The
`tigerspeech/…` and `leopardspeech/…` tags are the releases from before the two
became one, and are left alone; a bare `vN.N.N` tag predates all of it and is
Tiger's.

Upgrading from those releases does not remove them: NVDA's manifest has no
`replaces` field, so both can sit installed at once and each offers a
synthesizer of the same name. The add-on notices at start-up and offers to
remove the older copies, because which one NVDA loads otherwise depends on the
order it reads the add-ons folder in.

Releases published before this repository was renamed are still reachable:
the old `tiger-speech` URL redirects, and `leopard-speech` is archived with
its own six releases intact.

## Notes on the engines themselves

One per generation, in `docs/`. They are written for somebody doing this work
rather than for somebody using the add-on, and everything in them was measured
against the binaries rather than remembered.

| | |
|---|---|
| [`macintalk-3.3.md`](docs/macintalk-3.3.md) | Tiger. The loader, the twelve-function interface, the fake AUGraph, and Vicki's AAC through the Sound Manager. **Read this one first** -- the other two assume it. |
| [`macintalk-3.6.md`](docs/macintalk-3.6.md) | Leopard. Alex and the 669 MB sample bank, AudioConverter and its priming, the slice timeline that restarts at zero, the dictionary's SQLite and POSIX regex, and what prosody is and is not tunable. |
| [`macintalk-4.0.md`](docs/macintalk-4.0.md) | Lion. Compressed dyld info, Grand Central Dispatch and the timer that stops the audio graph, rate and pitch on a new API, and WSOLA moved into the frequency domain. |
| [`engine-tunables.md`](docs/engine-tunables.md) | The 283 named parameters, and which 82 are live. |

## Faithful, including the flaws

Lion's Alex says **"Dropbox"** without much of the P. It is not a decoding
fault and it is not the loader: Leopard's Alex says it properly, and the two
are different recordings of the same voice — Lion's bank is 422 MB where
Leopard's is 669, and Apple rebuilt it between the two. `meow` 1.0 and `meow`
2.0 genuinely disagree about that word.

Which is, in its way, the point. You can put the two Alexes side by side and
hear what Apple changed in 2011, on a machine Apple never shipped either of
them for. A loader that quietly patched the P back in would be a nicer
synthesizer and a worse record of one.

The line moves when the engine says a *different word* — "cologne" for
"colon", or "doctor" for the acronym "DR". Those are repaired in the text
before the engine sees it, each behind a setting, and each written down where
it happens: see `pantherastress.py` and `pantheraabbrev.py`.

## Nothing of Apple's is here

**No engine and no voice ships from this repository**, and none is committed
to it. The add-on reads an install image *you own* and takes the engine out of
it; `package.py` refuses to build a release containing engine data, and
`tools/check_clean.py` refuses one carrying a developer's paths.

That is the whole arrangement, and it is not a formality: what is being
licensed here is the work of making Apple's engine run somewhere it was never
built to run, and nothing else.

### Why it will not be bundled, however often it is asked

It is asked often, and always in good faith, and the usual argument is that
Apple has better things to do than sue a hobbyist. That is probably true and
it is not the risk.

**The risk is a takedown notice, not a lawsuit.** It costs a rights holder
about an hour of a paralegal's time, GitHub removes the repository first and
hears the dispute afterwards, and Apple demonstrably does this — the iBoot
source leak, the Hackintosh distributions, Psystar, Corellium. None of those
needed anyone at Apple to have an opinion about a screen reader.

**And "it is a twenty-year-old system" helps less than it sounds.** Copyright
does not lapse because a product is discontinued, and this particular data has
not gone anywhere: Alex still ships in current macOS, and Lion's `meow` 2.0.0
bank and Sequoia's declare the same version with 22 of 26 descriptor lines
identical. Distributing Lion's Alex is closer to distributing a current
product than to distributing an artefact.

**The asymmetry settles it.** Bundling the data saves each user one setup
step. One notice removes the repository — every release, the loader, the
extractors, the drivers, four generations of work that infringes nothing and
is the only part anyone here actually wrote. You cannot buy that back, and an
add-on carrying Apple binaries would not be in the NVDA add-on store either,
which is where the people who most need the convenience actually look.

So the friction had to go somewhere else, and it went into the tool: **Tools →
Mac OS X speech data → point it at your disc image.** No Python, no command
line, no unpacking software, nothing downloaded. That is the answer to the
convenience argument, and it is a better one than shipping the bytes.

### The same posture, done by someone else

[Google TTS For NVDA](https://github.com/nguyenanhduc09/Google-TTS-For-NVDA)
is worth reading if this seems over-cautious. It bundles Google's WASM speech
engine, because that engine is Chromium's and BSD-3-Clause explicitly permits
binary redistribution. It does **not** bundle the voices: `voices.json` is a
catalogue of URLs on Google's own CDN, and its own notice says so —
*"Downloaded .zvoice voice packages are not distributed as part of this
add-on."*

Ship what the licence permits, fetch the rest from whoever owns it, document
where everything came from. Apple gives us neither half — MacinTalk is not
BSD, which is why this repository is a loader rather than a bundle, and Apple
does not serve the voice data from a public URL, which is why there is an
extractor.

### This notice is also a fingerprint

If a fork ever does bundle the engine, it cannot keep this section — it would
be describing a rule it is breaking. Its absence, or its quiet replacement
with something friendlier, is a reliable way to tell a redistribution apart
from a genuine derivative of this work. That is worth stating plainly rather
than leaving to be inferred.

## Sibling project

[**outspoken-nvda**](https://github.com/tgeczy/outspoken-nvda) does the same
job for the generation before this one — MacinTalk 1, 2, 3 and Pro, from 1984
to 1994 — but by a completely different route: real 68k code under the Musashi
CPU emulator. Different host, different era, its own repository.

## Licence

**MIT** — see `LICENSE`. It covers the loader, the drivers, the shims and the
tools. It does not and cannot cover Apple's engine, which is not distributed
here.
