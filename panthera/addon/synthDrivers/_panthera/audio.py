# -*- coding: utf-8 -*-
"""What the rendered audio is cut into, and the silence put back between it.

Split out of `pantheradriver.py` unchanged.  Two helpers and one measurement,
all of them about samples rather than about the engine.

Small on purpose.  Most of this driver's audio handling is the feeder thread's
and stays with it -- what is here is the part with no state to carry: how big
a piece of PCM may be before it stops being safe to hand over, and how long a
silence is.

`OUT_RATE` comes from `constants`, which is where every measured number in
this add-on lives.
"""
from .constants import OUT_RATE

def _sliceAudio(pcm, seconds):
    """Cut PCM into pieces of at most `seconds`, on frame boundaries.

    Never zero-length and never an odd number of bytes: half a frame handed
    to the player is a click, and a frame split across two feeds is a click
    in the middle of a word.
    """
    step = max(2, int(OUT_RATE * seconds) * 2)
    if len(pcm) <= step:
        yield pcm
        return
    for i in range(0, len(pcm), step):
        yield pcm[i:i + step]


def _silence(ms):
    """-> that many milliseconds of 16-bit mono silence."""
    if ms <= 0:
        return b""
    return b"\0" * (2 * int(OUT_RATE * ms / 1000.0))


#: The engine's own composed inter-sentence pause, which chunked reading
#: loses: every utterance ends the instant its last phoneme does, so the
#: pause that exists between sentences *inside* an utterance never exists
#: between utterances, and say-all slams sentence endings shut
#: (panthera-speech#10).
#:
#: Measured as whole-minus-parts on "A. B." against "A." and "B.":
#:
#:     80 wpm   1116 ms Fred   1062 Alex   1072 Leopard   1077 Snow Leopard
#:    180 wpm    491           471          476            471
#:    400 wpm    216           207          207            216
#:
#: One number describes all of it: the product of pause and rate is a
#: constant 86 wpm-seconds, for every voice and every generation measured.
#: So the restored pause is `this / wpm`, and nothing here is per-voice.
SENTENCE_PAUSE_FACTOR = 86000.0
