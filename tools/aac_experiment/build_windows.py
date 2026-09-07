"""Build an isolated native i386 host using Glint, leaving installed hosts alone."""
import argparse
from pathlib import Path
import shutil
import subprocess

import glint

ROOT = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True, help="Local official Glint Git clone")
    parser.add_argument("--out", type=Path, required=True, help="New ignored experiment directory")
    parser.add_argument("--cmake", default="C:/Android/sdk/cmake/3.22.1/bin/cmake.exe")
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists():
        parser.error("--out must be new; rebuild an existing directory with cmake --build OUT/build --config Release")
    host = out / "host"
    host.mkdir(parents=True)
    for path in (ROOT / "src").iterdir():
        if path.is_file():
            shutil.copy2(path, host / path.name)
    vendor = glint.prepare(args.source, out, host)
    shutil.copy2(glint.HERE / "fpcheck.cpp", out)
    (out / "CMakeLists.txt").write_text('''
cmake_minimum_required(VERSION 3.22)
project(panthera_aac_experiment LANGUAGES C CXX)
set(CMAKE_MSVC_RUNTIME_LIBRARY MultiThreaded)
''' + glint.cmake_library(vendor) + '''
add_executable(tiger_host host/tiger_host.c)
target_compile_definitions(tiger_host PRIVATE TIGER_AAC_GLINT)
target_link_libraries(tiger_host PRIVATE panthera_glint_decoder winmm ole32 mfuuid)
target_link_options(tiger_host PRIVATE /LARGEADDRESSAWARE)
add_executable(fpcheck fpcheck.cpp)
target_link_libraries(fpcheck PRIVATE panthera_glint_decoder)
''', encoding="utf8")
    with (out / "build.log").open("w", encoding="utf8") as log:
        for command in ([args.cmake, "-S", str(out), "-B", str(out / "build"),
                         "-G", "Visual Studio 17 2022", "-A", "Win32"],
                        [args.cmake, "--build", str(out / "build"), "--config", "Release"]):
            subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
    print(out / "build/Release/tiger_host.exe")


if __name__ == "__main__":
    main()
