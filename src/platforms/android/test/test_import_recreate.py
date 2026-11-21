"""The settings screen rebuilt while it checks a zip to import, on the attached
phone (panthera-speech#38, and #40's second half).

An import shows "Checking zip" while a worker reads the zip (ZipImport.inspect:
seconds, or minutes for Alex from a cloud source).  A configuration change in
that time -- the screen turned, a keyboard, dark mode, font size -- rebuilds
the screen; the worker then dismissed the destroyed screen's dialog, whose
window was gone: IllegalArgumentException, and the app's process died, the TTS
service with it.  And the rebuilt screen read its intent's import again, so the
same zip was checked -- and, had it held an engine, imported -- twice.

The case: a zip of many small entries and no engine (so nothing is ever
imported) pushed to the app's own external files folder, the check started
through the adb route (`am start ... --es import <path>`), dark mode switched
0.1 s in.  Then the app's process must be the one it was, the log must show no
crash, and the rebuilt screen must report the check's outcome exactly once.
Dark mode is put back as it was.

    python test_import_recreate.py [--tries 3]     (ANDROID_SERIAL picks the device)

Works with a debug or a release build.  Adapted from ssi263-speech's test for
the same class (e3d43be).
"""
import argparse
import os
import random
import subprocess
import sys
import tempfile
import time
import zipfile

PKG = "com.pantheraspeech.tts"
ACT = PKG + "/.SettingsActivity"
ADB = os.environ.get("ADB") or "adb"
DEVICE_DIR = "/sdcard/Android/data/%s/files" % PKG


def adb(*args, check=True):
    env = dict(os.environ, MSYS_NO_PATHCONV="1")
    r = subprocess.run([ADB] + list(args), capture_output=True, text=True, errors="replace", env=env)
    if check and r.returncode:
        raise RuntimeError("adb %s: %s" % (" ".join(args), r.stderr.strip()))
    return r.stdout


def pid():
    return adb("shell", "pidof", PKG, check=False).strip()


MACINTALK = "Speech/Synthesizers/MacinTalk.SpeechSynthesizer/Contents/MacOS/MacinTalk"


def big_zip(path, mb=60):
    """A zip the check must actually read, and refuses.  From a file the check
    reads only the central directory and the entries it wants, so a zip of
    small files is checked in milliseconds -- over before any rebuild.  So:
    under each of four generation folders, an "engine" binary of random bytes
    just under the 64 MB the check will read, with no version in it.  The
    check reads all four, cannot tell which generation any of them is, and
    refuses: nothing is ever imported."""
    rng = random.Random(38)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as z:
        for gen in ("tiger", "leopard", "snowleopard", "lion"):
            z.writestr(gen + "/" + MACINTALK, rng.randbytes(mb << 20))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tries", type=int, default=3)
    a = ap.parse_args()
    tmp = tempfile.mkdtemp(prefix="panthera_recreate_")
    local = os.path.join(tmp, "no-engine.zip")
    big_zip(local)
    adb("shell", "mkdir", "-p", DEVICE_DIR)
    remote = DEVICE_DIR + "/no-engine.zip"
    adb("push", local, remote)
    night = adb("shell", "cmd", "uimode", "night").strip().split(":")[-1].strip()      # "no" or "yes"
    bad = 0
    try:
        for k in range(a.tries):
            adb("shell", "am", "force-stop", PKG)
            adb("logcat", "-c")
            adb("shell", "am", "start", "-W", "-n", ACT, "--es", "import", remote)
            before = pid()
            time.sleep(0.1)
            adb("shell", "cmd", "uimode", "night", "no" if night == "yes" else "yes")    # the screen rebuilt
            time.sleep(8.0)
            after = pid()
            log = adb("logcat", "-d", "-v", "brief")
            crashed = "not attached to window manager" in log or "FATAL EXCEPTION" in log
            told = sum(1 for ln in log.splitlines() if "PantheraImport" in ln and "plan for" in ln)
            ok = bool(before) and after == before and not crashed and told == 1
            print("%-4s try %d: process %s -> %s, %s, the outcome reported %d time(s)" % (
                "ok" if ok else "FAIL", k + 1, before or "-", after or "(gone)",
                "a crash in the log" if crashed else "no crash in the log", told))
            bad += not ok
            if not ok:
                for ln in log.splitlines():
                    if "AndroidRuntime" in ln or "PantheraImport" in ln:
                        print("     " + ln[:190])
            adb("shell", "cmd", "uimode", "night", night)
            time.sleep(1.0)
    finally:
        adb("shell", "cmd", "uimode", "night", night, check=False)
        adb("shell", "rm", "-f", remote, check=False)
        os.remove(local)
        os.rmdir(tmp)
    print("import recreate: %s" % ("%d of %d FAILED" % (bad, a.tries) if bad else "all ok"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
