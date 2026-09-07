# -*- coding: utf-8 -*-
"""Rate boost must not leave a hole between the speech and its own tail.

Reported by Tomi, at rate 50 with rate boost on:

    "It actually delays it with a little gap after the speech and then the
    tail fades in... it reminds me a bit of when old speech synths closed
    their wave sockets and made a little popping noise."

That is exactly what it was.  The host holds `STREAM_LOOKBEHIND` frames back
so that a slice landing behind the frontier can still overwrite audio nobody
has heard yet, and releases them when the response ends.  On 10.7 the response
ends 300 ms after the audio is finished, because Lion never stops its audio
graph and the quiet period is the only thing that ends an utterance.

At ordinary rates the margin is a small part of a long utterance and nothing
notices.  With rate boost the utterance is smaller than the margin's own
delay -- a letter at 640 wpm is 100 ms of audio -- so the player drains, sits
with nothing, and is started again for the last 23 ms.  Measured before the
fix, on the letter "O":

    lion  180 wpm    320 ms of audio, player dry for  32 ms
    lion  640 wpm    100 ms of audio, player dry for 247 ms
    lion 1200 wpm     57 ms of audio, player dry for 293 ms

Leopard never does it: `AUGraphStop` ends its response about 11 ms after the
audio, and the tail arrives while 77 ms is still buffered.  So this test is
Lion's, even though the code it covers is shared.

**What it asserts is the hole, not the fix.**  The margin, the settle period
and Lion's quiet window can all be re-tuned; what must stay true is that a
listener never hears the audio stop and start again inside one utterance.
"""
import os
import struct
import subprocess
import time

import pytest

REQ_STREAM = 0x54475234
RSP = 0x54475253
RATE = 22050.0

#: Slider 50 and slider 100 with rate boost -- see `_wpm` in pantheradriver.
#: 180 is there because the fault was present at ordinary rates too, just
#: quietly: 32 ms of silence in the middle of a spoken letter.
RATES = [180, 640, 1200]

#: What the player can lose without a listener hearing a break.  The floor in
#: every measurement is about 11 ms, which is not a hole at all -- it is the
#: wait for the *first* chunk, before any audio has been delivered to lose.
MAX_STARVE_MS = 25.0


def _chunks(host, tree, text, voice, wpm):
    """-> [(frames, arrival ms)] for one streamed utterance in a warm host."""
    mt = (tree + "/Speech/Synthesizers/MacinTalk.SpeechSynthesizer"
                 "/Contents/MacOS/MacinTalk")
    dic = tree + "/SpeechDictionary.framework/Versions/A/SpeechDictionary"
    p = subprocess.Popen([host, "--serve", mt, dic, tree + "/Speech/Voices"],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL)
    try:
        v = voice.encode()

        def request(body):
            p.stdin.write(struct.pack("<IiIIII", REQ_STREAM, wpm, 0, 0,
                                      len(v), len(body)) + v + body)
            p.stdin.flush()
            t0 = time.perf_counter()
            head = p.stdout.read(8)
            assert len(head) == 8, "the host closed the pipe"
            magic, _status = struct.unpack("<Ii", head)
            assert magic == RSP, hex(magic)
            out = []
            while True:
                n = struct.unpack("<I", p.stdout.read(4))[0]
                if n == 0:
                    return out
                got = b""
                while len(got) < n * 2:
                    c = p.stdout.read(n * 2 - len(got))
                    assert c, "the chunk was short"
                    got += c
                out.append((n, (time.perf_counter() - t0) * 1000.0))

        request(b"warming up")           # the first render pays for start-up
        return request(text.encode("utf-8"))
    finally:
        p.kill()


def _worstStarve(chunks):
    """-> the longest the player would have had nothing, in ms.

    Counted against audio *delivered*, not audio requested: a chunk arriving
    later than everything before it would have finished playing is a hole the
    listener hears, whatever the host thought it was doing.
    """
    played = 0.0
    worst = 0.0
    for n, at in chunks:
        worst = max(worst, at - played)
        played += n / RATE * 1000.0
    return worst


@pytest.mark.parametrize("wpm", RATES)
def test_a_letter_arrives_without_a_hole_in_it(engine_tree, wpm):
    import lionspeech
    if not os.path.isfile(lionspeech.HOST_EXE):
        pytest.skip("panthera_host.exe not built")
    chunks = _chunks(lionspeech.HOST_EXE, engine_tree, "O", "Alex", wpm)
    assert chunks, "nothing was streamed"
    worst = _worstStarve(chunks)
    total = sum(n for n, _ in chunks) / RATE * 1000.0
    assert worst <= MAX_STARVE_MS, (
        "at %d wpm the player ran dry for %.0f ms inside an utterance only "
        "%.0f ms long -- the speech stops and the tail starts again"
        % (wpm, worst, total))
