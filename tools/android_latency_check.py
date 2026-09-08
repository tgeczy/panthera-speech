"""Run synthetic TTS latency checks on one explicitly selected Android device.

Preserve preferences and capture logs while the test runs, before Android's
small log buffers overwrite the evidence. No engine or voice data is copied.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--label", default="latency")
    parser.add_argument("--no-install", action="store_true", help="Use the APK already installed, avoiding package-replacement callbacks")
    parser.add_argument("--mode", choices=["true", "native", "rapid", "lifecycle", "audio", "reuse"], default="true")
    args = parser.parse_args()
    adb = [args.adb, "-s", args.serial]
    package = "com.pantheraspeech.tts"
    output = Path(tempfile.mkdtemp(prefix=f"panthera-{args.label}-{args.serial}-"))
    apk = Path(__file__).resolve().parents[1] / "src/platforms/android/app/build/outputs/apk"
    prefs = subprocess.check_output(adb + ["exec-out", "run-as", package,
                                          "cat", "shared_prefs/pantheraspeech.xml"])
    (output / "original-preferences.xml").write_bytes(prefs)
    print(f"Device {args.serial}; diagnostics: {output}", flush=True)
    logs = None
    try:
        for path in ([] if args.no_install else [apk / "debug/app-debug.apk", apk / "androidTest/debug/app-debug-androidTest.apk"]):
            subprocess.run(adb + ["install", "-r", str(path)], check=True)
        with (output / "logcat.txt").open("wb") as log:
            logs = subprocess.Popen(adb + ["logcat", "-T", "1", "-b", "main,system,crash,events",
                                           "-v", "threadtime"], stdout=log, stderr=subprocess.STDOUT)
            mode = ["audioOnly", "true"] if args.mode == "audio" else ["latency", args.mode]
            result = subprocess.run(adb + ["shell", "am", "instrument", "-w", "-e", *mode,
                f"{package}.test/{package}.EngineSmokeTest"], capture_output=True, timeout=600)
            (output / "result.txt").write_bytes(result.stdout + result.stderr)
            print(result.stdout.decode(errors="replace"), flush=True)
            result.check_returncode()
            expected = {"lifecycle": b"PASS worker ownership", "audio": b"PASS: sliders",
                        "reuse": b"PASS completed renderer reuse"}.get(args.mode, b"PASS latency probe")
            if expected not in result.stdout or b"FAIL" in result.stdout:
                raise RuntimeError(f"Latency test failed; see {output}")
    finally:
        if logs is not None:
            logs.terminate()
            logs.wait(timeout=10)
        subprocess.run(adb + ["shell", "am", "force-stop", package], check=True)
        subprocess.run(adb + ["shell", f"run-as {package} sh -c 'cat > shared_prefs/pantheraspeech.xml'"],
                       input=prefs, check=True)
        restored = subprocess.check_output(adb + ["exec-out", "run-as", package,
                                                  "cat", "shared_prefs/pantheraspeech.xml"])
        if restored != prefs:
            raise RuntimeError(f"Preference restoration failed; backup: {output}")
        print("Preferences restored exactly.", flush=True)


if __name__ == "__main__":
    main()
