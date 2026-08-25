# -*- coding: utf-8 -*-
"""Turning off "Expand abbreviations" has to turn off the ones people meet.

Tomi, testing something else entirely:

    "2-letter acronyms like DR read as 'doctor', and someone may not genuinely
    want the first two letters to get expanded."

The setting existed and did not cover it.  `TIGER_NO_ABBREV` declines to
compile the `SpeechDictionary` rules that rewrite units and quantities -- 5KB
into kilobytes, 20ish into twentyish -- and those are regular expressions this
host compiles, so refusing one turns it off.  "DR" never reaches them: it is a
lexical entry inside MacinTalk, and rendering it runs no regular expression and
no SQL query at all.

The other half of the same setting is in
`tests/leopard/test_abbreviations.py`, which covers the dictionary rules and
the unterminated-buffer bug that made them fire only sometimes.  Two
mechanisms, one checkbox -- which is exactly how half of it went unnoticed.

Two halves are tested here and they need each other:

* `test_the_table_is_what_the_engine_actually_does` asks the engine, so the
  table cannot quietly drift from the thing it describes -- and it is the test
  that would have caught the entries never being right in the first place.
* the rest are plain text tests of the rule, which is where the judgement
  lives: only capitals, never a trailing stop, and `vs` and `etc` as written in
  prose are left alone.
"""
import os
import struct
import subprocess

import pytest

import pantheraabbrev


# -- the rule -------------------------------------------------------------

@pytest.mark.parametrize("token", sorted(pantheraabbrev.ACRONYMS))
def test_capitals_are_spelt_out(token):
    assert pantheraabbrev.spell(token) == " ".join(token)


@pytest.mark.parametrize("written", ["dr", "vs", "etc", "st"])
def test_a_lowercase_word_is_left_alone(written):
    """`vs` and `etc` mean what the engine says they mean.

    Reading "Ali vs Frazier" as "Ali V S Frazier" would be a plain regression,
    and it is the reason this is case-sensitive rather than thorough.
    """
    assert pantheraabbrev.spell(written) == written


def test_a_trailing_stop_no_longer_saves_it():
    """Deliberately the reverse of the rule this module shipped with.

    The first release kept "DR." expanded on the theory that the stop marks
    a deliberate abbreviation.  Tomi, 2026-08-26: "the toggle should do what
    it's advertised more (including doctor and drive)" -- the setting being
    off is itself the deliberate act, so the dotted forms despell too.
    """
    assert pantheraabbrev.spell("DR.") == "D R."
    assert pantheraabbrev.spell("ST. LOUIS") == "S T. LOUIS"


@pytest.mark.parametrize("written,spoken", [
    ("Dr. Kirk", "D R. Kirk"),
    ("Dr Kirk", "D R Kirk"),
    ("St. Louis", "S T. Louis"),
    ("Mrs. Kirk", "M R S. Kirk"),
    ("Prof. Kirk", "P R O F. Kirk"),
    ("Main Blvd.", "Main B L V D."),
    #: The dot survives despelling everywhere, deliberately: "Dr." at a
    #: sentence end still has to end the sentence.  Whether a mid-sentence
    #: "J R." earns a spurious pause is one for Tomi's ear.
    ("John Smith Jr. spoke", "John Smith J R. spoke"),
])
def test_a_title_case_abbreviation_despells(written, spoken):
    """The engine expands these from its own lexicon, dotted or not --
    measured 2026-08-26, "Dr Kirk" reads "doctor Kirk" -- so the setting
    can only keep its word by despelling the written-as-abbreviation forms
    too."""
    assert pantheraabbrev.spell(written) == spoken


@pytest.mark.parametrize("written,spoken", [
    ("4m", "4 M"),
    ("4 m", "4 M"),
    ("4mm", "4 M M"),
    ("4cm", "4 C M"),
    ("12km", "12 K M"),
    ("4kg", "4 K G"),
])
def test_a_digit_adjacent_unit_despells(written, spoken):
    """10.7 reads "4m" as "four meters" from inside its lexicon, where
    `TIGER_NO_ABBREV` cannot reach -- measured with the flag set.  The
    capital letters are the engine's own bare-letter reading."""
    assert pantheraabbrev.spell(written) == spoken


@pytest.mark.parametrize("untouched", ["4mph", "4ml today", "grams", "M4"])
def test_a_unit_lookalike_is_left_alone(untouched):
    assert pantheraabbrev.spell(untouched) == untouched


def test_only_whole_words():
    for text in ("DRUM", "ADR", "MRS_SMITH", "CTRL", "FTP"):
        assert pantheraabbrev.spell(text) == text, text


def test_it_works_inside_a_sentence():
    assert pantheraabbrev.spell("the DR and ST fields") == \
        "the D R and S T fields"


def test_prose_and_dictionary_rules_are_not_this_module():
    """5KB and 20ish are SpeechDictionary regexes, which TIGER_NO_ABBREV
    already covers; lowercase vs. is prose.  Dr. despells -- see above."""
    text = "Meeting at 5KB, room 20ish -- vs. the Master."
    assert pantheraabbrev.spell(text) == text


# -- the engine's guesses, settled in both settings ------------------------

def test_x_possessive_never_reads_as_a_numeral():
    """NVDA's builtin dictionary splits camel case for every synthesizer,
    and the engine reads "<word> X's" as the roman numeral: "SpaceX's own
    page" arrived as "Space X's" and was spoken "space ten's"
    (panthera-speech, news-reading quirks, 2026-08-26)."""
    for apostrophe in ("'", "’"):
        text = "Space X%ss own page" % apostrophe
        want = "Space ex%ss own page" % apostrophe
        assert pantheraabbrev.disambiguate(text, True) == want
        assert pantheraabbrev.disambiguate(text, False) == want


def test_doctor_before_a_name_stays_a_doctor():
    """"Forensic Phycologist Dr. Kirk Heilbrun" read as "drive": the POS
    resolver guesses an unknown capitalised word is a name, and a name
    before Dr. makes a street.  Writing Doctor settles it."""
    got = pantheraabbrev.disambiguate("Phycologist Dr. Kirk spoke", True)
    assert got == "Phycologist Doctor Kirk spoke"


def test_a_real_street_is_left_alone():
    for text in ("Mulholland Dr. is long", "Smith Dr., Springfield",
                 "turn onto Smith Dr."):
        assert pantheraabbrev.disambiguate(text, True) == text


def test_the_doctor_rewrite_defers_to_despelling():
    """With expansion off, spell() reads "Dr." as letters; writing Doctor
    would be an expansion the user declined."""
    text = "Phycologist Dr. Kirk spoke"
    assert pantheraabbrev.disambiguate(text, False) == text
    assert pantheraabbrev.spell(text) == "Phycologist D R. Kirk spoke"


# -- the table, against the engine ----------------------------------------

REQ, RSP = 0x54475233, 0x54475253


def _render(proc, text):
    t, v = text.encode("utf-8"), b"Fred"
    proc.stdin.write(struct.pack("<IiIIII", REQ, 180, 0, 0, len(v), len(t))
                     + v + t)
    proc.stdin.flush()
    head = proc.stdout.read(12)
    assert len(head) == 12, "the host closed the pipe"
    _magic, _status, nframes = struct.unpack("<IiI", head)
    out = b""
    while len(out) < nframes * 2:
        chunk = proc.stdout.read(nframes * 2 - len(out))
        assert chunk, "the response was short"
        out += chunk
    return out


@pytest.fixture(scope="module")
def host():
    import leopardspeech
    tree = leopardspeech.find_tree()
    if not tree:
        pytest.skip("no Leopard speech tree")
    if not os.path.isfile(leopardspeech.HOST_EXE):
        pytest.skip("panthera_host.exe not built")
    mt, sd, voices = leopardspeech.engine_paths(tree)
    p = subprocess.Popen([leopardspeech.HOST_EXE, "--serve", mt, sd, voices],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL)
    yield p
    p.kill()


@pytest.mark.parametrize("token", sorted(pantheraabbrev.ACRONYMS))
def test_the_table_is_what_the_engine_actually_does(host, token):
    """Every entry must really be expanded, and spelling it must really stop it.

    Asked of the engine rather than asserted: the token and its expansion come
    back byte-identical, and the spelt-out form does not.  An entry the engine
    does not expand would be this module rewriting text for no reason.
    """
    word = pantheraabbrev.ACRONYMS[token]
    assert _render(host, token) == _render(host, word), (
        "%s is not expanded to %r by the engine, so spelling it out changes "
        "a reading that was already correct" % (token, word))
    assert _render(host, pantheraabbrev.spell(token)) != _render(host, word), \
        "spelling %s out did not stop the expansion" % token


# -- roman numerals -------------------------------------------------------
#
# Tomi, the morning after the acronym half shipped:
#
#     "Roman numerals, like XL, are not honored with abbreviations off."
#
# The host carried a mark claiming to cover them and it never matched a
# pattern, because the dictionary has no roman-numeral rule at all -- they are
# a lexical entry inside MacinTalk, exactly where DR is.  See the note in
# `pantheraabbrev.py` for the six patterns the dictionary really does compile.

#: Numerals the engine reads as something other than their letters, and a
#: couple it already spells.  Both halves matter: the first is what the rule is
#: for, the second is what makes it safe to state as a rule instead of a table.
ROMAN_EXPANDED = ["II", "III", "VII", "VIII", "XI", "XII", "XIV", "XVIII",
                  "XXI", "XXX", "MMXXVI"]
ROMAN_ALREADY_LETTERS = ["IV", "IX", "XX", "XL", "XC"]


@pytest.mark.parametrize("token", ROMAN_EXPANDED + ROMAN_ALREADY_LETTERS)
def test_a_roman_numeral_is_spelt_out(token):
    assert pantheraabbrev.spell(token) == " ".join(token)


@pytest.mark.parametrize("word", ["MIX", "DIM", "MILD", "LID", "CIVIL",
                                  "VIVID", "LIVID", "IMAX", "MI5"])
def test_a_word_made_of_roman_letters_is_not_a_numeral(word):
    """`DIM`, `MILD`, `LID` and `CIVIL` have their letters out of order, so the
    strict pattern rejects them.  `MIX` really is 1009 and is excluded by name
    -- it is the only ordinary English word that survives the pattern."""
    assert pantheraabbrev.spell(word) == word


def test_a_lower_case_numeral_is_left_alone():
    """Same rule as the acronyms: capitals are the acronym form."""
    assert pantheraabbrev.spell("chapter ii and iii") == "chapter ii and iii"


def test_a_sentence_final_numeral_is_still_spelt():
    """**No trailing-stop exception here**, unlike DR.

    A stop after `DR` says "abbreviation"; a stop after `II` says "end of
    sentence".  Both readings cannot be right, and this is the one that is."""
    assert pantheraabbrev.spell("He fought in World War II.") == \
        "He fought in World War I I."


@pytest.mark.parametrize("token", ROMAN_EXPANDED)
def test_the_engine_really_does_expand_these(host, token):
    """Asked of the engine, like the acronym table above it.

    A numeral the engine already spells would be this module rewriting text for
    no reason -- which is exactly what `ROMAN_ALREADY_LETTERS` is, and why they
    are not in this list."""
    assert _render(host, token) != _render(host, " ".join(token)), \
        "%s is already read as its letters, so spelling it out changes " \
        "nothing and it does not belong in this list" % token


@pytest.mark.parametrize("token", ROMAN_ALREADY_LETTERS)
def test_and_these_it_already_spells(host, token):
    """The other half, and the reason the rule is safe to apply broadly:
    where the engine is already right, the rewrite is a no-op."""
    assert _render(host, token) == _render(host, " ".join(token))
