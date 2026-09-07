"""Build isolated Android translator experiments from a pinned local Git clone.

Does not modify the supplied clone, production sources, APK, or connected devices.
Uses git archive so local third-party edits cannot silently affect the comparison.
"""
import argparse
import io
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

PINS = {
    "box86": "39d3ed203323000c11f47b780f7468fa24a7185d",
    "box64": "36d1cd790a6992cf188ef0ad5c5536d811c96d0c",
}
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent


def run(args, **kwargs):
    subprocess.run([str(a) for a in args], check=True, **kwargs)


def replace(path, old, new):
    source = path.read_text(encoding="utf8")
    if old not in source:
        raise RuntimeError(f"Expected patch site missing: {path}: {old}")
    path.write_text(source.replace(old, new), encoding="utf8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=PINS, required=True)
    parser.add_argument("--source", type=Path, required=True,
                        help="Local official Box86/Box64 Git clone containing the pinned commit")
    parser.add_argument("--out", type=Path, required=True, help="New experiment directory")
    parser.add_argument("--ndk", type=Path, default=Path("C:/Android/sdk/ndk/27.2.12479018"))
    parser.add_argument("--cmake", type=Path, default=Path("C:/Android/sdk/cmake/3.22.1/bin/cmake.exe"))
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists():
        parser.error("--out must be new; rebuild an existing experiment with cmake --build OUT/build")
    backend = args.backend
    abi = "armeabi-v7a" if backend == "box86" else "arm64-v8a"
    faad = ROOT / "android/harness" / f"faad2-obj-{abi}"
    if not list(faad.glob("*.o")):
        parser.error(f"Build the existing FAAD2 objects first: {faad}")
    archive = subprocess.check_output(["git", "-C", str(args.source), "archive", PINS[backend]])
    out.mkdir(parents=True)
    vendor = out / "translator"
    vendor.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(vendor, filter="data")
    host = out / "host"
    host.mkdir()
    for path in (ROOT / "src").iterdir():
        if path.is_file():
            shutil.copy2(path, host / path.name)
    # Keep the loader and argument dispatch unchanged. Only replace the emitted
    # guest bridge record, whose layout is specific to the chosen translator.
    seam = host / "tiger_host_uc.c"
    source = seam.read_text(encoding="utf8")
    start = source.index("    unsigned char b[UC_TRAMP_STRIDE];")
    end = source.index("\n}", start)
    source = (source[:start] + f"    extern void {backend}_write_slot(uc_engine*, unsigned, int);\n"
              f"    {backend}_write_slot(t_uc,g_uc_tramp+(unsigned)idx*UC_TRAMP_STRIDE,fp);" + source[end:])
    if backend == "box64":
        source = source.replace("#define UC_TRAMP_STRIDE 16u", "#define UC_TRAMP_STRIDE 32u")
    seam.write_text(source, encoding="utf8")

    if backend == "box86":
        for path in (vendor / "src").rglob("*.c"):
            old = path.read_bytes()
            new = old.replace(b"isnanf(", b"isnan(").replace(b"isinff(", b"isinf(")
            if new != old:
                path.write_bytes(new)
        replace(vendor / "src/elfs/elfload_dump.c", "#ifndef TERMUX",
                "#if defined(SHT_CHECKSUM) && !defined(TERMUX)")
        replace(vendor / "src/box86context.c", "context->deferedInit = 0", "context->deferredInit = 0")
        objects = "box86 dynarec interpreter"
        options = ["-DARM_DYNAREC=ON", "-DBOX86LIB=ON", f"-DBOX86_ROOT={vendor.as_posix()}",
                   "-DANDROID_ARM_MODE=arm", "-DNO_LIB_INSTALL=ON", "-DNO_CONF_INSTALL=ON"]
    else:
        # Use the raw 32-bit instruction translator with our own bridge, not
        # Box64's optional Linux i386 library wrappers (BOX32).
        replace(vendor / "src/main.c", "int main(", "int box64_cli_main(")
        replace(vendor / "src/emu/x64int3.c", "void x86Int3(", "__attribute__((weak)) void x86Int3(")
        objects = "mainobj dynarec interpreter"
        options = ["-DARM64=ON", "-DBOX32=OFF", "-DNOBOX64=ON", "-DNOLOADADDR=ON"]

    replace(vendor / "CMakeLists.txt", "$(git rev-parse --short HEAD)", PINS[backend][:7])
    for filename in (f"{backend}_adapter.c", f"memory_{backend}.c", "engine_bench.c", "memory_native.c"):
        shutil.copy2(HERE / filename, vendor / filename)
    uc = ROOT / "android/harness/unicorn"
    cmake = f'''
add_executable(panthera_memory_bench memory_{backend}.c)
target_include_directories(panthera_memory_bench PRIVATE "{ROOT.as_posix()}/android/harness/src")
target_link_libraries(panthera_memory_bench {objects} m dl)
add_executable(panthera_native_bench memory_native.c)
add_executable(panthera_engine_bench engine_bench.c {backend}_adapter.c "{host.as_posix()}/tiger_host.c")
target_include_directories(panthera_engine_bench PRIVATE "{host.as_posix()}" "{uc.as_posix()}/include" "{ROOT.as_posix()}/android/harness/faad2/include")
target_compile_definitions(panthera_engine_bench PRIVATE TIGER_UC TIGER_AAC_FAAD TIGER_SHARED)
target_compile_options(panthera_engine_bench PRIVATE -Wno-macro-redefined)
file(GLOB FAAD_OBJS "{faad.as_posix()}/*.o")
target_link_libraries(panthera_engine_bench {objects} ${{FAAD_OBJS}} m dl log mediandk)
'''
    with (vendor / "CMakeLists.txt").open("a", encoding="utf8") as f:
        f.write(cmake)
    env = os.environ.copy()
    env["PATH"] = "C:/Program Files/Git/bin" + os.pathsep + env["PATH"]
    with (out / "build.log").open("w", encoding="utf8") as log:
        run([args.cmake, "-S", vendor, "-B", out / "build", "-G", "Ninja",
             f"-DCMAKE_MAKE_PROGRAM={args.cmake.parent.as_posix()}/ninja.exe",
             f"-DCMAKE_TOOLCHAIN_FILE={args.ndk.as_posix()}/build/cmake/android.toolchain.cmake",
             f"-DANDROID_ABI={abi}", "-DANDROID_PLATFORM=android-28", "-DCMAKE_BUILD_TYPE=Release",
             *options], env=env, stdout=log, stderr=subprocess.STDOUT)
        run([args.cmake, "--build", out / "build", "--target", "panthera_engine_bench",
             "panthera_memory_bench", "panthera_native_bench", "-j6"],
            env=env, stdout=log, stderr=subprocess.STDOUT)
    print(f"Built {backend} {PINS[backend]} for {abi}: {out / 'build'}")


if __name__ == "__main__":
    main()
