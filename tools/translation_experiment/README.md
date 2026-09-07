# Android translation experiments

Research harnesses, **not a shipping emulator backend**. They use Panthera's
existing public synthesis API and locally extracted engine paths. No engine,
voice data, generated audio, or third-party source belongs in this directory.

## Experimental Android app integration

The builder also produces `OUT/build/libpanthera.so`, with a static C++ runtime,
16 KB ELF alignment, and only the JNI entry points exported. This is an isolated
output: it does not replace the normal Unicorn library or install an APK.

For a debug comparison, create an ignored directory containing
`jniLibs/arm64-v8a/libpanthera.so` from Box64 and
`jniLibs/armeabi-v7a/libpanthera.so` from Box86. Put both translators' LICENSE
files in its sibling `assets` directory as `box64-LICENSE.txt` and
`box86-LICENSE.txt`. Pass the absolute `jniLibs` path to Gradle:

```powershell
.\gradlew.bat -PpantheraExperimentalJni=C:/path/to/experiment/jniLibs assembleDebug assembleDebugAndroidTest
```

The override rejects release tasks. Normal builds retain the normal JNI inputs.
Use the device runner documented in `tools/fixtures/android-latency.md`, with
an explicit serial. The installed debug app can differ from the default build;
record which backend each measurement actually uses.

Box runs guest execution inline. `TIGER_INLINE_GUEST` selects a real yield in
the shared host instead of the 1 ms sleep intended for Unicorn's separate TCG
thread. In one controlled phone comparison this reduced median first PCM for
Leopard Alex's Seven from 26.7 to 7.6 ms and the phrase from 41.7 to 17.7 ms,
with all samples unchanged. The corresponding watch change was small.

`android_bench.py` repeats the standalone process, compares each render with
eight existing local PCM references, and writes a JSON report. Its optional
`--signal-check` verifies that a native thread's SIGILL handler survives Box
initialization. The experimental signal dispatcher retains prior native
handlers outside guest execution. This test does not establish full Android
runtime compatibility: signal registration, fault handling, mapping lifetime,
and code-cache invalidation still need review.

The experimental APK passed the watch's complete audio suite. The phone suite
exposed a Lion paragraph length variation (527289 frames instead of 527288).
Repeated standalone controls retained the paragraph breath, but timing changes
can alter the collected timeline; slowing callback pacing removes the measured
length variation, whereas precise rounding alone does not. This is unresolved,
and the short-reference matches must not be reported as full audio correctness.
Generated PCM and detailed diagnostic logs stay outside Git.

The release gate remains audible, reliable replacement speech during rapid
navigation: the requested target is 100–200 ms on the watch and 50–100 ms on
phones. First PCM from this standalone harness does not establish that gate.

## September 7, 2026: Pixel Watch 2 (ARMv7)

Box86 commit `39d3ed203323000c11f47b780f7468fa24a7185d`, NDK 27.2.12479018,
Android API 28, Release, ARM dynarec enabled. The harness uses Leopard Alex,
387 words/minute, volume 90, phrase breaks `fewest`. Eight renders alternate
`Seven` and `Restart with debug logging enabled.` within one process.

| Warm input | Unicorn first PCM | Box86 first PCM | Unicorn complete | Box86 complete |
| --- | ---: | ---: | ---: | ---: |
| Seven | 412–418 ms | 44 ms | 552–558 ms | 85–91 ms |
| Restart with debug logging enabled. | 1,426–1,454 ms | 99–106 ms | 2,438–2,481 ms | 331–342 ms |

Box86's first, cold `Seven` took 214 ms to first PCM, after 358 ms engine
initialization. All eight renders matched the successful Unicorn run byte for
byte: 6,026 and 22,077 frames respectively. SHA-256 of the PCM:

- Seven: `81df35191cd5b567ca727c54793e4341c6eb066809089baa71d053baf810cb2f`
- Phrase: `7961f85dfe049671fadacb85dfc3f64c471873f23ac592f266d8e3c59ba128d3`

The first standalone Unicorn comparison crashed in native code (exit 139);
the next completed. That failure remains undiagnosed. These are small samples
from one device, not a broad correctness or reliability claim. The standalone
process also cannot open the system SQLite library; both sides logged that
limitation. Android service playback and interruption were not exercised here.

The preceding memory benchmark runs the same 99-byte i386 blob from
`android/harness/src/workload.c` through both translators. One million steps
took approximately 840 ms under Unicorn, 140 ms under Box86, and 127 ms as
native ARM code. All returned `0xd7d42e80`. This is a dependent-memory-access
microbenchmark; its speedup must not be generalized to arbitrary engine code.

## Files and experimental limitations

- `engine_bench.c`: identical public-API driver for either translator. Accepts
  engine, dictionary, voice bundle, and output prefix; writes eight raw PCM files.
- `memory_box86.c`, `memory_box64.c`, and `memory_native.c`: synthetic drivers.
- `box86_adapter.c` and `box64_adapter.c`: temporary implementations of the narrow
  Unicorn API subset Panthera currently calls. The original shim dispatch remains
  in use. Guest and host mappings must be identical.

## Building and running

Cancellation remains a separate release blocker. A quiet standalone Box86 run
on the watch, interrupting a roughly 2,000-character paragraph after first PCM,
spent 22.5 seconds waiting to acquire an MP critical region held by a synthesis
worker. The replacement then produced first PCM in 63 ms and matched the normal
6,026-frame Seven reference exactly. Repeating with lock tracing located the
wait inside the stop call, rather than emulator initialization or the final
37 ms callback settle. This is a failed responsiveness result, despite correct
replacement PCM and successful process exit. The app still needs a proven
cancellation strategy; faster ordinary rendering does not establish one.

On this Windows development setup, with NDK 27.2, CMake 3.22.1, Git Bash, Python
3.13, and the existing FAAD2 objects already built:

```powershell
py -3 tools/translation_experiment/build.py --backend box86 --source build/research/box86 --out build/translation-box86
py -3 tools/translation_experiment/build.py --backend box64 --source build/research/box64 --out build/translation-box64
```

For an explicit Glint AAC comparison, add `--aac glint --aac-source PATH`.
See the [AAC experiment](../aac_experiment/README.md) for the pinned source,
audio comparisons, and remaining limitations. FAAD2 remains the default.

`--source` is a local clone of the official translator repository containing
the pinned commit. The script exports that exact commit to a new directory,
applies the compatibility changes, copies the host sources, and builds there.
It changes neither the supplied clone nor the production build. Compiler output
is in `OUT/build.log`. Rebuild an existing output with `cmake --build OUT/build`.
Box64 is pinned to `36d1cd790a6992cf188ef0ad5c5536d811c96d0c`.

`OUT/build/panthera_engine_bench` takes four arguments: the MacinTalk image,
SpeechDictionary image, voice bundle, and writable PCM output prefix. It writes
eight files suffixed `.0` through `.7`. Run the same driver linked against
Unicorn for a controlled comparison; require both successful exit and matching
PCM, not just matching duration. The standalone build uses `TIGER_SHARED` to
retain stderr rather than start the app's log-pump thread.

ADB commands must name the device serial. On these debug installations,
`run-as` cannot execute from `/data/local/tmp`; copy the executable into the
app's private `files` directory and run it there. Engine paths refer to the
user's existing private data tree. Do not add those data files or generated PCM
to Git. These standalone runs do not change Android preferences or install an APK.

## Initial Nothing Phone 3 result (ARM64)

The first Box64 run that completed the engine adapter produced warm first PCM
in 22–27 ms for Seven and 35–44 ms for the phrase, with completion in 52–58 ms
and 159–167 ms. All eight alternating renders match the existing ARM64 PCM
oracles exactly. This is not yet a same-harness timing comparison with Unicorn
on that device, nor an app playback test.

The initial adapter encountered a host-context startup failure with the app's
log-pump thread active. The CLI's native `mmap`/`munmap` interposition routed
host allocations into Box bookkeeping during initialization. Excluding that
interposition resolves the reproduced failure: ten fresh process starts with
the log pump enabled all completed, and all eighty PCM files matched Unicorn.
The adapter also initializes memory bookkeeping before selecting 32-bit guest
mode, so it does not reserve all native addresses above 4 GB. Android's runtime
and native threads retain their normal address space.

`OUT/build/panthera_engine_logcat_bench` exercises this startup order with
`TIGER_JNI` and the same arguments as the stderr benchmark. These remain
standalone tests; signal ownership, mapping lifetime and app integration still
need verification. Box64's optional Linux i386 wrappers
are not used: Panthera supplies its own 32-bit host bridge to the instruction
translator. That distinction is experimental and needs broader coverage.

A fresh build from the pinned-source script reproduced exact PCM on both
devices. A subsequent same-driver ARM64 comparison measured warm Leopard Seven
at 23–30 ms first PCM under Box64 versus 79–82 ms under Unicorn, and the phrase
at 44–48 ms versus 151–156 ms. Full renders took 59–61 / 173–175 ms under Box64
and 109–117 / 283–287 ms under Unicorn. The sixteen additional Snow Leopard and
Lion renders also matched their corresponding Unicorn output byte for byte.
These samples cover two texts; they are not complete voice or generation suites.

## Cancellation remains unresolved

Pass `cancel` as a fifth argument to `panthera_engine_bench` to interrupt a
roughly 2,000-character paragraph after its first PCM, call `panthera_finish`,
then render the normal comparison input. This deliberately tests reuse of the
same engine rather than replacing its process.

On the watch, the first six Box86 cancellation cycles took about 23.2–23.5
**seconds** in `panthera_finish`. The experiment was stopped during the next
cycle (exit 143); it is not a passing interruption test. The replacement renders
returned the expected frame counts, but their PCM was not yet checked in this
run. The ARM64 cancellation case did not run because the watch test was stopped
first. The faster translator therefore does not by itself establish a usable
stop/reuse path. Worker replacement or a diagnosed cancellation fix remains
necessary. The standalone `TIGER_SHARED` harness retains verbose host diagnostics;
control that difference before comparing cancellation times with the APK.

The adapters do **not** implement general Unicorn memory protection or fault
hooks. Mapping invalidation, thread teardown, nested state restoration, all
engine generations, longer text, and interruption need further validation.
It is an experiment to establish performance headroom, not a drop-in library.
The existing production build still uses Unicorn.

Box86 and Box64 source are MIT licensed; retain their copyright/license when
incorporating them. See [Box86](https://github.com/ptitSeb/box86) and
[Box64](https://github.com/ptitSeb/box64). The current FAAD2 decoder is still
GPLv2; a translator change alone does not remove it from the distribution.
FEX remains another ARM64 candidate. Nothing measured here establishes final
phone playback performance or fixes the separate amd64 Linux volume issue.

UTM uses QEMU for cross-architecture emulation; its hardware virtualization
requires matching guest and host architectures. Its iOS SE edition lacks JIT.
See [UTM architecture](https://github.com/utmapp/UTM/blob/main/Documentation/Architecture.md)
and [iOS documentation](https://docs.getutm.app/installation/ios/). Panthera's
Unicorn build already enables JIT and optimization; this experiment changes the
translator and memory-access strategy rather than merely enabling compilation.
