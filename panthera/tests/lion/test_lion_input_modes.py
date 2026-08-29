# -*- coding: utf-8 -*-
"""10.7's input modes work now, and the stripping machinery stays.

For two shipped releases this file guarded the opposite: `[[inpt TUNE]]`
annotations were ignored (the melody died in two CF constructors the host
had left as thunks) and a malformed phoneme after `[[inpt PHON]]` killed the
host (the engine's error handler was a stack block that was never really
copied).  Both are fixed -- see panthera-speech#6 -- so Lion answers True
like its siblings, and these tests now hold the door open instead of shut.

`INPUT_MODE_RE` and the per-generation flag remain, tested below, because a
generation added later must be able to answer False and have the stripping
work on day one -- the mechanism was built and measured, and the day it is
needed again is the wrong day to rebuild it.
"""
import pytest

from synthDrivers._panthera import pantheradriver


def test_every_generation_answers_for_its_input_modes():
    """The flag is per generation, and every generation now says True.

    Lion said False for two releases; what flipped it is recorded in
    `lionspeech.py` and panthera-speech#6, and the flag stays a per-generation
    answer so the next generation has to earn its True the same way.
    """
    from synthDrivers import leopardspeech
    from synthDrivers import lionspeech
    from synthDrivers import snowleopardspeech
    from synthDrivers import tigerspeech
    for mod in (tigerspeech, leopardspeech, snowleopardspeech, lionspeech):
        assert mod.SynthDriver.INPUT_MODES_WORK is True, mod.__name__


@pytest.mark.parametrize("command", [
    "[[inpt TUNE]]", "[[inpt PHON]]", "[[inpt TEXT]]",
    "[[inpt tune]]", "[[ inpt  TUNE ]]",
])
def test_the_stripping_machinery_still_strips(command):
    """Nobody uses `INPUT_MODE_RE` today; the next False will.

    Case and spacing included -- the engine's parser tolerates both, so the
    stripper has to.
    """
    assert pantheradriver.INPUT_MODE_RE.sub("", "a %s b" % command) == "a  b"


@pytest.mark.parametrize("command", [
    "[[slnc 2000]]", "[[rate 90]]", "[[volm 0.5]]", "[[char LTRL]]",
    "[[pbas 60]]", "[[emph +]]",
])
def test_the_stripper_takes_only_the_input_modes(command):
    """**The reason it is a pattern and not a checkbox.**

    When Lion's stripping was live, these all worked and had to survive it;
    they still do, and the pattern still must not grow to touch them.
    """
    text = "a %s b" % command
    assert pantheradriver.INPUT_MODE_RE.sub("", text) == text


def test_a_tune_reaches_the_engine_through_the_driver(driver):
    """The whole point of the flip: no monkeypatch, just the driver.

    `test_lion_tune.py` proves the host can sing with the flag forced; this
    proves the driver actually lets the user's text through.  A held note is
    dramatically longer than a bare one, and ratios rather than frame counts
    because Lion's Fred is not reproducible.
    """
    driver._set_voice("Fred")
    wpm = driver._wpm()
    driver._acceptCommands = True
    noted = driver._render("[[inpt TUNE]] m{D 2000; P 220:0 220:100}", wpm,
                           "Fred")
    bare = driver._render("[[inpt TUNE]] m", wpm, "Fred")
    assert noted and bare
    assert len(noted) > len(bare) * 8, (
        "the annotation did not reach the engine: %d frames against %d"
        % (len(noted) // 2, len(bare) // 2))


def test_a_malformed_phoneme_no_longer_kills_the_host(driver):
    """The crash that kept the flag False, as a survival test.

    `[[inpt PHON]] hxEHlOW` used to die in the engine's error handler -- a
    stack block firing from a dead frame.  With the Blocks ABI done honestly
    the handler records the bad phoneme and the rest is spoken, which is
    Apple's own recovery doing its job.
    """
    driver._set_voice("Fred")
    wpm = driver._wpm()
    driver._acceptCommands = True
    got = driver._render("[[inpt PHON]] hxEHlOW [[inpt TEXT]]", wpm, "Fred")
    assert got, "the host did not survive the malformed phoneme"
