# -*- coding: utf-8 -*-
"""The NVDA driver, on an engine folder outside the system code page, speaks
and keeps streaming on (panthera-speech#27).

The host test (tests/test_unicode_engine_path.py) proves the host opens such a
tree.  This is the listener's side of the same bug: the driver found the tree,
started a host that could not open it, saw every render fail, turned streaming
off for the session and logged "reinstall the add-on" -- and NVDA was silent.
"""
import os

import pytest

import unicode_tree
from test_leopard_driver import _warm, _speakAndWait
from unicode_tree import NAME


@pytest.fixture
def unicode_driver(engine_tree, monkeypatch):
    with unicode_tree.junction(engine_tree, "leopard") as link:
        monkeypatch.setenv("LEOPARD_TREE", link)
        from synthDrivers import leopardspeech
        d = leopardspeech.SynthDriver()
        try:
            yield d, link
        finally:
            d.terminate()


def test_the_driver_speaks_from_outside_the_code_page(unicode_driver):
    d, link = unicode_driver
    assert os.path.normcase(d._mt).startswith(os.path.normcase(link)), \
        "the driver did not take the tree under %r" % NAME
    _warm(d)
    _feeds, spoken = _speakAndWait(d, ["Hello there, from a folder named %s." % NAME])
    assert spoken > 0, "the driver was silent under %r" % NAME
    assert d._streaming, "streaming was turned off: the host could not open the engine"
