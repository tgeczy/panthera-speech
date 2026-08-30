# -*- coding: utf-8 -*-
"""The global plugin's dialog text, actually formatted rather than read.

Every other test of this plugin reads its source, because it imports NVDA's
`globalPluginHandler`, `gui` and `wx` and none of those exist here. That left
the one thing most likely to be wrong untested: these are `%`-formatted
strings with six substitutions and a singular and a plural branch, and a
count mismatch raises `TypeError` *inside a wx handler*, which NVDA swallows
into the log.

The symptom of that is no dialog at all -- which is also what a suppressed
reminder, a mistimed timer and a missing add-on all look like, and it has
already been blamed on each of those in turn once. So the modules get stubbed
and the functions get called.
"""
import os
import sys
import types

import pytest

ADDON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))), "addon")


def _stub_nvda():
    """Enough of NVDA for the plugin to import. Nothing here is exercised."""
    if "globalPluginHandler" in sys.modules:
        return
    gph = types.ModuleType("globalPluginHandler")
    gph.GlobalPlugin = type("GlobalPlugin", (object,), {
        "__init__": lambda self: None, "terminate": lambda self: None})
    sys.modules["globalPluginHandler"] = gph

    g = types.ModuleType("gui")
    g.messageBox = lambda *a, **k: None
    g.mainFrame = None
    sys.modules["gui"] = g

    wx = types.ModuleType("wx")
    for n in ("OK", "CANCEL", "YES", "NO", "YES_NO", "ID_ANY", "EVT_MENU",
              "ICON_INFORMATION", "ICON_WARNING"):
        setattr(wx, n, len(n))
    wx.CallAfter = wx.CallLater = lambda *a, **k: None
    sys.modules["wx"] = wx


@pytest.fixture(scope="module")
def plugin():
    _stub_nvda()
    sys.path.insert(0, os.path.join(ADDON, "synthDrivers", "_panthera"))
    sys.path.insert(0, os.path.join(ADDON, "globalPlugins"))
    import pantheraData
    return pantheraData


class _FakeAddon(object):
    def __init__(self, name):
        self.name = name


def test_the_conflict_message_formats_for_one_addon(plugin):
    said = plugin._conflict_message([_FakeAddon("tigerspeech")])
    assert "The tigerspeech add-on is still installed" in said
    assert "It has been replaced" in said
    assert "Remove the older add-on now?" in said
    assert "%" not in said, "an unsubstituted placeholder reached the user"


def test_the_conflict_message_formats_for_two(plugin):
    said = plugin._conflict_message([_FakeAddon("tigerspeech"),
                                     _FakeAddon("leopardspeech")])
    assert "tigerspeech and leopardspeech add-ons are still installed" in said
    assert "They have been replaced" in said
    assert "Remove the older add-ons now?" in said
    assert "%" not in said


def test_nothing_is_said_when_there_is_no_conflict(plugin):
    assert plugin._conflict_message([]) == ""


def test_the_conflict_message_does_not_promise_which_copy_wins(plugin):
    """Which one NVDA loads depends on the order it reads a directory in.

    Saying "the old one will take priority" would be a guess dressed as a
    fact, and the guess is wrong half the time: `leopardspeech` sorts before
    `pantheraspeech` and `tigerspeech` sorts after it, so the two generations
    can land on opposite sides of the same start-up.
    """
    said = plugin._conflict_message([_FakeAddon("tigerspeech")])
    assert "depends on the order" in said
    for promise in ("will take priority", "will be loaded instead",
                    "will win", "always loads"):
        assert promise not in said, promise


def test_the_conflict_reaches_the_tools_report(plugin):
    """The one route it has, in the state it is about.

    With both older add-ons running, this add-on skips registering both
    generations -- so it owns no reporter, so it does not own the shared Tools
    menu item either, and the report is being drawn by a 0.8.0 plugin that has
    never heard of any of this. Appending the warning inside this add-on's own
    `_onMenu` therefore renders it in exactly the case it is not needed and
    never in the case it is.

    A registered entry is what an older plugin *does* know how to print.
    """
    ok, lines = plugin._conflict_report([_FakeAddon("tigerspeech"),
                                         _FakeAddon("leopardspeech")])
    assert ok, "a False verdict would put this in the missing list and make " \
               "the older plugin offer to open a folder for it"
    body = " ".join(lines)
    assert "tigerspeech and leopardspeech are still installed" in body
    assert "depends on the order" in body
    assert "Add-on Store" in body
    assert "%" not in body
    # The report indents each detail line under the label, so a line long
    # enough to wrap twice is a line the user hears as one long run.
    assert max(len(l) for l in lines) < 80, max(lines, key=len)


def test_one_older_addon_reads_as_one(plugin):
    ok, lines = plugin._conflict_report([_FakeAddon("tigerspeech")])
    body = " ".join(lines)
    assert "tigerspeech is still installed, and it has been replaced" in body
    assert "Remove the older add-on in the Add-on Store" in body
    # Not "the older add-ons". `add-ons folder` is in there too, so the check
    # has to be the phrase and not the word -- the first version of this
    # assertion failed on the sentence three lines above the one it meant.
    assert "older add-ons" not in body


def test_no_conflict_reports_nothing(plugin):
    assert plugin._conflict_report([]) == (True, [])


def test_the_conflict_is_only_said_once(plugin):
    """It used to be appended in `_onMenu` as well as registered.

    That renders it twice in the one case where this add-on owns the menu --
    and a screen reader reads both."""
    import io
    src = io.open(os.path.join(ADDON, "globalPlugins", "pantheraData.py"),
                  encoding="utf-8").read()
    body = src[src.index("def _onMenu"):]
    body = body[:body.index("\n    def ")]
    assert "self._conflicts" not in body, (
        "_onMenu adds the conflict as well as the registered entry")


ENTRY = {"label": "Tiger speech -- Mac OS X 10.4, twenty-three voices",
         "folder": r"C:\Users\x\AppData\Roaming\nvda\macintalk\tiger",
         "source": "your own Mac OS X 10.4 install disc"}
ENTRY2 = {"label": "Leopard speech -- Mac OS X 10.5, Alex and twenty-three more",
          "folder": r"C:\Users\x\AppData\Roaming\nvda\macintalk\leopard",
          "source": "your own Mac OS X 10.5 install disc"}


def test_one_missing_engine_reads_as_a_sentence_not_a_list(plugin):
    said = plugin._combined_message([ENTRY])
    assert said.startswith(ENTRY["label"] + " has no engine yet.")
    assert "do not ask again\n" in said
    assert "any of them" not in said
    assert "%" not in said


def test_two_missing_engines_are_named_in_one_question(plugin):
    said = plugin._combined_message([ENTRY, ENTRY2])
    assert said.startswith("2 Macintosh speech engines are missing:")
    assert ENTRY["folder"] in said and ENTRY2["folder"] in said
    assert "do not ask again about any of them" in said
    assert "%" not in said


def test_the_button_opens_the_folder_that_holds_both(plugin):
    """The single dialog is only worth having because one button serves it.

    Both engines live under `macintalk`, so the common parent is the thing to
    open -- and with one entry there is no parent to take, only the folder.
    """
    assert plugin._folder_to_open([ENTRY, ENTRY2]).endswith("macintalk")
    assert plugin._folder_to_open([ENTRY]) == ENTRY["folder"]


def test_every_generation_is_declared(plugin):
    """The table is what a generation is added to; an empty one is silence."""
    keys = [g["key"] for g in plugin.GENERATIONS]
    assert keys == ["tiger", "leopard", "snowleopard", "lion"]
    # Chronological, because the dialog lists them in this order and a
    # user looking for 10.6 looks between 10.5 and 10.7.
    for gen in plugin.GENERATIONS:
        assert set(gen) == {"key", "tree", "label", "source", "readme",
                            "oldAddon"}
        # Each generation has to be able to answer the two questions the
        # report asks it, or the Tools menu item raises inside a wx handler.
        assert callable(gen["tree"].explain)
        assert callable(gen["tree"].config_dir)
        # `_check` migrates every generation, covered or not, so a table entry
        # without one is an AttributeError inside a start-up timer thread.
        assert callable(gen["tree"].migrate)
        assert gen["readme"].strip(), gen["key"]


def test_no_two_generations_share_a_folder(plugin):
    """They sit side by side under `macintalk`, one subfolder each.

    Same folder for both would mean one engine's README overwriting the
    other's, and a "do not ask again" for one silencing both.
    """
    folders = {g["tree"].config_dir() for g in plugin.GENERATIONS}
    assert len(folders) == len(plugin.GENERATIONS), folders


# ---------------------------------------------------------------------------
# The README goes into a generation's folder whether or not it is ready.
#
# It used to be written only on the way to the missing-engine dialog, below
# an early `if ok: return` -- so a generation that had always had data never
# got one.  Tomi found exactly that on his own machine: Tiger and Leopard
# carried a README.txt because each had once been empty, Snow Leopard and Lion
# had none because they never were.
#
# `_checkOne` returns before touching wx when the engine is ready, so it can
# be called with no `self` at all.
# ---------------------------------------------------------------------------

class _FakeTree(object):
    def __init__(self, folder, ok):
        self._folder = folder
        self._ok = ok

    def explain(self):
        return self._ok, ["nothing worth saying"]

    def config_dir(self):
        return self._folder


def _gen(folder, ok):
    return {"key": "lion", "tree": _FakeTree(folder, ok),
            "label": "Lion speech", "source": "your own disc",
            "readme": "Lion speech needs Apple's speech engine.\n"}


def test_a_ready_generation_still_gets_its_readme(plugin, tmp_path):
    folder = str(tmp_path / "lion")
    os.makedirs(folder)
    plugin.GlobalPlugin._checkOne(None, _gen(folder, True))
    written = os.path.join(folder, "README.txt")
    assert os.path.isfile(written)
    with open(written, encoding="utf-8") as f:
        assert "Lion speech" in f.read()


def test_a_ready_generation_gets_no_folder_conjured_for_it(plugin, tmp_path):
    """Its tree may live in %ProgramData% or the SAPI world entirely.

    Creating an empty folder in NVDA's configuration directory just to hold a
    note would put a bare `lion` beside four real ones, and the speech data
    manager counts folders.
    """
    folder = str(tmp_path / "not-there")
    plugin.GlobalPlugin._checkOne(None, _gen(folder, True))
    assert not os.path.exists(folder)


class _JustEnoughSelf(object):
    """The not-ready path goes on to schedule the shared dialog."""

    def _askCombined(self):
        pass


def test_an_empty_generation_still_gets_folder_and_readme(plugin, tmp_path):
    """The original behaviour, which was right and stays."""
    folder = str(tmp_path / "made")
    plugin.GlobalPlugin._checkOne(_JustEnoughSelf(), _gen(folder, False))
    assert os.path.isfile(os.path.join(folder, "README.txt"))


def test_an_existing_readme_is_never_overwritten(plugin, tmp_path):
    """Somebody may have written notes of their own in it."""
    folder = str(tmp_path / "lion")
    os.makedirs(folder)
    written = os.path.join(folder, "README.txt")
    with open(written, "w", encoding="utf-8") as f:
        f.write("my own notes")
    plugin.GlobalPlugin._checkOne(None, _gen(folder, True))
    with open(written, encoding="utf-8") as f:
        assert f.read() == "my own notes"
