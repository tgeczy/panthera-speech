# -*- coding: utf-8 -*-
"""Moving the tree under `macintalk`, which touches the user's own data.

This is the 717 MB case. Every other test here can be wrong and cost a bad
render; this one can be wrong and cost somebody an Alex they extracted from a
DVD they may no longer have to hand. So it is written from that end: what
must never happen, first.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "addon", "synthDrivers",
                                "_leopardspeech"))


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """A throwaway NVDA configuration directory."""
    import leopardtree
    monkeypatch.setattr(leopardtree, "config_base", lambda: str(tmp_path))
    # `find_tree` honours LEOPARD_TREE above everything, which is right for a
    # developer and wrong for a test about where the folder is.
    monkeypatch.delenv("LEOPARD_TREE", raising=False)
    return str(tmp_path)


def _plant(folder):
    """A directory that `is_tree` will accept."""
    os.makedirs(os.path.join(folder, "Speech", "Voices"), exist_ok=True)
    with open(os.path.join(folder, "Speech", "Voices", "Alex"), "wb") as f:
        f.write(b"not really 701 MB")
    return folder


def test_the_tree_is_moved_not_copied(cfg):
    import leopardtree
    _plant(os.path.join(cfg, "leopardspeech-data"))
    assert leopardtree.migrate()
    assert not os.path.exists(os.path.join(cfg, "leopardspeech-data"))
    assert leopardtree.is_tree(os.path.join(cfg, "macintalk", "leopard"))


def test_the_extractors_old_default_is_found_too(cfg):
    """`leopard-data`, and this is a bug being fixed rather than tidied.

    `extract_leopard.py` defaulted to writing `%APPDATA%\\nvda\\leopard-data`
    while this module only ever looked in `leopardspeech-data`. Anyone who
    took the extractor at its word had a tree the add-on could not find and no
    way to tell why.
    """
    import leopardtree
    _plant(os.path.join(cfg, "leopard-data"))
    assert leopardtree.migrate()
    assert leopardtree.is_tree(os.path.join(cfg, "macintalk", "leopard"))


def test_it_never_moves_on_top_of_an_existing_folder(cfg):
    """The case that would destroy an Alex: both folders present.

    Install, downgrade, re-extract, upgrade again. If migration overwrote or
    merged, one of the two copies loses. It must decline and leave both.

    **This test passes with `migrate`'s own guard removed**, and that is worth
    knowing rather than mistaking for proof: Windows refuses `os.rename` onto
    an existing destination, so the OSError path returns None and the outcome
    is the same. Checked by disabling the guard and re-running. The guard
    stays because it states the intent and because POSIX will happily rename
    onto an empty directory, but the assertion below is testing the platform
    as much as the code.
    """
    import leopardtree
    _plant(os.path.join(cfg, "leopardspeech-data"))
    _plant(os.path.join(cfg, "macintalk", "leopard"))
    assert leopardtree.migrate() is None
    assert leopardtree.is_tree(os.path.join(cfg, "leopardspeech-data"))
    assert leopardtree.is_tree(os.path.join(cfg, "macintalk", "leopard"))


def test_a_refused_move_changes_nothing(cfg, monkeypatch):
    """Windows refuses to rename a directory with a file open inside it.

    Not an error to recover from -- the ordinary case of the engine being in
    use. Change nothing, and go on reading the old location.
    """
    import leopardtree
    _plant(os.path.join(cfg, "leopardspeech-data"))
    monkeypatch.setattr(os, "rename", lambda *a, **k: (_ for _ in ()).throw(
        OSError(32, "The process cannot access the file")))
    assert leopardtree.migrate() is None
    assert leopardtree.is_tree(os.path.join(cfg, "leopardspeech-data"))
    assert leopardtree.find_tree() == os.path.join(cfg, "leopardspeech-data")


def test_the_breadcrumb_is_what_an_older_add_on_already_reads(cfg):
    """The pointer file is load-bearing, not a note to a human.

    `find_tree` in every previous release reads `leopardspeech-data.txt`. By
    writing the new path there, a user who rolls back -- or who has an older
    sibling add-on installed beside this one -- still finds the tree.
    """
    import leopardtree
    _plant(os.path.join(cfg, "leopardspeech-data"))
    moved = leopardtree.migrate()
    pointer = os.path.join(cfg, "leopardspeech-data.txt")
    assert os.path.isfile(pointer), "no breadcrumb was left"
    assert open(pointer, encoding="utf-8").read().strip() == moved


def test_a_pointer_the_user_wrote_is_never_overwritten(cfg):
    """Somebody keeping a 717 MB tree on another drive said so in that file."""
    import leopardtree
    pointer = os.path.join(cfg, "leopardspeech-data.txt")
    with open(pointer, "w", encoding="utf-8") as f:
        f.write("D:\\my-own-leopard")
    _plant(os.path.join(cfg, "leopardspeech-data"))
    leopardtree.migrate()
    assert open(pointer, encoding="utf-8").read() == "D:\\my-own-leopard"


def test_migrating_twice_is_harmless(cfg):
    import leopardtree
    _plant(os.path.join(cfg, "leopardspeech-data"))
    assert leopardtree.migrate()
    assert leopardtree.migrate() is None
    assert leopardtree.is_tree(os.path.join(cfg, "macintalk", "leopard"))


def test_nothing_happens_on_a_fresh_install(cfg):
    import leopardtree
    assert leopardtree.migrate() is None
    assert not os.path.exists(os.path.join(cfg, "macintalk"))
