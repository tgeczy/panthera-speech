# -*- coding: utf-8 -*-
"""A long run of one letter must not be able to kill the host.

`SLPrefixMorph::AddAffix` keeps a saved word's length in a signed byte and adds
each affix to it without a bound check. A run of the same letter is what makes
that reachable: every position in the run offers the morphology the same prefix
match, the decompositions multiply, and the byte climbs past 127 and reads back
negative. Twenty x's followed by "the" was enough (issue #4, found by Brandon's
fuzzer).

It failed two ways depending on how far it got — a `memmove` asking for four
gigabytes, or a quieter overrun of one record into the next that surfaced later
as a null dereference in the synthesis path — and one input wedged a host
process that had to be killed by hand. All three are the same overflow.

The host now breaks a run longer than ten with a space before the engine sees
it. These are the inputs that used to be fatal, so they are the ones worth a
test; what the audio *sounds* like is not the point, staying alive is.
"""
import struct
import subprocess

import pytest

REQ = 0x54475233                    # 'TGR3'
RSP = 0x54475253                    # 'TGRS'


def _host(tree):
    import tigerspeech
    mt, sd, voices = tigerspeech.engine_paths(tree)
    return subprocess.Popen([tigerspeech.HOST_EXE, "--serve", mt, sd, voices],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)


def _say(proc, text, voice="Fred", wpm=180):
    """Render one utterance, returning the frame count.

    Raises rather than hanging if the host dies: a closed pipe is exactly the
    symptom this test exists to catch.
    """
    t, v = text.encode("utf-8"), voice.encode("utf-8")
    proc.stdin.write(struct.pack("<IiIIII", REQ, wpm, 0, 0, len(v), len(t))
                     + v + t)
    proc.stdin.flush()
    head = proc.stdout.read(12)
    if len(head) < 12:
        raise AssertionError("the host died on %r" % text[:40])
    magic, status, nframes = struct.unpack("<IiI", head)
    assert magic == RSP, "bad response magic %08x" % magic
    got = b""
    while len(got) < nframes * 2:
        chunk = proc.stdout.read(nframes * 2 - len(got))
        if not chunk:
            raise AssertionError("the host died mid-response on %r" % text[:40])
        got += chunk
    return status, nframes


#: The exact inputs from the report: a run, then an affix the dictionary knows.
FATAL = [pytest.param("x" * 20 + "the", id="x20-the"),
         pytest.param("x" * 60 + "the", id="x60-the"),
         pytest.param("z" * 20 + "the", id="z20-the"),
         pytest.param("X" * 20 + "the", id="X20-the"),
         pytest.param("x" * 30 + "ing", id="x30-ing"),
         pytest.param("x" * 30 + "able", id="x30-able"),
         pytest.param("before it: " + "x" * 20 + "the", id="mid-sentence")]


#: **Vicki, and not Fred.**
#:
#: Written with Fred first, and it passed with the fix compiled out -- which
#: makes it worth nothing.  Which voice you ask decides what the corrupted word
#: record does, and it splits by engine: rebuilt with the limit raised out of
#: reach, the two concatenative voices **crash** (Vicki on the first `meow`
#: bank, Alex on the later one), the two MacinTalk Pro voices **wedge the
#: process**, and MacinTalk 3's Fred and Junior render all 83328 frames without
#: noticing.
#:
#: Vicki is the one that both fails and loads quickly -- Alex costs 701 MB of
#: samples per host -- so she is the canary.
CANARY = "Vicki"


@pytest.mark.parametrize("text", FATAL)
def test_a_long_letter_run_does_not_kill_the_host(engine_tree, text):
    proc = _host(engine_tree)
    try:
        status, nframes = _say(proc, text, voice=CANARY)
        assert status == 0
        assert nframes > 0, "survived but produced nothing"
        # Still usable afterwards, which is the part a user would notice.
        status, nframes = _say(proc, "Hello there.", voice=CANARY)
        assert status == 0 and nframes > 0
    finally:
        proc.stdin.close()
        proc.wait(timeout=20)


def test_ordinary_long_words_are_untouched(engine_tree):
    """The cure must not reach anything real.

    These are longer than the run limit and none of them is repetitive, so the
    engine has never had trouble with them and the host must not start
    inserting spaces into them.
    """
    proc = _host(engine_tree)
    try:
        for word in ("antidisestablishmentarianism",
                     "supercalifragilisticexpialidocious",
                     "kubernetes-controller-manager-the",
                     "getUserPreferencesTheme"):
            status, nframes = _say(proc, word)
            assert status == 0 and nframes > 0, "failed on %r" % word
    finally:
        proc.stdin.close()
        proc.wait(timeout=20)
