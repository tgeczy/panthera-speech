# -*- coding: utf-8 -*-
"""Where the engine folder is, is NVDA's answer and never ours.

`globalVars.appArgs.configPath` is the only correct source. NVDA's own
`NVDAState.WritePaths.configDir` is a property wrapping exactly that value, so
it already accounts for a **portable copy** and for a config directory handed
in on the command line with `-c`.

Expanding `%APPDATA%` ourselves would be right on the machine it was written
on and wrong on every portable one -- and wrong in the quietest possible way,
because the add-on would go looking in the installed copy's folder, find
nothing, and report a missing engine to somebody who has one. There is no
error to read and no way to tell it from an extraction that failed.

The `expanduser("~")/.nvda` fallback is for running outside NVDA -- these
tests, and the command-line tools -- and for nothing else.
"""
import ast
import io
import os
import sys

import pytest


def _code_only(src):
    """The file with its prose removed. -> str

    Parsed rather than grepped, and the first version was grepped: the
    docstring in `config_base` explains at length why expanding `%APPDATA%`
    would be wrong, so a plain search found the word in the one file that most
    carefully does not do it. A comment that describes a mistake is not the
    mistake.
    """
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            del body[0]
    return ast.unparse(tree)

ADDON = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(
    __file__))), "addon")
SHIPPED = [os.path.join(dirpath, n)
           for dirpath, dirs, names in os.walk(ADDON)
           for n in names if n.endswith(".py") and "__pycache__" not in dirpath]

TREES = ("pantheratiger", "pantheraleopard")


@pytest.fixture(params=TREES)
def tree(request):
    sys.path.insert(0, os.path.join(ADDON, "synthDrivers", "_panthera"))
    return __import__(request.param)


def test_the_config_folder_follows_nvdas_own(tree, tmp_path, monkeypatch):
    """A portable NVDA on a memory stick has to find its own engine."""
    import globalVars
    monkeypatch.setattr(globalVars.appArgs, "configPath", str(tmp_path))
    assert tree.config_base() == str(tmp_path)
    assert tree.config_dir().startswith(str(tmp_path))
    assert tree.config_dir() == os.path.join(str(tmp_path), "macintalk",
                                             tree.CONFIG_DIRNAME.split(
                                                 os.sep)[-1])


def test_moving_nvdas_config_moves_the_engine_folder(tree, tmp_path,
                                                     monkeypatch):
    """Read every time, not cached at import.

    A module-level constant computed once would be right until somebody ran a
    second NVDA with `-c`, and would then quietly answer for the first.
    """
    import globalVars
    monkeypatch.setattr(globalVars.appArgs, "configPath", str(tmp_path / "a"))
    first = tree.config_dir()
    monkeypatch.setattr(globalVars.appArgs, "configPath", str(tmp_path / "b"))
    assert tree.config_dir() != first


def test_the_fallback_is_only_reachable_without_nvda(tree, monkeypatch):
    """With no `globalVars` at all -- a command-line tool -- it still answers.

    It must not raise: `find_tree` is called from the driver's `check()`, and
    an exception there takes the synthesizer out of NVDA's list entirely.
    """
    monkeypatch.setitem(sys.modules, "globalVars", None)
    assert tree.config_base()


@pytest.mark.parametrize("path", SHIPPED,
                         ids=lambda p: os.path.basename(p))
def test_nothing_shipped_reaches_for_appdata(path):
    """The check the whole file exists for, stated as a grep.

    `os.environ` is allowed: the drivers copy it to hand a verbosity flag to
    the host, and TIGER_TREE/LEOPARD_TREE are a documented escape hatch for
    keeping an engine on another drive. What is not allowed is deciding where
    NVDA keeps its configuration.
    """
    code = _code_only(io.open(path, encoding="utf-8").read())
    for reach in ("APPDATA", "expandvars", "Roaming", r"C:\Users",
                  "LOCALAPPDATA", "USERPROFILE"):
        assert reach not in code, "%s reaches for %s" % (
            os.path.basename(path), reach)
