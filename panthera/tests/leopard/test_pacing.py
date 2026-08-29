# -*- coding: utf-8 -*-
"""How an utterance is cut up, on the way in and on the way out.

Both halves of the wait a user calls lag are decided here, and neither needs
an engine to check:

* what is handed to the player in one call, which decides how long `feed()`
  blocks and therefore whether `cancel()` can get the player lock; and
* what is handed to the engine in one request, which decides how much audio
  has to exist before any of it can be heard.

The rule both share is that nothing may be lost.  A splitter that drops a
comma is a splitter that drops a word, and no listening test would find it
reliably -- so every case here rejoins and compares.
"""
from synthDrivers import leopardspeech as ls
from synthDrivers._panthera import pantheradriver as pd


# -- what goes to the player -------------------------------------------------
def test_slicing_never_loses_or_splits_a_frame():
    """Half a frame handed to the player is a click; a lost one is a word."""
    for ms in (5, 22, 460, 3098, 17649):
        pcm = b"\0" * (int(pd.OUT_RATE * ms / 1000) * 2)
        parts = list(pd._sliceAudio(pcm, pd.FEED_SLICE))
        assert b"".join(parts) == pcm, "%d ms did not rejoin" % ms
        assert all(parts), "an empty slice was produced"
        assert all(len(p) % 2 == 0 for p in parts), "a frame was split in half"


def test_a_slice_fits_the_output_device():
    """The whole point: one `feed()` must not block, so one slice must fit.

    The device holds 250 to 330 ms, measured from two uninterrupted feeds in
    a user's log -- 939 ms of audio blocked 614, and 457 blocked 203.  Lead
    plus a slice has to stay under that, or `feed()` blocks holding the
    player lock and `cancel()` cannot have it.
    """
    assert pd.FEED_LEAD + pd.FEED_SLICE < 0.25
    biggest = max(len(p) for p in pd._sliceAudio(b"\0" * 400000, pd.FEED_SLICE))
    assert biggest / 2.0 / pd.OUT_RATE <= pd.FEED_SLICE + 0.001


# -- what goes to the engine -------------------------------------------------
#: Straight out of a user's log, with the driver's own spacing.
CAPTURED = [
    "Saved Messages, nvda dot zip, Sent 2026-08-17 at 11:08  not selected  10 of 61  ",
    "Richard Loyie, https: slash  slash www dot midea dot com slash ca slash "
    "small-appliances slash cookers slash 3l-low-sugar-rice-cooker dot mrc "
    "300dtcpw campaign id equals 120246700224620690 and ad id equals "
    "120249254388910690, Sent 2026-08-17 at 12:46  not selected  9 of 61  ",
    "accessible gram beta, Group, 3 new messages, Muted, "
    "مصطفى: مازال "
    "الناطق, Received yesterday at 4:12  "
    "not selected  7 of 61  ",
    "blank  ",
    "3 of 61  ",
]


def test_splitting_never_loses_a_character():
    for text in CAPTURED:
        assert "".join(pd._splitUtterance(text)) == text, repr(text[:40])


def test_short_announcements_are_left_whole():
    """A control type or a position is already fast, and a split is heard."""
    for text in ("blank  ", "3 of 61  ", "check box  not checked  "):
        assert pd._splitUtterance(text) == [text]


def test_the_first_piece_is_small_when_anything_allows_it():
    """It is the only piece the user waits for.

    275 characters of URL is 12.46 s of audio and was 789 ms of silence
    before a word of it; cut at the first comma it is under a tenth of that.
    """
    for text in CAPTURED[:3]:
        pieces = pd._splitUtterance(text)
        assert len(pieces) > 1, "a long announcement was not split"
        assert len(pieces[0]) < 40, "the first piece is too big: %r" % pieces[0]


def test_an_abbreviation_is_not_a_sentence_end():
    """The fault this refuses to cause: a full stop in the middle of a
    sentence, which is what joining NVDA's fragments exists to avoid."""
    text = ("A message from Dr. Smith and Mrs. Jones about the U.S. Army, "
            "e.g. this one, arrived at 3 p.m. yesterday and it was fine. "
            "This is the only real boundary in the whole of this string.")
    starts = list(pd._sentenceStarts(text))
    assert len(starts) == 1, "found %d boundaries, expected 1" % len(starts)
    assert text[starts[0]:].startswith("This is the only real")


def test_a_lower_case_word_after_a_full_stop_is_not_a_boundary():
    """From a real post: "in Leopard's. the engine names its own domains"."""
    text = ("There are a total of 283 in Leopard's. the engine names its own "
            "domains: com dot apple dot speech. That one is real.")
    starts = list(pd._sentenceStarts(text))
    assert all(not text[s:].startswith("the engine") for s in starts)
    assert any(text[s:].startswith("That one is real") for s in starts)


def test_a_number_is_never_a_phrase_boundary():
    """"1,000" and "18:05" have no space after the mark, and the space is
    what the pattern requires."""
    text = "It arrived at 18:05:45 UTC weighing 1,234 grams and 5.5 kilos"
    assert list(pd._phraseStarts(text)) == []


def test_a_sentence_end_is_preferred_to_a_phrase_boundary():
    """Cutting at a comma costs it its continuation rise.  Only worth it when
    no sentence end is anywhere near -- see SPLIT_SLACK."""
    text = ("Jayson Smith boosted, Tamas G, ok, for my nerd friends, two text "
            "files! The surface changes are small. This was what was mapped "
            "out as each tunable, and there is plenty more after it to make "
            "this long enough to be worth splitting at all.")
    first = pd._splitUtterance(text)[0]
    assert first.rstrip().endswith("two text files!"), repr(first)


def test_a_streaming_host_is_sent_the_text_whole(monkeypatch):
    """Splitting is for the fallback, not for the normal path.

    A sentence end is exactly where Alex breathes -- N sentences in one
    utterance give N-1 breaths, at the boundaries and nowhere else -- so
    cutting there removes them. Measured on a four-sentence paragraph, whole
    against split into three: 9 pauses of 70 ms or more against 5, and 1.36 s
    of silence against 1.08.

    And against a streaming host it buys nothing to pay for that, because the
    first chunk is already on its way while the rest renders: 33.4 ms split
    against 30.8 ms whole, same driver, same text. The 1323 ms that justified
    splitting was measured with streaming switched off by the bug fixed
    alongside it.

    So the rule is: stream and stay whole, fall back and split. This test is
    the one that argues with anyone who re-enables it.
    """
    called = []
    real = pd._splitUtterance
    monkeypatch.setattr(pd, "_splitUtterance",
                        lambda t: called.append(t) or real(t))

    long_text = ("The engine renders at about ninety times real time. "
                 "That is why the wait before the first sound was never the "
                 "engine's fault. It was ours, and the host accumulated the "
                 "whole utterance before handing over a single sample.")

    d = ls.SynthDriver()
    try:
        d._streaming = True
        d.speak([long_text])
        _settle_quiet(d)
        assert called == [], "a streaming host was sent split text"

        d._streaming = False
        d.speak([long_text])
        _settle_quiet(d)
        assert called, "the fallback did not split, so long text waits again"
    finally:
        d.terminate()


def _settle_quiet(driver, timeout=20.0):
    import time
    end = time.perf_counter() + timeout
    seen = driver._player.bytes
    while time.perf_counter() < end:
        time.sleep(0.05)
        if driver._player.bytes > seen:
            seen = driver._player.bytes
            end = time.perf_counter() + 1.0
    return seen
