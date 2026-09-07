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
