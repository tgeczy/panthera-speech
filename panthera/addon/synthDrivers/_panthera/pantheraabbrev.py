# -*- coding: utf-8 -*-
"""Acronyms the engine insists are abbreviations.

**"DR" is read as "doctor".**  So is "Dr", "dr" and "Dr." -- which for three of
those is right, and for the capitals is very often not.  Reported by Tomi, who
found it while testing something else:

    "2-letter acronyms like DR read as 'doctor', and someone may not genuinely
    want the first two letters to get expanded."

Measured rather than guessed.  Rendering the token and rendering the word it
might become, and comparing the audio, says which ones the engine actually
rewrites -- no listening and no reading of a table that may not be the live
one.  Every entry in `ACRONYMS` came back **byte-identical** to its expansion
on Leopard's engine, in all three case forms:

    DR -> doctor    ST -> street    MR -> mister     MRS -> missus
    JR -> junior    SR -> senior    FT -> feet       RD  -> road
    CT -> court     VS -> versus    ETC -> etcetera

and the controls did not: DZ, QK and XR are left alone.

## Where it lives, and why the repair is in the text

Not in `SpeechDictionary`: rendering "DR" compiles no regular expression and
runs no SQL query, so neither of the two hooks this host owns is involved.  It
is a table inside MacinTalk itself -- `DRIVE`, `DOCTOR`, `SAINT`, `STREET`,
`FEET` sit in a row at MacinTalk + 0x702c4, and `SpeechDictionary` carries the
matching lexical tags `Abbrev`, `Street`, `Saint`, `Drive`, `Doctor` in its
feature list.  Those are *tags on dictionary entries*, not preferences: none of
the engine's 283 tunables reaches them.

So the repair is in the text, exactly as it is for numbers and for the colon,
and for the same reason.  See [[pronunciation-policy]].

## What it is replaced with, and why that is not an invention

A space between the letters, which is the engine's **own** reading of a capital
pair it does not recognise.  Measured: `DZ` and `D Z` render byte-identically,
so the engine already spells out an unknown acronym and this only stops the
dictionary getting in the way first.  Nothing here decides how anything should
sound.

## Only the capitals, and only when the setting is off

`vs` and `etc` are written that way in ordinary prose and mean exactly what the
engine says they mean -- reading them as "V S" and "E T C" would be a
straightforward regression.  `Dr.` and `St.` likewise.

The line that holds is **written as an abbreviation, or written as an
acronym**: `Dr.` is the first, `DR` is the second, and only the second is
touched.  That is also why this is behind "Expand abbreviations" rather than
being always on -- the default behaviour does not change for anybody.
"""
import re

#: Every token measured to be rewritten into a different word, upper case.
#:
#: The value is only documentation -- what is spoken is the letters, never the
#: word -- but it is what makes the table checkable against the engine, which
#: `tests/test_abbreviations.py` does.
ACRONYMS = {
    "DR": "doctor",
    "ST": "street",
    "MR": "mister",
    "MRS": "missus",
    "JR": "junior",
    "SR": "senior",
    "FT": "feet",
    "RD": "road",
    "CT": "court",
    "VS": "versus",
    "ETC": "etcetera",
}

#: Capitals only, whole word, and **not** followed by a full stop.
#:
#: The trailing stop is the tell that somebody wrote an abbreviation rather
#: than an acronym, and it is cheap to respect: "DR." keeps its expansion.
#: `[^.]` rather than a plain boundary because `\b` matches before a period.
_RE = re.compile(r"\b(%s)\b(?!\.)" % "|".join(sorted(ACRONYMS)))


# -- roman numerals -------------------------------------------------------
#
# **The other half of the same complaint, reported by Tomi the morning after
# the first half shipped:**
#
#     "Roman numerals, like XL, are not honored with abbreviations off."
#
# He is right, and *why* is worth writing down, because the code claimed
# otherwise.  `k_abbrev_marks` in `tiger_host_regex.c` -- the list of which
# dictionary rules "Expand abbreviations" covers -- carried an entry reading
# "IVXLCDM", commented "roman numerals".  **It never matched anything.**
#
# Logging every regular expression `SpeechDictionary` actually compiles, on
# text full of roman numerals, gives six patterns, and not one of them is
# about roman numerals:
#
#     ^((JAN(UARY)?)|(FEB(RUARY)?)|...)$                 the month alternation
#     ^(([[:digit:]]{1,3}(,[[:digit:]]{3})*)|...)[[:upper:]]+$  5KB, 1,234MB
#     ^(K|M|G|T|P)B$                                     a bare unit
#     ^[[:digit:]]+ISH$                                  20ish
#     ^[[:digit:]]{7,}$                                  long digit runs
#     ^[[:upper:]]+&[[:upper:]]+$                        AT&T
#
# So roman numerals are not a dictionary rule at all.  They are where `DR`
# lives: inside MacinTalk's own lexicon, which no setting of the engine's
# reaches.  A mark for a rule that does not exist is worse than no mark,
# because it reads as coverage -- it has been removed.
#
# Measured on Leopard, the token against the word it might become:
#
#     II -> "two", III -> "three", and VI, VII, VIII, XI, XII, XIII, XIV,
#     XVI..XIX, XXI..XXXIX, XLV, XLIX, XCIX, LXX, LXXX, MCM and MMXXVI all
#     read as something other than their letters.
#
#     IV, IX, XX, XL, XC, L, C, D and M already read as letters, so spelling
#     those changes nothing -- which is why the rule can be a rule rather than
#     another hand-kept table.
#
#: A strict roman numeral: thousands, then hundreds, tens and units, each
#: group in descending order.  Strictness is the whole point.  A loose
#: `[IVXLCDM]+` would claim `DIM`, `MILD`, `LID` and `CIVIL`, none of which is
#: a number; this pattern rejects all four, because their letters are out of
#: order.
_ROMAN = re.compile(r"\b(?=[MDCLXVI]{2,}\b)"
                    r"(M{0,3}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})"
                    r"(?:IX|IV|V?I{0,3}))\b")

#: Valid roman numerals that are also ordinary English words.
#:
#: Checked rather than guessed, and the answer is short: of every word tried,
#: exactly one survives the pattern above.  `MIX` is `M` + `IX`, 1009.
#:
#: `CD`, `DC`, `MD`, `CM`, `MM`, `XL` and `DIV` survive it as well and are
#: deliberately **not** here.  They are abbreviations, and reading an
#: abbreviation as its letters is the whole point of the setting being off.
_ROMAN_NOT = frozenset(["MIX"])


def _roman(m):
    tok = m.group(1)
    return tok if tok in _ROMAN_NOT else " ".join(tok)


def spell(text):
    """-> `text` with acronym-shaped abbreviations spelt out.

    Case-sensitive on purpose: only the all-capitals form is touched, so
    "Dr. Who" and "Ali vs Frazier" are left exactly as written.

    **No trailing-stop exception for the numerals**, unlike the acronyms
    above, and the difference is real: a full stop after `DR` is the mark that
    says "abbreviation", while a full stop after `II` is the end of a
    sentence.  "He fought in World War II." should not keep its expansion for
    having ended a paragraph.
    """
    if not text:
        return text
    text = _RE.sub(lambda m: " ".join(m.group(1)), text)
    return _ROMAN.sub(_roman, text)
