"""A MacinTalk 3.4 tree must be named, not left silent.

Tiger from 10.4.5 ships MacinTalk 3.4, which calls Apple's "Don't Steal Mac
OS X" routine -- answered by the kernel extension of that name, keyed from the
SMC on genuine Apple hardware.  Off that hardware the call goes nowhere and the
engine dies, and because the driver restarts a crashed host quietly it arrives
as total silence with no error anywhere.  That was issue #1.

We do not answer it: doing so means reproducing a value that exists only on a
real Mac.  What we can do is say so, from the file alone, before anyone spends
an evening on it.
"""
import os

import pytest

from synthDrivers._panthera import pantheratiger as tree


def _write_tree(root, engine_bytes, dict_bytes):
    """A tree shaped like the real thing, with only the two files that matter."""
    mt = os.path.join(root, "Speech", "Synthesizers",
                      "MacinTalk.SpeechSynthesizer", "Contents", "MacOS")
    sd = os.path.join(root, "SpeechDictionary.framework", "Versions", "A")
    os.makedirs(mt)
    os.makedirs(sd)
    os.makedirs(os.path.join(root, "Speech", "Voices"))
    with open(os.path.join(mt, "MacinTalk"), "wb") as f:
        f.write(engine_bytes)
    with open(os.path.join(sd, "SpeechDictionary"), "wb") as f:
        f.write(dict_bytes)
    return root


def test_a_clean_tree_is_not_accused(tmp_path):
    root = _write_tree(str(tmp_path / "clean"), b"\xce\xfa\xed\xfe" + b"\0" * 64,
                       b"\xce\xfa\xed\xfe" + b"\0" * 64)
    assert tree.unsupported_build(root) is None


def test_a_protected_dictionary_is_named(tmp_path):
    root = _write_tree(str(tmp_path / "dict"), b"\xce\xfa\xed\xfe" + b"\0" * 64,
                       b"junk___commpage_dsmos\0more")
    why = tree.unsupported_build(root)
    assert why and "dictionary" in why


def test_a_protected_engine_is_named(tmp_path):
    root = _write_tree(str(tmp_path / "engine"), b"x___commpage_dsmos\0",
                       b"\xce\xfa\xed\xfe" + b"\0" * 64)
    why = tree.unsupported_build(root)
    assert why and "engine" in why


def test_the_message_says_what_to_do(tmp_path):
    """A dead end is not an answer.

    Two ways on, and neither is "go and put an engine in that folder" -- theirs
    is already there, which is exactly why the missing-engine dialog is the
    wrong thing to show.
    """
    root = _write_tree(str(tmp_path / "both"), b"a___commpage_dsmos\0",
                       b"b___commpage_dsmos\0")
    why = tree.unsupported_build(root)
    assert "3.4" in why
    assert "3.3" in why, "should name the Tiger build that does work"
    assert "leopard-speech" in why, "should name the add-on that is unaffected"
    assert "engine and dictionary" in why


def test_an_unreadable_file_does_not_condemn_a_tree(tmp_path):
    """A check that cannot run must not be what stops someone."""
    assert tree.needs_dsmos(str(tmp_path / "does-not-exist")) is False


@pytest.mark.skipif(not os.path.isdir(r"D:\speech-tiger45"),
                    reason="no 10.4.5 tree on this machine")
def test_against_the_real_trees():
    """The real thing, both ways round, if the trees are here.

    `D:\\speech-tiger45` is MacinTalk 3.4 and reproduces issue #1 exactly;
    the tree in `D:\\speech-tiger\\x86` is 3.3 and renders fine.
    """
    assert tree.needs_dsmos(
        r"D:\speech-tiger45\SpeechDictionary.framework\Versions\A"
        r"\SpeechDictionary") is True
    assert tree.needs_dsmos(
        r"D:\speech-tiger45\MacinTalk.SpeechSynthesizer\Contents\MacOS"
        r"\MacinTalk") is True
    assert tree.needs_dsmos(
        r"D:\speech-tiger\x86\SpeechDictionary.framework\Versions\A"
        r"\SpeechDictionary") is False
    assert tree.needs_dsmos(
        r"D:\speech-tiger\x86\Speech\Synthesizers"
        r"\MacinTalk.SpeechSynthesizer\Contents\MacOS\MacinTalk") is False
