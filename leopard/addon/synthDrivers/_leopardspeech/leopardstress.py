# -*- coding: utf-8 -*-
"""Words the engine stresses wrongly, and the respellings that fix them.

Alex says **"cologne"** for "colon" whenever another word follows it in the
same phrase. The phonemes it picks are right -- the engine's own narration
gives `k OW l IX n`, which is KOH-lin -- so this is not letter-to-sound. It is
prosody. Phrase-final, the word carries a pitch rise to 277; with any word
after it the contour goes flat at 187, the first syllable reduces, and the
stress is heard on the second.

Measured, on our own renders, with Whisper adjudicating:

    C colon                 -> See colon          OK
    the colon is fine       -> The colon is fine  OK
    C colon backslash       -> See COLOGNE backslash
    colon slash             -> COLOGNE slash
    C colon, backslash      -> See colon, backslash   (a comma rescues it)

Which is why it matters far more than a curiosity about Windows paths: NVDA
speaks every timestamp as "five colon eighteen colon forty five", and every
one of those colons has a word after it.

**There is no engine-side fix.** Eleven of the engine's own tuning parameters
were tried on the failing phrase -- `UnitCost.AccentCostWeight`, both
`ToBIPitch` phrase prominences, `LowFinalProm`, `ToBIPitch.DowngradeVerbs`,
`VerbDowngradeFactor`, `DownStepMonosyllabicPhrases`, `WordCost.KeepWithNext`,
`Word.Threshold` -- and all but one left the render byte-for-byte identical.
The phrasing table cannot reach it either: Lion's larger `TuplesEng`, which
does improve phrasing elsewhere, matches **zero** rows on this text. The
de-accenting is structural.

So the repair is in the text, before the engine sees it, exactly as it is for
numbers -- and for the same reason. See [[pronunciation-policy]]: the engine
having a *wrong* answer is normally its character and none of our business.
The exception here is that "cologne" is not a shade of pronunciation, it is a
different word, and the one that appears is never the one meant -- a screen
reader saying "colon" is naming a punctuation mark, never an organ.

There is a second reason, and it is the better one. **Nobody heard this voice
raw.** Alex reached people through VoiceOver, which brought its own symbol
handling, and Tomi's recollection is that VoiceOver did not suffer from this
anything like as often -- which would mean Apple's own screen reader was
already working around it, or never produced the word in this position at all.
That is unverified and the VM has no Alex to settle it with. But it reframes
what faithfulness even means here: the engine is faithfully reproduced either
way, and a bare engine with no symbol layer in front of it is not what this
voice ever sounded like to a listener. Correcting it is closer to the original
experience than shipping it uncorrected, not further from it.

**Chosen by measurement, not by ear-guessing.** Four respellings were rendered
and transcribed:

    colen    -> See COLON backslash    OK
    kohlin   -> See COLON backslash    OK
    kolon    -> See call on backslash
    koh lon  -> sequel on backslash

and `colen` was checked for harm where the word was already right:

    C colen (phrase-final)      -> C. Colin       still correct
    the colen is fine           -> The colon is fine
    Bruce, Victoria             -> See colon backslash, unchanged
    Fred                        -> unchanged from his own baseline

Whole words only. "semicolon" is not affected by the bug in the first place,
and must not be rewritten by a rule aimed at "colon".
"""
import re

#: Word -> respelling. Deliberately tiny.
#:
#: The bar for adding a line here is high: the word has to come out as a
#: *different word*, not merely as an odd reading, and the replacement has to
#: be measured on the voices that already say it correctly as well as on the
#: one that does not. Anything short of that belongs in the user's own NVDA
#: speech dictionary, where the user holds the pen.
RESPELLINGS = {
    "colon": "colen",
}

#: Whole words, case-insensitive, and the case of what was there is put back
#: below -- NVDA sends "Colon" at the start of a sentence.
_WORDS = re.compile(r"\b(%s)\b" % "|".join(sorted(RESPELLINGS, key=len,
                                                  reverse=True)),
                    re.IGNORECASE)


def _match(m):
    was = m.group(0)
    now = RESPELLINGS[was.lower()]
    if was.isupper():
        return now.upper()
    if was[:1].isupper():
        return now[:1].upper() + now[1:]
    return now


def fix(text):
    """Respell the words the engine stresses wrongly.

    Returns the text unchanged when there is nothing to do, which is almost
    always -- the caller can hand the result straight on.
    """
    if not text:
        return text
    return _WORDS.sub(_match, text)
