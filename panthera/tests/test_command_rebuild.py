# -*- coding: utf-8 -*-
"""NVDA speaks the brackets before the synthesizer sees them; we put them back.

Adison: "it actually says the command, [pbas X]], it doesn't go through with
it."  At punctuation level "most" or "all", NVDA's speak() runs every string
through symbol processing before the synth gets it, and `[` and `]` are
level "most" -- so `[[pbas 60]]` arrives as spoken words and the engine
reads them out.  At the default level "some" it passes through, which is why
the driver-level pbas tests were green while a user's machine was not.
"""
from synthDrivers._panthera.text import rebuild_commands


def test_a_spoken_bracket_pair_becomes_a_command_again():
    # Exactly what NVDA produces: each replacement padded with spaces.
    text = " left bracket  left bracket pbas 60 right bracket  right bracket "
    assert rebuild_commands(text).strip() == "[[pbas 60]]"


def test_surrounding_text_survives():
    text = "hello  left bracket  left bracket slnc 500 right bracket  right bracket  there"
    assert rebuild_commands(text).split() == ["hello", "[[slnc", "500]]", "there"]


def test_case_does_not_matter():
    text = "Left Bracket left bracket inpt TUNE Right Bracket right bracket"
    assert rebuild_commands(text).strip() == "[[inpt TUNE]]"


def test_text_without_a_pair_is_untouched():
    for text in ("no brackets here", "left bracket alone", "[[already raw]]",
                 "left bracket right bracket"):
        assert rebuild_commands(text) == text


def test_other_locales_names_are_honoured():
    text = "corchete izquierdo corchete izquierdo pbas 40 corchete derecho corchete derecho"
    assert rebuild_commands(text, "corchete izquierdo",
                            "corchete derecho").strip() == "[[pbas 40]]"


def test_two_commands_in_one_string():
    text = ("left bracket left bracket rate 90 right bracket right bracket x "
            "left bracket left bracket pbas 50 right bracket right bracket")
    assert rebuild_commands(text).split() == ["[[rate", "90]]", "x", "[[pbas", "50]]"]
