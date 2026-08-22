"""A breath needs a sentence boundary inside one utterance.

Measured on Alex: N sentences handed over together give N-1 breaths, at the
boundaries and nowhere else.  A breath is 369-399 ms of turbulent noise --
rms 76-123 against a peak of ~680, around 3000 zero crossings a second -- which
is what tells it apart from the 125 ms clause pauses.

The point of testing it *here*, through the driver rather than through the host
protocol, is that the driver rewrites text before it sends it: it strips
embedded commands, substitutes typographic apostrophes and joins the pieces of
one utterance back together.  Any of that could eat the sentence boundary, and
then the missing breathing would be ours rather than a consequence of how much
text NVDA hands over.
"""
import array
import wave
import io

S1 = ("The US Chamber of Commerce had also warned Tuesday that higher tariffs "
      "would damage both economies, drive up costs for families, further "
      "disrupt critical supply chains, and risk the 13 million American jobs "
      "that depend on trade.")
S2 = ("Negotiators met again on Wednesday morning, but neither side would say "
      "whether a deal was close, and the deadline is now only days away.")
S3 = ("Economists warn the cost will land on households first, in grocery "
      "bills and in the price of a new car, long before any factory reopens.")

RATE = 22050
WIN = RATE // 100                      # 10 ms


#: What Alex measured at `volm 1.0`, which is the level every threshold below
#: was calibrated against.
#:
#: The driver now sends a per-voice `[[volm]]` on every utterance -- Alex is
#: the quietest speaking voice in the set and is turned up by 6 dB -- so a
#: breath that peaked near 680 now peaks near 1360 and sails straight past the
#: "is this stretch quiet" test at 900. **The thresholds are not wrong, the
#: scale moved**, so normalise rather than re-tune: a detector that has to be
#: recalibrated every time the volume changes is a detector that will be
#: silently wrong one day.
REFERENCE_PEAK = 14000


def _samples(rendered):
    """The driver's renders are wav bytes or raw frames, depending on caller.

    Normalised to REFERENCE_PEAK so everything below is scale-invariant.
    """
    a = array.array("h")
    if rendered[:4] == b"RIFF":
        w = wave.open(io.BytesIO(rendered), "rb")
        a.frombytes(w.readframes(w.getnframes()))
    else:
        a.frombytes(rendered[:len(rendered) // 2 * 2])
    if not len(a):
        return a
    peak = max(max(a), -min(a))
    if peak and abs(peak - REFERENCE_PEAK) > REFERENCE_PEAK // 20:
        k = REFERENCE_PEAK / float(peak)
        a = array.array("h", [int(v * k) for v in a])
    return a


def _breaths(a):
    """Quiet stretches that carry breath's signature rather than silence's."""
    runs, start = [], None
    for i in range(0, len(a) - WIN, WIN):
        chunk = a[i:i + WIN]
        if max(max(chunk), -min(chunk)) < 900:
            if start is None:
                start = i
        else:
            if start is not None and i - start >= RATE * 35 // 1000:
                runs.append((start, i))
            start = None

    found = []
    for lo, hi in runs:
        c = a[lo:hi]
        ms = (hi - lo) * 1000.0 / RATE
        rms = (sum(float(s) * s for s in c) / len(c)) ** 0.5
        zc = sum(1 for i in range(1, len(c)) if (c[i - 1] < 0) != (c[i] < 0))
        if ms >= 300 and zc * RATE / len(c) >= 2200 and rms > 20:
            found.append(round(lo / RATE, 2))
    return found


def _say(driver, text):
    return _samples(driver._render(text, driver._wpm(), driver._get_voice()))


def test_one_sentence_cannot_breathe(driver):
    """No boundary, no breath -- at any length.  This is the control."""
    assert _breaths(_say(driver, S1)) == []


def test_a_sentence_boundary_produces_a_breath(driver):
    """The driver must not swallow the boundary on its way to the engine.

    If this fails while the same text through `tools/render_once.py` still
    breathes, the fault is the driver's text handling, not how much NVDA sends.
    """
    assert len(_breaths(_say(driver, S1 + " " + S2))) == 1


def test_breaths_scale_with_sentences(driver):
    """N sentences in one utterance, N-1 breaths."""
    assert len(_breaths(_say(driver, " ".join([S1, S2, S3])))) == 2


def test_joining_two_lines_is_what_makes_them_breathe(driver):
    """The whole chain, end to end.

    Two lines as NVDA hands them over while reading continuously -- each a
    finished sentence, each carrying its own index -- go through `_join` and
    come out as one utterance, and *that* utterance breathes where neither of
    them could alone.  `test_join.py` covers the joining on its own; this is
    the part that says the joining was worth doing.
    """
    import queue
    driver._queue = queue.Queue()
    driver._audioQueue = queue.Queue()
    driver._joinSentences = True
    driver._spokeSinceCancel = True

    driver._queue.put([("text", S2), ("index", 2)])
    joined = driver._join([("text", S1), ("index", 1)], driver._epoch)
    text = " ".join(v for k, v in joined if k == "text")

    assert len(_breaths(_say(driver, text))) == 1
    for part in (S1, S2):
        assert _breaths(_say(driver, part)) == [], \
            "a single line cannot breathe -- that is the whole problem"
