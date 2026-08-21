# -*- coding: utf-8 -*-
"""The respelling that stops Alex saying "cologne" for "colon".

The bug is prosodic: phrase-final the word is fine, and with any word after it
the engine de-accents it until the stress is heard on the second syllable.
NVDA speaks every timestamp as "five colon eighteen colon forty five", so it is
not an edge case -- it is most of a day's punctuation.

These tests are about the *rewrite*, which is where it can go wrong silently.
That the respelling actually sounds right was settled by rendering and
transcribing (the table is in leopardstress.py); no test here can hear.
"""
import pytest

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "addon", "synthDrivers", "_leopardspeech"))

import leopardstress                                          # noqa: E402


def test_it_fixes_the_word_that_is_broken():
    assert leopardstress.fix("C colon backslash") == "C colen backslash"


def test_it_fixes_a_time_which_is_the_common_case():
    assert leopardstress.fix("five colon eighteen colon forty five") == \
        "five colen eighteen colen forty five"


def test_it_keeps_the_case_it_was_given():
    """NVDA capitalises at the start of an utterance, and shouts in some
    contexts; a respelling that lower-cased them would be audible as a change
    of emphasis in voices that key off capitals."""
    assert leopardstress.fix("Colon") == "Colen"
    assert leopardstress.fix("COLON") == "COLEN"
    assert leopardstress.fix("colon") == "colen"


def test_semicolon_is_left_alone():
    """The bug does not affect it, and a substring rule would have rewritten
    it -- which is the mistake this whole module is one word away from."""
    assert leopardstress.fix("semicolon") == "semicolon"
    assert leopardstress.fix("press semicolon here") == "press semicolon here"


@pytest.mark.parametrize("text", [
    "the colonel gave an order",     # contains "colon", is not it
    "colonial rule",
    "colons and semicolons",         # plural: a different word to the engine
])
def test_it_does_not_reach_into_other_words(text):
    assert leopardstress.fix(text) == text


def test_ordinary_text_is_returned_unchanged():
    """The overwhelmingly common case, and it must not be a new object graph
    or a stripped string -- it is handed straight to the engine."""
    for text in ("Hello there.", "", "1,234MB and 20ish", "[[rate 200]]"):
        assert leopardstress.fix(text) == text


def test_the_driver_exposes_it_as_a_setting_that_is_on():
    """On by default is a deliberate departure from leaving pronunciation to
    the user, so it is worth a test that says so out loud: if someone flips the
    default, this is the line that argues with them."""
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "addon", "synthDrivers"))
    import leopardspeech
    names = [s.id for s in leopardspeech.SynthDriver.supportedSettings]
    assert "fixStress" in names
    setting = [s for s in leopardspeech.SynthDriver.supportedSettings
               if s.id == "fixStress"][0]
    assert setting.defaultVal is True
