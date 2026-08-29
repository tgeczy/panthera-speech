# -*- coding: utf-8 -*-
"""The add-on with its executable taken away still speaks.

**This is the whole point of the DLL, so it is worth testing directly rather
than inferring.**  NVDA copies the user configuration to `systemConfig` for
secure screens and drops every file ending `.exe` on the way --
`config._setSystemConfig`, deliberately and at `log.debug`.  So on the sign-in
desktop, on a UAC prompt and on every other secure screen, the add-on folder
NVDA speaks from has no `panthera_host.exe` in it.  The test stages exactly
that: a copy of the add-on with the executable removed, and nothing else
changed.

**It runs in a 32-bit interpreter**, because Apple's MacinTalk is i386 code and
the process has to be able to call it.  `py -3.13-32` is a faithful stand-in
for NVDA's bridge host rather than a convenient one -- neither is
large-address-aware, so both see the same 2 GB.  The 64-bit suite around this
file cannot load the library at all, which is why this one test steps outside
it instead of pretending.
"""
import os
import shutil
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ADDON = os.path.join(ROOT, "addon", "synthDrivers")
PROBE = os.path.join(HERE, "secure_screen_probe.py")

#: One per generation: the driver module, the variable naming its tree, and
#: the folder that tree lives in under `macintalk`.
GENERATIONS = [
    ("tigerspeech", "TIGER_TREE", "tiger"),
    ("leopardspeech", "LEOPARD_TREE", "leopard"),
    ("snowleopardspeech", "SNOWLEOPARD_TREE", "snowleopard"),
    ("lionspeech", "LION_TREE", "Lion"),
]


def _python32():
    """-> a 32-bit interpreter, or None.

    `py -3.13-32` rather than `sys.executable`: the suite runs 64-bit, where
    the library cannot be loaded at all.
    """
    for args in (["py", "-3.13-32"], ["py", "-3-32"]):
        try:
            out = subprocess.run(
                args + ["-c", "import ctypes,sys;"
                              "sys.exit(0 if ctypes.sizeof(ctypes.c_void_p)"
                              " == 4 else 1)"],
                capture_output=True, timeout=60)
        except Exception:
            continue
        if out.returncode == 0:
            return args
    return None


def _tree(folder, envName):
    """Where this generation's engine actually is, or None."""
    fromEnv = os.environ.get(envName)
    if fromEnv and os.path.isdir(os.path.join(fromEnv, "Speech", "Voices")):
        return fromEnv
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    guess = os.path.join(appdata, "nvda", "macintalk", folder)
    return guess if os.path.isdir(os.path.join(guess, "Speech",
                                               "Voices")) else None


@pytest.mark.parametrize("module,envName,folder", GENERATIONS)
def test_it_speaks_with_the_executable_removed(tmp_path, module, envName,
                                               folder):
    if not os.path.isfile(os.path.join(ADDON, "_panthera",
                                       "panthera_host.dll")):
        pytest.skip("panthera_host.dll not built; run sh build.sh")
    python32 = _python32()
    if python32 is None:
        pytest.skip("no 32-bit Python; install one to test secure screens")
    tree = _tree(folder, envName)
    if tree is None:
        pytest.skip("no %s tree; set %s" % (folder, envName))

    # The staging NVDA does, minus the one thing it does differently.
    staged = str(tmp_path / "synthDrivers")
    shutil.copytree(ADDON, staged,
                    ignore=shutil.ignore_patterns("__pycache__", "*.exe"))
    assert not os.path.isfile(os.path.join(staged, "_panthera",
                                           "panthera_host.exe"))

    # A configuration folder of its own.  The probe imports this suite's
    # conftest to get the NVDA fakes, and conftest writes a tree pointer for
    # whatever `envName` says -- into the suite's own folder, if it is let.
    # That is not hypothetical: it repointed the Lion tree at one with no
    # Compact voices in it and failed an unrelated test three directories away.
    env = dict(os.environ)
    env["PANTHERA_TEST_CONFIG"] = str(tmp_path / "config")

    out = subprocess.run(python32 + [PROBE, staged, module, envName, tree],
                         capture_output=True, timeout=600, env=env)
    said = (out.stdout or b"").decode("utf-8", "replace")
    why = (out.stderr or b"").decode("utf-8", "replace")
    assert out.returncode == 0, "the probe failed:\n%s\n%s" % (said, why)

    frames = [ln for ln in said.splitlines() if ln.startswith("FRAMES ")]
    assert frames, "the probe rendered nothing:\n%s\n%s" % (said, why)
    # A tenth of a second is far below any real utterance and far above the
    # empty render a broken channel produces.
    assert int(frames[0].split()[1]) > 2205, (
        "rendered almost nothing: %s" % frames[0])

    standby = [ln for ln in said.splitlines() if ln.startswith("STANDBY ")]
    assert standby and standby[0] == "STANDBY none", (
        "a spare engine was started in a process that can only hold one: %s"
        % (standby or said))
