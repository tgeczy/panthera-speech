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

### Indexed-post regression found on the Ally X

Tomi reported a repeatable 0.6-0.8 s delay navigating from a long post to a
shorter one with Lion driving Sequoia's Alex bank, rate 100 (400 wpm), boost off.
His diagnostic log showed a separate NVDA driver fault: at 13:17:10.678 the
replacement arrived, cancellation finished at .715, but the driver did not
start rendering the replacement until 13:17:11.073. The same host was reused.

An interrupted `_run` set `_spokeSinceCancel = True` after `cancel()` had
cleared it. The next indexed, one-sentence post therefore waited `JOIN_WAIT`
(350 ms) as though it continued a say-all run. Longer posts bypassed that
hold, explaining the directional difference. The same interrupted path
appended its sentence pause with the new epoch, making stale silence playable
and consuming the diagnostic's "first playback feed" timestamp before the
replacement text even reached the engine.

The driver now records the rendering epoch for joining and keeps every pause
with its originating epoch. A cancelled render cannot authorize a joiner wait
or append a new request's silence. Ongoing say-all joining remains enabled.
The generic interruption log no longer claims every interrupted host was
retired; actual retirement has its own log.

Replayed the two texts as NVDA supplied them, including index commands, with
Lion and the Sequoia bank at 400 wpm, abbreviation expansion off, Leopard
phrasing. Nine mid-render interruptions on this development PC fell from
461-486 ms to 99-165 ms; three interruptions after rendering had already ended
were 43-57 ms across the runs. Measurement: first nonquiet PCM at the simulated
player, **not physical sound on the Ally**. The earlier reproduction omitted
indexes and could not exercise this failure. Original Lion Alex was also
tested during diagnosis; the long-post warm PCM controls were not consistently
byte-identical even without cancellation, so no exact-PCM claim is made for
this fixture.

Three deterministic regressions first failed on the original code and pass
with the fix: no join wait after in-render cancellation, and no stale sentence
pause with joining on or off. The existing cancelled-break check now also
requires no leftover silence. 65 marker, joiner, breath and sentence-pause
checks pass; two checks requiring explicit engine variables skip. Breathing
and playback-marker engine checks are included among the passes.

`pantheraspeech-3.2.0-reading-interruption.nvda-addon` contains this fix and
diagnostics with the unchanged 3.2.0 native binaries. Worker retirement policy
is unchanged. Local evidence is in `build/research/sequoia-cancel-20260920/`;
the original Ally log is kept outside the repository. User listening on the
fixed package passed on the Ally X: Tomi reports the transition is consistently
responsive. This fix is in the shared Leopard/Snow/Lion
NVDA driver, not Tiger's separate driver or the Android/SAPI/Linux frontends.

### Tiger cancellation coverage

Tiger has no say-all joiner, so it cannot incur that 350 ms hold. Its separate
driver did have a related sequence-lifetime fault: cancel during a render
before a break, rate, pitch or volume command could still render the abandoned
remainder, and label the break's silence with the new cancellation count.
The worker now keeps one cancellation count for the entire dequeued sequence,
checks it after rendering, and passes it into each flush. Four deterministic
regressions reproduced the old behavior and pass with the fix. Ordinary
Tiger render behavior is unchanged.

The shared join/pause regressions now explicitly exercise the Leopard, Snow
Leopard and Lion driver classes. Snow Leopard's unresolved Android native-stop
abort remains separate: it retains worker retirement on Android and still
receives the NVDA joining fix.

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
