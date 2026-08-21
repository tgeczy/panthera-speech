"""A single very long token must render audio, not one frame of silence.

Reported by Brandon (@serrebidev) as issue #4: **Alex returned one frame and a
status of 0 -- indistinguishable from success -- for an unbroken token of about
370 characters or more**, and was intermittently silent below that, one run in
five at 300.

It no longer reproduces. What makes this worth a test rather than a note is
that **nobody knows which change cured it**, and the obvious candidates are not
guilty: rebuilding the shared host with both morphology fixes disabled --
`MAX_LETTER_RUN` raised out of reach and `COPY_SANITY_LIMIT` at 4 GB -- still
renders every one of his cases correctly. So it was something else in the same
window, and an unattributed fix is exactly the kind that comes undone quietly.

`break_letter_runs` does change these inputs, and legitimately: a run of one
letter is what makes `SLPrefixMorph::AddAffix` overflow (tiger-speech#4). But
it only touches A-Z, so his digit case passes through untouched and renders
anyway -- which is the measurement that ruled it out as the cure here.

The assertion is deliberately weak on *how much* audio: the point is that it is
not one frame, and not the same one frame every time.
"""
import pytest


def _frames(driver, text):
    return len(driver._render(text, driver._wpm(), driver._get_voice())) // 2


def test_these_run_against_alex(driver):
    """**Brandon: "No other voice does it."**

    The tests below take whatever voice the driver defaults to, which is Alex
    today. If that ever changes they would still pass and would be testing
    nothing -- the same way a letter-run test written with Fred passed with the
    fix compiled out, because Fred never reaches the fault.
    """
    assert driver._get_voice() == "Alex", \
        "the default voice is %r; these cases are Alex-specific" \
        % driver._get_voice()


@pytest.mark.parametrize("n", [370, 384, 395])
def test_a_long_single_token_is_not_silent(driver, n):
    """His threshold, and either side of it. One frame is the failure."""
    got = _frames(driver, "b" * n)
    assert got > 1000, \
        "a token of %d characters rendered %d frames" % (n, got)


def test_a_long_digit_run_is_not_silent(driver):
    """`'7' * 60 + 'the'` -- his other shape, and the one that proves the
    letter-run fix is not what cures this: `is_letter` is A-Z only, so no run
    is broken here and the text reaches the engine exactly as sent."""
    assert _frames(driver, "7" * 60 + "the") > 1000


def test_it_is_not_intermittent(driver):
    """The half of the report that is easiest to lose.

    Below the threshold it was silent about one run in five, which is the
    signature of reading uninitialised or unterminated memory -- the same shape
    as the abbreviation bug in test_abbreviations.py. A test that renders once
    would have passed all through the broken period four times out of five.
    """
    got = [_frames(driver, "b" * 300) for _ in range(5)]
    assert len(set(got)) == 1, "five renders of one text disagreed: %r" % (got,)
    assert got[0] > 1000, "300 characters rendered %d frames" % got[0]
