/* tiger_host_aac_ndk.c -- the AAC decoder Android already ships.
 *
 * The other backend behind tiger_host_aac.c; see tiger_host_aac_mf.c for the
 * contract, which is six functions and no reaching into g_sc.pcm.  Android has
 * had a hardware or software AAC decoder behind AMediaCodec since 4.1, so this
 * carries none: the same rule as Media Foundation on Windows and winsqlite3 in
 * tiger_host_sqlite.c -- use what is on the machine, redistribute nothing.
 *
 * Three things differ from Media Foundation and each one is a way to get
 * silence that looks like success:
 *
 *  - MediaCodec is a *pipeline*, not a call.  Input and output are separate
 *    queues, and the first two or three access units can come back with no
 *    output at all before several arrive at once.  So aac_feed does not expect
 *    a packet in to mean a packet out, and every drain loops until the codec
 *    says try-again rather than until it has what it wanted.
 *
 *  - It wants the AudioSpecificConfig as csd-0 and raw AAC-LC access units.
 *    Media Foundation reads ADTS headers; this must not be given them.  The
 *    engine's units are already raw -- aac_dump_adts adds the headers only for
 *    the debug dump -- so both backends feed exactly the same bytes and only
 *    the configuration differs.
 *
 *  - flush() empties *both* queues, including finished output nobody has
 *    collected yet.  aac_begin flushes, so anything still owed from the
 *    previous unit has to have been drained before it, which aac_end does.
 *    After a flush the codec is still started; calling start() again is an
 *    error, and the error is quiet.
 */
#include <media/NdkMediaCodec.h>
#include <media/NdkMediaFormat.h>

/* One decoder, kept across units.  Creating one costs tens of milliseconds on
 * a watch and the engine opens a converter per voice but decodes per unit, so
 * building it each time would be most of the render. */
static AMediaCodec *g_aac;
static int          g_aac_state;        /* 0 untried, 1 ready, -1 no decoder */
static int64_t      g_aac_time;         /* presentation time, microseconds */
static int          g_aac_eos;          /* end-of-stream has been signalled */

/* How long to wait for a buffer.  Input is nearly always ready, so a short
 * wait there costs nothing; output needs long enough for the codec to have
 * produced something but not so long that a drain with nothing left stalls the
 * render -- the loop below leaves on the first try-again either way. */
#define NDK_IN_TIMEOUT   10000          /* 10 ms */
#define NDK_OUT_TIMEOUT   5000          /*  5 ms */

static int aac_is_open(void)
{
    return g_aac != NULL;
}

static void aac_reset_decoder(void)
{
    if (g_aac) {
        AMediaCodec_stop(g_aac);
        AMediaCodec_delete(g_aac);
    }
    g_aac = NULL;
    g_aac_state = 0;
    g_aac_eos = 0;
}

/* Take everything the codec has finished with.
 *
 * Loops rather than reading once: a decoder that has been fed several access
 * units may have several buffers ready, and leaving one behind would put every
 * later unit 1024 samples out of place -- not silence but wrong speech, which
 * is the failure aac_feed's Media Foundation counterpart guards against too. */
static void aac_drain(void)
{
    for (;;) {
        AMediaCodecBufferInfo info;
        ssize_t idx = AMediaCodec_dequeueOutputBuffer(g_aac, &info,
                                                      NDK_OUT_TIMEOUT);
        if (idx >= 0) {
            size_t cap = 0;
            uint8_t *buf = AMediaCodec_getOutputBuffer(g_aac, (size_t)idx, &cap);
            if (buf && info.size > 0)
                pcm_append(buf + info.offset, (unsigned)info.size);
            AMediaCodec_releaseOutputBuffer(g_aac, (size_t)idx, false);
            if (info.flags & AMEDIACODEC_BUFFER_FLAG_END_OF_STREAM) return;
            continue;
        }
        if (idx == AMEDIACODEC_INFO_OUTPUT_FORMAT_CHANGED) {
            /* The codec states its real output format once, before the first
             * buffer.  Anything but 16-bit PCM would be appended as though it
             * were, so say so rather than produce noise. */
            AMediaFormat *of = AMediaCodec_getOutputFormat(g_aac);
            if (of) {
                int32_t enc = 2;         /* ENCODING_PCM_16BIT */
                if (AMediaFormat_getInt32(of, "pcm-encoding", &enc) && enc != 2)
                    fprintf(stderr, "  [aac] decoder is producing pcm-encoding "
                                    "%d, not 16-bit; audio will be wrong\n", enc);
                AMediaFormat_delete(of);
            }
            continue;
        }
        if (idx == AMEDIACODEC_INFO_OUTPUT_BUFFERS_CHANGED) continue;
        return;                          /* try-again, or an error: we are done */
    }
}

static int aac_open(void)
{
    AMediaFormat *fmt;
    media_status_t st;

    if (g_aac_state) return g_aac_state > 0;
    g_aac_state = -1;                    /* pessimistic until it works */

    if (g_sc.asclen < 2) {
        fprintf(stderr, "  [aac] no AudioSpecificConfig in the voice's "
                        "'wave' atom\n");
        return 0;
    }
    g_aac = AMediaCodec_createDecoderByType("audio/mp4a-latm");
    if (!g_aac) {
        fprintf(stderr, "  [aac] this device has no AAC decoder\n");
        return 0;
    }
    fmt = AMediaFormat_new();
    if (!fmt) { aac_reset_decoder(); return 0; }
    AMediaFormat_setString(fmt, AMEDIAFORMAT_KEY_MIME, "audio/mp4a-latm");
    AMediaFormat_setInt32(fmt, AMEDIAFORMAT_KEY_SAMPLE_RATE, (int32_t)g_sc.rate);
    AMediaFormat_setInt32(fmt, AMEDIAFORMAT_KEY_CHANNEL_COUNT,
                          (int32_t)(g_sc.channels ? g_sc.channels : 1));
    /* csd-0 is the AudioSpecificConfig, and it is what tells the codec these
     * are raw access units rather than ADTS.  Without it the first queued
     * buffer is rejected as a bad frame and every unit decodes to nothing. */
    AMediaFormat_setBuffer(fmt, "csd-0", g_sc.asc, g_sc.asclen);

    st = AMediaCodec_configure(g_aac, fmt, NULL, NULL, 0);
    AMediaFormat_delete(fmt);
    if (st != AMEDIA_OK) {
        fprintf(stderr, "  [aac] configure failed (%d) for %u Hz, %u ch\n",
                (int)st, g_sc.rate, g_sc.channels);
        aac_reset_decoder();
        return 0;
    }
    if (AMediaCodec_start(g_aac) != AMEDIA_OK) {
        fprintf(stderr, "  [aac] the decoder would not start\n");
        aac_reset_decoder();
        return 0;
    }
    g_aac_time = 0;
    g_aac_eos = 0;
    g_aac_state = 1;
    return 1;
}

static int aac_feed(const unsigned char *data, unsigned len)
{
    ssize_t idx;
    size_t cap = 0;
    uint8_t *buf;
    if (!g_aac) return 0;
    idx = AMediaCodec_dequeueInputBuffer(g_aac, NDK_IN_TIMEOUT);
    if (idx < 0) {
        /* Every input buffer is held by output nobody has taken.  Dropping the
         * access unit here would be silent and ruinous, so take the output and
         * ask once more. */
        aac_drain();
        idx = AMediaCodec_dequeueInputBuffer(g_aac, NDK_IN_TIMEOUT);
        if (idx < 0) return 0;
    }
    buf = AMediaCodec_getInputBuffer(g_aac, (size_t)idx, &cap);
    if (!buf || cap < len) {
        AMediaCodec_queueInputBuffer(g_aac, (size_t)idx, 0, 0, g_aac_time, 0);
        return 0;
    }
    memcpy(buf, data, len);
    if (AMediaCodec_queueInputBuffer(g_aac, (size_t)idx, 0, len,
                                     (uint64_t)g_aac_time, 0) != AMEDIA_OK)
        return 0;
    g_aac_time += 1000000LL * AAC_FRAME / (g_sc.rate ? g_sc.rate : 22050);
    aac_drain();
    return 1;
}

static void aac_begin(void)
{
    g_sc.pcm_n = g_sc.pcm_pos = 0;
    if (g_aac) {
        /* Also the only way back from an end-of-stream: a codec that has been
         * told the stream ended refuses further input until it is flushed. */
        AMediaCodec_flush(g_aac);
        g_aac_eos = 0;
    }
    g_aac_time = 0;
}

static void aac_end_stream(void)
{
    ssize_t idx;
    if (!g_aac) return;
    if (!g_aac_eos) {
        idx = AMediaCodec_dequeueInputBuffer(g_aac, NDK_IN_TIMEOUT);
        if (idx < 0) { aac_drain(); idx = AMediaCodec_dequeueInputBuffer(g_aac, NDK_IN_TIMEOUT); }
        if (idx >= 0) {
            AMediaCodec_queueInputBuffer(g_aac, (size_t)idx, 0, 0,
                                         (uint64_t)g_aac_time,
                                         AMEDIACODEC_BUFFER_FLAG_END_OF_STREAM);
            g_aac_eos = 1;
        }
    }
    aac_drain();
}

static void aac_flush_now(void)
{
    if (g_aac) { AMediaCodec_flush(g_aac); g_aac_eos = 0; }
}

static int aac_check(void)
{
    AMediaCodec *c = AMediaCodec_createDecoderByType("audio/mp4a-latm");
    if (!c) {
        fprintf(stderr, "RESULT: this device has no AAC decoder, so Vicki and\n"
                        "Alex cannot be decoded here.  Every formant voice --\n"
                        "Fred and the rest -- is unaffected.\n");
        return 2;
    }
    AMediaCodec_delete(c);
    fprintf(stderr, "RESULT: an AAC decoder is available.  If a voice still\n"
                    "sounds wrong, it is the frame counts that differ: check\n"
                    "the priming line in logcat against 2112.\n");
    return 0;
}
