# -*- coding: utf-8 -*-
"""Handing the host over is for the first interrupt, not for every one.

A handoff is cheap once.  In a user's log, arrowing a Mastodon timeline
produced **twenty-six of them in twelve seconds** -- a process killed and
another spawned on every arrow key -- while he heard two- and three-second
stalls.  Two things were wrong and only the second one mattered:

* `_retire` promoted a spare on `poll() is None`, which asks whether a
  process has exited, not whether it has finished mapping the engine.  Alive
  is not ready.  Measured, that one is nearly always harmless: a spare is
  ready about twelve milliseconds after it starts, well inside the sixty the
  grace period already waits.  It is fixed here because it is wrong, not
  because it was the fault.
* Nothing limited how often a handoff could happen.  That was the fault.

Measured on this machine over fourteen rapid arrows, before and after:

    lion          3 retirements -> 1     median 61 -> 57 ms
    snow leopard 13 retirements -> 4     median 107 -> 169 ms

Snow Leopard pays about sixty milliseconds of median latency for nine fewer
process spawns, which is the right way round: these numbers come from a fake
wave player, so the tests *understate* what a retirement costs a real machine.
"""
import time
import pytest


def test_a_spare_that_is_merely_alive_is_not_ready(driver):
    """`poll()` is not a readiness test, and the fix must not pretend it is."""
    class _Alive(object):
        def poll(self):
            return None                 # running, and nothing more is claimed
    driver._standby = _Alive()
    assert driver._standbyIsReady() is False, (
        "a spare with no readiness flag was treated as ready")

    import threading
    spare = _Alive()
    spare.pantheraReady = threading.Event()
    driver._standby = spare
    assert driver._standbyIsReady() is False, "an unset flag read as ready"
    spare.pantheraReady.set()
    assert driver._standbyIsReady() is True, "a ready spare read as not ready"
    driver._standby = None


def test_a_dead_spare_is_never_ready(driver):
    class _Dead(object):
        def poll(self):
            return 1
    import threading
    spare = _Dead()
    spare.pantheraReady = threading.Event()
    spare.pantheraReady.set()           # it said ready, then it died
    driver._standby = spare
    assert driver._standbyIsReady() is False
    driver._standby = None


def test_the_cooldown_is_longer_than_a_rapid_arrow(driver):
    """The whole point is that consecutive arrows cannot each retire a host."""
    assert driver.RETIRE_COOLDOWN >= 0.5, (
        "a cooldown this short does not stop a burst")


def test_a_burst_of_interrupts_does_not_retire_a_host_each_time(driver):
    """The storm itself, at the speed a person actually arrows."""
    cls = type(driver)
    if cls.HANDOFF_GRACE is None:
        pytest.skip("this generation never hands off")
    seen = {"n": 0}
    real = cls._retire

    def counting(self, *a, **k):
        seen["n"] += 1
        return real(self, *a, **k)
    original, cls._retire = cls._retire, counting
    try:
        driver.speak(["warm"])
        time.sleep(1.2)
        arrows = 14
        #: Both of the posts that produced the log this test comes from: one
        #: long enough that its render is always still in flight when the next
        #: arrow lands, one ordinary.  A short text alone renders faster than
        #: a person can arrow, so the cancel never lands mid-render and
        #: nothing is exercised at all.
        long = ("Nicks World boosted The Witchy Bitches: Good morning from "
                "the camp for wayward dogs. It has been another hot sweaty "
                "morning full of the usual exercise and not enough coffee. "
                * 6)
        short = ("Bleeping Computer: The latest version of the Brave browser "
                 "introduces a feature called Email Aliases that allows users "
                 "to generate disposable email addresses. ")
        midRender = 0
        for i in range(arrows):
            if getattr(driver, "_rendering", False):
                midRender += 1
            driver.cancel()
            driver.speak([long if i % 2 == 0 else short])
            time.sleep(0.10)
        driver.cancel()
    finally:
        cls._retire = original
    assert midRender >= arrows // 3, (
        "only %d of %d cancels landed mid-render -- this machine renders "
        "faster than the test arrows, so nothing was exercised"
        % (midRender, arrows))
    assert seen["n"] <= arrows // 3, (
        "%d handoffs for %d arrows -- the burst is still retiring a host "
        "almost every time" % (seen["n"], arrows))
