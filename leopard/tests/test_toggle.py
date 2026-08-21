"""Toggling an engine setting must survive being done while speech is running.

The settings reach the engine through its environment, which is read once at
startup, so changing one has to replace the host process.  How that replacement
is done is the whole question.
"""
import time

from test_driver import _warm, _waitForFeeds, _speakAndWait


def test_toggling_a_setting_mid_utterance_does_not_disable_streaming(driver):
    """Tomi, on the shipped 0.6.0: "I unchecked it, checked it on again and
    boom.  It stopped working after."

    The first fix closed the host's stdin from NVDA's own thread, which could
    land while the worker was halfway through a streamed response -- and a
    pipe that closes mid-stream is exactly how an engine too old to stream
    announces itself.  The driver therefore switched streaming off for the
    rest of the session and logged "reinstall the add-on".  Two clicks did it.

    Toggling while nothing is speaking never reproduced it, which is why the
    first version of this test passed against the bug.
    """
    _warm(driver)
    assert driver._streaming, "streaming was already off before the test"

    for _ in range(3):
        driver.speak(["The quick brown fox jumps over the lazy dog. " * 6])
        _waitForFeeds(driver, 1)
        driver._set_expandAbbreviations(False)     # mid-stream, on this thread
        time.sleep(0.2)
        driver._set_expandAbbreviations(True)
        driver.cancel()
        time.sleep(0.3)

    assert driver._streaming, \
        "toggling a setting was mistaken for an engine that cannot stream"

    _feeds, spoken = _speakAndWait(driver, ["still here"])
    assert spoken > 0, "the driver went silent after the setting was toggled"


def test_a_setting_still_reaches_the_engine_after_several_toggles(driver):
    """And the point of the restart has to survive the fix to it."""
    voice = driver._get_voice()
    text = "the file is 5MB in size"

    driver._set_expandAbbreviations(True)
    first = driver._render(text, driver._wpm(), voice)
    for _ in range(3):
        driver._set_expandAbbreviations(False)
        plain = driver._render(text, driver._wpm(), voice)
        driver._set_expandAbbreviations(True)
        again = driver._render(text, driver._wpm(), voice)

    assert first and plain and again
    assert first != plain, "the setting did nothing in the first place"
    assert again == first, "it did not come back after being toggled"
