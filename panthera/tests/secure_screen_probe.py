# -*- coding: utf-8 -*-
"""Speak one utterance from an add-on that has no executable in it.

Run by `test_secure_screen_host.py` in a **32-bit** interpreter, against a
staged copy of the add-on with `panthera_host.exe` removed -- which is what
NVDA's `systemConfig` copy is, because `config._setSystemConfig` drops every
file ending `.exe`.

Not named `test_*` on purpose: it is the thing under test, not a test, and
pytest collecting it in the 64-bit suite would only fail on the bitness.

It drives the real `SynthDriver`, not `DllHost` directly.  The question worth
asking is not whether the library loads -- `tools/dll_smoke.py` settles that --
but whether the driver reaches for it when there is no executable, and whether
everything downstream of that (the readiness flag, the log thread that has no
stream of its own, the standby that must not be started) still holds together.
"""
import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv):
    staged, module, envName, tree = argv[1:5]
    os.environ[envName] = tree

    sys.path.insert(0, HERE)
    import conftest                                   # noqa: F401
    # The fakes are installed when conftest is imported; repoint the package
    # at the staged copy so the driver loads from the folder without the exe.
    sys.modules["synthDrivers"].__path__ = [staged]

    mod = importlib.import_module("synthDrivers." + module)
    if os.path.isfile(mod.HOST_EXE):
        print("STAGING FAILED: the executable is still there")
        return 2
    if not os.path.isfile(mod.HOST_DLL):
        print("STAGING FAILED: no library was staged")
        return 2

    driver = mod.SynthDriver()
    try:
        if not driver._useLibrary():
            print("the driver did not reach for the library")
            return 1
        voice = driver._voices[0][0]
        driver._set_voice(voice)
        pcm = driver._render("Panthera speaks with no executable.",
                             driver._wpm(), voice)
        print("FRAMES %d" % (len(pcm) // 2 if pcm else 0))
        # A standby must not exist: one process holds one engine, and there is
        # no way to unmap it.  Tiger's driver never grew standby machinery at
        # all, which is the same answer arrived at earlier.
        if hasattr(driver, "_ensureStandby"):
            driver._ensureStandby()
            print("STANDBY %s" % ("none" if driver._standby is None
                                  else "started, which it must not be"))
        else:
            print("STANDBY none")
        return 0
    finally:
        driver.terminate()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
