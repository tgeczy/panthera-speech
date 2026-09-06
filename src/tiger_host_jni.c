/* tiger_host_jni.c -- the in-process synthesis API (see tiger_host_jni.h).
 *
 * Part of tiger_host.c, which includes it; one translation unit, so it reaches
 * host_open, the engine exports, and the audio globals directly.  This is the
 * render-to-buffer path the Android TTS service drives: the same bring-up the
 * one-shot render and serve mode use, wrapped so Kotlin can call it, producing
 * exactly the PCM write_wav would -- so the WAV oracle checks the APK too.
 */

/* host_open maps every image and is a once-per-process step; guard it. */
static int          g_pt_ready;
static volatile int g_pt_stop;      /* set by panthera_stop, read by the poll */

/* In an app, the engine's stderr diagnostics (its "[au] slice" commentary and
 * every error) go nowhere.  Pump them into logcat so a silent render can be
 * read.  Android-only; the desktop --jni-check build keeps its real stderr. */
#ifdef TIGER_JNI
#include <android/log.h>
static void *pt_log_pump(void *arg)
{
    int fd = (int)(intptr_t)arg, used = 0, n;
    char buf[512];
    while ((n = (int)read(fd, buf + used, (size_t)(sizeof(buf) - 1 - used))) > 0) {
        char *line, *nl;
        used += n; buf[used] = 0;
        line = buf;
        while ((nl = strchr(line, '\n')) != 0) {
            *nl = 0;
            __android_log_write(ANDROID_LOG_INFO, "PantheraEngine", line);
            line = nl + 1;
        }
        used = (int)strlen(line);
        memmove(buf, line, (size_t)used + 1);
    }
    return 0;
}
static void pt_stderr_to_logcat(void)
{
    static int done = 0;
    int pfd[2];
    pthread_t t;
    if (done) return;
    done = 1;
    if (pipe(pfd) != 0) return;
    dup2(pfd[1], 2);                 /* the engine's stderr -> the pipe */
    if (pthread_create(&t, 0, pt_log_pump, (void *)(intptr_t)pfd[0]) == 0)
        pthread_detach(t);
}
#endif

/* Only the emulated build has per-thread guest engines to ensure; a native
 * (Linux x86) build calls the i386 engine directly, so this is a no-op there.
 * The synthesis API is otherwise identical -- which is what lets it feed ALSA
 * or PipeWire on the Linux port as readily as AudioTrack on Android: it hands
 * back raw PCM and the caller owns playback. */
#ifdef TIGER_UC
#define PT_ENSURE_ENGINE() uc_ensure_engine()
#else
#define PT_ENSURE_ENGINE() ((void)0)
#endif

static unsigned g_pull_pos;          /* streaming read cursor into g_pcm */
static unsigned g_pt_voice_creator;  /* last voice selected, to skip re-selecting */
static int      g_pt_voice_id;
static int      g_pt_have_voice;

/* Clamp one float sample to int16, the way write_wav does. */
static short pt_clip(double v)
{
    if (v > 1.0) v = 1.0;
    if (v < -1.0) v = -1.0;
    return (short)(v * 32767.0);
}

/* Reset the audio-path state for a new utterance.  Mirrors serve mode's reset;
 * the AAC/SQL/streaming counters it also clears are inert on the Fred build. */
static void pt_utterance_reset(void)
{
    g_pcm_n = 0; g_slices = 0; g_stopped = 0; g_empty_run = 0;
    g_dup_slices = 0; g_have_last = 0; g_p_drops = 0;
    g_epoch_base = 0; g_last_stime = 0.0; g_have_origin = 0;
    g_utt++; g_stale_slices = 0; g_pull_pos = 0;
}

/* Select the voice, but only when it actually changes -- reloading a voice
 * preset is real work, and doing it on every utterance (a screen reader sends
 * many) both wastes time and churns channel state.  serve mode does the same.
 * Returns 0, or the engine's OSErr. */
static int pt_use_voice(const char *voiceDir, unsigned creator, int voiceId)
{
    typedef int (__cdecl *SEUseVoice_t)(void *, const void *, const void *);
    SEUseVoice_t use;
    struct { unsigned creator; int id; } spec;
    int err;
    if (g_pt_have_voice && creator == g_pt_voice_creator && voiceId == g_pt_voice_id)
        return 0;
    use = (SEUseVoice_t)find_export(&g_mt, "_SEUseVoice");
    if (!use) return -2;
    spec.creator = creator; spec.id = voiceId;
    err = call_aligned3((void *)use, g_chan, UC_IN(&spec, sizeof spec),
                        cf_pinned(voiceDir));
    if (err) { fprintf(stderr, "panthera: SEUseVoice -> OSErr %d\n", err); return err; }
    g_pt_voice_creator = creator; g_pt_voice_id = voiceId; g_pt_have_voice = 1;
    return 0;
}

/* write_wav's float->int16, factored so a rendered utterance is byte-identical
 * to the desktop WAV.  Fills out[0..g_pcm_n) and returns the frame count. */
static unsigned pcm_to_i16(short *out)
{
    unsigned i;
    for (i = 0; i < g_pcm_n; i++) {
        double v = g_pcm[i];
        if (v > 1.0) v = 1.0;
        if (v < -1.0) v = -1.0;
        out[i] = (short)(v * 32767.0);
    }
    return g_pcm_n;
}

int panthera_init(const char *mtPath, const char *sdPath)
{
    if (g_pt_ready) return 0;
#ifdef TIGER_JNI
    pt_stderr_to_logcat();          /* capture the engine's own diagnostics */
#endif
    /* host_open brings the emulator up (uc_host_init reserves the guest block)
     * and ensures THIS thread's engine -- so, unlike render/stop below, it must
     * NOT be preceded by uc_ensure_engine: there is no arena to map yet. */
    {
        int e = host_open(mtPath, sdPath);
        if (e) return e;
    }
#ifdef TIGER_JNI
    g_verbose = 0;                  /* the per-slice commentary floods logcat */
#endif
    g_pt_ready = 1;
    return 0;
}

int panthera_sample_rate(void)
{
    return (int)(g_rate + 0.5);
}

int panthera_voice_spec(const char *voiceDir, unsigned *creator, int *voiceId)
{
    return voice_spec(voiceDir, creator, voiceId);   /* serve.c: reads the file */
}

void panthera_stop(void)
{
    PT_ENSURE_ENGINE();
    g_pt_stop = 1;
    if (g_pt_ready) {
        SEStop_t stop = (SEStop_t)find_export(&g_mt, "_SEStopSpeechAt");
        if (stop) call_aligned2((void *)stop, g_chan, (void *)0); /* stop now */
    }
}

/* ---- streaming synthesis (the low-latency TTS path) -------------------- */

int panthera_speak_start(const char *voiceDir, unsigned creator, int voiceId,
                         const char *text, int wpm)
{
    speech_api api;
    int err;
    PT_ENSURE_ENGINE();
    if (!g_pt_ready) return -1;
    g_pt_stop = 0;
    pt_utterance_reset();
    err = pt_use_voice(voiceDir, creator, voiceId);
    if (err) return err;
    api = speech_api_of(&g_mt);
    if (wpm > 0) set_param(&api, g_chan, PARAM_RATE, (unsigned)wpm << 16);
    err = speak_text(&api, g_chan, text, (unsigned)strlen(text));
    if (err) fprintf(stderr, "panthera: SESpeakBuffer -> OSErr %d\n", err);
    return err;   /* synthesis now runs on the engine's worker; drain with pull */
}

int panthera_pull(short *out, int maxSamples)
{
    int idle = 0;
    /* No PT_ENSURE_ENGINE: this only reads g_pcm, a host-side float buffer the
     * engine's worker fills -- it never enters the guest, so it needs no engine
     * on this thread. */
    if (!g_pt_ready || maxSamples <= 0) return 0;
    for (;;) {
        unsigned avail = (g_pcm_n > g_pull_pos) ? (g_pcm_n - g_pull_pos) : 0;
        if (g_pt_stop) return 0;
        if (avail > 0) {
            unsigned n = avail < (unsigned)maxSamples ? avail : (unsigned)maxSamples;
            unsigned i;
            for (i = 0; i < n; i++) out[i] = pt_clip(g_pcm[g_pull_pos + i]);
            g_pull_pos += n;
            return (int)n;
        }
        if (g_stopped) return 0;              /* utterance finished, all drained */
        Sleep(5);
        if (++idle > 2000) return 0;          /* ~10 s with no audio: give up */
    }
}

int panthera_render(const char *voiceDir, unsigned creator, int voiceId,
                    const char *text, int wpm,
                    short **outPcm, unsigned *outFrames)
{
    speech_api api;
    int err;

    if (outPcm)   *outPcm = 0;
    if (outFrames) *outFrames = 0;
    PT_ENSURE_ENGINE();
    if (!g_pt_ready) return -1;
    g_pt_stop = 0;

    pt_utterance_reset();
    err = pt_use_voice(voiceDir, creator, voiceId);
    if (err) return err;

    api = speech_api_of(&g_mt);
    /* Rate, in words per minute, as a Fixed 16.16.  Re-applied every utterance
     * because embedded commands in the text can change the channel for good. */
    if (wpm > 0) set_param(&api, g_chan, PARAM_RATE, (unsigned)wpm << 16);

    err = speak_text(&api, g_chan, text, (unsigned)strlen(text));
    if (err) { fprintf(stderr, "panthera: SESpeakBuffer -> OSErr %d\n", err); return err; }

    /* SESpeakBuffer returns as soon as the utterance is accepted; the slices
     * arrive on the engine's worker.  The engine sets g_stopped (AUGraphStop)
     * when the utterance ends -- that is the reliable signal, the one serve
     * mode waits on.  A "quiet window" is only a fallback, and it must not start
     * counting until slices have actually begun: a slow first slice (a cold
     * engine, a voice still loading) is longer than the window, and counting
     * quiet from t=0 made this return a one-frame render that played as
     * silence. */
    {
        unsigned last = 0, quiet = 0, ticks = 0;
        int started = 0;
        while (!g_pt_stop && !g_stopped && ticks < 4000) {   /* <= ~100 s cap */
            Sleep(25); ticks++;
            if (g_slices != last) { last = g_slices; quiet = 0; started = 1; }
            else if (started && ++quiet >= 120) break;       /* 3 s of quiet */
        }
    }

    {
        unsigned n = g_pcm_n;
        short *buf = (short *)malloc((size_t)(n ? n : 1) * sizeof(short));
        if (!buf) return -108;                       /* memFullErr */
        if (outFrames) *outFrames = pcm_to_i16(buf);
        if (outPcm)    *outPcm = buf;
        else           free(buf);
    }
    return 0;
}
