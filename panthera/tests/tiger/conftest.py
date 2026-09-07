# -*- coding: utf-8 -*-
"""Tiger's engine fixtures.

Scoped to this directory on purpose. Leopard's conftest calls its fixtures
exactly the same things, and pytest keeps them apart because a conftest only
reaches its own folder and below. The NVDA fakes both generations share are
one level up.
"""
import os

import pytest


@pytest.fixture(scope="session")
def engine_tree():
    import tigerspeech
    tree = tigerspeech.find_tree()
    if not tree:
        pytest.skip("no Tiger speech tree; set TIGER_TREE")
    if not os.path.isfile(tigerspeech.HOST_EXE):
        pytest.skip("panthera_host.exe not built; run sh build.sh")
    return tree


@pytest.fixture
def driver(engine_tree):
    import tigerspeech
    d = tigerspeech.SynthDriver()
    yield d
    d.terminate()
