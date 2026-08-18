# -*- coding: utf-8 -*-
"""Drive tiger_host in serve mode from the command line.

The same conversation the NVDA driver will have, so it is the cheapest way to
check the protocol and, more usefully, to time it: the whole point of the
native host is that a round trip costs milliseconds where the VM bridge cost
1.4 seconds.

    py -3 tools/speak.py "hello there" [voice] [wpm] [pitch]
"""
import os
import struct
import subprocess
import sys
import time
import wave

REQ = 0x54475233        # 'TGR3'
RSP = 0x54475253        # 'TGRS'

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TREE = os.environ.get("TIGER_TREE")
HOST = os.path.join(ROOT, "build", "tiger_host.exe")

if not TREE:
    raise SystemExit("set TIGER_TREE to your extracted Tiger speech tree")

MACINTALK = TREE + ("/Speech/Synthesizers/MacinTalk.SpeechSynthesizer"
                    "/Contents/MacOS/MacinTalk")
DICT = TREE + "/SpeechDictionary.framework/Versions/A/SpeechDictionary"
VOICES = TREE + "/Speech/Voices"


def start():
    return subprocess.Popen(
        [HOST, "--serve", MACINTALK, DICT, VOICES],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE)


def render(proc, text, voice="Fred", wpm=180, pitch=0, commands=False):
    t = text.encode("utf-8")
    v = voice.encode("utf-8")
    proc.stdin.write(struct.pack("<IiIIII", REQ, wpm, pitch,
                                 1 if commands else 0, len(v), len(t)) + v + t)
    proc.stdin.flush()
    head = proc.stdout.read(12)
    if len(head) < 12:
        raise SystemExit("host closed the pipe")
    magic, status, nframes = struct.unpack("<IiI", head)
    if magic != RSP:
        raise SystemExit("bad response magic %08x" % magic)
    want = nframes * 2
    pcm = b""
    while len(pcm) < want:                    # a pipe read can come up short
        chunk = proc.stdout.read(want - len(pcm))
        if not chunk:
            break
        pcm += chunk
    return status, pcm


def save(path, pcm, rate=22050):
    w = wave.open(path, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(rate)
    w.writeframes(pcm)
    w.close()


def main():
    text = sys.argv[1] if len(sys.argv) > 1 else "Hello there."
    voice = sys.argv[2] if len(sys.argv) > 2 else "Fred"
    wpm = int(sys.argv[3]) if len(sys.argv) > 3 else 180
    pitch = int(sys.argv[4]) if len(sys.argv) > 4 else 0

    t0 = time.perf_counter()
    proc = start()
    status, pcm = render(proc, text, voice, wpm, pitch)  # first pays startup
    t1 = time.perf_counter()
    print("startup + first utterance: %6.1f ms  (status %d, %d frames)"
          % ((t1 - t0) * 1000, status, len(pcm) // 2))

    for phrase in ("button", "checked", "menu", "b", text):
        t = time.perf_counter()
        status, pcm = render(proc, phrase, voice, wpm, pitch)
        dt = (time.perf_counter() - t) * 1000
        secs = len(pcm) / 2.0 / 22050
        print("  %-14s %6.1f ms  -> %5.2f s of audio  (%.0fx real time)"
              % ('"%s"' % phrase, dt, secs, secs / (dt / 1000) if dt else 0))

    save(os.path.join(ROOT, "serve-out.wav"), pcm)
    print("wrote serve-out.wav")
    proc.stdin.close()
    proc.wait(timeout=5)


if __name__ == "__main__":
    main()
