# -*- coding: utf-8 -*-
"""The same symbol, spelled differently by a later OS.

Four separate crashes in one evening were all one bug wearing different
clothes. Lion imports a function Leopard also imports, under a name with a
suffix on it, and the shim table only knows Leopard's spelling:

    _stat            ->  _stat$INODE64        (10.6 widened st_ino)
    _fstat           ->  _fstat$INODE64
    _sqlite3_prepare ->  _sqlite3_prepare_v2
    _sqlite3_column_int -> _sqlite3_column_int64
    _strncasecmp     ->  _strncasecmp_l       (the locale-taking form)

**None of these fails at load.** An unknown import is thunked to a stub that
returns zero, and zero is `noErr`, and zero is `SQLITE_OK`, and zero is a
successful `stat()`. So the program carries on with a struct nobody filled or
a statement nobody prepared, and falls over somewhere with no connection to
the missing name -- in a constructor, in a part-of-speech resolver, inside
libstdc++. Every one of them cost an hour of disassembly to walk back.

This test walks it forwards instead. For every symbol a newer engine imports,
strip the suffixes an OS release adds, and if the *stripped* name is one this
host shims while the full name is not, that is a rename nobody noticed -- and
it is reported by name, before it can be reported as a crash somewhere else.

The rule is deliberately narrow. It says nothing about symbols we have simply
never implemented; there are hundreds of those and they are fine, because the
engine never calls them. It only fires when we can show we *meant* to
implement this exact function and are missing it purely on spelling.
"""
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import machodyld as M                                          # noqa: E402

SHIMTAB = os.path.join(ROOT, "src", "tiger_host_shimtab.c")

#: Every binary of a generation newer than Leopard that this host loads.
NEWER = {
    "lion MacinTalk":
        r"D:\speech-lion\Speech\Synthesizers"
        r"\MacinTalk.SpeechSynthesizer\Contents\MacOS\MacinTalk",
    "lion SpeechDictionary":
        r"D:\speech-lion\SpeechDictionary.framework\Versions\A"
        r"\SpeechDictionary",
    "snowleopard MacinTalk":
        r"D:\speech-snowleopard\MacinTalk.SpeechSynthesizer"
        r"\Contents\MacOS\MacinTalk",
}

#: Renames that change the *name* and nothing else, so one implementation can
#: serve both. `lookup_shim` already strips these generically -- everything
#: from the `$` on -- because they are libSystem's conformance variants, the
#: same function with standards-mandated behaviour on edge cases none of this
#: reaches. `$INODE64` is the exception that proves it needs watching: that
#: one changes the struct, so it has its own entry despite the `$`.
DOLLAR = "$"

#: Renames that change the function. These can never be aliased and must be
#: implemented, so the test demands an entry of its own for each.
#:
#: `64` is the trap: `sqlite3_column_int64` returns in edx:eax where
#: `column_int` returns in eax alone, so wiring them to one function hands the
#: caller a garbage high word -- and a garbage high word in a table offset is
#: not a crash, it is a wrong answer. `_v2` is a second-generation entry point;
#: `_l` takes an explicit locale.
SEMANTIC = ("_v2", "64", "_l")


def _shimmed():
    """-> every symbol name the shim table binds, as written in the source."""
    with open(SHIMTAB, "r", encoding="utf-8", errors="replace") as f:
        return set(re.findall(r'\{\s*"([^"]+)"', f.read()))


def _strip(name):
    """-> the name with one semantic suffix removed, or None."""
    for suf in SEMANTIC:
        if name.endswith(suf) and len(name) > len(suf) + 1:
            return name[:-len(suf)]
    return None


def _resolves(name, shimmed):
    """-> True if `lookup_shim` would find something for this name.

    It matches exactly, then retries with everything from the `$` removed.
    Modelled here rather than assumed; `test_the_dollar_rule_is_really_there`
    keeps the model honest.
    """
    return name in shimmed or name.split(DOLLAR)[0] in shimmed


def test_the_dollar_rule_is_really_there():
    """The `$`-stripping this test relies on, in the host that must do it.

    Without it these tests would quietly excuse fourteen real misses.
    """
    with open(os.path.join(ROOT, "src", "tiger_host_macho.c"), "r",
              encoding="utf-8", errors="replace") as f:
        src = f.read()
    lookup = src[src.index("static void *lookup_shim"):][:1600]
    assert "strchr(name, '$')" in lookup, lookup[:400]


@pytest.fixture(scope="module")
def shimmed():
    return _shimmed()


@pytest.mark.parametrize("label", sorted(NEWER))
def test_no_import_is_missed_on_spelling_alone(label, shimmed):
    """A newer name whose older form we shim, and which we do not.

    Failing here names the symbol. That is the entire point: the alternative
    is finding it as a null pointer four frames away from anything related.
    """
    path = NEWER[label]
    if not os.path.isfile(path):
        pytest.skip("no engine at %s" % path)
    missed = []
    for sym in M.Image(path).undefined_symbols():
        if _resolves(sym, shimmed):
            continue
        older = _strip(sym)
        if older and _resolves(older, shimmed):
            missed.append("%s (we shim %s)" % (sym, older))
    assert not missed, "%s renames a symbol we already shim:\n  %s" % (
        label, "\n  ".join(sorted(missed)))


def test_the_rule_would_have_caught_the_ones_that_crashed():
    """Non-vacuous: the ones that actually cost an evening.

    A test that can only pass is not a test. These are checked against the
    stripping rule directly, so the rule stays able to fire even when every
    real binary is clean.
    """
    for newer, older in (("_sqlite3_prepare_v2", "_sqlite3_prepare"),
                         ("_sqlite3_column_int64", "_sqlite3_column_int"),
                         ("_strncasecmp_l", "_strncasecmp")):
        assert _strip(newer) == older, newer


def test_the_stat_family_is_named_outright(shimmed):
    """`$INODE64` is a `$` rename that is *not* safe to strip.

    It changes the struct, so it needs its own entry -- and the generic `$`
    rule would otherwise hide that by resolving it to the 10.5 layout, which
    is the failure it caused in the first place, only silent.
    """
    assert "_stat$INODE64" in shimmed
    assert "_fstat$INODE64" in shimmed


def test_a_name_that_merely_ends_in_a_suffix_is_not_a_rename():
    """Only a suffix over a real base name counts."""
    assert _strip("_CFRelease") is None
    assert _strip("_l") is None
