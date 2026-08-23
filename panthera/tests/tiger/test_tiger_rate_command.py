# -*- coding: utf-8 -*-
"""Tiger had the same gap, and had to be fixed with the others.

Amir reported it against Leopard and Lion, which share one driver body. Tiger
is the one generation that still has its own -- it predates the merge -- and it
was missing `RateCommand` and `VolumeCommand` in exactly the same way. Fixing
two of three would have left the next report to somebody using Fred.

The interesting half is the constraint: **Tiger's renders must stay
byte-identical**, so the default path may not change by one sample. It does
not, and that is arranged rather than hoped -- at the default the offset is
zero, `level` is the user's own volume, and the text handed to the engine is
character for character what it was.
"""
import speech.commands as commands


def _speak(driver, seq):
    captured = []
    real = driver._queue.put
    driver._queue.put = captured.append
    try:
        driver.speak(seq)
    finally:
        driver._queue.put = real
    return captured[0] if captured else []


def test_both_commands_are_declared(driver):
    assert commands.RateCommand in driver.supportedCommands
    assert commands.VolumeCommand in driver.supportedCommands


def test_they_survive_speak(driver):
    items = _speak(driver, [commands.RateCommand(offset=25), "typed",
                            commands.VolumeCommand(offset=-10), "quietly",
                            commands.RateCommand()])
    assert items == [("rate", 25), ("text", "typed"), ("volume", -10),
                     ("text", "quietly"), ("rate", 0)]


def test_the_offset_moves_the_rate(driver):
    driver.rate = 50
    assert driver._wpm(25) > driver._wpm()
    assert driver._wpm(100) == driver._wpm(50)      # clamped at the top


def test_the_default_render_is_untouched(driver):
    """The whole risk of this change, stated as a test.

    Tiger's byte-exactness is the guard the whole project leans on. A volume
    offset of zero has to leave the text -- and therefore the audio -- exactly
    as it was.
    """
    wpm = driver._wpm()
    plain = driver._render("Hello there!", wpm, driver._get_voice())
    explicit = driver._render("Hello there!", wpm, driver._get_voice(),
                              volume=0)
    assert plain == explicit and plain, \
        "asking for a zero volume offset changed the audio"
