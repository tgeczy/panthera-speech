# -*- coding: utf-8 -*-
"""Enough of NVDA to import and drive the synthesizer outside NVDA.

Lifted, deliberately, from the sibling ROM add-on.  The driver is the part of
this project most likely to be wrong in ways the engine cannot be blamed for,
and every rule at the top of `tigerspeech.py` was paid for there.  The fakes
model the behaviour those bugs turned on, and nothing else:

* `WavePlayer.idle()` blocks until the audio drains, and `stop()` cuts it
  short.  A non-interruptible `idle()` reports huge latency for a driver that
  is already correct.
* `stop()` costs `STREAM_START` on the next `feed()`, because tearing the
  output stream down and starting it again is what made short utterances lag
  while whole sentences were fine.
* `synthDoneSpeaking` can be waited on, because NVDA paces typed characters on
  it -- a test that does not wait is not testing what a user experiences.

Tests needing the real engine skip when no Tiger tree can be found; it is
never in the repository.
"""
import os
import sys
import threading
import time
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = os.path.join(ROOT, "addon", "synthDrivers")

#: What a real output stream costs to start after being stopped.
STREAM_START = 0.12
OUT_RATE = 22050.0


class FakeWavePlayer(object):
    def __init__(self, *a, **k):
        self.fed = 0
        self.bytes = 0
        self.stops = 0
        self.idles = 0
        self.startups = 0
        self._lock = threading.Lock()
        self._until = 0.0
        self._running = False

    def feed(self, data):
        with self._lock:
            self.fed += 1
            self.bytes += len(data)
            now = time.perf_counter()
            if not self._running:
                self._running = True
                self.startups += 1
                now += STREAM_START
            self._until = max(self._until, now) + len(data) / 2.0 / OUT_RATE

    def stop(self):
        with self._lock:
            self.stops += 1
            self._until = 0.0
            self._running = False

    def idle(self):
        self.idles += 1
        while True:
            with self._lock:
                left = self._until - time.perf_counter()
            if left <= 0:
                with self._lock:
                    self._running = False
                return
            time.sleep(min(left, 0.005))

    def pause(self, switch):
        pass

    def close(self):
        self.stop()


def _stage_tree(cfg_dir):
    """Point the fake config dir at a real Tiger tree, if one exists.

    A pointer file rather than a copy -- the tree is hundreds of megabytes --
    which has the happy side effect of exercising the real lookup path instead
    of bypassing it.
    """
    sys.path.insert(0, ADDON)
    # No guesses.  Whoever runs the tests says where their tree is, exactly as
    # a user does -- and guessing would put somebody's disk layout in the
    # repository.
    env = os.environ.get("TIGER_TREE")
    for c in ([env] if env else []):
        if c and os.path.isdir(os.path.join(c, "Speech", "Voices")):
            os.makedirs(cfg_dir, exist_ok=True)
            with open(os.path.join(cfg_dir, "tigerspeech-data.txt"), "w",
                      encoding="utf-8") as f:
                f.write(c)
            return c
    return None


def _install_fake_nvda():
    if "synthDriverHandler" in sys.modules:
        return

    nvwave = types.ModuleType("nvwave")
    nvwave.WavePlayer = FakeWavePlayer
    nvwave.AudioPurpose = type("AudioPurpose", (), {"SPEECH": 1})
    sys.modules["nvwave"] = nvwave

    logh = types.ModuleType("logHandler")
    class _Log(object):
        def __init__(self): self.messages = []
        def _rec(self, level, msg, *a): self.messages.append((level, msg % a if a else msg))
        def info(self, m, *a, **k): self._rec("info", m, *a)
        def debug(self, m, *a, **k): self._rec("debug", m, *a)
        def debugWarning(self, m, *a, **k): self._rec("debug", m, *a)
        def warning(self, m, *a, **k): self._rec("warning", m, *a)
        def error(self, m, *a, **k): self._rec("error", m, *a)
        #: Real NVDA loggers have these; the driver asks before building a
        #: debug string, and a fake without them fails only in the tests.
        DEBUG = 10
        def isEnabledFor(self, level): return False
    logh.log = _Log()
    sys.modules["logHandler"] = logh

    cfg = types.ModuleType("config")
    cfg.conf = {"audio": {"outputDevice": "default"},
                "speech": {"outputDevice": "default"}}
    sys.modules["config"] = cfg

    cfg_dir = os.path.join(ROOT, "build", "test-config")
    _stage_tree(cfg_dir)
    gv = types.ModuleType("globalVars")
    gv.appArgs = type("_A", (), {"configPath": cfg_dir, "secure": False})()
    sys.modules["globalVars"] = gv

    speech = types.ModuleType("speech")
    commands = types.ModuleType("speech.commands")
    class IndexCommand(object):
        def __init__(self, index): self.index = index
    class BreakCommand(object):
        def __init__(self, time=0): self.time = time
    class PitchCommand(object):
        def __init__(self, offset=0, multiplier=1):
            self.offset = offset
            self.isDefault = offset == 0 and multiplier == 1
    commands.PitchCommand = PitchCommand
    commands.IndexCommand = IndexCommand
    commands.BreakCommand = BreakCommand
    speech.commands = commands
    sys.modules["speech"] = speech
    sys.modules["speech.commands"] = commands

    sdh = types.ModuleType("synthDriverHandler")
    class _Setting(object):
        def __init__(self, *a, **k): pass
    class VoiceInfo(object):
        def __init__(self, id, name, language=None):
            self.id, self.name, self.language = id, name, language
    class SynthDriver(object):
        VoiceSetting = RateSetting = PitchSetting = VolumeSetting = _Setting
        InflectionSetting = _Setting
        def __init__(self): pass
    class _Notifier(object):
        """Counts, and lets a test wait for the next notification.

        NVDA paces speech on synthDoneSpeaking when a sequence ends with
        EndUtteranceCommand, which typed characters always do.
        """
        def __init__(self):
            self.count = 0
            self._event = threading.Event()
        def notify(self, **k):
            self.count += 1
            self._event.set()
        def wait(self, timeout=5.0):
            return self._event.wait(timeout)
        def arm(self):
            self._event.clear()
    sdh.SynthDriver = SynthDriver
    sdh.VoiceInfo = VoiceInfo
    sdh.synthDoneSpeaking = _Notifier()
    sdh.synthIndexReached = _Notifier()
    sys.modules["synthDriverHandler"] = sdh

    asu = types.ModuleType("autoSettingsUtils")
    ds = types.ModuleType("autoSettingsUtils.driverSetting")
    ds.DriverSetting = ds.BooleanDriverSetting = ds.NumericDriverSetting = _Setting
    asu.driverSetting = ds
    sys.modules["autoSettingsUtils"] = asu
    sys.modules["autoSettingsUtils.driverSetting"] = ds

    #: How NVDA describes each choice in a list setting.
    class _StringParameterInfo(object):
        def __init__(self, id, displayName):
            self.id = id
            self.displayName = displayName

    utils = types.ModuleType("autoSettingsUtils.utils")
    utils.StringParameterInfo = _StringParameterInfo
    asu.utils = utils
    sys.modules["autoSettingsUtils.utils"] = utils

    import builtins
    if not hasattr(builtins, "_"):
        builtins._ = lambda s: s


_install_fake_nvda()


@pytest.fixture(scope="session")
def tiger_tree():
    import tigerspeech
    tree = tigerspeech.find_tree()
    if not tree:
        pytest.skip("no Tiger speech tree; set TIGER_TREE")
    if not os.path.isfile(tigerspeech.HOST_EXE):
        pytest.skip("tiger_host.exe not built; run sh build.sh")
    return tree


@pytest.fixture
def driver(tiger_tree):
    import tigerspeech
    d = tigerspeech.SynthDriver()
    yield d
    d.terminate()
