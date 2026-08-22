# -*- coding: utf-8 -*-
"""Every `wx.NAME` in the add-on, checked against the ones wxPython has.

Repository-wide on purpose: it walks the whole `addon` tree, drivers and
global plugin alike. It used to be a copy in each driver's test file, which
is two allowed-name sets to keep in step and one of them silently going
stale.
"""
import os
import re


def test_every_wx_name_we_use_actually_exists_in_wxpython():
    """A misspelt wx constant is invisible until a user sends in a log.

    `YES_NO_CANCEL` is real in wxWidgets' C++ API and absent from wxPython.
    The start-up dialog asked for it, so `_ask` raised AttributeError every
    time it ran, and that dialog had never once appeared in any release of
    either add-on. It presented as nothing happening -- which is also what a
    missing add-on, a suppressed reminder and a mistimed thread all look like,
    so it was blamed on each of those in turn before a user's log named it.

    wxPython is not installed here and should not have to be: this reads the
    source and checks every `wx.NAME` against the ones wxPython really has.
    Add to the set when a genuinely new one is needed -- deliberately, which is
    the whole point of it being a list.
    """
    known = {
        # message box styles and answers
        "OK", "CANCEL", "YES", "NO", "YES_NO", "OK_DEFAULT", "NO_DEFAULT",
        "ICON_INFORMATION", "ICON_WARNING", "ICON_ERROR", "ICON_QUESTION",
        "CENTRE", "CENTER",
        # scheduling
        "CallAfter", "CallLater",
        # menus, if a future version grows one
        "ID_ANY", "EVT_MENU", "Menu", "MenuItem",
    }

    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.join(os.path.dirname(here), "addon")
    assert os.path.isdir(root), root

    used = {}
    scanned = 0
    for dirpath, _dirs, names in os.walk(root):
        for n in names:
            if not n.endswith(".py"):
                continue
            scanned += 1
            with open(os.path.join(dirpath, n), encoding="utf-8") as f:
                for name in re.findall(r"\bwx\.([A-Za-z_][A-Za-z0-9_]*)",
                                       f.read()):
                    used.setdefault(name, n)

    # The first version of this test matched nothing at all and passed
    # vacuously, which is worse than not having it: an escaping mistake had
    # turned the \b in the pattern into a literal backspace byte. So prove it
    # is looking at something before trusting what it says.
    assert scanned, "scanned no Python files under %s" % root
    assert used, "found no wx names at all -- the pattern is broken"

    unknown = {k: v for k, v in used.items() if k not in known}
    assert not unknown, (
        "these wx names are not in the allowed set. Check they exist in "
        "wxPython -- YES_NO_CANCEL does not -- then add them here: %r"
        % unknown)
