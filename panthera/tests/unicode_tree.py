# -*- coding: utf-8 -*-
"""An engine tree reached through a folder named outside the system code page
(panthera-speech#27), for the tests that need one.

**A junction to the user's real engine tree, so it is handled with care.**
It is made under `tempfile.mkdtemp`, never under pytest's `tmp_path`: pytest
deletes old `tmp_path` folders with `shutil.rmtree`, and on Python 3.7 that
follows a junction and empties the tree it points at.  Every removal here is
`os.rmdir`, which removes a junction itself, refuses a folder with anything in
it, and never follows anything.  A run killed before its clean-up leaves a
junction behind, so each use first sweeps those away -- again with `rmdir`
alone.
"""
import contextlib
import os
import subprocess
import sys
import tempfile

import pytest

NAME = "Gergő Дмитрий"
PREFIX = "panthera-unicode-"


def _rmdir(path):
    try:
        os.rmdir(path)
        return True
    except OSError:
        return False


def sweep():
    """Remove junctions and empty folders left by an earlier, killed run."""
    root = tempfile.gettempdir()
    for base in os.listdir(root):
        if not base.startswith(PREFIX):
            continue
        base = os.path.join(root, base)
        holder = os.path.join(base, NAME)
        if os.path.isdir(holder):
            for entry in os.listdir(holder):
                path = os.path.join(holder, entry)
                if os.path.isfile(path):
                    os.remove(path)         # a copied host, a params.txt
                else:
                    _rmdir(path)            # a junction, or an empty folder
            _rmdir(holder)
        _rmdir(base)


@contextlib.contextmanager
def folder():
    """-> a real, empty folder whose path has `NAME` in it."""
    if sys.platform != "win32":
        pytest.skip("the ANSI code page is a Windows matter")
    sweep()
    base = tempfile.mkdtemp(prefix=PREFIX)
    holder = os.path.join(base, NAME)
    os.makedirs(holder)
    try:
        yield holder
    finally:
        for entry in os.listdir(holder):
            path = os.path.join(holder, entry)
            if os.path.isfile(path):
                os.remove(path)
            else:
                _rmdir(path)
        _rmdir(holder)
        _rmdir(base)


@contextlib.contextmanager
def junction(tree, leaf):
    """-> `tree`, reached through <temp>/.../`NAME`/`leaf`."""
    with folder() as holder:
        link = os.path.join(holder, leaf)
        made = subprocess.run(["cmd", "/c", "mklink", "/J", link, os.path.normpath(tree)],
                              capture_output=True)
        if made.returncode:
            pytest.skip("could not make a junction here")
        try:
            yield link
        finally:
            _rmdir(link)                    # the junction, never the tree
