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

#: **One dialog covering every Macintosh speech add-on, not one each and not
#: one that only mentions whichever add-on got there first.**
#:
#: Tested by renaming the shared `macintalk` folder and restarting: three
#: add-ons meant *three* modal dialogs stacked at start-up, each naming its own
#: engine. For a screen-reader user that is three dialogs to hear and dismiss
#: before NVDA is usable.
#:
#: The first fix suppressed the others, which is worse than it sounds: you were
#: told Tiger had no engine and nothing at all about the two that also had
#: none. So they rendezvous instead. Each add-on that finds itself without an
#: engine adds a line to a shared list, and the first to arrive schedules one
#: dialog a moment later that reads the whole list out.
#:
#: **This only works because they share the `macintalk` folder now.** One
#: dialog, one "open the folder" button, and all the subfolders are in it.
#:
#: The three add-ons cannot import each other -- separate add-ons, separate
#: repositories -- so the meeting place is an attribute on `globalVars` and
#: this block of code is duplicated in each. Keep the key and the entry shape
#: identical across all three or they will hold two separate meetings.
_REGISTRY = "_macintalkMissingEngines"
_REGISTRY_LOCK = "_macintalkMissingEnginesLock"

#: How long to wait for the others to arrive. They all fire off a 6.0 s timer
#: so they are milliseconds apart; this is slack, not a real delay.
_RENDEZVOUS_MS = 1500


def _register_missing(entry):
    """Add this add-on to the shared list. -> True if it should show the dialog.

    Locked, because `append` then `len(...) == 1` is two operations and two
    threads can interleave between them so that *neither* sees itself as
    first -- and then nobody shows the dialog at all. `setdefault` for the
    lock itself is the one atomic step this needs.
    """
    lock = globalVars.__dict__.setdefault(_REGISTRY_LOCK, threading.Lock())
    with lock:
        registry = globalVars.__dict__.setdefault(_REGISTRY, [])
        registry.append(entry)
        return len(registry) == 1


def _missing_engines():
    return list(globalVars.__dict__.get(_REGISTRY, []))


def _folder_to_open(missing):
    """The one folder that holds all of them, when there is one.

    Since 0.10.0 every engine lives under `macintalk`, so one "open the folder"
    button can serve three add-ons -- which is the thing that makes a single
    dialog worth having rather than a compromise.
    """
    parents = {os.path.dirname(m["folder"]) for m in missing}
    if len(missing) > 1 and len(parents) == 1:
        return parents.pop()
    return missing[0]["folder"]


def _combined_message(missing):
    """One question about however many add-ons turned up. -> str

    Singular when it is one, which is the common case and should not read like
    a form letter about a list of one.
    """
    one = len(missing) == 1
    if one:
        head = ("%s has no engine yet.\n\nIt goes in:\n    %s\n"
                % (missing[0]["label"], missing[0]["folder"]))
        body = ("\nIt ships no part of Apple's or Berkeley's software. You "
                "supply that from %s. There is a README in the folder naming "
                "the extractor to run and what it needs.\n\n"
                "The synthesizer is listed in NVDA either way, and says what "
                "is missing if you select it. There is an entry in the Tools "
                "menu for it too.\n\n" % missing[0]["source"])
        refuse = "No  -  do not ask again\n"
    else:
        head = ("%d Macintosh speech add-ons have no engine yet:\n\n"
                % len(missing))
        for m in missing:
            head += "    %s\n        %s\n" % (m["label"], m["folder"])
        body = ("\nNone of them ships any part of Apple's or Berkeley's "
                "software. You supply that from your own Macintosh discs and "
                "disk images. Each folder has a README naming the extractor to "
                "run and what it needs.\n\n"
                "All of these synthesizers are listed in NVDA either way, and "
                "each says what is missing if you select it. There is an entry "
                "in the Tools menu for each as well.\n\n")
        refuse = "No  -  do not ask again about any of them\n"
    return (head + body +
            "Yes  -  open the folder\n" + refuse +
            "Cancel  -  remind me next time NVDA starts")


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
            first = _register_missing({
                "label": "Tiger speech -- Mac OS X 10.4, twenty-three voices",
                "folder": folder,
                "source": "your own Mac OS X 10.4 install disc",
            })
            if not first:
                log.info("Tiger-speech: joined another add-on's dialog")
                return
            log.info("Tiger-speech: will show the combined engine-missing dialog")
            wx.CallLater(_RENDEZVOUS_MS, self._askCombined)
        except Exception:
            log.error("Tiger-speech: engine check failed", exc_info=True)

    def _askCombined(self):
        """One dialog for every add-on that registered, however many that is.

        Whoever got here first shows it and speaks for all of them, so this
        code has to be identical in each add-on -- it is duplicated, not
        shared, because they are separate add-ons in separate repositories and
        cannot import one another.
        """
        try:
            missing = _missing_engines()
            if not missing:
                return
            log.info("Tiger-speech: combined dialog for %d add-on(s): %s"
                     % (len(missing),
                        ", ".join(m["folder"] for m in missing)))
            wx.CallAfter(self._ask, missing)
        except Exception:
            log.error("Tiger-speech: combined dialog failed", exc_info=True)

    def _ask(self, missing):
        """Ask once, about all of them, and honour the answer for all of them.

        `missing` is the shared registry: one entry per add-on that has no
        engine. Passing a list rather than a folder is what turns "Tiger has
        no engine, and nothing about the other two" into one honest question.

        The style is `wx.YES_NO | wx.CANCEL`. It was `YES_NO_CANCEL`, which
        wxWidgets has in C++ and wxPython does not, so this raised
        AttributeError every single time and the dialog had never once appeared
        in any release of either add-on. It presented as nothing happening --
        which is also what a missing add-on, a suppressed reminder and a
        too-early timer all look like, so it was blamed on each of those in
        turn. A user's log gave it up in one line.
        """
        try:
            answer = gui.messageBox(_combined_message(missing),
                                    "Tiger-speech",
                                    wx.YES_NO | wx.CANCEL | wx.ICON_INFORMATION)
            if answer == wx.YES:
                os.startfile(_folder_to_open(missing))
            elif answer == wx.NO:
                # Answered about all of them, so recorded for all of them.
                for m in missing:
                    try:
                        with open(os.path.join(m["folder"], _MARKER), "w",
                                  encoding="utf-8") as f:
                            f.write("Delete this file to be asked about the "
                                    "engine again.\n")
                    except OSError:
                        log.error("Tiger-speech: could not write %s in %s"
                                  % (_MARKER, m["folder"]), exc_info=True)
            # wx.CANCEL, and closing the dialog, both leave no marker, so the
            # question comes back next start-up.  That is the default on
            # purpose: the alternative is a synthesizer the user cannot find
            # and has no way to be told about.
        except Exception:
            log.error("Tiger-speech: could not open the engine folder",
                      exc_info=True)
