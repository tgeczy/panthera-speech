"""Run an isolated translator benchmark on an explicitly selected Android device.

Requires existing user-supplied engine data and eight local reference PCM files.
Does not install an APK or modify preferences. Output is local diagnostic data.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", default="adb")
    parser.add_argument("--serial", required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--oracle", type=Path, required=True, help="Prefix of .0 through .7 reference files")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--generation", default="leopard", choices=["tiger", "leopard", "snowleopard", "lion"])
    parser.add_argument("--voice", default="Alex")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--signal-check", action="store_true")
    args = parser.parse_args()
    references = [Path(f"{args.oracle}.{i}").read_bytes() for i in range(8)]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    adb = [args.adb, "-s", args.serial]
    package = "com.pantheraspeech.tts"
    executable = "panthera-translator-check"
    subprocess.run(adb + ["push", str(args.binary), f"/data/local/tmp/{executable}"], check=True)
    for command in (["cp", f"/data/local/tmp/{executable}", f"files/{executable}"],
                    ["chmod", "700", f"files/{executable}"]):
        subprocess.run(adb + ["shell", "run-as", package, *command], check=True)
    root = f"files/panthera-data/{args.generation}"
    prefix = "files/panthera-translator-check.pcm"
    environment = ["env", "PANTHERA_SIGNAL_CHECK=1"] if args.signal_check else []
    command = adb + ["shell", "run-as", package, *environment, f"files/{executable}",
                     f"{root}/MacinTalk", f"{root}/SpeechDictionary.framework/Versions/A/SpeechDictionary",
                     f"{root}/Voices/{args.voice}.SpeechVoice", prefix]
    report = {"serial": args.serial, "generation": args.generation, "voice": args.voice,
              "binary_sha256": hashlib.sha256(args.binary.read_bytes()).hexdigest(), "runs": []}
    with args.output.with_suffix(".log").open("wb") as log:
        try:
            for repeat in range(args.repeats):
                result = subprocess.run(command, capture_output=True, timeout=60)
                log.write(result.stdout + result.stderr); log.flush()
                result.check_returncode()
                if args.signal_check and b"PASS native signal handler" not in result.stdout:
                    raise RuntimeError("Native signal handler check did not pass")
                timings = re.findall(rb"RENDER (\d+) first=([\d.]+) done=([\d.]+) frames=(\d+) status=(\d+)", result.stdout)
                if len(timings) != 8:
                    raise RuntimeError("Benchmark did not complete all eight renders")
                for index, first, done, frames, status in timings:
                    i = int(index)
                    pcm = subprocess.check_output(adb + ["exec-out", "run-as", package, "cat", f"{prefix}.{i}"])
                    record = {"process": repeat, "render": i, "first_ms": float(first), "done_ms": float(done),
                              "frames": int(frames), "sha256": hashlib.sha256(pcm).hexdigest(),
                              "exact": pcm == references[i]}
                    report["runs"].append(record)
                    if int(status) or len(pcm) != int(frames) * 2 or not record["exact"]:
                        raise RuntimeError(f"PCM comparison failed: {record}")
                print(f"Process {repeat}: eight exact renders" + (", native signal handler preserved" if args.signal_check else ""), flush=True)
        finally:
            args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")


if __name__ == "__main__":
    main()
