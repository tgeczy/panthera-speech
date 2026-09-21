# Panthera 3.3.0

This one is about moving on. Scroll past a long post, interrupt a paragraph,
or jump to the next item: Panthera can get to the new speech sooner, without
bringing bits of the old text along with it.

## NVDA

- **Quicker transitions away from long text.** Cancelled speech no longer
  leaves its text-joining state or pauses attached to the next request. Tomi
  tested the long Mastodon-post case with Lion and Sequoia's Alex: the large
  gap is gone in his listening tests, with no old words coming back. He also
  heard a smaller improvement with Leopard.
- **Tiger drops the rest of a cancelled speech sequence**, including queued
  breaks and changes that belonged to it.
- Your Say All joining choice stays as it is. The breathing and audio-position
  marker work from 3.2.0 remains in place. Tomi also completed about ten minutes
  of Say All without a stop.

## Android

- **More interrupted requests can reuse their engine worker.** A long request
  now gets a bounded opportunity to finish native cleanup in the background,
  instead of always paying for a replacement process. Playback cancellation
  remains immediate; it does not wait for that cleanup.
- All four generations use this path, including Snow Leopard. If cleanup
  fails or takes too long, Panthera still replaces the worker. This improves
  many handoffs, rather than promising that every interruption is faster.
- The existing short-label behavior, Direct Boot, imported data and settings
  are retained. The final candidate passed 120 exact replacement-audio checks
  and the playback suite on the Nothing Phone running Android 16. Both ARM
  builds were rebuilt; this round did not include a new Watch listening test.

## Shared runtime, SAPI and Linux

The host now handles dispatch-source lifetime, cancellation cleanup and serial
callback ordering more faithfully. Before reusing a cancelled channel, it also
waits for pending callbacks that could otherwise affect the next utterance.
These fixes were particularly important for Snow Leopard's Android crashes
and incomplete replacements after long Lion utterances.

SAPI and Linux receive the shared runtime fixes too. SAPI keeps its existing
interruption policy; it does not acquire Android's worker-reuse policy. Its
installer also retains the 3.2.0 revision-2 fix for preserving which engine
generations you registered. Thanks again to x0 for that report.

Linux support stays the same: native **i686** is the recommended x86 build,
including on x86-64 systems with 32-bit runtime support; **AArch64** uses
Box64 and Glint. AArch64 still awaits listening confirmation on Linux hardware.
The slower x86-64 Unicorn build remains experimental and source-only. Native
marker support and the existing emulated-host fallback are unchanged.

No Apple engines or voice data are included. Keep using your extracted data.

Astra 6 helped shape this release end to end, from tracing the stubborn Snow
Leopard callbacks to checking the replacements across platforms. Tomi's ears
kept us honest about the part that matters: how it feels to read and move on.

Enjoy the smoother scrolling! <3
