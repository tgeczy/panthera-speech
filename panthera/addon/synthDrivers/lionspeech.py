# -*- coding: utf-8 -*-
"""NVDA speaking with Mac OS X 10.7 Lion's MacinTalk, as native code.

The driver is `_panthera/pantheradriver.py`, shared with Leopard.  What is
here is what is actually Lion's.

**MacinTalk 4.0**, by Apple's own reckoning: the engine carries
`/SourceCache/SpeechSynthesis_MacInTalk/SpeechSynthesis-4.0.74/` in its debug
paths where Leopard's carries `SpeechSynthesis-3.6.59`.  The project had been
split out under its own name by then, which fits what else 10.7 did to
speech -- it is the release that added the Vocalizer voices, and the last one
that shipped a MacinTalk at all.

What made 10.7 hard is all in the host, not here: compressed dyld info instead
of relocations, the C++ ABI moved into `libc++abi.dylib`, `stat$INODE64`,
rate and pitch moved to `SESetSpeechProperty`, and a real FFT because 10.7
correlates its time-scaling in the frequency domain.  By the time the driver
is involved, 10.7 answers the same calls 10.5 does.
"""
import os
import subprocess                                             # noqa: F401
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ENGINE_DIR = os.path.join(_HERE, "_panthera")
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

# Imported under a `panthera` prefix for the reason `leopardspeech.py` sets
# out at length: every NVDA add-on shares one `sys.modules`, and a generic
# name here once had Leopard speaking in Tiger's voices.
import pantheradriver                                         # noqa: E402
import pantheralion                                           # noqa: E402

HOST_EXE = pantheralion.HOST_EXE
find_tree = pantheralion.find_tree
engine_paths = pantheralion.engine_paths
read_voices = pantheralion.read_voices
config_base = pantheralion.config_base

#: How far each voice may be turned up, so that one slider position means
#: roughly one loudness whichever voice is speaking.  Built by
#: `tools/volume_table.py lion`, worst case across five probe texts; the
#: method is documented there and in `pantheradriver.VOLUME_NORM_LEOPARD`.
#:
#: **Measured on Lion's own recordings, and it was worth the trouble.**
#: Twenty-three of these names also appear in Leopard's table, so copying it
#: would have looked reasonable and been wrong where it matters most: Leopard
#: gives Alex 1.80 and Lion gives him **1.19**.  Lion's Alex is a different,
#: smaller bank -- 422 MB against 701 -- and it is already only 2 dB below
#: Bruce where Leopard's is nearly 8 dB below.  Leopard's factor applied here
#: would have asked for 3.6 dB the voice does not have and clipped him.
#:
#: The set is more even than Leopard's to begin with: 5.3 dB of spread after
#: normalisation against Leopard's 6.6, excluding Whisper, which is supposed
#: to be quiet and keeps its character.
VOLUME_NORM = {
    "Agnes":      1.00,
    "Albert":     1.70,
    "Alex":       1.19,
    "BadNews":    1.80,
    "Bahh":       1.70,
    "Bells":      1.70,
    "Boing":      1.80,
    "Bruce":      1.00,
    "Bubbles":    1.70,
    "Cellos":     1.75,
    "Deranged":   1.70,
    "Fred":       1.80,
    "GoodNews":   1.69,
    "Hysterical": 1.70,
    "Junior":     1.77,
    "Kathy":      1.72,
    "Organ":      1.67,
    "Princess":   1.65,
    "Ralph":      1.74,
    "Trinoids":   1.70,
    "Vicki":      1.17,
    "Victoria":   1.00,
    "Whisper":    1.80,
    "Zarvox":     1.70,
}


class SynthDriver(pantheradriver.PantheraDriver):
    name = "lionspeech"
    description = _("Lion speech (Alex, MacinTalk 4.0)")

    #: **Interim, until panthera-speech#6 is solved.**
    #:
    #: 10.7 accepts `[[inpt TUNE]]` and then ignores every `{D …; P …}`
    #: annotation -- measured, `m{D 500}` and a bare `m` render identically at
    #: 3358 frames -- and a malformed phoneme after `[[inpt PHON]]` faults
    #: inside Apple's own `SLLexerImpl::Error` and takes the host with it.
    #: Both work on 10.5 and 10.6.
    #:
    #: So the mode switches are stripped here and every other embedded command
    #: is left alone: `[[slnc]]`, `[[rate]]`, `[[volm]]` and `[[char]]` are all
    #: measured working on 10.7, and dropping the whole setting -- the first
    #: thing considered -- would have taken those with them for a fault that
    #: is not theirs.
    INPUT_MODES_WORK = False

    TREE = pantheralion
    TITLE = "Lion speech"
    DISC = "Mac OS X 10.7 install image"
    EXTRACTOR = "extract_lion.py"
    VOLUME_NORM = VOLUME_NORM

    #: Retire a cancelled render early when newer speech is queued.  Measured
    #: here as on Snow Leopard: 345 to 947 ms holds against tens once the
    #: worker is free -- 10.7 never stops its audio graph, so a cancelled
    #: render sits out its quiet window with the worker held.  Leopard
    #: answers `None`; see both comments in `pantheradriver`.
    HANDOFF_GRACE = 0.06

    @classmethod
    def check(cls):
        """**Listed only when there is an engine to run** -- unlike its siblings.

        Tiger and Leopard are always offered, and explain themselves in a
        dialog if they cannot start.  That was the right answer for them: the
        alternative had people install the add-on, extract nothing, find no
        synthesizer and have nothing at all to go on.

        Four generations changes the arithmetic.  Somebody who has only Tiger
        and Leopard data should not arrow past two synthesizers that cannot
        speak to reach the two that can, and the list is read aloud one item
        at a time.

        What makes hiding safe now, and did not exist then, is that there is
        another route to the explanation: the Tools menu report says what was
        looked for and what was missing, for every generation including the
        hidden ones, and the first-run message points at it by name.  Hiding
        without that would be the old mistake again.
        """
        return pantheralion.usable()
