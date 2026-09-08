"""Compare whole native renders using two isolated hosts and local voice data.

Writes generated PCM only to the caller's output directory. Requires NumPy and
the repository's NVDA test fakes. Reports differences without changing oracles.
"""
import argparse
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True,
                        help="Parent of the user's speech-tiger, speech-leopard, etc. trees")
    parser.add_argument("--generation", action="append", choices=["tiger", "leopard", "snowleopard", "lion"])
    parser.add_argument("--max-pcm-delta", type=int,
                        help="Fail on unequal lengths, unstable repeats, or PCM differences above this explicit tolerance")
    args = parser.parse_args()
    if args.max_pcm_delta is not None and args.max_pcm_delta < 0:
        parser.error("--max-pcm-delta must be nonnegative")
    generations = args.generation or ["tiger", "leopard", "snowleopard", "lion"]
    for gen in generations:
        tree = args.data_root / ("speech-" + gen)
        if gen == "tiger":
            tree /= "x86"
        os.environ[gen.upper() + "_TREE"] = str(tree.resolve(strict=True))
    load("aac_nvda_fakes", ROOT / "panthera/tests/conftest.py")
    breath = load("aac_breath", ROOT / "panthera/tests/leopard/test_breath.py")
    texts = ["Hello there.", "Seven.", "Restart with debug logging enabled.",
             breath.S1 + " " + breath.S2]
    args.out.mkdir(parents=True, exist_ok=False)
    rows = []
    failures = []
    for gen in generations:
        module = importlib.import_module("synthDrivers." + gen + "speech")
        for voice in (("Vicki",) if gen == "tiger" else ("Alex", "Vicki")):
            refs = {}
            for backend, host in [("baseline", args.baseline), ("candidate", args.candidate)]:
                if gen == "tiger":
                    module.HOST_EXE = str(host.resolve(strict=True))
                else:
                    module.SynthDriver.TREE.HOST_EXE = str(host.resolve(strict=True))
                driver = module.SynthDriver()
                try:
                    for rate in (180, 387):
                        for index, text in enumerate(texts):
                            start = time.perf_counter()
                            pcm = driver._render(text, rate, voice)
                            elapsed = (time.perf_counter() - start) * 1000
                            if not pcm:
                                raise RuntimeError(f"No audio: {gen} {voice} {backend}")
                            name = f"{gen}-{voice}-{rate}-{index}"
                            (args.out / f"{name}-{backend}.pcm").write_bytes(pcm)
                            a = np.frombuffer(pcm, dtype="<i2").astype(float)
                            row = dict(generation=gen, voice=voice, backend=backend, rate=rate,
                                       text=index, frames=len(a), complete_ms=elapsed,
                                       sha256=hashlib.sha256(pcm).hexdigest(),
                                       repeat_exact=driver._render(text, rate, voice) == pcm)
                            if voice == "Alex" and index == 3:
                                row["breaths"] = breath._breaths(breath._samples(pcm))
                            if backend == "baseline":
                                refs[name] = a
                            else:
                                b = refs[name]
                                row["reference_frames"] = len(b)
                                if len(a) == len(b):
                                    error = np.sum((a - b)**2)
                                    row["max_delta"] = float(np.max(np.abs(a - b)))
                                    row["snr_db"] = float(10 * np.log10(np.sum(b*b) / max(error, 1e-20)))
                            rows.append(row)
                            if args.max_pcm_delta is not None:
                                if not row["repeat_exact"]:
                                    failures.append(f"{name} {backend}: unstable repeat")
                                if backend == "candidate":
                                    if row["frames"] != row["reference_frames"]:
                                        failures.append(f"{name}: unequal frame counts")
                                    elif row["max_delta"] > args.max_pcm_delta:
                                        failures.append(f"{name}: PCM delta {row['max_delta']} exceeds {args.max_pcm_delta}")
                            (args.out / "comparison.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf8")
                            print(json.dumps(row), flush=True)
                finally:
                    driver.terminate()
    if failures:
        raise SystemExit("Comparison failed:\n" + "\n".join(failures))
    if args.max_pcm_delta is not None:
        print(f"PASS: {len(rows)//2} configurations, equal lengths, exact repeats, PCM delta <= {args.max_pcm_delta}")


if __name__ == "__main__":
    main()
