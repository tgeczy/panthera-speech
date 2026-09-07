# Navigation workload

`navigation-posts.json` contains two anonymized post texts supplied by the
maintainer to reproduce stalls when rapidly switching between longer items.
Personal names and account handles have been removed. The announcement and URL
are speech input, not a claim about the current NVDA release.

On Windows, run the real driver and engine with the repository's simulated
player, without touching the user's NVDA configuration:

```powershell
py -3 tools/rapid_navigation_check.py --generation leopard --tree D:/speech-leopard --interval-ms 150 --output build/rapid-leopard.json
py -3 tools/rapid_navigation_check.py --generation lion --tree D:/speech-lion --interval-ms 100 --output build/rapid-lion.json
```

The driver uses Alex at slider rate 50. Requests alternate at a fixed cadence;
they do not wait for earlier synthesis to finish. The JSON records time from
cancellation/replacement request to the first nonquiet PCM passed to the player,
including requests that were superseded before producing any. It does not
measure physical playback, NVDA's upstream speech manager, or browser event
delivery. A successful exit only establishes that the final request produced
audio; it is not a responsiveness pass.

Initial September 7 measurements, 16 requests each: Leopard delivered PCM before
the next request on 8/16 at both 150 ms and 100 ms cadence; Lion managed 16/16 at
150 ms and 15/16 at 100 ms. These small samples justify investigating sustained
replacement behavior; they do not diagnose an exact third-request stall.
