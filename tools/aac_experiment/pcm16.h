#pragma once
#include <cstdint>
#include <cstring>
#include <limits>

// Convert IEEE binary32 PCM to signed 16-bit, scaled by 2^15, with nearest-even
// rounding and saturation. Work on the significand directly: multiplication
// by a power of two is exact, and no per-sample libm call is necessary.
// Return false on NaN or infinity, as the decoder bridge previously did.
inline bool pt_pcm16(const float *input, short *output, int count) {
    static_assert(sizeof(float) == 4 && std::numeric_limits<float>::is_iec559,
                  "PCM conversion requires IEEE binary32");
    static_assert(sizeof(short) == 2, "PCM output requires 16-bit short");
    for (int i = 0; i < count; ++i) {
        uint32_t bits;
        std::memcpy(&bits, input + i, sizeof(bits));
        unsigned exponent = (bits >> 23) & 255;
        if (exponent == 255) return false;
        if (exponent < 111) {
            output[i] = 0; // magnitude below half a PCM unit, including subnormals
        } else if (exponent >= 127) {
            output[i] = bits >> 31 ? -32768 : 32767;
        } else {
            unsigned significand = (bits & 0x7fffff) | 0x800000;
            unsigned shift = 135 - exponent; // 9..24 in this branch
            unsigned magnitude = significand >> shift;
            unsigned remainder = significand & ((1u << shift) - 1);
            unsigned halfway = 1u << (shift - 1);
            magnitude += remainder > halfway ||
                         (remainder == halfway && (magnitude & 1));
            output[i] = bits >> 31 ? static_cast<short>(-static_cast<int>(magnitude))
                : static_cast<short>(magnitude > 32767 ? 32767 : magnitude);
        }
    }
    return true;
}
