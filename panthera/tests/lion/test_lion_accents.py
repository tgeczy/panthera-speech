# -*- coding: utf-8 -*-
"""10.7 is the only generation that reads its text as a CFString.

Which is why it is the only one that lost accented characters.  Tiger, Leopard
and Snow Leopard hand the engine raw bytes through `SESpeakBuffer` and its own
front end decodes MacRoman itself; 10.7 takes a `CFStringRef` and asks this
host to widen it, and the host widened by zero-extension -- so `á`, MacRoman
0x87, arrived as **U+0087, a C1 control**, and the lexer dropped it.

Reported by Tomi in Hungarian: silent on Lion, spoken on Tiger and Leopard.

The tell, before anything was disassembled, was that all eight accented
letters produced *identical* output -- 15568 frames every time, whichever
accent it was.  Identical output for different input is what "the character is
not there" looks like.
"""
import pytest


ACCENTED = u"The word is sár."
PLAIN = u"The word is sar."


def _frames(pcm):
    return len(pcm) // 2 if pcm else 0


def test_an_accented_word_renders_like_its_plain_form(driver):
    """**The property that matters, and it is not "makes a sound".**

    MacinTalk reads English rules, so `sár` should come out as `sar` does --
    the accent neither deletes the letter nor adds anything.  Measured on
    Leopard, where it has always worked, the two are byte-identical; asserting
    that exactly here would be asserting something about 10.7 nobody has
    promised, so this asks for the same length within a hair.

    Before the fix this was 17350 against 15568 -- the letter simply gone.
    """
    driver._set_voice("Fred")
    wpm = driver._wpm()
    a = _frames(driver._render(ACCENTED, wpm, "Fred"))
    b = _frames(driver._render(PLAIN, wpm, "Fred"))
    assert a and b, (a, b)
    assert abs(a - b) < 0.02 * b, (
        "accented %d frames against plain %d -- 10.7 is not reading the "
        "accented letter as the letter" % (a, b))


@pytest.mark.parametrize("letter", [u"á", u"é", u"ó",
                                    u"ö", u"ú", u"ü",
                                    u"ő", u"ű"])
def test_no_accented_letter_is_silently_dropped(driver, letter):
    """All eight of Hungarian's, including the two MacRoman cannot spell.

    `ő` and `ű` reach the engine as `ö` and `ü` -- see
    `tests/test_encoding.py`; what is checked here is only that something
    arrives, on the generation where nothing used to.
    """
    driver._set_voice("Fred")
    wpm = driver._wpm()
    text = u"the letter %s here" % letter
    got = _frames(driver._render(text, wpm, "Fred"))
    bare = _frames(driver._render(u"the letter here", wpm, "Fred"))
    assert got > bare, (
        "U+%04X adds nothing to the utterance (%d frames against %d without "
        "it), so 10.7 is dropping it" % (ord(letter), got, bare))


def test_a_lone_accented_character_is_spoken_as_a_name(driver):
    """**Not a bug, and worth pinning so nobody "fixes" it.**

    On its own, 10.7 expands an accented character into a name rather than a
    letter: its own narration (`MTX_DEBUG=1`) shows seven phonemes for `á`
    where plain `a` gets two, and it runs about 590 ms against 250.  Leopard
    says the letter instead.

    That is 10.7's front end, not this host -- what this host now does is hand
    it the character it was given.  Saying a name is a great deal better than
    the silence it replaced.
    """
    driver._set_voice("Fred")
    wpm = driver._wpm()
    accented = _frames(driver._render(u"á", wpm, "Fred"))
    plain = _frames(driver._render(u"a", wpm, "Fred"))
    assert accented > plain, (accented, plain)
