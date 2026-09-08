"""NVDA delegates number styles to the host without changing the PCM."""
import pytest

from synthDrivers._panthera import pantheranumbers
from synthDrivers._panthera import pantheraabbrev


@pytest.mark.parametrize("style", ["off", "fix", "words"])
@pytest.mark.parametrize("text", [
    "There are 1234567 people.",
    "Version 0.7.3 has 0012 entries.",
    "Dr. Kirk carried 1234567mm and 1234567KB.",
])
def test_native_number_style_matches_python_reference(driver, monkeypatch, style, text):
    reference = pantheranumbers.expand(text, style)
    driver._set_voice("Fred")
    driver._numberStyle = "off"
    expected = driver._render(reference, 180, "Fred")
    assert expected

    def no_python_rewrite(*args):
        raise AssertionError("number handling returned to the Python speech path")

    monkeypatch.setattr(pantheranumbers, "expand", no_python_rewrite)
    driver._numberStyle = style
    actual = driver._render(text, 180, "Fred")
    assert actual == expected


def test_spaced_commands_are_protected_or_removed(driver):
    driver._set_voice("Fred")
    driver._numberStyle = "words"
    driver._acceptCommands = True
    assert driver._modeAfter("[ [inpt TUNE] ] notes") == "TUNE"
    assert driver._modeAfter("[ [inpt TUNE] ] notes [ [inpt TEXT] ]") is None
    assert driver._render("[ [rate 200] ] Hello there.", 180, "Fred") == driver._render(
        "[[rate 200]] Hello there.", 180, "Fred")
    driver._acceptCommands = False
    assert driver._render("[ [rate 200] ] Hello there.", 180, "Fred") == driver._render(
        "Hello there.", 180, "Fred")


@pytest.mark.parametrize("style", ["fix", "words"])
def test_number_unit_order_with_expansion_disabled(driver, style):
    text = "Dr. Kirk carried 1234567mm and 1234567KB."
    # Preserve the released order: numbers before abbreviation despelling.
    reference = pantheraabbrev.spell(pantheraabbrev.disambiguate(
        pantheranumbers.expand(text, style), False))
    driver._set_voice("Fred")
    driver._expandAbbreviations = False
    driver._numberStyle = "off"
    expected = driver._render(reference, 180, "Fred")
    driver._numberStyle = style
    assert driver._render(text, 180, "Fred") == expected
