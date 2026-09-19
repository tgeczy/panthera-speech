"""Positioned indexes preserve speech, action order, and cancellation."""
import io
import contextlib
import os
from pathlib import Path
import struct
import threading
from types import SimpleNamespace

import pytest

from synthDrivers._panthera.host import HostMixin, RSP_MAGIC
from synthDrivers._panthera.marker_pipeline import marker_plan
from synthDrivers._panthera.audio import _silence
from test_marks import _bare, _drain, _feed, _Reports


def configured(proc):
    d = _bare(False)
    d._markerStreaming = True
    d._proc, d._procLock = proc, threading.Lock()
    d._host = lambda: d._proc
    d._clearCancel = lambda: None
    d._volume, d.VOLUME_NORM = 100, {}
    d._numberStyle, d._expandAbbreviations, d._fixStress = "off", True, False
    d._inflection, d._inflectionSent, d._renderSeq = 50, False, 0
    d._render = HostMixin._render.__get__(d)
    return d


def fake(response):
    return SimpleNamespace(pid=123, stdin=io.BytesIO(), stdout=io.BytesIO(response), kill=lambda: None)


def response(*records):
    return struct.pack("<Ii", RSP_MAGIC, 0) + b"".join(records) + bytes(4)


def audio(pcm):
    return struct.pack("<I", len(pcm) // 2) + pcm


def mark(ident, frame):
    return struct.pack("<III", 0x80000001, ident, frame)


def run(d, items):
    d._queue.put(items)
    d._queue.put(None)
    d._run()
    return _drain(d._audioQueue)


@contextlib.contextmanager
def runtime(module, host, tree, log):
    if Path(host).suffix.lower() != ".dll":
        with module.engine(host, tree, log) as proc:
            yield proc
        return
    from synthDrivers._panthera.dllhost import DllHost
    root = Path(tree)
    proc = DllHost("marker-test", host,
        str(root / "Speech/Synthesizers/MacinTalk.SpeechSynthesizer/Contents/MacOS/MacinTalk"),
        str(root / "SpeechDictionary.framework/Versions/A/SpeechDictionary"),
        str(root / "Speech/Voices"), None, [])
    try:
        yield proc
    finally:
        proc.kill()


def test_plan_keeps_heads_and_tails_out_of_engine_and_preserves_spacing():
    text, heads, interior, tails = marker_plan([
        ("index", 1), ("text", "One "), ("index", 2), ("break", 250),
        ("index", 3), ("text", "two."), ("index", 4)])
    assert text == "One [[sync 0x1]]two."
    assert heads == [("index", 1)] and tails == [("index", 4)]
    assert interior == {1: [("index", 2), ("break", 250), ("index", 3)]}


def test_command_split_across_fragments_cannot_smuggle_an_owned_sync():
    text, heads, interior, tails = marker_plan([
        ("text", "One [[sync "), ("index", 7), ("text", "0x1]] two.")])
    assert text.count("[[sync") == 1 and text == "One [[sync 0x1]] two."
    assert interior == {1: [("index", 7)]}
    assert not heads and not tails


def test_tgr5_splices_pause_without_losing_any_pcm_and_marks_play_in_order(monkeypatch):
    first, last = b"\x12\x34" * 6000, b"\x56\x78" * 3000
    proc = fake(response(audio(first), mark(1, 6000), audio(last)))
    # Real host chunks have at most 1024 frames.
    proc.stdout = io.BytesIO(response(
        *(audio(first[i:i+2048]) for i in range(0, len(first), 2048)),
        mark(1, 6000), *(audio(last[i:i+2048]) for i in range(0, len(last), 2048))))
    d = configured(proc)
    observed = _Reports(monkeypatch)
    items = run(d, [("index", 6), ("text", "One "), ("index", 7),
                    ("break", 250), ("text", "two."), ("index", 8)])
    values = b"".join(v for k, v, _ in items if k == "audio")
    gap = _silence(250)
    assert values[:len(first)] == first
    assert values[len(first):len(first)+len(gap)] == gap
    assert values[len(first)+len(gap):len(first)+len(gap)+len(last)] == last
    wire = proc.stdin.getvalue()
    assert struct.unpack_from("<I", wire)[0] == 0x54475235
    assert b"One [[sync 0x1]]two." in wire
    assert b"slnc" not in wire
    t0 = _feed(d, items)
    assert [i for _, i in observed.seen] == [6, 7, 8]
    assert observed.seen[1][0] - t0 >= 6000 / 22050
    # Callbacks can be delivered late on a later feed. Compare each against
    # its audio position, not against the delivery time of the previous one.
    assert observed.seen[2][0] - t0 >= .25 + 9000 / 22050


@pytest.mark.parametrize("record", [mark(1, 1), mark(2, 0), mark(1, 0) + mark(1, 0), b""])
def test_bad_or_missing_marker_disables_only_marker_protocol(record):
    d = configured(fake(response(record)))
    assert d._render("One [[sync 0x1]]two.", 180, "Alex", sink=lambda c: True,
                     markerSink=lambda *a: True, markerIds=(1,)) is None
    assert not d._markerStreaming and d._streaming


def test_old_host_refusing_tgr5_does_not_disable_tgr4():
    d = configured(fake(b""))
    assert d._render("One [[sync 0x1]]two.", 180, "Alex", sink=lambda c: True,
                     markerSink=lambda *a: True, markerIds=(1,)) is None
    assert not d._markerStreaming and d._streaming


def test_ordinary_text_cannot_enable_sync_commands():
    p = fake(response(audio(bytes(2))))
    d = configured(p)
    assert d._render("One [[sync 0x1]] two.", 180, "Alex", sink=lambda c: True) == b""
    assert b"sync" not in p.stdin.getvalue()


def test_marker_crossing_a_lexical_rewrite_falls_back_before_speaking():
    p = fake(response())
    d = configured(p)
    assert d._render("Meet Dr. [[sync 0x1]]Smith today.", 180, "Alex",
        sink=lambda c: True, markerSink=lambda *a: True, markerIds=(1,)) is None
    assert not p.stdin.getvalue()
    assert d._markerStreaming, "one lexical boundary must not disable other markers"


@pytest.mark.parametrize("style,expand,stress", [
    ("off", True, False), ("fix", True, True), ("words", True, True),
    ("words", False, True)])
def test_settings_and_number_rewrites_preserve_owned_sync_only(style, expand, stress):
    p = fake(response(mark(1, 0), audio(bytes(2))))
    d = configured(p)
    d._numberStyle, d._expandAbbreviations, d._fixStress = style, expand, stress
    text, _, ids, _ = marker_plan([("text", "Meet Dr. Smith at 1234567mm. "),
        ("index", 7), ("text", "Read the colon label [[rate 1]] now.")])
    assert d._render(text, 180, "Alex", sink=lambda c: True,
        markerSink=lambda *a: True, markerIds=tuple(ids)) == b""
    wire = p.stdin.getvalue()
    assert b"[[sync 0x1]]" in wire and b"[[rate 1]]" not in wire


def test_refusal_before_audio_falls_back_without_duplicate_head_index():
    d = _bare(False)
    d._markerStreaming = True
    legacy, calls = d._render, []

    def render(*args, **kwargs):
        calls.append(args[0])
        if "markerSink" in kwargs:
            d._markerStreaming = False
            return None
        return legacy(*args, **kwargs)
    d._render = render
    items = run(d, [("index", 6), ("text", "One "), ("index", 7),
                    ("break", 250), ("text", "two."), ("index", 8)])
    assert [v for k, v, _ in items if k == "mark"] == [6, 7, 8]
    assert calls == ["One [[sync 0x1]]two.", "One ", "two."]


def test_cancel_at_marker_discards_its_pause_and_tail():
    d = _bare(False)
    d._markerStreaming = True

    def render(text, wpm, voice, pitch, sink, markerSink, markerIds, **kwargs):
        assert sink(bytes(20))
        d._epoch += 1
        assert not markerSink(1, 10)
        assert not sink(bytes(20))
        return b""
    d._render = render
    items = run(d, [("text", "One "), ("index", 7), ("break", 250),
                    ("text", "two."), ("index", 8)])
    assert [(k, len(v)) for k, v, _ in items if k == "audio"] == [("audio", 20)]
    assert not any(k == "mark" for k, _, _ in items)


def test_cancelled_stream_may_end_before_remaining_markers_without_killing_host():
    proc = fake(response(audio(bytes(20))))
    d = configured(proc)

    def sink(chunk):
        # Cancel can land after the last sink call, with no further audio or
        # marker callback to tell the parser it stopped feeding.
        d._epoch += 1
        return True
    assert d._render("One [[sync 0x1]]two.", 180, "Alex", sink=sink,
        markerSink=lambda *a: True, markerIds=(1,)) == b""
    assert d._proc is proc and d._markerStreaming


@pytest.mark.parametrize("text", ["This is the first part of the sentence, ",
    "Run  dialog  Type the name of a program, folder, document, or Internet resource, "])
def test_real_host_keeps_reference_pcm_through_driver(text, tmp_path):
    host, tree = os.environ.get("PANTHERA_MARKER_HOST"), os.environ.get("PANTHERA_MARKER_TREE")
    if not host or not tree:
        pytest.skip("set PANTHERA_MARKER_HOST and PANTHERA_MARKER_TREE for native integration")
    import importlib.util
    path = Path(__file__).resolve().parents[2] / "tools/check_markers.py"
    spec = importlib.util.spec_from_file_location("marker_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rest = ("and Windows will open it for you.\nThis task will be created with administrative privileges."
            if text.startswith("Run") else "and this is the second part.")
    with runtime(module, host, tree, tmp_path / "host.log") as proc:
        d = configured(proc)
        d._wpm = lambda value=0: 387
        # Same preprocessing and voice level, ordinary blocking request.
        reference = d._render(text + rest, 387, "Alex")
        assert reference
        items = run(d, [("text", text), ("index", 7), ("break", 250),
                        ("text", rest), ("index", 8)])
        assert d._markerStreaming, "new path fell back rather than passing"
        assert [v for k, v, _ in items if k == "mark"] == [7, 8]
        # Tail index is before the existing inter-announcement AND Say All
        # sentence gaps. Compare speech up to that boundary, not those pauses.
        tail = next(i for i, (k, v, _) in enumerate(items) if k == "mark" and v == 8)
        chunks = [v for k, v, _ in items[:tail] if k == "audio"]
        gap = _silence(250)
        assert chunks.count(gap) == 1
        chunks.remove(gap)
        assert b"".join(chunks) == reference
