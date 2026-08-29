# -*- coding: utf-8 -*-
"""Enough of NVDA to import and drive the synthesizers outside NVDA.

The fakes are installed once, here, for both generations.  They used to be a
copy each, and the copies had drifted: only Leopard's fake `DriverSetting`
recorded its arguments, which is the whole of what `test_settings_panel.py`
looks at.  In one directory the first conftest to load wins -- everything
after it finds `synthDriverHandler` already in `sys.modules` and accepts it --
so the drift would have surfaced as Leopard's panel tests failing whenever
Tiger happened to load first, for a reason nothing in them mentions.

The engine fixtures stay per-generation, in `tiger/` and `leopard/`, because
pytest scopes a conftest to its own directory.  Both are called `driver` and
neither can see the other.

Lifted, deliberately, from the sibling ROM add-on.  The driver is the part of
this project most likely to be wrong in ways the engine cannot be blamed for,
and every rule at the top of the drivers was paid for there.  The fakes
model the behaviour those bugs turned on, and nothing else:

* `WavePlayer.idle()` blocks until the audio drains, and `stop()` cuts it
  short.  A non-interruptible `idle()` reports huge latency for a driver that
  is already correct.
* `stop()` costs `STREAM_START` on the next `feed()`, because tearing the
  output stream down and starting it again is what made short utterances lag
  while whole sentences were fine.
* `synthDoneSpeaking` can be waited on, because NVDA paces typed characters on
  it -- a test that does not wait is not testing what a user experiences.

Tests needing a real engine skip when its tree cannot be found; neither is
ever in the repository.
"""
import os
import sys
import threading
import time
import types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON = os.path.join(ROOT, "addon", "synthDrivers")
#: Where the shared driver body and the tree modules live.
PRIVATE = os.path.join(ADDON, "_panthera")

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


#: Every generation, because one add-on now holds them all.  The pointer files are
#: named apart, so a config folder can carry both at once -- which is exactly
#: what a user with both engines extracted has.
TREES = (("TIGER_TREE", "tigerspeech-data.txt"),
         ("LEOPARD_TREE", "leopardspeech-data.txt"),
         ("SNOWLEOPARD_TREE", "snowleopardspeech-data.txt"),
         ("LION_TREE", "lionspeech-data.txt"))


def _stage_tree(cfg_dir):
    """Point the fake config dir at whichever real trees exist.

    A pointer file rather than a copy -- the trees are hundreds of megabytes
    each -- which has the happy side effect of exercising the real lookup path
    instead of bypassing it.
    """
    # `synthDrivers` as a real package rooted at the add-on's own folder,
    # which is exactly what NVDA builds:
    # `addonHandler.Addon.addToPackagePath` inserts every add-on's
    # `synthDrivers` directory into the real package's `__path__`.
    #
    # Registering it the same way here means the tests exercise the import
    # path NVDA actually uses -- `from ._panthera import ...` inside a driver,
    # `synthDrivers._panthera.*` from the global plugin.  A flattened stand-in
    # on `sys.path` would let a broken relative import pass the suite and fail
    # only once it was loaded by NVDA, which is the worst place to find it.
    if "synthDrivers" not in sys.modules:
        pkg = types.ModuleType("synthDrivers")
        pkg.__path__ = [ADDON]
        sys.modules["synthDrivers"] = pkg
    for env_name, pointer in TREES:
        # No guesses.  Whoever runs the tests says where their tree is,
        # exactly as a user does -- and guessing would put somebody's disk
        # layout in the repository.
        c = os.environ.get(env_name)
        if c and os.path.isdir(os.path.join(c, "Speech", "Voices")):
            os.makedirs(cfg_dir, exist_ok=True)
            with open(os.path.join(cfg_dir, pointer), "w",
                      encoding="utf-8") as f:
                f.write(c)


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
    class RateCommand(object):
        """The same shape as the pitch one, because NVDA's really is.

        Both derive from `BaseProsodyCommand`, and `offset` is the amount to
        add to the user's own setting on NVDA's 0-100 scale, 0 meaning "back
        to what the user chose".  An add-on wraps the text it wants spoken
        differently in a pair of them.
        """
        def __init__(self, offset=0, multiplier=1):
            self.offset = offset
            self.isDefault = offset == 0 and multiplier == 1
    class VolumeCommand(object):
        """A *sibling* of the rate command, not a subclass of it.

        In NVDA both derive from `BaseProsodyCommand` and neither is the
        other.  Making one a subclass here would have made `speak()`'s
        `isinstance` chain pass by accident -- and pass in an order the real
        classes cannot guarantee.
        """
        def __init__(self, offset=0, multiplier=1):
            self.offset = offset
            self.isDefault = offset == 0 and multiplier == 1
    commands.PitchCommand = PitchCommand
    commands.RateCommand = RateCommand
    commands.VolumeCommand = VolumeCommand
    commands.IndexCommand = IndexCommand
    commands.BreakCommand = BreakCommand
    speech.commands = commands
    sys.modules["speech"] = speech
    sys.modules["speech.commands"] = commands

    sdh = types.ModuleType("synthDriverHandler")
    class _Setting(object):
        """Records what it was given, where the real one would build a control.

        It used to discard its arguments, which meant the settings panel was
        the one part of the driver no test could look at -- including the two
        things that have actually gone wrong there: a default flipped by
        accident, and two labels claiming the same access key.
        """
        def __init__(self, *a, **k):
            self.id = a[0] if len(a) > 0 else k.get("id")
            self.displayName = a[1] if len(a) > 1 else k.get("displayName")
            self.defaultVal = k.get("defaultVal", a[2] if len(a) > 2 else None)
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
