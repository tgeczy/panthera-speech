# -*- coding: utf-8 -*-
"""What survives the trip from NVDA's Unicode to the engine's bytes.

The engine's text is a single-byte Mac encoding, and everything here is about
the characters that do not fit in one.  Pure Python, no engine, so these run on
a machine with no Mac OS X data at all -- which matters because the bug that
prompted them was reported in Hungarian and the person who has to reproduce it
may not have a Hungarian keyboard, let alone Lion.
"""
import pantheradriver


def enc(text):
    return pantheradriver._encode(text)


def test_ascii_is_unchanged():
    """The floor under every other test here: nothing below 0x80 may move.

    Two of the four generations render byte-identically across any change to
    this file only because of that, and a regression would be inaudible in the
    one place it was measured and wrong everywhere else.
    """
    plain = "The quick brown fox, 1 2 3 -- don't stop!"
    assert enc(plain) == plain.encode("ascii")


def test_the_accents_macroman_has_are_passed_straight_through():
    """Six of Hungarian's eight, and all of Western Europe's.

    These were never the problem in the driver: they encode to one byte each
    and always did.  What broke was 10.7 reading that byte -- see
    `tests/lion/test_lion_accents.py`.
    """
    assert enc(u"á") == b"\x87"        # a acute
    assert enc(u"é") == b"\x8e"        # e acute
    assert enc(u"ó") == b"\x97"        # o acute
    assert enc(u"ö") == b"\x9a"        # o diaeresis
    assert enc(u"ú") == b"\x9c"        # u acute
    assert enc(u"ü") == b"\x9f"        # u diaeresis


def test_hungarians_two_long_vowels_are_folded_rather_than_dropped():
    """**These were silent on every generation, not just Lion.**

    MacRoman has no double acute, so `ő` and `ű` fell through to the
    unmappable handler and came out as a gap -- in Tiger, Leopard, Snow
    Leopard and Lion alike.  Reported as "most of them are spoken", which is
    what six-of-eight sounds like.

    Folded to the diaeresis, not to a bare vowel: in Hungarian these are the
    long counterparts of `ö` and `ü`, so `ö` is much nearer than `o`.
    """
    assert enc(u"ő") == enc(u"ö")     # ő -> ö
    assert enc(u"ű") == enc(u"ü")     # ű -> ü
    assert enc(u"Ő") == enc(u"Ö")     # Ő -> Ö
    assert enc(u"Ű") == enc(u"Ü")     # Ű -> Ü
    # The word that started it, and not one gap in it.
    assert b" " not in enc(u"hűtő")


def test_an_accent_macroman_never_heard_of_is_stripped_not_dropped():
    """`Şoseaua`, not ` oseaua`.

    Every Polish, Czech, Turkish and Romanian letter outside MacRoman used to
    arrive as a hole.  Decomposing and keeping the base letter is wrong the
    way an English-speaking reader is wrong, which is a great deal better than
    absent.
    """
    assert enc(u"Şoseaua") == b"Soseaua"       # Ş
    assert enc(u"ź") == b"z"                   # ź
    assert enc(u"ř") == b"r"                   # ř
    assert enc(u"ğ") == b"g"                   # ğ


def test_a_stroke_is_not_a_combining_mark():
    """Which is why these four need naming and the rest do not.

    `Ł` is one indivisible character to Unicode rather than `L` plus a mark,
    so there is nothing for the decomposition above to strip and it would
    still have arrived as a gap.
    """
    assert enc(u"Łódź") == b"L\x97dz"     # Łódź
    assert enc(u"đ") == b"d"                       # đ


def test_something_with_no_letter_in_it_at_all_is_still_a_space():
    """A gap, deliberately, and never "?".

    The engine reads a question mark as a *question* and lifts the intonation
    of the whole sentence for it, so the obvious `errors="replace"` would make
    one unmappable character change how the rest of the line sounds.
    """
    assert enc(u"a中b") == b"a b"
    assert enc(u"☃") == b" "                       # a snowman
    assert b"?" not in enc(u"中")


def test_the_symbols_macroman_does_have_are_not_stripped():
    """Kept from before: the em dash and the curly quotes are real characters
    here, and turning them into ASCII would undo a fix that shipped."""
    assert enc(u"—") == b"\xd1"                    # em dash
    assert enc(u"“") == b"\xd2"                    # left double quote
    assert enc(u"…") == b"\xc9"                    # ellipsis
    # ...but the typographic apostrophe is still folded, because the engine
    # reads MacRoman's own 0xD5 as a quotation mark and breaks the phrase.
    assert enc(u"Canopy’s") == b"Canopy's"
