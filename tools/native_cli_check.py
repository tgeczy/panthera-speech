"""Check native --render file/stdin/stdout behavior with user-owned data.

Usage: python tools/native_cli_check.py HOST TREE [VOICE]
"""
import io
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import wave


host, tree = sys.argv[1], Path(sys.argv[2])
voice = sys.argv[3] if len(sys.argv) > 3 else "Fred"
base = [host, "--render", "--tree", str(tree), "--voice", voice]


def render(text, *options):
    result = subprocess.run(base + ["--output", "-"] + list(options),
                            input=text.encode("utf-8"), capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr.decode(errors="replace")[-1000:]
    with wave.open(io.BytesIO(result.stdout), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 22050)
        pcm = wav.readframes(wav.getnframes())
    assert len(pcm) > 1000
    return result.stdout, pcm


text = "Hello there."
wav, full = render(text)
assert any(full)
with tempfile.TemporaryDirectory() as tmp:
    output = Path(tmp) / "render.wav"
    subprocess.run(base + ["--text", text, "--output", str(output)],
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=60)
    assert output.read_bytes() == wav
    source = Path(tmp) / "input.txt"
    source.write_text("Caf\u00e9. Version 0.7.3.", encoding="utf-8")
    result = subprocess.run(base + ["--input", str(source), "--output", "-"],
                            capture_output=True, check=True, timeout=60)
    assert result.stdout == render(source.read_text(encoding="utf-8"))[0]

_, mute = render(text, "--volume", "0")
assert len(mute) == len(full) and not any(mute)
_, fast = render(text, "--rate", "360")
assert len(fast) < len(full) * .8
assert render("There are 1234567 people.")[1] == render(
    "There are 1,234,567 people.", "--numbers", "off")[1]
assert render("There are 12 people.", "--numbers", "words")[1] == render(
    "There are twelve people.", "--numbers", "off")[1]

# An independent request over IPC checks UTF-8 -> MacRoman conversion and
# ensures CLI output follows the same synthesis path as integration clients.
utterance = "Caf\u00e9 is open."
name = voice.encode("utf-8")
encoded = ("[[volm 1.000]]" + utterance).encode("mac_roman")
request = struct.pack("<IiiIII", 0x54475233, 180, 0, 2, len(name), len(encoded)) + name + encoded
result = subprocess.run([host, "--serve",
    str(tree / "Speech/Synthesizers/MacinTalk.SpeechSynthesizer/Contents/MacOS/MacinTalk"),
    str(tree / "SpeechDictionary.framework/Versions/A/SpeechDictionary"),
    str(tree / "Speech/Voices")], input=request, capture_output=True, check=True, timeout=60)
magic, status, frames = struct.unpack("<IiI", result.stdout[:12])
assert magic == 0x54475253 and status == 0 and len(result.stdout) == 12 + frames * 2
assert render(utterance)[1] == result.stdout[12:]

for options, data in [(["--rate", "12oops"], b"hello"),
                      (["--volume", "101"], b"hello"),
                      (["--numbers", "invalid"], b"hello"),
                      ([], b"\xff"), ([], b"a\0b"), ([], b"")]:
    result = subprocess.run(base + options + ["--output", "-"], input=data,
                            capture_output=True, timeout=60)
    assert result.returncode != 0 and not result.stdout
print(json.dumps(dict(voice=voice, frames=len(full)//2, cli=True, utf8=True,
                      pipes=True, numbers=True, volume=True)))
