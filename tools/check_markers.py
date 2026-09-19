"""Check TGR5 against the legacy transports on an extracted engine tree.

No playback or deployment. Example:
  python tools/check_markers.py --host build/tiger_host.exe --tree C:/path/to/lion --voice Alex
"""
import argparse
import contextlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import threading
import time

BLOCK, STREAM, MARKED, RESPONSE = 0x54475233, 0x54475234, 0x54475235, 0x54475253
MARK, ERROR = 0x80000001, 0x80000002
CASES = {
    "adjacent": "One [[sync 0x65]][[sync 0x66]] two three.",
    "sentences": "Hello [[sync 0x65]] there. This [[sync 0x66]] is a second sentence.",
    "paragraph": ("This is the first sentence.\n\nHere [[sync 0x65]] comes the second "
                  "paragraph, with several more words before [[sync 0x66]] its ending."),
    "breath": ("Run  dialog  Type the name of a program, folder, document, or Internet "
               "resource, [[sync 0x65]] and Windows will open it for you.\n"
               "This task [[sync 0x66]] will be created with administrative privileges."),
}


def read_exactly(stream, n):
    result = bytearray()
    while len(result) < n:
        data = stream.read(n - len(result))
        if not data:
            raise AssertionError("host closed the protocol mid-response")
        result.extend(data)
    return bytes(result)


@contextlib.contextmanager
def engine(host, tree, log, env=None):
    tree = Path(tree)
    args = [str(host), "--serve",
            str(tree / "Speech/Synthesizers/MacinTalk.SpeechSynthesizer/Contents/MacOS/MacinTalk"),
            str(tree / "SpeechDictionary.framework/Versions/A/SpeechDictionary"),
            str(tree / "Speech/Voices")]
    with open(log, "wb") as errors:
        proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=errors, env=env)
        watchdog = threading.Timer(90, proc.kill)
        watchdog.start()
        try:
            yield proc
        finally:
            watchdog.cancel()
            proc.stdin.close()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            proc.stdout.close()


def render(proc, text, voice, rate, protocol=MARKED, slow=False, on_audio=None):
    text, voice = text.encode("mac_roman"), voice.encode("utf8")
    proc.stdin.write(struct.pack("<IiiIII", protocol, rate, 0, 0, len(voice), len(text))
                     + voice + text)
    proc.stdin.flush()
    magic, status = struct.unpack("<Ii", read_exactly(proc.stdout, 8))
    assert magic == RESPONSE and status == 0, (hex(magic), status)
    if protocol == BLOCK:
        frames, = struct.unpack("<I", read_exactly(proc.stdout, 4))
        return read_exactly(proc.stdout, frames * 2), []
    pcm, marks = bytearray(), []
    while True:
        count, = struct.unpack("<I", read_exactly(proc.stdout, 4))
        if count == 0:
            return bytes(pcm), marks
        if count == MARK and protocol == MARKED:
            ident, frame = struct.unpack("<II", read_exactly(proc.stdout, 8))
            assert frame * 2 == len(pcm), ("late/out of order marker", ident, frame, len(pcm))
            marks.append((ident, frame))
        elif count == ERROR:
            raise AssertionError(("runtime marker invariant failed",
                                  struct.unpack("<i", read_exactly(proc.stdout, 4))))
        else:
            assert 0 < count <= 1024, count
            pcm.extend(read_exactly(proc.stdout, count * 2))
            if on_audio:
                on_audio(len(pcm) // 2)
            if slow:
                time.sleep(0.001)


def check(host, tree, voice, rate, log):
    results = []
    with engine(host, tree, log) as proc:
        for name, marked in CASES.items():
            plain = re.sub(r"\[\[sync .*?\]\]", "", marked)
            reference, _ = render(proc, plain, voice, rate, BLOCK)
            streamed, _ = render(proc, plain, voice, rate, STREAM)
            assert reference and streamed == reference, name
            markers = None
            for repeat in range(3):
                pcm, current = render(proc, marked, voice, rate, slow=repeat == 1)
                assert pcm == reference, (name, len(reference), len(pcm))
                assert [i for i, _ in current] == [101, 102], (name, current)
                assert markers is None or markers == current, (name, markers, current)
                markers = current
            # Installed hooks must remain transparent to legacy consumers.
            after, _ = render(proc, plain, voice, rate, STREAM)
            if after != reference:
                Path(log).with_suffix(".reference.pcm").write_bytes(reference)
                Path(log).with_suffix(".after.pcm").write_bytes(after)
                raise AssertionError((name, "legacy after hooks", len(reference), len(after),
                    next((i // 2 for i, (a, b) in enumerate(zip(reference, after)) if a != b), None)))
            results.append(dict(case=name, frames=len(reference) // 2, markers=markers))
    return results


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--host", required=True, type=Path)
    p.add_argument("--tree", required=True, type=Path)
    p.add_argument("--voice", default="Alex")
    p.add_argument("--rate", type=int, default=387)
    p.add_argument("--out", type=Path, default=Path("build/marker-check"))
    args = p.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    result = check(args.host.resolve(), args.tree, args.voice, args.rate, args.out / "host.log")
    (args.out / "results.json").write_text(json.dumps(result, indent=2), encoding="utf8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
