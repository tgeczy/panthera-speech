# -*- coding: utf-8 -*-
"""Speak the way a secure screen has to: no executable, and no help.

Run by `test_secure_screen_host.py` in a **32-bit** interpreter against a
staged copy of the add-on laid out the way NVDA lays one out, with
`panthera_host.exe` removed -- which is what NVDA's `systemConfig` is, because
`config._setSystemConfig` drops every file ending `.exe`.

Not named `test_*` on purpose: it is the thing under test, not a test, and
pytest collecting it in the 64-bit suite would only fail on the bitness.

**Two things it must not be given, because the bridge does not give them.**

`appArgs.configPath` is set to `"."`, because that is literally what
`_bridge/runtimes/synthDriverHost/globalVars.py` sets it to -- a stub, under a
comment reading "very basic values to allow things to run".

And no `TIGER_TREE` / `LEOPARD_TREE` / … in the environment.  `find_tree`
consults those *first*, before anything to do with the configuration
directory, and an earlier version of this probe set them -- so it passed on
all four generations, proving only that an environment variable works, while
the real sign-in screen listed all four synthesizers and loaded none of them.
The tree is reached here the way a user's is: a pointer file in the
configuration directory the add-on is installed under.
"""
import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

#: What each generation's pointer file is called, beside the config folder.
POINTERS = {
    "tigerspeech": "tigerspeech-data.txt",
    "leopardspeech": "leopardspeech-data.txt",
    "snowleopardspeech": "snowleopardspeech-data.txt",
    "lionspeech": "lionspeech-data.txt",
}


def main(argv):
    staged, module, _envName, tree = argv[1:5]

    # Nothing up our sleeve: `find_tree` looks at these before it looks at the
    # configuration directory, and the point of this probe is the latter.
    for name in ("TIGER_TREE", "LEOPARD_TREE", "SNOWLEOPARD_TREE",
                 "LION_TREE"):
        os.environ.pop(name, None)

    sys.path.insert(0, HERE)
    import conftest                                   # noqa: F401
    # The fakes are installed when conftest is imported; repoint the package
    # at the staged copy so the driver loads from the folder without the exe.
    sys.modules["synthDrivers"].__path__ = [staged]

    # Be the bridge host, not merely a 32-bit process.  Standing in for the
    # bridge while quietly holding a correct configPath is standing in for the
    # easy half.
    import globalVars
    globalVars.appArgs.configPath = "."

    mod = importlib.import_module("synthDrivers." + module)
    if os.path.isfile(mod.HOST_EXE):
        print("STAGING FAILED: the executable is still there")
        return 2
    if not os.path.isfile(mod.HOST_DLL):
        print("STAGING FAILED: no library was staged")
        return 2

    from synthDrivers._panthera import pantheratrees
    found = pantheratrees.config_base()
    print("CONFIGBASE %s" % found)
    if not os.path.isabs(found):
        print("the add-on believed the bridge's placeholder configuration "
              "path, so it will look for its engine in the wrong place")
        return 1

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
