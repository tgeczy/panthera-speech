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

The driver *module* names — `tigerspeech`, `leopardspeech` — are frozen for
good. NVDA keys every speech setting by synth name, so renaming one silently
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
panthera/     the add-on: both drivers, the shared plugin, tests, package.py
tiger/        Tiger's extractor and engine notes
leopard/      Leopard's extractor and engine notes
```

`py -3 -m pytest tests/ -q` from inside `panthera/` runs the whole suite; the
NVDA fakes are shared and each generation's engine fixtures live in
`tests/tiger/` and `tests/leopard/`.

The extractors stay one per generation, under `tiger/` and `leopard/`, because
they read different install images by different means — and because the
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
