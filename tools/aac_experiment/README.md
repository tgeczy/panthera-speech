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
order and the original calculation for larger escape values.

The host bridge wraps raw AAC-LC access units in ADTS, validates the supported
mono ASC/rate, checks the returned frame size, and retains shared priming and
tail handling. It saves the caller's floating-point environment, uses the default
environment while decoding, then restores the caller's state. This matters on
native i386, where guest arithmetic and host arithmetic share a thread.

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

The Windows `fpcheck.exe` accepts a caller-supplied 22050 Hz mono ADTS AAC-LC
file. It checks identical decoded PCM under all four rounding modes and verifies
that the caller's rounding mode survives. No AAC test file is distributed.

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
  is unresolved; it is not waved through as rounding. A decoder-output dump
  before trimming has unequal backend tail lengths and is not a valid direct
  sample comparison.
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
recovery cases, and all four rounding-mode checks. None of these results turns
the unresolved Lion paragraph difference into a pass.

## Before adopting

Resolve the remaining native Lion PCM difference and Box paragraph timing;
recover ARMv7 performance; audit decoder bounds, reset state (including PNS),
and source provenance. Upstream generates normative AAC tables by extracting
and comparing tables from vo-aacenc and FFmpeg; its MIT declaration alone is
not a completed provenance review. Keep the current release license notices
and decoder defaults until the complete dependency selection is settled.

[oxideav-aac](https://github.com/OxideAV/oxideav-aac) was also inspected at
`365e40e00c22a20fd83ba2bdc1aee826cc937cbc`. It declares MIT, but its current
`filterbank::imdct` uses nested sample/coefficient loops and a cosine per term
(2048 by 1024 for a long AAC-LC window). It was not integrated or benchmarked;
that implementation would need substantial optimization for this latency goal.
