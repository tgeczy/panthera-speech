# -*- coding: utf-8 -*-
"""Exercise the driver's logic without NVDA.

The parts worth testing are the ones that do not need a screen reader: finding
the Tiger tree, reading the voice list out of the user's own bundles, and the
request/response exchange with the engine.  Everything else is NVDA plumbing
that only a real NVDA can check.

    py -3 tools/test_driver.py
"""
import builtins
import os
import struct
import sys
import time
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def stub_nvda():
    """Minimal stand-ins so the driver module imports outside NVDA."""
    builtins._ = lambda s: s

    nvwave = types.ModuleType("nvwave")
    class WavePlayer:
        def __init__(self, *a, **k): pass
        def feed(self, data): pass
        def idle(self): pass
        def stop(self): pass
        def pause(self, s): pass
        def close(self): pass
    nvwave.WavePlayer = WavePlayer
    sys.modules["nvwave"] = nvwave

    commands = types.ModuleType("speech.commands")
    class IndexCommand:
        def __init__(self, index): self.index = index
    commands.IndexCommand = IndexCommand
    speech = types.ModuleType("speech")
    speech.commands = commands
    sys.modules["speech"] = speech
    sys.modules["speech.commands"] = commands

    lh = types.ModuleType("logHandler")
    class _Log:
        def debugWarning(self, *a): print("   log:", *a)
        def info(self, *a): pass
        def debug(self, *a): pass
    lh.log = _Log()
    sys.modules["logHandler"] = lh

    sdh = types.ModuleType("synthDriverHandler")
    class _Setting:
        def __init__(self, *a, **k): pass
    class SynthDriver:
        VoiceSetting = _Setting
        RateSetting = _Setting
        def __init__(self): pass
    class _Notify:
        def notify(self, **k): pass
    sdh.SynthDriver = SynthDriver
    sdh.VoiceInfo = lambda id, name, lang=None: (id, name, lang)
    sdh.synthDoneSpeaking = _Notify()
    sdh.synthIndexReached = _Notify()
    sys.modules["synthDriverHandler"] = sdh

    cfg = types.ModuleType("config")
    cfg.conf = {"audio": {"outputDevice": None}, "speech": {"outputDevice": None}}
    sys.modules["config"] = cfg


def main():
    stub_nvda()
    sys.path.insert(0, os.path.join(ROOT, "addon", "synthDrivers"))
    import tigerspeech as ts

    print("host exe      :", "found" if os.path.isfile(ts.HOST_EXE) else "MISSING")
    tree = ts.find_tree()
    print("tiger tree    :", tree or "NOT FOUND")
    if not tree:
        raise SystemExit("set TIGER_TREE or drop tiger-tree.txt in the config dir")
    mt, sd, voicesdir = ts.engine_paths(tree)
    for label, p in (("MacinTalk", mt), ("SpeechDictionary", sd)):
        print("%-14s: %s" % (label, "found" if os.path.isfile(p) else "MISSING"))

    voices = ts.read_voices(voicesdir)
    offered = {b for b, _d, _e in ts.read_voices(voicesdir, playable_only=True)}
    print("voices        : %d on disk, %d offered to NVDA" % (len(voices),
                                                              len(offered)))
    for bundle, display, engine in voices:
        print("   %-6s %-12s %s" % (engine, display,
                                    "" if bundle in offered
                                    else "(withheld: no decoder for this engine)"))

    print("check()       :", bool(ts.SynthDriver.check()))

    drv = ts.SynthDriver()
    print("default voice :", drv._get_voice())   # NVDA maps _get_voice -> .voice
    print("rate 50 ->", drv._wpm(), "wpm")

    print("\nrendering:")
    ok = True
    for bundle, display, engine in voices:
        note = "" if bundle in offered else "   (withheld from NVDA)"
        t = time.perf_counter()
        pcm = drv._render("The quick brown fox jumps over the lazy dog.",
                          180, bundle)
        dt = (time.perf_counter() - t) * 1000
        if pcm is None:
            print("   %-12s FAILED%s" % (display, note))
            ok = ok and bundle not in offered
            continue
        n = len(pcm) // 2
        v = struct.unpack("<%dh" % n, pcm) if n else ()
        peak = max((abs(x) for x in v), default=0)
        print("   %-12s %6.1f ms  %5.2f s  peak %5d %s%s"
              % (display, dt, n / float(ts.OUT_RATE), peak,
                 "" if peak else "  <- SILENT", note))
        if not peak and bundle in offered:
            ok = False

    drv.terminate()
    print("\nevery voice offered to NVDA produced audio:", ok)


if __name__ == "__main__":
    main()
