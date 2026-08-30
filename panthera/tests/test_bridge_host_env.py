# -*- coding: utf-8 -*-
"""Load the driver against NVDA's real 32-bit host library, not our fakes.

**The suite's fakes are kinder than the bridge host is, and that gap cost a
trip to a sign-in screen.**  `secure_screen_probe.py` stands in for the host
using `conftest`'s stubs, and those stubs give `SynthDriver` an `__init__` that
does nothing -- so the probe passed on all four generations while the real host
raised before any of our code ran:

    File "autoSettingsUtils/autoSettings.pyc", in _registerConfigSaveAction
    AttributeError: module 'config' has no attribute 'pre_configSave'

NVDA's own `driverHandler` reaches that through `SynthDriver.__init__`, and the
host's `config` is a stub whose entire contents is a `conf` dict.  No fake of
ours would have found it, because a fake written by the same person who wrote
the driver agrees with the driver.

So this test uses the host's **actual** frozen library -- `library.zip` from
`synthDriverHost-runtime`, the real `driverHandler`, the real `autoSettings`,
and the real stub `config` -- arranged the way `main.pyw` and
`installProxies` arrange them.  It is the closest thing to the sign-in screen
that can be run from a desk.

Skipped when NVDA is not installed, since it is NVDA's files it needs.
"""
import glob
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ADDON = os.path.join(ROOT, "addon", "synthDrivers")

GENERATIONS = ["tigerspeech", "leopardspeech", "snowleopardspeech",
               "lionspeech"]


def _runtime():
    """-> (runtime dir, NVDA app dir), or (None, None).

    The newest installed NVDA that carries a 32-bit synth-driver host.
    """
    for base in (r"C:\Program Files\NVDA", r"C:\Program Files (x86)\NVDA"):
        found = sorted(glob.glob(os.path.join(
            base, "lib", "*", "x86", "synthDriverHost-runtime")))
        for path in reversed(found):
            if os.path.isfile(os.path.join(path, "library.zip")):
                return path, base
    return None, None


#: Run inside the host's own library, so that every NVDA module involved is
#: the one the host would use.
_SCRIPT = r'''
import os, sys, gettext
runtime, appDir, staged, module = sys.argv[1:5]
sys.path.insert(0, os.path.join(runtime, "library.zip"))
sys.path.insert(1, runtime)
gettext.install("nvda", names=["pgettext", "npgettext", "ngettext"])

# Exactly what `HostService.installProxies` does, in the same order.  It
# asks NVDA over RPYC for these; here they are handed in.  Without them
# nvwave cannot find nvdaHelperLocal.dll, which is a fault of the harness
# rather than of the add-on.
import NVDAState
NVDAState.isRunningAsSource = lambda: False
_versionedLib = os.path.dirname(os.path.dirname(runtime))


class _ReadPaths(NVDAState._ReadPaths):
    @property
    def versionedLibPath(self):
        return _versionedLib


NVDAState.ReadPaths = _ReadPaths()

import globalVars
globalVars.appDir = appDir
# It notably does NOT repair configPath, which is the whole reason
# `config_base` has to derive one of its own.
assert globalVars.appArgs.configPath == ".", (
    "the host no longer reports configPath as '.'; check config_base")

import config
for section, keys in (("audio", ["outputDevice", "audioAwakeTime",
                                 "whiteNoiseVolume"]),
                      ("speech", ["useWASAPIForSAPI4", "trimLeadingSilence"]),
                      ("debugLog", ["synthDriver"])):
    config.conf[section] = {}
    for key in keys:
        config.conf[section][key] = (
            "default" if key == "outputDevice" else False)

import nvwave
nvwave.initialize()

import synthDrivers
synthDrivers.__path__.insert(0, staged)
import importlib
mod = importlib.import_module("synthDrivers." + module)
driver = mod.SynthDriver()
print("CONSTRUCTED")
try:
    # **Exactly the calls `_bridge/components/services/synthDriver.py` makes,
    # by the names it uses.**  Overriding `_get_availableVoices` and not
    # `_getAvailableVoices` reads identically in NVDA, which goes through the
    # caching property, and raises NotImplementedError through the bridge,
    # which calls the underscore method straight.  That cost a whole trip to a
    # sign-in screen after everything else was already right.
    voices = driver._getAvailableVoices()
    print("VOICES %d" % len(voices))
    for setting in driver.supportedSettings:
        getattr(driver, setting.id)
    print("SETTINGS READ")
finally:
    driver.terminate()
'''


@pytest.mark.parametrize("module", GENERATIONS)
def test_the_driver_survives_the_real_host_library(tmp_path, module):
    runtime, appDir = _runtime()
    if runtime is None:
        pytest.skip("no installed NVDA with a 32-bit synth-driver host")
    python32 = _python32()
    if python32 is None:
        pytest.skip("no 32-bit Python; install one to test the bridge host")

    from test_secure_screen_host import _tree, POINTERS
    folder = dict(zip(GENERATIONS,
                      ("tiger", "leopard", "snowleopard", "Lion")))[module]
    tree = _tree(folder, folder.upper() + "_TREE")
    if tree is None:
        pytest.skip("no %s tree" % folder)

    import shutil
    config = tmp_path / "config"
    staged = str(config / "addons" / "pantheraspeech" / "synthDrivers")
    shutil.copytree(ADDON, staged,
                    ignore=shutil.ignore_patterns("__pycache__", "*.exe"))
    (config / POINTERS[module]).write_text(tree, encoding="utf-8")

    script = str(tmp_path / "inhost.py")
    with open(script, "w", encoding="utf-8") as f:
        f.write(_SCRIPT)

    env = dict(os.environ)
    for name in ("TIGER_TREE", "LEOPARD_TREE", "SNOWLEOPARD_TREE",
                 "LION_TREE"):
        env.pop(name, None)
    out = subprocess.run(
        python32 + ["-u", script, runtime, appDir, staged, module],
        capture_output=True, timeout=600, env=env, cwd=str(tmp_path))
    said = (out.stdout or b"").decode("utf-8", "replace")
    why = (out.stderr or b"").decode("utf-8", "replace")
    assert "CONSTRUCTED" in said, (
        "the driver would not start inside NVDA's own 32-bit host "
        "library:\n%s\n%s" % (said, why))
    voices = [ln for ln in said.splitlines() if ln.startswith("VOICES ")]
    assert voices and int(voices[0].split()[1]) > 0, (
        "the bridge asks for the voice list by a name this driver does not "
        "answer to:\n%s\n%s" % (said, why))
    assert "SETTINGS READ" in said, (
        "a setting could not be read the way the bridge reads it:\n%s\n%s"
        % (said, why))


def _python32():
    from test_secure_screen_host import _python32 as f
    return f()
