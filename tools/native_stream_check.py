"""Linux native-host integration check; requires the user's engine tree.
Usage: python3 tools/native_stream_check.py HOST TREE [VOICE]
Tests optional readiness, PCM volume, SIGUSR1, and persistent channel recovery.
"""
import array
import hashlib
import json
import os
from pathlib import Path
import signal
import struct
import subprocess
import sys
import time

host, tree = sys.argv[1], Path(sys.argv[2])
voice = sys.argv[3] if len(sys.argv) > 3 else "Fred"
env = dict(os.environ, TIGER_READY_HANDSHAKE="1")
proc = subprocess.Popen([host, "--serve",
    str(tree / "Speech/Synthesizers/MacinTalk.SpeechSynthesizer/Contents/MacOS/MacinTalk"),
    str(tree / "SpeechDictionary.framework/Versions/A/SpeechDictionary"),
    str(tree / "Speech/Voices")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, env=env)

def exact(n):
    data = b""
    while len(data) < n:
        chunk = proc.stdout.read(n-len(data))
        if not chunk:
            raise AssertionError("host closed mid-response")
        data += chunk
    return data

def render(level=1000, cancel=False):
    text = "Hello there." if not cancel else "The quick brown fox jumps over the lazy dog. " * 20
    t = ("[[volm %d.%03d]]" % (level//1000, level%1000) + text).encode("ascii")
    v = voice.encode("utf-8")
    proc.stdin.write(struct.pack("<IiIIII", 0x54475234, 180, 0, 0, len(v), len(t))+v+t)
    proc.stdin.flush()
    assert struct.unpack("<Ii", exact(8)) == (0x54475253, 0)
    chunks=[]; stopped=None
    while True:
        n, = struct.unpack("<I", exact(4))
        if not n: break
        chunks.append(exact(n*2))
        if cancel and stopped is None:
            stopped=time.monotonic()
            proc.send_signal(signal.SIGUSR1)
    elapsed = (time.monotonic()-stopped)*1000 if stopped else None
    return b"".join(chunks), elapsed

def energy(pcm):
    return sum(v*v for v in array.array('h', pcm))

try:
    signal.alarm(60)
    assert exact(4) == b"TRDY"
    full,_=render()
    assert len(full)>20000 and energy(full)>0
    for _ in range(3): assert render()[0] == full, "repeated render changed"
    half,_=render(500)
    mute,_=render(0)
    assert len(half)==len(full)==len(mute)
    assert not any(mute)
    assert .15 < energy(half)/energy(full) < .35
    assert render()[0]==full, "mute did not restore"
    _,cancel_ms=render(cancel=True)
    after,_=render()
    assert after==full, "PCM changed after cancellation"
    assert proc.poll() is None
    print(json.dumps(dict(voice=voice, frames=len(full)//2,
        sha256=hashlib.sha256(full).hexdigest(), cancel_ms=round(cancel_ms,1),
        persistent_recovery=True, volume=True)))
finally:
    signal.alarm(0)
    proc.stdin.close()
    try: proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill();proc.wait()
