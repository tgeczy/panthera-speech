# -*- coding: utf-8 -*-
"""Moving the tree under `macintalk`, which touches the user's own data.

Tiger's tree is 37 MB against Leopard's 717, but the rule and the risks are
the same: what must never happen, first. The sibling file in
`leopard/tests/` covers the same ground and has notes on what these
assertions do and do not prove.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "addon", "synthDrivers", "_panthera"))


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    import pantheratiger as tree
    monkeypatch.setattr(tree, "config_base", lambda: str(tmp_path))
    monkeypatch.delenv("TIGER_TREE", raising=False)
    return str(tmp_path)


def _plant(folder):
    os.makedirs(os.path.join(folder, "Speech", "Voices"), exist_ok=True)
    with open(os.path.join(folder, "Speech", "Voices", "Fred"), "wb") as f:
        f.write(b"engine")
    return folder


def test_the_tree_is_moved_not_copied(cfg):
    import pantheratiger as tree
    _plant(os.path.join(cfg, "tigerspeech-data"))
    assert tree.migrate()
    assert not os.path.exists(os.path.join(cfg, "tigerspeech-data"))
    assert tree.is_tree(os.path.join(cfg, "macintalk", "tiger"))


def test_it_never_moves_on_top_of_an_existing_folder(cfg):
    import pantheratiger as tree
    _plant(os.path.join(cfg, "tigerspeech-data"))
    _plant(os.path.join(cfg, "macintalk", "tiger"))
    assert tree.migrate() is None
    assert tree.is_tree(os.path.join(cfg, "tigerspeech-data"))
    assert tree.is_tree(os.path.join(cfg, "macintalk", "tiger"))


def test_a_refused_move_changes_nothing_and_still_speaks(cfg, monkeypatch):
    import pantheratiger as tree
    _plant(os.path.join(cfg, "tigerspeech-data"))
    monkeypatch.setattr(os, "rename", lambda *a, **k: (_ for _ in ()).throw(
        OSError(32, "The process cannot access the file")))
    assert tree.migrate() is None
    assert tree.find_tree() == os.path.join(cfg, "tigerspeech-data")


def test_the_breadcrumb_is_what_an_older_add_on_already_reads(cfg):
    import pantheratiger as tree
    _plant(os.path.join(cfg, "tigerspeech-data"))
    moved = tree.migrate()
    pointer = os.path.join(cfg, "tigerspeech-data.txt")
    assert os.path.isfile(pointer), "no breadcrumb was left"
    assert open(pointer, encoding="utf-8").read().strip() == moved


def test_a_pointer_the_user_wrote_is_never_overwritten(cfg):
    import pantheratiger as tree
    pointer = os.path.join(cfg, "tigerspeech-data.txt")
    with open(pointer, "w", encoding="utf-8") as f:
        f.write("D:\\my-own-tiger")
    _plant(os.path.join(cfg, "tigerspeech-data"))
    tree.migrate()
    assert open(pointer, encoding="utf-8").read() == "D:\\my-own-tiger"


def test_find_tree_prefers_the_shared_folder(cfg):
    import pantheratiger as tree
    _plant(os.path.join(cfg, "macintalk", "tiger"))
    _plant(os.path.join(cfg, "tigerspeech-data"))
    assert tree.find_tree() == os.path.join(cfg, "macintalk", "tiger")


def test_nothing_happens_on_a_fresh_install(cfg):
    import pantheratiger as tree
    assert tree.migrate() is None
    assert not os.path.exists(os.path.join(cfg, "macintalk"))
