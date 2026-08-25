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

## What the setting covers now, and the line that moved

The first release of this module drew the line at **written as an
abbreviation, or written as an acronym**: `Dr.` kept its expansion, `DR`
was spelt.  Tomi moved the line on 2026-08-26, from news articles read by
ear: the engine's own table turns `Dr.` into "doctor" *or "drive"* by a
part-of-speech guess, `St.` into "saint" or "street", and on 10.7 `4m`
into "four meters" -- and none of that is reachable by `TIGER_NO_ABBREV`,
so the "Expand abbreviations" checkbox only did half of what it said.

    "the toggle should do what it's advertised more (including doctor
    and drive)"

So with the setting off, the written-as-abbreviation forms despell too:
`Dr.` reads "D R.", `4mm` reads "4 M M".  Lowercase prose words -- `vs`,
`etc`, `dr` as somebody's initials -- are still never touched; reading
"Ali vs Frazier" as "Ali V S Frazier" is the regression this module
promised not to have, and keeps promising.
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

#: Capitals, whole word, dotted or not.  The first release excepted "DR."
#: on the theory that the trailing stop marks a deliberate abbreviation;
#: Tomi reversed that on 2026-08-26 -- with the setting off, "ST. LOUIS"
#: in a headline reads "S T. LOUIS", because the setting being off is
#: itself the deliberate act.
_RE = re.compile(r"\b(%s)\b" % "|".join(sorted(ACRONYMS)))

#: The written-as-abbreviation forms, title case, measured against both
#: engines on 2026-08-26 (Alex's MEOW fetch list names the words): every
#: one of these expands on 10.5 and 10.7 whether or not the stop is
#: written -- "Dr Kirk" reads "doctor Kirk" -- and several are homograph
#: pairs decided by a part-of-speech guess ("Smith St." is "street",
#: "St. Louis" is "saint"; "Smith Dr." is "drive").  One comedy datum:
#: 10.7 dropped the Capt expansion 10.5 has.  Despelled all the same --
#: one shared map, no per-generation branching.
TITLES = ("Dr", "St", "Mr", "Mrs", "Ms", "Prof", "Gen", "Sen", "Rep",
          "Gov", "Capt", "Lt", "Jr", "Sr", "Ave", "Rd", "Blvd", "Ft", "Ct")

_TITLE_RE = re.compile(r"\b(%s)\b" % "|".join(sorted(TITLES, key=len,
                                                     reverse=True)))

#: Units the engine expands from inside its own lexicon, where the
#: dictionary switch cannot reach: measured with `TIGER_NO_ABBREV=1`,
#: 10.7 still reads "4m" as "four meters", "4mm" as millimeters, "4cm",
#: "4km", "4kg" likewise; 10.5 only knows the spaced form ("4 m") and
#: spells the rest.  The rewrite is to the capital letters -- "4M" is
#: measured to read "four M" on both -- so "4mm" becomes "4 M M".
_UNIT_RE = re.compile(r"\b(\d+)\s?(mm|cm|km|kg|g|m)\b")


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
    """-> `text` with abbreviation-shaped tokens spelt out.

    Case-sensitive on purpose: the all-capitals acronyms, the title-case
    abbreviations, and digit-adjacent units are touched; "ali vs frazier"
    and a lowercase "dr" are left exactly as written.

    **No trailing-stop exception for the numerals**: a full stop after `II`
    is the end of a sentence.  "He fought in World War II." should not keep
    its expansion for having ended a paragraph.
    """
    if not text:
        return text
    text = _RE.sub(lambda m: " ".join(m.group(1)), text)
    text = _TITLE_RE.sub(lambda m: " ".join(m.group(1).upper()), text)
    text = _UNIT_RE.sub(
        lambda m: m.group(1) + " " + " ".join(m.group(2).upper()), text)
    return _ROMAN.sub(_roman, text)


# -- the engine's guesses, corrected in both settings ---------------------
#
# Two rewrites that run whether expansion is on or off, because what they
# fix is not an expansion but a wrong guess (see [[news-reading-quirks]]):
#
# * "Dr." before a capitalised word is a title in news prose, but the
#   engine's part-of-speech resolver reads "<proper noun> Dr." as a street
#   -- "Forensic Phycologist Dr. Kirk Heilbrun" came back "drive", because
#   an unknown capitalised word is guessed to be a name.  Writing "Doctor"
#   settles it.  Streets survive: "Mulholland Dr. is long" and
#   "Smith Dr., Springfield" have no capitalised word after the stop.
# * "X's" after any word reads as the roman numeral -- "Space X's" came
#   back "space ten's", and NVDA's builtin dictionary splits camel case
#   for every synthesizer, so every "SpaceX's" in a news article arrives
#   pre-split.  "ex's" is the engine's own reading of the letter, so the
#   rewrite only stops the numeral guess.  Unconditional because
#   sentence-initially it rewrites to what the engine already says.
#
# Both are authored without lookbehind, deliberately: the SAPI engine
# ports these to `std::wregex`, which has none.

_DOCTOR_RE = re.compile(r"\bDr\.(\s+)(?=[A-Z][a-z])")
_EX_RE = re.compile(u"\\bX([\u2019']s)\\b")


def disambiguate(text, expand=True):
    """-> `text` with the engine's measured wrong guesses settled.

    `expand` says whether "Expand abbreviations" is on: the Doctor rewrite
    only applies then, because with it off `spell()` despells "Dr." to
    letters and writing "Doctor" would be an expansion the user declined.
    """
    if not text:
        return text
    text = _EX_RE.sub(r"ex\1", text)
    if expand:
        text = _DOCTOR_RE.sub(r"Doctor\1", text)
    return text
