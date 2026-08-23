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

The floor is deliberately far below what any generation actually achieves,
because it has to survive a slower machine and a loaded one without ever
crying wolf.  What it catches is a regression of an order of magnitude, which
is the only kind that has happened -- twice now, and the second time nobody
was looking for it either.
"""
import time

import pytest

#: Rendering must outrun speaking by at least this much.
#:
#: **Raised from 3 to 25 in 0.98.0, and the old number is worth keeping in
#: view.** It was 3 because Lion measured 8x where Tiger measured 77x and
#: Leopard 87x -- an order of magnitude slower, entirely from the fixed 300 ms
#: every utterance spent waiting to find out it had finished. 10.7 turned out
#: to signal the end after all, by arming a deferred audio-graph stop, and the
#: transform underneath Alex turned out to be two thirds of what was left. See
#: `tiger_host_serve.c` and `tiger_host_accel.c`.
#:
#: Now, best of three on one sentence: **Fred 101x, Alex 78x**. A floor of 25
#: keeps three times the headroom on the worst of those, so a slower or busier
#: machine cannot cry wolf -- while still catching a return to anything like
#: the old number, which is the regression that has actually happened twice.
MIN_REALTIME = 25.0

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
    print("\n  %s: %.2f s of audio in %.0f ms, %.1fx real time"
          % (voice, seconds, best * 1000, ratio))


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


#: What one keystroke may cost, end to end, on 10.7.
#:
#: Arrowing through a timeline is nothing but short utterances, and a *fixed*
#: cost is worst exactly there: the 300 ms quiet period this replaces was
#: several times the whole render of a single letter. Generous against the
#: 30-60 ms measured, and far below the 300+ that a return to the old
#: behaviour would produce.
MAX_SHORT_MS = 150.0


def test_a_single_letter_does_not_pay_a_fixed_wait(driver):
    """**10.7 says when it has finished, and this is what says we listen.**

    It arms `_MTBEAudioUnitDeferredStopAudioGraph` -- a five-second one-shot
    the host refuses, because letting it fire is what made Lion go deaf after
    five seconds of silence. Arming it is the engine's end-of-utterance
    signal, measured at within 25 ms of the last slice and exactly once per
    utterance, including for an unbroken 370-character token.

    Before that, an utterance ended when the audio had been quiet for 300 ms,
    and there was no other way to know. This test fails by roughly a factor of
    four if anything puts that back.
    """
    voice = driver._get_voice()
    wpm = driver._wpm()
    driver._render("o", wpm, voice)                 # warm
    best = min(_time_one(driver, "o", wpm, voice) for _ in range(3))
    print("\n  one letter on %s: %.0f ms" % (voice, best * 1000))
    assert best * 1000 < MAX_SHORT_MS, (
        "a single letter took %.0f ms to render on %s. A fixed wait has come "
        "back -- see the deferred graph stop in tiger_host_serve.c."
        % (best * 1000, voice))


def _time_one(driver, text, wpm, voice):
    started = time.time()
    pcm = driver._render(text, wpm, voice)
    elapsed = time.time() - started
    assert pcm, "rendered nothing"
    return elapsed
