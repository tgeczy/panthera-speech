/* tiger_host_aac_mf.c -- the AAC decoder Windows already ships.
 *
 * One of the interchangeable backends behind tiger_host_aac.c, which includes
 * exactly one of them.  The converter shims, the priming arithmetic and the
 * PCM sink live there and are the same whoever decodes; this file is only the
 * decode.  The Android one is tiger_host_aac_ndk.c.
 *
 * What a backend owes tiger_host_aac.c:
 *
 *     aac_open()       -> 1 once a decoder is configured from g_sc.asc, else 0
 *     aac_begin()      -- start of one unit: reset the PCM cursor, flush state
 *     aac_feed(d,n)    -> 1 if the access unit was accepted
 *     aac_drain()      -- hand every finished sample to pcm_append()
 *     aac_end_stream() -- end of stream: flush the decoder and drain it
 *     aac_check()      -> 0 healthy, nonzero with a reason on stderr
 *
 * and nothing else: no backend touches g_sc.pcm directly, and none of them
 * knows what a unit means.  aac_flush_delay and the TIGER_SIM_WIN7 arithmetic
 * are deliberately not here -- they are statements about AAC rather than about
 * a decoder, and duplicating them per platform is how two backends come to
 * disagree about where a voice starts.
 */
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
            if (!i) fprintf(stderr, "  [aac] no output types offered (%08lx)\n",
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

/* End of stream: tell the decoder no more is coming and take what it holds.
 *
 * Only the flush; the priming arithmetic that decides how much of what comes
 * back is real audio lives in tiger_host_aac.c, where both backends share it. */
static void aac_end_stream(void)
{
    IMFTransform_ProcessMessage(g_aac, MFT_MESSAGE_NOTIFY_END_OF_STREAM, 0);
    IMFTransform_ProcessMessage(g_aac, MFT_MESSAGE_COMMAND_DRAIN, 0);
    aac_drain();
}

/* Is there a decoder right now?  The shared side asks before deciding whether
 * a change of rate or channel count means the current one is the wrong one. */
static int aac_is_open(void)
{
    return g_aac != NULL;
}

/* Throw the decoder away; the next aac_open builds a fresh one.
 *
 * Two callers, both of them "this decoder is not the one we want": a second
 * AAC voice at another sample rate (Tiger has only Vicki, but Leopard's Alex
 * is the same engine), and a unit that came back short, which means the
 * transform is in a state we did not put it in. */
static void aac_reset_decoder(void)
{
    if (g_aac) IMFTransform_Release(g_aac);
    g_aac = NULL;
    g_aac_state = 0;
}

/* Drop what the decoder is holding without ending the stream. */
static void aac_flush_now(void)
{
    if (g_aac) IMFTransform_ProcessMessage(g_aac, MFT_MESSAGE_COMMAND_FLUSH, 0);
}
