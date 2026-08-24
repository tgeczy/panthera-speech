# -*- coding: utf-8 -*-
"""10.7 cannot honour `[[inpt …]]`, so it does not get to see it.

Interim, until panthera-speech#6 is solved. Two things are wrong with the
input-mode commands on 10.7 and neither is wrong on 10.5 or 10.6:

* `[[inpt TUNE]]` is accepted and then every `{D …; P …}` annotation is
  ignored -- measured, `m{D 500}` and a bare `m` both render 3358 frames,
  where Leopard gives 12096 and 1008.
* a malformed phoneme after `[[inpt PHON]]` faults inside Apple's own
  `SLLexerImpl::Error` and takes the host process with it.

What is *not* wrong on 10.7 is every other embedded command, which is why the
whole setting is still there and only this family is removed. Dropping the
checkbox was the first idea and would have cost `[[slnc]]` and `[[rate]]` --
both measured working -- for a fault that is not theirs.
"""
import pytest

import pantheradriver


def _strip(driver, text):
    """What reaches the engine, for a driver with commands switched on."""
    driver._acceptCommands = True
    if not driver._acceptCommands:
        return pantheradriver.COMMAND_RE.sub("", text)
    if driver.INPUT_MODES_WORK is False:
        return pantheradriver.INPUT_MODE_RE.sub("", text)
    return text


def test_lion_declares_that_its_input_modes_do_not_work():
    """The flag is the whole switch, and it is per generation."""
    import leopardspeech
    import lionspeech
    import snowleopardspeech
    import tigerspeech
    assert lionspeech.SynthDriver.INPUT_MODES_WORK is False
    for mod in (tigerspeech, leopardspeech, snowleopardspeech):
        assert mod.SynthDriver.INPUT_MODES_WORK is True, mod.__name__


@pytest.mark.parametrize("command", [
    "[[inpt TUNE]]", "[[inpt PHON]]", "[[inpt TEXT]]",
    "[[inpt tune]]", "[[ inpt  TUNE ]]",
])
def test_the_mode_switch_is_removed(command):
    """Case and spacing included -- the engine's parser tolerates both."""
    assert pantheradriver.INPUT_MODE_RE.sub("", "a %s b" % command) == "a  b"


@pytest.mark.parametrize("command", [
    "[[slnc 2000]]", "[[rate 90]]", "[[volm 0.5]]", "[[char LTRL]]",
    "[[pbas 60]]", "[[emph +]]",
])
def test_every_other_embedded_command_is_left_alone(command):
    """**The reason this is a pattern and not a checkbox.**

    All of these are measured working on 10.7. A user who turned embedded
    commands on almost certainly wanted the pause and the rate change, and
    they have nothing to do with the input-mode fault.
    """
    text = "a %s b" % command
    assert pantheradriver.INPUT_MODE_RE.sub("", text) == text


def test_a_tune_source_is_read_as_text_rather_than_silently_dropped(driver):
    """What is left when the switch goes is the notes, spoken as words.

    That sounds wrong, and it is meant to: it says out loud that the mode did
    not engage. Silence would have said nothing at all, and silence is what
    made this take two reports to notice.
    """
    driver._set_voice("Fred")
    wpm = driver._wpm()
    driver._acceptCommands = True
    got = driver._render("[[inpt TUNE]] m{D 500; P 50:0 50:100}", wpm, "Fred")
    bare = driver._render("m", wpm, "Fred")
    assert got and bare
    assert len(got) > len(bare) * 2, (
        "the annotation was dropped along with the mode switch: %d frames "
        "against %d for a bare note" % (len(got) // 2, len(bare) // 2))


def test_the_phoneme_mode_that_crashes_the_host_never_reaches_it(driver):
    """The crash is Apple's; not handing it the input is ours.

    `[[inpt PHON]] hxEHlOW` faults in `SLLexerImpl::Error` and kills the host
    outright. With the switch stripped it is read as text, which is ugly and
    alive.
    """
    driver._set_voice("Fred")
    wpm = driver._wpm()
    driver._acceptCommands = True
    got = driver._render("[[inpt PHON]] hxEHlOW [[inpt TEXT]]", wpm, "Fred")
    assert got, "the host did not survive the phoneme that used to crash it"
