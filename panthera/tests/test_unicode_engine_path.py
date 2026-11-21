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
its first word.  The tree is reached through a junction, so nothing is copied.
"""
import os
import struct
import subprocess
import sys

import pytest

NAME = "Gergő Дмитрий"
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


def _junction(tmp_path, generation):
    if sys.platform != "win32":
        pytest.skip("the ANSI code page is a Windows matter")
    module = _module(generation)
    tree = module.find_tree()
    if not tree:
        pytest.skip("no %s speech tree here" % generation)
    link = os.path.join(str(tmp_path), NAME, generation)
    os.makedirs(os.path.dirname(link))
    made = subprocess.run(["cmd", "/c", "mklink", "/J", link, os.path.normpath(tree)],
                          capture_output=True)
    if made.returncode:
        pytest.skip("could not make a junction here")
    return module, link


@pytest.mark.parametrize("generation,voice", GENERATIONS)
def test_an_engine_outside_the_code_page_speaks(generation, voice, tmp_path):
    module, link = _junction(tmp_path, generation)
    try:
        if not os.path.isfile(module.HOST_EXE):
            pytest.skip("panthera_host.exe not built; run sh build.sh")
        p = subprocess.Popen([module.HOST_EXE, "--serve"] + list(module.engine_paths(link)),
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL)
        try:
            frames = _say(p, voice)
            assert frames > 5000, "%s under %r said %d frames" % (voice, NAME, frames)
        finally:
            try:
                p.stdin.close()
            except OSError:
                pass
            p.wait(30)
    finally:
        os.rmdir(link)                    # the junction, never the tree


def test_the_library_speaks_from_outside_the_code_page(tmp_path):
    """The DLL, as 32-bit NVDA loads it.  Runs only under a 32-bit Python:
    `C:\\Python313-32\\python.exe -m pytest test_unicode_engine_path.py`."""
    if struct.calcsize("P") != 4:
        pytest.skip("the library is 32-bit; run this under a 32-bit Python")
    module, link = _junction(tmp_path, "leopard")
    try:
        from synthDrivers._panthera import dllhost
        if not dllhost.available(module.HOST_DLL):
            pytest.skip("panthera_host.dll not built")
        mt, sd, voices = module.engine_paths(link)
        proc = dllhost.DllHost("unicode-test", module.HOST_DLL, mt, sd, voices, None, [])
        try:
            frames = _say(proc, "Fred")
            assert frames > 5000, "the library under %r said %d frames" % (NAME, frames)
        finally:
            proc.kill()
    finally:
        os.rmdir(link)
