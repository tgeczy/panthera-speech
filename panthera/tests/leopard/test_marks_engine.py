# -*- coding: utf-8 -*-
"""The same question against the real engine: with breathing off, an index
placed after some words and before a pause is reported when those words have
been heard, and with breathing on it is reported where it always was.

`test_marks.py` one level up proves the mechanism on a bare driver; this is
the pair that says the whole driver keeps the promise, streaming host and
all.  Skips without a Leopard tree, like everything in this folder.
"""
import time

import pytest

import synthDriverHandler
from synthDrivers._panthera import speech_pipeline
from synthDrivers._panthera.constants import OUT_RATE


def _warm(driver):
    before = synthDriverHandler.synthDoneSpeaking.count
    driver.speak(["warm"])
    end = time.perf_counter() + 20.0
    while time.perf_counter() < end:
        if synthDriverHandler.synthDoneSpeaking.count > before:
            break
        time.sleep(0.005)
    time.sleep(0.05)


def _speakAndWait(driver, seq, timeout=25.0):
    before = synthDriverHandler.synthDoneSpeaking.count
    driver.speak(seq)
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        if synthDriverHandler.synthDoneSpeaking.count > before:
            return
        time.sleep(0.005)
    raise AssertionError("the sequence never finished speaking")


class _Listener(object):
    """When the first chunk reached the device, how much audio the first
    render produced, and when each index was reported."""

    def __init__(self, driver, monkeypatch):
        self.firstFeed = None
        self.renders = []
        self.reported = []
        player = driver._player
        feed = player.feed

        def timedFeed(data, *a, **k):
            if self.firstFeed is None:
                self.firstFeed = time.perf_counter()
            return feed(data, *a, **k)
        monkeypatch.setattr(player, "feed", timedFeed)

        render = driver._render

        def spy(text, wpm, voice, pitch=0, sink=None, **kw):
            fed = []

            def counting(chunk):
                fed.append(len(chunk))
                return sink(chunk)
            result = render(text, wpm, voice, pitch,
                            sink=counting if sink else None, **kw)
            self.renders.append((text, sum(fed)))
            return result
        monkeypatch.setattr(driver, "_render", spy)

        original = speech_pipeline.synthIndexReached.notify

        def notify(**k):
            self.reported.append((time.perf_counter(), k.get("index")))
            original(**k)
        monkeypatch.setattr(speech_pipeline.synthIndexReached, "notify",
                            notify)


def _earcon():
    import speech.commands
    return ["first part of the sentence ", speech.commands.IndexCommand(7),
            speech.commands.BreakCommand(250), "and the second part"]


def test_with_breathing_off_the_index_waits_for_the_words_before_it(driver, monkeypatch):
    _warm(driver)
    driver._joinSentences = False
    listen = _Listener(driver, monkeypatch)
    _speakAndWait(driver, _earcon())
    assert [i for _t, i in listen.reported] == [7]
    text, nbytes = listen.renders[0]
    assert text.startswith("first part")
    first = nbytes / 2.0 / OUT_RATE
    at = listen.reported[0][0] - listen.firstFeed
    # The fake device starts its stream 120 ms after the first feed, which
    # is slack in the index's favour: it must not sound before the words.
    assert at >= first - 0.02, (
        "index reported %.0f ms after the first feed, but the first part "
        "lasts %.0f ms" % (at * 1000, first * 1000))


def test_with_breathing_on_the_index_is_reported_where_it_always_was(driver, monkeypatch):
    """Pinned: ahead of the audio, so nothing changes for the box checked."""
    _warm(driver)
    assert driver._joinSentences, "the default is on"
    listen = _Listener(driver, monkeypatch)
    _speakAndWait(driver, _earcon())
    assert [i for _t, i in listen.reported] == [7]
    at = listen.reported[0][0] - listen.firstFeed
    assert at <= 0.05, "index reported %.0f ms after the first feed" % (at * 1000)
