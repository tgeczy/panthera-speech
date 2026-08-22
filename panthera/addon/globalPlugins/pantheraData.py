# -*- coding: utf-8 -*-
"""Tell the user which engine folders are empty, and keep telling them.

The engines are not ours to ship, so a fresh install cannot speak until the
user supplies them.  That needs saying somewhere, and a settings panel would be
a lot of scaffolding around two folders -- so it is a dialog with a button that
opens the folder they share.

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

**One plugin for both generations.**  There used to be one per add-on, and the
pair had drifted in the ordinary way: 424 lines that differed only in strings,
so every fix had to be made twice and one of the two was eventually going to be
missed.  The generations live in `GENERATIONS` below, and adding Snow Leopard
or Lion is a table entry.
"""
import os
import sys
import textwrap
import threading

import globalPluginHandler
import globalVars
import gui
import wx
from logHandler import log

_ADDON = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ENGINE_DIR = os.path.join(_ADDON, "synthDrivers", "_panthera")
if _ENGINE_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_DIR)

# Finding the engines lives in the tree modules, not here: the drivers need
# exactly the same answer, and two copies of a lookup is two chances to
# disagree about where an engine is.
#
# The `panthera` prefix is not tidiness.  Every NVDA add-on shares one
# `sys.modules`, and while somebody still has the old `tigerspeech` or
# `leopardspeech` add-on installed alongside this one, their private folders
# are on `sys.path` too.  Both of those hold a `leopardtree`; a name no older
# add-on ever used is what keeps this one out of that fight.
import pantheraleopard                                        # noqa: E402
import pantheratiger                                          # noqa: E402

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
#: outSPOKEN cannot import this add-on -- separate add-on, separate repository
#: -- so the meeting place is an attribute on `globalVars` and this block of
#: code is duplicated there. Keep the key and the entry shape identical in both
#: or they will hold two separate meetings. **The literals are also the
#: contract with the two add-ons this one replaces**, which are still installed
#: on anybody mid-upgrade and are still speaking the 0.8.0 version of it.
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
                "is missing if you select it, and you can ask this "
                "question again whenever you like from NVDA's Tools "
                "menu.\n\n" % missing[0]["source"])
        refuse = "No  -  do not ask again\n"
    else:
        head = ("%d Macintosh speech engines are missing:\n\n"
                % len(missing))
        for m in missing:
            head += "    %s\n        %s\n" % (m["label"], m["folder"])
        body = ("\nNone of them ships any part of Apple's or Berkeley's "
                "software. You supply that from your own Macintosh discs and "
                "disk images. Each folder has a README naming the extractor to "
                "run and what it needs.\n\n"
                "All of these synthesizers are listed in NVDA either way, and "
                "each says what is missing if you select it, and you can "
                "ask this question again whenever you like from NVDA's "
                "Tools menu.\n\n")
        refuse = "No  -  do not ask again about any of them\n"
    return (head + body +
            "Yes  -  open the folder\n" + refuse +
            "Cancel  -  remind me next time NVDA starts")


#: Every Mac OS X speech engine that can report on itself. This add-on holds
#: two of them and outSPOKEN is a separate add-on that cannot import it, so the
#: shared Tools menu item is owned by whichever registers first and reads this
#: list when it is *clicked* -- not at start-up, when only the first has
#: registered.
#:
#: Three menu items to answer one question ("what voice data do I have?") is
#: what this replaces. outSPOKEN keeps its own, because it is a different
#: lineage in a different repository: classic Mac OS engines, not Mac OS X.
_REPORTERS = "_macosxSpeechEngineReporters"


def _register_reporter(entry):
    """-> True if this add-on should own the shared Tools menu item."""
    lock = globalVars.__dict__.setdefault(_REGISTRY_LOCK, threading.Lock())
    with lock:
        reporters = globalVars.__dict__.setdefault(_REPORTERS, [])
        reporters.append(entry)
        return len(reporters) == 1


def _engine_report():
    """Every registered add-on's verdict, in one page. -> (lines, [missing])"""
    lines, missing = [], []
    for r in globalVars.__dict__.get(_REPORTERS, []):
        try:
            ok, detail = r["explain"]()
            folder = r["folder"]()
        except Exception:
            ok, detail, folder = False, ["could not be checked"], "?"
        lines.append(r["label"])
        lines.append("    %s   %s" % ("ready  " if ok else "MISSING", folder))
        # Invisible state that presents exactly like a broken dialog:
        # somebody clicks "do not ask again" during a test and wonders
        # months later why start-up says nothing.
        if os.path.exists(os.path.join(folder, _MARKER)):
            lines.append("        start-up reminders are OFF for this "
                         "one -- delete %s in the folder above to turn "
                         "them back on" % _MARKER)
        for d in detail:
            lines.append("        %s" % d)
        lines.append("")
        if not ok:
            missing.append({"label": r["label"], "folder": folder,
                            "source": r["source"]})
    return lines, missing


#: The dialog speaks for every add-on that registered, so it is titled
#: after none of them. It read "Tiger-speech" while listing outSPOKEN and
#: Leopard -- confusing in the ordinary way, and worse on a screen reader,
#: where the title is the first thing announced.
_DIALOG_TITLE = "Macintosh speech"


#: Written only when the user explicitly says "stop asking".
_MARKER = "do-not-ask"


#: Left in each folder so the instructions are where the user is looking, not
#: only in a dialog they have already dismissed.
_TIGER_README = """Tiger speech needs Apple's speech engine, which this add-on
does not ship.

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

_LEOPARD_README = """Leopard speech needs Apple's speech engine, which this
add-on does not ship.

Put the contents of a Mac OS X 10.5 (Leopard) install here, so that this folder
contains:

    Speech\\Synthesizers\\MacinTalk.SpeechSynthesizer\\
    Speech\\Voices\\<name>.SpeechVoice\\
    SpeechDictionary.framework\\Versions\\A\\

Dropping the extracted folder in whole, one level down, works too.

The easiest way to produce it is the extractor in the project repository:

    py -3 tools\\extract_leopard.py "Mac OS X 10.5 Leopard.iso"

It reads your own installer image directly -- no 7-Zip, no other tool -- and
writes straight into this folder. You need an Intel Leopard image; the PowerPC
discs carry the same voices but a PowerPC engine, which cannot run here.

Leopard's engine also needs Apple's C++ runtime, libstdc++.6.0.4.dylib, which
is on the same disc under usr/lib. The extractor takes it. Without it nothing
loads at all -- not one voice.

The extractor is a single Python file. It is not bundled with the add-on --
Leopard's reads the DVD image itself, but Tiger's needs 7-Zip and that cannot
be shipped inside an NVDA add-on, so both are downloaded and run the same way:

    https://github.com/tgeczy/panthera-speech/blob/main/leopard/tools/extract_leopard.py

It needs Python 3.8 or newer installed (tested on 3.13).

If you would rather keep the engine on another drive, put its full path into a
file called leopardspeech-data.txt in the configuration folder instead.

If that file appeared on its own after an upgrade, it is a note left behind
when the engine folder moved into macintalk, so that going back to an older
version of this add-on still finds it. Deleting it is safe unless you do that.

Delete the file called "do-not-ask" here if you told NVDA to stop reminding
you and would like the reminder back.
"""


#: One entry per generation this add-on carries.
#:
#: `oldAddon` is the add-on that used to carry it on its own. It is not
#: history: those add-ons can still be installed next to this one, and while
#: they are, they contribute a `synthDrivers/tigerspeech.py` of their own to
#: the same package path. See `_old_addons` below.
GENERATIONS = (
    {
        "key": "tiger",
        "tree": pantheratiger,
        "label": "Tiger speech -- Mac OS X 10.4, twenty-three voices",
        "source": "your own Mac OS X 10.4 install disc",
        "readme": _TIGER_README,
        "oldAddon": "tigerspeech",
    },
    {
        "key": "leopard",
        "tree": pantheraleopard,
        "label": "Leopard speech -- Mac OS X 10.5, Alex and twenty-three more",
        "source": "your own Mac OS X 10.5 install disc",
        "readme": _LEOPARD_README,
        "oldAddon": "leopardspeech",
    },
)


def _pending_remove(name):
    """-> True if this add-on is already marked for removal on restart.

    Without it, saying yes and then reloading plugins without restarting asks
    the same question again, about a decision the user has already made.
    """
    try:
        from addonHandler import state
        from addonStore.models.status import AddonStateCategory
        return name in state[AddonStateCategory.PENDING_REMOVE]
    except Exception:
        return False


def _old_addons():
    """The replaced add-ons that are installed and running. -> [Addon]

    **NVDA's manifest has no `replaces` or `conflicts` field** -- the whole
    spec is name, summary, description, author, version, changelog,
    minimum/lastTested, url, docFileName, braille tables and symbol
    dictionaries -- so nothing stops both being installed at once and nothing
    warns anybody that they are.

    What happens then is quiet rather than loud. `Addon.addToPackagePath` does
    `package.__path__.insert(0, ...)` for each running add-on in turn, so the
    *last* one added wins the name -- and add-ons are walked in the order the
    add-ons folder lists them, which is alphabetical. `tigerspeech` sorts after
    `pantheraspeech` and `leopardspeech` sorts before it, so the likely result
    is not even consistently one or the other: Tiger from the old add-on and
    Leopard from this one, at the same time. Nothing errors. The user simply
    runs some of last month's code and cannot tell.

    Only *running* add-ons count. A disabled one contributes nothing to the
    package path, so it cannot shadow anything, and asking about it would be
    asking about a problem the user has already solved.
    """
    names = {g["oldAddon"] for g in GENERATIONS}
    try:
        import addonHandler
        return [a for a in addonHandler.getRunningAddons()
                if a.name in names and not _pending_remove(a.name)]
    except Exception:
        # Nothing here is worth breaking start-up for. Worst case the user is
        # not warned, which is exactly where they were before this existed.
        log.error("Panthera: could not look for the older add-ons",
                  exc_info=True)
        return []


#: The heading the shared Tools report shows the conflict under.
#:
#: It is worded so that the line the report prints under it -- `ready` and a
#: folder, in a format this add-on does not control -- says something true.
#: "Panthera speech: ready" is correct; the detail underneath is the warning.
_CONFLICT_LABEL = "Panthera speech -- installed, and so are the add-ons it replaces"


def _macintalk_root():
    """The folder both generations live under. -> str

    Only ever printed. The report also looks for a `do-not-ask` beside it,
    which nothing writes here -- and if one ever appeared it would add a
    harmless "reminders are off" line to an entry that has no reminder.
    """
    return os.path.dirname(pantheratiger.config_dir())


def _conflict_report(addons):
    """The conflict, in the shape `_engine_report` reads. -> (True, [lines])

    **This is how the warning reaches the Tools menu at all.** In the state it
    describes, the older add-ons are running, so this add-on skips registering
    both generations -- and then owns no reporter, and so does not own the
    shared menu item either. The report is being drawn by one of the 0.8.0
    plugins, whose code cannot be changed retroactively and knows nothing
    about any of this.

    What it does know is how to read a registered entry and print its detail
    lines. So the conflict registers as an entry of its own: `True` for the
    verdict, because Panthera speech itself is fine, and the sentences that
    matter go in the detail.
    """
    if not addons:
        return True, []
    one = len(addons) == 1
    names = " and ".join(a.name for a in addons)
    # Wrapped rather than hand-broken: the report indents every detail line
    # under its label, and two add-on names in one sentence is already past
    # the width a hand-broken line was measured at.
    return True, textwrap.wrap(
        "%s %s still installed, and %s been replaced by this add-on. Both "
        "offer a synthesizer of the same name, so which copy NVDA loads "
        "depends on the order it reads the add-ons folder in. You can end up "
        "running the older code with nothing saying so. Remove the older "
        "add-on%s in the Add-on Store, under Tools."
        % (names, "is" if one else "are", "it has" if one else "they have",
           "" if one else "s"),
        width=68)


def _conflict_message(addons):
    """-> str, or the empty string when there is nothing to say.

    "can take priority", not "will": which copy wins is decided by the order
    NVDA reads the add-ons folder in, and promising a direction we have not
    measured on the user's machine would be a guess dressed as a fact.
    """
    if not addons:
        return ""
    one = len(addons) == 1
    names = " and ".join(a.name for a in addons)
    return (
        "The %s add-on%s still installed alongside Panthera speech.\n\n"
        "%s been replaced: Panthera speech carries the same synthesizers "
        "under the same names, so your voice, rate, pitch and volume settings "
        "carry over untouched.\n\n"
        "Leaving %s installed is not harmless. Both copies offer a "
        "synthesizer of the same name, and which one NVDA loads depends on "
        "the order it happens to read the add-ons folder in. You can end up "
        "running the older code with nothing saying so.\n\n"
        "Remove the older add-on%s now?\n\n"
        "Yes  -  remove, then restart NVDA\n"
        "No  -  leave %s for now, and ask again next time"
        % (names, " is" if one else "s are",
           "It has" if one else "They have",
           "it" if one else "them",
           "" if one else "s",
           "it" if one else "them"))


class GlobalPlugin(globalPluginHandler.GlobalPlugin):

    #: Shown in NVDA's Tools menu. Translators: an item in NVDA's Tools menu.
    MENU_LABEL = _("Mac OS X &speech engines...")
    MENU_HELP = _("Show which Mac OS X speech engines are installed, and open "
                  "the folder they go in.")

    def __init__(self):
        super().__init__()
        self._menuItem = None
        self._conflicts = []
        if globalVars.appArgs.secure:
            log.info("Panthera: secure mode, not checking for the engines")
            return

        self._conflicts = _old_addons()
        if self._conflicts:
            log.warning("Panthera: the replaced add-on(s) %s are still "
                        "running; either copy of a driver may be the one NVDA "
                        "loaded" % ", ".join(a.name for a in self._conflicts))

        owner = False
        for gen in GENERATIONS:
            # An old add-on still running reports on this generation itself,
            # with its own entry and its own folder. Registering a second one
            # would list the same engine twice in a dialog this add-on may not
            # even be the one showing.
            if self._covered(gen):
                continue
            if _register_reporter({
                    "label": gen["label"],
                    "source": gen["source"],
                    "explain": gen["tree"].explain,
                    "folder": gen["tree"].config_dir,
            }):
                owner = True
        # After the loop, and unconditional on ownership: in the state this
        # describes, every generation is covered, so nothing above registered
        # and the shared menu item belongs to a 0.8.0 plugin that has never
        # heard of any of this. Registering the conflict as an entry is what
        # puts it into the report that plugin draws.
        if self._conflicts:
            if _register_reporter({
                    "label": _CONFLICT_LABEL,
                    "source": "the Add-on Store, under Tools",
                    "explain": lambda: _conflict_report(self._conflicts),
                    "folder": _macintalk_root,
            }):
                owner = True
        if owner:
            self._addMenuItem()
        log.info("Panthera: engine check armed")
        threading.Timer(6.0, self._check).start()

    def _covered(self, gen):
        """-> True if a still-running older add-on speaks for this generation."""
        return any(a.name == gen["oldAddon"] for a in self._conflicts)

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
            log.info("Panthera: added the Tools menu item")
        except Exception:
            # Never fatal: the add-on still speaks without a menu entry, and
            # global plugins load while the GUI is still assembling itself.
            log.error("Panthera: could not add the Tools menu item",
                      exc_info=True)

    def terminate(self):
        """Take the menu item away again, or reloading duplicates it."""
        try:
            if self._menuItem is not None:
                gui.mainFrame.sysTrayIcon.toolsMenu.Remove(self._menuItem.Id)
                self._menuItem.Destroy()
                self._menuItem = None
        except Exception:
            log.error("Panthera: could not remove the Tools menu item",
                      exc_info=True)
        super().terminate()

    def _onMenu(self, evt):
        """Report on every Mac OS X engine, not only this add-on's.

        Always answers, whatever the do-not-ask marker says: somebody opening
        this from a menu is asking the question right now.
        """
        try:
            # The conflict is in here without being added here. It registered
            # as a reporter at start-up, so it appears whether this add-on
            # draws the report or one of the older plugins does -- and it
            # cannot appear twice, which appending it here as well would have
            # managed in the one case where this add-on owns the menu.
            lines, missing = _engine_report()
            log.info("Panthera: Tools menu report\n  %s" % "\n  ".join(lines))
            for m in missing:
                try:
                    os.makedirs(m["folder"], exist_ok=True)
                except OSError:
                    pass
            report = "\n".join(lines).rstrip()
            if not missing:
                gui.messageBox(report, _DIALOG_TITLE,
                               wx.OK | wx.ICON_INFORMATION)
                return
            answer = gui.messageBox(
                report + "\n\nOpen the folder they go in?",
                _DIALOG_TITLE, wx.YES_NO | wx.ICON_INFORMATION)
            if answer == wx.YES:
                os.startfile(_folder_to_open(missing))
        except Exception:
            log.error("Panthera: Tools menu report failed", exc_info=True)

    def _check(self):
        """Decide whether to ask, and leave a record either way.

        Every step here logs.  Users reported no synthesizer *and* no dialog,
        which are two different failures with the same appearance -- nothing
        happening -- and no way to tell them apart after the fact.  A handful of
        log lines is the difference between "we need another test build" and an
        answer from the log they already sent.

        The conflict is asked about first and on its own. It is the more
        urgent of the two -- until it is settled the user may not be running
        the code they think they are -- and folding it into the engine dialog
        would make one question with two subjects and one answer.
        """
        if self._conflicts:
            log.info("Panthera: asking about %d older add-on(s)"
                     % len(self._conflicts))
            wx.CallAfter(self._askConflict, list(self._conflicts))
        for gen in GENERATIONS:
            if self._covered(gen):
                log.info("Panthera: %s is being reported by the older %s "
                         "add-on, not by this one"
                         % (gen["key"], gen["oldAddon"]))
                continue
            try:
                self._checkOne(gen)
            except Exception:
                log.error("Panthera: %s engine check failed" % gen["key"],
                          exc_info=True)

    def _checkOne(self, gen):
        """One generation: log the verdict, and join the dialog if it is bare."""
        tree = gen["tree"]
        ok, lines = tree.explain()
        log.info("Panthera: %s engine %s\n  %s"
                 % (gen["key"], "ready" if ok else "NOT ready",
                    "\n  ".join(lines)))
        if ok:
            return
        folder = tree.config_dir()
        if os.path.exists(os.path.join(folder, _MARKER)):
            log.info("Panthera: not asking about %s, %s exists in %s"
                     % (gen["key"], _MARKER, folder))
            return
        os.makedirs(folder, exist_ok=True)
        readme = os.path.join(folder, "README.txt")
        if not os.path.exists(readme):
            with open(readme, "w", encoding="utf-8") as f:
                f.write(gen["readme"])
        first = _register_missing({
            "label": gen["label"],
            "folder": folder,
            "source": gen["source"],
        })
        if not first:
            log.info("Panthera: %s joined another dialog" % gen["key"])
            return
        log.info("Panthera: will show the combined engine-missing dialog")
        wx.CallLater(_RENDEZVOUS_MS, self._askCombined)

    def _askCombined(self):
        """One dialog for every add-on that registered, however many that is.

        Whoever got here first shows it and speaks for all of them, so this
        code has to be identical in each add-on -- it is duplicated, not
        shared, because outSPOKEN is a separate add-on in a separate
        repository and cannot import this one.
        """
        try:
            missing = _missing_engines()
            if not missing:
                return
            log.info("Panthera: combined dialog for %d engine(s): %s"
                     % (len(missing),
                        ", ".join(m["folder"] for m in missing)))
            wx.CallAfter(self._ask, missing)
        except Exception:
            log.error("Panthera: combined dialog failed", exc_info=True)

    def _ask(self, missing):
        """Ask once, about all of them, and honour the answer for all of them.

        `missing` is the shared registry: one entry per engine that is not
        there. Passing a list rather than a folder is what turns "Tiger has no
        engine, and nothing about the other two" into one honest question.

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
                                    _DIALOG_TITLE,
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
                        log.error("Panthera: could not write %s in %s"
                                  % (_MARKER, m["folder"]), exc_info=True)
            # wx.CANCEL, and closing the dialog, both leave no marker, so the
            # question comes back next start-up.  That is the default on
            # purpose: the alternative is a synthesizer the user cannot find
            # and has no way to be told about.
        except Exception:
            log.error("Panthera: could not open the engine folder",
                      exc_info=True)

    def _askConflict(self, addons):
        """Offer to remove the add-ons this one replaces.

        No marker file and no "do not ask again", unlike the missing-engine
        question. That one is about something the user may simply not want;
        this one is about a state where which code NVDA runs is decided by the
        order of a directory listing. It is asked every start-up until it is
        settled one way or the other, and the Tools report names it meanwhile.

        Nothing is removed without being asked. They are the user's add-ons.
        """
        try:
            answer = gui.messageBox(_conflict_message(addons), _DIALOG_TITLE,
                                    wx.YES_NO | wx.ICON_WARNING)
            if answer != wx.YES:
                log.info("Panthera: user left the older add-on(s) in place")
                return
            removed = []
            for addon in addons:
                try:
                    addon.requestRemove()
                    removed.append(addon.name)
                except Exception:
                    log.error("Panthera: could not mark %s for removal"
                              % addon.name, exc_info=True)
            if not removed:
                gui.messageBox(
                    "NVDA would not remove %s automatically.\n\nYou can "
                    "remove it yourself from the Add-on Store, under Tools."
                    % ", ".join(a.name for a in addons),
                    _DIALOG_TITLE, wx.OK | wx.ICON_WARNING)
                return
            log.info("Panthera: marked %s for removal" % ", ".join(removed))
            self._conflicts = [a for a in addons if a.name not in removed]
            if gui.messageBox(
                    "%s will be removed when NVDA restarts.\n\nRestart now?"
                    % ", ".join(removed),
                    _DIALOG_TITLE,
                    wx.YES_NO | wx.ICON_INFORMATION) == wx.YES:
                import core
                core.restart()
        except Exception:
            log.error("Panthera: the conflict dialog failed", exc_info=True)
