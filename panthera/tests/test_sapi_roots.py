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


def _fake_winreg(datapath):
    """A winreg whose HKCU\\Software\\Panthera SAPI holds `datapath`."""
    mod = types.ModuleType("winreg")
    mod.HKEY_CURRENT_USER = object()
    mod.REG_SZ = 1

    def OpenKey(root, path):
        if datapath is None:
            raise OSError("no such key")
        return _FakeKey(datapath)

    def QueryValueEx(key, name):
        return key.value, mod.REG_SZ

    mod.OpenKey = OpenKey
    mod.QueryValueEx = QueryValueEx
    return mod


def test_the_remembered_folder_comes_first(monkeypatch):
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(r"D:\my-voices"))
    monkeypatch.setenv("APPDATA", r"C:\Users\someone\AppData\Roaming")
    roots = pantheratrees.sapi_roots("leopard")
    assert roots[0] == os.path.join(r"D:\my-voices", "leopard")
    assert roots[1] == os.path.join(
        r"C:\Users\someone\AppData\Roaming", "macintalk-data", "leopard")


def test_the_standalone_default_is_found_without_a_choice(monkeypatch):
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(None))
    monkeypatch.setenv("APPDATA", r"C:\Users\someone\AppData\Roaming")
    roots = pantheratrees.sapi_roots("tiger")
    assert roots == [os.path.join(
        r"C:\Users\someone\AppData\Roaming", "macintalk-data", "tiger")]


def test_nothing_to_find_is_quietly_nothing(monkeypatch):
    monkeypatch.setitem(sys.modules, "winreg", _fake_winreg(None))
    monkeypatch.delenv("APPDATA", raising=False)
    assert pantheratrees.sapi_roots("lion") == []
