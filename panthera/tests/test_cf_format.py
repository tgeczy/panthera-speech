# -*- coding: utf-8 -*-
"""`CFStringCreateWithFormat`, which is the whole of Lion's dictionary.

Lion's `SLDictLookup::Create` does not name its tables. It formats them:

    CFStringCreateWithFormat(0, 0, CFSTR("%@Eng"), CFSTR("PrefixDictionary"))
    CFBundleCopyResourceURL(bundle, thatName, NULL, NULL)

three times over -- PrefixDictionary, CartLite, CartNames -- and once more in
`CreatePhonemeSymbols`. `%@Eng` is the *only* format string in the entire
binary, so this one function decides whether the dictionary exists.

A stub returning NULL asks the bundle for a resource called nothing, gets NULL
back for all three, and `Create` returns NULL by its own error path. That
surfaced two stages downstream as `SLPostLexerImpl` holding a null
`SLDictLookup` -- a crash in a constructor that is not the one at fault, and
which names neither the format nor the missing shim.

`TuplesEng` is why this was findable: it is a *literal* CFString, so it got its
URL while the three formatted names did not. Two tables arriving out of six is
the shape of a formatter that returns nothing, not of a missing file.

**The arguments are Apple's CFSTR constants, not objects this host made.** So
the check builds one of those by hand -- the bare `{isa, flags, cstr, len}`
record, with an isa that is not ours -- because a formatter that only reads
its own objects passes every test written with its own objects and fails every
real call site.
"""
import os
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
HOST = os.path.join(ROOT, "build", "tiger_host.exe")

#: What Lion actually asks for, and must get back.
WANTED = ("PrefixDictionaryEng", "CartLiteEng", "CartNamesEng",
          "PhonemeSymbolsEng")


@pytest.fixture(scope="module")
def check():
    """-> the host's own report on its formatter."""
    if not os.path.isfile(HOST):
        pytest.skip("tiger_host.exe not built; run sh build.sh")
    out = subprocess.run([HOST, "--cf-check"], capture_output=True,
                         text=True, encoding="utf-8", timeout=60)
    return out


def test_the_check_passes(check):
    """The C side asserts its own cases; a non-zero exit is a real failure."""
    assert check.returncode == 0, check.stdout + check.stderr
    assert "FAIL" not in check.stdout, check.stdout


@pytest.mark.parametrize("name", WANTED)
def test_lions_four_table_names_are_produced(name, check):
    """The four names `SLDictLookup` cannot open the dictionary without."""
    assert name in check.stdout, check.stdout


def test_a_constant_shaped_string_formats(check):
    """Apple's own CFSTR records, not just objects this host allocated.

    The distinction is the whole point: `cf_ours` gates on a magic word only
    the host's own objects carry, and every argument at every real call site
    is a constant in Apple's `__cfstring` section.
    """
    assert "constant" in check.stdout.lower(), check.stdout


def test_an_unknown_specifier_says_so(check):
    """Silence here would be a quieter version of the bug being fixed.

    Nothing in either binary uses anything but `%@`, so an unknown conversion
    means an assumption has expired -- and it has to name itself rather than
    return a plausible-looking partial string.
    """
    assert "unsupported" in check.stdout.lower(), check.stdout


# -- the token layer, which hands text back the other way round -----------
#
# `SLTokenGetText` does not return a string it was given. It builds one, from
# a UTF-16 range it holds as two pointers:
#
#     CFStringCreateWithCharactersNoCopy(0, begin, (end-begin)/2 - 1,
#                                        kCFAllocatorNull)
#
# and caches it at `tok+0x24`. `SLHomographGetPhonemes` is the same shape over
# `CFDataCreateWithBytesNoCopy`. Stubbed, the cache is filled with NULL and the
# front end reads through it -- which is the read of address 0 in
# `MTFEBuilder::PeekToken`, a function that has nothing to do with either.
#
# Note the length: `(end - begin) / 2 - 1`. The range holds a terminator the
# count excludes, so a shim that trusts a NUL to end the buffer and one that
# trusts the count disagree by exactly one character.

def test_utf16_characters_come_back_as_text(check):
    """The inverse of `CFStringGetCharacters`, which widens byte to UniChar."""
    assert "chars \"Homophones\"" in check.stdout, check.stdout


def test_the_count_is_believed_over_any_terminator(check):
    """A length shorter than the buffer must truncate, not run to the NUL."""
    assert "count \"Homo\"" in check.stdout, check.stdout


def test_a_character_out_of_range_does_not_read_off_the_end(check):
    """`CFStringGetCharacterAtIndex` past the end answers 0, not memory."""
    assert "index oob 0" in check.stdout, check.stdout


def test_data_is_readable_back_at_its_own_length(check):
    """`CFDataGetBytePtr`/`GetLength` over a CFData nothing read from a file."""
    assert "data 5 bytes ok" in check.stdout, check.stdout
