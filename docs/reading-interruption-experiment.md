# Local reading-interruption experiment

September 20, 2026, branch `tgeczy/improve-reading-interuption`. Experimental;
not published or included in the 3.2.0 release.

## Android behavior

Cancellation still detaches the request immediately and stops playback. A
completed renderer is reused; short labels retain their existing completion
grace of at most 60 ms.

For an acknowledged, unfinished longer request, all four generations now
try native cleanup. The worker sets the thread-safe stop flag, then queues
`nativeFinish` on its owning synthesis executor. The cancellation executor
waits up to 120 ms for this work and a true completion predicate. It never
waits on the public stop caller. Failure, death or timeout retires the worker
through the existing binding owner. A timed-out native thread is not interrupted;
the process is retired before the next synthesis request can acquire ownership.

Snow Leopard initially remained excluded after a direct-JNI probe aborted on
its third stop. Source reclamation, missing cancellation cleanup and serial
source delivery were subsequently fixed as described below. The final local
candidate includes Snow Leopard after 108 exact direct recoveries and the
120-case service handoff check.
This policy also retains retirement for cancellation during an unacknowledged
start, whose completion predicate could still describe the preceding request.

The 120 ms bounds the wait for cleanup, not total Android scheduling, Binder,
replacement startup or playback latency. A timed-out attempt can make the
fallback slower than immediate retirement. This needs broader device testing.

## Initial Nothing Phone comparison

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
guarantee. No Watch test or user listening A/B yet. This initial comparison
predates the serial-queue fix and Snow Leopard expansion below.

### Snow Leopard dispatch-source race

The September 20 follow-up captured native stacks on the Nothing Phone.
The original abort reported a destroyed mutex. Instrumented repetitions
aborted in `pthread_detach`, called by `CloseHandle` from
`sh_dispatch_source_create`: source creators could concurrently select and
close the same retired slot. The slot scan, thread-handle close, claim and
initialization now share a lock with retirement. The lock is released before
waiting for pool space, and never spans a guest callback.

`tools/gcd_sources_check.c` exercises eight concurrent callers without Apple
data. The old code reported 8,200 duplicate/invalid-handle comparisons in
8,000 allocations; the patch reports zero. The check is included in the native
i686 Linux and Windows builds and passes on both. Native Windows Snow Leopard
Alex and Lion Alex also pass three cancellation/replacement comparisons each;
Linux Tiger Fred and Leopard Alex pass the streaming checks.

This fixes a shared host race, not the whole Android reuse problem. Three
instrumented direct-stop runs previously aborted; with the lock, three runs
completed all 12 cycles each, but some replacement audio differed. With
per-destruction tracing removed, two runs completed with mismatches and a third
exited after exhausting the guest arena. Some `nativeFinish` returns still
precede `nativeRenderComplete`; even a true completion flag did not guarantee
exact replacement PCM in these research sequences. The diagnostic probe
deliberately continued after failures to collect evidence; these are not
passing recovery tests or supported worker behavior. Do not enable Snow
Leopard cleanup on the strength of removing one abort.

With Snow Leopard's existing retirement policy retained, the patched Android
build passes 120 worker handoffs with exact replacement PCM and the audio,
preview/service, volume and playback suite. The original app and test APKs
were restored in place after each batch; no uninstall or data move was needed.
Research artifacts and symbolized-stack evidence are local under
`build/research/snow-stop-20260920/`.

#### Cancellation follow-up: completion and queued work

Further isolated Android probes found that `g_stopped` can be set while the
engine's `soStatus.outputBusy` remains true and source handlers are still
running. Waiting for status to become idle and for active handlers to finish
did not establish safe reuse: replacement mismatches and a ten-second busy
timeout remained. Requiring every timer to be disarmed was also unsuitable;
an armed worker source persisted even across successful idle renders.

The source shim ignores its target queue. The trace recorded up to six
simultaneous source handlers targeting the same queue created with a null
attribute (a serial queue). An experimental per-queue mutex removed that
overlap, but did not eliminate the cancellation failures. It is not a complete
queue implementation: it supplies neither FIFO ordering nor asynchronous
dispatch semantics. None of these research changes is enabled in production.

Three captured replacement failures contain 14,183 samples instead of 13,910.
In each, the entire reference word is present byte-for-byte after a 273-sample
prefix. A buffered trace caught that prefix arriving as two scheduled slices,
229 and 44 frames, tagged with the new request's utterance number. The first
slice has 229 nonzero floats; the second has 43 and a trailing zero. The next
slice begins the correct word. This is evidence of extra leading audio, not a
reason to trim a fixed number of samples. Whether the prefix comes from work
left by the cancelled request or retained engine buffer state remains open.

Immediate per-slice logging passed 36 interruptions; buffered tracing later
caught two failures in five fresh processes. Additional request-phase probes
caught busy timeouts but not the prefix, so the precise point within request
startup remains unmeasured. The native Windows matched-text/rate/gap control
passed 36 replacements, but uses Media Foundation rather than Android's Glint
decoder and does not prove the fault is exclusive to Android.

The later provenance probe identified the first 272 samples as an exact match
to a fragment of the interrupted text (sample 73,748 in its full render),
followed by a zero. The first stale slice was scheduled during `SESpeakBuffer`
on a thread outside a timer handler; the second followed from the completion
path. Disassembly-derived, read-only snapshots of the Snow Leopard audio
object showed state=0 and scheduled=0, but **two buffers still occupied** after
settling and before the next request. Its `Wakeup` path subsequently schedules
them. The producer/stop ordering that leaves those buffers remains under
investigation; these private field offsets are not used by production code.

#### Restore the missing cancellation cleanup

Snow Leopard does release its dispatch sources, contrary to the earlier
observation in the host's source-pool comment. `MTBEWorker::AddTask` installs
`_MTBEWorkerCancelTask` with `dispatch_source_set_cancel_handler_f`; that import
was missing from the shim table. Its `CancelTask` releases the source and
deletes the 24-byte task record, so silently ignoring registration skipped
the engine's cleanup on every task.

The host now records the cancellation handler and runs it once, after the
source's event handler has returned and before its thread becomes reclaimable.
It never invokes guest cleanup under the source-pool lock. This restores
per-source cleanup ordering; it does not solve the existing target-queue
ordering limitation. The ordering requirement is described in
[Apple's cancellation-handler documentation](https://developer.apple.com/documentation/dispatch/dispatch_source_set_cancel_handler_f).

The allocation check now also covers external cancellation during an active
event, cancellation from inside the event, repeated cancellation and a live
disarmed timer. The old host fails on the missing import; the fixed native
Windows and Linux hosts pass, with one cleanup call and no overlap in each
case. An Android diagnostic observed 1,349 cleanup callbacks and matching
source releases in one process. Replacement corruption still occurred in a
separate direct-reuse run, so this is not evidence that reuse is safe.

With the cleanup fix alone and Snow Leopard's retirement policy retained,
Android passes 120 exact worker handoffs and the playback suite. Native
Windows Snow Leopard and Lion each pass 12 direct cancellation/replacement
comparisons. Linux Tiger Fred and Leopard Alex streaming recovery checks also
pass. Original phone app and test packages are restored after each batch.

#### Serial source delivery and the reuse candidate

Timer threads previously invoked guest handlers independently, despite their
serial target queues. A per-queue mutex reduced overlap but still failed the
audio checks. Delivering source events and cancellation callbacks through a
FIFO on one persistent worker per serial queue passed 108 direct interruptions
with the extended status wait, then another 108 using the ordinary wait.

`tiger_host_gcd_queue.c` now implements that delivery without diagnostic
instrumentation or engine-private offsets. Timer threads wait for the callback
to return, so their source/context cannot be reclaimed during delivery. Events
cancelled while waiting are skipped; cleanup remains queued. Different queues
can advance independently. General `dispatch_async`/`dispatch_sync` block
semantics are unchanged and remain a separate limitation.

One related lifetime gap is closed: a source cannot be recycled while its
`CreateThread` call has yet to publish the thread handle. A fast callback can
cancel itself before that call returns. Queue-handle allocation is also
synchronized; exhaustion of the existing 64-handle pool now fails explicitly
instead of aliasing a live queue.

The native check holds an event open and requires another source on the same
queue to wait, while a different queue progresses. It also checks that a
queue's callbacks share its persistent worker. The previous host fails; the
new Windows and Linux builds pass, alongside the 8,000-allocation and cleanup
ordering checks. Native Windows Snow Leopard and Lion each pass 12 matched
replacement comparisons. Linux Tiger Fred and Leopard Alex streaming checks
pass. The clean Android native build passes 108 direct Snow Leopard Alex
interruptions with exact replacement PCM, including uninterrupted controls;
median stop-to-replacement PCM is 137 ms in that probe.

The Android service candidate now allows Snow Leopard cleanup with the same
120 ms budget and retirement fallback as the other generations. All 120
service replacements are byte-exact; the playback suite and 26 JVM tests pass.
Snow Leopard reuses 25 of 30 workers in this run. Its six long-text replacement
times are 467, 142, 108, 534, 122 and 83 ms (median 132 ms). The two slow cases
retired their workers: a failed cleanup attempt still adds cost before restart.
This is a follow-up run, not a randomized latency comparison against the
initial table. Public stop calls remain in the probe's 0 ms measurement bin.

The original phone app and instrumentation were restored and verified by
SHA-256 after testing. No new build is published. The normal JNI staging
directory was not overwritten; the candidate APK and research results remain
under `build/research/snow-stop-20260920/`.

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

The shared join/pause regressions explicitly exercise the Leopard, Snow Leopard
and Lion driver classes. The Android native-stop investigation and its source
queue changes above are separate from the NVDA joining fix.

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
