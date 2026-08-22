"""Joining finished sentences, so the engine has a boundary to breathe at.

Alex breathes at a sentence boundary *inside* one utterance and nowhere else,
and reading continuously NVDA hands over one finished sentence at a time -- so
nothing ever breathes.  `_join` holds a sentence briefly and speaks it with the
next one.

These drive `_join` directly rather than through `speak()`, because the worker
thread is the other consumer of that queue and a test racing it would pass or
fail on timing.  `test_breath.py` is the audio-level half: that the joined
utterance really does breathe.
"""
import queue

import pytest

import leopardspeech
import pantheradriver
from leopardspeech import SynthDriver
from pantheradriver import _sentenceEnds


S1 = "The deadline is now only days away."
S2 = "Negotiators met again on Wednesday."
S3 = "Nobody expects a quick resolution."


@pytest.fixture
def d():
    """A driver with only the parts `_join` touches -- no host, no threads."""
    obj = SynthDriver.__new__(SynthDriver)
    obj._queue = queue.Queue()
    obj._audioQueue = queue.Queue()
    obj._joinSentences = True
    obj._spokeSinceCancel = True
    obj._stopped = False
    obj._epoch = 0
    return obj


def _texts(items):
    return [v for k, v in items if k == "text"]


def _drain(q):
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


# -- counting sentences ---------------------------------------------------

def test_sentence_ends_counts_finished_sentences():
    assert _sentenceEnds("") == 0
    assert _sentenceEnds("one long clause, with a comma") == 0
    assert _sentenceEnds(S1) == 1
    assert _sentenceEnds(S1 + " " + S2) == 2
    assert _sentenceEnds("Really? Yes! Truly.") == 3


def test_a_closing_quote_does_not_hide_the_full_stop():
    """The passage that started this had its quote *after* the stop."""
    assert _sentenceEnds('he said "we will not pay." Then he left.') == 2


def test_a_decimal_point_is_not_a_sentence():
    assert _sentenceEnds("It rose 3.5 percent") == 0


# -- what gets joined -----------------------------------------------------

def test_two_lines_become_one_utterance(d):
    """The whole point: one sentence waits for the next."""
    d._queue.put([("text", S2), ("index", 2)])
    out = d._join([("text", S1), ("index", 1)], d._epoch)
    assert _texts(out) == [S1, S2]
    assert _sentenceEnds(" ".join(_texts(out))) == 2


def test_it_stops_at_two_sentences(d):
    """Two is enough -- a third would only delay the first."""
    d._queue.put([("text", S2), ("index", 2)])
    d._queue.put([("text", S3), ("index", 3)])
    out = d._join([("text", S1), ("index", 1)], d._epoch)
    assert _texts(out) == [S1, S2]
    assert d._queue.qsize() == 1, "the third line must still be waiting"


def test_the_last_sentence_of_a_document_is_still_spoken(d, monkeypatch):
    """Nothing follows it, and NVDA never says so.

    A driver is not told that an utterance was the last: NVDA splits sequences
    at EndUtteranceCommand before they get here.  So the timeout is not a
    safety net, it is the only thing that speaks the final sentence.
    """
    monkeypatch.setattr(pantheradriver, "JOIN_WAIT", 0.05)
    out = d._join([("text", S1), ("index", 1)], d._epoch)
    assert _texts(out) == [S1]


def test_text_without_a_full_stop_is_bounded(d, monkeypatch):
    """A page with no punctuation must not accumulate until someone notices."""
    monkeypatch.setattr(pantheradriver, "JOIN_WAIT", 0.05)
    for i in range(40):
        d._queue.put([("text", "a clause with no full stop in it at all "),
                      ("index", i)])
    out = d._join([("text", "opening clause "), ("index", 99)], d._epoch)
    assert len("".join(_texts(out))) < pantheradriver.JOIN_MAX_CHARS + 100
    assert d._queue.qsize() > 0, "it should have stopped well short"


# -- what does not get joined --------------------------------------------

def test_navigation_speech_is_never_held(d):
    """No index means nothing more is coming, and latency is what matters.

    Arrowing through a list is the case this must not touch: holding those
    would be heard as the synthesizer lagging a line behind.
    """
    d._queue.put([("text", S2), ("index", 2)])
    out = d._join([("text", "Documents folder")], d._epoch)
    assert _texts(out) == ["Documents folder"]
    assert d._queue.qsize() == 1


def test_an_announcement_carrying_an_index_is_not_held(d, monkeypatch):
    """NVDA puts an index on more than say-all lines.

    A callback in a sequence reaches a driver as an IndexCommand too, so the
    index alone cannot mean "a document is being read".  What distinguishes a
    line of a document is that NVDA only flushes at a full stop -- so text
    arriving without one must never block, or an ordinary announcement is
    heard a third of a second late.
    """
    import time
    monkeypatch.setattr(pantheradriver, "JOIN_WAIT", 5.0)
    t = time.time()
    out = d._join([("text", "Search results list"), ("index", 7)], d._epoch)
    assert time.time() - t < 0.5
    assert _texts(out) == ["Search results list"]


def test_the_setting_turns_it_off(d):
    d._joinSentences = False
    d._queue.put([("text", S2), ("index", 2)])
    out = d._join([("text", S1), ("index", 1)], d._epoch)
    assert _texts(out) == [S1]


def test_a_break_command_is_not_joined_across(d):
    """A break divides the utterance where it stands.

    Joining across one would promise the engine a sentence boundary that is
    not there, and NVDA asked for that pause on purpose.
    """
    d._queue.put([("text", S2), ("index", 2)])
    out = d._join([("text", S1), ("break", 250), ("index", 1)], d._epoch)
    assert _texts(out) == [S1]
    assert d._queue.qsize() == 1


def test_cancelling_stops_the_join(d):
    """Held text belongs to the run that was cancelled."""
    epoch = d._epoch
    d._epoch += 1                       # what cancel() does
    d._queue.put([("text", S2), ("index", 2)])
    out = d._join([("text", S1), ("index", 1)], epoch)
    assert _texts(out) == [S1]


def test_the_first_utterance_after_a_cancel_never_waits(d, monkeypatch):
    """Starting to read must be as immediate as it was.

    With nothing queued yet this would otherwise sit for JOIN_WAIT before a
    word came out, which is exactly what a reader notices.
    """
    import time
    d._spokeSinceCancel = False
    monkeypatch.setattr(pantheradriver, "JOIN_WAIT", 5.0)
    t = time.time()
    d._join([("text", S1), ("index", 1)], d._epoch)
    assert time.time() - t < 0.5


# -- indexes --------------------------------------------------------------

def test_indexes_are_reported_before_the_text_is_spoken(d):
    """Or reading stops dead.

    NVDA asks for the next line from `lineReached`, which *is* this
    notification.  Hold the index with its sentence and the next sentence
    never arrives -- the join would wait for text that its own silence
    prevented.
    """
    d._queue.put([("text", S2), ("index", 2)])
    out = d._join([("text", S1), ("index", 1)], d._epoch)
    reported = [v for (kind, v, _tag) in _drain(d._audioQueue)
                if kind == "index"]
    assert reported == [1, 2]
    assert not [k for k, _ in out if k == "index"], \
        "an index reported here must not be reported again by _flush"
