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
    replace(decoder, "    int nodes = 1;", """    int32_t prefix_node[256];
    uint8_t prefix_bits[256];
    int nodes = 1;""")
    replace(decoder,
        "    int decode(BitReader& br) const {\n        int at = 0;\n        for (int g = 0; g < 24; g++) {",
        """    // Panthera experiment: resolve up to eight bits at initialization.
    // Longer codes resume the same tree walk; truncated input keeps the
    // original bitwise path and its overrun/consumption behavior.
    void prepare_prefix() {
        for (int prefix = 0; prefix < 256; ++prefix) {
            int at = 0, bits = 0;
            do {
                at = child[at][(prefix >> (7 - bits)) & 1];
                ++bits;
            } while (at > 0 && bits < 8);
            prefix_node[prefix] = at;
            prefix_bits[prefix] = static_cast<uint8_t>(bits);
        }
    }
    int decode(BitReader& br) const {
        int at = 0, used = 0;
        if (br.left() >= 8) {
            unsigned byte = static_cast<unsigned>(br.pos) >> 3;
            unsigned offset = br.pos & 7;
            unsigned window = static_cast<unsigned>(br.data[byte]) << 8;
            if (offset) window |= br.data[byte + 1];
            unsigned prefix = (window >> (8 - offset)) & 255;
            used = prefix_bits[prefix];
            br.pos += used;
            at = prefix_node[prefix];
            if (at <= 0) return ~at;
        }
        for (int g = used; g < 24; g++) {""")
    replace(decoder, "    g_imdct_long.init(2048);", """    for (auto &tree : g_spec) tree.prepare_prefix();
    g_scf.prepare_prefix();
    g_imdct_long.init(2048);""")
    shutil.copy2(HERE / "glint_bridge.cpp", vendor)
    shutil.copy2(HERE / "pcm16.h", vendor)
    shutil.copy2(HERE / "tiger_host_aac_glint.c", host)
    replace(host / "tiger_host_aac.c", "#if defined(TIGER_AAC_FAAD)",
        '#if defined(TIGER_AAC_GLINT)\n#include "tiger_host_aac_glint.c"\n#elif defined(TIGER_AAC_FAAD)')
    replace(host / "tiger_host.c", "#elif defined(TIGER_AAC_FAAD)",
        '#elif defined(TIGER_AAC_GLINT)\n        const char *backend = "glint-experimental";\n#elif defined(TIGER_AAC_FAAD)')
    (vendor / "EXPERIMENT.txt").write_text(
        f"Glint {PIN}, MIT. Local cube-root cache and Huffman prefix table in src/aac_decoder.cpp.\n"
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


def cmake_checks(vendor):
    """Independent numeric/parser checks; no voice data or emulator required."""
    return f'''
add_executable(pcm16_check "{HERE.as_posix()}/pcm16_check.cpp")
target_compile_features(pcm16_check PRIVATE cxx_std_17)
add_executable(huffman_check "{HERE.as_posix()}/huffman_check.cpp")
target_include_directories(huffman_check PRIVATE "{vendor.as_posix()}/src")
target_compile_features(huffman_check PRIVATE cxx_std_17)
target_compile_definitions(huffman_check PRIVATE _USE_MATH_DEFINES)
enable_testing()
add_test(NAME pcm16_conversion COMMAND pcm16_check)
add_test(NAME huffman_prefix COMMAND huffman_check)
'''
