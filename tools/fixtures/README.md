# Navigation workload

`navigation-posts.json` contains two anonymized post texts supplied by the
maintainer to reproduce stalls when rapidly switching between longer items.
Personal names and account handles have been removed. The announcement and URL
are speech input, not a claim about the current NVDA release.

`navigation-long-posts.json` pairs the short post with the maintainer's longer
podcast testing post. Personal names, including the narrative name, have been
removed or replaced. Its URL is also only speech input.

On Windows, run the real driver and engine with the repository's simulated
player, without touching the user's NVDA configuration:

```powershell
py -3 tools/rapid_navigation_check.py --generation leopard --tree D:/speech-leopard --interval-ms 150 --output build/rapid-leopard.json
py -3 tools/rapid_navigation_check.py --generation lion --tree D:/speech-lion --interval-ms 100 --output build/rapid-lion.json
py -3 tools/rapid_navigation_check.py --generation leopard --tree D:/speech-leopard --posts tools/fixtures/navigation-long-posts.json --host-timing --output build/rapid-leopard-long.json
```

The driver uses Alex at slider rate 50. Requests alternate at a fixed cadence;
they do not wait for earlier synthesis to finish. The JSON records time from
cancellation/replacement request to the first nonquiet PCM passed to the player,
including requests that were superseded before producing any. It does not
measure physical playback, NVDA's upstream speech manager, or browser event
delivery. A successful exit only establishes that the final request produced
audio; it is not a responsiveness pass.

Use `--host path/to/tiger_host.exe` to compare an isolated experimental host
without replacing the staged add-on executable. The report records the selected
host path. Keep performance comparisons sequential so the runs do not compete
for CPU.

The event trace associates cancellation and render start/end with driver epochs,
so a skipped request can be distinguished from a slow render. `--host-timing`
enables `TIGER_CANCEL_TRACE` in the native host and includes its diagnostics in
the JSON. Rebuild the host with `build.sh` before using this option. It reports
the stop call and callback-settle durations separately, plus MP critical-region
waits and holds of at least 10 ms. Lock timing uses the monotonic performance
counter and measures an outermost recursive hold once. The trace is disabled
by default and does not enable per-sample audio statistics.

Initial September 7 measurements, 16 requests each: Leopard delivered PCM before
the next request on 8/16 at both 150 ms and 100 ms cadence; Lion managed 16/16 at
150 ms and 15/16 at 100 ms. These small samples justify investigating sustained
replacement behavior; they do not diagnose an exact third-request stall.

The longer-post reproduction exposed native Leopard cancellations taking
18–145 ms in the stop call and another 34–104 ms waiting for callbacks in one
run. Subsequent lock tracing located much of the stop time in an MP critical
region held by the synthesis worker. These are measured waits, not a diagnosis
that the guest cannot be interrupted. Increasing the guest clock multiplier
from 128 to 1024 made replacement behavior worse; scaling `usleep` provided no
clear improvement either. Neither experiment changes the production defaults.

## Native Windows follow-up, September 7

The earlier [Leopard cancellation PR](https://github.com/tgeczy/leopard-speech/pull/2)
and [Lion/Snow Leopard handoff PR](https://github.com/tgeczy/panthera-speech/pull/7)
addressed this same family of delays. Restarting on every interrupt and splitting
streamed paragraphs have both been tried; neither is a safe default remedy.

An isolated Windows diagnostic host separated lock-holder CPU time from wall
time. A representative Leopard Alex hold was 110 ms wall / 109 ms CPU, with
52 ms inside AudioConverter. Further instrumentation measured about 1 ms of
output-buffer allocation versus 40 ms in Media Foundation decoding during a
similar hold. These coarse CPU samples and process-wide shim counters identify
where to investigate; they are not a full profiler or a guest limitation verdict.

Returning a converter error after cancellation improved one rapid run to 16/16,
but changed Vicki's next utterance, including one render 23 frames short. This
experiment was rejected. The recovery test in `test_leopard_interrupt_cost.py`
now checks complete replacement PCM and same-host reuse after alternating the
two navigation fixtures. Its four Vicki cases fail with that experiment enabled
and all eight Alex/Vicki, rate and cancellation-offset cases pass normally.

The retained change uses `fabsf` for vector magnitude scoring while preserving
the sequential float accumulation. Three alternating control/candidate sessions,
four full renders each, of the 2,551-character post at 387 wpm produced identical
1,819,641-frame PCM in all 24 renders. Excluding each session's first render,
median completion fell from 3,718.73 to 3,473.25 ms (6.6%). This is full-render
time through the native driver with no physical player, not first-sound latency.

At 150 ms navigation cadence, three paired 16-request runs delivered nonquiet
PCM before the next request on 8/8/8 requests in the control versus 11/10/8 with
the change. The gain is incremental: the remaining rapid-cancellation gap is
not fixed, and the text is still handed to the engine whole. Numerical checks
cover accumulation order, reversed/nonunit/zero strides and empty input;
paragraph-breath, streaming and cancellation regressions also pass.
