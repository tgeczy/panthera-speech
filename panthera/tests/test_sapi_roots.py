# -*- coding: utf-8 -*-
"""The add-on finds the SAPI driver's data, both of its places.

The SAPI settings tool already resolves NVDA's macintalk folder so an
NVDA-first user never keeps the data twice; this is the same courtesy
pointed the other way.  Requested by Tomi for 1.3.0: "if they installed
SAPI first and then the NVDA driver, they don't need double places."
"""
import os
import sys
import types

from synthDrivers._panthera import pantheratrees


class _FakeKey(object):
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fake_winreg(datapath, machinePath=None, views=None):
    """A winreg holding `datapath` for this user and `machinePath` for the
    machine, under `Software\\Panthera SAPI`.

    Two hives, because the machine-wide one is the only one a secure screen
    can read: on the sign-in desktop NVDA runs as SYSTEM, whose HKCU is its
    own and holds nothing the signed-in person ever chose.

    Two *views* as well.  Pass `views` as `{(hive, viewFlag): path}` to answer
    differently depending on which one was asked for; by default both views
    hold the same thing, which is what the settings tool arranges and what a
    real machine therefore looks like.  Every call is recorded in `mod.asked`
    so a test can show that both were tried rather than assuming it.
    """
    mod = types.ModuleType("winreg")
    mod.HKEY_CURRENT_USER = object()
    mod.HKEY_LOCAL_MACHINE = object()
    mod.REG_SZ = 1
    #: The real values, so a flag that leaks into a comparison is the same
    #: number a real winreg would have produced.
    mod.KEY_READ = 0x20019
    mod.KEY_WOW64_32KEY = 0x0200
    mod.KEY_WOW64_64KEY = 0x0100
    _VIEWS = mod.KEY_WOW64_32KEY | mod.KEY_WOW64_64KEY
    held = {mod.HKEY_CURRENT_USER: datapath,
            mod.HKEY_LOCAL_MACHINE: machinePath}
    asked = []

    def OpenKey(root, path, reserved=0, access=0):
        view = access & _VIEWS
        asked.append((root, view))
        value = views.get((root, view)) if views is not None else held.get(root)
        if value is None:
            raise OSError("no such key")
        return _FakeKey(value)

    def QueryValueEx(key, name):
        return key.value, mod.REG_SZ

    mod.OpenKey = OpenKey
    mod.QueryValueEx = QueryValueEx
    mod.asked = asked
    return mod


def _noCommonFolder(monkeypatch):
    """No `%ProgramData%`, for the tests that predate it."""
    monkeypatch.delenv("ProgramData", raising=False)
    monkeypatch.delenv("ALLUSERSPROFILE", raising=False)


def test_the_remembered_folder_comes_first(monkeypatch):
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(r"D:\my-voices"))
    monkeypatch.setenv("APPDATA", r"C:\Users\someone\AppData\Roaming")
    _noCommonFolder(monkeypatch)
    roots = pantheratrees.sapi_roots("leopard")
    assert roots[0] == os.path.join(r"D:\my-voices", "leopard")
    # Then the two per-user spellings, bare first: `%APPDATA%\macintalk` is
    # where people keep it beside a SAPI install, and `macintalk-data` is the
    # one the installer picks.
    assert roots[1] == os.path.join(
        r"C:\Users\someone\AppData\Roaming", "macintalk", "leopard")
    assert roots[2] == os.path.join(
        r"C:\Users\someone\AppData\Roaming", "macintalk-data", "leopard")


def test_the_standalone_default_is_found_without_a_choice(monkeypatch):
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(None))
    monkeypatch.setenv("APPDATA", r"C:\Users\someone\AppData\Roaming")
    _noCommonFolder(monkeypatch)
    roots = pantheratrees.sapi_roots("tiger")
    assert roots == [
        os.path.join(r"C:\Users\someone\AppData\Roaming", "macintalk",
                     "tiger"),
        os.path.join(r"C:\Users\someone\AppData\Roaming", "macintalk-data",
                     "tiger"),
    ]


def test_nothing_to_find_is_quietly_nothing(monkeypatch):
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(None))
    monkeypatch.delenv("APPDATA", raising=False)
    _noCommonFolder(monkeypatch)
    assert pantheratrees.sapi_roots("lion") == []


# ---------------------------------------------------------------------------
# The machine-wide answers, which are the only ones a secure screen can read.
#
# On the sign-in desktop NVDA runs as SYSTEM: `HKEY_CURRENT_USER` is SYSTEM's
# and `%APPDATA%` is SYSTEM's, so a tree the signed-in person extracted under
# their own profile is reachable through neither.  The SAPI side's voice
# tokens are already registered in HKLM and are visible there -- it is the
# data behind them that has not been.
# ---------------------------------------------------------------------------

def test_the_machine_wide_choice_is_offered(monkeypatch):
    monkeypatch.setitem(sys.modules, "winreg",
                        _fake_winreg(None, machinePath=r"D:\shared-voices"))
    monkeypatch.delenv("APPDATA", raising=False)
    _noCommonFolder(monkeypatch)
    assert pantheratrees.sapi_roots("leopard") == [
        os.path.join(r"D:\shared-voices", "leopard")]


def test_program_data_is_offered_without_any_choice(monkeypatch):
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(None))
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setenv("ProgramData", r"C:\ProgramData")
    assert pantheratrees.sapi_roots("lion") == [
        os.path.join(r"C:\ProgramData", "macintalk-data", "lion")]


def test_a_chosen_folder_still_outranks_the_shared_ones(monkeypatch):
    """Explicit beats default, and this user beats the machine.

    Somebody who pointed the settings tool at a folder of their own must keep
    getting it, whatever an installer later left for everybody.
    """
    monkeypatch.setitem(sys.modules, "winreg",
                        _fake_winreg(r"D:\mine", machinePath=r"D:\everyones"))
    monkeypatch.setenv("APPDATA", r"C:\Users\someone\AppData\Roaming")
    monkeypatch.setenv("ProgramData", r"C:\ProgramData")
    assert pantheratrees.sapi_roots("tiger") == [
        os.path.join(r"D:\mine", "tiger"),
        os.path.join(r"D:\everyones", "tiger"),
        os.path.join(r"C:\ProgramData", "macintalk-data", "tiger"),
        os.path.join(r"C:\Users\someone\AppData\Roaming", "macintalk",
                     "tiger"),
        os.path.join(r"C:\Users\someone\AppData\Roaming", "macintalk-data",
                     "tiger"),
    ]


def test_a_winreg_without_the_machine_hive_is_not_fatal(monkeypatch):
    """A stand-in registry missing a hive is a hive with nothing in it.

    The suite's own fake had exactly one hive until this change, and reaching
    for the other by attribute took the whole lookup down with an
    `AttributeError` -- turning "no machine-wide folder" into "no folders at
    all", which would have hidden every generation.
    """
    mod = types.ModuleType("winreg")
    mod.HKEY_CURRENT_USER = object()
    mod.REG_SZ = 1

    def OpenKey(root, path, reserved=0, access=0):
        raise OSError("no such key")

    mod.OpenKey = OpenKey
    mod.QueryValueEx = lambda key, name: (None, mod.REG_SZ)
    monkeypatch.setitem(sys.modules, "winreg", mod)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setenv("ProgramData", r"C:\ProgramData")
    assert pantheratrees.sapi_roots("tiger") == [
        os.path.join(r"C:\ProgramData", "macintalk-data", "tiger")]


# ---------------------------------------------------------------------------
# Both registry views, because HKLM\Software is redirected under WOW64.
#
# NVDA is 32-bit, so an unqualified read sees `Wow6432Node` and nothing else.
# The settings tool writes the machine-wide DataPath through both views to
# meet this, but a value put there by a 64-bit tool -- or by hand in regedit,
# which is 64-bit -- would otherwise be perfectly present and entirely
# invisible.  HKCU never needed this: `HKCU\Software` is not redirected, which
# is exactly why the trap arrives with the machine-wide key and not before.
# ---------------------------------------------------------------------------

def test_a_machine_folder_set_only_in_the_64_bit_view_is_still_found(
        monkeypatch):
    # Filled after construction, because the keys are the module's own hive
    # objects: a second `_fake_winreg` would mint different ones and the view
    # table would match nothing.
    views = {}
    mod = _fake_winreg(None, views=views)
    views[(mod.HKEY_LOCAL_MACHINE, mod.KEY_WOW64_64KEY)] = r"D:\shared"
    monkeypatch.setitem(sys.modules, "winreg", mod)
    monkeypatch.delenv("APPDATA", raising=False)
    _noCommonFolder(monkeypatch)
    assert pantheratrees.sapi_roots("lion") == [
        os.path.join(r"D:\shared", "lion")]


def test_both_views_are_asked_and_one_answer_is_offered_once(monkeypatch):
    """A folder in both views is one folder, not two.

    The usual arrangement -- the settings tool writes through both -- must not
    make every caller search the same tree twice.
    """
    mod = _fake_winreg(None, machinePath=r"D:\shared")
    monkeypatch.setitem(sys.modules, "winreg", mod)
    monkeypatch.delenv("APPDATA", raising=False)
    _noCommonFolder(monkeypatch)
    assert pantheratrees.sapi_roots("lion") == [
        os.path.join(r"D:\shared", "lion")]
    machine = [view for hive, view in mod.asked
               if hive is mod.HKEY_LOCAL_MACHINE]
    assert sorted(machine) == sorted([mod.KEY_WOW64_32KEY,
                                      mod.KEY_WOW64_64KEY])
