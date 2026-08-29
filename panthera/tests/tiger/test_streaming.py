# -*- coding: utf-8 -*-
"""Streaming must change when the audio arrives, never what the audio is.

The host can answer a request in two shapes: 'TGR3' returns the whole
utterance once it is finished, 'TGR4' returns it in chunks as the engine
produces it.  The second exists because the wait before the first sound was
most of a second on a paragraph, and the engine was never the reason -- it
renders at about ninety times real time, and the host was accumulating the
lot before handing over a single sample.

That makes one invariant worth more than any latency number: **the bytes must
be identical**.  Concatenate the chunks of a streamed response and you must
get, sample for sample, what the blocking response would have given for the
same request.  If that holds, streaming is a transport change and nothing
else -- Tiger's renders stay byte-exact, the epoch rebase still lands slices
where they belong, and the conversion from float to PCM has not moved.

The margin is what makes it possible.  Slices are written at an absolute
sample position, so one can land *behind* audio already sent, and sent audio
cannot be recalled.  Measured across Alex, Fred and Vicki at 120, 180 and 300
wpm, that happens 19 to 53 times an utterance and never by more than a single
frame -- the one-frame probe that opens each epoch.  The host holds back 512.
"""
import os
import struct
import subprocess

import pytest

REQ_BLOCKING = 0x54475233           # 'TGR3'
REQ_STREAMING = 0x54475234          # 'TGR4'
RSP = 0x54475253                    # 'TGRS'

#: Long enough to span many scheduling epochs, which is where a naive
#: implementation would lose or duplicate audio.
LONG_TEXT = ("The quick brown fox jumps over the lazy dog. "
             "Pack my box with five dozen liquor jugs. ") * 4


def _host(tree):
    from synthDrivers import tigerspeech
    mt, sd, voices = tigerspeech.engine_paths(tree)
    return subprocess.Popen([tigerspeech.HOST_EXE, "--serve", mt, sd, voices],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)


def _request(proc, magic, text, voice, wpm):
    t, v = text.encode("utf-8"), voice.encode("utf-8")
    proc.stdin.write(struct.pack("<IiIIII", magic, wpm, 0, 0, len(v), len(t))
                     + v + t)
    proc.stdin.flush()


def _read_exactly(proc, n):
    buf = b""
    while len(buf) < n:
        chunk = proc.stdout.read(n - len(buf))
        if not chunk:
            raise AssertionError("the host closed the pipe mid-response")
        buf += chunk
    return buf


def _blocking_response(proc, text, voice, wpm):
    _request(proc, REQ_BLOCKING, text, voice, wpm)
    magic, status, nframes = struct.unpack("<IiI", _read_exactly(proc, 12))
    assert magic == RSP, "bad response magic %08x" % magic
    return status, _read_exactly(proc, nframes * 2)


def _streamed_response(proc, text, voice, wpm):
    """Return (status, pcm, chunk_count) with the chunks concatenated."""
    _request(proc, REQ_STREAMING, text, voice, wpm)
    magic, status = struct.unpack("<Ii", _read_exactly(proc, 8))
    assert magic == RSP, "bad response magic %08x" % magic
    pcm, chunks = b"", 0
    while True:
        (n,) = struct.unpack("<I", _read_exactly(proc, 4))
        if n == 0:
            return status, pcm, chunks
        pcm += _read_exactly(proc, n * 2)
        chunks += 1


@pytest.mark.parametrize("voice,wpm", [("Fred", 180), ("Fred", 300)])
def test_streamed_audio_is_identical_to_blocking(engine_tree, voice, wpm):
    """The whole point: a different transport, the same samples."""
    proc = _host(engine_tree)
    try:
        want_status, want = _blocking_response(proc, LONG_TEXT, voice, wpm)
        got_status, got, chunks = _streamed_response(proc, LONG_TEXT, voice,
                                                     wpm)
    finally:
        proc.stdin.close()
        proc.wait(timeout=10)

    assert got_status == want_status
    assert len(got) == len(want), (
        "streamed %d bytes, blocking %d -- audio was lost or duplicated"
        % (len(got), len(want)))
    assert got == want, "the streamed samples differ from the blocking ones"
    # If it all arrived in one chunk nothing was actually streamed, and the
    # test would pass while proving nothing.
    assert chunks > 1, "the response arrived in one chunk; nothing streamed"


def test_a_stale_host_refuses_a_streaming_request(engine_tree):
    """The reason the request magic carries a version.

    An add-on folder can end up with an old tiger_host.exe beside a new
    driver.  A host that cannot stream has to refuse the request outright: a
    driver reading chunk lengths out of a blocking response would play noise.
    Approximated here by sending a magic no host will ever know.
    """
    proc = _host(engine_tree)
    try:
        _request(proc, 0x54475239, "hello", "Fred", 180)   # 'TGR9'
        assert proc.stdout.read(4) == b"", "the host answered a magic it does not know"
        assert proc.wait(timeout=10) != 0
    finally:
        if proc.poll() is None:
            proc.kill()


def test_streaming_still_ends_cleanly_on_an_empty_utterance(engine_tree):
    """A request that produces no audio must still terminate the response.

    Without the zero-length chunk the driver would block forever waiting for
    audio that is never coming, which is the failure mode the drivers guard
    against with `never_goes_permanently_silent`.
    """
    proc = _host(engine_tree)
    try:
        status, pcm, _ = _streamed_response(proc, "   ", "Fred", 180)
        assert status == 0
        # Whatever the engine makes of whitespace, the response has to end.
        assert isinstance(pcm, bytes)
    finally:
        proc.stdin.close()
        proc.wait(timeout=10)


def test_a_stale_cancel_does_not_kill_the_next_utterance(engine_tree):
    """How a fix for the lag came to be the lag.

    `cancel()` reaches the host through a Windows event, because it runs on
    NVDA's main thread and must never block.  The driver clears that event
    before writing a request, but it cannot close the gap: arrowing quickly
    through a timeline sends cancels faster than requests, and one landing
    between the clear and the host's wait loop aborted an utterance nobody
    had cancelled.  The user hears nothing at all for it.

    So the host consumes any pending cancel at the moment it starts an
    utterance.  Signalled before the request, the audio must arrive whole.
    """
    import ctypes
    from synthDrivers import tigerspeech
    k32 = ctypes.windll.kernel32
    # chr(92) rather than a backslash escape: this file has been rewritten by
    # shell heredocs more than once, and the escape does not survive that --
    # it arrived here as a tab, which still names a valid event and so still
    # passed.  A test that passes for the wrong reason is the thing to avoid.
    name = "Local" + chr(92) + "tigerspeech-test-cancel-%d" % os.getpid()
    ev = k32.CreateEventW(None, False, False, name)
    assert ev, "could not create the event this test needs"

    import subprocess as _sp
    mt, sd, voices = tigerspeech.engine_paths(engine_tree)
    env = dict(os.environ)
    env["TIGER_CANCEL_EVENT"] = name
    proc = _sp.Popen([tigerspeech.HOST_EXE, "--serve", mt, sd, voices],
                     stdin=_sp.PIPE, stdout=_sp.PIPE, env=env)
    try:
        _, want, _ = _streamed_response(proc, LONG_TEXT, "Fred", 180)
        # A cancel with nothing rendering: it belongs to the past, not to the
        # utterance that follows it.
        k32.SetEvent(ev)
        _, got, _ = _streamed_response(proc, LONG_TEXT, "Fred", 180)
    finally:
        proc.stdin.close()
        proc.wait(timeout=10)

    assert len(got) == len(want), (
        "a cancel from before the request truncated it: %d bytes against %d"
        % (len(got), len(want)))
