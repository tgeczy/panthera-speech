# -*- coding: utf-8 -*-
"""The compressed dyld info, read twice and made to agree.

Snow Leopard is where Apple stopped emitting classic relocation tables. From
10.6 on MacinTalk carries `LC_DYLD_INFO_ONLY` and its `nextrel`/`nlocrel` are
**zero**, so the loader's relocation path finds nothing to do and every
internal pointer stays unslid. Two bytecode streams replaced those tables, and
the host has to interpret them.

**Two readings of the same bytecode agreeing proves nothing** -- they would
agree on garbage. So most of what is checked here is the streams against data
parsed by a completely different route:

* the **indirect symbol table**, which is a second and independent encoding of
  what every pointer slot holds, read by code that has been right since Tiger
* **`LC_SYMTAB`'s undefined list**, which every bound symbol must appear in
* the **segment table**, because a POINTER rebase has to land somewhere the
  loader may write

That is what caught the one real bug in the oracle: stream offsets accumulate
in a pointer-sized unsigned and are *meant* to wrap, because `ADD_ADDR_ULEB`
is how a stream steps backwards. C does that by doing nothing; Python's
unbounded integers do not, and 75 of Lion's slots landed past 2**64. The
indirect-table comparison is what made it visible -- the record counts were
right the whole time.
"""
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import machodyld as M                                          # noqa: E402

#: The two generations that use compressed info, and the one that does not.
#: Nothing here is in the repository; these are the trees a developer
#: extracted for themselves.
BINARIES = {
    "snowleopard": r"D:\speech-snowleopard\MacinTalk.SpeechSynthesizer"
                   r"\Contents\MacOS\MacinTalk",
    "lion": r"D:\speech-lion\Speech\Synthesizers"
            r"\MacinTalk.SpeechSynthesizer\Contents\MacOS\MacinTalk",
}
CLASSIC = (r"D:\speech-leopard\Speech\Synthesizers"
           r"\MacinTalk.SpeechSynthesizer\Contents\MacOS\MacinTalk")

HOST = os.path.join(ROOT, "build", "tiger_host.exe")


def _image(name):
    path = BINARIES[name]
    if not os.path.isfile(path):
        pytest.skip("no %s engine at %s" % (name, path))
    return M.Image(path)


@pytest.fixture(params=sorted(BINARIES))
def image(request):
    return _image(request.param)


# -- the oracle against independently parsed data -------------------------

def test_the_streams_and_the_indirect_table_name_the_same_slots(image):
    """Every pointer slot, from two encodings that share no code.

    The indirect symbol table walks sections and symbol indices; the streams
    are a bytecode. If they name the same addresses *and* agree on the symbol
    at each one, the bytecode walk is right about the thing that matters.
    """
    ind = image.indirect_pointer_slots()
    assert ind, "no indirect pointer slots; the section walk is broken"
    named = {}
    for which in ("bind", "weak", "lazy"):
        for addr, _t, sym, _add, _wk in M.walk_bind(image, which)[0]:
            named[addr] = sym

    missing = sorted(set(ind) - set(named))
    assert not missing, (
        "%d slots the indirect table names and no stream binds, first few %s"
        % (len(missing), ["%08x" % a for a in missing[:5]]))

    disagree = [(a, ind[a], named[a]) for a in sorted(ind) if ind[a] != named[a]]
    assert not disagree, disagree[:5]


def test_the_ordinary_streams_name_only_imports(image):
    """`bind` and `lazy` may only name undefined externals.

    Anything else would mean the walk drifted into the middle of an opcode and
    is reading operands as opcodes -- which produces plausible-looking
    nonsense rather than an error.
    """
    undef = image.undefined_symbols()
    for which in ("bind", "lazy"):
        names = {r[2] for r in M.walk_bind(image, which)[0]}
        assert names, "%s stream named nothing" % which
        assert names <= undef, sorted(names - undef)[:5]


def test_the_weak_stream_names_what_this_image_defines(image):
    """And that is the whole point of it, not a contradiction.

    Weak binding is C++ coalescing: the same template instantiation, inline
    function or vtable is emitted into every object that used it, and one
    definition has to win at load time. So the symbols here are overwhelmingly
    **defined in this very image**, unlike `bind` and `lazy`.

    That is what makes the existing `lookup_shim -> lookup_in -> lookup_loaded`
    chain the right resolver for them: `lookup_in` searches self first and
    finds the local definition, which is the answer dyld would reach for a
    single image with no other copy to prefer. Treating weak binds as imports
    and thunking them would replace ~180 real functions with stubs.
    """
    undef = image.undefined_symbols()
    names = {r[2] for r in M.walk_bind(image, "weak")[0]}
    assert names, "weak stream named nothing"
    local = names - undef
    assert len(local) > len(names) // 2, (
        "expected most weak symbols to be defined here, got %d of %d"
        % (len(local), len(names)))
    # And they really are findable the way the loader would find them.
    exported = image.exported_symbols()
    unfindable = sorted(local - exported)
    assert not unfindable, (
        "%d weak symbols defined nowhere this image can see: %s"
        % (len(unfindable), unfindable[:3]))


def test_pointer_rebases_land_somewhere_writable(image):
    """And text rebases land in __TEXT.

    `TEXT_ABSOLUTE32` is real and there are ~360 of them, which is why this
    cannot simply demand that every rebase be writable -- the loader maps
    everything RWX, so both kinds succeed either way and a misparse would be
    silent.
    """
    rebases, _ = M.walk_rebase(image)
    assert rebases
    text_lo, text_hi = image.segments[0][1], image.segments[0][1] + \
        image.segments[0][2]
    for addr, typ in rebases:
        if typ == 1:
            assert image.writable(addr), "POINTER rebase at %08x" % addr
        elif typ == 2:
            assert text_lo <= addr < text_hi, "text rebase at %08x" % addr
        else:
            pytest.fail("unexpected rebase type %d at %08x" % (typ, addr))


def test_offsets_wrap_at_thirty_two_bits(image):
    """The bug this file exists to have caught.

    `ADD_ADDR_ULEB` steps backwards by overflowing a `uintptr_t`. Every record
    must therefore be inside the image, and an unmasked accumulator puts them
    past 2**32 instead.
    """
    hi = max(va + sz for _n, va, sz, _p in image.segments)
    for which in ("bind", "weak", "lazy"):
        for addr, _t, sym, _a, _w in M.walk_bind(image, which)[0]:
            assert addr < hi, "%s record for %s at %x is outside the image" \
                % (which, sym, addr)
    for addr, _t in M.walk_rebase(image)[0]:
        assert addr < hi, "rebase at %x is outside the image" % addr


def test_leopard_has_no_compressed_info():
    """The generation split this whole file is about.

    If Leopard ever grew a dyld info command, the loader would have two paths
    live on one image and the classic one would be writing over the other.
    """
    if not os.path.isfile(CLASSIC):
        pytest.skip("no Leopard engine")
    im = M.Image(CLASSIC)
    assert im.info is None
    assert im.dysymtab[15] and im.dysymtab[17], \
        "Leopard should have external and local relocations"


def test_the_newer_ones_have_no_classic_relocations(image):
    """The other half of the same split, and the reason this is needed at all.

    `nextrel` and `nlocrel` are zero, so `apply_relocs` and `apply_ext_relocs`
    have nothing to read -- they do not fail, they silently do nothing.
    """
    assert image.info is not None
    assert image.dysymtab[15] == 0, "nextrel is not zero"
    assert image.dysymtab[17] == 0, "nlocrel is not zero"
    assert image.dysymtab[13] > 0, "but the indirect table is still populated"


# -- the host's interpreter against the oracle ----------------------------

def _host_dump(path):
    """-> the host's own reading of the streams, as {kind: [lines]}."""
    if not os.path.isfile(HOST):
        pytest.skip("tiger_host.exe not built; run sh build.sh")
    out = subprocess.run([HOST, "--dyld-check", path], capture_output=True,
                         text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr or out.stdout
    return out.stdout


def _oracle_lines(im):
    """The same records the host prints, in the same shape."""
    lines = []
    for addr, typ in M.walk_rebase(im)[0]:
        lines.append("R %08x %d" % (addr, typ))
    for tag, which in (("B", "bind"), ("W", "weak"), ("L", "lazy")):
        for addr, typ, sym, addend, _wk in M.walk_bind(im, which)[0]:
            lines.append("%s %08x %d %s %d" % (tag, addr, typ, sym, addend))
    return lines


@pytest.mark.parametrize("name", sorted(BINARIES))
def test_the_host_reads_the_streams_the_same_way(name):
    """The C interpreter against the Python one, record for record.

    Weakest of the checks here on its own -- two readings of one bytecode --
    which is why it comes after the three that compare against something else.
    It earns its place by covering the C, not the format.
    """
    im = _image(name)
    got = [l for l in _host_dump(BINARIES[name]).splitlines()
           if l[:2] in ("R ", "B ", "W ", "L ")]
    want = _oracle_lines(im)
    assert len(got) == len(want), "host %d records, oracle %d" % (len(got),
                                                                  len(want))
    for i, (g, w) in enumerate(zip(got, want)):
        assert g == w, "record %d: host %r, oracle %r" % (i, g, w)
