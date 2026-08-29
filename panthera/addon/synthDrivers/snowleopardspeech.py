# -*- coding: utf-8 -*-
"""NVDA speaking with Mac OS X 10.6 Snow Leopard's MacinTalk, as native code.

The driver is `_panthera/pantheradriver.py`, shared with Leopard and Lion.
What is here is what is actually Snow Leopard's.

**MacinTalk 3.10**, by Apple's own reckoning: the engine carries
`SpeechSynthesis-3.10.35` in its debug paths where Leopard's carries `3.6.59`
and Lion's `4.0.74`.  A late 3.x rather than the rewrite 4.0 was, and the
measurements agree with the numbering -- 10.6 speaks through Leopard's API,
with Lion's threading underneath it.

Nothing in the host was written for this generation.  10.6 binds the way 10.7
does, talks the way 10.5 does, and every piece of that already existed; what
it took was two bug fixes in code that had only ever met one of the two.
`_panthera/pantherasnowleopard.py` has the table.
"""
import subprocess                                             # noqa: F401

# Imported out of the package for the reason `leopardspeech.py` sets out at
# length: every NVDA add-on shares one `sys.modules`, a generic name here once
# had Leopard speaking in Tiger's voices, and a package cannot collide in that
# namespace at all.
import os

from ._panthera import bridge
from ._panthera import pantheradriver, pantherasnowleopard

_HERE = os.path.dirname(os.path.abspath(__file__))

HOST_EXE = pantherasnowleopard.HOST_EXE
HOST_DLL = pantherasnowleopard.HOST_DLL
find_tree = pantherasnowleopard.find_tree
engine_paths = pantherasnowleopard.engine_paths
read_voices = pantherasnowleopard.read_voices
config_base = pantherasnowleopard.config_base

#: How far each voice may be turned up, so that one slider position means
#: roughly one loudness whichever voice is speaking.  Built by
#: `tools/volume_table.py snowleopard`, worst case across five probe texts;
#: the method is documented there and in `pantheradriver.VOLUME_NORM_LEOPARD`.
#:
#: **Measured on 10.6's own recordings, like the other two, and this is the
#: generation that proves why.**  Twenty-three of these names appear in
#: Leopard's table and twenty-four in Lion's, so copying either would have
#: looked entirely reasonable -- and both would have been wrong about the one
#: voice anybody installs this for.  Alex measures **1.46** here, against
#: Leopard's 1.80 and Lion's 1.19: Snow Leopard's bank is the shrunken 400 MB
#: recording, so Leopard's factor asks for gain it does not have, and Lion's
#: leaves 1.8 dB of it unused.  Neither neighbour is a guide, and Snow Leopard
#: sits between them because it is between them.
#:
#: Spread after normalisation is 6.3 dB, excluding Whisper -- between
#: Leopard's 6.6 and Lion's 5.3.  Whisper is supposed to be quiet and keeps
#: its character.
VOLUME_NORM = {
    "Agnes":      1.00,
    "Albert":     1.70,
    "Alex":       1.46,
    "BadNews":    1.80,
    "Bahh":       1.70,
    "Bells":      1.70,
    "Boing":      1.70,
    "Bruce":      1.00,
    "Bubbles":    1.70,
    "Cellos":     1.70,
    "Deranged":   1.70,
    "Fred":       1.80,
    "GoodNews":   1.80,
    "Hysterical": 1.70,
    "Junior":     1.80,
    "Kathy":      1.73,
    "Organ":      1.70,
    "Princess":   1.70,
    "Ralph":      1.71,
    "Trinoids":   1.70,
    "Vicki":      1.19,
    "Victoria":   1.00,
    "Whisper":    1.80,
    "Zarvox":     1.70,
}


class SynthDriver(pantheradriver.PantheraDriver):
    name = "snowleopardspeech"
    description = _("Snow Leopard speech (Alex, MacinTalk 3.10)")

    TREE = pantherasnowleopard
    TITLE = "Snow Leopard speech"
    DISC = "Mac OS X 10.6 install disc"
    EXTRACTOR = "extract_snowleopard.py"
    VOLUME_NORM = VOLUME_NORM

    #: Retire a cancelled render early when newer speech is queued.  Measured
    #: on this generation: cancelled responses held the worker 345 to 947 ms
    #: while the queued utterance would render in tens once free -- the
    #: dispatch-source teardown, not the driver.  Leopard answers `None`; see
    #: both comments in `pantheradriver`.
    HANDOFF_GRACE = 0.06

    @classmethod
    def check(cls):
        """**Listed only when there is an engine to run**, like its siblings.

        Every generation answers this the same way now, and the reasoning is
        in `pantheraspeech.py`: the list is read aloud one item at a time, so
        nobody should arrow past synthesizers that cannot speak to reach one
        that can -- and when *none* of them can, one placeholder stands in for
        all four and opens the tool that fixes it.

        Four generations is what made that the only workable answer.  Two
        dataless entries was arguable; three would not have been.
        """
        return pantherasnowleopard.usable()

#: The class NVDA loads, which is this one everywhere except a secure screen.
#:
#: **Still one synthesizer, not two.**  NVDA finds one `SynthDriver` per
#: module; this rebinds the name, it does not add an entry.  The name, the
#: description and every stored setting are unchanged either way -- see
#: `_panthera/bridge.py` for when the substitution happens and why.
SynthDriver = bridge.driverFor(SynthDriver, "snowleopardspeech", _HERE, HOST_EXE, HOST_DLL)
