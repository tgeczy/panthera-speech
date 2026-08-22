# -*- coding: utf-8 -*-
"""Every `wx.NAME` in the add-on, checked against the ones wxPython has.

Repository-wide on purpose: it walks the whole `addon` tree, drivers and
global plugin alike. It used to be a copy in each driver's test file, which
is two allowed-name sets to keep in step and one of them silently going
stale.
"""
import os
import re
import sys


#: Asks a real wxPython which of these names it has.  Run as its own process
#: on purpose -- see `_wx_missing`.
_PROBE = """
import os, sys
for base in (os.environ.get("NVDA_PATH"), r"C:\\Program Files\\NVDA",
             r"C:\\Program Files (x86)\\NVDA"):
    if base and os.path.isdir(base):
        sys.path.insert(0, os.path.join(base, "library.zip"))
        sys.path.insert(0, base)
try:
    import wx
except Exception:
    print("NOWX")
else:
    missing = [n for n in sys.argv[1:] if not hasattr(wx, n)]
    print(wx.__version__)
    print(" ".join(missing))
"""


def _wx_missing(names):
    """-> (version, [names wxPython lacks]), or (None, []) if none was found.

    **In a subprocess, and that is not caution.**  This suite fakes `wx` so the
    drivers can be imported without NVDA, so an in-process `import wx` returns
    the fake -- which has almost none of these names, and the check inverts
    into failing on every correct one.  It passed alone and failed in the full
    run, which is the signature of exactly this.

    NVDA ships wxPython, and on a machine that has NVDA this reads the same
    build the add-on will really run against.  Where there is no NVDA it
    answers `None` and the curated list above stands on its own.
    """
    import subprocess
    try:
        done = subprocess.run([sys.executable, "-c", _PROBE] + sorted(names),
                              capture_output=True, text=True, timeout=120)
    except Exception:
        return None, []
    out = done.stdout.strip().split("\n")
    if not out or out[0].strip() == "NOWX":
        return None, []
    version = out[0].strip()
    missing = out[1].split() if len(out) > 1 else []
    return version, missing


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
        # menus
        "ID_ANY", "EVT_MENU", "Menu", "MenuItem",
        # the speech data dialog
        "Dialog", "Panel", "BoxSizer", "StaticText", "TextCtrl", "ListBox",
        "Button", "Gauge", "FileDialog",
        "VERTICAL", "HORIZONTAL", "EXPAND", "ALL", "RIGHT",
        "ALIGN_RIGHT", "ALIGN_CENTER_VERTICAL",
        "LB_SINGLE", "TE_MULTILINE", "TE_READONLY", "TE_DONTWRAP",
        "FD_OPEN", "FD_FILE_MUST_EXIST",
        "ID_OK", "ID_CLOSE",
        "EVT_BUTTON", "EVT_CLOSE", "EVT_LISTBOX",
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

    # And where a real wxPython can be found, stop taking the list's word for
    # it.  This is the check the list was standing in for.
    version, absent = _wx_missing(used)
    assert not absent, (
        "wxPython %s does not have these, whatever the list above says: %s"
        % (version, ", ".join("%s (%s)" % (n, used[n]) for n in absent)))
