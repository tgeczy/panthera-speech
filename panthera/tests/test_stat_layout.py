# -*- coding: utf-8 -*-
"""`stat$INODE64` -- 10.6 changed the name of stat(), and its shape with it.

Leopard imports `_stat`. Lion imports **`_stat$INODE64`**, and MacinTalk
imports `_fstat$INODE64`, because 10.6 widened `st_ino` to 64 bits and gave
the wide form its own symbols so old binaries kept the old struct.

A shim table that only knows the 10.5 spellings does not fail loudly here. The
call falls through to the auto-stub, which returns **0 -- and 0 is success**,
over a stat buffer nobody filled. `SLMMapCache::Map` then reads a size of zero,
maps zero bytes, and hands `SLCartDict` a window into nothing. What that looks
like from the outside is a crash in a constructor two frames further on.

The offsets are measured, never looked up:

* `SLMMapCache::Map` keeps its buffer at `[ebp-0x78]` and reads the size from
  `[ebp-0x3c]` -- 0x78-0x3c = **60**, and it reads `[ebp-0x70]`/`[ebp-0x6c]`
  as one 64-bit inode, which is offset **8**.
* Lion's `MacinTalk` keeps its buffer at `[ebp-0x80]` and reads the size from
  `[ebp-0x44]`. Also **60**, from an entirely different binary.

`st_dev` and `st_ino` are not decoration. `SLMMapCache` keys its whole mapping
cache on them, so a layout that puts them anywhere but where the engine looks
says *every file is the same file* -- and that failure is invisible, because
the cache then answers six later lookups with the first file's bytes and never
calls open() again. Both layouts are checked for it here.
"""
import os
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
HOST = os.path.join(ROOT, "build", "tiger_host.exe")


@pytest.fixture(scope="module")
def report():
    """-> {field: value} per layout, as the host fills them."""
    if not os.path.isfile(HOST):
        pytest.skip("tiger_host.exe not built; run sh build.sh")
    out = subprocess.run([HOST, "--stat-check"], capture_output=True,
                         text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "FAIL" not in out.stdout, out.stdout
    got = {}
    for line in out.stdout.splitlines():
        if not line.startswith("[stat-check]"):
            continue
        parts = line.split()
        if len(parts) >= 4 and parts[2] == "field":
            got.setdefault(parts[1], {})[parts[3]] = int(parts[4])
    return got


def test_both_layouts_are_reported(report):
    assert set(report) == {"stat", "stat64"}, report


@pytest.mark.parametrize("layout,off", [("stat", 48), ("stat64", 60)])
def test_st_size_lands_where_the_engine_reads_it(layout, off, report):
    """The one field a zero in which maps nothing and crashes elsewhere."""
    assert report[layout]["size_off"] == off


@pytest.mark.parametrize("layout,off", [("stat", 4), ("stat64", 8)])
def test_st_ino_moved_when_it_widened(layout, off, report):
    """8 bytes at offset 8 in the wide form, 4 at offset 4 in the old one."""
    assert report[layout]["ino_off"] == off


@pytest.mark.parametrize("layout,size", [("stat", 96), ("stat64", 108)])
def test_the_struct_is_the_size_the_caller_reserved(layout, size, report):
    """A fill wider than the caller's buffer writes over its locals."""
    assert report[layout]["struct_size"] == size


@pytest.mark.parametrize("layout", ["stat", "stat64"])
def test_two_different_files_are_not_one_file(layout, report):
    """The property `SLMMapCache`'s cache is keyed on, in both shapes.

    Equal keys here are silent: the second lookup is answered from the cache
    with the first file's bytes, and no open() is ever attempted for it.
    """
    assert report[layout]["distinct"] == 1
    assert report[layout]["ino_nonzero"] == 1
