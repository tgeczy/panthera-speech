# -*- coding: utf-8 -*-
"""Reading a Mac OS X install image: what it is, and what comes out.

These need real install images, which are nobody's to distribute and are not
in this repository.  Point `MAC_IMAGES` at a folder holding them, or leave it
and they skip.  The names are matched loosely because nobody renames a disc
image to suit a test.
"""
import os

import pytest

import pantheradiscs

IMAGE_DIR = os.environ.get("MAC_IMAGES") or r"D:\downloads"


def _find(*words):
    """-> the first image whose name has all of `words` in it."""
    try:
        names = sorted(os.listdir(IMAGE_DIR))
    except OSError:
        return None
    for name in names:
        low = name.lower()
        if not low.endswith((".iso", ".dmg", ".cdr")):
            continue
        if all(w.lower() in low for w in words):
            return os.path.join(IMAGE_DIR, name)
    return None


def _image(*words):
    path = _find(*words)
    if not path:
        pytest.skip("no image matching %s in %s" % (" ".join(words), IMAGE_DIR))
    return path


def test_a_leopard_dvd_is_recognised_as_leopard():
    disc = pantheradiscs.identify(_image("leopard", "10.5"))
    assert disc.usable, disc.problem
    assert disc.generation.key == "leopard"
    assert disc.version.startswith("10.5")


def test_a_lion_installer_is_recognised_as_lion():
    disc = pantheradiscs.identify(_image("lion", "10.7"))
    assert disc.usable, disc.problem
    assert disc.generation.key == "lion"


def test_mountain_lion_is_refused_and_says_why():
    """10.8 is where the i386 slice stops.

    Not a gap anyone can close by trying harder: its MacinTalk is a thin
    x86_64 binary and the host is 32-bit because Apple's engine is. Refusing
    with the reason is the whole difference between a wall and a mystery.
    """
    disc = pantheradiscs.identify(_image("mountain lion"))
    assert not disc.usable
    assert "10.8" in disc.problem and "32-bit" in disc.problem


def test_something_that_is_not_a_mac_disc_is_refused_kindly():
    path = _find("coconut") or _find("ubuntu") or _find("debian")
    if not path:
        pytest.skip("no non-Mac image to try")
    disc = pantheradiscs.identify(path)
    assert not disc.usable
    assert "Mac OS X install image" in disc.problem


def test_the_tiger_build_that_crashes_is_refused_before_extracting():
    """**The check the version number cannot make.**

    The MacinTalk that works is byte-identical on a disc reporting 10.4.1;
    the one that crashes is on 10.4.5. So a version test would send someone to
    exactly the wrong disc, and did -- see tiger-build-generations. The engine
    is a megabyte and can be hashed off the image in seconds, which is the
    difference between knowing now and finding out from silence in an hour.
    """
    path = _find("tiger", "10.4.5")
    if not path:
        pytest.skip("no 10.4.5 Tiger image to try")
    disc = pantheradiscs.identify(path)
    assert not disc.usable
    assert "crash" in disc.problem.lower()
    assert disc.engine_sha in pantheradiscs.KNOWN_BAD_ENGINES


def test_every_generation_we_offer_has_somewhere_to_put_it():
    for gen in pantheradiscs.GENERATIONS:
        assert gen.dirname.startswith("macintalk"), gen.key
        if gen.why_not is None:
            assert gen.driver, "%s has no synthesizer to read it" % gen.key


def test_the_reader_has_not_drifted_from_the_extractor(tmp_path):
    """`pantherahfs` is a copy of the extractor's reader.  Prove they agree.

    The copy exists because the standalone extractors have to stay single
    files and the add-on must not shell out to anything -- but a copy that
    quietly diverges is exactly what this project refuses everywhere else, so
    it is checked rather than trusted.
    """
    import sys
    lion_tools = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "lion", "tools")
    if not os.path.isdir(lion_tools):
        pytest.skip("the extractor is not in this checkout")
    sys.path.insert(0, lion_tools)
    try:
        import extract_lion
    finally:
        sys.path.remove(lion_tools)
    import pantherahfs

    path = _image("leopard", "10.5")
    got = []
    for reader in (pantherahfs, extract_lion):
        with open(path, "rb") as probe:
            def read_at(off, n, _p=probe):
                _p.seek(off)
                return _p.read(n)
            base = reader.find_hfs(read_at, os.path.getsize(path), path)
        volume = reader.Volume(path, base)
        entry = volume.entry(pantheradiscs.LIVE_ENGINE)
        got.append((base, entry[3],
                    reader.ExtentStream(volume, entry).read(65536)))
    assert got[0] == got[1], "the copied reader disagrees with the extractor"


def test_a_bundle_with_no_voice_description_is_not_counted(tmp_path):
    """The number in the dialog has to be the number in the voice list.

    A tree extracted before the Vocalizer filter existed holds all 28 Compact
    bundles too, so counting folders reads 52 where the synthesizer offers 24.
    The drivers route a voice by the creator in its `VoiceDescription`, and
    the Compact bundles have none at all, so this asks the same question.
    """
    voices = tmp_path / "Speech" / "Voices"
    real = voices / "Alex.SpeechVoice" / "Contents" / "Resources"
    real.mkdir(parents=True)
    (real / "VoiceDescription").write_bytes(b"\0" * 80)
    (voices / "KyokoCompact.SpeechVoice" / "Contents" / "Resources").mkdir(
        parents=True)
    (voices / "HalfCopied.SpeechVoice").mkdir()

    assert pantheradiscs.installed_voices(str(tmp_path)) == ["Alex"]
