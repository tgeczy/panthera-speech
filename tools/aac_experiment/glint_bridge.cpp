#include "aac_decoder.hpp"
#include <new>
#include <cmath>
#include <cstdint>
#include <cfenv>

// A native i386 guest may leave a different floating-point environment.
// The host decoder owns its arithmetic; restore the caller's state on exit.
struct DecoderFloatEnvironment {
    fenv_t saved;
    DecoderFloatEnvironment() { std::fegetenv(&saved); std::fesetenv(FE_DFL_ENV); }
    ~DecoderFloatEnvironment() { std::fesetenv(&saved); }
};

extern "C" void *pt_glint_open(void) {
    DecoderFloatEnvironment fp;
    try {
        auto *d = new glint::aac::AacDecoder();
        d->init();
        return d;
    } catch (...) { return nullptr; }
}
extern "C" void pt_glint_close(void *p) {
    delete static_cast<glint::aac::AacDecoder *>(p);
}
extern "C" int pt_glint_decode(void *p, const unsigned char *data, int len,
                                short *out, unsigned rate, unsigned channels) {
    DecoderFloatEnvironment fp;
    try {
        float pcm[2048];
        glint::aac::AacFrameInfo info;
        int n = static_cast<glint::aac::AacDecoder *>(p)->decode_frame(data, len, pcm, &info);
        if (n != 1024 || info.sample_rate != (int)rate ||
            info.channels != (int)channels || info.frame_bytes != len) return -1;
        for (int i = 0; i < n * info.channels; ++i) {
            double x = pcm[i] * 32768.0;
            if (!std::isfinite(x)) return -2;
            out[i] = x >= 32767 ? 32767 : x <= -32768 ? -32768 : (short)std::lrint(x);
        }
        return n * info.channels;
    } catch (...) { return -3; }
}
