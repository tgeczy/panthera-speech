# -*- coding: utf-8 -*-
"""The two modules the SAPI installer stages flat must import flat.

`sapi/build.ps1` copies `pantheradiscs.py` and `pantherahfs.py` out of the
add-on and drops them beside `extract.py` in the installer's staging folder,
where the bundled embeddable Python imports them as **top-level modules with
no package around them** -- and its `._pth` locks `sys.path` to exactly that
folder, so there is nowhere else for them to be found.

That is the path every JAWS user's extraction goes through, and the suite was
blind to it: making `_panthera` a package turned `pantheradiscs`'s
`import pantherahfs` into `from . import pantherahfs`, which raises
`ImportError: attempted relative import with no known parent package` the
moment there is no package -- and nothing in 588 tests noticed, because every
one of them imports through the package.

This stages them the way `build.ps1` does and imports them the way the
installer's Python does.
"""
import os
import shutil
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_PRIVATE = os.path.join(os.path.dirname(_HERE), "addon", "synthDrivers",
                        "_panthera")
_REPO = os.path.dirname(os.path.dirname(_HERE))

#: Exactly the list `sapi/build.ps1` copies.
STAGED = ("pantheradiscs.py", "pantherahfs.py")


def _stage(tmp_path):
    for name in STAGED:
        shutil.copy(os.path.join(_PRIVATE, name), str(tmp_path / name))
    return tmp_path


@pytest.mark.parametrize("modname", [n[:-3] for n in STAGED])
def test_each_staged_module_imports_with_no_package_around_it(tmp_path, modname):
    """A fresh interpreter, one folder on the path, and nothing else."""
    _stage(tmp_path)
    #: `-S` and an emptied path are the closest thing to the embeddable
    #: build's locked `._pth` that a normal interpreter can be asked for.
    code = "import %s; print('ok')" % modname
    out = subprocess.run([sys.executable, "-S", "-c", code], cwd=str(tmp_path),
                         capture_output=True, text=True)
    assert out.returncode == 0, (
        "%s cannot be imported the way the SAPI installer imports it:\n%s"
        % (modname, out.stderr.strip()))
    assert "ok" in out.stdout


def test_the_sapi_extractor_runs_from_that_folder(tmp_path):
    """`extract.py` itself, staged and started the way `settings.ps1` starts it."""
    extract = os.path.join(_REPO, "sapi", "extract.py")
    if not os.path.isfile(extract):
        pytest.skip("no sapi/extract.py in this checkout")
    _stage(tmp_path)
    shutil.copy(extract, str(tmp_path / "extract.py"))
    out = subprocess.run([sys.executable, "-u", "extract.py"],
                         cwd=str(tmp_path), capture_output=True, text=True)
    #: No arguments, so it prints its usage.  Reaching the usage line at all
    #: means every import above it resolved.
    assert "usage:" in (out.stdout + out.stderr).lower(), (
        "the staged extractor did not get as far as its own usage:\n%s"
        % (out.stderr.strip() or out.stdout.strip()))
