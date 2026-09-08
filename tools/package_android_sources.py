"""Archive the sources used by an Android build, without engine or voice data.

Run after committing Panthera changes. Upstream archives use pinned commits,
not the clone's current checkout. Local changes to upstream trees are ignored
by the builder and by this packager; Panthera's patch scripts are included.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parents[1]


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy", action="store_true", help="Unicorn + FAAD2 build")
    for name in ("box86", "box64", "glint"):
        parser.add_argument("--" + name + "-source", type=Path,
            default=os.environ.get(name.upper() + "_SOURCE", ROOT / "build/dependencies" / (name + "-source")))
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("box_builder", ROOT / "tools/translation_experiment/build.py")
    box = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(box)
    paths = ["src", "android/harness", "build_jni_so.sh", "licenses", "LICENSE",
             "docs/android-engine-workers.md", "tools/package_android_sources.py",
             "tools/build_android.py", "tools/translation_experiment", "tools/aac_experiment"]
    if git(ROOT, "status", "--porcelain", "--untracked-files=all", "--", *paths):
        raise SystemExit("Commit the built Panthera sources before packaging")
    sources = {"panthera": (ROOT, "HEAD", paths)}
    if args.legacy:
        for name in ("unicorn", "faad2"):
            repo = ROOT / "android/harness" / name
            if git(repo, "status", "--porcelain", "--untracked-files=all"):
                raise SystemExit(name + ": source checkout has local changes")
            sources[name] = (repo, "HEAD", [])
    else:
        pins = dict(box.PINS, glint=box.glint_experiment.PIN)
        for name, pin in pins.items():
            sources[name] = (getattr(args, name + "_source"), pin, [])
        for abi, backend in (("armeabi-v7a", "box86"), ("arm64-v8a", "box64")):
            directory = ROOT / "src/platforms/android/app/src/main/jniLibs" / abi
            info = json.loads((directory / "build.json").read_text())
            if (info.get("runtime") != backend or info.get("translator_commit") != pins[backend]
                    or info.get("aac") != "glint" or info.get("aac_commit") != pins["glint"]
                    or info.get("sha256") != hashlib.sha256((directory / "libpanthera.so").read_bytes()).hexdigest()):
                raise SystemExit(abi + ": staged library does not match the pinned build manifest")
    out = ROOT / "build" / ("android-sources-legacy" if args.legacy else "android-sources")
    out.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for name, (repo, revision, selected) in sources.items():
        archive = out / (name + ".tar.gz")
        git(repo, "archive", "--format=tar.gz", "--output=" + str(archive), revision, *selected)
        manifest[name] = {
            "commit": git(repo, "rev-parse", revision).decode().strip(),
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        }
    manifest_path = out / "sources.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    bundle = ROOT / "build" / ("panthera-android-sources-legacy.tar.gz" if args.legacy else "panthera-android-sources.tar.gz")
    with tarfile.open(bundle, "w:gz") as package:
        for name in sources:
            package.add(out / (name + ".tar.gz"), arcname=name + ".tar.gz")
        package.add(manifest_path, arcname="sources.json")
        for notice in sorted((ROOT / "licenses").iterdir()):
            if notice.is_file():
                package.add(notice, arcname="licenses/" + notice.name)
    print(bundle)


if __name__ == "__main__":
    main()
