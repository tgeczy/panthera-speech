# -*- coding: utf-8 -*-
"""NVDA speaking with Mac OS X 10.5 Leopard's MacinTalk, as native code.

The driver itself is `_panthera/pantheradriver.py`, which Lion's driver uses
unchanged.  What is left here is what is actually Leopard's: a name, a
description, the module that finds its folder, and a table of per-voice levels
measured on its own recordings.

**The module name is load-bearing and can never change.**  NVDA keys every
stored setting by it, so a rename would silently reset everybody's voice, rate
and pitch.  That is also why the shared body lives one folder down: NVDA scans
`synthDrivers/` for modules with a `SynthDriver` class in them, and a second
one up here would be a second synthesizer in the list.
"""
import subprocess                                             # noqa: F401

# Imported out of the package rather than off `sys.path`, and that is not
# cosmetic.
#
# Every NVDA add-on shares one `sys.modules`.  This driver and its Tiger
# sibling both put their private folder on `sys.path`, and both used to
# `import tree`, so whichever loaded first won and the second silently got the
# first one's module.  Leopard read tigerspeech-data, ran tiger_host.exe and
# offered Tiger's twenty-three voices under Leopard's name -- working
# perfectly, and completely wrong.  Nothing failed, which is why it took a
# user noticing the wrong voices to see it.
#
# A prefix no older add-on used was the answer for as long as this folder was
# reached through `sys.path`.  A package ends the hazard rather than naming its
# way around it: these are `synthDrivers._panthera.*` and cannot collide in the
# flat namespace at all, so somebody still running the old `tigerspeech` or
# `leopardspeech` add-on beside this one is no longer a hazard.  The reasoning
# is kept in full in `_panthera/__init__.py`.
import os

from ._panthera import bridge
from ._panthera import pantheradriver, pantheraleopard

#: Re-exported because finding the engine is the same question the global
#: plugin asks, and two copies of a lookup are two chances to disagree about
#: where the engine is.
_HERE = os.path.dirname(os.path.abspath(__file__))

HOST_EXE = pantheraleopard.HOST_EXE
HOST_DLL = pantheraleopard.HOST_DLL
find_tree = pantheraleopard.find_tree
engine_paths = pantheraleopard.engine_paths
read_voices = pantheraleopard.read_voices
config_base = pantheraleopard.config_base

#: Leopard's measured per-voice levels.  Named here rather than in the shared
#: body so that nothing can apply them to a bank they were not measured on.
VOLUME_NORM = pantheradriver.VOLUME_NORM_LEOPARD


class SynthDriver(pantheradriver.PantheraDriver):
    name = "leopardspeech"
    description = _("Leopard speech (Alex, MacinTalk 3.6)")

    TREE = pantheraleopard
    TITLE = "Leopard speech"
    DISC = "Mac OS X 10.5 install disc"
    EXTRACTOR = "extract_leopard.py"
    VOLUME_NORM = pantheradriver.VOLUME_NORM_LEOPARD

    #: Never retire a cancelled render early to reach queued speech.  On this
    #: generation the cancel event ends a render in tens of milliseconds and a
    #: replacement means reloading Alex's 701 MB bank, so the handoff trade
    #: that pays on Lion and Snow Leopard only re-creates Timothy's glitch
    #: here: the executable running again on an ordinary keystroke.
    HANDOFF_GRACE = None

    @classmethod
    def check(cls):
        """**Listed only when there is an engine to run.**

        Both halves of this were once right and they disagreed.  Tiger and
        Leopard were always offered and explained themselves in a dialog when
        chosen, because hiding them had left people with an add-on, no
        synthesizer and nothing to go on.  Lion, added later, listed itself
        only when it had an engine, because nobody should arrow past
        synthesizers that cannot speak to reach one that can.

        Timothy Wynn found the combination: install with no data at all and
        `Leopard speech (Alex, MacinTalk 3.6)` is sitting there, selectable and
        mute, while Lion -- equally dataless -- is not.

        `synthDrivers/pantheraspeech.py` is what makes hiding safe here.  When
        no generation can speak it takes their place, as one entry, and opens
        the tool that fixes it.  So there is still a route to the explanation,
        which is the thing whose absence made hiding wrong the first time.
        """
        return pantheraleopard.usable()

#: The class NVDA loads, which is this one everywhere except a secure screen.
#:
#: **Still one synthesizer, not two.**  NVDA finds one `SynthDriver` per
#: module; this rebinds the name, it does not add an entry.  The name, the
#: description and every stored setting are unchanged either way -- see
#: `_panthera/bridge.py` for when the substitution happens and why.
SynthDriver = bridge.driverFor(SynthDriver, "leopardspeech", _HERE, HOST_EXE, HOST_DLL)
