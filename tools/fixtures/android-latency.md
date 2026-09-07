# Android cancellation and latency checks

Run `tools/android_latency_check.py --serial DEVICE --mode MODE` from a built
Android tree. The runner installs the debug app and test APK, captures main,
system, crash and event logs continuously, and restores preferences byte for
byte even after a failed check. Each command targets one explicit device.

- `lifecycle`: request cancellation before attachment, active cancellation,
  delayed cancellation after completion, and eight real Binder retire/rebind
  cycles. A stale worker identity must not retire its replacement.
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
The app still uses Unicorn; translator experiments are separate.

Android keeps a disconnected service binding active. The owner must unbind;
intentional shutdown now does this before killing the private worker, then
waits for Binder death before reusing that generation's service component.
See the [ServiceConnection contract](https://developer.android.com/reference/android/content/ServiceConnection).
