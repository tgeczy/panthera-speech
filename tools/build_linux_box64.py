"""Build the native Linux aarch64 host on Box64, the translator the phones use.

Apple's engines are i386 code.  On x86 Linux the host calls them directly; on
an ARM machine it cannot, so the same pinned Box64 translator that runs them
on Android runs them here, with Panthera's own 32-bit bridge rather than
Box64's Linux library wrappers -- exactly the arrangement the Android build
ships.  The AAC decoder is the pinned Glint, as on every normal build.

    python3 tools/build_linux_box64.py --out build/linux-aarch64-glint

build_linux.sh aarch64 runs this.  The translator and decoder are exported
from local clones at their pinned commits (fetched into build/dependencies if
absent); nothing of either is committed here, and nothing of Apple's is
touched.  Honest scope: this build is compiled and self-tested on ARM hardware
in CI, through the same translator the phones were validated with, but at the
time of writing nobody has listened to it on an ARM Linux machine.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
BUILD_SCRIPT = ROOT / "tools/translation_experiment/build.py"
spec = importlib.util.spec_from_file_location("box_builder", BUILD_SCRIPT)
box = importlib.util.module_from_spec(spec)
spec.loader.exec_module(box)
glint_experiment = box.glint_experiment


def checkout(name, supplied, url):
    if supplied:
        return Path(supplied).resolve(strict=True)
    path = ROOT / "build/dependencies" / (name + "-source")
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--no-checkout", url, str(path)], check=True)
    return path


def run(args, **kwargs):
    subprocess.run([str(a) for a in args], check=True, **kwargs)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "build/linux-aarch64-glint")
    parser.add_argument("--box-source", type=Path, default=os.environ.get("BOX64_SOURCE"))
    parser.add_argument("--glint-source", type=Path, default=os.environ.get("GLINT_SOURCE"))
    parser.add_argument("--cmake", default="cmake")
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 2)
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists():
        shutil.rmtree(out)
    source = checkout("box64", args.box_source, "https://github.com/ptitSeb/box64.git")
    glint = checkout("glint", args.glint_source, "https://github.com/CrispStrobe/glint.git")
    out.mkdir(parents=True)
    vendor, objects, options = box.vendor_translator("box64", source, out)
    host = box.stage_host(out, "box64")
    # The same stage layout build_linux.sh makes for i686, so the CI checks
    # that look for out/glint/glint/src find it in both builds.
    stage = out / "glint"
    stage.mkdir()
    aac_vendor = glint_experiment.prepare(glint, stage, host)
    exports = ROOT / "src/panthera.exports"
    cmake = f'''
remove_definitions(-std=gnu11)
add_compile_options("$<$<COMPILE_LANGUAGE:C>:-std=gnu11>")
enable_language(CXX)
{glint_experiment.cmake_library(aac_vendor)}
{glint_experiment.cmake_checks(aac_vendor)}
# The host is one C translation unit, tiger_host.c including the rest, built
# with the flags build_linux.sh uses and the translator's bridge beside it.
function(panthera_host_target target)
    target_include_directories(${{target}} PRIVATE "{host.as_posix()}")
    target_compile_definitions(${{target}} PRIVATE TIGER_UC TIGER_INLINE_GUEST TIGER_BOX64 TIGER_AAC_GLINT _GNU_SOURCE ${{ARGN}})
    # Box64's CMake compiles everything with -fvisibility=hidden, which would
    # hide the panthera_* API from the version script; the host is visible.
    target_compile_options(${{target}} PRIVATE -O2 -fno-strict-aliasing -Wno-deprecated-declarations -Wno-macro-redefined -fvisibility=default)
    target_link_libraries(${{target}} {objects} panthera_glint_decoder m dl pthread)
endfunction()
add_executable(tiger_host box64_adapter.c "{host.as_posix()}/tiger_host.c")
panthera_host_target(tiger_host)
# The synthesis library Android and independent clients use, with the same
# export list and soname as the i686 build.
add_library(panthera SHARED box64_adapter.c "{host.as_posix()}/tiger_host.c")
panthera_host_target(panthera TIGER_LIB TIGER_SHARED)
set_target_properties(panthera PROPERTIES SOVERSION 0)
target_link_options(panthera PRIVATE "-Wl,--version-script={exports.as_posix()}" "-Wl,--no-undefined")
'''
    with (vendor / "CMakeLists.txt").open("a", encoding="utf8") as f:
        f.write(cmake)
    build = out / "build"
    with (out / "build.log").open("w", encoding="utf8") as log:
        run([args.cmake, "-S", vendor, "-B", build, "-DCMAKE_BUILD_TYPE=Release",
             "-DCMAKE_POSITION_INDEPENDENT_CODE=ON", f"-DPYTHON_EXECUTABLE={sys.executable}",
             *options], stdout=log, stderr=subprocess.STDOUT)
        run([args.cmake, "--build", build, "--target", "tiger_host", "panthera",
             "pcm16_check", "huffman_check", "-j", str(args.jobs)], stdout=log, stderr=subprocess.STDOUT)
    # The layout build_linux.sh produces, so packaging and the checks are shared.
    shutil.copy2(build / "tiger_host", out / "tiger_host")
    shutil.copy2(build / "libpanthera.so.0", out / "libpanthera.so.0")
    link = out / "libpanthera.so"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to("libpanthera.so.0")
    for name in ("pcm16_check", "huffman_check"):
        shutil.copy2(build / name, out / name)
    (out / "include").mkdir()
    shutil.copy2(ROOT / "src/tiger_host_jni.h", out / "include/panthera.h")
    licenses = out / "licenses"
    licenses.mkdir()
    for name in ("Panthera-MIT.txt", "DISTRIBUTION.txt", "box64-LICENSE.txt", "Box-component-notices.txt"):
        shutil.copy2(ROOT / "licenses" / name, licenses / name)
    shutil.copy2(aac_vendor / "LICENSE", licenses / "Glint-MIT.txt")
    if (ROOT / "licenses/Glint-MIT.txt").read_text() != (aac_vendor / "LICENSE").read_text():
        raise SystemExit("The pinned Glint license differs from licenses/Glint-MIT.txt")
    if (ROOT / "licenses/box64-LICENSE.txt").read_text() != (vendor / "LICENSE").read_text():
        raise SystemExit("The pinned Box64 license differs from licenses/box64-LICENSE.txt")
    digest = hashlib.sha256((out / "tiger_host").read_bytes()).hexdigest()
    (out / "build.json").write_text(json.dumps(dict(
        runtime="box64", translator_commit=box.PINS["box64"], aac="glint",
        aac_commit=glint_experiment.PIN, host="tiger_host", sha256=digest), indent=2) + "\n")
    print(f"Built Box64 {box.PINS['box64']} + Glint {glint_experiment.PIN} for aarch64 Linux: {out}")


if __name__ == "__main__":
    main()
