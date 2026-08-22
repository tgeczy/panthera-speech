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

#: **Not measured yet, so nothing is turned up.**  1.0 is the level every
#: voice already has, and it is the only safe placeholder: a voice nobody has
#: measured might already be at the engine's ceiling, and guessing high turns
#: it into clipping rather than loudness.  Leopard's table is deliberately not
#: copied here even though twenty-three of the names match -- the recordings
#: behind them are different recordings, and Lion's Alex is a 422 MB bank
#: where Leopard's is 701 MB.  `leopard/tools/volume_table.py` is what fills
#: this in, one probe text at a time.
VOLUME_NORM = {}


class SynthDriver(pantheradriver.PantheraDriver):
    name = "lionspeech"
    description = _("Lion speech (Alex, MacinTalk 4.0)")

    TREE = pantheralion
    TITLE = "Lion speech"
    DISC = "Mac OS X 10.7 install image"
    EXTRACTOR = "extract_lion.py"
    VOLUME_NORM = VOLUME_NORM

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
