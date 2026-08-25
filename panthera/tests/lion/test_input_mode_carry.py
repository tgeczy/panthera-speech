# -*- coding: utf-8 -*-
"""An unclosed input mode survives the utterance boundary.

panthera-speech#9: say-all hands a tune file over in pieces, only the first
piece carries its `[[inpt TUNE]]`, and the engine starts every utterance in
text mode -- so verse one sang for 1.97 seconds and every verse after it was
about 110 seconds of spoken annotations.  The driver carries the mode now:
a switch left open covers the utterances that follow, `[[inpt TEXT]]` closes
it, and a cancel resets it because the next thing spoken after an interrupt
is the user doing something else, not verse five.

The thresholds are gross on purpose.  A sung note is well under a second;
the same line spelled out as text is several.  Nothing here depends on Lion
Fred being reproducible, which he is not.
"""

NOTE_OPEN = "[[inpt TUNE]] m{D 400; P 220:0 220:100}"
NOTE_BARE = "m{D 400; P 165:0 165:100}"

SUNG_MAX_FRAMES = 22050          # a 0.4 s note with tails, generously
TEXT_MIN_FRAMES = 44100          # the same line spelled out runs seconds


def _prep(driver):
    driver._set_voice("Fred")
    driver._acceptCommands = True
    return driver._wpm()


def test_an_unclosed_tune_carries_to_the_next_utterance(driver):
    wpm = _prep(driver)
    first = driver._render(NOTE_OPEN, wpm, "Fred")
    second = driver._render(NOTE_BARE, wpm, "Fred")
    assert first and second
    assert len(second) // 2 < SUNG_MAX_FRAMES, (
        "the second piece was spelled as text (%d frames): the mode did not "
        "carry" % (len(second) // 2))


def test_inpt_text_closes_the_carry(driver):
    wpm = _prep(driver)
    driver._render(NOTE_OPEN, wpm, "Fred")
    driver._render(NOTE_BARE + " [[inpt TEXT]]", wpm, "Fred")
    after = driver._render(NOTE_BARE, wpm, "Fred")
    assert len(after) // 2 > TEXT_MIN_FRAMES, (
        "the annotation was still sung (%d frames) after [[inpt TEXT]] "
        "closed the mode" % (len(after) // 2))


def test_cancel_resets_the_carried_mode(driver):
    wpm = _prep(driver)
    driver._render(NOTE_OPEN, wpm, "Fred")
    driver.cancel()
    after = driver._render(NOTE_BARE, wpm, "Fred")
    assert len(after) // 2 > TEXT_MIN_FRAMES, (
        "a cancelled run's tune mode leaked into the next utterance "
        "(%d frames)" % (len(after) // 2))


def test_no_carry_with_commands_off(driver):
    wpm = _prep(driver)
    driver._acceptCommands = False
    driver._render(NOTE_OPEN, wpm, "Fred")
    after = driver._render(NOTE_BARE, wpm, "Fred")
    assert len(after) // 2 > TEXT_MIN_FRAMES, (
        "with commands off nothing may sing, and nothing may carry "
        "(%d frames)" % (len(after) // 2))
