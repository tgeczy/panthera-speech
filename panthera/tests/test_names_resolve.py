# -*- coding: utf-8 -*-
"""Every module a shipped file names, it also imports.

**Written because this exact bug reached Tomi's live NVDA.**  A change replaced
ten uses of `log.DEBUG` with `logging.DEBUG` across four files and added
`import logging` to three of them: the insertion looked for an anchor line to
sit beside, `speech_pipeline.py` had none of the anchors, and the insert
silently did nothing.  The count of *replacements* was checked; the presence of
the *import* was not.

It got past everything.  The file imports cleanly, because the name is only
looked up when the line runs, and the line runs in the render worker -- so the
driver loaded, spoke once, and the thread died with `NameError` half way
through an utterance.

Python cannot catch that at import and neither can a test that only imports.
This walks the syntax tree instead: for every `x.y` where `x` is one of the
modules these files use, `x` must be imported, assigned, or a parameter
somewhere in the same file.

Deliberately narrow.  It knows nothing about scopes and does not try to be a
linter -- it answers one question, the one that has actually gone wrong, and a
narrow check that is trusted beats a broad one that is muted.
"""
import ast
import glob
import io
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.join(os.path.dirname(HERE), "addon")

#: The modules worth checking: everything the add-on actually reaches for by
#: name.  A module missing from this list is simply not checked, which is why
#: it lists more than is currently used.
WATCHED = frozenset("""
    logging os sys re io struct time threading queue subprocess ctypes msvcrt
    codecs json math shutil tempfile traceback textwrap zipfile hashlib
    itertools functools binascii glob importlib types wx gui nvwave globalVars
    config addonHandler speech winreg zlib
""".split())


def _files():
    return sorted(glob.glob(os.path.join(ADDON, "**", "*.py"), recursive=True))


def _bound(tree):
    """Every name the file binds: imports, assignments, defs, parameters."""
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names |= {(a.asname or a.name) for a in node.names}
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                               ast.ClassDef)):
            names.add(node.name)
            args = getattr(node, "args", None)
            if args is not None:
                for group in (args.args, args.posonlyargs, args.kwonlyargs):
                    names |= {a.arg for a in group}
                for extra in (args.vararg, args.kwarg):
                    if extra is not None:
                        names.add(extra.arg)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            names |= set(node.names)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
    return names


@pytest.mark.parametrize("path", _files(),
                         ids=lambda p: os.path.relpath(p, ADDON))
def test_every_module_used_is_imported(path):
    source = io.open(path, encoding="utf-8").read()
    tree = ast.parse(source, path)
    bound = _bound(tree)
    used = {}
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id in WATCHED):
            used.setdefault(node.value.id, node.lineno)
    missing = sorted((name, line) for name, line in used.items()
                     if name not in bound)
    assert not missing, (
        "%s uses %s but never imports it -- this only fails when the line "
        "runs, which for a render worker means half way through an utterance"
        % (os.path.relpath(path, ADDON),
           ", ".join("%s (line %d)" % m for m in missing)))
