# -*- coding: utf-8 -*-
"""Exactly one Panthera entry in the synthesizer list when nothing can speak.

**The bug this closes was two reasonable decisions meeting.**  Tiger and
Leopard always listed themselves and explained on selection, because hiding had
once left people with an add-on, no synthesizer and nothing to go on.  Lion
listed itself only when it had an engine, because the list is read aloud one
item at a time and nobody should arrow past dead entries.

Timothy Wynn installed the add-on with no data at all and found `Leopard speech
(Alex, MacinTalk 3.6)` selectable and mute, next to no Lion at all.  His fix,
which is the one implemented:

    "Why not make it so that if no synth data is available, populate Panthera
    speech as the placeholder?  And when one or more synth is present, it will
    get rid of the other placeholders."

The property worth testing is not any single `check()`.  It is the invariant
across all four: **every generation appears only when it can speak, and the
placeholder appears only when none of them can.**  A future fifth generation
that forgets its `check()` breaks that, and this is what notices.
"""
import pytest


#: The real drivers, and the tree module each one's availability comes from.
GENERATIONS = [
    ("tigerspeech", "pantheratiger"),
    ("leopardspeech", "pantheraleopard"),
    ("snowleopardspeech", "pantherasnowleopard"),
    ("lionspeech", "pantheralion"),
]


def _drivers():
    """-> {module name: SynthDriver class}, including the placeholder."""
    import importlib
    out = {}
    for name, _tree in GENERATIONS + [("pantheraspeech", None)]:
        out[name] = importlib.import_module(name).SynthDriver
    return out


@pytest.fixture
def usable(monkeypatch):
    """Say which generations have data, without needing any on disk."""
    import importlib

    def setUsable(**flags):
        for name, treeName in GENERATIONS:
            tree = importlib.import_module(treeName)
            monkeypatch.setattr(tree, "usable",
                                lambda v=flags.get(name, False): v)
    return setUsable


def offered(drivers):
    """-> the names NVDA would put in the synthesizer list."""
    return {name for name, cls in drivers.items() if cls.check()}


def test_nothing_installed_offers_only_the_placeholder(usable):
    usable()
    assert offered(_drivers()) == {"pantheraspeech"}


@pytest.mark.parametrize("present", [g[0] for g in GENERATIONS])
def test_one_generation_installed_offers_only_that_one(usable, present):
    """No placeholder beside a working synthesizer, and no dead siblings."""
    usable(**{present: True})
    assert offered(_drivers()) == {present}


def test_all_installed_offers_every_generation_and_no_placeholder(usable):
    """Driven from the table rather than a list of names.

    The version of this that named its three drivers passed unchanged when a
    fourth was added, because it was still asking about three.  A test that
    cannot notice a new generation is not testing the invariant its own
    docstring claims.
    """
    names = {g[0] for g in GENERATIONS}
    usable(**{name: True for name in names})
    assert offered(_drivers()) == names


def test_the_placeholder_never_loads(usable):
    """Choosing it must fail, so NVDA keeps the synthesizer already speaking.

    A placeholder that *loaded* would be the old 32-bit DLL case again: a
    synthesizer sitting there selected and silent, which for a screen reader is
    the only failure that really matters.
    """
    usable()
    import pantheraspeech
    with pytest.raises(RuntimeError):
        pantheraspeech.SynthDriver()


def test_a_generation_that_raises_still_leaves_the_placeholder(monkeypatch,
                                                               usable):
    """An engine check that throws is a reason to show the tool, not to hide it."""
    import importlib
    usable()

    def boom():
        raise OSError("the folder is not readable")

    monkeypatch.setattr(importlib.import_module("pantheraleopard"), "usable",
                        boom)
    import pantheraspeech
    assert pantheraspeech.SynthDriver.check() is True
