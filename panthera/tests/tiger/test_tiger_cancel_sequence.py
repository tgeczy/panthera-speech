"""Cancellation abandons the whole sequence, including later command segments."""
import queue
import threading

import nvwave
import pytest

from synthDrivers.tigerspeech import SynthDriver


@pytest.mark.parametrize("boundary", [("break", 500), ("rate", 20),
                                      ("pitch", 10), ("volume", -10)])
def test_cancel_during_render_drops_the_rest_of_the_sequence(boundary):
    d = SynthDriver.__new__(SynthDriver)
    d._queue = queue.Queue()
    d._audioQueue = queue.Queue()
    d._stopped = False
    d._cancels = 0
    d._cancelEvent = None
    d._player = nvwave.WavePlayer()
    d._playerLock = threading.Lock()
    d._voiceId = "Fred"
    d._streaming = True
    d._pauseMode = "short"
    d._wpm = lambda adj=0: 180 + adj
    d._pitchOffset = lambda adj=0: adj
    rendered = []
    replacement = b"\x01\x00" * 100

    def render(text, wpm, voice, pitch=0, sink=None, volume=0):
        rendered.append(text)
        if text == "old beginning":
            d.cancel()
            d._queue.put([("text", "new request")])
            d._queue.put(None)
        else:
            sink(replacement)
        return b""

    d._render = render
    d._queue.put([("text", "old beginning"), ("index", 7), boundary,
                  ("text", "abandoned ending")])
    d._run()
    audio = []
    while not d._audioQueue.empty():
        kind, value, tag = d._audioQueue.get_nowait()
        if kind == "audio" and tag == d._cancels:
            audio.append(value)
    assert rendered == ["old beginning", "new request"]
    assert b"".join(audio) == replacement, "old silence or speech reached the new request"
