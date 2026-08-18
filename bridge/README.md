# The old VM bridge

Kept as a **reference**, not as the product.

This is how the project started: Apple's engine running in a PowerPC Tiger
guest under QEMU, with NVDA sending text over a local port. It worked, and it
was too slow to read with — about 1.4 s an utterance, almost all of it `dyld`
relaunching `say`.

Its remaining value is as an oracle. It renders the same voices through the
same engine on the hardware Apple built it for, so it is the thing to A/B
against if the native host ever sounds wrong.

- `start-tiger.cmd` boots QEMU against a copy-on-write overlay
- `tiger-guest-server.py` goes in the guest as `~/srv.py` (Python 2.3)
