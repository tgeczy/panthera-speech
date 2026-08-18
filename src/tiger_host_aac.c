/* tiger_host_aac.c -- both converters, over the AAC decoder Windows ships.
 *
 * Part of tiger_host.c, which includes it; see there for why this is one
 * translation unit. */

/* ---- the Sound Manager's converter, as much as Vicki needs -------------- */
/*
 * `meow` -- Vicki's engine, and the precursor to Alex -- keeps its unit
 * database as AAC and decodes it through the Sound Manager.  The engine's own
 * MEOWQTDecoder::Decode(nBytes, data, nFrames, out) opens one converter per
 * voice and then, per unit, wraps the compressed bytes in a MEOWQTIterator and
 * pumps SoundConverterFillBuffer until the output buffer is full.
 *
 * The shape below is not guessed; it is read off the engine's own code:
 *
 *   SoundConverterFillBuffer(sc, upp, refCon, outputPtr, outputByteCount,
 *                            *actualOutputBytes, *actualOutputFrames,
 *                            *outputFlags)
 *
 * -- eight arguments, not the seven a first reading of Sound.h suggests.
 * Getting that wrong is what turned Vicki from silent into a crash last time:
 * argument five is a *count*, and treating it as the `actualOutputBytes`
 * pointer writes 46976 through a number.
 *
 * The fill callback hands back an *extended* descriptor: the engine sets
 * kExtendedSoundData (1 << 14) in `flags`, which means the fields past
 * `reserved` are live and describe VBR data --
 *
 *   +0x1c recordSize     68
 *   +0x20 extendedFlags  7 = sampleCountNotValid|bufferSizeValid|frameSizesValid
 *   +0x24 bufferSize     total compressed bytes
 *   +0x28 frameCount     number of AAC access units
 *   +0x2c frameSizes     long[frameCount], the size of each one
 *
 * -- which is why `sampleCount` is zero and always was: the engine declares it
 * invalid.  The compressed blob is a big-endian u16 size table followed by the
 * payload, and `buffer` already points past the table.
 *
 * The configuration arrives via SetInfo 'wave' as a QuickTime codec atom
 * carrying an `esds`, whose DecoderSpecificInfo is the two-byte
 * AudioSpecificConfig 0x1388 -- AAC-LC, 22050 Hz, mono, 1024-sample frames.
 *
 * The engine sizes its output buffer at frameCount * 1024 - 2112 frames, every
 * time, which says what Apple's converter did with the codec delay: it dropped
 * the 2112 priming samples and returned the rest.  Windows' decoder hands them
 * over, so we drop them here.  Measured rather than assumed -- see the trim
 * below, which takes the difference and never more than a priming's worth.
 */
#define SND_MAGIC       0x534e4443u            /* 'SNDC' */
#define ASC_MAX         64
#define AAC_FRAME       1024                   /* samples per access unit    */
#define AAC_PRIMING     2112                   /* Apple's AAC-LC codec delay */
#define kExtendedSoundData        (1 << 14)

typedef struct {
    long           flags;
    unsigned       format;
    short          numChannels;
    short          sampleSize;
    unsigned       sampleRate;
    long           sampleCount;
    unsigned char *buffer;
    long           reserved;
    /* live only when flags & kExtendedSoundData */
    long           recordSize;
    long           extendedFlags;
    long           bufferSize;
    long           frameCount;
    long          *frameSizes;
} snd_data;

/* Boolean, so the callee only sets AL -- reading the whole of EAX would make
 * "no more data" look like "more data" whenever the high bytes held junk. */
typedef unsigned char (__cdecl *fill_proc)(snd_data **data, void *refCon);

typedef struct {
    unsigned      magic;
    unsigned char asc[ASC_MAX];
    unsigned      asclen;
    unsigned      rate;
    unsigned      channels;
    short        *pcm;                  /* decoded, priming already dropped */
    unsigned      pcm_cap, pcm_n, pcm_pos;      /* in samples */
    unsigned      sessions;
    unsigned      lost;                 /* access units the decoder refused */
    int           complained;
    int           quiet;                /* one complaint per run is plenty  */
} sndconv;

static sndconv g_sc;

/* ---- AAC, through the decoder Windows already ships -------------------- */
/*
 * Bound at run time rather than linked: a Windows N install without the Media
 * Feature Pack has no mfplat.dll, and an import would stop the host loading at
 * all -- taking the other twenty-two voices down with it.  Missing here just
 * means Vicki renders silence, which is what she did before.
 */
typedef HRESULT (STDAPICALLTYPE *MFStartup_t)(ULONG, DWORD);
typedef HRESULT (STDAPICALLTYPE *MFCreateMediaType_t)(IMFMediaType **);
typedef HRESULT (STDAPICALLTYPE *MFCreateSample_t)(IMFSample **);
typedef HRESULT (STDAPICALLTYPE *MFCreateMemoryBuffer_t)(DWORD, IMFMediaBuffer **);

static MFStartup_t            p_MFStartup;
static MFCreateMediaType_t    p_MFCreateMediaType;
static MFCreateSample_t       p_MFCreateSample;
static MFCreateMemoryBuffer_t p_MFCreateMemoryBuffer;

/* CLSID_CMSAACDecMFT, spelled out rather than linked from wmcodecdspuuid.lib
 * so the build needs nothing beyond the base SDK. */
static const CLSID g_clsid_aac =
    { 0x32d186a7, 0x218f, 0x4c75,
      { 0x88, 0x76, 0xdd, 0x77, 0x27, 0x3a, 0x89, 0x99 } };

static IMFTransform *g_aac;
static int           g_aac_state;       /* 0 untried, 1 ready, -1 no decoder */
static LONGLONG      g_aac_time;

/* The engine decodes on its Multiprocessing worker, not on main. */
static __declspec(thread) int g_com_ready;

static void com_join(void)
{
    if (g_com_ready) return;
    CoInitializeEx(NULL, COINIT_MULTITHREADED);
    g_com_ready = 1;
}

/* 16-bit PCM at the voice's own rate.  Block alignment and bytes-per-second
 * are not optional here; a type without them is refused. */
static int aac_set_output(void)
{
    IMFMediaType *mt = NULL;
    DWORD i;
    HRESULT hr;
    if (SUCCEEDED(p_MFCreateMediaType(&mt))) {
        IMFMediaType_SetGUID(mt, &MF_MT_MAJOR_TYPE, &MFMediaType_Audio);
        IMFMediaType_SetGUID(mt, &MF_MT_SUBTYPE, &MFAudioFormat_PCM);
        IMFMediaType_SetUINT32(mt, &MF_MT_AUDIO_NUM_CHANNELS, g_sc.channels);
        IMFMediaType_SetUINT32(mt, &MF_MT_AUDIO_SAMPLES_PER_SECOND, g_sc.rate);
        IMFMediaType_SetUINT32(mt, &MF_MT_AUDIO_BITS_PER_SAMPLE, 16);
        IMFMediaType_SetUINT32(mt, &MF_MT_AUDIO_BLOCK_ALIGNMENT,
                               2 * g_sc.channels);
        IMFMediaType_SetUINT32(mt, &MF_MT_AUDIO_AVG_BYTES_PER_SECOND,
                               2 * g_sc.channels * g_sc.rate);
        IMFMediaType_SetUINT32(mt, &MF_MT_ALL_SAMPLES_INDEPENDENT, TRUE);
        hr = IMFTransform_SetOutputType(g_aac, 0, mt, 0);
        IMFMediaType_Release(mt);
        mt = NULL;
        if (SUCCEEDED(hr)) return 1;
        if (g_verbose) printf("  [aac] 16-bit PCM out refused (%08lx); enumerating\n",
               (unsigned long)hr);
    }
    for (i = 0; i < 16; i++) {
        UINT32 bits = 0, rate = 0, ch = 0;
        GUID sub;
        HRESULT ehr = IMFTransform_GetOutputAvailableType(g_aac, 0, i, &mt);
        if (FAILED(ehr) || !mt) {
            if (!i) printf("  [aac] no output types offered (%08lx)\n",
                           (unsigned long)ehr);
            break;
        }
        memset(&sub, 0, sizeof sub);
        IMFMediaType_GetGUID(mt, &MF_MT_SUBTYPE, &sub);
        IMFMediaType_GetUINT32(mt, &MF_MT_AUDIO_BITS_PER_SAMPLE, &bits);
        IMFMediaType_GetUINT32(mt, &MF_MT_AUDIO_SAMPLES_PER_SECOND, &rate);
        IMFMediaType_GetUINT32(mt, &MF_MT_AUDIO_NUM_CHANNELS, &ch);
        if (IsEqualGUID(&sub, &MFAudioFormat_PCM) && bits == 16 &&
            rate == g_sc.rate && ch == g_sc.channels &&
            SUCCEEDED(IMFTransform_SetOutputType(g_aac, 0, mt, 0))) {
            IMFMediaType_Release(mt);
            return 1;
        }
        IMFMediaType_Release(mt);
        mt = NULL;
    }
    return 0;
}

static int aac_open(void)
{
    HMODULE mf;
    IMFMediaType *mt = NULL;
    unsigned char ud[12 + ASC_MAX];
    HRESULT hr;

    if (g_aac_state) return g_aac_state > 0;
    g_aac_state = -1;                        /* pessimistic until it works */

    if (g_sc.asclen < 2) {
        if (g_verbose) printf("  [aac] no AudioSpecificConfig in the voice's 'wave' atom\n");
        return 0;
    }
    /* The config is not passed to the decoder (see below), but it does say
     * whether this is a stream the decoder can be asked for at all. */
    {
        static const unsigned asc_rates[13] = {
            96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050,
            16000, 12000, 11025, 8000, 7350 };
        unsigned obj = g_sc.asc[0] >> 3;
        unsigned idx = ((g_sc.asc[0] & 7) << 1) | (g_sc.asc[1] >> 7);
        unsigned chn = (g_sc.asc[1] >> 3) & 0xf;
        if (obj != 2)
            if (g_verbose) printf("  [aac] object type %u is not AAC-LC -- trying anyway\n", obj);
        if (idx < 13 && asc_rates[idx] != g_sc.rate)
            if (g_verbose) printf("  [aac] the config says %u Hz but the voice says %u\n",
                   asc_rates[idx], g_sc.rate);
        if (chn && chn != g_sc.channels)
            if (g_verbose) printf("  [aac] the config says %u channels but the voice says %u\n",
                   chn, g_sc.channels);
    }
    mf = LoadLibraryA("mfplat.dll");
    if (!mf) {
        if (g_verbose) printf("  [aac] no mfplat.dll on this system -- Vicki stays silent\n");
        return 0;
    }
    p_MFStartup = (MFStartup_t)GetProcAddress(mf, "MFStartup");
    p_MFCreateMediaType =
        (MFCreateMediaType_t)GetProcAddress(mf, "MFCreateMediaType");
    p_MFCreateSample = (MFCreateSample_t)GetProcAddress(mf, "MFCreateSample");
    p_MFCreateMemoryBuffer =
        (MFCreateMemoryBuffer_t)GetProcAddress(mf, "MFCreateMemoryBuffer");
    if (!p_MFStartup || !p_MFCreateMediaType || !p_MFCreateSample ||
        !p_MFCreateMemoryBuffer) {
        if (g_verbose) printf("  [aac] mfplat.dll is missing entry points\n");
        return 0;
    }
    com_join();
    if (FAILED(p_MFStartup(MF_VERSION, MFSTARTUP_LITE))) {
        if (g_verbose) printf("  [aac] MFStartup failed\n");
        return 0;
    }
    hr = CoCreateInstance(&g_clsid_aac, NULL, CLSCTX_INPROC_SERVER,
                          &IID_IMFTransform, (void **)&g_aac);
    if (FAILED(hr) || !g_aac) {
        if (g_verbose) printf("  [aac] no AAC decoder registered (%08lx)\n", (unsigned long)hr);
        return 0;
    }
    if (FAILED(p_MFCreateMediaType(&mt))) return 0;
    IMFMediaType_SetGUID(mt, &MF_MT_MAJOR_TYPE, &MFMediaType_Audio);
    IMFMediaType_SetGUID(mt, &MF_MT_SUBTYPE, &MFAudioFormat_AAC);
    IMFMediaType_SetUINT32(mt, &MF_MT_AUDIO_BITS_PER_SAMPLE, 16);
    IMFMediaType_SetUINT32(mt, &MF_MT_AUDIO_SAMPLES_PER_SECOND, g_sc.rate);
    IMFMediaType_SetUINT32(mt, &MF_MT_AUDIO_NUM_CHANNELS, g_sc.channels);
    IMFMediaType_SetUINT32(mt, &MF_MT_AAC_PAYLOAD_TYPE, 0);   /* raw blocks */
    /* HEAACWAVEINFO past its WAVEFORMATEX -- and *only* that.  Appending the
     * AudioSpecificConfig, which is what the documentation describes and what
     * every example does, makes this decoder ignore the sample rate and the
     * channel count it was just given and fall back to 44100 stereo; it then
     * refuses 22050 mono out.  Measured across six recipes: the bare twelve
     * bytes is the one that configures it from the media type.  Vicki's
     * config says the same thing the media type does, so nothing is lost. */
    memset(ud, 0, sizeof ud);
    ud[2] = 0xfe;                            /* profile-level: unspecified */
    IMFMediaType_SetBlob(mt, &MF_MT_USER_DATA, ud, 12);
    hr = IMFTransform_SetInputType(g_aac, 0, mt, 0);
    IMFMediaType_Release(mt);
    if (FAILED(hr)) {
        if (g_verbose) printf("  [aac] the decoder refused %u Hz %u ch AAC (%08lx)\n",
               g_sc.rate, g_sc.channels, (unsigned long)hr);
        return 0;
    }
    if (!aac_set_output()) {
        if (g_verbose) printf("  [aac] the decoder refused 16-bit PCM out\n");
        return 0;
    }
    IMFTransform_ProcessMessage(g_aac, MFT_MESSAGE_NOTIFY_BEGIN_STREAMING, 0);
    IMFTransform_ProcessMessage(g_aac, MFT_MESSAGE_NOTIFY_START_OF_STREAM, 0);
    if (g_verbose) {
        unsigned i;
        if (g_verbose) printf("  [aac] Windows' AAC decoder ready: %u Hz, %u ch, ASC",
               g_sc.rate, g_sc.channels);
        for (i = 0; i < g_sc.asclen; i++) printf(" %02x", g_sc.asc[i]);
        printf("\n");
    }
    g_aac_state = 1;
    return 1;
}

/* `tiger_host --aac-check`: does this machine's AAC decoder behave like the
 * one Vicki was measured against?  Needs no engine, no voices and no
 * arguments, so it is something a user can be asked to run and paste back --
 * which is the only way to tell "she sounds wrong here" from "she sounds wrong
 * everywhere". */
static int aac_check(void)
{
    DWORD i;
    IMFMediaType *mt = NULL;
    static const unsigned char asc[2] = { 0x13, 0x88 };

    g_verbose = 1;
    g_sc.rate = 22050;
    g_sc.channels = 1;
    memcpy(g_sc.asc, asc, 2);
    g_sc.asclen = 2;

    printf("tiger_host AAC check\n");
    if (!aac_open()) {
        printf("\nRESULT: no usable AAC decoder -- Vicki cannot speak here,\n"
               "and the driver should not be offering her.\n");
        return 1;
    }
    printf("  input accepted at %u Hz, %u channel(s)\n",
           g_sc.rate, g_sc.channels);
    for (i = 0; i < 8; i++) {
        UINT32 bits = 0, rate = 0, ch = 0;
        GUID sub;
        if (FAILED(IMFTransform_GetOutputAvailableType(g_aac, 0, i, &mt)) || !mt)
            break;
        memset(&sub, 0, sizeof sub);
        IMFMediaType_GetGUID(mt, &MF_MT_SUBTYPE, &sub);
        IMFMediaType_GetUINT32(mt, &MF_MT_AUDIO_BITS_PER_SAMPLE, &bits);
        IMFMediaType_GetUINT32(mt, &MF_MT_AUDIO_SAMPLES_PER_SECOND, &rate);
        IMFMediaType_GetUINT32(mt, &MF_MT_AUDIO_NUM_CHANNELS, &ch);
        printf("  offers #%lu: fmt %08lx  %u bit  %u Hz  %u ch\n",
               (unsigned long)i, (unsigned long)sub.Data1, bits, rate, ch);
        IMFMediaType_Release(mt);
        mt = NULL;
    }
    printf("\nRESULT: decoder present and configured. If Vicki still sounds\n"
           "wrong here, it is the frame counts that differ -- select her in\n"
           "NVDA, speak a sentence, and send the NVDA log: the host writes a\n"
           "line there saying so.\n");
    return 0;
}

static void pcm_append(const unsigned char *p, unsigned bytes)
{
    unsigned need = g_sc.pcm_n + bytes / 2;
    if (need > g_sc.pcm_cap) {
        unsigned cap = g_sc.pcm_cap ? g_sc.pcm_cap : 65536;
        short *grown;
        while (cap < need) cap *= 2;
        grown = (short *)realloc(g_sc.pcm, (size_t)cap * 2);
        if (!grown) return;                  /* silence beats a crash */
        g_sc.pcm = grown;
        g_sc.pcm_cap = cap;
    }
    memcpy(g_sc.pcm + g_sc.pcm_n, p, bytes & ~1u);
    g_sc.pcm_n += bytes / 2;
}

/* Take everything the transform is holding.  The AAC decoder does not supply
 * its own samples, so the buffer is ours to provide. */
static void aac_drain(void)
{
    MFT_OUTPUT_STREAM_INFO si;
    memset(&si, 0, sizeof si);
    IMFTransform_GetOutputStreamInfo(g_aac, 0, &si);
    for (;;) {
        MFT_OUTPUT_DATA_BUFFER ob;
        IMFSample *s = NULL;
        IMFMediaBuffer *b = NULL;
        DWORD status = 0, cb = si.cbSize ? si.cbSize : 65536;
        HRESULT hr;
        if (FAILED(p_MFCreateSample(&s))) return;
        if (FAILED(p_MFCreateMemoryBuffer(cb, &b))) {
            IMFSample_Release(s);
            return;
        }
        IMFSample_AddBuffer(s, b);
        memset(&ob, 0, sizeof ob);
        ob.pSample = s;
        hr = IMFTransform_ProcessOutput(g_aac, 0, 1, &ob, &status);
        if (SUCCEEDED(hr)) {
            BYTE *p = NULL;
            DWORD len = 0;
            if (SUCCEEDED(IMFMediaBuffer_Lock(b, &p, NULL, &len))) {
                pcm_append(p, len);
                IMFMediaBuffer_Unlock(b);
            }
        }
        if (ob.pEvents) IMFCollection_Release(ob.pEvents);
        IMFMediaBuffer_Release(b);
        IMFSample_Release(s);
        if (hr == MF_E_TRANSFORM_STREAM_CHANGE) {
            /* The decoder wants to restate its output format; say PCM again. */
            if (!aac_set_output()) return;
            continue;
        }
        if (FAILED(hr)) return;              /* NEED_MORE_INPUT lands here */
    }
}

static int aac_feed(const unsigned char *data, unsigned len)
{
    IMFSample *s = NULL;
    IMFMediaBuffer *b = NULL;
    BYTE *p = NULL;
    HRESULT hr;
    int tries;
    if (FAILED(p_MFCreateSample(&s))) return 0;
    if (FAILED(p_MFCreateMemoryBuffer(len, &b))) { IMFSample_Release(s); return 0; }
    if (SUCCEEDED(IMFMediaBuffer_Lock(b, &p, NULL, NULL))) {
        memcpy(p, data, len);
        IMFMediaBuffer_Unlock(b);
    }
    IMFMediaBuffer_SetCurrentLength(b, len);
    IMFSample_AddBuffer(s, b);
    IMFSample_SetSampleTime(s, g_aac_time);
    IMFSample_SetSampleDuration(s, 10000000LL * AAC_FRAME / g_sc.rate);
    g_aac_time += 10000000LL * AAC_FRAME / g_sc.rate;
    /* A transform holding finished output refuses new input with
     * MF_E_NOTACCEPTING.  Dropping the access unit there would be silent and
     * ruinous: every later unit would sit 1024 samples out of place, which is
     * not silence but *wrong* speech.  Drain and offer it again. */
    for (tries = 0; tries < 8; tries++) {
        hr = IMFTransform_ProcessInput(g_aac, 0, s, 0);
        if (hr != MF_E_NOTACCEPTING) break;
        aac_drain();
    }
    if (SUCCEEDED(hr)) aac_drain();
    IMFMediaBuffer_Release(b);
    IMFSample_Release(s);
    return SUCCEEDED(hr);
}

/* Set TIGER_AAC_DUMP to a path and the first unit's access units are written
 * there as an ADTS stream, which any other decoder will read.  That is how the
 * PCM below was checked against something that is not Media Foundation. */
static void aac_dump_adts(const snd_data *in)
{
    static int done;
    const char *path = getenv("TIGER_AAC_DUMP");
    static const unsigned rates[13] = { 96000, 88200, 64000, 48000, 44100,
        32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350 };
    unsigned idx = 7, off = 0;
    long i;
    FILE *f;
    if (!path || done) return;
    done = 1;
    for (i = 0; i < 13; i++) if (rates[i] == g_sc.rate) idx = (unsigned)i;
    f = fopen(path, "wb");
    if (!f) return;
    for (i = 0; i < in->frameCount; i++) {
        unsigned sz = (unsigned)in->frameSizes[i], len = sz + 7;
        unsigned char h[7];
        if (!sz || off + sz > (unsigned)in->bufferSize) break;
        h[0] = 0xff;
        h[1] = 0xf1;                          /* MPEG-4, no CRC */
        h[2] = (unsigned char)((1 << 6) | (idx << 2) |
                               ((g_sc.channels >> 2) & 1));
        h[3] = (unsigned char)(((g_sc.channels & 3) << 6) | ((len >> 11) & 3));
        h[4] = (unsigned char)((len >> 3) & 0xff);
        h[5] = (unsigned char)(((len & 7) << 5) | 0x1f);
        h[6] = 0xfc;
        fwrite(h, 1, 7, f);
        fwrite(in->buffer + off, 1, sz, f);
        off += sz;
    }
    fclose(f);
    if (g_verbose) printf("  [aac] wrote %ld access units to %s\n", in->frameCount, path);
}

/* One unit of the voice's database: `n` access units laid end to end, with
 * their sizes alongside.  Everything is decoded here and doled out to the
 * engine afterwards, because the engine's own loop wants it that way. */
/* Start and finish one run of packets.  Split out because Alex arrives through
 * AudioConverter and Vicki through the Sound Manager, and only the plumbing
 * differs -- the decoder underneath is the same one. */
static void aac_begin(void)
{
    g_sc.pcm_n = g_sc.pcm_pos = 0;
    IMFTransform_ProcessMessage(g_aac, MFT_MESSAGE_COMMAND_FLUSH, 0);
    IMFTransform_ProcessMessage(g_aac, MFT_MESSAGE_NOTIFY_START_OF_STREAM, 0);
    g_aac_time = 0;
}

/* Push the decoder's own latency out of it before draining.
 *
 * Windows 7 returns exactly 1024 frames fewer than the stream holds -- one AAC
 * frame, every time, with nothing refused -- because it keeps the last frame
 * back rather than dropping the first.  Windows 10 and 11 return the lot.
 * Working the priming out from how much arrived therefore gives a different
 * answer on each, and on Windows 7 it starts the audio 1024 samples early,
 * which is what "her syllables run together" sounds like.
 *
 * So do not work it out.  Feed the last packet again a couple of times and
 * throw away what comes back: a decoder holding a frame then lets the real
 * final frame go, and every decoder has produced at least the whole stream.
 * The priming is 2112 on all of them after that -- a constant, not a
 * measurement, which is the whole point.
 *
 * Decoding a packet twice cannot disturb what came before it: an AAC frame
 * depends on the frame before it, never on the one after. */
static void aac_flush_delay(const unsigned char *last, unsigned lastlen)
{
    int i;
    if (!last || !lastlen) return;
    for (i = 0; i < 2; i++) aac_feed(last, lastlen);
}

static void aac_end(void)
{
    IMFTransform_ProcessMessage(g_aac, MFT_MESSAGE_NOTIFY_END_OF_STREAM, 0);
    IMFTransform_ProcessMessage(g_aac, MFT_MESSAGE_COMMAND_DRAIN, 0);
    aac_drain();
}

static void aac_run_unit(const snd_data *in)
{
    unsigned off = 0, lastoff = 0, lastlen = 0;
    long i;
    aac_begin();
    for (i = 0; i < in->frameCount; i++) {
        unsigned sz = (unsigned)in->frameSizes[i];
        if (!sz || off + sz > (unsigned)in->bufferSize) break;
        if (!aac_feed(in->buffer + off, sz)) g_sc.lost++;
        lastoff = off;
        lastlen = sz;
        off += sz;
    }
    aac_flush_delay(in->buffer + lastoff, lastlen);
    aac_end();
}

static void aac_decode_unit(const snd_data *in)
{
    unsigned target, trim, full;

    aac_dump_adts(in);

    g_sc.pcm_n = g_sc.pcm_pos = 0;
    if (!aac_open()) return;
    aac_run_unit(in);

    /* A complete decode is frameCount whole frames.  Coming up short means
     * this transform is in a state we did not put it in, so throw it away and
     * decode the unit again on a new one -- once.  Cheaper than being wrong,
     * and it only ever runs when something is already amiss. */
    full = (unsigned)in->frameCount * AAC_FRAME;
    if (g_sc.pcm_n < full && g_aac) {
        IMFTransform_Release(g_aac);
        g_aac = NULL;
        g_aac_state = 0;
        g_sc.lost = 0;
        if (aac_open()) aac_run_unit(in);
    }

    { static int dumped;
      const char *path = getenv("TIGER_AAC_DUMP");
      if (path && !dumped) {
          char pcmpath[512];
          FILE *f;
          dumped = 1;
          _snprintf(pcmpath, sizeof(pcmpath), "%s.pcm", path);
          pcmpath[sizeof(pcmpath) - 1] = 0;
          f = fopen(pcmpath, "wb");
          if (f) {
              fwrite(g_sc.pcm, 2, g_sc.pcm_n, f);
              fclose(f);
              if (g_verbose) printf("  [aac] wrote %u untrimmed frames to %s\n",
                     g_sc.pcm_n, pcmpath);
          }
      }
    }

    /* Drop the codec delay.  The engine asks for exactly
     * frameCount * 1024 - 2112 frames, which is Apple's AAC priming written
     * into the arithmetic, and the priming sits at the front of the stream.
     *
     * This used to be worked out from how much the decoder handed over, which
     * is precisely the thing that differs between versions of Windows.  Now
     * that `aac_flush_delay` has pushed each decoder's own latency out, the
     * answer is the same everywhere and can simply be stated. */
    {
        target = full > AAC_PRIMING ? full - AAC_PRIMING : 0;
        trim = AAC_PRIMING;
        if (trim > g_sc.pcm_n) trim = g_sc.pcm_n;
        g_sc.pcm_pos = trim;

        if (g_sc.pcm_n < full || g_sc.lost) {
            /* Not gated on verbosity: this is the one thing worth saying out
             * loud in serve mode, because it is what a user hears as "she
             * speaks, but wow". */
            if (g_sc.complained < 3) {
                g_sc.complained++;
                fprintf(stderr,
                        "tiger_host: AAC decoder returned %u frames for %ld "
                        "units, expected %u (%u short); %u access unit(s) "
                        "refused. Vicki will sound wrong on this machine.\n",
                        g_sc.pcm_n, in->frameCount, full, full - g_sc.pcm_n,
                        g_sc.lost);
            }
        } else if (g_verbose && g_sc.sessions <= 3) {
            if (g_verbose) printf("  [aac] unit %ld units -> %u frames, want %u, dropping %u\n",
                   in->frameCount, g_sc.pcm_n, target, trim);
        }
    }
}

/* Walk the QuickTime atom tree for the esds, then its DecoderSpecificInfo.
 * Descriptor lengths are 7-bit continuation encoded, which is why the 0x80
 * bytes appear between tags. */
static const unsigned char *desc_len(const unsigned char *p, unsigned *out)
{
    unsigned v = 0;
    int i;
    for (i = 0; i < 4; i++) {
        unsigned char b = *p++;
        v = (v << 7) | (b & 0x7f);
        if (!(b & 0x80)) break;
    }
    *out = v;
    return p;
}

static void grab_asc(const unsigned char *wave, unsigned len)
{
    unsigned off = 0;
    while (off + 8 <= len) {
        unsigned size = (wave[off] << 24) | (wave[off+1] << 16) |
                        (wave[off+2] << 8) | wave[off+3];
        const unsigned char *tag = wave + off + 4;
        if (size < 8 || off + size > len) break;
        if (!memcmp(tag, "esds", 4)) {
            const unsigned char *p = wave + off + 12;   /* skip version/flags */
            const unsigned char *end = wave + off + size;
            while (p < end) {
                unsigned char t = *p++;
                unsigned l;
                p = desc_len(p, &l);
                if (t == 0x03) { p += 3; continue; }    /* ES_Descr header */
                if (t == 0x04) { p += 13; continue; }   /* DecoderConfig hdr */
                if (t == 0x05) {                        /* DecSpecificInfo */
                    if (l > ASC_MAX) l = ASC_MAX;
                    memcpy(g_sc.asc, p, l);
                    g_sc.asclen = l;
                    return;
                }
                p += l;
            }
        }
        off += size;
    }
}

/* CoreAudio hands the same information over as a "magic cookie" rather than a
 * QuickTime atom, and what is inside it varies: sometimes the whole `esds`
 * atom, sometimes just the ES descriptor, sometimes the bare
 * AudioSpecificConfig.  Try each, widest first. */
static void grab_cookie(const unsigned char *p, unsigned len)
{
    if (!p || !len) return;
    grab_asc(p, len);
    if (g_sc.asclen) return;
    if (p[0] == 0x03) {                        /* a bare ES_Descriptor */
        const unsigned char *q = p + 1, *end = p + len;
        unsigned l;
        q = desc_len(q, &l);
        q += 3;
        while (q < end) {
            unsigned char t = *q++;
            q = desc_len(q, &l);
            if (t == 0x04) { q += 13; continue; }
            if (t == 0x05) {
                if (l > ASC_MAX) l = ASC_MAX;
                memcpy(g_sc.asc, q, l);
                g_sc.asclen = l;
                return;
            }
            q += l;
        }
    }
    if (len >= 2 && len <= ASC_MAX) {          /* the config, on its own */
        memcpy(g_sc.asc, p, len);
        g_sc.asclen = len;
    }
}

static int __cdecl sh_SoundConverterClose(void *sc)
{ (void)sc; return 0; }

static void * __cdecl sh_NewFillBufferUPP(void *proc)
{ return proc; }                        /* a UPP is just the pointer here */

static void __cdecl sh_DisposeFillBufferUPP(void *upp)
{ (void)upp; }

static int __cdecl sh_SoundConverterBeginConversion(void *sc)
{
    (void)sc;
    g_sc.sessions++;
    g_sc.pcm_n = g_sc.pcm_pos = 0;
    return 0;
}

static int __cdecl sh_SoundConverterFillBuffer(void *sc, fill_proc upp,
                                               void *refcon, void *outbuf,
                                               unsigned outbytes,
                                               unsigned *actualbytes,
                                               unsigned *actualframes,
                                               unsigned *outflags)
{
    unsigned give = 0;
    (void)sc;
    if (g_sc.pcm_pos >= g_sc.pcm_n) {           /* need another blob */
        snd_data *in = NULL;
        g_sc.pcm_n = g_sc.pcm_pos = 0;
        if (upp && upp(&in, refcon) && in && in->buffer) {
            if (!(in->flags & kExtendedSoundData) || in->recordSize < 68 ||
                !in->frameSizes || in->frameCount <= 0) {
                if (!g_sc.quiet++)
                    if (g_verbose) printf("  [snd] fill: flags %08lx recordSize %ld -- not the "
                           "extended VBR descriptor this expects\n",
                           in->flags, in->recordSize);
            } else {
                aac_decode_unit(in);
            }
        }
    }
    if (g_sc.pcm_pos < g_sc.pcm_n) {
        give = (g_sc.pcm_n - g_sc.pcm_pos) * 2;
        if (give > outbytes) give = outbytes & ~1u;
        memcpy(outbuf, g_sc.pcm + g_sc.pcm_pos, give);
        g_sc.pcm_pos += give / 2;
    }
    if (actualbytes) *actualbytes = give;
    if (actualframes) *actualframes = give / 2;
    /* Bit 1 is kSoundConverterHasLeftOverData: the engine's loop reads it as
     * "there is more where that came from, ask again". */
    if (outflags) *outflags = (g_sc.pcm_pos < g_sc.pcm_n) ? 2 : 0;
    return 0;
}

static int __cdecl sh_SoundConverterConvertBuffer(void *sc, const void *in,
                                                  unsigned inframes, void *out,
                                                  unsigned *outframes,
                                                  unsigned *outbytes)
{
    (void)sc; (void)in; (void)out;
    if (g_verbose) printf("  [snd] ConvertBuffer %u frames -- the fixed-rate path, which no "
           "voice here has ever taken\n", inframes);
    if (outframes) *outframes = 0;
    if (outbytes) *outbytes = 0;
    return 0;
}

static int __cdecl sh_SoundConverterEndConversion(void *sc, void *outbuf,
                                                  unsigned *outframes,
                                                  unsigned *outbytes)
{
    /* Nothing is held back: FillBuffer drained the decoder before returning. */
    (void)sc; (void)outbuf;
    if (outframes) *outframes = 0;
    if (outbytes) *outbytes = 0;
    return 0;
}

/* ---- AudioConverter: the same decoder, the API Alex uses --------------- */
/*
 * Vicki goes through the Sound Manager; Alex goes through CoreAudio.  The
 * engine has one decoder class for each -- `MEOWQTDecoder` and
 * `MEOWACDecoder` -- and their `Decode(nBytes, data, nFrames, out)` methods
 * are the same shape, so everything below feeds the same AAC decoder as the
 * SoundConverter side above.
 *
 * Read off MEOWACDecoder's constructor and Decode:
 *
 *   AudioConverterNew(&sourceASBD, &destASBD, &conv)
 *   AudioConverterSetProperty(conv, 'prmm', 4, {2})     kConverterPrimeMethod_None
 *   AudioConverterSetProperty(conv, 'dmgc', n, cookie)  the AAC magic cookie
 *   AudioFormatGetProperty('fexf', 40, &sourceASBD, &4, &flag)
 *   AudioConverterFillComplexBuffer(conv, proc, iterator,
 *                                   &packets, &bufferList, NULL)
 *
 * The destination format is a static ASBD in the engine's __DATA: 22050 Hz
 * 'lpcm', 1 channel, 16 bits, one frame per packet.
 *
 * `fexf` is kAudioFormatProperty_FormatIsExternallyFramed, and the answer
 * decides everything after it: TRUE makes the engine pass -1 as its frame
 * size, which is what puts MEOWACIterator into its packet-table branch.  AAC
 * is variable rate, so TRUE is the truthful answer as well as the useful one.
 */
#define AC_MAGIC 0x41434e56u                   /* 'ACNV' */

typedef struct { unsigned mNumberChannels, mDataByteSize; void *mData; } au_buffer;
typedef struct { unsigned mNumberBuffers; au_buffer mBuffers[1]; } au_bufferlist;
typedef struct {
    long long mStartOffset;
    unsigned  mVariableFramesInPacket;
    unsigned  mDataByteSize;
} au_packetdesc;
typedef struct {
    double   mSampleRate;
    unsigned mFormatID, mFormatFlags, mBytesPerPacket, mFramesPerPacket;
    unsigned mBytesPerFrame, mChannelsPerFrame, mBitsPerChannel, mReserved;
} au_asbd;

typedef int (__cdecl *ac_input_proc)(void *conv, unsigned *ioPackets,
                                     au_bufferlist *ioData,
                                     au_packetdesc **outDesc, void *user);

/* The source description arrives **big-endian**, straight out of the voice
 * file, and the engine passes it through without swapping.  On PowerPC that is
 * native and correct; on Intel it is not, which is a fair sign this decoder
 * was never exercised in Tiger's own Intel build -- Tiger ships no voice that
 * uses it.  Alex does, so the swapping has to happen somewhere, and here is
 * the honest place: we are the CoreAudio implementation.
 *
 * Decided once for the whole record rather than per field, by asking which
 * reading gives a believable channel count. */
static void asbd_native(const au_asbd *src, au_asbd *dst)
{
    const unsigned char *b = (const unsigned char *)src;
    unsigned le = src->mChannelsPerFrame, be = bswap(le);
    int i;
    *dst = *src;
    if ((le < 1 || le > 64) && be >= 1 && be <= 64) {
        unsigned char *d = (unsigned char *)dst;
        for (i = 0; i < 8; i++) d[i] = b[7 - i];          /* the Float64 */
        dst->mFormatID        = bswap(src->mFormatID);
        dst->mFormatFlags     = bswap(src->mFormatFlags);
        dst->mBytesPerPacket  = bswap(src->mBytesPerPacket);
        dst->mFramesPerPacket = bswap(src->mFramesPerPacket);
        dst->mBytesPerFrame   = bswap(src->mBytesPerFrame);
        dst->mChannelsPerFrame = be;
        dst->mBitsPerChannel  = bswap(src->mBitsPerChannel);
    }
}

static int __cdecl sh_AudioConverterNew(const au_asbd *insrc,
                                        const au_asbd *out, void **conv)
{
    au_asbd native;
    const au_asbd *in = NULL;
    if (insrc) { asbd_native(insrc, &native); in = &native; }
    if (in) {
        unsigned rate = (unsigned)in->mSampleRate;
        unsigned ch = in->mChannelsPerFrame ? in->mChannelsPerFrame : 1;
        char f[5];
        if (!rate) rate = 22050;
        fourcc(f, in->mFormatID);
        /* A converter for a different rate cannot reuse the old transform. */
        if (g_aac && (rate != g_sc.rate || ch != g_sc.channels)) {
            IMFTransform_Release(g_aac);
            g_aac = NULL;
            g_aac_state = 0;
        }
        g_sc.rate = rate;
        g_sc.channels = ch;
        if (g_verbose) {
            const unsigned char *b = (const unsigned char *)in;
            int k;
            if (g_verbose) printf("  [ac] New: '%s' %u Hz %u ch -> %u Hz %u ch\n", f, rate, ch,
                   out ? (unsigned)out->mSampleRate : 0,
                   out ? out->mChannelsPerFrame : 0);
            if (g_verbose) printf("  [ac] source ASBD:");
            for (k = 0; k < 40; k++) printf(" %02x", b[k]);
            printf("\n");
        }
    }
    if (conv) *conv = (void *)AC_MAGIC;
    return 0;
}

static int __cdecl sh_AudioConverterDispose(void *conv) { (void)conv; return 0; }

static int __cdecl sh_AudioConverterReset(void *conv)
{
    (void)conv;
    g_sc.pcm_n = g_sc.pcm_pos = 0;
    return 0;
}

static int __cdecl sh_AudioConverterSetProperty(void *conv, unsigned sel,
                                                unsigned size, const void *data)
{
    char f[5];
    (void)conv;
    fourcc(f, sel);
    if (sel == 0x646d6763u && data && size)    /* 'dmgc' -- the magic cookie */
        grab_cookie((const unsigned char *)data, size);
    if (g_verbose) printf("  [ac] SetProperty '%s' (%u bytes)\n", f, size);
    return 0;
}

static int __cdecl sh_AudioConverterGetProperty(void *conv, unsigned sel,
                                                unsigned *iosize, void *out)
{
    (void)conv; (void)sel;
    if (iosize) *iosize = 0;
    (void)out;
    return -50;                                /* paramErr: we have none */
}

static int __cdecl sh_AudioConverterGetPropertyInfo(void *conv, unsigned sel,
                                                    unsigned *size, int *writable)
{
    (void)conv; (void)sel;
    if (size) *size = 0;
    if (writable) *writable = 0;
    return -50;
}

static int __cdecl sh_AudioFormatGetProperty(unsigned sel, unsigned specsize,
                                             const void *spec, unsigned *iosize,
                                             void *out)
{
    (void)specsize; (void)spec;
    if (sel == 0x66657866u) {                  /* 'fexf' externally framed */
        if (out && iosize && *iosize >= 4) *(unsigned *)out = 1;
        if (iosize) *iosize = 4;
        return 0;
    }
    if (iosize) *iosize = 0;
    return -50;
}

/* The engine's callback hands over one blob and the packet boundaries inside
 * it, then reports no more.  Decode the lot, then dole it out: the engine
 * calls back until it has the frames it asked for. */
static int __cdecl sh_AudioConverterFillComplexBuffer(void *conv,
                                                      ac_input_proc proc,
                                                      void *user,
                                                      unsigned *iopackets,
                                                      au_bufferlist *outdata,
                                                      au_packetdesc *outdesc)
{
    unsigned want, give = 0;
    (void)conv; (void)outdesc;
    if (!iopackets || !outdata || !outdata->mNumberBuffers) return -50;
    want = *iopackets;

    if (g_sc.pcm_pos >= g_sc.pcm_n && proc) {
        au_bufferlist in;
        au_packetdesc *descs = NULL;
        unsigned packets = 0;
        memset(&in, 0, sizeof in);
        in.mNumberBuffers = 1;
        g_sc.pcm_n = g_sc.pcm_pos = 0;
        proc(conv, &packets, &in, &descs, user);
        if (packets && descs && in.mBuffers[0].mData && aac_open()) {
            const unsigned char *base =
                (const unsigned char *)in.mBuffers[0].mData;
            const unsigned char *lastpkt = NULL;
            unsigned lastlen = 0;
            unsigned i;
            aac_begin();
            for (i = 0; i < packets; i++) {
                if (descs[i].mStartOffset < 0 || !descs[i].mDataByteSize) continue;
                if ((unsigned)descs[i].mStartOffset + descs[i].mDataByteSize >
                    in.mBuffers[0].mDataByteSize &&
                    in.mBuffers[0].mDataByteSize)
                    break;
                if (!aac_feed(base + (unsigned)descs[i].mStartOffset,
                              descs[i].mDataByteSize))
                    g_sc.lost++;
                lastpkt = base + (unsigned)descs[i].mStartOffset;
                lastlen = descs[i].mDataByteSize;
            }
            /* NOT aac_flush_delay() here, though Vicki needs it.
             *
             * That trick re-feeds the last packet twice to shake the decoder's
             * own held frames loose. On Vicki's single long stream the
             * duplicates land past the end of the utterance and are never
             * handed out. Alex's grains are decoded one AAC packet at a time,
             * so re-feeding the packet *is* the payload: one grain came back
             * three times over, and what reached the engine was the third
             * copy. Real Alex bytes, wrong ones, blipping in and out.
             *
             * A drain alone gets everything out of this decoder. */
            (void)lastpkt; (void)lastlen;
            aac_end();
            /* Nothing is trimmed here, which is the other half of the same
             * fact.  Apple sets kAudioConverterPrimeMethod to None on this
             * converter -- that is the 'prmm' SetProperty above -- because a
             * grain is stored as a complete little AAC sequence with no
             * priming to discard.  One packet in, 1024 samples out, and all of
             * them wanted.
             *
             * Trimming Vicki's 2112 here would delete every grain twice over,
             * and it is tempting: it is the same codec, the same decoder, and
             * the constant is sitting right there in this file. */
            if (g_verbose && g_sc.sessions <= 3)
                printf("  [ac] %u packet(s) -> %u frames, asked for %u\n",
                       packets, g_sc.pcm_n, want);
        }
    }

    if (g_sc.pcm_pos < g_sc.pcm_n) {
        give = g_sc.pcm_n - g_sc.pcm_pos;
        if (give > want) give = want;
        if (give * 2 > outdata->mBuffers[0].mDataByteSize)
            give = outdata->mBuffers[0].mDataByteSize / 2;
        memcpy(outdata->mBuffers[0].mData, g_sc.pcm + g_sc.pcm_pos, give * 2);
        g_sc.pcm_pos += give;
    }
    outdata->mBuffers[0].mDataByteSize = give * 2;
    *iopackets = give;
    return 0;
}

/* Opened once per voice, at load time.  `SoundComponentData` is
 * {long flags; OSType format; short channels; short sampleSize;
 *  UnsignedFixed sampleRate; long sampleCount; Byte *buffer; long reserved}. */
static int __cdecl sh_SoundConverterOpen(const unsigned char *in,
                                         const unsigned char *out, void **sc)
{
    int k;
    const unsigned char *p[2];
    p[0] = in; p[1] = out;
    for (k = 0; k < 2; k++) {
        char f[5];
        if (!p[k]) { printf("  [snd] %s format: NULL\n", k ? "out" : "in"); continue; }
        fourcc(f, *(const unsigned *)(p[k] + 4));
        if (g_verbose) printf("  [snd] %-3s format '%s'  %d ch  %d bits  rate %.1f\n",
               k ? "out" : "in", f, *(const short *)(p[k] + 8),
               *(const short *)(p[k] + 10),
               *(const unsigned *)(p[k] + 12) / 65536.0);
    }
    /* The decoder is configured from the voice's own numbers rather than from
     * the AudioSpecificConfig, which only carries a sample-rate index. */
    if (in) {
        unsigned ch = (unsigned)*(const short *)(in + 8);
        unsigned rate = *(const unsigned *)(in + 12) >> 16;
        if (!ch) ch = 1;
        if (!rate) rate = 22050;
        /* A second AAC voice at a different rate would otherwise be decoded
         * with the first one's decoder.  Tiger has only Vicki, but Leopard's
         * Alex uses this same engine. */
        if (g_aac && (ch != g_sc.channels || rate != g_sc.rate)) {
            IMFTransform_Release(g_aac);
            g_aac = NULL;
            g_aac_state = 0;
        }
        g_sc.channels = ch;
        g_sc.rate = rate;
    }
    if (sc) *sc = (void *)SND_MAGIC;       /* 'SNDC', a handle we never use */
    return 0;
}

/* What the engine tells the converter before decoding.  'wave' is
 * siDecompressionParams: a QuickTime sound description extension whose `esds`
 * carries the AudioSpecificConfig, which is exactly what an AAC decoder needs
 * to be configured -- and is why none of this had to be written by hand. */
static int __cdecl sh_SoundConverterSetInfo(void *sc, unsigned sel,
                                            const void *data)
{
    char f[5];
    fourcc(f, sel);
    if (sel == 0x77617665u && data)          /* 'wave' */
        grab_asc((const unsigned char *)data, 256);
    if (g_verbose) printf("  [snd] SetInfo '%s'\n", f);
    (void)sc;
    return 0;
}

static int __cdecl sh_AudioUnitReset(void *u, unsigned s, unsigned e)
{ (void)u; (void)s; (void)e; printf("  [au] Reset\n"); return 0; }
static int __cdecl sh_SpeechBusy(void) { return 0; }
