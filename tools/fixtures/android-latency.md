# Android cancellation and latency checks

Run `tools/android_latency_check.py --serial DEVICE --mode MODE` from a built
Android tree. The runner installs the debug app and test APK, captures main,
system, crash and event logs continuously, and restores preferences byte for
byte even after a failed check. Each command targets one explicit device.
Use `--no-install` when testing the installed build to keep package replacement
and the screen reader's engine reconnection out of the measured run.

- `lifecycle`: request cancellation before attachment, active cancellation,
  delayed cancellation after completion, and eight real Binder retire/rebind
  cycles. A stale worker identity must not retire its replacement.
- `reuse`: leave completed PCM unread, cancel the request, and require the same
  worker Binder and exact replacement PCM for four cycles per generation.
  This uses the normal 180 wpm reference rate; it is a lifecycle check.
- `audio`: the existing audio regression suite, including installed generations,
  reference WAVs, settings, volume, breaths, playback and cancellation.
- `native`: warm streaming through the worker IPC; first PCM is not playback.
- `rapid`: 30 `QUEUE_FLUSH` requests at each of 150 and 100 ms, followed by a
  request that must complete. Reports how many began before the next request.
- `true`: completed playback and cancellation probes with a playback marker.

The latency probe's PASS means its completion/error checks passed. It does not
mean the responsiveness target passed. The range marker estimates when the
first nonquiet sample reaches Android's playback position; it is not an
acoustic measurement.

September 7, 2026: both the Nothing Phone 3 (ARM64) and Pixel Watch 2 (ARMv7)
pass ownership and audio regressions after moving retirement to the binding
owner. The phone rapid run has zero scheduled automatic service restarts:
53 deliberate retirements and 54 worker starts, plus the public app process.
Both rapid runs report zero synthesis errors, but miss almost every navigation
deadline. Phone Alex's final replacement playback is 471/526 ms, watch Alex's
3225/4614 ms (150/100 ms cadence). These are failures of the latency target.
Those baseline measurements used Unicorn.

The experimental Box APK plus completed-renderer reuse was also measured on
September 7. During playback, Android can still be accepting queued PCM after
native synthesis finishes. A stop now checks the completed renderer and keeps
it warm. Start must have been acknowledged for that particular request;
otherwise an idle result could describe the preceding utterance.

| Device / voice | Playback before next request, 150 ms | 100 ms |
| --- | ---: | ---: |
| Nothing Phone 3 / Fred | 30/30 | 30/30 |
| Nothing Phone 3 / Alex | 30/30 | 0/30 |
| Pixel Watch 2 / Fred | 28/30 | 23/30 |
| Pixel Watch 2 / Alex | 0/30 | 0/30 |

All bursts reported zero synthesis errors. The phone's final Alex replacement
began playback at 83/250 ms, the watch's at 697/1173 ms. These are individual
runs, not guaranteed limits. Unfinished synthesis still requires retirement;
the fastest repeated interruptions remain a release blocker. Whole input
paragraphs are preserved, including internal sentence boundaries and breaths.

The original Unicorn backend passed completed-renderer reuse at 180 wpm on
both devices. A separate control exposed a Tiger Fred failure at 387 wpm on
"Restart with debug logging enabled." through direct native rendering, without
the new completion query. The same text passes at 180 wpm; the Windows CLI
passes at 387, though that uses a different entry path. Keep this as a separate
unresolved high-rate case, rather than treating a reuse-test failure as its
diagnosis. The direct device check
accepts `nativeGeneration`, `nativeVoice`, `nativeText`, and `nativeWpm`.

Android keeps a disconnected service binding active. The owner must unbind;
intentional shutdown now does this before killing the private worker, then
waits for Binder death before reusing that generation's service component.
See the [ServiceConnection contract](https://developer.android.com/reference/android/content/ServiceConnection).

## Galaxy S22: both ABIs on the same device

September 7, 2026, SM-S901U1 on Android 16, experimental Box APK with the
completed-renderer reuse change (`572ec12`). The same APK was installed with
`adb -s DEVICE install -r --abi ABI APK`; both the package's selected ABI and
the running engine's Box64/Box86 banner were checked. Engine data stayed in
place and preferences were restored byte for byte. ARM64 was restored afterward.

Both ABIs passed the full four-generation audio suite, including paragraph
breaths, settings, repeated AAC rendering and cancellation recovery. Both also
passed completed-renderer reuse. Short PCM hashes matched the previous device
references for the corresponding ABI.

These Alex measurements use Leopard at 387 wpm. Native first PCM includes worker
IPC; playback uses the Android marker described above.

| Measurement | ARM64 / Box64 | ARMv7 / Box86 |
| --- | ---: | ---: |
| Warm Seven, median first PCM | 16 ms | 12 ms |
| Warm debug-logging phrase, median first PCM | 41 ms | 22 ms |
| Eight completed digits, median playback start | 95 ms | 66 ms |
| Ten replacements of unfinished long text, median playback start | 383 ms | 479 ms |

Rapid runs were ordered ARM64, ARMv7, ARM64, ARMv7. The table retains both runs:

| Alex playback before next request | ARM64 first / repeat | ARMv7 first / repeat |
| --- | ---: | ---: |
| 150 ms spacing, 30 requests | 1 / 30 | 30 / 30 |
| 100 ms spacing, 30 requests | 1 / 26 | 1 / 1 |

Fred reached all 30 playback markers at both spacings in every run. No burst
reported a synthesis error. The first runs had zero automatic service restarts;
intentional worker retirements were 25 on ARM64 and 8 on ARMv7. Warm rendering
can be fast on either ABI, while unfinished work still causes costly restarts.
The ARM64 variation has not been isolated to warm-up, scheduling or thermal
conditions, so the best run is not a guaranteed latency figure.

An APK carrying both libraries normally selects the S22's primary ARM64 ABI;
ARMv7 here was an explicit test override. ABI selection is separate from CPU
affinity. The host already requests the fastest cores for synthesis workers.
See Android's [ABI selection rules](https://developer.android.com/ndk/guides/abis#am).
