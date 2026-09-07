# -*- coding: utf-8 -*-
"""One entry in the synthesizer list when none of the engines has any data.

**This synthesizer never speaks.**  Selecting it opens the speech data tool and
then fails, so NVDA falls back to whatever was speaking a moment ago.  That is
the whole of its job.

Proposed by Timothy Wynn, who had just installed the add-on with no data at all
and found `Leopard speech (Alex, MacinTalk 3.6)` sitting in the list, ready to
be chosen and unable to say a word:

    "Why not make it so that if no synth data is available, populate Panthera
    speech as the placeholder?  And when one or more synth is present, it will
    get rid of the other placeholders."

The add-on used to answer this two different ways at once.  Tiger and Leopard
were always listed and explained themselves in a dialog when chosen, because
the alternative had been people installing the add-on, finding no synthesizer
at all and having nothing to go on.  Lion, added later, listed itself only when
it had an engine, reasoning that nobody should have to arrow past synthesizers
that cannot speak to reach one that can -- the list is read aloud, one item at
a time.

Both were right about their own half and the combination was the worst of it:
somebody with no data heard two dead entries and not the third, and somebody
with only Lion data heard two dead entries in front of their working one.

So: **every generation is listed only when it can speak, and this stands in for
all of them when none can.**  One entry instead of three, and the one entry
leads to the tool that fixes the problem rather than to a message about it.

Nothing here is a fallback for a *broken* generation.  If Tiger has data and
Lion does not, Tiger is listed, Lion is not, and this is not listed either --
the Tools menu report is where you find out why Lion is missing, and the
first-run message points at it by name.
"""
import os

from autoSettingsUtils.driverSetting import DriverSetting      # noqa: F401
from logHandler import log
from synthDriverHandler import SynthDriver

# Out of the package for the reason `leopardspeech.py` sets out at length:
# every add-on shares one `sys.modules`, an older `tigerspeech` or
# `leopardspeech` add-on may still be running beside this one, and a package
# cannot collide in that namespace at all.
from ._panthera import (pantheraleopard, pantheralion, pantherasnowleopard,
                        pantheratiger, pantheratrees)

#: Where the global plugin leaves the callable that opens the speech data
#: tool.  A string rather than an import: a synth driver reaching into a global
#: plugin is a bet on load order, and this one has somewhere to fall back to.
_OPENER = "_macosxSpeechDataOpener"

_GENERATIONS = (pantheratiger, pantheraleopard, pantherasnowleopard,
                pantheralion)


def _anythingUsable():
    """-> True if any generation could actually speak.

    Each `usable()` touches the disk, and a generation that raises is not a
    reason to hide the placeholder -- it is a reason to show it.
    """
    for tree in _GENERATIONS:
        try:
            if tree.usable():
                return True
        except Exception:
            log.debugWarning("pantheraspeech: %s could not be checked"
                             % tree.__name__, exc_info=True)
    return False


def _dataFolderRefused():
    """-> (path, why) for a declared data folder this account cannot open.

    Every place a person can put speech data and then be unable to read it:
    each generation's folder under NVDA's configuration directory, and each
    folder the SAPI side was pointed at -- which is the one that actually
    bites, because that folder can be anywhere, including inside somebody
    else's profile.

    Absence is not failure and is not reported: a machine with no data at all
    is the ordinary first run, and this exists only to separate that from
    data which is present and out of reach.
    """
    candidates = []
    for tree in _GENERATIONS:
        try:
            candidates.append(tree.config_dir())
            candidates.extend(
                pantheratrees.sapi_roots(os.path.basename(
                    tree.CONFIG_DIRNAME)))
        except Exception:
            log.debugWarning("pantheraspeech: could not list %s's folders"
                             % tree.__name__, exc_info=True)
    return pantheratrees.unreadable(candidates)


def _offerTheTool():
    """Open the speech data tool, or failing that the folder it fills.

    Queued by the caller, never run from `__init__`: a modal dialog there
    would stall the synthesizer switch with speech half torn down.
    """
    try:
        import globalVars
        opener = globalVars.__dict__.get(_OPENER)
    except Exception:
        opener = None
    if opener is not None:
        try:
            if opener():
                return
        except Exception:
            log.error("pantheraspeech: the speech data tool would not open",
                      exc_info=True)
    # No plugin, or it could not draw its dialog.  Somebody who just chose
    # this deserves *something*, and the folder is where the data goes.
    try:
        folder = pantheraleopard.config_base()
        os.makedirs(folder, exist_ok=True)
        os.startfile(folder)
    except Exception:
        log.error("pantheraspeech: could not open the speech data folder",
                  exc_info=True)


class SynthDriver(SynthDriver):
    """Listed only when nothing else in the add-on can speak."""

    #: The module name, and NVDA keys stored settings by it.  This one has no
    #: settings to lose, but it must never collide with a real driver's.
    name = "pantheraspeech"
    # Translators: the name of the placeholder synthesizer, shown only when no
    # Mac OS X speech data has been installed yet.
    description = _("Panthera speech (no Mac OS X speech data yet)")

    supportedSettings = ()

    @classmethod
    def check(cls):
        return not _anythingUsable()

    def __init__(self):
        """Offer the tool, then refuse to load.

        **Raising is the safe half.**  NVDA catches it, says so, and keeps the
        synthesizer that was already speaking, so choosing this can never leave
        somebody in silence -- which for a screen reader is the only failure
        that actually matters.
        """
        super().__init__()
        #: **Offering the tool must never change the refusal.**  Any exception
        #: raised here would escape in place of the `RuntimeError` below, and
        #: while NVDA falls back either way, what it reports would no longer
        #: say why.
        #:
        #: Not hypothetical: `wx.CallAfter` asserts that an application object
        #: exists, and outside NVDA there is none -- which surfaced the moment
        #: a real wxPython was installed beside the suite's fake one.
        try:
            import wx
            wx.CallAfter(_offerTheTool)
        except Exception:
            log.debugWarning("pantheraspeech: could not offer the speech data "
                             "tool", exc_info=True)
        #: **"Not installed" and "installed and out of reach" are different
        #: problems and only one of them is the person's to fix by
        #: installing.**  Deciding here rather than in `check()` on purpose:
        #: the folder is touched once, when somebody has actually chosen this,
        #: rather than on every build of the synthesizer list.
        try:
            path, why = _dataFolderRefused()
        except Exception:
            log.debugWarning("pantheraspeech: could not check the speech data "
                             "folders", exc_info=True)
            path = why = None
        if path:
            raise RuntimeError(
                "the Mac OS X speech data folder cannot be opened: %s (%s). "
                "Windows will not let this account read it -- move the data "
                "somewhere every account can, such as "
                "%%ProgramData%%\\macintalk-data, or into NVDA's own "
                "configuration folder." % (path, why))
        raise RuntimeError("no Mac OS X speech data is installed yet")

    def terminate(self):
        pass

    def speak(self, speechSequence):
        pass

    def cancel(self):
        pass

    def pause(self, switch):
        pass
