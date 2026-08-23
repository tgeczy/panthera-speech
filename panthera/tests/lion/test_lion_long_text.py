# -*- coding: utf-8 -*-
"""A paragraph must not stop at 511 characters.

Reported by Amir, who found the boundary from the outside and gave the exact
paragraph to reproduce it with:

    "I've found an Alex-related text omission bug. It seems that if a paragraph
    contains more than 500 characters, Alex Premium 2.0 skips or omits the rest
    of the paragraph... the following paragraph, to me, is cut on the word
    date. Even the s in dates isn't pronounced."

He was right about the effect and about roughly where, and the cause was not
where anyone was looking.

**It was not `meow` and it was not Apple.** `cfobj` -- this host's stand-in for
a `CFStringRef` -- carries a fixed `char buf[512]`, because those objects began
life holding file paths, and `cf_make` truncated to 511 characters with
`strncpy`. Then Lion started speaking through them: 10.7 takes its text as a
CFStringRef where Leopard takes a buffer and a length, so on Lion every
utterance passes through that buffer and anything past 511 characters was cut,
mid-word, silently.

Two measurements said so before a line was changed:

* **Every Lion voice is cut, not just the concatenative ones.** Alex and Vicki
  are `meow`, Kathy and Fred are `mtk3`, Bruce is `gala` -- all five stop in
  the same place. A fault inside a voice cannot do that.
* **Leopard renders the same paragraph in full**, on all five, and Leopard is
  the generation that never touches `cf_make`.

Amir's "dates" begins at character 496. The audio stops growing at exactly 511.
"""
import pytest

#: Amir's paragraph, kept verbatim -- it is the report, and a paraphrase would
#: not be.
PARAGRAPH = (
    "It's impossible to say just how or when the number thirteen got its bad "
    "reputation. There are a number of theories, of course. Some say it comes "
    "from the Last Supper because Jesus was betrayed afterwards by one among "
    "the thirteen present. Others trace the source of the superstition back "
    "to ancient Hindu beliefs or Norse mythology. But if written references "
    "are any indication, the phenomenon isn't all that old (at least, not "
    "among English speakers). Known mention of fear of thirteen in print "
    "dates back only to the late 1800s. By circa 1911, however, it was "
    "prevalent enough to merit a name."
)

#: Where the truncation used to land, and the reason the number is exact: a
#: 512-byte buffer written with `strncpy(dst, src, CFPATH - 1)`.
OLD_LIMIT = 511


def _frames(driver, text, voice):
    return len(driver._render(text, driver._wpm(), voice)) // 2


@pytest.mark.parametrize("voice", ["Alex", "Fred"])
def test_a_paragraph_is_not_cut_at_the_old_limit(driver, voice):
    """Audio must keep growing well past 511 characters.

    Measured against a shorter render of the *same* text rather than against a
    constant, so the assertion does not depend on the rate, the voice or the
    machine -- only on the engine having been given the whole paragraph.
    """
    if voice not in {v[0] for v in driver._voices}:
        pytest.skip("%s is not in this tree" % voice)
    assert len(PARAGRAPH) > OLD_LIMIT, "the test paragraph is too short to bite"

    short = _frames(driver, PARAGRAPH[:400], voice)
    full = _frames(driver, PARAGRAPH, voice)
    assert short, "the short render produced nothing"

    #: What the whole paragraph should come to at the rate the first 400
    #: characters actually rendered at.  Generous, because speech is not
    #: uniform -- it only has to separate "all of it" from "the first 511".
    expected = short * len(PARAGRAPH) / 400.0
    assert full > expected * 0.85, (
        "%s rendered %d frames for %d characters, against about %d expected "
        "-- roughly the first %d characters, which is the CFPATH truncation"
        % (voice, full, len(PARAGRAPH), expected,
           full / (short / 400.0)))


def test_it_keeps_scaling_far_past_the_buffer(driver):
    """Not merely past 511: the fix must not have moved the cliff.

    A bigger fixed buffer would pass the test above and fail here, which is the
    point -- NVDA hands over whatever the user asked it to read, and a
    clipboard can be tens of kilobytes.
    """
    voice = driver._voices[0][0]
    unit = "trice "
    base = _frames(driver, (unit * 200)[:600], voice)
    assert base, "the baseline render produced nothing"
    for chars in (1200, 2400):
        got = _frames(driver, (unit * 800)[:chars], voice)
        expected = base * chars / 600.0
        assert got > expected * 0.85, (
            "%d characters gave %d frames, against about %d expected"
            % (chars, got, expected))
