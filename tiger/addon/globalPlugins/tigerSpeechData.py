# -*- coding: utf-8 -*-
"""Tell the user the engine folder is empty, and keep telling them.

The engine is not ours to ship, so a fresh install cannot speak until the user
supplies it.  That needs saying somewhere, and a settings panel would be a lot
of scaffolding around one folder -- so it is a dialog with a button that opens
the folder.

**It asks again on every start-up until either the engine is there or the user
says no.**  The sibling ROM add-on originally asked exactly once ever, and
recorded that it had asked *before* the dialog was even shown: anyone who
dismissed it without reading -- which is most people, for a dialog that
arrives six seconds after start-up -- never saw it again and was left with a
synthesizer that silently refused to appear in the list.  Repeating a question
is a much smaller harm than that, and "No" is honoured permanently.

The check runs off the main thread after a short delay: NVDA is still starting
up when global plugins load, and a modal dialog thrown at that moment is a
good way to make a screen reader look broken.
"""
import os
import sys
import threading

import globalPluginHandler
import globalVars
import gui
import wx
from logHandler import log

_ADDON = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ENGINE_DIR = os.path.join(_ADDON, "synthDrivers", "_tigerspeech")
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

import tree                                                   # noqa: E402

#: Written only when the user explicitly says "stop asking".
_MARKER = "do-not-ask"

_MESSAGE = (
    "Tiger-speech has no engine to run yet.\n\n"
    "This add-on ships no part of Apple's software. You supply it from your "
    "own Mac OS X 10.4 install disc, and until then the synthesizer will not "
    "appear in NVDA's list at all.\n\n"
    "Put the extracted Speech folder and SpeechDictionary.framework into the "
    "macintalk\\tiger folder. The extract_tiger.py tool in the project "
    "repository will do that for you from an installer image; there is a "
    "README in the folder with the details.\n\n"
    "Yes  -  open the folder the engine goes in\n"
    "No  -  do not ask again\n"
    "Cancel  -  remind me next time NVDA starts"
)

#: Left in the folder so the instructions are where the user is looking, not
#: only in a dialog they have already dismissed.
_README = """Tiger-speech needs Apple's speech engine, which this add-on does
not ship.

Put the contents of a Mac OS X 10.4 (Tiger) install here, so that this folder
contains:

    Speech\\Synthesizers\\MacinTalk.SpeechSynthesizer\\
    Speech\\Voices\\<name>.SpeechVoice\\
    SpeechDictionary.framework\\Versions\\A\\

Dropping the extracted folder in whole, one level down, works too.

The easiest way to produce it is the extractor in the project repository:

    py -3 tools\\extract_tiger.py "Mac OS X 10.4 Tiger.iso"

It needs 7-Zip installed, reads your own installer image, and writes straight
into this folder. You need an Intel Tiger image; the PowerPC discs carry the
same voices but a PowerPC engine, which cannot run here.

The extractor is a single Python file. It is not bundled with the add-on
because reading an ISO or DMG needs 7-Zip, which cannot be shipped inside an
NVDA add-on -- so you download and run it yourself:

    https://github.com/tgeczy/panthera-speech/blob/main/tiger/tools/extract_tiger.py

It needs Python 3.8 or newer installed (tested on 3.13), and 7-Zip.

If you would rather keep the engine on another drive, put its full path into a
file called tigerspeech-data.txt in the configuration folder instead.

If that file appeared on its own after an upgrade, it is a note left behind
when the engine folder moved into macintalk, so that going back to an older
version of this add-on still finds it. Deleting it is safe unless you do that.

Delete the file called "do-not-ask" here if you told NVDA to stop reminding
you and would like the reminder back.
"""


class GlobalPlugin(globalPluginHandler.GlobalPlugin):

    def __init__(self):
        super().__init__()
        if globalVars.appArgs.secure:
            log.info("Tiger-speech: secure mode, not checking for the engine")
            return
        log.info("Tiger-speech: engine check armed")
        threading.Timer(6.0, self._check).start()

    def _check(self):
        """Decide whether to ask, and leave a record either way.

        Every step here logs.  Users reported no synthesizer *and* no dialog,
        which are two different failures with the same appearance -- nothing
        happening -- and no way to tell them apart after the fact.  A handful of
        log lines is the difference between "we need another test build" and an
        answer from the log they already sent.
        """
        try:
            ok, lines = tree.explain()
            log.info("Tiger-speech: engine %s\n  %s"
                     % ("ready" if ok else "NOT ready", "\n  ".join(lines)))
            if ok:
                return
            folder = tree.config_dir()
            if os.path.exists(os.path.join(folder, _MARKER)):
                log.info("Tiger-speech: not asking, %s exists in %s"
                         % (_MARKER, folder))
                return
            os.makedirs(folder, exist_ok=True)
            readme = os.path.join(folder, "README.txt")
            if not os.path.exists(readme):
                with open(readme, "w", encoding="utf-8") as f:
                    f.write(_README)
            log.info("Tiger-speech: showing the engine-missing dialog")
            wx.CallAfter(self._ask, folder)
        except Exception:
            log.error("Tiger-speech: engine check failed", exc_info=True)

    def _ask(self, folder):
        """Ask, and only record a refusal when the user actually gives one.

        The style is `wx.YES_NO | wx.CANCEL`. It was `YES_NO_CANCEL`, which
        wxWidgets has in C++ and wxPython does not, so this raised
        AttributeError every single time and the dialog had never once appeared
        in any release of either add-on. It presented as nothing happening --
        which is also what a missing add-on, a suppressed reminder and a
        too-early timer all look like, so it was blamed on each of those in
        turn. A user's log gave it up in one line.
        """
        try:
            answer = gui.messageBox(_MESSAGE, "Tiger-speech",
                                    wx.YES_NO | wx.CANCEL | wx.ICON_INFORMATION)
            if answer == wx.YES:
                os.startfile(folder)
            elif answer == wx.NO:
                with open(os.path.join(folder, _MARKER), "w",
                          encoding="utf-8") as f:
                    f.write("Delete this file to be asked about the engine "
                            "again.\n")
            # wx.CANCEL, and closing the dialog, both leave no marker, so the
            # question comes back next start-up.  That is the default on
            # purpose: the alternative is a synthesizer the user cannot find
            # and has no way to be told about.
        except Exception:
            log.error("Tiger-speech: could not open the engine folder",
                      exc_info=True)
