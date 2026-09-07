# -*- coding: utf-8 -*-
"""Arrowing by letter must not restart the engine on every keystroke.

Reported by Timothy Wynn, who could hear it happening:

    "Interrupt the voice close to the start of the utterance, e.g., navigating
    rapidly by letter.  You will hear that the executable runs again."

`cancel()` used to retire the host process every time it found one rendering.
That was right when it was written: honouring the cancel saved nothing, so the
worker stayed asleep in a read until the whole utterance had been synthesised
anyway, and killing the process was the only way to free it.  The host answers
a cancel properly now -- 588 ms down to 96 on a paragraph of Alex -- so the
wait is over before a kill could have finished, and killing costs a start-up
and a voice reload of hundreds of megabytes.

Lion turned "sometimes" into "every time" for a reason that is not the
driver's: 10.7 never stops its audio graph, so every utterance sits out a
300 ms quiet window with the render still counted as in flight.  Any keystroke
inside that window retired the host.

Run against Leopard because both generations share `pantheradriver.py`, and
Leopard's engine is the faster of the two to set up.
"""
import time

import pytest


def _pid(driver):
    proc = getattr(driver, "_proc", None)
    return proc.pid if proc is not None else None


def _waitFor(pred, seconds=20.0):
    end = time.time() + seconds
    while time.time() < end and not pred():
        time.sleep(0.01)
    return pred()


def test_a_cancelled_render_ends_well_inside_the_grace_period(driver):
    """The measurement the backstop rests on, as an assertion.

    **This is the property, and the pid test below is the consequence.**  If a
    cancelled response ends promptly, waiting is cheaper than killing; if it
    ever stops doing so, the driver is back to holding the worker in a read
    and the grace period becomes a delay rather than a saving.  Either way
    this is the number that decides, so it is the one worth watching.
    """
    driver.speak(["a paragraph long enough that cancelling it part way "
                  "through leaves a great deal of it unspoken, which is the "
                  "whole situation the host has to handle promptly. " * 3])
    assert _waitFor(lambda: driver._rendering), "the render never started"
    driver.cancel()
    started = time.time()
    assert _waitFor(lambda: not driver._rendering, 10.0),         "the host never finished the response it was told to abandon"
    took = time.time() - started
    assert took < driver.ABANDON_GRACE, (
        "a cancelled response took %.2f s to end, against a grace period of "
        "%.2f s -- the backstop will now fire on ordinary interruptions"
        % (took, driver.ABANDON_GRACE))


def test_interrupting_does_not_restart_the_host(driver):
    """A burst of cancels must leave the same process running.

    The assertion is on the pid rather than on any timing, because the cost
    being avoided is a process start plus a voice load, and a machine under
    load can hide that in a wall-clock number while still paying it.

    Each interruption waits for the response to end before the next one, which
    is what a person arrowing through text does -- the keystroke that
    interrupts is also the one that asks for the next thing.  Firing eight
    cancels at a fixed 30 ms regardless made this fail on a loaded machine for
    a reason that was not the bug: the worker really was still rendering when
    the grace period expired, and the backstop was right to act.
    """
    driver.speak(["warming up the engine"])
    assert _waitFor(lambda: _pid(driver) is not None), "no host was started"
    started = _pid(driver)
    assert _waitFor(lambda: not driver._rendering), "the warm-up never ended"

    for _ in range(8):
        driver.speak(["The quick brown fox jumps over the lazy dog, and then "
                      "carries on for long enough to still be rendering."])
        assert _waitFor(lambda: driver._rendering), "a render never started"
        driver.cancel()
        assert _waitFor(lambda: not driver._rendering, 10.0),             "a cancelled response never ended"

    #: Long enough that any grace timer this started has had its chance.
    time.sleep(driver.ABANDON_GRACE + 0.5)
    assert _pid(driver) == started, (
        "the host was replaced during eight interruptions: %s then %s"
        % (started, _pid(driver)))


def test_speech_that_carries_on_after_a_cancel_keeps_its_host(driver):
    """The case the grace period got wrong, and the reason it counts renders.

    Arrow into a long article: one cancel, then speech that keeps going for
    longer than the grace period, split across several renders.  The timer that
    cancel armed is still running, and if it decides by polling `_rendering` it
    can miss every microsecond-wide gap between those renders, reach its
    deadline with the flag true and retire a host that is speaking exactly what
    the user asked for -- Timothy Wynn's glitch back again, in the middle of a
    say-all, at random.

    A render sequence number cannot be missed, which is why `_abandonHost`
    passes one.
    """
    driver.speak(["warming up the engine"])
    assert _waitFor(lambda: _pid(driver) is not None), "no host was started"
    started = _pid(driver)
    assert _waitFor(lambda: not driver._rendering), "the warm-up never ended"

    driver.speak(["something the listener is about to interrupt"])
    assert _waitFor(lambda: driver._rendering), "the render never started"
    driver.cancel()

    #: Now keep it busy for longer than the grace period, the way NVDA does
    #: when it hands over a paragraph: several sentences, back to back.
    deadline = time.time() + driver.ABANDON_GRACE + 1.0
    while time.time() < deadline:
        driver.speak(["The quick brown fox jumps over the lazy dog. ",
                      "Pack my box with five dozen liquor jugs. ",
                      "How vexingly quick daft zebras jump. "])
        time.sleep(0.05)

    assert _pid(driver) == started, (
        "the host was retired while it was speaking: %s then %s"
        % (started, _pid(driver)))


def test_the_handoff_answer_is_per_generation():
    """`HANDOFF_GRACE` is measured policy, not a shared constant.

    Lion and Snow Leopard hold a cancelled render's worker for hundreds of
    milliseconds after the audio is done with it, and a replacement host
    there is cheap -- so they answer with a number.  Leopard ends a
    cancelled render in tens of milliseconds and pays a 701 MB voice reload
    for a replacement, so the same number re-creates Timothy's glitch here.
    Each generation answers for itself, and the shared body answers never,
    so a generation added later cannot inherit a retirement policy nobody
    measured on it.
    """
    from synthDrivers import leopardspeech
    from synthDrivers import lionspeech
    from synthDrivers import snowleopardspeech
    base = leopardspeech.pantheradriver.PantheraDriver

    assert base.HANDOFF_GRACE is None
    assert leopardspeech.SynthDriver.HANDOFF_GRACE is None
    assert lionspeech.SynthDriver.HANDOFF_GRACE == 0.06
    assert snowleopardspeech.SynthDriver.HANDOFF_GRACE == 0.06


def test_a_host_that_stops_answering_is_still_retired(driver, monkeypatch):
    """The kill has to remain available, because a wedged host is real.

    For the whole of 0.95.0, five seconds of silence left Lion's engine unable
    to answer at all -- so the backstop is not hypothetical, and a grace period
    that quietly disabled it would have made that bug permanent instead of
    merely severe.
    """
    driver.speak(["warming up the engine"])
    assert _waitFor(lambda: _pid(driver) is not None)
    started = _pid(driver)
    #: Wait for the warm-up to *finish*.  Setting the flag while the worker is
    #: still in a render means the worker clears it a moment later and the
    #: grace period reads that as the host letting go by itself -- which is
    #: precisely the behaviour under test, so the test would pass for the
    #: wrong reason if it were watching a real render instead of a stuck one.
    end = time.time() + 20.0
    while time.time() < end and driver._rendering:
        time.sleep(0.02)
    assert not driver._rendering, "the warm-up never finished"

    #: Stand in for a host that never answers: the flag the grace period
    #: watches is exactly what a wedged render leaves set.
    monkeypatch.setattr(driver, "ABANDON_GRACE", 0.1)
    driver._rendering = True
    driver._abandonHost()

    end = time.time() + 10.0
    while time.time() < end and _pid(driver) == started:
        time.sleep(0.02)
    assert _pid(driver) != started, \
        "a host still marked as rendering was never retired"


@pytest.mark.parametrize("_run", range(2))
def test_speech_still_works_after_being_interrupted(driver, _run):
    """The point of all of it: the next thing asked for still gets spoken."""
    driver.speak(["a first sentence that will be cut off part way through"])
    time.sleep(0.05)
    driver.cancel()
    fed = driver._player.fed
    driver.speak(["and this one has to be heard"])
    end = time.time() + 20.0
    while time.time() < end and driver._player.fed <= fed:
        time.sleep(0.01)
    assert driver._player.fed > fed, "nothing was spoken after an interruption"
