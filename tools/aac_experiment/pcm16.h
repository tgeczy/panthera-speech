#pragma once
#include <cstdint>
#include <cstring>
#include <limits>
#include <type_traits>

// Convert IEEE binary32/binary64 PCM to signed 16-bit, scaled by 2^15, with nearest-even
// rounding and saturation. Work on the significand directly: multiplication
// by a power of two is exact, and no per-sample libm call is necessary.
// Return false on NaN or infinity, as the decoder bridge previously did.
template<typename Sample>
inline bool pt_pcm16(const Sample *input, short *output, int count) {
    static_assert((std::is_same<Sample, float>::value || std::is_same<Sample, double>::value)
                  && std::numeric_limits<Sample>::is_iec559
                  && ((sizeof(Sample) == 4 && std::numeric_limits<Sample>::digits == 24)
                      || (sizeof(Sample) == 8 && std::numeric_limits<Sample>::digits == 53)),
                  "PCM conversion requires IEEE binary32 or binary64");
    static_assert(sizeof(short) == 2, "PCM output requires 16-bit short");
    using Bits = typename std::conditional<sizeof(Sample) == 4, uint32_t, uint64_t>::type;
    constexpr unsigned fraction_bits = std::numeric_limits<Sample>::digits - 1;
    constexpr unsigned bias = std::numeric_limits<Sample>::max_exponent - 1;
    constexpr unsigned exponent_mask = bias * 2 + 1;
    constexpr unsigned sign_bit = sizeof(Bits) * 8 - 1;
    constexpr Bits leading = Bits{1} << fraction_bits;
    for (int i = 0; i < count; ++i) {
        Bits bits;
        std::memcpy(&bits, input + i, sizeof(bits));
        unsigned exponent = static_cast<unsigned>((bits >> fraction_bits) & exponent_mask);
        if (exponent == exponent_mask) return false;
        if (exponent < bias - 16) {
            output[i] = 0; // magnitude below half a PCM unit, including subnormals
        } else if (exponent >= bias) {
            output[i] = bits >> sign_bit ? -32768 : 32767;
        } else {
            Bits significand = (bits & (leading - 1)) | leading;
            unsigned shift = fraction_bits + bias - 15 - exponent;
            unsigned magnitude = static_cast<unsigned>(significand >> shift);
            Bits remainder = significand & ((Bits{1} << shift) - 1);
            Bits halfway = Bits{1} << (shift - 1);
            magnitude += remainder > halfway ||
                         (remainder == halfway && (magnitude & 1));
            output[i] = bits >> sign_bit ? static_cast<short>(-static_cast<int>(magnitude))
                : static_cast<short>(magnitude > 32767 ? 32767 : magnitude);
        }
    }
    return true;
}
