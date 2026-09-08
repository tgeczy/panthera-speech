"""Build and stage the normal Box + Glint Android runtime and its notices."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "tools/translation_experiment/build.py"
spec = importlib.util.spec_from_file_location("box_builder", BUILD_SCRIPT)
box = importlib.util.module_from_spec(spec)
spec.loader.exec_module(box)


def checkout(name, supplied, url):
    if supplied:
        return Path(supplied).resolve(strict=True)
    path = ROOT / "build/dependencies" / (name + "-source")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--no-checkout", url, str(path)], check=True)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--abi", choices=["armeabi-v7a", "arm64-v8a"], required=True)
    parser.add_argument("--box-source", type=Path)
    parser.add_argument("--glint-source", type=Path, default=os.environ.get("GLINT_SOURCE"))
    parser.add_argument("--ndk", type=Path, default=os.environ.get("ANDROID_NDK", "C:/Android/sdk/ndk/27.2.12479018"))
    parser.add_argument("--cmake", type=Path, default="C:/Android/sdk/cmake/3.22.1/bin/cmake.exe")
    args = parser.parse_args()
    backend = "box86" if args.abi == "armeabi-v7a" else "box64"
    source = checkout(backend, args.box_source or os.environ.get(backend.upper() + "_SOURCE"),
                      "https://github.com/ptitSeb/" + backend + ".git")
    glint = checkout("glint", args.glint_source, "https://github.com/CrispStrobe/glint.git")
    work = ROOT / "build/android-native"
    work.mkdir(parents=True, exist_ok=True)
    # Keep failed builds inspectable, and never reuse partially built objects
    # or an earlier experiment's JNI library as a normal build.
    stage = Path(tempfile.mkdtemp(prefix=args.abi + "-", dir=work)) / "runtime"
    subprocess.run([sys.executable, str(BUILD_SCRIPT), "--backend", backend,
                    "--source", str(source), "--out", str(stage),
                    "--aac", "glint", "--aac-source", str(glint), "--api", "26",
                    "--ndk", str(args.ndk), "--cmake", str(args.cmake)], check=True)
    notice = ROOT / "licenses" / (backend + "-LICENSE.txt")
    if notice.read_text() != (stage / "translator/LICENSE").read_text():
        raise RuntimeError("Translator's pinned licence differs from packaged notice")
    if (ROOT / "licenses/Glint-MIT.txt").read_text() != (stage / "glint/LICENSE").read_text():
        raise RuntimeError("Glint's pinned licence differs from packaged notice")
    library = stage / "build/libpanthera.so"
    target = ROOT / "src/platforms/android/app/src/main/jniLibs" / args.abi
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(library, target / library.name)
    manifest = dict(runtime=backend, translator_commit=box.PINS[backend],
                    aac="glint", aac_commit=box.glint_experiment.PIN, api=26,
                    sha256=hashlib.sha256(library.read_bytes()).hexdigest())
    (target / "build.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf8")
    print("Staged " + str(target / library.name))
    print("Build and contract checks: " + str(stage / "build"))


if __name__ == "__main__":
    main()
