"""Prepare a pinned, isolated Glint decoder experiment; never vendor its source."""
import io
from pathlib import Path
import shutil
import subprocess
import tarfile

PIN = "77738f3ed9b15f627196cc5bbd7f6406814ba2fb"
HERE = Path(__file__).resolve().parent


def replace(path, old, new):
    source = path.read_text(encoding="utf8")
    if source.count(old) != 1:
        raise RuntimeError(f"Expected one patch site: {path}: {old}")
    path.write_text(source.replace(old, new), encoding="utf8")


def prepare(source, out, host):
    """Extract only decoder sources and license from the pin, ignoring local edits."""
    archive = subprocess.check_output(["git", "-C", str(source), "archive", PIN,
        "LICENSE", "src/aac_decoder.cpp", "src/aac_decoder.hpp", "src/aac_tables.hpp"])
    vendor = out / "glint"
    vendor.mkdir()
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(vendor, filter="data")
    decoder = vendor / "src/aac_decoder.cpp"
    replace(decoder, "#include <cmath>", "#include <cmath>\n#include <array>")
    replace(decoder,
        "            coef_[ch][i] = (v < 0 ? -1 : 1) * a * std::cbrt(a) * gain;",
        """            // Panthera experiment: cache the same integer cube roots.
            // Preserve multiplication order; escape values use the old path.
            static const std::array<double, 8192> roots = [] {
                std::array<double, 8192> table{};
                for (unsigned j = 0; j < table.size(); ++j)
                    table[j] = std::cbrt(static_cast<double>(j));
                return table;
            }();
            double root = a < roots.size() && a == static_cast<unsigned>(a)
                              ? roots[static_cast<unsigned>(a)] : std::cbrt(a);
            coef_[ch][i] = (v < 0 ? -1 : 1) * a * root * gain;""")
    shutil.copy2(HERE / "glint_bridge.cpp", vendor)
    shutil.copy2(HERE / "tiger_host_aac_glint.c", host)
    replace(host / "tiger_host_aac.c", "#if defined(TIGER_AAC_FAAD)",
        '#if defined(TIGER_AAC_GLINT)\n#include "tiger_host_aac_glint.c"\n#elif defined(TIGER_AAC_FAAD)')
    replace(host / "tiger_host.c", "#elif defined(TIGER_AAC_FAAD)",
        '#elif defined(TIGER_AAC_GLINT)\n        const char *backend = "glint-experimental";\n#elif defined(TIGER_AAC_FAAD)')
    (vendor / "EXPERIMENT.txt").write_text(
        f"Glint {PIN}, MIT. Local cube-root cache in src/aac_decoder.cpp.\n"
        "Experimental decoder; not approved for release. See tools/aac_experiment/README.md.\n",
        encoding="utf8")
    return vendor


def cmake_library(vendor):
    path = vendor.as_posix()
    return f'''
add_library(panthera_glint_decoder STATIC "{path}/src/aac_decoder.cpp" "{path}/glint_bridge.cpp")
target_include_directories(panthera_glint_decoder PRIVATE "{path}/src")
target_compile_features(panthera_glint_decoder PRIVATE cxx_std_17)
target_compile_definitions(panthera_glint_decoder PRIVATE _USE_MATH_DEFINES)
set_target_properties(panthera_glint_decoder PROPERTIES POSITION_INDEPENDENT_CODE ON)
'''
