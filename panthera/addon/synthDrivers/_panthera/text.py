# -*- coding: utf-8 -*-
"""Turning what NVDA hands over into what the engine can be told.

Split out of `pantheradriver.py` unchanged.  Everything here is a pure
function of its arguments or a table one reads -- no engine, no host, no
driver state -- which is why it is worth having apart: these are the pieces
that can be tested by calling them, and most of the bugs recorded in the
comments below were found that way.

Three jobs, in the order an utterance meets them:

* **encoding** -- the engine's text is MacRoman, not UTF-8, and what MacRoman
  cannot spell is folded or stripped rather than replaced with "?", which the
  engine reads as a question and lifts a whole sentence for.
* **splitting** -- where a long utterance may be cut, which is a latency
  question: the first piece should be short so sound starts early, and the
  cut has to land on a sentence, then a phrase, then nothing.
* **joining** -- putting NVDA's fragments back into a sentence, so the engine
  composes one intonation over it rather than four.

`codecs.register_error` runs at import, next to the handler it registers.
`pantheradriver` re-exports every name here, so the drivers and the tests that
reach for `pantheradriver._encode` and the rest still find them.
"""
import codecs
import re
import unicodedata

#: An embedded speech command, as the engine's front end parses it.  Non-greedy, and
#: it will not run past a newline, so an unclosed "[[" cannot eat a paragraph.
COMMAND_RE = re.compile(r"\[\[[^\]]{0,64}\]\]")
#: The same thing, capturing, for splitting text into "command" and "not a
#: command" runs.  `re.split` keeps the separators only when the pattern has a
#: group -- without one it deletes every command it splits on.
COMMAND_SPLIT_RE = re.compile(r"(\[\[[^\]]{0,64}\]\])")

#: `[[inpt PHON]]`, `[[inpt TUNE]]` and the `[[inpt TEXT]]` that closes them.
#:
#: These switch the front end into a different *input mode*, where the text
#: that follows is phonemes or notes rather than words.  Every other embedded
#: command sets a parameter and leaves the text alone, which is why this one
#: needs its own pattern: it is the only family whose failure changes what the
#: rest of the utterance means.
INPUT_MODE_RE = re.compile(r"\[\[\s*inpt\s+[A-Za-z]{0,16}\s*\]\]", re.I)
#: The same family, capturing the mode word -- for carrying an unclosed mode
#: across utterance boundaries.  See the carry block in `_render` and
#: panthera-speech#9.
INPUT_MODE_CAPTURE_RE = re.compile(r"\[\[\s*inpt\s+([A-Za-z]{1,16})\s*\]\]",
                                   re.I)

#: Characters MacRoman has no room for, mapped to something it can say.
#: Everything typographic that matters -- em dash, en dash, curly quotes,
#: ellipsis -- MacRoman already has, so it is not listed here.
_FOLD = {
    0x00A0: u" ", 0x2007: u" ", 0x2009: u" ", 0x202F: u" ",   # fixed spaces
    0x2011: u"-", 0x2012: u"-", 0x2015: u"-", 0x2212: u"-",   # more dashes
    0x2032: u"'", 0x2033: u'"', 0x02BC: u"'",                 # primes
    #: The typographic apostrophe, and the reason a sentence full of them
    #: fell apart.  MacRoman *has* it, at 0xD5 -- but 0xD5 is the right
    #: single QUOTATION mark, and the engine's front end treats it as one:
    #: it breaks the phrase there.  "Canopy’s investments" came out as
    #: "Canopy" - 250 ms of silence - "s investments", and the sentence ran
    #: 1.57 s longer for the pauses it grew.  A straight apostrophe is an
    #: apostrophe, so these are folded before encoding.  Curly *double*
    #: quotes are left alone: those really are quotation marks.
    0x2018: u"'", 0x2019: u"'",
    0x2044: u"/",                                             # fraction slash

    #: **Hungarian's two long vowels, which no generation has ever spoken.**
    #: MacRoman has every accent Western European typography needed in 1984
    #: and the double acute is not among them, so `ő` and `ű` fell through to
    #: `_unmappable` and came out as a gap -- in Tiger, Leopard, Snow Leopard
    #: and Lion alike. Reported as "most of them are spoken", which is exactly
    #: what six-of-eight sounds like.
    #:
    #: Folded to the diaeresis rather than to bare `o` and `u`, which is what
    #: stripping the accent generically would give. In Hungarian these are the
    #: long counterparts of `ö` and `ü` -- same vowel, held longer -- so the
    #: diaeresis is the nearest thing MacRoman has, and it is near.
    0x0151: u"ö", 0x0150: u"Ö",                     # ő Ő
    0x0171: u"ü", 0x0170: u"Ü",                     # ű Ű

    #: **A stroke is not a combining mark**, so these four survive the
    #: decomposition in `_unmappable` and would still arrive as gaps: `Ł` is
    #: one indivisible character to Unicode rather than `L` plus a mark, so
    #: there is nothing to strip. Listed here because "Łódź" reading as "ódź"
    #: is the same complaint in Polish, and it is two lines to not have it.
    0x0141: u"L", 0x0142: u"l",                     # Ł ł
    0x0110: u"D", 0x0111: u"d",                     # Đ đ
}


def _unmappable(err):
    """Anything MacRoman cannot spell loses its accent, or becomes a space.

    **Strip the diacritic before giving up.**  MacRoman covers Western Europe
    as of 1984 and no further, so every Polish, Czech, Turkish and Romanian
    letter it never heard of used to arrive as a gap: `Łódź` was read as
    "ódź" with a hole where the L should be.  Decomposing and keeping the base
    letter gives "Lodz" -- wrong in the way a English-speaking reader is
    wrong, rather than absent.

    Characters with no decomposition at all still become a space, and the
    alternative to a space is worse than it looks: `errors="replace"` produces
    "?", which the engine reads as a *question* and lifts the intonation of
    the whole sentence for.  A gap is closer to the truth than a wrong
    inflection, and it leaves a real "?" the user typed meaning what it says.

    The four letters worth doing better than this for are in `_FOLD` above,
    which runs first: stripping would turn Hungarian `ő` into `o` where `ö` is
    the same vowel.
    """
    out = []
    for ch in err.object[err.start:err.end]:
        bare = u"".join(c for c in unicodedata.normalize("NFD", ch)
                        if not unicodedata.combining(c))
        try:
            bare.encode("mac_roman")
        except UnicodeEncodeError:
            bare = u""
        out.append(bare or u" ")
    return (u"".join(out), err.end)


codecs.register_error("panthera_fold", _unmappable)


def _encode(text):
    """-> the engine's bytes.

    **The engine's text is a single-byte Mac encoding, not UTF-8.**  Sent as
    UTF-8, one em dash arrived as three bytes and was read a character at a
    time: "he paused - then left" came out as "he paused, AI then left", which
    is how a tester found this.  MacRoman puts the em dash at 0xD1, the curly
    quotes at 0xD2 to 0xD5 and the ellipsis at 0xC9, so encoding properly is
    the whole fix -- there is no table of symbol names to maintain.
    """
    return text.translate(_FOLD).encode("mac_roman", "panthera_fold")


#: Text shorter than this is never split.
#:
#: Low, because characters are a poor proxy for how long a thing takes to
#: say and the error runs the wrong way.  "Saved Messages, nvda dot zip,
#: Sent 2026-08-17 at 11:08" is 80 characters and 2.98 seconds of audio --
#: dates, filenames and version numbers expand about fourfold -- and it cost
#: 193 ms of silence before a word of it was heard.  Measured across a list:
#: 27 to 36 characters per second of audio, against 40 to 60 for prose.
#:
#: What is under this really is short: a word, a control type, a state.
SPLIT_MIN = 60

#: How much has to accumulate before a boundary may close the *first* piece.
#:
#: Small, because this piece alone is what the user waits for, and because
#: character count is not what the wait is made of.  Measured: 275 characters
#: of URL became 12.46 s of audio and 789 ms of silence first, while 233
#: characters of Arabic became 2.65 s.  The engine renders at a steady 60 to
#: 65 ms per second of audio, so the wait is set by how much *audio* has to
#: exist before any of it may be heard -- and a short first piece is short in
#: audio whatever the text is made of.
SPLIT_FIRST = 12

#: And for every piece after it.  Larger, because these are rendered while
#: the piece before them is still playing, so their cost is hidden -- but not
#: unbounded, or the piece after a short one would not be ready in time.
SPLIT_TARGET = 160

#: How far past SPLIT_TARGET a sentence end is still worth waiting for.
#:
#: Inside this, the split goes there and the prosody is untouched.  Past it,
#: a phrase boundary is taken instead: a comma the engine was already going
#: to break at loses its continuation rise and gains a full stop, which is a
#: real change, and a smaller one than a second of silence.
SPLIT_SLACK = 200

#: Closing quotes and brackets that may sit between a sentence terminator
#: and the space after it.  Built through re.escape rather than written as
#: a character class, so the source carries no escape for Python to read
#: differently from the regex engine.
_CLOSERS = ")]}\"”’'»"
_SENTENCE_END = re.compile("[.!?][" + re.escape(_CLOSERS) + "]*\\s+")

#: Words that end in a full stop without ending a sentence.
_ABBREVIATIONS = frozenset("""
mr mrs ms dr prof rev hon sr jr st mt gen col sgt lt capt
ave rd blvd dept est fig vol no nos pp al vs etc approx
inc ltd co corp univ dept
jan feb mar apr jun jul aug sep sept oct nov dec
mon tue tues wed thu thur thurs fri sat sun
am pm
""".split())


def _sentenceStarts(text):
    """-> offsets in `text` where a new sentence demonstrably begins.

    **Conservative on purpose.**  A boundary that is not really one is heard
    as a full stop in the middle of a sentence, which is exactly the fault
    this driver joins NVDA's fragments together to avoid -- "narrowing.
    budgets", at the wrapped line boundaries.  Everything doubtful is left
    alone here, which costs latency on that utterance and never costs a wrong
    reading.
    """
    for m in _SENTENCE_END.finditer(text):
        start = m.end()
        if start >= len(text):
            break
        if text[m.start()] == ".":
            word = re.search(r"[\w']+$", text[:m.start()])
            if word:
                w = word.group(0)
                # A single letter before a full stop is an initial or part of
                # an abbreviation, never the end of a sentence: "J. Smith",
                # "U.S. Army", "e.g. this one".
                if len(w) == 1 or w.lower() in _ABBREVIATIONS:
                    continue
        nxt = text[start]
        # What follows has to be able to open a sentence.  A lower case
        # letter after a full stop is an abbreviation this list does not
        # know about -- "in Leopard's. the engine names", from a real post.
        # `islower` rather than `isupper` so that Arabic, Hebrew and CJK,
        # which are neither, are not excluded from splitting.
        if nxt.islower() or not (nxt.isalnum() or nxt in "\"'“‘(["):
            continue
        yield start


#: Punctuation the engine already breaks a phrase at.  Weaker than a
#: sentence end and used only when no sentence end is near -- see
#: SPLIT_SLACK.  A number never matches: "1,000" and "18:05" have no space
#: after the mark, and the space is required.
_PHRASE_MARKS = ",;:—–"
_PHRASE_END = re.compile("[.!?" + re.escape(_PHRASE_MARKS) + "][ "
                         + re.escape(_CLOSERS) + "]*\\s+")


def _phraseStarts(text):
    """-> offsets where a new phrase begins: sentence ends and the marks above.

    The sentence rules still apply to a full stop, so an abbreviation is no
    more a phrase boundary here than it was a sentence one.
    """
    sentences = set(_sentenceStarts(text))
    for m in _PHRASE_END.finditer(text):
        start = m.end()
        if start >= len(text):
            break
        if text[m.start()] in ".!?":
            if start in sentences:
                yield start
            continue
        yield start


def _splitUtterance(text):
    """-> `text` in pieces that rejoin to exactly `text`.

    The first piece is cut as early as a boundary allows, because it is the
    only one the user waits for.  Every piece after it is rendered while the
    one before it plays, so those are cut long, and only at a sentence end
    unless none is anywhere near.

    Never fewer characters than went in, and never a cut anywhere except a
    boundary the text already had, so an utterance with none comes back whole
    and is rendered exactly as it was before.
    """
    if len(text) <= SPLIT_MIN:
        return [text]
    sentences = list(_sentenceStarts(text))
    phrases = list(_phraseStarts(text))

    def firstPast(offsets, lower, upper=None):
        for off in offsets:
            if off >= lower and (upper is None or off <= upper):
                return off
        return None

    pieces = []
    at = 0
    want = SPLIT_FIRST
    while True:
        # A sentence end if there is one within reach, a phrase boundary only
        # if there is not.
        cut = firstPast(sentences, at + want, at + want + SPLIT_SLACK)
        if cut is None:
            cut = firstPast(phrases, at + want)
        if cut is None or cut <= at:
            break
        pieces.append(text[at:cut])
        at = cut
        want = SPLIT_TARGET
    pieces.append(text[at:])
    return [piece for piece in pieces if piece.strip()]


def _joinFragments(parts):
    """Join the pieces of one utterance back into a sentence.

    A space goes in only where neither side already has one: NVDA's fragments
    usually carry their own spacing, and doubling it is harmless, but "link"
    followed by "Home" with nothing between them would otherwise be handed to
    the engine as "linkHome" and spoken as one word.
    """
    out = []
    for part in parts:
        if out and part[:1].strip() and out[-1][-1:].strip():
            out.append(" ")
        out.append(part)
    return "".join(out)


#: The end of a sentence, in the shape NVDA itself uses
#: (`speech/speechWithoutPauses.py`), so what we count as a boundary is what
#: NVDA counted as one when it decided to hand the line over.
SENTENCE_END_RE = re.compile(u"[.!?][\"'”’)\\]]*(?:\\s|$)")


def _sentenceEnds(text):
    """How many sentences have finished inside this text.

    **Two is the number that matters.**  A breath is not a gap the engine
    inserts, it is a unit it selects, and it only ever selects one at a
    sentence boundary *inside* one utterance.  So one sentence -- however long
    -- can never breathe, and two breathe once, at the join.  Measured on Alex:
    1 sentence -> 0 breaths, 2 -> 1, 3 -> 2, 6 -> 5.
    """
    return len(SENTENCE_END_RE.findall(text))


# ---------------------------------------------------------------------------
# NVDA speaks the brackets before we ever see them.
#
# At punctuation level "most" or "all", NVDA's `speak()` runs every string
# through symbol processing *before* the synthesizer gets it, and `[` and `]`
# are level "most" -- so `[[pbas 60]]` arrives here as
#
#     " left bracket  left bracket pbas 60 right bracket  right bracket "
#
# and the engine, quite reasonably, reads it out.  That is what "it actually
# says the command" means, and it is why the same tag works for one person
# and not the next: the default level is "some", where brackets pass through
# untouched.  (Adison found it; the driver-level tests could not, because
# they hand text to the engine directly.)
#
# So when commands are on, the driver puts the brackets back.  The names come
# from NVDA's own symbol table for the current language, so this is not an
# English-only trick; the English pair is the fallback for outside NVDA.
# ---------------------------------------------------------------------------

def rebuild_commands(text, left="left bracket", right="right bracket"):
    """Turn spoken bracket pairs back into `[[...]]`.

    Two of the left name, the command, two of the right name -- with any
    amount of whitespace between, since NVDA pads each replacement with a
    space on both sides.  Case-insensitive, because a locale's symbol name
    may be capitalised and the engine's parser does not care.
    """
    if not text or not left or not right:
        return text
    pattern = re.compile(
        r"\s*%s\s+%s\s+(.*?)\s+%s\s+%s\s*"
        % (re.escape(left), re.escape(left), re.escape(right), re.escape(right)),
        re.IGNORECASE)
    return pattern.sub(lambda m: " [[%s]] " % m.group(1).strip(), text)
