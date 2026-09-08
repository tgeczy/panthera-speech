"""Build the native i386 EXE/DLL with Media Foundation and a Glint fallback.

The pinned dependency is exported into an ignored temporary build directory;
only binaries and notices are staged. No engine or voice data is needed.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent / "aac_experiment"))
import glint

ROOT = Path(__file__).resolve().parents[1]


def newest(paths):
    return max(paths, key=lambda p: tuple(int(x) for x in p.name.split(".")))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "build")
    parser.add_argument("--source", type=Path, default=os.environ.get("GLINT_SOURCE"))
    parser.add_argument("--no-dll", action="store_true")
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    source = args.source
    if source is None:
        source = ROOT / "build/dependencies/glint-source"
        if not source.exists():
            source.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "clone", "--no-checkout",
                            "https://github.com/CrispStrobe/glint.git", str(source)], check=True)
    source = source.resolve(strict=True)
    candidates = []
    for root in ("C:/Program Files (x86)/Microsoft Visual Studio",
                 "C:/Program Files/Microsoft Visual Studio"):
        candidates.extend(Path(root).glob("*/*/VC/Tools/MSVC/*"))
    if not candidates:
        parser.error("MSVC Build Tools are required")
    msvc = newest(candidates)
    sdk = Path("C:/Program Files (x86)/Windows Kits/10")
    sdkver = newest((sdk / "Include").iterdir()).name
    cl = msvc / "bin/Hostx64/x86/cl.exe"
    includes = [msvc / "include"] + [sdk / "Include" / sdkver / p
                                      for p in ("ucrt", "um", "shared")]
    libs = [msvc / "lib/x86"] + [sdk / "Lib" / sdkver / p / "x86"
                                  for p in ("ucrt", "um")]
    common = [str(cl), "/nologo", "/O2", "/MT", "/W3", "/D_WIN32_WINNT=0x0601"]
    common += ["/I" + str(p) for p in includes]
    with tempfile.TemporaryDirectory(prefix="windows-", dir=out) as scratch:
        stage = Path(scratch)
        host = stage / "host"
        shutil.copytree(ROOT / "src", host)
        vendor = glint.prepare(source, stage, host)
        with (out / "build-windows.log").open("w", encoding="utf8") as log:
            def run(args):
                subprocess.run(args, cwd=stage, check=True, stdout=log, stderr=subprocess.STDOUT)

            objects = []
            for name, path in [("decoder", vendor / "src/aac_decoder.cpp"),
                               ("bridge", vendor / "glint_bridge.cpp")]:
                obj = stage / (name + ".obj")
                run(common + ["/EHsc", "/std:c++17", "/D_USE_MATH_DEFINES",
                              "/I" + str(vendor / "src"), "/c", str(path), "/Fo" + str(obj)])
                objects.append(str(obj))
            for dll in ([False] if args.no_dll else [False, True]):
                obj = stage / ("host-dll.obj" if dll else "host.obj")
                run(common + ["/DTIGER_AAC_FALLBACK"] + (["/DPT_DLL"] if dll else []) +
                    ["/c", str(host / "tiger_host.c"), "/Fo" + str(obj)])
                binary = out / ("tiger_host.dll" if dll else "tiger_host.exe")
                run(common + (["/LD"] if dll else []) + [str(obj)] + objects +
                    ["/Fe" + str(binary), "/link"] +
                    ["/LIBPATH:" + str(p) for p in libs] +
                    ["winmm.lib", "ole32.lib", "mfuuid.lib"] +
                    ([] if dll else ["/LARGEADDRESSAWARE"]))
                print(binary)
        # Check the fetched notice, so a stale local licence cannot accompany
        # a dependency whose pinned archive says something different.
        notice = ROOT / "licenses/Glint-MIT.txt"
        if notice.read_text() != (vendor / "LICENSE").read_text():
            raise RuntimeError("Pinned Glint licence differs from distribution notice")
        shutil.copy2(notice, out / "Glint-MIT.txt")
        shutil.copy2(ROOT / "LICENSE", out / "Panthera-MIT.txt")


if __name__ == "__main__":
    main()
