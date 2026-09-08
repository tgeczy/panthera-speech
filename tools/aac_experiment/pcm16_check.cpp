#include "pcm16.h"
#include <cfenv>
#include <cmath>
#include <cstdio>
#include <vector>

static short reference(float value) {
    double scaled = static_cast<double>(value) * 32768.0;
    return scaled >= 32767 ? 32767 : scaled <= -32768 ? -32768
        : static_cast<short>(std::lrint(scaled));
}

int main() {
    std::fesetenv(FE_DFL_ENV);
    std::vector<float> input;
    // Every half-way boundary in the output range, including both adjacent
    // floats: catches tie direction, sign, clipping, and one-unit errors.
    for (int i = -32769; i <= 32768; ++i) {
        float mid = static_cast<float>((i + 0.5) / 32768.0);
        input.push_back(mid);
        input.push_back(std::nextafter(mid, -INFINITY));
        input.push_back(std::nextafter(mid, INFINITY));
    }
    // All exponent/sign combinations, selected mantissas, then arbitrary bits.
    for (unsigned exponent = 0; exponent < 255; ++exponent) {
        for (unsigned sign : {0u, 0x80000000u}) {
            for (unsigned fraction : {0u, 1u, 0x3fffffu, 0x400000u, 0x7ffffeu, 0x7fffffu}) {
                uint32_t bits = sign | (exponent << 23) | fraction;
                float value;
                std::memcpy(&value, &bits, sizeof(value));
                input.push_back(value);
            }
        }
    }
    uint32_t random = 0x27af914bu;
    for (int i = 0; i < 1000000; ++i) {
        random ^= random << 13; random ^= random >> 17; random ^= random << 5;
        if (((random >> 23) & 255) == 255) continue;
        float value;
        std::memcpy(&value, &random, sizeof(value));
        input.push_back(value);
    }
    std::vector<short> expected, actual(input.size());
    for (float value : input) expected.push_back(reference(value));
    for (int mode : {FE_TONEAREST, FE_DOWNWARD, FE_UPWARD, FE_TOWARDZERO}) {
        std::fesetround(mode);
        if (!pt_pcm16(input.data(), actual.data(), static_cast<int>(input.size())) || actual != expected) {
            std::fprintf(stderr, "FAIL PCM conversion under rounding mode %d\n", mode);
            return 1;
        }
        if (std::fegetround() != mode) return 2;
    }
    for (uint32_t bits : {0x7f800000u, 0xff800000u, 0x7fc00000u, 0xffc00000u,
                          0x7f800001u, 0xff800001u}) {
        float invalid;
        std::memcpy(&invalid, &bits, sizeof(invalid));
        short output;
        if (pt_pcm16(&invalid, &output, 1)) return 3;
    }
    if (!pt_pcm16(nullptr, nullptr, 0)) return 4;
    std::printf("PASS %zu finite samples in four rounding modes; clipping, ties, NaN/infinity rejection\n", input.size());
    return 0;
}
