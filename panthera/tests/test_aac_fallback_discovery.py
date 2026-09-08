"""A Windows N/KN machine must still offer its locally installed AAC voices."""
import sys
import types

import pytest


@pytest.mark.parametrize("generation", ["tiger", "leopard", "snowleopard", "lion"])
def test_missing_windows_codec_does_not_hide_bundled_aac(monkeypatch, generation):
    import importlib
    registry = types.ModuleType("winreg")
    registry.HKEY_CLASSES_ROOT = object()
    registry.KEY_READ = 0x20019
    registry.KEY_WOW64_32KEY = 0x200
    registry.KEY_WOW64_64KEY = 0x100

    def missing(*args):
        raise FileNotFoundError("Windows AAC codec is not registered")

    registry.OpenKey = missing
    monkeypatch.setitem(sys.modules, "winreg", registry)
    tree = importlib.import_module("synthDrivers._panthera.panthera" + generation)
    assert tree.aac_available()
