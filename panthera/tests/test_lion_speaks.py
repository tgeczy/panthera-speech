# -*- coding: utf-8 -*-
"""Lion speaks, and has to keep speaking.

Every bug fixed on the way to 10.7 got a test. The thing they were all for did
not, which is the wrong way round: a later shim or loader change could kill
Lion outright and the rest of the suite would stay green. That is precisely
the silent-failure shape this project keeps writing notes about.

So: render Fred's default utterance through the built host and check the
bytes. Fred is MacinTalk 3 formant synthesis -- no sample bank, no AAC, no
FFT -- so this is the cheapest render the engine can do and it exercises the
whole chain anyway: dyld info streams, `libc++abi` and `libstdc++` bound as
optional images, the formatted dictionary table names, `stat$INODE64`, the
SQLite phrasing table, GCD's timer as the render pump, and the AudioUnit
graph.

**The render is deterministic**, checked three times before pinning: same
SHA-256 every run, so the hash is the assertion rather than a tolerance. If it
ever stops being deterministic, that is worth knowing on its own -- real time
now flows into the engine through `gettimeofday`, where a stub's zero used to,
and "it only measures intervals with it" is an assumption rather than a
finding.

The stub report is checked too. A shim that goes from unreached to reached
means the engine has taken a path it did not take before, and every expensive
bug this session began exactly there.
"""
import hashlib
import os
import subprocess
import wave

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
HOST = os.path.join(ROOT, "build", "tiger_host.exe")

TREE = r"D:\speech-lion"
ENGINE = os.path.join(TREE, r"Speech\Synthesizers"
                            r"\MacinTalk.SpeechSynthesizer\Contents\MacOS"
                            r"\MacinTalk")
DICT = os.path.join(TREE, r"SpeechDictionary.framework\Versions\A"
                          r"\SpeechDictionary")
VOICE = os.path.join(TREE, r"Speech\Voices\Fred.SpeechVoice")

#: "Hello there." -- the host's own default, so nothing has to be passed in.
FRAMES = 18704
SHA256 = "15110608433adf0746689a8bc2040a56e55f48d8397a1431631abfa1a8086c00"

#: Shims the engine is *expected* to call without them being implemented.
#: `CFPropertyListCreateFromXMLData` is asked for once, answered NULL, and has
#: never moved anything -- but it stays named here rather than ignored, so
#: that a *second* unimplemented call becomes a test failure.
EXPECTED_STUBS = {"_CFPropertyListCreateFromXMLData"}


@pytest.fixture(scope="module")
def render(tmp_path_factory):
    """-> (wav path, host stderr). The host writes into its own cwd."""
    for p in (HOST, ENGINE, DICT):
        if not os.path.exists(p):
            pytest.skip("not built, or no Lion tree at %s" % TREE)
    out = tmp_path_factory.mktemp("lion")
    env = dict(os.environ)
    env.pop("TIGER_TEXT", None)          # the default utterance is the subject
    env.pop("TIGER_SQLITE", None)
    run = subprocess.run([HOST, ENGINE, DICT, VOICE], cwd=str(out), env=env,
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=600)
    return os.path.join(str(out), "tiger-out.wav"), run.stderr


def test_a_wav_was_written(render):
    path, err = render
    assert os.path.isfile(path), err[-3000:]


def test_it_is_the_same_render_every_time(render):
    """Deterministic, so the hash can be the assertion.

    A change here is not necessarily a regression -- but it is never nothing,
    and it should have to be looked at deliberately.
    """
    path, err = render
    with open(path, "rb") as f:
        got = hashlib.sha256(f.read()).hexdigest()
    assert got == SHA256, "render changed:\n%s" % err[-3000:]


def test_it_is_audio_and_not_silence(render):
    """The assertion the hash cannot make on its own.

    A hash pins whatever was produced, including a wav full of zeroes. This is
    the part that says the engine actually synthesised something.
    """
    path, _ = render
    with wave.open(path, "rb") as w:
        assert w.getframerate() == 22050
        assert w.getnframes() == FRAMES
        data = w.readframes(w.getnframes())
    peak = max(abs(int.from_bytes(data[i:i + 2], "little", signed=True))
               for i in range(0, len(data), 2))
    quiet = sum(1 for i in range(0, len(data), 2)
                if data[i:i + 2] == b"\0\0")
    assert peak > 5000, peak
    assert quiet < len(data) // 4, "%d of %d samples are zero" % (
        quiet, len(data) // 2)


def test_no_new_shim_is_being_reached(render):
    """A stub going from unreached to reached is the engine taking a new path.

    Every expensive bug on the way to 10.7 started as a shim quietly answering
    zero, and zero is `noErr`, `SQLITE_OK` and a successful `stat()`. This is
    the cheap way to be told.
    """
    _, err = render
    reached = set()
    for line in err.splitlines():
        parts = line.split()
        # "     5 x  _strpbrk"
        if len(parts) == 3 and parts[1] == "x" and parts[2].startswith("_"):
            reached.add(parts[2])
    assert reached <= EXPECTED_STUBS, "newly reached: %s" % sorted(
        reached - EXPECTED_STUBS)


def test_the_optional_runtimes_were_both_loaded(render):
    """Lion needs two, not one: 10.7 split the C++ ABI out of libstdc++."""
    _, err = render
    assert "libc++abi" in err, err[:3000]
    assert "libstdc++" in err, err[:3000]
    assert "no libc++abi" not in err, "libc++abi was not found"
