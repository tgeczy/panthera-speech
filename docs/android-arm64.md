# AArch64 validation, updated 2026-09-07

Tiger, Leopard, Snow Leopard and Lion now pass the Galaxy S22 framework
suite on both ARMv7 and arm64. Release builds include both ABIs. See
[engine workers and saved settings](android-engine-workers.md) for the
newer-generation fixes, numerical oracles and additional measured voices.

The September 6 results below remain useful background.

Tiger and Leopard pass the Android framework integration suite on the
Nothing A024 (arm64-only), including Fred, Vicki and Leopard Alex, 24 long
AAC utterances, voice switching, playback and interruption/restart.
This is waveform and lifecycle validation; audible quality was not separately
confirmed by a listener during this session.

## The short-render diagnosis was a rate mismatch

The phone's system TTS rate was 215%. The service maps that to 387 wpm.
At 387 wpm desktop Unicorn and arm64 produce the same 4032 PCM frames for
Tiger Fred's `Hello there.`, MD5 `a02f4f810aac04c2095d47a74ff6771a`.
Tracing `cvttss2si` showed correct conversion of nonzero float durations.
The float-to-duration conversion was not the arm64 fault.

The test now explicitly selects normal rate (180 wpm) before applying its
length bound, checks desktop frame counts, and checks Fred's PCM SHA-256.
The original 18144-frame desktop oracle uses Fred's own default rate,
not the Android service's 180 wpm.

## Actual bridge defects fixed

- Darwin i386 mutex storage is 44 bytes. Bionic arm64's native mutex plus
  wrapper magic and alignment occupied 48, overlapping adjacent guest fields.
  Native mutexes and condition variables now live in a synchronized table
  keyed by guest address; copied guest bytes cannot duplicate handle ownership.
  Compile-time bounds protect both guest storage sizes.
- C++ list nodes contain two four-byte links. Reading them as native pointers
  combined adjacent guest addresses into one invalid 64-bit address on Leopard.
- MP queue/task, AUGraph/AudioUnit, SoundConverter and AudioConverter outputs
  now write four-byte guest handles rather than overwriting the next slot.
- AudioUnitReset previously returned while callbacks remained queued. Tiger
  retired those slices itself and the delayed callbacks retired them again,
  driving its pending count to -4 and crashing the next Wakeup. Reset now
  drains callbacks through the pacer before returning. Explicit cancellation
  discards abandoned audio; ordinary between-sentence resets preserve it.
  Dropping callbacks alone left bookkeeping unfinished, so all callbacks must
  finish through the existing path.

## Matched desktop comparisons

Use the correct VoiceDescription creator/id and the same synthesis API:

```text
build/uc/tiger_host_uc.exe --jni-check <MacinTalk> <SpeechDictionary> <voice> <creator-hex> <voice-id> 180
```

Read creator/id as two big-endian 32-bit words at VoiceDescription offsets
4 and 8. Fred is `6d746b33 / 1`; Vicki `6d656f77 / 200`; Alex
`6d656f77 / 201`. Do not compare an unspecified CLI voice/rate with framework
output. The one-shot native and JNI paths can select different Alex segments.

All references below use `Hello there.`, 180 wpm, mono PCM16 at 22050 Hz:

| Generation / voice | Frames | Desktop comparison |
| --- | ---: | --- |
| Tiger Fred | 15792 | PCM byte-identical |
| Tiger Vicki | 15713 | correlation > 0.9999999999; maximum difference 2 |
| Leopard Fred | 17360 | PCM byte-identical |
| Leopard Vicki | 17887 | correlation > 0.9999999999; maximum difference 1 |
| Leopard Alex | 17973 | correlation > 0.9999999999; maximum difference 1 |

AAC differences are measured in signed 16-bit sample units: Android uses
FAAD2; the Windows reference uses Media Foundation. The test enforces frame
counts for these canonical data sets, not AAC byte equality.

These are the September 6 data pairings. The current locally supplied Leopard
Alex pairing renders 17851 frames on both native Windows and Android; the
current device test uses that independently checked reference. The one-unit
AAC comparison above describes this short sentence, not every long passage.

Fred PCM SHA-256:

- Tiger: `cef98214a9eb7c7052053619f027c73badfbf251c016313be31d6091278d8b91`
- Leopard: `ec4be821f742fcd8facd6d215ce4443d01c55ac0dc983d482574c31cfb63c761`

## Build and device checks

```text
./build_jni_so.sh armeabi-v7a
./build_jni_so.sh arm64-v8a
cd src/platforms/android
gradlew assembleDebug assembleDebugAndroidTest
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb install -r app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
adb shell am instrument -w com.pantheraspeech.tts.test/com.pantheraspeech.tts.EngineSmokeTest
```

Select and verify an installed generation before running. The suite also
switches through the other installed generations. Both build types contain
both ABIs; use `adb install -r --abi armeabi-v7a` or `--abi arm64-v8a` to
exercise each on a dual-ABI device. Do not use connectedDebugAndroidTest: its uninstall
can remove the user's engine data. No Apple engine data or generated audio is
included in this repository.

## Limits and follow-up

The September 6 audit targets (blocks/GCD, SQLite handles and outputs, CF
container inputs, CFNumber widths and typed return values) are addressed in
the [September 7 continuation](android-engine-workers.md). Android still
reports SQLite unavailable when the system library is outside its linker
namespace. The recent device runs used the S22; a fresh watch run remains
separate validation, and not every supplied voice bundle has been sampled.

FEX was not needed for these correctness fixes. Its documented use is x86
programs on ARM64 Linux (https://github.com/FEX-Emu/FEX); embedding its core in
this Android loader remains separate work. Apple-Eloquence-ELF's ABI bridge
notes are useful, but its direct ARM64 conversion starts from an ARM64 engine
slice; these Tiger/Leopard binaries are i386.
