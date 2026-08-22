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


def test_both_generations_are_declared(plugin):
    """The table is what a generation is added to; an empty one is silence."""
    keys = [g["key"] for g in plugin.GENERATIONS]
    assert keys == ["tiger", "leopard"]
    for gen in plugin.GENERATIONS:
        assert set(gen) == {"key", "tree", "label", "source", "readme",
                            "oldAddon"}
        # Each generation has to be able to answer the two questions the
        # report asks it, or the Tools menu item raises inside a wx handler.
        assert callable(gen["tree"].explain)
        assert callable(gen["tree"].config_dir)
        assert gen["readme"].strip(), gen["key"]


def test_the_two_generations_do_not_share_a_folder(plugin):
    """They sit side by side under `macintalk`, one subfolder each.

    Same folder for both would mean one engine's README overwriting the
    other's, and a "do not ask again" for one silencing both.
    """
    folders = {g["tree"].config_dir() for g in plugin.GENERATIONS}
    assert len(folders) == len(plugin.GENERATIONS), folders
