# -*- coding: utf-8 -*-
"""Render exactly one utterance in a fresh host, and nothing else.

`speak.py` keeps a host resident and sends it a handful of utterances, which
is the right shape for timing the protocol but the wrong one for diagnosis:
every render after the first is measured in a warmed process, and state that
leaks between utterances hides inside the warm-up.

This starts a host, sends one request, writes the wav and exits.  That is what
caught the timeline-epoch bug: the same sentence in the same voice came back
either as 98900 frames with every word present or as 50395 with the second
sentence written over the first, a coin flip run to run, and it only looked
like a coin flip once each render had a process to itself.

    set TIGER_TREE=D:\\speech-leopard
    py -3 tools/render_once.py "One, two, three." Alex 180 out.wav

Useful alongside it:

    TIGER_FLOAT_STATS=1   slice timeline, and the decoder session profile
    TIGER_ACCEL_DEBUG=1   the vDSP calls, if any
"""
import os
import struct
import subprocess
import sys
import wave

REQ = 0x54475233        # 'TGR3'
RSP = 0x54475253        # 'TGRS'

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOST = os.path.join(ROOT, "build", "tiger_host.exe")
TREE = os.environ.get("TIGER_TREE")

if not TREE:
    raise SystemExit("set TIGER_TREE to your extracted speech tree")

MACINTALK = TREE + ("/Speech/Synthesizers/MacinTalk.SpeechSynthesizer"
                    "/Contents/MacOS/MacinTalk")
DICT = TREE + "/SpeechDictionary.framework/Versions/A/SpeechDictionary"
VOICES = TREE + "/Speech/Voices"


def main():
    text = sys.argv[1] if len(sys.argv) > 1 else "Hello there."
    voice = sys.argv[2] if len(sys.argv) > 2 else "Fred"
    wpm = int(sys.argv[3]) if len(sys.argv) > 3 else 180
    out = sys.argv[4] if len(sys.argv) > 4 else "once-out.wav"

    proc = subprocess.Popen([HOST, "--serve", MACINTALK, DICT, VOICES],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    t, v = text.encode("utf-8"), voice.encode("utf-8")
    proc.stdin.write(struct.pack("<IiIIII", REQ, wpm, 0, 0, len(v), len(t))
                     + v + t)
    proc.stdin.flush()

    head = proc.stdout.read(12)
    if len(head) < 12:
        raise SystemExit("the host closed the pipe without answering")
    magic, status, nframes = struct.unpack("<IiI", head)
    if magic != RSP:
        raise SystemExit("bad response magic %08x" % magic)

    pcm = b""
    while len(pcm) < nframes * 2:           # a pipe read can come up short
        chunk = proc.stdout.read(nframes * 2 - len(pcm))
        if not chunk:
            break
        pcm += chunk
    proc.stdin.close()
    proc.wait()

    w = wave.open(out, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(22050)
    w.writeframes(pcm)
    w.close()
    print("status %d, %d frames, %.2f s -> %s"
          % (status, nframes, nframes / 22050.0, out))


if __name__ == "__main__":
    main()
