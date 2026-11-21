# -*- coding: utf-8 -*-
"""A pause between slices is not the end of an utterance (panthera-speech#26).

Serve mode waits for the engine's own end signal -- the graph stop on Tiger,
Leopard and Snow Leopard, the deferred stop armed on Lion -- with a quiet
period as the fallback.  That fallback used to count from before the first
slice and give up after 300 ms, so an engine slower than that between two
slices (a cold start, a loaded machine) came back with a fragment of its
utterance, and the rest of it spilled into the next.  Measured with the
pacer held at 350 ms a slice: "Hello there." answered 570 frames of 17,360,
and the next request queued behind what was left.

TIGER_PACE_FLOOR makes the gap deterministic, which is all this needs: the
utterance must come back whole however long the engine pauses, and the one
after it must start clean.
"""
import os
import struct
import subprocess

TEXT = "Hi."


def _paths(tree):
    return (os.path.join(tree, "Speech", "Synthesizers", "MacinTalk.SpeechSynthesizer",
                         "Contents", "MacOS", "MacinTalk"),
            os.path.join(tree, "SpeechDictionary.framework", "Versions", "A", "SpeechDictionary"),
            os.path.join(tree, "Speech", "Voices"))


def _exact(stream, n):
    data = b""
    while len(data) < n:
        chunk = stream.read(n - len(data))
        assert chunk, "the host closed the pipe"
        data += chunk
    return data


def _serve(tree, texts, **env):
    from synthDrivers import leopardspeech
    p = subprocess.Popen([leopardspeech.HOST_EXE, "--serve", *_paths(tree)],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, env=dict(os.environ, **env))
    out = []
    try:
        for text in texts:
            voice, body = b"Fred", text.encode("utf-8")
            p.stdin.write(struct.pack("<IiiIII", 0x54475234, 180, 0, 2, len(voice), len(body))
                          + voice + body)
            p.stdin.flush()
            magic, status = struct.unpack("<Ii", _exact(p.stdout, 8))
            assert status == 0
            pcm = b""
            while True:
                n = struct.unpack("<I", _exact(p.stdout, 4))[0]
                if not n:
                    break
                pcm += _exact(p.stdout, n * 2)
            out.append(pcm)
    finally:
        p.stdin.close()
        p.wait(20)
    return out


def test_a_slow_engine_still_returns_its_whole_utterance(engine_tree):
    reference = _serve(engine_tree, [TEXT, TEXT])
    slow = _serve(engine_tree, [TEXT, TEXT], TIGER_PACE_FLOOR="350")
    assert len(slow[0]) == len(reference[0]), \
        "a 350 ms pause between slices cut the utterance to %d of %d frames" % (
            len(slow[0]) // 2, len(reference[0]) // 2)
    assert slow[0] == reference[0]
    assert slow[1] == reference[1], "the next utterance did not start clean"
