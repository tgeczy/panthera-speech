# -*- coding: utf-8 -*-
"""The add-ons meet on `globalVars` and must agree on where.

Panthera and outSPOKEN each show a start-up dialog when they have no engine.
Three dialogs stacked on a screen reader is what a user actually got, so they
rendezvous instead: each registers into one shared list and the first to
arrive shows a single dialog naming all of them.

**They cannot import each other** -- separate add-ons, separate repositories --
so the block of code that does this is duplicated, and it only works if the
key, the lock key and the entry shape are identical in every copy.

There are more copies than the two in this repository and outSPOKEN's. The
`tigerspeech` and `leopardspeech` add-ons that this one replaces speak the
0.8.0 version of the same protocol, and they are still installed on anybody
mid-upgrade -- so the literals cannot be changed even now that the two plugins
they lived in have become one.

This is exactly the shape of bug that cost most of 2026-08-21: two places
answering the same question, drifting apart, nothing failing loudly. A comment
saying "keep these in step" was tried elsewhere in this codebase and did not
survive its own next edit, so this asserts the contract instead. The literals
below *are* the contract; change them here and in every plugin together or not
at all.
"""
import io
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
ADDON = os.path.join(ROOT, "panthera", "addon")

#: Every plugin in this repository that joins the rendezvous. There is one now;
#: there were two until Tiger and Leopard became one add-on. outSPOKEN's copy
#: lives in the other repository and has the same test there.
PLUGINS = [
    os.path.join(ADDON, "globalPlugins", "pantheraData.py"),
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

#: The synthesizer module names, which are frozen. NVDA keys every speech
#: setting by synth name, so renaming one of these resets the voice, rate,
#: pitch and volume of everybody who had it selected. The *add-on* name was
#: free to change and did; these were not and did not.
DRIVERS = ("tigerspeech.py", "leopardspeech.py")


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
    calls = re.findall(r"_register_reporter\(\{(.+?)\n\s*\}\)", src, re.S)
    assert calls, "%s never registers a reporter" % os.path.basename(path)
    # *Every* registration, not the first one. There is more than one now --
    # the generations, and the older-add-ons conflict, which registers as an
    # entry of its own precisely so an older plugin's report will carry it.
    for call in calls:
        keys = set(re.findall(r'"(\w+)":', call))
        assert keys == {"label", "source", "explain", "folder"}, (
            "%s registers %s" % (os.path.basename(path), sorted(keys)))


@pytest.mark.parametrize("path", PLUGINS, ids=lambda p: os.path.basename(p))
def test_only_the_owner_adds_the_menu_item(path):
    """Two add-ons adding one is the bug being fixed. The non-owner never sets
    `_menuItem`, which is also what makes `terminate` correct: it only removes
    an item it created.

    One plugin now registers a reporter per generation, so ownership is the OR
    of those registrations rather than a single call -- but it still has to be
    decided by `_register_reporter` and by nothing else."""
    src = _source(path)
    assert re.search(r"if _register_reporter\(\{.+?\}\):\s*\n\s*owner = True",
                     src, re.S), (
        "%s does not take ownership from _register_reporter"
        % os.path.basename(path))
    assert re.search(r"\n        if owner:\n            self\._addMenuItem\(\)",
                     src), (
        "%s adds the menu item unconditionally" % os.path.basename(path))
    assert src.count("self._addMenuItem()") == 1, (
        "%s has more than one route to the menu item"
        % os.path.basename(path))


# -- the add-ons this one replaces ----------------------------------------

def _manifest():
    out = {}
    for line in io.open(os.path.join(ADDON, "manifest.ini"), encoding="utf-8"):
        if "=" in line and not line.startswith(" "):
            k, v = line.split("=", 1)
            out.setdefault(k.strip(), v.strip())
    return out


def test_the_drivers_keep_the_names_nvda_stores_settings_under():
    """The add-on was renamed; the synthesizers must not be.

    NVDA's speech config is keyed by synth name, so `tigerspeech` becoming
    anything else silently resets voice, rate, pitch and volume for everyone
    who had it selected -- and looks, from the user's side, like the upgrade
    ate their settings."""
    for name in DRIVERS:
        assert os.path.isfile(os.path.join(ADDON, "synthDrivers", name)), name


def test_the_addon_does_not_offer_to_remove_itself():
    """`_old_addons` matches installed add-ons by name against `oldAddon`.

    If the merged add-on had kept one of those names it would find *itself* in
    `getRunningAddons()` and offer to remove it -- an upgrade that uninstalls
    the thing you just installed, once per start-up."""
    src = _source(PLUGINS[0])
    old = set(re.findall(r'"oldAddon": "(\w+)"', src))
    assert old == {"tigerspeech", "leopardspeech"}, sorted(old)
    assert _manifest()["name"] not in old, (
        "the add-on is called %s, which is also an add-on it replaces"
        % _manifest()["name"])


def test_a_generation_an_older_addon_still_covers_is_not_registered_twice():
    """Both plugins run while somebody is mid-upgrade.

    The old `tigerspeech` add-on registers "Tiger speech" into the shared list
    and the shared reporter list, and this add-on would register it again --
    so the one combined dialog would name the same engine twice, and the Tools
    report list it twice. The guard has to be on both registrations, not only
    the one that shows."""
    src = _source(PLUGINS[0])
    for where in ("if _register_reporter", "self._checkOne(gen)"):
        i = src.index(where)
        # `_covered` has to be consulted before reaching either registration.
        before = src[:i]
        assert before.rindex("if self._covered(gen):") > before.rindex(
            "for gen in GENERATIONS:"), (
            "the loop reaching %r does not skip a covered generation" % where)


def test_nothing_is_removed_without_being_asked():
    """They are the user's add-ons.

    `requestRemove` marks an add-on for deletion on the next restart. It must
    sit behind a Yes, and the dialog must be the one asking -- an upgrade that
    quietly uninstalls something is indistinguishable, from outside, from an
    upgrade that broke it."""
    src = _source(PLUGINS[0])
    body = src[src.index("def _askConflict"):]
    ask = body.index("gui.messageBox")
    guard = body.index("if answer != wx.YES:")
    remove = body.index("requestRemove()")
    assert ask < guard < remove, (
        "requestRemove is reached without a Yes in front of it")
