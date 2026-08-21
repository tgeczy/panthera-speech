# -*- coding: utf-8 -*-
"""Numbers the engine reads wrongly, rewritten before it sees them.

Leopard's front end is good at numbers up to six digits and gives up after
that.  Measured on Alex, what the engine does with a bare digit string:

    1234        "twelve thirty four"        -- read as a year
    12345       "twelve thousand three hundred 'n' forty five"
    123456      "one hundred twenty three thousand four hundred 'n' fifty six"
    1234567     "1 2 3 4 5 6 7"             <- spelled, one digit at a time
    3222233     "3 2 2 2 3 3"               <- spelled

**Seven digits is where it stops.**  And the fix the engine itself suggests is
already in the text: `1,234,567` with its separators is read correctly as a
number.  So the cheapest repair is to group the digits and let the engine go on
doing its own arithmetic in its own style, rather than replacing its number
reading with ours.

The other report is versions: `v0.7.3` comes out as "v point seven point
three", the leading zero dropped entirely.  Nothing recovers that except
writing the zero as a word.

None of this is tunable in the engine.  All 283 of its parameters are prosody
and unit selection -- the only two with "Num" in them are
`PitchAssembly.NumPPConsidered` and `SVDDistance.NumPitchPeriodsForSVDDistance`
-- and number reading lives in `SpeechDictionary`, which takes no settings.

A separate module because it is separate work: text in, text out, no engine and
no driver state, so it can be read and tested on its own.
"""
import re

#: Never touch a number that is part of a word.  `5KB` and `1,234MB` are the
#: dictionary's business -- it has rules for exactly those, and rewriting them
#: here would take the rule away without replacing it.  Both lookarounds are
#: needed: the leading one keeps `MP3` intact, the trailing one keeps `5KB`.
#:
#: `\.\d` in the trailing guard is not decoration.  Without it "1.5x" is not
#: left alone: the decimal part cannot be taken (the "x" fails the guard), so
#: the match backtracks to the bare "1", and the "." after it is not a letter,
#: so the guard passes and "1.5x" comes out as "one.5x".  That is exactly the
#: smooshing Tomi hit in another driver, arrived at from the other direction.
_TOKEN_RE = re.compile(
    r"(?<![A-Za-z0-9,.])(-?\d[\d,]*(?:\.\d+)*)(?![A-Za-z0-9]|\.\d)")

_ONES = ("zero one two three four five six seven eight nine ten eleven twelve "
         "thirteen fourteen fifteen sixteen seventeen eighteen nineteen").split()
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
         "eighty", "ninety")
#: Stops at trillion deliberately: past that the words stop being agreed on,
#: and a number that long is an identifier rather than a quantity anyway.
_SCALES = ((1000000000000, "trillion"), (1000000000, "billion"),
           (1000000, "million"), (1000, "thousand"))

#: Above this the engine spells the digits out one at a time.
LONG_DIGITS = 7


def to_words(n):
    """-> an integer in English words, or None if it is too large to say."""
    if n < 0:
        rest = to_words(-n)
        return None if rest is None else "minus " + rest
    if n < 20:
        return _ONES[n]
    if n < 100:
        return _TENS[n // 10] + ("" if n % 10 == 0 else " " + _ONES[n % 10])
    if n < 1000:
        return (_ONES[n // 100] + " hundred"
                + ("" if n % 100 == 0 else " " + to_words(n % 100)))
    if n >= 1000 * _SCALES[0][0]:
        return None
    #: Largest scale first, and the test is `n >= value`, not `n < value*1000`:
    #: the latter picks "trillion" for one thousand, names it "zero trillion"
    #: and then recurses on the same number for ever.
    for value, name in _SCALES:
        if n >= value:
            head = to_words(n // value) + " " + name
            return head if n % value == 0 else head + " " + to_words(n % value)
    return None


def _digits(s):
    """-> "seven five" for "75" -- how the part after a point is read."""
    return " ".join(_ONES[int(d)] for d in s)


def _group(digits):
    """-> "3,222,233" for "3222233", which the engine reads correctly."""
    out = []
    while len(digits) > 3:
        out.insert(0, digits[-3:])
        digits = digits[:-3]
    out.insert(0, digits)
    return ",".join(out)


def _rewrite(token, style):
    sign = "-" if token.startswith("-") else ""
    body = token.lstrip("-").replace(",", "")
    parts = body.split(".")
    if not all(parts):
        return token                       # a trailing dot: leave it alone

    minus = "minus " if sign else ""

    #: Three or more parts is a version, never a quantity: 0.7.3.  Each part is
    #: its own number and the separators have to be spoken, which is the one
    #: case the engine loses entirely.
    if len(parts) > 2:
        said = [to_words(int(p)) for p in parts]
        if any(w is None for w in said):
            return token
        return minus + " point ".join(said)

    whole, frac = parts[0], (parts[1] if len(parts) == 2 else None)

    if style == "words":
        said = to_words(int(whole))
        if said is None:
            return token
        return minus + said + ("" if frac is None else " point " + _digits(frac))

    # style == "fix": change only what the engine gets wrong, and leave its own
    # number reading alone everywhere else.
    if frac is not None:
        #: A leading zero is dropped by the engine -- "0.5" is heard as "point
        #: five" -- so that one is written out.  Every other decimal is left as
        #: it is, because the engine reads those correctly.
        if whole.lstrip("-") in ("0", "00"):
            return minus + "zero point " + _digits(frac)
        return token
    if len(whole) >= LONG_DIGITS:
        #: Grouped, not spelled: the engine reads "1,234,567" correctly, so it
        #: keeps its own phrasing and we change as little as possible.
        return sign + _group(whole)
    return token


def expand(text, style="fix"):
    """Rewrite the numbers in `text` that the engine would get wrong.

    `style` is "off" (leave everything alone), "fix" (only what is broken:
    seven digits and up, and versions and decimals whose leading zero would be
    lost), or "words" (every number in English words).
    """
    if style == "off" or not text:
        return text
    return _TOKEN_RE.sub(lambda m: _rewrite(m.group(1), style), text)
