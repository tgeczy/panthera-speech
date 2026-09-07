# Android engine switching and saved settings

Android offers the installed Tiger, Leopard, Snow Leopard and Lion voices in
one list. A name such as **Vicki (Snow Leopard)** selects both voice and engine.
Each generation runs in its own private service process. The public Android
TTS service and its clients stay connected when the generation changes.
Returning to a generation reuses its worker; a dead worker is rebound on the
next request.

The app process owns preferences. Workers receive a settings snapshot for each
utterance, rather than reading a separate process's SharedPreferences cache.
Voice, rate, volume and number handling are saved per generation. Existing
global settings remain migration fallbacks. Rate zero follows the requesting
app; a saved rate overrides it. The optional **Use selected voice in all apps**
setting overrides explicit client voice requests. It is off by default.

The sample uses the same streaming path as Android TTS. Sample text and the
selected settings page survive activity recreation. The sliders follow
TG Speechbox's View-based accessibility model: named values, single-step
arrows, Home/End, and standard accessibility range actions.

## Native fixes

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

Galaxy S22 tests cover both Android ABIs: settings recreation and generation
round trips, reference speech, live rate/volume changes, voice override,
preview/service agreement, 24 repeated AAC utterances per installed generation,
and cancellation followed by correct speech through the same TTS client.
The sampled AAC voices are Leopard Alex, Snow Leopard Vicki and Lion Alex;
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

See [the arm64 build and device commands](android-arm64.md). Numerical checks
need only built hosts and NumPy/pytest:

```text
python -m pytest panthera/tests/test_linalg.py panthera/tests/test_sqlite_abi.py -q
```

Use `PANTHERA_TEST_HOST` and `PANTHERA_TEST_UC_HOST` to select native and
emulated executable paths on Linux. `--linalg-check` in a UC build traverses
the actual guest trampolines, including the 14- and 21-argument math calls.
