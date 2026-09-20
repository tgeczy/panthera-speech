# Local reading-interruption experiment

September 20, 2026, branch `tgeczy/improve-reading-interuption`. Experimental;
not published or included in the 3.2.0 release.

## Android behavior

Cancellation still detaches the request immediately and stops playback. A
completed renderer is reused; short labels retain their existing completion
grace of at most 60 ms.

For an acknowledged, unfinished longer request, Tiger, Leopard and Lion now
try native cleanup. The worker sets the thread-safe stop flag, then queues
`nativeFinish` on its owning synthesis executor. The cancellation executor
waits up to 120 ms for this work and a true completion predicate. It never
waits on the public stop caller. Failure, death or timeout retires the worker
through the existing binding owner. A timed-out native thread is not interrupted;
the process is retired before the next synthesis request can acquire ownership.

Snow Leopard retains retirement: a separate direct-JNI probe aborted on its
third stop, after two exact recoveries. Its root cause is not established.
This policy also retains retirement for cancellation during an unacknowledged
start, whose completion predicate could still describe the preceding request.

The 120 ms bounds the wait for cleanup, not total Android scheduling, Binder,
replacement startup or playback latency. A timed-out attempt can make the
fallback slower than immediate retirement. This needs broader device testing.

## Nothing Phone comparison

A024, Android 16, ARM64 Box64. Control and candidate use identical current
native libraries and differ in worker cleanup policy (plus diagnostic build
version labels). Original phone APK was a previously approved pre-release
candidate; it was backed up and restored without uninstalling or moving data.

Both policies passed 120 handoffs with exact replacement PCM. For the six
long-text cases per generation, median cancellation-to-replacement PCM:

| Generation / voice | Control | Candidate | Worker retirements |
|---|---:|---:|---:|
| Tiger Fred | 213.5 ms | 49 ms | 6 -> 0 |
| Leopard Alex | 276 ms | 105.5 ms | 6 -> 0 |
| Snow Leopard Alex | 355.5 ms | 363 ms | 6 -> 6 |
| Lion Alex | 281.5 ms | 85.5 ms | 6 -> 0 |

Every public stop call in these handoffs returned within the probe's 0 ms
measurement bin. The table measures the replacement, not time to silence.

The real TTS playback-marker probe interrupted a long request and asked for
"Next item" ten times per voice. Leopard Fred's median marker arrival fell
from 308 to 105 ms; Leopard Alex's from 304.5 to 153 ms. These are Android
playback-position notifications, not microphone measurements.

Both builds also passed 240 rapid label requests with no synthesis errors and
all final replacements completed. Not every superseded label played. Captured
label retirements were 5 control / 7 candidate; the short-label policy is
unchanged, and this is not a claim of universal improvement. No automatic
service restarts were recorded in any comparison capture.

Small sequential runs on one phone, not randomized crossover or a latency
guarantee. No Watch test or user listening A/B yet. Keep the fallback and
investigate the Snow Leopard abort before expanding coverage.

## NVDA / Rog Ally

NVDA already requests native stop and attempts host reuse. The Android change
does not change Windows synthesis, SAPI or Linux.

The local `pantheraspeech-3.2.0-reading-diagnostics.nvda-addon` is a diagnostic
package built from 3.2.0 with the branch's Python logging changes, existing
native binaries, and a `cancel-diagnostics` marker. The marker enables
`TIGER_CANCEL_TRACE` while suppressing verbose per-slice host output, which can
distort timing. Normal packages have no marker and retain their normal logging.
The driver now accurately calls its last timestamp the return from the first
playback feed after cancellation, rather than calling it sound onset.

Install the diagnostic package, restart NVDA, enable Debug logging, and read
and interrupt posts normally. Record voice generation and rate with the log.
Restore normal logging afterward; reinstall released 3.2.0 to remove the
diagnostic package. This package does not promise an NVDA speed improvement.

Local evidence and repeatable build/device runners are under
`build/research/android-cancel-20260920/`. No engine or voice data is packaged.

## Validation

- 26 Android JVM tests pass, including owning-thread execution, release of a
  blocked pull, failed cleanup and timeout without interrupting native work.
- Device ownership checks and eight Binder retire/rebind cycles pass.
- Completed-worker reuse preserves exact replacement PCM for every installed
  generation. The audio suite passes with Tiger as the initial reference:
  controls, volume/mute/restore, preview/service identity, reference WAVs,
  playback and stop/restart. It also exercises the other installed generations.
- 31 focused NVDA marker checks pass, 2 skip; 23 bridge, secure-screen and
  Leopard interruption checks pass. Diagnostic archive syntax/ZIP checks pass,
  and the copied network-drive artifact matches its source SHA-256.
