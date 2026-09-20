# -*- coding: utf-8 -*-
"""Where an index is reported, with breathing on and with it off.

An index is how NVDA hangs a callback on a point in the speech: the say-all
cursor, its own spelling-error sound and indentation tones, an add-on's
earcon.  With "Join text blocks during Say All to preserve breaths" checked,
every index is reported ahead of its audio, because the joiner needs NVDA's
next line before it renders this one and NVDA only sends it on the previous
line's index -- so the cursor leads, and so does the earcon.  That is the
trade the checkbox exists for, and unchecking it now buys the other side:
an index whose place in the audio is known is reported when playback
reaches it, the way NVDA's own eSpeak driver reports its markers.

The first half here drives the render loop and the feeder synchronously on
a bare driver, the way test_join.py drives the joiner, so nothing races.
The property under test for breathing *on* is that nothing moved.
"""
import queue
import threading
import time

import pytest

import nvwave
import synthDriverHandler
from synthDrivers.leopardspeech import SynthDriver
from synthDrivers._panthera import speech_pipeline
from synthDrivers._panthera.audio import _silence
from synthDrivers._panthera.constants import OUT_RATE


#: A finished sentence long enough for the joiner to call it a line of a
#: document, so a sequence carrying it and an index counts as continuous
#: reading and earns the restored pause.
S1 = "The deadline is now only days away, and nobody expects a resolution."


def _bare(join, player=None):
    """A driver with only the parts the render loop and the feeder touch:
    no host, no threads, and a render that produces silence sized to the
    text -- ten milliseconds a character -- so a test can tell which audio
    an index landed beside."""
    d = SynthDriver.__new__(SynthDriver)
    d._queue = queue.Queue()
    d._audioQueue = queue.Queue()
    d._joinSentences = join
    d._lastSpeechEpoch = 0
    d._stopped = False
    d._epoch = 0
    d._inputMode = None
    d._acceptCommands = False
    d._pauseMode = "short"
    d._streaming = True
    d._voiceId = "Alex"
    d._rate = 50
    d._pitch = 50
    d._rateBoost = False
    d._player = player if player is not None else nvwave.WavePlayer()
    d._playerLock = threading.Lock()
    d._playerIdle = True
    d._fedUntil = 0.0
    d._afterCancel = False
    d._cancelledAt = 0.0
    d._markLock = threading.Lock()
    d._marks = []
    d._markGen = 0
    d._fedBytes = 0
    d._playedBytes = 0
    d._playerTakesOnDone = d._probeOnDone()

    def render(text, wpm, voice, pitch=0, sink=None, volume=0):
        sink(_silence(10 * len(text)))
        return b""
    d._render = render
    return d


def _drain(q):
    out = []
    while True:
        try:
            out.append(q.get_nowait())
        except queue.Empty:
            return out


def _shape(items):
    """The queue as a list of (kind, value), audio reduced to its length in
    bytes so an assertion can say "the first part's audio" and mean it."""
    return [(k, len(v) if k == "audio" else v) for k, v, _t in items]


def _run(d, item):
    d._queue.put(item)
    d._queue.put(None)
    d._run()
    return _shape(_drain(d._audioQueue))


def _cancelDuringRender(join):
    """Finish an interrupted indexed post with its replacement already queued.

    The render owns the old epoch even though cancel() has advanced the driver.
    No timing sleeps: observe whether the replacement asks to wait for more text.
    """
    d = _bare(join)
    d._markerStreaming = not join
    d._cancelEvent = None
    d._rendering = False
    d._retiring = False
    waits, beforeReplacement, rendered = [], [], []

    class ObservedQueue(queue.Queue):
        def get(self, block=True, timeout=None):
            if timeout is not None:
                waits.append(timeout)
                # A missing next sentence normally spends JOIN_WAIT here.
                return super().get(block=False)
            return super().get(block=block)

    d._queue = ObservedQueue()
    old = S1 * 15  # Longer than JOIN_MAX_CHARS; no wait on this first post.

    def render(text, wpm, voice, pitch=0, sink=None, volume=0):
        rendered.append(text)
        if len(rendered) == 1:
            d.cancel()
            d._queue.put([("text", S1), ("index", 2)])
        else:
            beforeReplacement.extend(_drain(d._audioQueue))
            sink(_silence(10))
            d._queue.put(None)
        return b""

    d._render = render
    d._queue.put([("text", old), ("index", 1)])
    d._run()
    assert rendered == [old, S1]
    return waits, beforeReplacement


def test_cancelled_post_cannot_make_its_replacement_wait_for_say_all():
    waits, _ = _cancelDuringRender(True)
    assert not any(timeout > 0 for timeout in waits), (
        "The first post after cancel waited for another sentence")


@pytest.mark.parametrize("join", [True, False])
def test_cancelled_post_cannot_append_silence_to_the_new_utterance(join):
    _, beforeReplacement = _cancelDuringRender(join)
    assert not [item for item in beforeReplacement
                if item[0] == "audio" and item[2] == 1], (
        "The cancelled post appended its sentence pause with the new epoch")


#: The shape the EarCons and Speech Rules add-on sends, and the shape NVDA's
#: own spelling-error sound takes when a break follows it: a callback after
#: some words, then a pause for the sound, then the rest.
EARCON = [("text", "first part "), ("index", 7), ("break", 250),
          ("text", "second part")]


# -- the render loop: which side of the audio an index lands on ------------

def test_with_breathing_on_an_index_after_text_is_still_reported_ahead_of_it():
    """Pinned: nothing moves for anyone with the box checked."""
    out = _run(_bare(True), EARCON)
    kinds = [k for k, _ in out]
    assert kinds[0] == "index" and out[0][1] == 7, out
    assert "mark" not in kinds, "breathing on must never hold an index"


def test_with_breathing_off_an_index_before_a_break_follows_the_words_before_it():
    out = _run(_bare(False), EARCON)
    kinds = [k for k, _ in out]
    assert "index" not in kinds, "breathing off reports through marks: %r" % out
    first_audio = kinds.index("audio")
    mark = kinds.index("mark")
    assert first_audio < mark, "the earcon still sounds before its words: %r" % out
    # The first part's audio is 110 ms of silence; the break's is 250.  The
    # mark sits between them, after the words and before the pause.
    assert out[first_audio] == ("audio", len(_silence(110)))
    assert out[mark] == ("mark", 7)
    assert out[mark + 1] == ("audio", len(_silence(250)))


def test_with_breathing_off_an_index_with_text_on_both_sides_stays_at_the_head():
    """Its position in the audio is not known without the engine's help --
    the `[[sync]]` callback the host maps but does not install -- so it is
    reported where it always was.  Named here so the day the host can say
    where it got to, this is the test to change."""
    out = _run(_bare(False), [("text", "a "), ("index", 7), ("text", "b")])
    assert out[0] == ("mark", 7), out
    assert out[1][0] == "audio"


def test_with_breathing_off_the_end_index_precedes_the_restored_pause():
    """NVDA pushes the next utterance when the last index of this one is
    reached, so the mark goes before the pause appended for continuous
    reading: the next chunk renders under the pause rather than after it."""
    out = _run(_bare(False), [("index", 1), ("text", S1), ("index", 2)])
    assert out[0] == ("mark", 1), out
    assert out[1][0] == "audio" and out[1][1] == len(_silence(10 * len(S1)))
    assert out[2] == ("mark", 2)
    assert out[3][0] == "audio", "the restored pause should follow the mark"
    assert out[4] == ("done", None)


def test_with_breathing_on_the_end_index_is_reported_before_any_audio():
    """Pinned: the joiner reports every index up front."""
    out = _run(_bare(True), [("index", 1), ("text", S1), ("index", 2)])
    assert out[0] == ("index", 1) and out[1] == ("index", 2), out
    assert out[2][0] == "audio"


def test_with_breathing_off_an_index_is_never_lost_when_the_run_is_cancelled():
    """Whatever the epoch did, NVDA is told about every index it sent.

    The cancel lands *during* the render of the first part -- the real
    case, since `_run` reads the epoch after it dequeues -- so the audio is
    dropped and the rest of the sequence is abandoned, and the index that
    was trailing that part still goes out."""
    d = _bare(False)
    render = d._render

    def cancelledMidRender(text, wpm, voice, pitch=0, sink=None, volume=0):
        d._epoch += 1                   # cancel() arrived while rendering
        return render(text, wpm, voice, pitch, sink=sink, volume=volume)
    d._render = cancelledMidRender
    d._queue.put(EARCON)
    d._queue.put(None)
    d._run()
    raw = _drain(d._audioQueue)
    reported = [v for k, v, _t in raw if k in ("mark", "index")]
    assert reported == [7]
    # Neither the rendered words nor the abandoned break may delay what
    # follows. Silence relabelled with the new epoch was audible as lag too.
    assert not [item for item in raw if item[0] == "audio"]


# -- the feeder: when a mark is reported --------------------------------------

class _Reports(object):
    """Timestamps of every index reported, in order."""

    def __init__(self, monkeypatch):
        self.seen = []
        #: The bound method, taken before the patch: the module attribute
        #: and NVDA's are one object, so calling through it would recurse.
        original = speech_pipeline.synthIndexReached.notify

        def notify(**k):
            self.seen.append((time.perf_counter(), k.get("index")))
            original(**k)
        monkeypatch.setattr(speech_pipeline.synthIndexReached, "notify",
                            notify)


def _feed(d, items):
    for item in items:
        d._audioQueue.put(item)
    d._audioQueue.put(None)
    t0 = time.perf_counter()
    d._feed()
    return t0


#: Four tenths of a second of audio: long enough that being fed and being
#: heard are visibly different moments, with FEED_LEAD keeping the feeder
#: a fraction of a second ahead of the fake device.
AUDIO = _silence(400)


def test_with_breathing_off_a_mark_is_reported_when_its_audio_has_played(monkeypatch):
    r = _Reports(monkeypatch)
    d = _bare(False)
    t0 = _feed(d, [("audio", AUDIO, 0), ("mark", 5, None),
                   ("done", None, None)])
    assert [i for _t, i in r.seen] == [5]
    at = r.seen[0][0] - t0
    # The fake device starts a stream 120 ms after its first feed and the
    # audio lasts 400 ms, so the mark is due at 520 ms at the earliest.
    assert at >= 0.50, "reported %.0f ms in, before the audio ended" % (at * 1000)


def test_with_breathing_on_an_index_is_reported_as_soon_as_it_is_dequeued(monkeypatch):
    """Pinned: with the box checked, nothing waits for the device."""
    r = _Reports(monkeypatch)
    d = _bare(True)
    t0 = _feed(d, [("audio", AUDIO, 0), ("index", 5, None),
                   ("done", None, None)])
    assert [i for _t, i in r.seen] == [5]
    at = r.seen[0][0] - t0
    assert at < 0.45, "reported %.0f ms in, held for the audio" % (at * 1000)


def test_a_mark_with_nothing_playing_is_reported_at_once(monkeypatch):
    r = _Reports(monkeypatch)
    d = _bare(False)
    t0 = _feed(d, [("mark", 9, None), ("done", None, None)])
    assert [i for _t, i in r.seen] == [9]
    assert r.seen[0][0] - t0 < 0.05


class _PlayerWithoutCallbacks(nvwave.WavePlayer):
    """NVDA's player as it was before it could say when a chunk had played."""

    def feed(self, data):
        nvwave.WavePlayer.feed(self, data)


def test_a_mark_never_waits_on_a_player_that_cannot_call_back(monkeypatch):
    """The manifest admits one NVDA older than the callback.  There a mark
    is reported the moment it is dequeued -- what every index did before --
    and never lost."""
    r = _Reports(monkeypatch)
    d = _bare(False, player=_PlayerWithoutCallbacks())
    assert not d._playerTakesOnDone
    t0 = _feed(d, [("audio", AUDIO, 0), ("mark", 5, None),
                   ("done", None, None)])
    assert [i for _t, i in r.seen] == [5]
    assert r.seen[0][0] - t0 < 0.45


def test_every_mark_is_reported_before_the_utterance_is_declared_done(monkeypatch):
    r = _Reports(monkeypatch)
    done = []
    original = speech_pipeline.synthDoneSpeaking.notify

    def notify(**k):
        done.append((time.perf_counter(), [i for _t, i in r.seen]))
        original(**k)
    monkeypatch.setattr(speech_pipeline.synthDoneSpeaking, "notify", notify)
    d = _bare(False)
    _feed(d, [("audio", AUDIO, 0), ("mark", 1, None), ("audio", AUDIO, 0),
              ("mark", 2, None), ("done", None, None)])
    assert done and done[0][1] == [1, 2], \
        "done was declared with %r reported" % (done[0][1],)


def test_marks_are_reported_in_order_and_each_after_its_own_audio(monkeypatch):
    r = _Reports(monkeypatch)
    d = _bare(False)
    t0 = _feed(d, [("audio", AUDIO, 0), ("mark", 1, None), ("audio", AUDIO, 0),
                   ("mark", 2, None), ("done", None, None)])
    assert [i for _t, i in r.seen] == [1, 2]
    first, second = (t - t0 for t, _i in r.seen)
    assert first >= 0.50 and second >= 0.90, (first, second)
    # 400 ms of audio apart, less the fake device's granularity: it fires a
    # due callback from the next feed() or idle() tick, not from a thread of
    # its own the way the real one does.
    assert second - first >= 0.30, "the second mark did not wait for its audio"


def test_a_cancel_drops_held_marks_and_retires_their_callbacks(monkeypatch):
    """After cancel() the device holds nothing of ours, so a callback from
    the audio it threw away must not count against what comes next."""
    r = _Reports(monkeypatch)
    d = _bare(False)
    # Hand-feed one chunk so a callback is outstanding, then hold a mark.
    with d._playerLock:
        d._feedPiece(AUDIO)
    d._markReached(4)
    assert d._marks and not r.seen
    old = d._markGen
    d._resetMarks()                     # what cancel() does
    assert not d._marks and d._fedBytes == 0 and d._playedBytes == 0
    d._played(old, len(AUDIO))          # the retired callback arriving late
    assert d._playedBytes == 0, "a stale callback moved the played count"
    assert not r.seen, "a cancelled mark was reported"


def test_the_drain_reports_whatever_a_silent_device_left_behind(monkeypatch):
    """A player that never calls back still loses no index."""
    r = _Reports(monkeypatch)
    d = _bare(False)
    d._fedBytes = len(AUDIO)
    d._markReached(6)
    assert d._marks == [(len(AUDIO), 6)]
    d._marksDrained()
    assert [i for _t, i in r.seen] == [6]
    assert d._fedBytes == 0 and d._playedBytes == 0 and not d._marks
