# Android engine switching and saved settings

Android offers the installed Tiger, Leopard, Snow Leopard and Lion voices in
one list. A name such as **Vicki (Snow Leopard)** selects both voice and engine.
Each generation runs in its own private service process. The public Android
TTS service and its clients stay connected when the generation changes.
Returning to a generation reuses its worker; a dead worker is rebound on the
next request.

The Android distribution is GPLv2; **Licenses and source** in Setup displays
the notice and full license. After committing the sources used to build the
APK, run `python tools/package_android_sources.py`. Distribute the resulting
`build/panthera-android-sources.tar.gz` alongside that APK. It includes Panthera's
build scripts and the exact tracked Unicorn/FAAD2 source checkouts, with commit
identifiers and archive checksums. It excludes ignored engine data and build
products. Windows NVDA and SAPI distributions remain MIT.

The app process owns preferences. Workers receive a settings snapshot for each
utterance, rather than reading a separate process's SharedPreferences cache.
Voice, rate, volume, phrase breaks, number handling, embedded-command acceptance and
abbreviation expansion are saved per generation. Existing
global settings remain migration fallbacks. Rate zero follows the requesting
app; a saved rate overrides it. The optional **Use selected voice in all apps**
setting overrides explicit client voice requests. It is off by default.

Volume starts with **System default**, followed by mute (0%) and 5% steps up
to 100%. System default preserves the normal engine level (Tiger 100, later
generations 90); Android still applies the requesting app's playback volume
and device volume. Previously saved percentages remain available exactly,
including values between the new steps. Changing generations refreshes both
the displayed value and the slider's accessibility description.

**Inflection** uses the NVDA/SAPI scale, 0–100 with 50 selecting the voice's
own default. Nondefault values apply `pmod` at twice the selected percentage.
Returning to 50 replaces only the private generation worker, because
`pmod 100` is not every voice's default (notably Lion Alex). Settings take
effect on the next request; no sentence boundaries or pauses are inserted.

**Engine phrase breaks** is available on Leopard and later; Tiger's control is
disabled with an explanation. Its choices use the NVDA/SAPI mapping: Fewest
(-8), Fewer (-4), More (0), Most (5), or Engine default (no threshold override).
Fewest is the app default. The engine caches this preference beyond channel
lifetime. A changed choice replaces only that generation's private worker
before the next utterance and configures it before loading the engine. The
public TTS service and its clients remain connected.

**Accept embedded speech commands** defaults off and removes command blocks
from incoming text. When enabled, commands such as `[[rate 200]]`,
`[[slnc 500]]` and `[[volm 0]]` reach the engine. Command-containing requests
stay intact to preserve their state. Lion input
mode commands are excluded, matching the desktop driver.

**Expand abbreviations** defaults on. The Kotlin lexical rules match the NVDA
driver; the worker also updates the dictionary abbreviation switch before
each request. Compiled dictionary rules are retained and their matches gated
at execution time, allowing off/on changes without restarting a worker.
The quantity-rule effect is specific to Leopard and Snow Leopard; native
Tiger and Lion use different quantity handling. Lexical spelling controls such
as Dr. and XIV are tested on all four generations.

The sample uses the same streaming path as Android TTS. Sample text and the
selected settings page survive activity recreation. The sliders follow
TG Speechbox's View-based accessibility model: named values, single-step
arrows, Home/End, and standard accessibility range actions.
Spinners, sliders and the sample field have associated labels. Section titles
are accessibility headings, and native checkboxes own their labels and checked
states without an additional clickable parent.

Each incoming request stays whole while its audio streams. This follows the
NVDA driver's paragraph handling: splitting at sentence boundaries removes
Alex's breaths and composed pauses. Explicit stops still cancel speech; separate
Android requests retain their own boundaries. No artificial gaps or audio
silence trimming are added. An explicit stop retires an active private worker
and the next request reopens it. The emulated engines can drain a whole
paragraph inside their stop call, or return with deferred work that truncates
the next utterance. Process isolation makes cancellation a reliable boundary
without disconnecting the public service. Completed requests retain their
warm worker; stopping idle playback does not retire it.

## Native fixes

The emulator now binds `__DefaultRuneLocale` as immutable guest data rather
than a function trampoline. Tiger and Leopard's inlined digit checks had been
reading instruction bytes, rejecting numeric command arguments. This also
fixes embedded volume in the emulated Linux host. A generated libc check reads
the table through Unicorn and checks it against the initialized host table.

An ordinary audio-unit reset now drains queued speech before completing its
callbacks. Engines also reset between sentences; treating that as cancellation
discarded sentence tails on slower hosts. Explicit cancellation still discards
audio through its separate flag. The two-sentence phrase-break reference exposed
this on Android and Linux emulation, while faster native runs hid the loss.

The emulator registry grows under its existing mapping lock. Snow Leopard's
Alex can overlap more than sixteen dispatch workers during a long paragraph;
the former fixed registry terminated the host even though retiring workers
were releasing their emulators. Finished MP tasks now release their guest
stacks and emulators as dispatch workers already do.

The Snow Leopard/Lion startup failure was in the host's loader: compressed
dyld bindings and external relocations bypassed the guest-call bridge. It was
not an unsupported generation or an arm64-only failure. The corrected paths
use the same bridge as the older indirect symbol table.

Additional fixes cover four-byte guest CF pointer outputs and container
inputs, block layouts and callbacks, split-complex FFT storage, guest-visible
allocations, floating-point returns and packed 64-bit timer arguments. Retired
dispatch workers now release their emulator and their actual allocated stack
base. Lion's deferred audio-graph stop completes streaming without waiting for
the timeout or withholding the final samples.

Snow Leopard's concatenative path also called two missing math operations:
`cblas_sgemm` and `ssyevr_`. Returning success without producing their outputs
left speech-unit cost weighting incomplete on both native and emulated hosts.
The host now supplies scalar matrix multiplication and a symmetric Jacobi
eigensolver implementing the documented
[LAPACK interface](https://www.netlib.org/lapack/lapack-3.1.1/html/ssyevr.f.html).
The observed engine matrices are 3×3. Generated numerical tests also cover
larger, degenerate and scaled matrices, eigenpair residuals, orthogonality,
selection ranges and workspace queries. No vendor implementation is embedded.

SQLite outputs likewise use four-byte guest slots. On 64-bit hosts, opaque
SQLite handles live behind low-address wrappers. The unavailable-library
path also respects those slot widths. Android still skips the optional
phrasing database when SQLite is unavailable in its linker namespace.

## Validation

Galaxy S22 tests cover both Android ABIs, and the Nothing Phone 3 passes the
same four-generation suite with an arm64-only userspace (no secondary ABI).
These checks cover settings recreation and generation
round trips, reference speech, live rate/volume changes, voice override,
preview/service agreement, 24 repeated AAC utterances per installed generation,
and cancellation followed by correct speech through the same TTS client.
Settings coverage includes 294 independently generated desktop abbreviation
fixtures, live command and abbreviation switches, and all five phrase-break
choices compared with native frame counts on Leopard, Snow Leopard and Lion.
The same TTS client survives every private worker replacement.
Alex's longer paragraph is checked against native duration and the existing
NVDA breath detector, which distinguishes turbulent breath audio from silence.
The sampled AAC voices include Alex on Leopard, Snow Leopard and Lion, plus
Snow Leopard Vicki;
this is not a claim that every voice bundle has been individually tested.

For `Hello there.` at 180 wpm, native Windows and desktop Unicorn agree
byte-for-byte on these additional references:

| Voice | Frames | PCM SHA-256 |
| --- | ---: | --- |
| Snow Leopard Fred | 17360 | `ec4be821f742fcd8facd6d215ce4443d01c55ac0dc983d482574c31cfb63c761` |
| Snow Leopard Vicki | 19610 | `c8ee09f1f4f263d3b5bbebc38b3a63f85b9385be2eff928ffe24646deb53fcad` |
| Snow Leopard Alex | 19450 | `b2084b882a92219bfdcebc94619f54bca2c70508860b52e16e2c2814ec9115a2` |
| Lion Fred | 17360 | `0e29a652fe28b36857c525714cc53407f8f9eb04b266d29309831ebaf03c3fa7` |
| Lion Vicki | 19517 | `93fedc0627b18e1fabfd5ad7f738560dfcaca16483d86102de2cab1279470cad` |
| Lion Alex | 19387 | `f0225e43396a14654b7dcfd11569c61bb898fa850d7c6e6f59cf4c7c2c49edd4` |

Snow Leopard Vicki and Lion Alex on arm64 have the same frame counts and
maximum difference of one PCM16 unit against equal-volume native references.
FAAD2 and Media Foundation need not round AAC decoding identically. Match
volume as well as voice, text, API and rate before comparing waveforms:
the app's voice normalization changes the gain.

The numerical and SQLite tests also pass on native Linux i686 and emulated
Linux x86_64. Native Linux Tiger/Leopard streaming, volume and cancellation
remain covered by the existing client harness. No Apple files or generated
speech are stored in the repository or sent to CI; all render comparisons
use the owner's extracted data locally.

Native Lion can produce different exact waveforms for repeated input even in
the same resident session. The tests accept only independently verified native
variants for their Fred reference phrases;
repeat identity alone is not a universal speech-correctness oracle.

A separate 212-word local reading sample exposes an unresolved Lion Alex
repeatability difference under emulation. Its first Android render has the
native duration (1911574 frames), with a maximum difference of two PCM16 units.
An Android repeat adds 121 samples (5.5 ms) within one roughly 0.4-second span;
the remaining waveform matches exactly after alignment. Forty resident native
Windows renders remain identical. Local Whisper transcripts of both Android
variants and the native render agree, but that does not establish waveform
equivalence. This longer sample is distinct from the passing fixed paragraph
duration/breath regressions above. Its text and recordings remain outside the
repository.

See [the arm64 build and device commands](android-arm64.md). Numerical checks
need only built hosts and NumPy/pytest:

```text
python -m pytest panthera/tests/test_linalg.py panthera/tests/test_sqlite_abi.py -q
```

Use `PANTHERA_TEST_HOST` and `PANTHERA_TEST_UC_HOST` to select native and
emulated executable paths on Linux. `--linalg-check` in a UC build traverses
the actual guest trampolines, including the 14- and 21-argument math calls.
