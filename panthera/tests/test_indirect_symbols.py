# -*- coding: utf-8 -*-
"""`N_INDR`: a symbol table entry that is a name, not an address.

Lion's libstdc++ 6.0.9 does not implement the C++ ABI. 10.7 moved it into
`libc++abi.dylib`, and libstdc++ re-exports it -- 150 symbols, each carried as
an `N_INDR` entry whose `n_value` is a **string table offset naming the real
symbol**, not a value.

A resolver that skips only `N_UNDF` walks straight past those and returns
`n_value + slide`: a text address computed from a string index. For
`___dynamic_cast` that offset is 0x24e6c, so the engine's first `dynamic_cast`
jumped four bytes into an unrelated function and died there. Nothing reported
it, because from the loader's point of view the symbol resolved.

Twenty-three imports across the two engines land on one of these aliases, and
the ones that are not `dynamic_cast` are worse for being quiet:

* the three `__ZTVN10__cxxabiv1..._type_infoE` **vtables**, which every RTTI
  object in both binaries points its vptr at;
* `__cxa_guard_acquire` and `__cxa_guard_release`, the static-local
  initialisation guards.

Leopard's 6.0.4 has **no** indirect symbols at all -- the split had not
happened yet -- so none of this can reach it. That is checked here too, since
"this cannot affect the generation that already works" is the claim doing the
most work.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import machodyld as M                                          # noqa: E402

LION_CXX = r"D:\speech-lion\libstdc++.6.0.9.dylib"
LEOPARD_CXX = r"D:\speech-leopard\libstdc++.6.0.4.dylib"

#: What the engines import and libstdc++ answers with an alias. Naming them
#: rather than counting them: if a future runtime resolves one of these for
#: real, that is a change worth reading, not a number worth updating.
THROUGH_AN_ALIAS = (
    "__ZTVN10__cxxabiv117__class_type_infoE",
    "__ZTVN10__cxxabiv120__si_class_type_infoE",
    "__ZTVN10__cxxabiv121__vmi_class_type_infoE",
    "___cxa_guard_acquire",
    "___cxa_guard_release",
    "___cxa_pure_virtual",
    "___cxa_throw",
    "___dynamic_cast",
    "___gxx_personality_v0",
)


def _image(path):
    if not os.path.isfile(path):
        pytest.skip("no runtime at %s" % path)
    return M.Image(path)


@pytest.mark.parametrize("name", THROUGH_AN_ALIAS)
def test_an_alias_is_not_a_definition(name):
    """The oracle must not offer these; they are names pointing elsewhere.

    This is the test that would have caught it. Both readings of the symbol
    table had the same bug, so they agreed -- and two readings agreeing is
    exactly what this project refuses to accept as evidence.
    """
    assert name not in _image(LION_CXX).exported_symbols(), name


def test_the_aliases_are_all_self_referential():
    """Each names itself, which is what makes it a re-export marker.

    A self-alias cannot be followed to a local definition, so the only honest
    answer is that this image does not have the symbol.
    """
    im = _image(LION_CXX)
    aliases = im.indirect_symbols()
    assert len(aliases) > 100, len(aliases)
    assert all(k == v for k, v in aliases.items()), \
        {k: v for k, v in aliases.items() if k != v}


def test_dynamic_cast_is_one_of_them():
    """Non-vacuous: the specific entry that cost the evening."""
    assert "___dynamic_cast" in _image(LION_CXX).indirect_symbols()


def test_leopards_runtime_has_none():
    """So the fix cannot reach the generation that already ships."""
    assert _image(LEOPARD_CXX).indirect_symbols() == {}


def test_the_engines_still_import_them():
    """If these ever stop being imported, this whole file is obsolete."""
    for path in (r"D:\speech-lion\Speech\Synthesizers"
                 r"\MacinTalk.SpeechSynthesizer\Contents\MacOS\MacinTalk",
                 r"D:\speech-lion\SpeechDictionary.framework\Versions\A"
                 r"\SpeechDictionary"):
        if not os.path.isfile(path):
            pytest.skip("no engine at %s" % path)
    macintalk = _image(r"D:\speech-lion\Speech\Synthesizers"
                       r"\MacinTalk.SpeechSynthesizer\Contents\MacOS"
                       r"\MacinTalk").undefined_symbols()
    assert "___dynamic_cast" in macintalk
    assert "___cxa_guard_acquire" in macintalk
