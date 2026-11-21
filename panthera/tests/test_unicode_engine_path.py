# -*- coding: utf-8 -*-
"""An engine folder whose path is outside the system code page still speaks
(panthera-speech#27).

NVDA's engine folder lives under the user profile, so a profile named with
characters the system's ANSI code page cannot spell -- Cyrillic on Western
Windows, or "Gergő" where the code page is 1252 -- reaches the host.  The
host took its paths through the ANSI code page (`main(char **argv)`, `fopen`,
`_open`, `_access`, `CreateFileA`), got "Gerg?" and the like, could not open
the engine and exited: the voice loaded in NVDA and never spoke.  The library
the 32-bit NVDA loads on secure screens was handed its paths as "mbcs", which
loses the same characters on the Python side.

Every generation is checked, because each opens different files on the way to
its first word.  The tree is reached through a junction; see unicode_tree for the care taken.
"""
import os
import shutil
import struct
import subprocess
import sys

import pytest

import unicode_tree
from unicode_tree import NAME
HELLO = "Hello there."

GENERATIONS = [("tiger", "Fred"), ("leopard", "Fred"), ("snowleopard", "Fred"),
               ("lion", "Fred"), ("lion", "Alex")]


def _module(generation):
    import importlib
    return importlib.import_module("synthDrivers._panthera.panthera" + generation)


def _exact(stream, n):
    data = b""
    while len(data) < n:
        chunk = stream.read(n - len(data))
        assert chunk, "the host closed the pipe: it could not open the engine"
        data += chunk
    return data


def _close(proc):
    """-> the host's stderr, if it was piped, once it has exited."""
    try:
        proc.stdin.close()
    except OSError:
        pass
    err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    proc.wait(30)
    return err


def _say(proc, voice):
    """One TGR4 request -> the number of frames that came back."""
    v, body = voice.encode("ascii"), HELLO.encode("utf-8")
    proc.stdin.write(struct.pack("<IiiIII", 0x54475234, 180, 0, 2, len(v), len(body))
                     + v + body)
    proc.stdin.flush()
    _magic, status = struct.unpack("<Ii", _exact(proc.stdout, 8))
    assert status == 0
    frames = 0
    while True:
        n = struct.unpack("<I", _exact(proc.stdout, 4))[0]
        if not n:
            return frames
        _exact(proc.stdout, n * 2)
        frames += n


def _tree(generation):
    module = _module(generation)
    tree = module.find_tree()
    if not tree:
        pytest.skip("no %s speech tree here" % generation)
    return module, tree


@pytest.mark.parametrize("generation,voice", GENERATIONS)
def test_an_engine_outside_the_code_page_speaks(generation, voice):
    module, tree = _tree(generation)
    if not os.path.isfile(module.HOST_EXE):
        pytest.skip("panthera_host.exe not built; run sh build.sh")
    with unicode_tree.junction(tree, generation) as link:
        p = subprocess.Popen([module.HOST_EXE, "--serve"] + list(module.engine_paths(link)),
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL)
        try:
            frames = _say(p, voice)
            assert frames > 5000, "%s under %r said %d frames" % (voice, NAME, frames)
        finally:
            _close(p)


def test_params_beside_a_host_outside_the_code_page_are_read():
    """The add-on, and so the host, lives under the profile too, and the
    host looks for `params.txt` beside itself -- by a path it asked Windows
    for in the code page, until this was fixed."""
    module, tree = _tree("leopard")
    if not os.path.isfile(module.HOST_EXE):
        pytest.skip("panthera_host.exe not built; run sh build.sh")
    with unicode_tree.folder() as holder:
        exe = os.path.join(holder, "panthera_host.exe")
        shutil.copy2(module.HOST_EXE, exe)
        with open(os.path.join(holder, "params.txt"), "w") as f:
            f.write("Boundaries.SilThreshold=0\n")
        p = subprocess.Popen([exe, "--serve"] + list(module.engine_paths(tree)),
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE)
        try:
            assert _say(p, "Fred") > 5000
        finally:
            err = _close(p)
        assert "reading engine parameters" in err,             "params.txt beside the host under %r was not read" % NAME


def test_the_library_speaks_from_outside_the_code_page():
    """The DLL, as 32-bit NVDA loads it.  Runs only under a 32-bit Python:
    `C:/Python313-32/python.exe -m pytest test_unicode_engine_path.py`."""
    if struct.calcsize("P") != 4:
        pytest.skip("the library is 32-bit; run this under a 32-bit Python")
    module, tree = _tree("leopard")
    from synthDrivers._panthera import dllhost
    if not dllhost.available(module.HOST_DLL):
        pytest.skip("panthera_host.dll not built")
    with unicode_tree.junction(tree, "leopard") as link:
        mt, sd, voices = module.engine_paths(link)
        proc = dllhost.DllHost("unicode-test", module.HOST_DLL, mt, sd, voices, None, [])
        try:
            frames = _say(proc, "Fred")
            assert frames > 5000, "the library under %r said %d frames" % (NAME, frames)
        finally:
            proc.kill()
