# -*- coding: utf-8 -*-
"""Time to first sound, through the streaming path, on whatever host is here.

**This measures the reason the Linux build exists.**  Devin's port runs the
Windows host under Wine and the complaint is not that it is wrong, it is that
it lags -- so a native ELF that renders byte-identical audio has proved
nothing until it has been timed.  Byte-identity is a claim about the samples;
this is a claim about when they arrive, and no oracle in this repository
covers it.

What a screen-reader user feels is the first number below: the gap between
pressing a key and hearing anything.  The last frame matters far less --
speech is consumed as it plays, so a render that finishes in half real time
sounds the same as one that finishes in a twentieth.

    py -3 tools/latency.py [text] [voice] [wpm] [runs]

    TIGER_TREE   the speech tree to use          (required)
    TIGER_HOST   a specific host binary          (optional)

The first utterance is reported separately and never averaged in: it carries
the dictionary map and the voice load, which for Alex is a 701 MB bank.  That
cost is real but it is paid once per session, not once per keystroke, and
averaging it in would hide the number that matters behind a number that only
happens at startup.
"""
import os
import struct
import subprocess
import sys
import time

REQ_STREAM = 0x54475234         # 'TGR4', the streamed request
RSP = 0x54475253                # 'TGRS'

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def find_host():
    named = os.environ.get("TIGER_HOST")
    if named:
        return named
    cands = [os.path.join(ROOT, "build", "tiger_host.exe")]
    build = os.path.join(ROOT, "build")
    if os.path.isdir(build):
        cands += [os.path.join(build, d, "tiger_host")
                  for d in sorted(os.listdir(build)) if d.startswith("linux-")]
    for c in cands:
        if os.path.exists(c):
            return c
    raise SystemExit("no host binary found; build one, or set TIGER_HOST")


HOST = find_host()
TREE = os.environ.get("TIGER_TREE")
if not TREE:
    raise SystemExit("set TIGER_TREE to your extracted speech tree")

MACINTALK = TREE + ("/Speech/Synthesizers/MacinTalk.SpeechSynthesizer"
                    "/Contents/MacOS/MacinTalk")
DICT = TREE + "/SpeechDictionary.framework/Versions/A/SpeechDictionary"
VOICES = TREE + "/Speech/Voices"


def read_exactly(f, n):
    """Read n bytes or die.  A short read here means the host stopped talking,
    which is a crash and not a measurement."""
    buf = b""
    while len(buf) < n:
        part = f.read(n - len(buf))
        if not part:
            raise SystemExit("the host closed the pipe mid-response")
        buf += part
    return buf


def one(proc, text, voice, wpm):
    """Send one streamed request; return (ms to first audio, ms to last, frames).

    The clock starts at the flush, because that is when the driver has finished
    asking.  It stops for the first number when the first non-empty chunk has
    been read whole -- a chunk the player could hand to the sound card.
    """
    t, v = text.encode("utf-8"), voice.encode("utf-8")
    start = time.perf_counter()
    proc.stdin.write(struct.pack("<IiIIII", REQ_STREAM, wpm, 0, 0,
                                 len(v), len(t)) + v + t)
    proc.stdin.flush()

    head = read_exactly(proc.stdout, 8)
    magic, err = struct.unpack("<Ii", head)
    if magic != RSP:
        raise SystemExit("bad response magic %08x" % magic)
    if err:
        raise SystemExit("the host reported OSErr %d" % err)

    first = None
    frames = 0
    while True:
        (n,) = struct.unpack("<I", read_exactly(proc.stdout, 4))
        if n == 0:
            break
        read_exactly(proc.stdout, n * 2)
        if first is None:
            first = time.perf_counter()
        frames += n
    done = time.perf_counter()
    return ((first - start) * 1000.0, (done - start) * 1000.0, frames)


def main():
    text = sys.argv[1] if len(sys.argv) > 1 else \
        "The quick brown fox jumps over the lazy dog."
    voice = sys.argv[2] if len(sys.argv) > 2 else "Fred"
    wpm = int(sys.argv[3]) if len(sys.argv) > 3 else 180
    runs = int(sys.argv[4]) if len(sys.argv) > 4 else 5

    proc = subprocess.Popen([HOST, "--serve", MACINTALK, DICT, VOICES],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        print("host   %s" % HOST)
        print("voice  %s at %d wpm, %d runs" % (voice, wpm, runs))
        print("")

        f0, d0, frames = one(proc, text, voice, wpm)
        audio_ms = frames * 1000.0 / 22050.0
        print("first utterance   first sound %7.1f ms   complete %7.1f ms"
              % (f0, d0))
        print("                  (includes the voice load, once per session)")
        print("")

        firsts, dones = [], []
        for _ in range(runs):
            f, d, _ = one(proc, text, voice, wpm)
            firsts.append(f)
            dones.append(d)

        firsts.sort()
        dones.sort()
        print("warm, %d runs      first sound  min %6.1f  median %6.1f  "
              "max %6.1f ms" % (runs, firsts[0], firsts[len(firsts) // 2],
                                firsts[-1]))
        print("                  complete     min %6.1f  median %6.1f  "
              "max %6.1f ms" % (dones[0], dones[len(dones) // 2], dones[-1]))
        print("")
        print("audio produced    %d frames, %.0f ms of speech" %
              (frames, audio_ms))
        print("render speed      %.1fx real time (median, to last frame)"
              % (audio_ms / dones[len(dones) // 2]))
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.wait()


main()
