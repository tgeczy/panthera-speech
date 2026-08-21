"""Numbers the engine reads wrongly, rewritten before it sees them.

Measured on Alex: six digits are read as a number, seven are spelled out one
digit at a time, and a version like "v0.7.3" loses its leading zero -- "v point
seven point three".
"""
import pytest

from _leopardspeech import leopardnumbers as numbers


# -- integers to words ----------------------------------------------------

@pytest.mark.parametrize("n,said", [
    (0, "zero"), (7, "seven"), (13, "thirteen"), (20, "twenty"),
    (21, "twenty one"), (100, "one hundred"), (101, "one hundred one"),
    (999, "nine hundred ninety nine"),
    (1000, "one thousand"),
    (1234567, "one million two hundred thirty four thousand five hundred "
              "sixty seven"),
    (1000000, "one million"),
    (-42, "minus forty two"),
])
def test_to_words(n, said):
    assert numbers.to_words(n) == said


def test_a_number_too_large_to_say_is_refused():
    """Better left as digits than given a name nobody agrees on."""
    assert numbers.to_words(10 ** 18) is None


# -- the reported faults --------------------------------------------------

def test_seven_digits_get_grouped():
    """Seven is where the engine starts spelling.

    Grouping rather than spelling out in words, because "1,234,567" is already
    read correctly -- the engine keeps its own number style and we change as
    little as possible.
    """
    assert numbers.expand("3222233") == "3,222,233"
    assert numbers.expand("32322333") == "32,322,333"
    assert numbers.expand("1234567") == "1,234,567"


def test_six_digits_are_left_alone():
    """The engine reads these correctly, so nothing should touch them."""
    for n in ("1234", "12345", "123456"):
        assert numbers.expand(n) == n


def test_a_version_keeps_its_zero():
    """"v0.7.3" was heard as "v point seven point three"."""
    assert numbers.expand("0.7.3") == "zero point seven point three"
    assert numbers.expand("v0.7.3") == "v0.7.3", \
        "a number glued to a letter belongs to the dictionary, not here"
    assert numbers.expand("version 0.7.3") == "version zero point seven point three"


def test_a_leading_zero_decimal_is_written_out():
    assert numbers.expand("0.5") == "zero point five"
    assert numbers.expand("0.75") == "zero point seven five"


def test_other_decimals_are_left_alone():
    """The engine reads these correctly; rewriting them would only risk it."""
    for n in ("1.5", "10.7", "3.14"):
        assert numbers.expand(n) == n


# -- what must not be touched --------------------------------------------

def test_the_dictionary_keeps_its_abbreviations():
    """5KB and 1,234MB have engine rules of their own.

    Rewriting them here would take the rule away without replacing it, and
    there is a regression test for those in test_abbreviations.py.
    """
    for t in ("5KB", "1,234MB", "20ish", "MP3", "H2O"):
        assert numbers.expand(t) == t


def test_off_changes_nothing():
    for t in ("3222233", "0.7.3", "0.5"):
        assert numbers.expand(t, "off") == t


def test_words_reads_everything_out():
    assert numbers.expand("1234", "words") == "one thousand two hundred thirty four"
    assert numbers.expand("1.5", "words") == "one point five"
    assert numbers.expand("3222233", "words") == (
        "three million two hundred twenty two thousand two hundred thirty three")


def test_text_around_the_number_survives():
    assert numbers.expand("it rose to 3222233 units in 1999") == \
        "it rose to 3,222,233 units in 1999"


def test_words_do_not_smoosh_into_their_neighbours():
    """The failure mode Tomi hit in another driver.

    "1.5 experience acceleration card" came back with "onepoint" run together.
    Nothing here can do that: a number touching a letter is never matched at
    all, and a number that is matched keeps the spacing around it exactly.
    """
    assert numbers.expand("1.5 experience acceleration card", "words") == \
        "one point five experience acceleration card"
    for glued in ("1.5x", "x1.5", "3222233rd", "a1234567"):
        assert numbers.expand(glued, "words") == glued
    assert numbers.expand("(1.5)", "words") == "(one point five)"
    assert numbers.expand("1.5, then 2", "words") == "one point five, then two"


def test_a_trailing_dot_is_not_a_decimal():
    """The end of a sentence must not turn into a version number."""
    assert numbers.expand("we counted 42.") == "we counted 42."


# -- through the driver ---------------------------------------------------

def test_an_embedded_command_is_not_rewritten(driver):
    """With commands accepted, "[[rate 200]]" is still in the text.

    Rewriting the 200 inside it would hand the engine
    "[[rate two hundred]]" -- an unparseable command where a working one was.
    Splitting has to keep the commands too: `re.split` drops its separators
    unless the pattern captures, which deleted every command on the first try.
    """
    driver.acceptCommands = True
    driver._set_numberStyle("words")
    try:
        a = driver._render("[[rate 200]] hello", driver._wpm(),
                           driver._get_voice())
        b = driver._render("[[rate 200]] hello", driver._wpm(),
                           driver._get_voice())
        assert a and a == b
        #: If the command had been mangled it would be spoken instead of
        #: obeyed, which is far longer than the word "hello".
        plain = driver._render("hello", driver._wpm(), driver._get_voice())
        assert len(a) < len(plain) * 3
    finally:
        driver.acceptCommands = False
        driver._set_numberStyle("fix")


def test_the_setting_reaches_the_engine(driver):
    """Off and on must render differently, or the setting does nothing."""
    driver._set_numberStyle("off")
    off = driver._render("3222233", driver._wpm(), driver._get_voice())
    driver._set_numberStyle("fix")
    fixed = driver._render("3222233", driver._wpm(), driver._get_voice())
    assert off and fixed
    assert off != fixed, "the number setting changed nothing"
