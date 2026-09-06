# Android TTS bring-up: 2026-09-06

Fred playback is confirmed by Tomi on the Pixel Watch 2. Earlier AudioTrack
logs established neither audible output nor a working persistent engine.

## The silent engine and second-request hang

The Unicorn bridge classified `_AddDurationToAbsolute` and
`_SubDurationFromAbsolute` as 32-bit returns. Both actually return a 64-bit
AbsoluteTime in EDX:EAX. Their arguments are a 32-bit Duration followed by
a packed 64-bit AbsoluteTime on i386; ARM AAPCS aligns the latter to an even
register pair. Untyped word forwarding therefore also corrupted the input.

The first request produced PCM but never stopped its graph. A second request
blocked in `SpeechChannelManager::SpeakBuffer`, sleeping in `_usleep` while
both workers waited on empty MP queues. Typed clock dispatch and restoring
both return registers fixed completion and repeated synthesis. Temporary
queue and per-thread tracing, a quiet-completion workaround, and a thread
affinity experiment were removed after isolating this cause.

The Android integration also now:

- Returns `eng-USA` in CHECK_TTS_DATA, reserving Panthera voice IDs for the
  modern Voice API; reports the supported country and respects verification.
- Holds engine ownership across the whole stream, including preview renders.
- Respects the framework audio-buffer limit and reports native timeouts.
- Holds back the same 512 samples as desktop streaming and drains the pacer
  before declaring completion.
- Posts cancellation from Binder and stops/settles the channel on its owning
  synthesis thread, so concurrent native stop calls cannot race.
- Waits for the sample player's playback head instead of a fixed tail delay.

## Reproduce the device checks

Build `build_jni_so.sh`, then from `src/platforms/android` run:

```text
gradlew assembleDebug assembleDebugAndroidTest
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb install -r app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
adb shell am instrument -w com.pantheraspeech.tts.test/com.pantheraspeech.tts.EngineSmokeTest
```

The app must already have user-supplied Tiger engine data and a successful
Check Engine. Tests use Android's TextToSpeech client, select Fred, render two
WAVs, check identical bytes and nonzero amplitude, exercise playback callbacks,
then interrupt a long request and require the next request to complete.
These tests play speech; avoid interacting with TalkBack during the run.
No engine data or generated audio is committed.

On the Pixel Watch 2, each `Hello there.` render at 180 wpm was 15,792 mono
16-bit samples at 22,050 Hz, peak 25,231. Both 31,628-byte framework WAVs had
SHA-256 `d3583e45e67d6556f57ebc3fa4b936e8a5b72f345cfb306694b017fecd6e2f5c`.
The native library and APK build, device integration test, and 32-bit desktop
Unicorn build passed. Audible playback was separately confirmed by Tomi.

## Remaining work

Cancellation recovers, but a 700-character stress request still delayed the
next request about 20 seconds on this watch. The test measures completion, not
an acceptable cancellation-latency target. Investigate native stop/worker
latency next; do not call this responsive long-text interruption.

The Android port remains Tiger/armeabi-v7a only, with AAC voices, other engine
generations, arm64, import UX, and full settings parity still separate work.
