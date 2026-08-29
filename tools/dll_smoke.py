# -*- coding: utf-8 -*-
"""Speak one utterance through the DLL, and prove it is the same utterance.

**Run me with 32-bit Python**: `py -3.13-32 tools/dll_smoke.py tiger`.  The
engine is Apple's i386 code and the process has to be able to call it, which
is the whole reason the executable existed in the first place.

Bare 32-bit Python is a faithful stand-in for NVDA's bridge host and not just
a convenient one: `py -3.13-32` is not large-address-aware, and neither is
`nvda_synthDriverHost.exe`, so both see the same 2 GB.  If it crashes here it
crashes in the bridge.

The check is identity of the audio, on both protocols, plus identity of the
raw bytes on the blocking one.  The same request goes to `tiger_host.exe` over
its pipes and to `tiger_host.dll` over the private pipes `pt_open` hands back,
and the two must agree -- not merely be similar in length, not merely both be
audible.  That is the acceptance test for the whole conversion, and running it
from the first day means a channel bug is found by the change that caused it.

**Why the streamed protocol is compared as audio and not as bytes.**  It was
compared as bytes first, and on Leopard it failed -- so the executable was run
against *itself*, and it failed the same way.  Decoded, the two runs had
126896 frames each and not one sample differed: what moves is where the chunk
boundaries fall.  `stream_chunk` sends whatever the render has produced by the
moment it looks, so the split depends on timing and nothing else, and two runs
of one binary disagree about it as readily as two binaries do.  The audio is
the promise; the chunking never was.

**Never on Lion.**  Lion's mtk3 voices do not render reproducibly -- the same
text through the same binary twice differs -- so a mismatch there would prove
nothing and a match would prove less.  Tiger and Leopard are deterministic and
are what this compares.
"""
from __future__ import print_function

import ctypes
import msvcrt
import os
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUILD = os.path.join(ROOT, "build")

REQ_MAGIC = 0x54475233          # 'TGR3', the whole response in one piece
REQ_MAGIC_STREAM = 0x54475234   # 'TGR4', the same audio in chunks
RSP_MAGIC = 0x54475253          # 'TGRS'

#: The generations that render the same bytes twice; see the module docstring.
TREES = {
    "tiger": ("Speech/Synthesizers/MacinTalk.SpeechSynthesizer/Contents/MacOS/"
              "MacinTalk", "Fred"),
    "leopard": ("Speech/Synthesizers/MacinTalk.SpeechSynthesizer/Contents/"
                "MacOS/MacinTalk", "Fred"),
    "snowleopard": ("Speech/Synthesizers/MacinTalk.SpeechSynthesizer/Contents/"
                    "MacOS/MacinTalk", "Fred"),
}


def enginePaths(tree, mtRelative):
    return (os.path.join(tree, *mtRelative.split("/")),
            os.path.join(tree, "SpeechDictionary.framework", "Versions", "A",
                         "SpeechDictionary"),
            os.path.join(tree, "Speech", "Voices"))


def request(voice, text, wpm=180, pitch=0, streaming=False):
    """The wire format, exactly as `pantheradriver` writes it."""
    name = voice.encode("ascii")
    body = text.encode("mac-roman")
    return (struct.pack("<IiiIII",
                        REQ_MAGIC_STREAM if streaming else REQ_MAGIC,
                        wpm, pitch, 0, len(name), len(body))
            + name + body)


def readExactly(stream, n):
    out = b""
    while len(out) < n:
        chunk = stream.read(n - len(out))
        if not chunk:
            raise IOError("the engine closed the channel")
        out += chunk
    return out


def readResponse(stdout, streaming):
    """Return the response bytes as they arrived, header and all.

    The raw bytes rather than decoded frames, because the question being asked
    is whether the two hosts *wrote the same thing*, and decoding first would
    hide a difference in framing behind identical audio.
    """
    head = readExactly(stdout, 8)
    magic, err = struct.unpack("<Ii", head)
    if magic != RSP_MAGIC:
        raise IOError("not a response: %08x" % magic)
    out = head
    if streaming:
        while True:
            raw = readExactly(stdout, 4)
            out += raw
            n = struct.unpack("<I", raw)[0]
            if not n:
                break
            out += readExactly(stdout, n * 2)
    else:
        raw = readExactly(stdout, 4)
        out += raw
        out += readExactly(stdout, struct.unpack("<I", raw)[0] * 2)
    return err, out


def throughExe(mt, sd, voices, requests):
    exe = os.path.join(BUILD, "tiger_host.exe")
    si = subprocess.STARTUPINFO()
    si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    proc = subprocess.Popen([exe, "--serve", mt, sd, voices],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, startupinfo=si)
    try:
        out = []
        for req, streaming in requests:
            proc.stdin.write(req)
            proc.stdin.flush()
            out.append(readResponse(proc.stdout, streaming))
        return out
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.kill()
        proc.wait()


def throughDll(mt, sd, voices, requests):
    dll = ctypes.CDLL(os.path.join(BUILD, "tiger_host.dll"))
    dll.pt_open.restype = ctypes.c_int
    dll.pt_open.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                            ctypes.c_char_p,
                            ctypes.POINTER(ctypes.c_void_p),
                            ctypes.POINTER(ctypes.c_void_p)]
    reqw, rspr = ctypes.c_void_p(), ctypes.c_void_p()
    err = dll.pt_open(mt.encode("mbcs"), sd.encode("mbcs"),
                      voices.encode("mbcs"), None,
                      ctypes.byref(reqw), ctypes.byref(rspr))
    if err:
        raise RuntimeError("pt_open returned %d" % err)

    # The handles become ordinary file objects; from here the driver's own
    # protocol code would not be able to tell them from a subprocess's pipes,
    # which is the entire point of doing it this way.
    stdin = os.fdopen(msvcrt.open_osfhandle(reqw.value, 0), "wb")
    stdout = os.fdopen(msvcrt.open_osfhandle(rspr.value, os.O_RDONLY), "rb")
    try:
        out = []
        for req, streaming in requests:
            stdin.write(req)
            stdin.flush()
            out.append(readResponse(stdout, streaming))
        return out
    finally:
        # Ours to close, and closed before pt_close: the DLL deliberately does
        # not touch these, so that no handle is ever closed twice.
        try:
            stdin.close()
        except Exception:
            pass
        try:
            stdout.close()
        except Exception:
            pass
        dll.pt_close()


def main(argv):
    if ctypes.sizeof(ctypes.c_void_p) != 4:
        print("this needs 32-bit Python: py -3.13-32 tools/dll_smoke.py",
              file=sys.stderr)
        return 2
    which = argv[1] if len(argv) > 1 else "tiger"
    if which not in TREES:
        print("choose one of: %s (never lion, it is not reproducible)"
              % ", ".join(sorted(TREES)), file=sys.stderr)
        return 2
    mtRelative, voice = TREES[which]
    tree = argv[2] if len(argv) > 2 else os.path.join(
        os.environ["APPDATA"], "nvda", "macintalk", which)
    mt, sd, voices = enginePaths(tree, mtRelative)
    if not os.path.isfile(mt):
        print("no engine at %s" % mt, file=sys.stderr)
        return 2

    text = ("The quick brown fox jumps over the lazy dog. "
            "Panthera speaks from a library now.")
    requests = [(request(voice, text), False),
                (request(voice, text, streaming=True), True)]

    print("engine: %s" % tree)
    print("running the executable ...")
    fromExe = throughExe(mt, sd, voices, requests)
    print("running the DLL ...")
    fromDll = throughDll(mt, sd, voices, requests)

    def frames(raw, streaming):
        """The audio out of a response, without the framing around it."""
        if not streaming:
            return raw[12:]
        out, at = b"", 8
        while True:
            n = struct.unpack_from("<I", raw, at)[0]
            at += 4
            if not n:
                return out
            out += raw[at:at + n * 2]
            at += n * 2

    ok = True
    for (req, streaming), (e1, b1), (e2, b2) in zip(requests, fromExe,
                                                    fromDll):
        label = "streamed" if streaming else "blocking"
        a1, a2 = frames(b1, streaming), frames(b2, streaming)
        if e1 != e2:
            print("  %s: FAIL -- OSErr %d from the exe, %d from the DLL"
                  % (label, e1, e2))
            ok = False
        elif a1 != a2:
            first = next((i for i, (x, y) in enumerate(zip(a1, a2))
                          if x != y), min(len(a1), len(a2)))
            print("  %s: FAIL -- %d frames vs %d, first differing at frame %d"
                  % (label, len(a1) // 2, len(a2) // 2, first // 2))
            ok = False
        elif not streaming and b1 != b2:
            # Blocking framing is a fixed header, so a difference there is a
            # difference in the header itself and worth failing on.
            print("  %s: FAIL -- identical audio behind a different header"
                  % label)
            ok = False
        else:
            print("  %s: %d frames, identical" % (label, len(a1) // 2))

    # The two protocols must also agree with each other, which is the
    # invariant the streaming test already rests on -- checked here because a
    # channel that dropped the held-back margin would otherwise pass above by
    # dropping it identically in both hosts.
    if ok and frames(fromDll[0][1], False) != frames(fromDll[1][1], True):
        print("  FAIL -- the DLL's streamed and blocking audio differ")
        ok = False

    print("OK" if ok else "MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
