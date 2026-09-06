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
static volatile LONG g_pt_stop;      /* set by panthera_stop, read by the poll */

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

/* What to pass SEStopSpeechAt as `whereToStop`.
 *
 * Under dispute and therefore made measurable rather than argued about.  The
 * Speech Synthesis Manager documents kImmediate/kEndOfWord/kEndOfSentence as
 * 0/1/2, which is what this host has always passed and what serve mode still
 * passes; the competing reading is that they are OSTypes 'immd'/'word'/'sent'.
 * Override at build time to try the other one and read the finish= line. */
#ifndef PT_STOP_WHERE
#define PT_STOP_WHERE 0u                 /* kImmediate, on the 0/1/2 reading */
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
    g_aac_ms = 0.0; g_aac_units = 0;
    /* Time to the FIRST slice, which is the latency a listener actually feels:
     * the service streams, so speech starts when the engine produces its first
     * audio and not when it finishes the utterance.  serve mode has always
     * reset these; this path never did, and so could only measure whole renders
     * and call them latency. */
    g_utt_t0 = wall_ms(); g_first_slice_ms = -1.0; g_last_slice_ms = 0.0;
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
    /* The Binder caller must never enter the shared guest channel concurrently
     * with the synthesis thread, so this posts flags and nothing else.
     *
     * g_au_cancel is the one that ends the utterance: it makes the audio shim
     * refuse the engine's next scheduled slice, which unwinds its render loop
     * from the inside.  It is a host-side flag, so setting it from Binder is
     * safe -- that is exactly why the cancellation lives there rather than in
     * a Speech Manager call this thread is not allowed to make. */
    InterlockedExchange(&g_pt_stop, 1);
    InterlockedExchange(&g_au_cancel, 1);
}

void panthera_finish(void)
{
    if (g_pt_ready && g_pt_stop) {
        unsigned last = g_slices, quiet = 0;
        unsigned slices_in = g_slices;
        double t0 = wall_ms(), t_ready, t_stop;
        int spin, err_stop = -1;
        SEStop_t stop;
        /* Belt and braces: panthera_stop normally sets this, but render mode
         * calls finish directly and a stop posted before the engine was ready
         * would not have. */
        InterlockedExchange(&g_au_cancel, 1);
        /* Timed apart from the stop itself: on the emulated build this lazily
         * creates the thread's uc_engine, and "the stop took 20 s" would be a
         * different fault if the 20 s were spent here. */
        PT_ENSURE_ENGINE();
        t_ready = wall_ms();
        /* Tell the channel as well, so its own notion of state agrees with
         * ours.  This is not what makes the cancellation quick -- refusing the
         * next slice is -- but by the time we get here the engine's loop is
         * already unwinding, so the call no longer has to wait out the rest of
         * the sentence to be answered.
         *
         * ('rset' used to be attempted here on the strength of a comment in
         * serve mode.  It answers paramErr on this engine, and serve mode's
         * own g_use_reset defaults to off, so it was never doing anything.) */
        stop = (SEStop_t)find_export(&g_mt, "_SEStopSpeechAt");
        if (stop) err_stop = call_aligned2((void *)stop, g_chan,
                                           (void *)(unsigned)PT_STOP_WHERE);
        t_stop = wall_ms();

        /* Then a bounded settle so outstanding slices land before the next
         * request resets the shared timeline (same rule as serve mode). */
        for (spin = 0; spin < 100 && quiet < 15; spin++) {
            Sleep(2);
            if (g_slices != last || !pacer_idle()) { last = g_slices; quiet = 0; }
            else quiet++;
        }
        /* One line, because this path is measured rather than reasoned about:
         * every previous guess at where the twenty seconds went was wrong. */
        fprintf(stderr, "panthera: finish ready=%.0fms stop=%.0fms(err %d) "
                        "settle=%.0fms slices %u->%u drops=%u stale=%u\n",
                t_ready - t0, t_stop - t_ready, err_stop,
                wall_ms() - t_stop, slices_in, g_slices,
                g_p_drops, g_stale_slices);
        InterlockedExchange(&g_au_cancel, 0);
    }
}

/* ---- streaming synthesis (the low-latency TTS path) -------------------- */

int panthera_speak_start(const char *voiceDir, unsigned creator, int voiceId,
                         const char *text, int wpm)
{
    speech_api api;
    int err;
    /* The synthesis thread runs speak_text and the pull loop, so it wants the
     * fast core too.  Asked per utterance rather than once: this thread belongs
     * to the framework, which may hand a different one over. */
    tiger_thread_wants_fast_core("synthesis");
    PT_ENSURE_ENGINE();
    if (!g_pt_ready) return -1;
    InterlockedExchange(&g_pt_stop, 0);
    InterlockedExchange(&g_au_cancel, 0);   /* this one's audio is wanted */
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
        int finished = g_stopped && pacer_idle();
        unsigned end = g_pcm_n;
        /* Match serve mode: the newest probe slice may still be overwritten. */
        if (!finished) end = end > STREAM_LOOKBEHIND ? end - STREAM_LOOKBEHIND : 0;
        unsigned avail = end > g_pull_pos ? end - g_pull_pos : 0;
        if (g_pt_stop) return 0;
        if (avail > 0) {
            unsigned n = avail < (unsigned)maxSamples ? avail : (unsigned)maxSamples;
            unsigned i;
            for (i = 0; i < n; i++) out[i] = pt_clip(g_pcm[g_pull_pos + i]);
            g_pull_pos += n;
            return (int)n;
        }
        if (g_stopped && pacer_idle()) {                      /* utterance finished, all drained */
            /* Slices per utterance, so a cancelled one can be told from a
             * completed one: the question is whether a stop truncates the
             * render or merely watches it run to the end. */
            {
                /* "Still eight megabytes and steady" and "climbing two a
                 * utterance" look identical from one render and are completely
                 * different problems, so the arena is reported every time. */
                unsigned bumped = 0, freed = 0;
#ifdef TIGER_UC
                arena_usage(&bumped, &freed);
#endif
                fprintf(stderr, "panthera: utterance done slices=%u frames=%u "
                                "first=%.0fms aac=%.0fms over %u units "
                                "arena=%uK free=%uK\n",
                        g_slices, g_pcm_n, g_first_slice_ms, g_aac_ms,
                        g_aac_units, bumped / 1024u, freed / 1024u);
            }
            return 0;
        }
        Sleep(5);
        if (++idle > 2000) {
            fprintf(stderr, "panthera: pull timeout pcm=%u cursor=%u slices=%u stopped=%ld pending=%d busy=%ld\n",
                    g_pcm_n, g_pull_pos, g_slices, g_stopped, g_p_count, (long)g_p_busy);
            return -1;
        }          /* ~10 s with no audio: give up */
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
    InterlockedExchange(&g_pt_stop, 0);
    InterlockedExchange(&g_au_cancel, 0);

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

    panthera_finish();
    {
        unsigned ticks;
        for (ticks = 0; !pacer_idle() && ticks < 500; ticks++) Sleep(2);
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
