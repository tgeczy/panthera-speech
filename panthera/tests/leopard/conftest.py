# -*- coding: utf-8 -*-
"""Leopard's engine fixtures.

Scoped to this directory on purpose. Tiger's conftest calls its fixtures
exactly the same things, and pytest keeps them apart because a conftest only
reaches its own folder and below. The NVDA fakes both generations share are
one level up.
"""
import os

import pytest


@pytest.fixture(scope="session")
def engine_tree():
    from synthDrivers import leopardspeech
    tree = leopardspeech.find_tree()
    if not tree:
        pytest.skip("no Leopard speech tree; set LEOPARD_TREE")
    if not os.path.isfile(leopardspeech.HOST_EXE):
        pytest.skip("panthera_host.exe not built; run sh build.sh")
    return tree


@pytest.fixture
def driver(engine_tree):
    from synthDrivers import leopardspeech
    d = leopardspeech.SynthDriver()
    yield d
    d.terminate()
