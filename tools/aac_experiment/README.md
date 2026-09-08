# Experimental MIT AAC decoder

Glint can render through Panthera's existing AAC seam on native Windows and
experimental Box Android builds. **This is an opt-in experiment, not the release
decoder.** Production builds still use their existing backends. No voice data,
AAC packets, generated PCM, or upstream implementation belongs in this directory.

Upstream: [CrispStrobe/glint](https://github.com/CrispStrobe/glint), pinned at
`77738f3ed9b15f627196cc5bbd7f6406814ba2fb`, with its
[MIT license](https://github.com/CrispStrobe/glint/blob/77738f3ed9b15f627196cc5bbd7f6406814ba2fb/LICENSE).
The builder extracts only the AAC decoder files and license from the pinned Git
archive. The source clone is left untouched. Its local modification caches cube
roots of integer spectral magnitudes, retaining the original multiplication
order and the original calculation for larger escape values, and adds an
eight-bit Huffman prefix table with the original tree walk as fallback.
It also preserves the transform's binary64 precision through its output buffer,
so conversion to integer PCM rounds once instead of narrowing to float first.

The host bridge wraps raw AAC-LC access units in ADTS, validates the supported
mono ASC/rate, checks the returned frame size, and retains shared priming and
tail handling. It saves the caller's floating-point environment, uses the default
environment while decoding, then restores the caller's state. This matters on
native i386, where guest arithmetic and host arithmetic share a thread.
PCM conversion uses the binary64 significand to implement exact nearest-even
rounding and saturation without calling libm for every sample.

## Reproduce

Requires an official local Glint clone containing the pin. Windows needs VS 2022
C++ Build Tools. Android uses the same NDK and pinned Box clones as the
[translator experiment](../translation_experiment/README.md).

```powershell
git clone https://github.com/CrispStrobe/glint.git build/research/glint
python tools/aac_experiment/build_windows.py --source build/research/glint --out build/aac-glint-windows
python tools/aac_experiment/compare_windows.py --baseline build/tiger_host.exe --candidate build/aac-glint-windows/build/Release/tiger_host.exe --data-root D:/ --out build/aac-glint-comparison
python tools/translation_experiment/build.py --backend box64 --source build/research/box64 --out build/aac-glint-box64 --aac glint --aac-source build/research/glint
python tools/translation_experiment/build.py --backend box86 --source build/research/box86 --out build/aac-glint-box86 --aac glint --aac-source build/research/glint
```

The comparison writes whole renders and reports lengths, differences, repeated
render identity, and the existing breath detector. It does not silently turn a
numeric tolerance into a passing oracle. Its timings are complete renders,
including driver overhead, not acoustic onset.
Pass `--max-pcm-delta 2` explicitly to require matching lengths, exact repeats
from both hosts, and a maximum two-unit PCM difference. `--max-pcm-delta 0`
requires byte identity. Without this option the tool only reports differences.

The Windows `fpcheck.exe` accepts a caller-supplied 22050 Hz mono ADTS AAC-LC
file. It checks identical decoded PCM under all four rounding modes and verifies
that the caller's rounding mode survives. No AAC test file is distributed.

Both builders also produce `pcm16_check` and `huffman_check`; Windows registers
them with CTest (`ctest --test-dir OUT/build -C Release --output-on-failure`).
These checks require no engine or voice data. On Android, run the matching ABI's
executables directly over ADB. They compare rounding boundaries, arbitrary
float/double bit patterns, and Huffman consumption/overrun behavior against independent
references; the existing audio oracles remain unchanged.

Android produces `libpanthera.so` and the existing native benchmarks in the
experiment's `build` directory. The existing debug-only
`pantheraExperimentalJni` Gradle override can package them. Include the extracted
Glint license with any experimental binary shared with someone else. Builds
using Glint do not link the FAAD2 objects. This does not constitute an audit of
every dependency or approval to describe the complete APK as MIT-only.

## Measured September 7, 2026

Local user-supplied trees; native i386 Windows baseline uses Media Foundation.

* 56 configurations: Tiger Vicki; Leopard, Snow Leopard, and Lion Alex/Vicki;
  four texts including a whole two-sentence paragraph; 180 and 387 wpm.
  All lengths match and all candidate repeats are exact. At 180 wpm Alex's
  detected breath positions match all three native baselines. The detector's
  duration threshold does not identify breaths at 387 wpm in either backend.
* 55 configurations differ by at most two signed-16-bit PCM units. One Lion
  Alex paragraph at 387 wpm differs in 724 samples, max delta 3961, SNR 45.43 dB,
  with equal 241402-frame length and stable candidate repeats. This difference
  was subsequently isolated to double rounding and fixed; see the later
  investigation below. The initial decoder-output dump mixed unequal backend
  tail lengths and was not a valid direct sample comparison.
* Initially one of eight Leopard cancellation/recovery tests failed. The
  floating-point environment guard makes all eight pass, including Vicki's
  full next-utterance PCM. A separate 25600-frame unit test changes over 11000
  samples under each nondefault rounding mode without the guard, and zero with
  it. The cube-root cache changes none of the 56 candidate renders.
* Direct Android comparisons: both S22 ABIs and Pixel Watch 2 ARMv7, two fresh
  processes per codec, eight alternating short renders per process. All repeat
  exactly, have matching lengths, preserve the native signal-handler check,
  and differ from FAAD2 by at most two PCM units.

Warm medians below exclude the first two renders of each process (six samples
per text/codec/device). These are **time until the first native pull returns**,
not physical sound or TalkBack swipe latency. Texts are `Seven` and
`Restart with debug logging enabled.`, Leopard Alex at 387 wpm.

| Device / ABI | Seven FAAD2 / Glint | Debug phrase FAAD2 / Glint |
| --- | ---: | ---: |
| S22 ARM64 | 10.85 / 8.70 ms | 23.50 / 27.15 ms |
| S22 ARMv7 | 10.85 / 16.25 ms | 30.75 / 53.85 ms |
| Watch 2 ARMv7 | 37.20 / 52.20 ms | 98.40 / 139.05 ms |

The complete Android audio/settings suite passed with Glint on the watch and
S22 ARMv7. S22 ARM64 failed the Lion paragraph oracle at 527291 frames; the
restored FAAD2 control also failed it at 527290, against 527288 expected.
This confirms the existing timing problem remains; it does not prove that the
codec cannot influence its frequency. Original APKs, primary ABIs, and exact
preference bytes were restored on both devices. Nothing Phone was disconnected.

Fresh builds from the committed scripts also reproduced 72/72 short renders
exactly against the prototype (three processes, eight renders each, on all
three device/ABI combinations), with the native signal check passing each time.
The fresh Windows build repeated the 56 comparisons, all eight cancellation
recovery cases, and all four rounding-mode checks. Those initial results did
not resolve the native Lion difference; the later investigation did.

## Before adopting

The decoder bounds/reset fixes and source-provenance findings are recorded
below. The Box paragraph overwrite is fixed by tagging slices with their
scheduled player restart, and the native Linux high-rate Fred crash is fixed
by porting Windows' guest divide-by-zero recovery. Before promotion, finish
the Box signal/mapping/code-cache review, reproducible default build and
distribution notices, and final real-navigation checks on the pure ARM64
phone. The remaining ARMv7 decoder cost is measured above. Keep the current
release license notices and decoder defaults until the complete dependency
selection is settled.

### Decoder audit, later September 7

The pinned decoder was read end to end for bounds, reset state and provenance,
and what Apple's banks actually exercise was measured rather than assumed:
41,662 access units (24,308 distinct) fed through a counting copy of the
parser, from Tiger, Leopard, Snow Leopard and Lion, Vicki and Alex, at 180
and 387 wpm. The counting copy, its records and results live under
`build/research/aac-census/` and are not committed.

What the banks use: mono SCE only; all four window sequences, including
EIGHT_SHORT with grouping; **sine windows only, never KBD**; TNS in a third to
a half of units (order up to 12, length up to 27 bands, so both the long and
the short-window TNS paths are live); spectral codebooks 1 through 11, with
book-11 escape prefixes up to 8 bits; `max_sfb` exactly at the 22050 Hz table
limits (47 long, 15 short) and never beyond. Never present: PNS (codebook 13),
intensity stereo (14, 15), pulse data, gain control, prediction, FIL, or any
element but SCE. Zero decoder errors and zero bit-reader overruns.

Three findings, each applied as a further `replace()` site in `glint.py` and
each inert for this data by the census above:

* A long-window `max_sfb` is six bits, so up to 63, and was never checked
  against the band table (47 entries at 22050 Hz). Past it the offsets are
  whatever memory follows the table, and the spectral loop writes `coef_` at
  those offsets. Now refused as a malformed unit.
* The book-11 escape prefix walk had no upper bound, and `1 << (n1 + 4)`
  overflows `int` from a prefix of 27. AAC-LC values stop at 8191 (prefix 8);
  a prefix over 12 is now refused, which keeps the shift defined without
  second-guessing any real stream.
* The PNS noise generator's state was a file-scope static that `init()` never
  reset, so two decoders, or one rebuilt per unit as this host does, would draw
  different noise for the same band. The state is now per decoder and reseeded
  by `init()`. No Apple unit codes a PNS band, so this could not have caused
  drift here; it is hygiene for the day one does.

Measured: the audited build renders all 56 native configurations
**byte-identical** to the previous Glint build (`--max-pcm-delta 0`, equal
lengths, exact repeats from both hosts), and `pcm16_check` and
`huffman_check` pass.

Provenance, stated precisely: the shipped `aac_tables.hpp` holds the
scalefactor-band offsets and Huffman codebooks of ISO/IEC 13818-7 / 14496-3,
normative numbers rather than anyone's expression. Upstream's generator reads
those numbers out of vo-aacenc (Apache-2.0) and FFmpeg (LGPL) and refuses to
emit unless the two agree bit for bit; identical values from two unrelated
codebases is evidence that they are the specification's data, and no code
from either project is taken. The decoder itself is written against the
specification and does not resemble FAAD2 or FFmpeg in structure. This is a
reading of the source, not a legal opinion.

### Native Linux

`AAC=glint GLINT_SOURCE=<clone> ./build_linux.sh i686` builds the same
pinned decoder into the native Linux host. Every AAC voice tried (Tiger
Vicki; Leopard Vicki and Alex; 180 and 387 wpm) renders **byte-identical** to
the Windows Glint host, with Fred unchanged as the control. The one thing
that mattered: gcc's i386 default is x87, whose 80-bit intermediates round
differently from the SSE2 doubles MSVC uses, so the decoder, bridge and host
are built with `-msse2 -mfpmath=sse`. On the build VM Glint is ~25x real
time against FAAD2's ~19.5x, with equal time to first sound.

[oxideav-aac](https://github.com/OxideAV/oxideav-aac) was also inspected at
`365e40e00c22a20fd83ba2bdc1aee826cc937cbc`. It declares MIT, but its current
`filterbank::imdct` uses nested sample/coefficient loops and a cosine per term
(2048 by 1024 for a long AAC-LC window). It was not integrated or benchmarked;
that implementation would need substantial optimization for this latency goal.

## ARMv7 optimization measurements, later September 7

Profiling eight alternating short Alex renders located a substantial cost in
the original bridge's per-sample `lrint(double)` call. On S22 ARMv7, conversion
took 162.7 ms of 318.0 ms in the AAC wrapper; on Watch 2, 222.5 of 865.1 ms.
ARM64 conversion was only 5.2 of 87.8 ms. These are instrumented totals across
2928 AAC access units, not per-utterance or acoustic timings. The nested decoder
timers include its transform/dequantization stages and must not be added twice.

Two retained changes:

* Convert float PCM by its IEEE binary32 sign, exponent, and significand.
  Multiplication by 2^15 becomes an exponent adjustment; discarded bits give
  exact nearest-even rounding. Preserve saturation, signed-zero output, and
  NaN/infinity rejection. No decoder precision or floating-point state handling
  is relaxed.
* Precompute the first eight Huffman bits. Longer codes resume at the same tree
  node, and fewer than eight available bits use the original walk. This adds
  15360 bytes of prefix tables across the twelve codebooks and avoids reading
  past the available input.

Matched runs rotated original Glint, optimized Glint, and FAAD2 through nine
fresh processes per device/ABI (three per variant), eight renders each. Warm
medians exclude the first two renders per process: nine samples per text and
variant. Same Alex, rate, phrases, and first-pull definition as above.

| Device / ABI | Text | Original Glint | Optimized Glint | FAAD2 |
| --- | --- | ---: | ---: | ---: |
| S22 ARMv7 | Seven | 20.7 ms | 15.6 ms | 10.5 ms |
| S22 ARMv7 | Debug phrase | 51.1 ms | 36.4 ms | 31.4 ms |
| Watch 2 ARMv7 | Seven | 52.5 ms | 42.3 ms | 38.6 ms |
| Watch 2 ARMv7 | Debug phrase | 138.8 ms | 113.3 ms | 97.9 ms |
| S22 ARM64 | Seven | 9.0 ms | 9.5 ms | 8.6 ms |
| S22 ARM64 | Debug phrase | 30.0 ms | 21.1 ms | 26.6 ms |

For the debug phrase, time inside AAC falls from 66 to 39 ms on S22 ARMv7,
and 183 to 138 ms on Watch 2. Complete-render medians are respectively
112.1 to 83.9 ms and 416.8 to 369.7 ms. ARM64 remains comparable to FAAD2:
17 versus 16 ms AAC and 47.0 versus 44.5 ms complete; its first-pull ordering
alone is not evidence that Glint is a faster decoder. Warm-up, scheduling, and
thermal variation still affect these short measurements.

Validation of fresh builds:

* All 56 native Windows voice/text/rate configurations are byte-identical to
  the previous Glint build; both builds repeat exactly. The existing native
  Lion discrepancy against Media Foundation is therefore unchanged.
* All eight native cancellation/recovery cases and four decoder rounding-mode
  checks pass.
* 1195698 finite float values in each of four rounding modes match the original
  conversion, including all half-way PCM boundaries and adjacent floats;
  non-finite inputs are rejected. Run on Windows, both S22 ABIs, and Watch 2.
* 4800000 Huffman comparisons match the original tree walk's value, consumed
  bits, and overrun flag, including truncated input and unaligned bit offsets.
  Run on the same four environments. This is equivalence coverage, not a full
  safety audit of the upstream AAC parser.
* 72/72 fresh Android renders match the prior Glint PCM exactly, with native
  signal-handler checks passing. All timed candidate renders also match it.
* The complete Android audio/settings suite passes on Watch 2 and both S22
  ABIs. The ARM64 pass does not resolve the previously observed intermittent
  Lion timeline failure; neither optimization changes that contract. Original
  APKs, ABIs, and exact preferences were restored after these temporary tests.

## Native Lion double-rounding fix

The native Lion Alex paragraph discrepancy was traced at matching converter
boundaries, using locally supplied data only. All 134 requests matched by
compressed input and requested/returned frame counts; the two decoders consumed
matching packet prefixes, with different lookahead lengths. Their returned PCM
differed in 471 samples by at most one signed-16-bit unit. Decode-request order
also differed, so concatenating their outputs was not a valid comparison.

Replacing Glint's returned PCM with the matched Media Foundation samples made
the final paragraph byte-identical to Windows. Narrowing the replacement to
one sample removed the large discrepancy; changing that same sample in the
Windows run recreated it. This is a causal test, not an inferred codec failure
from the final waveform alone. The resulting difference was confined to a
590-frame span (about 27 ms), with identical total length.

The transform calculated the sample as -4779.499919665047 in PCM units. Its
float output rounded that to -4779.5; nearest-even integer conversion then
produced -4780 instead of -4779. The isolated decoder now carries its existing
double precision through the output buffer. The bridge converts that binary64
value directly to int16, with the same nearest-even and saturation rules. No
voice-specific adjustment, priming change, or reference-waveform substitution
is used by the retained fix. The integer conversion remains free of per-sample
libm calls and independent of the caller's rounding mode.

Fresh native comparisons now pass the explicit `--max-pcm-delta 2` gate for
all 56 configurations, with matching lengths and exact repeats from both hosts.
The Lion paragraph's maximum difference falls from 3961 to 2 PCM units, and
SNR rises from 45.43 to 107.35 dB. All eight cancellation/recovery cases pass.
The converter checks cover 1195698 binary32 and 1220684 binary64 finite inputs
in each of four rounding modes, plus non-finite rejection. Both these checks
and the 4800000 Huffman equivalence cases pass on Windows, both S22 ABIs, and
Watch 2 ARMv7. The decoder rounding-environment check also passes all four modes.

Matched Android trials rotate the previous optimized Glint, corrected Glint,
and FAAD2 through three fresh processes each. All 72 corrected short renders
(24 per device/ABI) match the corrected native Windows Glint output exactly;
the control renders retain their own references. Native signal checks pass.
For the debug phrase, old/new AAC medians are 42/43 ms on S22 ARMv7, 18/19 ms
on S22 ARM64, and 140/145 ms on Watch 2. Corresponding first-pull medians are
40.9/41.0, 32.7/28.0, and 119.5/121.6 ms. These are matched warm measurements,
not acoustic onset or evidence of an ARM64 speed improvement. The retained
precision costs a small amount of decoding time, while preserving the earlier
optimization's substantial gain.

The complete Android audio/settings suite passes with this build on Watch 2
and both S22 ABIs, including reference WAVs, settings application, playback,
and stop/restart. Original APKs, primary ABIs, and exact preference bytes were
restored. The previous native Glint build fails the new explicit comparison
gate on the Lion paragraph, providing a negative control for the fix. The
separate Box paragraph-length issue was still open at that point; the later
restart-tag fix addresses the measured overwrite rather than relying on a
passing ARM64 run.

## Review after the restart and Linux changes

The collector's one-frame replacement exception now requires a zero sample,
matching its documented purpose. Synthetic slices test consecutive restarts
at zero, replacement of the silent kick, preservation of a nonzero one-frame
slice, and a fresh origin after reset. The Android paragraph check retains
the documented four-frame allowance for measured zero-padding variation and
still requires Alex's breath; the existing short reference WAVs are unchanged.

Native Linux Fred's high-rate SIGFPE was the missing platform counterpart of
Windows' existing divide-by-zero recovery. The native Linux handler handles
only zero memory divisors in loaded guest images; the translator's signal
handling is untouched. Independent synthetic cases check operand widths,
instruction bounds, overflow and read-only rejection, preservation of errno,
and prior/default signal ownership. The shared instruction parser is also
used by the Windows handler.

All 56 native Glint configurations remain byte-identical to the build before
the restart/audit/Linux changes, and all eight cancellation/recovery checks
pass. Thirteen native Linux Glint configurations, including Fred at 380/387
wpm, match Windows Glint byte for byte with exact repeats. Formant controls
also match in the FAAD2 build. Sixteen public-library cases cover both codecs,
Tiger Fred/Vicki and Leopard Alex/Vicki at 180/387 wpm, with streaming,
buffered output, cancellation recovery, and number settings. Linux CI now
builds Glint and checks its converter, parser, and library without engine data.
The rebuilt Android app passes its complete audio/settings suite on both S22
ABIs and Watch 2 ARMv7; original APKs and preferences were restored. The
collector regression rejects the prior unconditional one-frame replacement
in an isolated negative-control build.
