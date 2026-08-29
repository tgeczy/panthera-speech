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
import subprocess                                             # noqa: F401

# Imported out of the package for the reason `leopardspeech.py` sets out at
# length: every NVDA add-on shares one `sys.modules`, a generic name here once
# had Leopard speaking in Tiger's voices, and a package cannot collide in that
# namespace at all.
import os

from ._panthera import bridge
from ._panthera import pantheradriver, pantheralion

_HERE = os.path.dirname(os.path.abspath(__file__))

HOST_EXE = pantheralion.HOST_EXE
HOST_DLL = pantheralion.HOST_DLL
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

    #: True since 1.1, and it was False for two shipped releases with two
    #: separate faults behind it -- see panthera-speech#6 for both.
    #:
    #: The tune annotations died in `SLHomographCopyTune`, whose melody went
    #: through two CF constructors the host had left as thunks; the issue's
    #: repro case renders 12094 frames against Leopard's 12096 now.  And the
    #: malformed-phoneme crash in `SLLexerImpl::Error` was the engine's error
    #: handler -- a stack block the host's `Block_copy` never actually copied
    #: -- firing from a dead frame; with the Blocks ABI done honestly the
    #: engine's own recovery handles bad input the way Apple designed it.
    INPUT_MODES_WORK = True

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

#: The class NVDA loads, which is this one everywhere except a secure screen.
#:
#: **Still one synthesizer, not two.**  NVDA finds one `SynthDriver` per
#: module; this rebinds the name, it does not add an entry.  The name, the
#: description and every stored setting are unchanged either way -- see
#: `_panthera/bridge.py` for when the substitution happens and why.
SynthDriver = bridge.driverFor(SynthDriver, "lionspeech", _HERE, HOST_EXE, HOST_DLL)
