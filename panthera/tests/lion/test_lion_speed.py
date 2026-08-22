# -*- coding: utf-8 -*-
"""The engine has to render much faster than it speaks.

**This file exists because nothing else in the suite could tell that Lion ran
at nine tenths of real time.**  Every voice spoke, every render was clean, the
audio was byte-comparable with Leopard's -- and a three second announcement
took three and a half seconds to produce.  The whole host is built on the
engine's clock running fast (see `g_speed` in `tiger_host_shims.c`), and 10.7
moved the worker's clock from `UpTime`, which is scaled, to `gettimeofday`,
which was not.  The engine then polled a clock that never ran fast, twelve and
a half million times for one sentence.

It surfaced as something else entirely: the serve loop allows an utterance nine
seconds of wall clock, and at 0.9x the singing voices reach that inside one
ordinary sentence, so seventeen voices in a row rendered nothing.  A speed
floor is the test that names the cause rather than the symptom.

The floor is deliberately far below what any generation actually achieves --
77x to 88x measured on this machine -- because it has to survive a slower
machine and a loaded one without ever crying wolf.  What it catches is a
regression of two orders of magnitude, which is the only kind that has
happened.
"""
import time

import pytest

#: Rendering must outrun speaking by at least this much.
#:
#: Real numbers on the machine this was written on, best of three, one
#: sentence of Fred: Tiger 77x, Leopard 87x, **Lion 8x**. Lion is an order
#: lower for a reason that is not the engine and not this bug -- 10.7 never
#: calls `AUGraphStop`, measured 0 times in 96, so every utterance sits out
#: the serve loop's quiet period after its audio is already complete. That is
#: a fixed 300 ms, and `tiger_host_serve.c` says at length why it is still 300
#: rather than the 150 that was tried. A fixed cost hurts a short utterance
#: proportionally more, which is why the floor is set where a *long* sentence
#: clears it easily.
MIN_REALTIME = 3.0

TEXT = ("The US Chamber of Commerce warned Tuesday that higher tariffs would "
        "damage both economies and drive up costs for families.")


@pytest.mark.parametrize("voice", ["Fred", "Alex"])
def test_rendering_outruns_speaking(driver, voice):
    available = {v[0] for v in driver._voices}
    if voice not in available:
        pytest.skip("%s is not in this tree" % voice)
    driver._set_voice(voice)
    wpm = driver._wpm()
    driver._render(TEXT, wpm, voice)              # warm: first render pays setup
    best = None
    for _ in range(3):
        started = time.time()
        pcm = driver._render(TEXT, wpm, voice)
        elapsed = time.time() - started
        assert pcm, "%s rendered nothing" % voice
        if best is None or elapsed < best:
            best = elapsed
    seconds = len(pcm) / 2.0 / 22050.0
    ratio = seconds / best
    assert ratio >= MIN_REALTIME, (
        "%s renders %.2f s of audio in %.2f s, only %.1fx real time. The "
        "engine's clock is not running fast -- check that every clock it reads "
        "is scaled by g_speed, not just UpTime." % (voice, seconds, best, ratio))


def test_a_long_singing_utterance_survives_the_serve_loop(driver):
    """The far end of the same fault, and the way it was noticed.

    The singing voices render several times more audio per character than the
    rest, so they are the first to cross the nine seconds of wall clock the
    serve loop allows an utterance. Crossing it returns a truncated render and
    leaves the channel mid-speech, answering -231 to everything after -- so
    the failure is not one bad utterance, it is every utterance after it.
    """
    available = {v[0] for v in driver._voices}
    if "BadNews" not in available:
        pytest.skip("BadNews is not in this tree")
    driver._set_voice("BadNews")
    wpm = driver._wpm()
    pcm = driver._render(TEXT, wpm, "BadNews")
    seconds = len(pcm or b"") / 2.0 / 22050.0
    assert seconds > 14.0, (
        "a singing voice produced only %.1f s for that sentence, which is "
        "where the nine second cap truncates it" % seconds)

    # And the channel still works afterwards, which is the part that made
    # seventeen voices go quiet in a row.
    driver._set_voice("Fred")
    after = driver._render("Testing.", wpm, "Fred")
    assert after, "the channel never spoke again after a long utterance"
