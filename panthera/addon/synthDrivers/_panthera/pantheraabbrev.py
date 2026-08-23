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


def spell(text):
    """-> `text` with acronym-shaped abbreviations spelt out.

    Case-sensitive on purpose: only the all-capitals form is touched, so
    "Dr. Who" and "Ali vs Frazier" are left exactly as written.
    """
    if not text:
        return text
    return _RE.sub(lambda m: " ".join(m.group(1)), text)
