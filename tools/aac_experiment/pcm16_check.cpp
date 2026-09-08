#include "pcm16.h"
#include <cfenv>
#include <cmath>
#include <cstdio>
#include <vector>

template<typename Sample>
static short reference(Sample value) {
    double scaled = static_cast<double>(value) * 32768.0;
    return scaled >= 32767 ? 32767 : scaled <= -32768 ? -32768
        : static_cast<short>(std::lrint(scaled));
}

template<typename Sample, typename Bits>
static int check() {
    std::fesetenv(FE_DFL_ENV);
    constexpr unsigned fraction_bits = std::numeric_limits<Sample>::digits - 1;
    constexpr unsigned exponent_mask = std::numeric_limits<Sample>::max_exponent * 2 - 1;
    constexpr Bits sign_bit = Bits{1} << (sizeof(Bits) * 8 - 1);
    constexpr Bits fraction_top = Bits{1} << (fraction_bits - 1);
    std::vector<Sample> input;
    // Every half-way boundary in the output range, including both adjacent
    // representable values: catches tie direction, sign, clipping, and one-unit errors.
    for (int i = -32769; i <= 32768; ++i) {
        Sample mid = static_cast<Sample>((i + 0.5) / 32768.0);
        input.push_back(mid);
        input.push_back(std::nextafter(mid, -std::numeric_limits<Sample>::infinity()));
        input.push_back(std::nextafter(mid, std::numeric_limits<Sample>::infinity()));
    }
    // All exponent/sign combinations, selected mantissas, then arbitrary bits.
    for (unsigned exponent = 0; exponent < exponent_mask; ++exponent) {
        for (Bits sign : {Bits{0}, sign_bit}) {
            for (Bits fraction : {Bits{0}, Bits{1}, fraction_top-1, fraction_top, fraction_top*2-2, fraction_top*2-1}) {
                Bits bits = sign | (Bits{exponent} << fraction_bits) | fraction;
                Sample value;
                std::memcpy(&value, &bits, sizeof(value));
                input.push_back(value);
            }
        }
    }
    Bits random = 0x27af914bu;
    for (int i = 0; i < 1000000; ++i) {
        random ^= random << 13; random ^= random >> 17; random ^= random << 5;
        if (((random >> fraction_bits) & exponent_mask) == exponent_mask) continue;
        Sample value;
        std::memcpy(&value, &random, sizeof(value));
        input.push_back(value);
    }
    std::vector<short> expected, actual(input.size());
    for (Sample value : input) expected.push_back(reference(value));
    for (int mode : {FE_TONEAREST, FE_DOWNWARD, FE_UPWARD, FE_TOWARDZERO}) {
        std::fesetround(mode);
        if (!pt_pcm16(input.data(), actual.data(), static_cast<int>(input.size())) || actual != expected) {
            std::fprintf(stderr, "FAIL PCM conversion under rounding mode %d\n", mode);
            return 1;
        }
        if (std::fegetround() != mode) return 2;
    }
    for (Bits sign : {Bits{0}, sign_bit}) {
        for (Bits fraction : {Bits{0}, Bits{1}, fraction_top}) {
            Bits bits = sign | (Bits{exponent_mask} << fraction_bits) | fraction;
            Sample invalid;
            std::memcpy(&invalid, &bits, sizeof(invalid));
            short output;
            if (pt_pcm16(&invalid, &output, 1)) return 3;
        }
    }
    if (!pt_pcm16<Sample>(nullptr, nullptr, 0)) return 4;
    std::printf("PASS binary%zu: %zu finite samples in four rounding modes; clipping, ties, NaN/infinity rejection\n", sizeof(Sample)*8, input.size());
    return 0;
}

int main() {
    int status = check<float, uint32_t>();
    return status ? status : check<double, uint64_t>();
}
