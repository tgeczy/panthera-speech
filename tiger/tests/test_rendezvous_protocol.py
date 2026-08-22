# -*- coding: utf-8 -*-
"""The three add-ons meet on `globalVars` and must agree on where.

outSPOKEN, tigerspeech and leopardspeech each show a start-up dialog when they
have no engine. Three dialogs stacked on a screen reader is what a user
actually got, so they rendezvous instead: each registers into one shared list
and the first to arrive shows a single dialog naming all of them.

**They cannot import each other** -- separate add-ons, separate repositories --
so the block of code that does this is duplicated three times, and it only
works if the key, the lock key and the entry shape are identical in all three.

This is exactly the shape of bug that cost most of 2026-08-21: two places
answering the same question, drifting apart, nothing failing loudly. A comment
saying "keep these in step" was tried elsewhere in this codebase and did not
survive its own next edit, so this asserts the contract instead. The literals
below *are* the contract; change them here and in all three plugins together
or not at all.
"""
import io
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))

#: Every plugin in this repository that joins the rendezvous. outSPOKEN's copy
#: lives in the other repository and has the same test there.
PLUGINS = [
    os.path.join(ROOT, "tiger", "addon", "globalPlugins",
                 "tigerSpeechData.py"),
    os.path.join(ROOT, "leopard", "addon", "globalPlugins",
                 "leopardSpeechData.py"),
]

CONTRACT = {
    "_REGISTRY": '"_macintalkMissingEngines"',
    "_REGISTRY_LOCK": '"_macintalkMissingEnginesLock"',
    "_RENDEZVOUS_MS": "1500",
    # The Tools menu item is shared too, and by the same means: one entry for
    # every Mac OS X engine rather than one per add-on, because three menu
    # items to answer "what voice data do I have?" is three places to look.
    # outSPOKEN is deliberately not in this list -- classic Mac OS engines,
    # different repository, its own menu item.
    "_REPORTERS": '"_macosxSpeechEngineReporters"',
}

#: Every key the dialog reads out of a registry entry.
ENTRY_KEYS = {"label", "folder", "source"}


def _source(path):
    return io.open(path, encoding="utf-8").read()


@pytest.mark.parametrize("path", PLUGINS, ids=lambda p: os.path.basename(p))
@pytest.mark.parametrize("name,value", sorted(CONTRACT.items()))
def test_the_meeting_place_is_the_same_in_every_plugin(path, name, value):
    got = re.search(r"^%s = (.+)$" % name, _source(path), re.M)
    assert got, "%s does not define %s" % (os.path.basename(path), name)
    assert got.group(1).strip() == value, (
        "%s has %s = %s; the other add-ons use %s, so they would hold two "
        "separate meetings and each show its own dialog"
        % (os.path.basename(path), name, got.group(1).strip(), value))


@pytest.mark.parametrize("path", PLUGINS, ids=lambda p: os.path.basename(p))
def test_every_plugin_registers_the_keys_the_dialog_reads(path):
    """A missing key is a KeyError inside a wx handler, which NVDA swallows
    into the log -- so it presents as no dialog at all, which is the failure
    this whole mechanism exists to fix."""
    src = _source(path)
    call = re.search(r"_register_missing\(\{(.+?)\}\)", src, re.S)
    assert call, "%s never registers" % os.path.basename(path)
    keys = set(re.findall(r'"(\w+)":', call.group(1)))
    assert keys == ENTRY_KEYS, (
        "%s registers %s; the dialog reads %s"
        % (os.path.basename(path), sorted(keys), sorted(ENTRY_KEYS)))


@pytest.mark.parametrize("path", PLUGINS, ids=lambda p: os.path.basename(p))
def test_the_registry_is_appended_under_a_lock(path):
    """`append` then `len(...) == 1` is two operations. Two threads can
    interleave so that neither sees itself as first, and then nobody shows the
    dialog -- a race whose symptom is silence, on a screen reader."""
    src = _source(path)
    assert "setdefault(_REGISTRY_LOCK" in src, (
        "%s does not take the shared lock" % os.path.basename(path))
    body = src[src.index("def _register_missing"):]
    body = body[:body.index("\ndef ")]
    assert "with lock:" in body, (
        "%s registers without holding the lock" % os.path.basename(path))


@pytest.mark.parametrize("path", PLUGINS, ids=lambda p: os.path.basename(p))
def test_every_plugin_registers_what_the_report_reads(path):
    """The Tools menu item is owned by whichever add-on loads first and reads
    every registered entry, so a missing key here is an exception inside a wx
    handler -- which NVDA swallows into the log, so it presents as a menu item
    that does nothing."""
    src = _source(path)
    call = re.search(r"_register_reporter\(\{(.+?)\n        \}\)", src, re.S)
    assert call, "%s never registers a reporter" % os.path.basename(path)
    keys = set(re.findall(r'"(\w+)":', call.group(1)))
    assert keys == {"label", "source", "explain", "folder"}, (
        "%s registers %s" % (os.path.basename(path), sorted(keys)))


@pytest.mark.parametrize("path", PLUGINS, ids=lambda p: os.path.basename(p))
def test_only_the_owner_adds_the_menu_item(path):
    """Both add-ons adding one is the bug being fixed. The non-owner never
    sets `_menuItem`, which is also what makes `terminate` correct: it only
    removes an item it created."""
    src = _source(path)
    assert re.search(r"if _register_reporter\(\{.+?\}\):\s*\n\s*"
                     r"self\._addMenuItem\(\)", src, re.S), (
        "%s adds the menu item unconditionally" % os.path.basename(path))
