# -*- coding: utf-8 -*-
"""The engine's sentence pause, restored between continuous-reading chunks.

Inside one utterance the engine composes a pause between sentences -- about
half a second at 180 wpm -- and a chunk ends with zero trailing frames, so
during say-all every chunk boundary slammed shut (panthera-speech#10).
The driver now appends the engine's own pause length, `SENTENCE_PAUSE_FACTOR
/ wpm`, after index-carrying utterances only: arrowing and typing carry no
index and gain no latency.

Run against Leopard because the driver is shared and Leopard renders
deterministically, so the appended silence is measurable to the byte.
"""
import time

import pantheradriver

#: Long enough to pass JOIN_MIN_CHARS and carrying a sentence end -- the two
#: marks of a document chunk rather than an announcement.
TEXT = ("The quick brown fox jumps over the lazy dog, "
        "and then trots away into the evening.")
#: An index-carrying announcement: what a list item reached by first letter
#: looks like.  Short, no full stop -- it must gain nothing.
ITEM = "Documents folder"


def _fedAfter(driver, item, seconds=30.0):
    """-> bytes fed to the player by `item`, once feeding has settled.

    Counted by wrapping `feed` itself: the fake player's `fed` counts calls,
    and a chunk count varies with pipe timing where a byte count cannot.
    """
    counted = []
    realFeed = driver._player.feed

    def feed(data, *a, **k):
        counted.append(len(data))
        return realFeed(data, *a, **k)

    driver._player.feed = feed
    try:
        driver._queue.put(item)
        end = time.time() + seconds
        last, still = 0, 0.0
        while time.time() < end:
            time.sleep(0.05)
            now = sum(counted)
            if now != last:
                last, still = now, 0.0
            else:
                still += 0.05
                if now and still >= 0.75:
                    break
        return sum(counted)
    finally:
        driver._player.feed = realFeed


def _settle(driver, seconds=20.0):
    """Wait until the player has been idle for a second.

    Feeding is paced to real time, so the warm-up utterance is still being
    fed long after `speak` returns -- and its tail counted into the first
    measurement is exactly the mistake this fixture exists to avoid.
    """
    end = time.time() + seconds
    last, still = driver._player.fed, 0.0
    while time.time() < end:
        time.sleep(0.1)
        now = driver._player.fed
        if now != last:
            last, still = now, 0.0
        else:
            still += 0.1
            if still >= 1.0:
                return


def _expectedPause(driver):
    scale = driver.PAUSE_SCALE.get(driver._pauseMode, 1.0)
    return len(pantheradriver._silence(
        pantheradriver.SENTENCE_PAUSE_FACTOR / driver._wpm() * scale))


def test_an_indexed_chunk_gains_the_scaled_sentence_pause(driver):
    driver.speak(["warming up the engine"])
    _settle(driver)
    plain = _fedAfter(driver, [("text", TEXT)])
    indexed = _fedAfter(driver, [("index", 1), ("text", TEXT)])
    assert plain and indexed
    expected = _expectedPause(driver)
    grown = indexed - plain
    assert expected and abs(grown - expected) < 4410, (
        "an indexed chunk grew by %d bytes where the scaled pause is %d -- "
        "the restored sentence pause is wrong or missing" %
        (grown, expected))


def test_the_gap_setting_scales_the_pause(driver):
    """One control governs all the gaps; "Long" is the engine exactly."""
    driver.speak(["warming up the engine"])
    _settle(driver)
    plain = _fedAfter(driver, [("text", TEXT)])
    driver._pauseMode = "long"
    indexed = _fedAfter(driver, [("index", 1), ("text", TEXT)])
    driver._pauseMode = "short"
    full = len(pantheradriver._silence(
        pantheradriver.SENTENCE_PAUSE_FACTOR / driver._wpm()))
    #: "Long" also adds its own announcement gap after the flush, so the
    #: expected growth is the engine pause plus PAUSE_MS["long"].
    full += len(pantheradriver._silence(driver.PAUSE_MS["long"]))
    grown = indexed - plain
    assert abs(grown - full) < 4410, (
        "at Long the chunk grew by %d bytes where the engine's own pause "
        "plus the long gap is %d" % (grown, full))


def test_an_indexed_announcement_gains_nothing(driver):
    """First-letter navigation carries an index too, and must stay crisp.

    Reported by Tomi within hours of 1.2.0: a list item reached by first
    letter grew the sentence pause, because an index alone was read as
    continuous reading.  The joiner learned this lesson first -- a short
    thing carrying an index is an announcement -- and now both halves
    know it.
    """
    driver.speak(["warming up the engine"])
    _settle(driver)
    plain = _fedAfter(driver, [("text", ITEM)])
    indexed = _fedAfter(driver, [("index", 1), ("text", ITEM)])
    assert plain and indexed
    assert abs(indexed - plain) < 4410, (
        "an announcement with an index grew by %d bytes -- first-letter "
        "navigation has its 0.4 s gap back" % (indexed - plain))


def test_an_unindexed_utterance_gains_nothing(driver):
    """Arrowing must stay exactly as fast as it was."""
    driver.speak(["warming up the engine"])
    _settle(driver)
    first = _fedAfter(driver, [("text", TEXT)])
    second = _fedAfter(driver, [("text", TEXT)])
    assert first and second
    assert abs(first - second) < 4410, (
        "two identical plain utterances differ by %d bytes -- something is "
        "padding non-continuous speech" % (first - second))
