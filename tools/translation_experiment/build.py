"""Build isolated Android translator artifacts from a pinned local Git clone.

Does not modify the supplied clone, production sources, APK, or connected devices.
Uses git archive so local third-party edits cannot silently affect the comparison.
"""
import argparse
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

PINS = {
    "box86": "39d3ed203323000c11f47b780f7468fa24a7185d",
    "box64": "36d1cd790a6992cf188ef0ad5c5536d811c96d0c",
}
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT / "tools/aac_experiment"))
import glint as glint_experiment


def android_sdk():
    """The Android SDK: ANDROID_HOME or ANDROID_SDK_ROOT if set, else the SDK's
    own standard install folder.  Nothing here names one machine."""
    for name in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(name):
            return Path(os.environ[name])
    return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "Android/Sdk"


def sdk_cmake():
    exe = ".exe" if os.name == "nt" else ""
    return android_sdk() / f"cmake/3.22.1/bin/cmake{exe}"


def run(args, **kwargs):
    subprocess.run([str(a) for a in args], check=True, **kwargs)


def replace(path, old, new):
    source = path.read_text(encoding="utf8")
    if old not in source:
        raise RuntimeError(f"Expected patch site missing: {path}: {old}")
    path.write_text(source.replace(old, new), encoding="utf8")


def android_legacy_function(path, signature, fallback):
    """Keep unused Linux launcher helpers from raising Android's minimum API.

    They are outside Panthera's guest-call interface. Before API 28, report
    unsupported Linux wrapper calls and omit Steam symlink discovery.
    """
    source = path.read_text(encoding="utf8")
    start = source.index("{", source.index(signature)) + 1
    end = source.index("\n}", start)
    source = (source[:start] + "\n#if defined(__ANDROID__) && __ANDROID_API__ < 28\n"
              "    /* Panthera embedding: this Linux wrapper is outside our guest interface. */\n"
              f"    {fallback}\n#else\n" + source[start:end] + "\n#endif" + source[end:])
    path.write_text(source, encoding="utf8")


def wrapper_command(vendor, backend):
    """Put the long upstream header argument list in a file, not a cmd line."""
    path = vendor / "CMakeLists.txt"
    source = path.read_text(encoding="utf8")
    root = "${" + backend.upper() + "_ROOT}"
    command = 'COMMAND "${PYTHON_EXECUTABLE}" "' + root + '/rebuild_wrappers.py"'
    start = source.index(command)
    end = source.index("MAIN_DEPENDENCY", start)
    arguments = source[start+len(command):end].strip()
    setup = ('set(PANTHERA_WRAPPER_ARGS ' + arguments + ')\n'
             'string(REPLACE ";" "\\n" PANTHERA_WRAPPER_ARGS "${PANTHERA_WRAPPER_ARGS}")\n'
             'file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/panthera-wrapper-args.txt" "${PANTHERA_WRAPPER_ARGS}\\n")\n')
    replacement = ('COMMAND "${PYTHON_EXECUTABLE}" "' + root + '/wrapper_args.py"\n'
                   '        "' + root + '/rebuild_wrappers.py" "${CMAKE_CURRENT_BINARY_DIR}/panthera-wrapper-args.txt"\n        ')
    source = source[:start] + replacement + source[end:]
    position = source.index("set(WRAPPER ")
    path.write_text(source[:position] + setup + source[position:], encoding="utf8")
    shutil.copy2(HERE / "wrapper_args.py", vendor)


def vendor_translator(backend, source, out):
    """Export the pinned translator into out/translator and apply Panthera's
    patches.  -> (vendor, objects, options): the directory, the CMake targets
    the host links, and the CMake options the translator is configured with.
    Shared by the Android builder and the native Linux aarch64 builder, so
    there is one patch list and not two that drift."""
    archive = subprocess.check_output(["git", "-C", str(source), "archive", PINS[backend]])
    vendor = out / "translator"
    vendor.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(vendor, filter="data")
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
        # The CLI interposes native mmap/munmap to allocate Linux guest memory.
        # Panthera owns its guest mappings explicitly. Interposing here also
        # routes bionic's logging-thread allocations into partially initialized
        # Box memory bookkeeping during startup.
        replace(vendor / "CMakeLists.txt", '    "${BOX64_ROOT}/src/custommmap.c"', "")
        objects = "mainobj dynarec interpreter"
        options = ["-DARM64=ON", "-DBOX32=OFF", "-DNOBOX64=ON", "-DNOLOADADDR=ON"]

    libc = vendor / "src/wrapped/wrappedlibc.c"
    android_legacy_function(libc, "EXPORT int32_t my_posix_spawnp(", "return ENOSYS;")
    if backend == "box64":
        android_legacy_function(libc, "EXPORT int32_t my_posix_spawn(", "return ENOSYS;")
        android_legacy_function(vendor / "src/steam.c", "static void create_libs_symlink(", "(void)folder;")
        for name in ("attr_getinheritsched", "attr_setinheritsched",
                     "mutexattr_getprotocol", "mutexattr_setprotocol"):
            android_legacy_function(vendor / "src/libtools/threads.c",
                                    f"EXPORT int my_pthread_{name}(", "return ENOSYS;")

    wrapper_command(vendor, backend)
    replace(vendor / "CMakeLists.txt", "$(git rev-parse --short HEAD)", PINS[backend][:7])
    for filename in (f"{backend}_adapter.c", f"memory_{backend}.c", "engine_bench.c", "memory_native.c", "box_signals.h", "box_memory.h", "runtime_check.c"):
        shutil.copy2(HERE / filename, vendor / filename)
    return vendor, objects, options


def stage_host(out, backend):
    """Copy src/ into out/host and swap in the translator's bridge record."""
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
    return host


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=PINS, required=True)
    parser.add_argument("--source", type=Path, required=True,
                        help="Local official Box86/Box64 Git clone containing the pinned commit")
    parser.add_argument("--out", type=Path, required=True, help="New experiment directory")
    parser.add_argument("--aac", choices=["faad", "glint"], default="faad")
    parser.add_argument("--aac-source", type=Path, help="Official Glint clone; required with --aac glint")
    parser.add_argument("--api", type=int, default=26, help="Android minimum API (matches the app by default)")
    parser.add_argument("--ndk", type=Path,
                        default=Path(os.environ.get("ANDROID_NDK") or android_sdk() / "ndk/27.2.12479018"))
    parser.add_argument("--cmake", type=Path, default=sdk_cmake())
    args = parser.parse_args()
    out = args.out.resolve()
    if out.exists():
        parser.error("--out must be new; rebuild an existing experiment with cmake --build OUT/build")
    backend = args.backend
    abi = "armeabi-v7a" if backend == "box86" else "arm64-v8a"
    faad = ROOT / "android/harness" / f"faad2-obj-{abi}"
    if args.aac == "faad" and not list(faad.glob("*.o")):
        parser.error(f"Build the existing FAAD2 objects first: {faad}")
    if (args.aac == "glint") != (args.aac_source is not None):
        parser.error("--aac glint and --aac-source must be supplied together")
    out.mkdir(parents=True)
    vendor, objects, options = vendor_translator(backend, args.source, out)
    host = stage_host(out, backend)
    aac_cmake = ""
    aac_library = "${FAAD_OBJS}"
    if args.aac == "glint":
        aac_vendor = glint_experiment.prepare(args.aac_source, out, host)
        aac_cmake = glint_experiment.cmake_library(aac_vendor) + glint_experiment.cmake_checks(aac_vendor)
        aac_library = "panthera_glint_decoder"

    (vendor / "panthera_jni.map").write_text(
        "{ global: Java_com_pantheraspeech_tts_PantheraNative_*; local: *; };\n", encoding="utf8")
    faad_include = ('"' + (ROOT / "android/harness/faad2/include").as_posix() + '"') if args.aac == "faad" else ""
    cmake = f'''
remove_definitions(-std=gnu11)
add_compile_options("$<$<COMPILE_LANGUAGE:C>:-std=gnu11>")
enable_language(CXX)
{aac_cmake}
file(GLOB FAAD_OBJS "{faad.as_posix()}/*.o")
function(panthera_host_target target mode)
    target_include_directories(${{target}} PRIVATE "{host.as_posix()}" {faad_include})
    target_compile_definitions(${{target}} PRIVATE TIGER_UC TIGER_INLINE_GUEST TIGER_{backend.upper()} TIGER_AAC_{args.aac.upper()} ${{mode}})
    target_compile_options(${{target}} PRIVATE -Wno-macro-redefined)
    target_link_libraries(${{target}} {objects} {aac_library} m dl log mediandk)
endfunction()
add_executable(panthera_memory_bench memory_{backend}.c)
target_include_directories(panthera_memory_bench PRIVATE "{ROOT.as_posix()}/android/harness/src")
target_link_libraries(panthera_memory_bench {objects} m dl)
add_executable(panthera_native_bench memory_native.c)
add_executable(panthera_engine_bench engine_bench.c {backend}_adapter.c "{host.as_posix()}/tiger_host.c")
panthera_host_target(panthera_engine_bench TIGER_SHARED)
add_executable(panthera_runtime_check runtime_check.c {backend}_adapter.c)
panthera_host_target(panthera_runtime_check TIGER_SHARED)
# Exercise Android's stderr-pump thread before translator initialization too.
# The shared-library benchmark alone does not reproduce app startup ordering.
add_executable(panthera_engine_logcat_bench engine_bench.c {backend}_adapter.c "{host.as_posix()}/tiger_host.c")
panthera_host_target(panthera_engine_logcat_bench TIGER_JNI)
# Isolated JNI artifact for device research; never copied into an APK here.
add_library(panthera SHARED {backend}_adapter.c "{host.as_posix()}/tiger_host.c"
    "{ROOT.as_posix()}/src/platforms/android/app/src/main/cpp/panthera_jni.cpp")
panthera_host_target(panthera TIGER_JNI)
target_compile_features(panthera PRIVATE cxx_std_17)
target_link_options(panthera PRIVATE "-Wl,--version-script={vendor.as_posix()}/panthera_jni.map" "-Wl,-z,max-page-size=16384" "-Wl,--no-undefined")
'''
    with (vendor / "CMakeLists.txt").open("a", encoding="utf8") as f:
        f.write(cmake)
    env = os.environ.copy()
    env["PATH"] = "C:/Program Files/Git/bin" + os.pathsep + env["PATH"]
    with (out / "build.log").open("w", encoding="utf8") as log:
        run([args.cmake, "-S", vendor, "-B", out / "build", "-G", "Ninja",
             f"-DCMAKE_MAKE_PROGRAM={args.cmake.parent.as_posix()}/ninja{args.cmake.suffix}",
             f"-DCMAKE_TOOLCHAIN_FILE={args.ndk.as_posix()}/build/cmake/android.toolchain.cmake",
             f"-DANDROID_ABI={abi}", f"-DANDROID_PLATFORM=android-{args.api}", "-DCMAKE_BUILD_TYPE=Release",
             "-DANDROID_STL=c++_static", "-DCMAKE_POSITION_INDEPENDENT_CODE=ON",
             f"-DPYTHON_EXECUTABLE={sys.executable}",
             *options], env=env, stdout=log, stderr=subprocess.STDOUT)
        checks = ["pcm16_check", "huffman_check"] if args.aac == "glint" else []
        run([args.cmake, "--build", out / "build", "--target", "panthera_engine_bench",
             "panthera_engine_logcat_bench", "panthera_memory_bench", "panthera_native_bench", "panthera_runtime_check", "panthera", *checks, "-j6"],
            env=env, stdout=log, stderr=subprocess.STDOUT)
    print(f"Built {backend} {PINS[backend]} for {abi}: {out / 'build'}")


if __name__ == "__main__":
    main()
