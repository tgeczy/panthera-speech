# -*- coding: utf-8 -*-
"""Lion's engine fixtures.

Scoped to this directory on purpose.  Tiger's and Leopard's conftests call
their fixtures exactly the same things, and pytest keeps them apart because a
conftest only reaches its own folder and below.  The NVDA fakes all three
generations share are one level up.
"""
import os

import pytest


@pytest.fixture(scope="session")
def engine_tree():
    from synthDrivers import lionspeech
    tree = lionspeech.find_tree()
    if not tree:
        pytest.skip("no Lion speech tree; set LION_TREE")
    if not os.path.isfile(lionspeech.HOST_EXE):
        pytest.skip("panthera_host.exe not built; run sh build.sh")
    return tree


@pytest.fixture
def driver(engine_tree):
    from synthDrivers import lionspeech
    d = lionspeech.SynthDriver()
    yield d
    d.terminate()
