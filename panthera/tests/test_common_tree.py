# -*- coding: utf-8 -*-
"""A tree in `%ProgramData%\\macintalk` is found, by all four generations.

**The bug this closes was one folder name wide.**  `find_tree` already reached
into `%ProgramData%` -- but only through `sapi_roots`, which spells the folder
`macintalk-data`, because that is what the SAPI installer calls it.  NVDA's own
folder is called `macintalk`.

So Tomi moved his `macintalk` folder to `%ProgramData%`, restarted NVDA, and
got "5 Macintosh speech engines are missing" -- four Panthera generations and
outSPOKEN, every one of them looking at a machine-wide root spelled one word
differently from the one he had made.  Nothing in the report said so, because
nothing had looked.

His reason for putting it there is the good one, and it is the opposite of the
recorded design: data inside NVDA's configuration directory reaches the
sign-in screen only because NVDA copies that whole directory into
`systemConfig`, 1.6 GB of voice banks and all.  A tree at an absolute path
under `%ProgramData%` is read by SYSTEM directly, with no copy at all.

Written as a property over all four rather than four tests, because the point
is that no generation may forget: a fifth one that does is what this notices.
"""
import os

import pytest

GENERATIONS = [
    ("pantheratiger", "tiger"),
    ("pantheraleopard", "leopard"),
    ("pantherasnowleopard", "snowleopard"),
    ("pantheralion", "lion"),
]


def _tree_module(name):
    import importlib
    return importlib.import_module("synthDrivers._panthera." + name)


def _make_tree(root):
    """A directory `is_tree` will accept."""
    os.makedirs(os.path.join(root, "Speech", "Voices"))
    return root


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """No config folder, no SAPI registry, no environment overrides.

    So that anything found is found through the machine-wide folder and
    nowhere else -- otherwise this passes on a machine that simply has data.
    """
    import sys
    import types
    empty = str(tmp_path / "nvda-config")
    os.makedirs(empty)
    monkeypatch.setenv("PANTHERA_TEST_CONFIG", empty)
    for name, key in GENERATIONS:
        monkeypatch.delenv("%s_TREE" % key.upper(), raising=False)
        module = _tree_module(name)
        monkeypatch.setattr(module, "config_base", lambda _e=empty: _e)
    # A winreg with nothing in it, so no SAPI DataPath can answer instead.
    winreg = types.ModuleType("winreg")
    winreg.HKEY_CURRENT_USER = object()
    winreg.HKEY_LOCAL_MACHINE = object()
    winreg.REG_SZ = 1

    def OpenKey(*a, **k):
        raise OSError("no such key")

    winreg.OpenKey = OpenKey
    winreg.QueryValueEx = lambda *a: (None, 1)
    monkeypatch.setitem(sys.modules, "winreg", winreg)
    monkeypatch.delenv("APPDATA", raising=False)
    return tmp_path


@pytest.mark.parametrize("name,key", GENERATIONS)
def test_the_machine_wide_macintalk_folder_is_found(isolated, monkeypatch,
                                                    name, key):
    """`%ProgramData%\\macintalk\\<generation>` -- exactly what Tomi made."""
    common = str(isolated / "common")
    module = _tree_module(name)
    _make_tree(os.path.join(common, "macintalk", key))
    monkeypatch.setenv("ProgramData", common)
    assert module.find_tree() == os.path.join(common, "macintalk", key)


@pytest.mark.parametrize("name,key", GENERATIONS)
def test_a_folder_dropped_in_whole_is_found_there_too(isolated, monkeypatch,
                                                      name, key):
    """One level down, because that is what people actually do.

    The same courtesy the configuration folder has always paid.
    """
    common = str(isolated / "common")
    module = _tree_module(name)
    inner = os.path.join(common, "macintalk", key, "extracted")
    _make_tree(inner)
    monkeypatch.setenv("ProgramData", common)
    assert module.find_tree() == inner


@pytest.mark.parametrize("name,key", GENERATIONS)
def test_no_machine_wide_folder_finds_nothing_and_raises_nothing(
        isolated, monkeypatch, name, key):
    """The ordinary machine, where none of this exists."""
    monkeypatch.setenv("ProgramData", str(isolated / "empty"))
    assert _tree_module(name).find_tree() is None


def test_the_sapi_folder_name_still_works(isolated, monkeypatch):
    """`macintalk-data` was already reachable and must stay so.

    The fix adds a name; it must not have moved one.
    """
    common = str(isolated / "common")
    _make_tree(os.path.join(common, "macintalk-data", "lion"))
    monkeypatch.setenv("ProgramData", common)
    assert _tree_module("pantheralion").find_tree() == os.path.join(
        common, "macintalk-data", "lion")
