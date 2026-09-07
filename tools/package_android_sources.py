"""Package corresponding source for an Android build; never include engine data.

Run after committing the APK's sources, alongside build_jni_so.sh and Gradle.
The dependency checkouts must contain the exact sources used for that build.
Only tracked source is archived; ignored build products and personal data stay out.
"""
from pathlib import Path
import hashlib
import json
import subprocess
import tarfile


ROOT = Path(__file__).resolve().parents[1]


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args])


def main():
    out = ROOT / "build/android-sources"
    out.mkdir(parents=True, exist_ok=True)
    sources = {
        "panthera": (ROOT, ["src", "android/harness", "build_jni_so.sh",
                            "licenses", "LICENSE", "docs/android-engine-workers.md",
                            "tools/package_android_sources.py"]),
        "unicorn": (ROOT / "android/harness/unicorn", []),
        "faad2": (ROOT / "android/harness/faad2", []),
    }
    manifest = {}
    for name, (repo, paths) in sources.items():
        if git(repo, "diff", "HEAD", "--", *paths):
            raise SystemExit(f"{name}: commit the built source changes before packaging")
        archive = out / f"{name}.tar.gz"
        git(repo, "archive", "--format=tar.gz", f"--output={archive}", "HEAD", *paths)
        manifest[name] = {
            "commit": git(repo, "rev-parse", "HEAD").decode().strip(),
            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        }
    manifest_path = out / "sources.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    bundle = ROOT / "build/panthera-android-sources.tar.gz"
    with tarfile.open(bundle, "w:gz") as package:
        for name in sources:
            package.add(out / f"{name}.tar.gz", arcname=f"{name}.tar.gz")
        package.add(manifest_path, arcname="sources.json")
        for notice in sorted((ROOT / "licenses").iterdir()):
            if notice.is_file():
                package.add(notice, arcname=f"licenses/{notice.name}")
    print(bundle)


if __name__ == "__main__":
    main()
