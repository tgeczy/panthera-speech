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

#: **One start-up dialog between all the Macintosh speech add-ons, not one
#: each.** They run in the same NVDA process, so the first to get here speaks
#: for the rest by claiming this attribute on `globalVars`.
#:
#: Tested by renaming the shared `macintalk` folder and restarting: three
#: add-ons meant *three* modal dialogs stacked at start-up, each naming its own
#: engine, with nothing in the Tools menu to reach the other two afterwards.
#: For a screen-reader user that is three dialogs to hear and dismiss before
#: NVDA is usable.
#:
#: `dict.setdefault` rather than get-then-set: three `threading.Timer(6.0)`
#: fire within milliseconds of each other, and setdefault is one atomic
#: operation under the GIL where a read followed by a write is two.
#:
#: Suppressing the others is only safe because of the Tools menu entry below --
#: without a way to ask again on purpose, a suppressed dialog is a lost one.
_SESSION_CLAIM = "_macintalkEngineDialogShown"


def _claim_the_startup_dialog(who):
    """-> True if this add-on is the one that should ask this session."""
    return globalVars.__dict__.setdefault(_SESSION_CLAIM, who) == who


#: Written only when the user explicitly says "stop asking".
_MARKER = "do-not-ask"

_MESSAGE = (
    "Tiger-speech has no engine to run yet.\n\n"
    "This add-on ships no part of Apple's software. You supply it from your "
    "own Mac OS X 10.4 install disc. The synthesizer is listed in NVDA either "
    "way, and says what is missing if you select it before then.\n\n"
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

    #: Shown in NVDA's Tools menu. Translators: an item in NVDA's Tools menu.
    MENU_LABEL = _("&Tiger speech engine...")
    MENU_HELP = _("Check whether tigerspeech can find its engine, and open the "
                  "folder it goes in.")

    def __init__(self):
        super().__init__()
        self._menuItem = None
        if globalVars.appArgs.secure:
            log.info("Tiger-speech: secure mode, not checking for the engine")
            return
        self._addMenuItem()
        log.info("Tiger-speech: engine check armed")
        threading.Timer(6.0, self._check).start()

    def _addMenuItem(self):
        """A way to ask on purpose, which is what makes "do not ask" safe.

        Without this, saying no once meant deleting a file called
        `do-not-ask` by hand to ever see it again -- and there was no route at
        all to "is my engine actually installed?" short of selecting the
        synthesizer and listening for silence.
        """
        try:
            sysTrayIcon = gui.mainFrame.sysTrayIcon
            self._menuItem = sysTrayIcon.toolsMenu.Append(
                wx.ID_ANY, self.MENU_LABEL, self.MENU_HELP)
            sysTrayIcon.Bind(wx.EVT_MENU, self._onMenu, self._menuItem)
            log.info("Tiger-speech: added the Tools menu item")
        except Exception:
            # Never fatal: the add-on still speaks without a menu entry, and
            # global plugins load while the GUI is still assembling itself.
            log.error("Tiger-speech: could not add the Tools menu item",
                      exc_info=True)

    def terminate(self):
        """Take the menu item away again, or reloading duplicates it."""
        try:
            if self._menuItem is not None:
                gui.mainFrame.sysTrayIcon.toolsMenu.Remove(self._menuItem.Id)
                self._menuItem.Destroy()
                self._menuItem = None
        except Exception:
            log.error("Tiger-speech: could not remove the Tools menu item",
                      exc_info=True)
        super().terminate()

    def _onMenu(self, evt):
        """Always ask, whatever the marker says and whoever else asked today.

        Somebody who opens this from a menu is asking the question right now,
        and answering "you said not to ask" would be obtuse.
        """
        ok, lines = tree.explain()
        log.info("Tiger-speech: engine %s (from the Tools menu)\n  %s"
                 % ("ready" if ok else "NOT ready", "\n  ".join(lines)))
        folder = tree.config_dir()
        try:
            os.makedirs(folder, exist_ok=True)
        except OSError:
            pass
        if ok:
            gui.messageBox(
                "tigerspeech has its engine.\n\n%s" % "\n".join(lines),
                "Tiger-speech", wx.OK | wx.ICON_INFORMATION)
            return
        self._ask(folder)

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
            if not _claim_the_startup_dialog("tigerspeech"):
                log.info("Tiger-speech: another Macintosh speech add-on has already\n"
                         "  asked this session; the Tools menu still has ours")
                return
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
