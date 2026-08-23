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


def test_interrupting_does_not_restart_the_host(driver):
    """A burst of cancels must leave the same process running.

    The assertion is on the pid rather than on any timing, because the cost
    being avoided is a process start plus a voice load, and a machine under
    load can hide that in a wall-clock number while still paying it.
    """
    driver.speak(["warming up the engine"])
    end = time.time() + 20.0
    while time.time() < end and _pid(driver) is None:
        time.sleep(0.02)
    started = _pid(driver)
    assert started is not None, "no host was ever started"
    driver.cancel()
    time.sleep(driver.ABANDON_GRACE + 0.2)

    for _ in range(8):
        driver.speak(["The quick brown fox jumps over the lazy dog, and then "
                      "carries on for long enough to still be rendering."])
        time.sleep(0.03)                     # mid-render, as a keypress would
        driver.cancel()

    #: Long enough that any grace timer this started has had its chance.
    time.sleep(driver.ABANDON_GRACE + 0.5)
    assert _pid(driver) == started, (
        "the host was replaced during eight interruptions: %s then %s"
        % (started, _pid(driver)))


def test_a_host_that_stops_answering_is_still_retired(driver, monkeypatch):
    """The kill has to remain available, because a wedged host is real.

    For the whole of 0.95.0, five seconds of silence left Lion's engine unable
    to answer at all -- so the backstop is not hypothetical, and a grace period
    that quietly disabled it would have made that bug permanent instead of
    merely severe.
    """
    driver.speak(["warming up the engine"])
    end = time.time() + 20.0
    while time.time() < end and _pid(driver) is None:
        time.sleep(0.02)
    started = _pid(driver)
    assert started is not None
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
