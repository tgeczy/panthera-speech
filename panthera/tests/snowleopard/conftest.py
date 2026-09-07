# -*- coding: utf-8 -*-
"""Snow Leopard's engine fixtures.

Scoped to this directory on purpose.  All four generations call their fixtures
exactly the same things, and pytest keeps them apart because a conftest only
reaches its own folder and below.  The NVDA fakes they share are one level up.
"""
import os

import pytest


@pytest.fixture(scope="session")
def engine_tree():
    import snowleopardspeech
    tree = snowleopardspeech.find_tree()
    if not tree:
        pytest.skip("no Snow Leopard speech tree; set SNOWLEOPARD_TREE")
    if not os.path.isfile(snowleopardspeech.HOST_EXE):
        pytest.skip("panthera_host.exe not built; run sh build.sh")
    return tree


@pytest.fixture
def driver(engine_tree):
    import snowleopardspeech
    d = snowleopardspeech.SynthDriver()
    yield d
    d.terminate()
