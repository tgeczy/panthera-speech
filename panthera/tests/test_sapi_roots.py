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


def _fake_winreg(datapath, machinePath=None):
    """A winreg holding `datapath` for this user and `machinePath` for the
    machine, under `Software\\Panthera SAPI`.

    Two hives, because the machine-wide one is the only one a secure screen
    can read: on the sign-in desktop NVDA runs as SYSTEM, whose HKCU is its
    own and holds nothing the signed-in person ever chose.
    """
    mod = types.ModuleType("winreg")
    mod.HKEY_CURRENT_USER = object()
    mod.HKEY_LOCAL_MACHINE = object()
    mod.REG_SZ = 1
    held = {mod.HKEY_CURRENT_USER: datapath,
            mod.HKEY_LOCAL_MACHINE: machinePath}

    def OpenKey(root, path):
        if held.get(root) is None:
            raise OSError("no such key")
        return _FakeKey(held[root])

    def QueryValueEx(key, name):
        return key.value, mod.REG_SZ

    mod.OpenKey = OpenKey
    mod.QueryValueEx = QueryValueEx
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
    assert roots[1] == os.path.join(
        r"C:\Users\someone\AppData\Roaming", "macintalk-data", "leopard")


def test_the_standalone_default_is_found_without_a_choice(monkeypatch):
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(None))
    monkeypatch.setenv("APPDATA", r"C:\Users\someone\AppData\Roaming")
    _noCommonFolder(monkeypatch)
    roots = pantheratrees.sapi_roots("tiger")
    assert roots == [os.path.join(
        r"C:\Users\someone\AppData\Roaming", "macintalk-data", "tiger")]


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

    def OpenKey(root, path):
        raise OSError("no such key")

    mod.OpenKey = OpenKey
    mod.QueryValueEx = lambda key, name: (None, mod.REG_SZ)
    monkeypatch.setitem(sys.modules, "winreg", mod)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setenv("ProgramData", r"C:\ProgramData")
    assert pantheratrees.sapi_roots("tiger") == [
        os.path.join(r"C:\ProgramData", "macintalk-data", "tiger")]
