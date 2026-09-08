# Android translation runtime and comparison harnesses

The normal Android build uses the pinned Box86/Box64 translators with Glint.
`tools/build_android.py` calls this shared builder, then stages each JNI library
and its dependency/hash manifest. Gradle verifies both ABIs and packages the
selected dependency notices. No engine, voice data, generated audio, or
third-party implementation belongs in this directory.

## Build

On the Windows development setup, use NDK 27.2, CMake 3.22.1, Git Bash and
Python 3. The normal entry points are:

```sh
./build_jni_so.sh armeabi-v7a
./build_jni_so.sh arm64-v8a
```

The builder fetches official upstream repositories when local clones are not
provided. `BOX86_SOURCE`, `BOX64_SOURCE` and `GLINT_SOURCE` select existing
clones; only their pinned commits are exported, never uncommitted changes.
Builds target Android API 26. Linux launcher helpers outside Panthera's guest
interface return unsupported on Android versions lacking their APIs. Each
build has an isolated directory under `build/android-native` and a build log.

For a standalone comparison without changing staged app libraries:

```powershell
py -3 tools/translation_experiment/build.py --backend box86 --source build/research/box86 --out build/translation-box86 --aac glint --aac-source build/research/glint
py -3 tools/translation_experiment/build.py --backend box64 --source build/research/box64 --out build/translation-box64 --aac glint --aac-source build/research/glint
```

The standalone builder retains FAAD2 as its comparison default; the normal
builder explicitly selects Glint. See the [decoder review](../aac_experiment/README.md).
The pins are Box86 `39d3ed203323000c11f47b780f7468fa24a7185d`, Box64
`36d1cd790a6992cf188ef0ad5c5536d811c96d0c`, and the Glint revision recorded
in `tools/aac_experiment/glint.py`.

## Host interface and runtime checks

`box86_adapter.c` and `box64_adapter.c` implement Panthera's narrow synchronous,
identity-mapped guest interface. `src/tiger_box_api.h` declares it independently;
the retained `uc_*` names let the existing host share its dispatch code, but
this is not a general Unicorn API or binary-compatible replacement. Normal
Box builds do not include or link Unicorn.

Box executes inline. `TIGER_INLINE_GUEST` selects a real yield instead of the
sleep needed for Unicorn's separate TCG worker. The Android app retires an
interrupted private worker; it does not wait for an engine's potentially long
stop/reuse path. See [worker lifecycle](../../docs/android-engine-workers.md).

Mapping and translation caches are process-global. `box_memory.h` records
mapping lifetimes, makes per-thread mapping replay idempotent, and invalidates
translated code before unmapping or writing it. `box_signals.h` dispatches
faults according to whether the thread is executing the guest, preserving the
prior native handler's payload, mask and reset disposition. This is the
embedding's supported signal arrangement, not general signal interposition.

`panthera_runtime_check rewrite`, `remap`, and `signals` use synthetic input.
Before the adapter corrections, both ABIs crashed on translated-code writes,
executed stale code after address reuse, and lost the native signal mask.
All six corrected checks pass on Nothing Phone 3 and Pixel Watch 2. The same
checks pass from the normal API 26 builds. They require no Apple data.

`panthera_engine_bench` takes the MacinTalk image, SpeechDictionary image,
voice bundle, and PCM output prefix. It renders eight alternating inputs.
`panthera_engine_logcat_bench` also exercises the JNI stderr-pump startup order.
`android_bench.py` compares renders against local references and records timing.
`memory_box86.c`, `memory_box64.c` and `memory_native.c` are synthetic memory
workloads; their speedup is not a general engine performance claim.

Always select an ADB serial. On these debug installations, copy the standalone
executable to the app's private files directory and run it with `run-as`.
Keep extracted data, PCM and diagnostic logs outside Git. Standalone checks
neither install an app nor change preferences.

## Validation and limits

The earlier paragraph loss was a host timeline bug: consecutive player starts
at sample time zero shared an epoch and overwrote a slice. Each callback now
carries its player epoch. Native paragraph samples remain exact; the Android
paragraph oracle permits up to four padding frames and still requires the
breath. Short AAC references use the appropriate decoder's sample oracle.

The normal APK passes the audio/settings suite on Nothing Phone 3 (ARM64) and
Pixel Watch 2 (ARMv7): all generations, sliders, volume/mute/restore, preview
and service agreement, reference WAVs, playback and stop/restart. Matched
standalone adapter comparisons preserved all 48 Alex renders per device. Warm
phrase first PCM was 20.0 ms before / 20.3 ms after on the phone, and 120.6 /
122.2 ms on the watch. These small samples measure synthesis, not acoustic
latency. Rapid-navigation logs must be examined separately; a completed final
request does not prove every interrupted request reached playback.

The watch remains slower than phones, especially during repeated Alex
interruptions. API 26 compilation and 16 KB ELF alignment are build checks;
they do not substitute for testing an Android 8 or 16 KB-page device. Box64
uses the runtime page size rather than assuming 4 KB.

For isolated debug APK inputs, `-PpantheraExperimentalJni=PATH` overrides the
JNI directory and rejects release tasks. `PANTHERA_RUNTIME=unicorn` plus
`-PpantheraLegacyUnicorn=true` retains the older GPL configuration.

Box and Glint retain their MIT licenses, with separate permissive component
and compiler runtime notices. Optional Unicorn/FAAD2 builds retain their GPL
requirements. See [distribution licensing](../../licenses/DISTRIBUTION.txt).
