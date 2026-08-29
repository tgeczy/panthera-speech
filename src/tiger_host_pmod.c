/* tiger_host_pmod.c -- what it takes to undo an inflection.
 *
 * Part of tiger_host.c, which includes it; see there for why this is one
 * translation unit.
 *
 * **The question.**  Inflection is the engine's own `[[pmod]]`, sent as an
 * embedded command, and it sets state on the speech channel that outlives the
 * utterance which set it.  Coming back to the middle of the slider therefore
 * has to be *said* -- and there is no way to say it: `[[pmod 100]]` is not
 * "normal", it is a number, and for thirteen of Leopard's twenty-four voices
 * it is a different number from the one they were recorded with.  Alex is the
 * worst, because he ignores a raised pmod outright, so the command sent to
 * undo an inflection he never had is the only thing that ever altered him.
 *
 * The driver's answer today is a new process.  That answer does not exist
 * inside NVDA's bridge, where the engine is a DLL in a host that is not ours
 * to restart -- so this asks whether something cheaper reaches the same place:
 *
 *   rset      SESetSpeechInfo 'rset' -- soReset, the channel to its defaults
 *   reopen    SECloseSpeechChannel then SEOpenSpeechChannel
 *
 * Both are followed by SEUseVoice, because both drop the voice.
 *
 * **Run it on Tiger or Leopard, never on Lion**: Lion's mtk3 voices do not
 * render reproducibly, so neither a match nor a mismatch there would mean
 * anything.  Byte-identity against a never-inflected render is the whole test.
 *
 *   tiger_host --pmod-check <MacinTalk> <SpeechDictionary> <Voice.SpeechVoice>
 */

/* `SEOpen_t`'s twin is declared in tiger_host.c, but below this include --
 * so both are spelled out here rather than moving the include past the
 * function it exists to be called from. */
typedef int (__cdecl *SEClose_t)(void *chan);
typedef int (__cdecl *SEOpenChan_t)(void **chan);

/* One render, into a buffer of its own.  Returns frames, or -1.
 *
 * The counter reset is serve()'s, kept in step with it deliberately: a render
 * that started from different state would not be comparable with the one
 * before it, which is the only thing this file measures. */
/* How long the last `pmod_render` took, wall clock.
 *
 * **The reset calls are not where a cost would hide.**  If soReset drops
 * Alex's 701 MB bank and the engine reloads it lazily, `rset` and `SEUseVoice`
 * both return at once and the whole reload lands in the *next render* -- where
 * byte-identity would still pass, because a reload changes timing and not
 * samples.  So every render is timed, and the number that answers "is the
 * reset free" is the render after it, against the baseline render. */
static double g_pmod_ms;

static int pmod_render(speech_api *api, void *chan, const char *text,
                       short **out)
{
    unsigned last = 0, quiet = 0, ticks = 0, i;
    double t0 = wall_ms();
    int err;

    g_defer_arm = 0;
    g_pcm_n = 0; g_slices = 0; g_stopped = 0; g_empty_run = 0;
    g_dup_slices = 0; g_have_last = 0; g_p_drops = 0;
    g_epoch_base = 0; g_last_stime = 0.0; g_have_origin = 0;
    g_utt++; g_stale_slices = 0;

    err = speak_text(api, chan, text, strlen(text));
    if (err) return -1;
    while (quiet < 40 && ticks < 600) {          /* <= 30 s, then give up */
        Sleep(50); ticks++;
        if (g_slices != last) { last = g_slices; quiet = 0; }
        else quiet++;
    }
    g_pmod_ms = wall_ms() - t0;
    *out = (short *)malloc(g_pcm_n * sizeof(short) + 2);
    if (!*out) return -1;
    for (i = 0; i < g_pcm_n; i++) {
        double v = g_pcm[i];
        if (v > 1.0) v = 1.0;
        if (v < -1.0) v = -1.0;
        (*out)[i] = (short)(v * 32767.0);
    }
    return (int)g_pcm_n;
}

static int pmod_same(const short *a, int na, const short *b, int nb)
{
    return na == nb && na >= 0 && memcmp(a, b, (size_t)na * sizeof(short)) == 0;
}

static int pmod_check(image *mt, void *chan, const char *voicedir)
{
    /* Long enough to carry pitch movement -- pmod scales the depth of the
     * voice's pitch modulation, so a monotone fragment would understate it. */
    static const char text[] =
        "The way back to the middle of the slider has to be said, and there "
        "is no way to say it. So what does it take?";
    static const char loud[] =
        "[[pmod 200]]The way back to the middle of the slider has to be said, "
        "and there is no way to say it. So what does it take?";

    speech_api api = speech_api_of(mt);
    SEUseVoice_t use = (SEUseVoice_t)find_export(mt, "_SEUseVoice");
    SESetInfo_t setinfo = (SESetInfo_t)find_export(mt, "_SESetSpeechInfo");
    SEClose_t closechan = (SEClose_t)find_export(mt, "_SECloseSpeechChannel");
    SEOpenChan_t openchan = (SEOpenChan_t)find_export(mt, "_SEOpenSpeechChannel");
    struct { unsigned creator; int id; } spec;
    short *base = NULL, *inflected = NULL, *after = NULL;
    short *afterReset = NULL, *afterReopen = NULL;
    int nb, ni, na, nr = -1, no = -1;
    double t0, resetMs = -1.0, reopenMs = -1.0, baseMs = 0.0;
    double resetRenderMs = -1.0, reopenRenderMs = -1.0;
    int rc = 0;

    g_verbose = 0;
    if (!use || !setinfo) {
        fprintf(stderr, "pmod-check: the engine has no SEUseVoice or "
                        "SESetSpeechInfo\n");
        return 2;
    }
    if (!voice_spec(voicedir, &spec.creator, &spec.id)) {
        fprintf(stderr, "pmod-check: no VoiceDescription in %s\n", voicedir);
        return 2;
    }
    if (call_aligned3((void *)use, chan, &spec, cf_pinned(voicedir))) {
        fprintf(stderr, "pmod-check: SEUseVoice refused %s\n", voicedir);
        return 2;
    }
    fprintf(stderr, "pmod-check: %s, '%c%c%c%c' %d\n", voicedir,
            (spec.creator >> 24) & 0xff, (spec.creator >> 16) & 0xff,
            (spec.creator >> 8) & 0xff, spec.creator & 0xff, spec.id);

    /* A throwaway render before the baseline, because the *first* render of a
     * session is not always like the ones after it.
     *
     * Snow Leopard's Alex, measured: three renders of one sentence in one
     * session give 145743, 146926 and 146926 frames.  The first is the odd one
     * every time, and Leopard's Alex and Snow Leopard's Kathy are steady
     * across all three -- so it is this voice on this generation, warming up.
     *
     * Taken as a baseline it reported that soReset had failed on the one
     * generation where it had not, which is exactly the wrong way for a
     * measurement to be wrong. */
    {
        short *warm = NULL;
        int nw = pmod_render(&api, chan, text, &warm);
        fprintf(stderr, "  warm-up            %d frames, discarded\n", nw);
        free(warm);
    }

    nb = pmod_render(&api, chan, text, &base);
    if (nb <= 0) { fprintf(stderr, "pmod-check: the baseline render "
                                   "produced nothing\n"); return 2; }
    baseMs = g_pmod_ms;
    fprintf(stderr, "  baseline           %d frames in %.0f ms\n", nb, baseMs);

    ni = pmod_render(&api, chan, loud, &inflected);
    fprintf(stderr, "  with [[pmod 200]]  %d frames, %s\n", ni,
            pmod_same(base, nb, inflected, ni) ? "SAME as baseline"
                                               : "different");
    if (pmod_same(base, nb, inflected, ni)) {
        /* Eleven of Leopard's voices are unmoved by pmod.  Nothing can be
         * learned from one of those, and saying so is better than reporting a
         * clean pass that only means the voice never changed. */
        fprintf(stderr, "\npmod-check: this voice ignores pmod, so it cannot "
                        "answer the question.\n  Try Alex, Kathy, Vicki or "
                        "Zarvox.\n");
        return 3;
    }

    /* The state really does outlive the utterance that set it -- the premise
     * the whole driver behaviour rests on.  Checked rather than assumed,
     * because if it were false the restart would already be unnecessary. */
    na = pmod_render(&api, chan, text, &after);
    fprintf(stderr, "  plain again        %d frames, %s\n", na,
            pmod_same(base, nb, after, na)
                ? "back to baseline BY ITSELF"
                : "still inflected (the state outlives the utterance)");

    /* Route one: soReset, then the voice again.  Cheaper to say than to do --
     * it drops the voice, and for Alex the voice is a 701 MB sample bank. */
    if (setinfo) {
        unsigned zero = 0;
        t0 = wall_ms();
        call_aligned3((void *)setinfo, chan, (void *)SEL_RESET, &zero);
        call_aligned3((void *)use, chan, &spec, cf_pinned(voicedir));
        resetMs = wall_ms() - t0;
        nr = pmod_render(&api, chan, text, &afterReset);
        resetRenderMs = g_pmod_ms;
        fprintf(stderr, "  after 'rset'       %d frames, %s -- the reset calls "
                        "took %.0f ms, the render %.0f ms (baseline %.0f)\n",
                nr,
                pmod_same(base, nb, afterReset, nr) ? "IDENTICAL to baseline"
                                                    : "still not the baseline",
                resetMs, resetRenderMs, baseMs);
    }

    /* Route two: close the channel and open another one.  If soReset was
     * enough this is only of interest as a price comparison; if it was not,
     * this is the last thing short of a new process. */
    if (closechan && openchan) {
        void *fresh = NULL;
        /* Put the channel back into the inflected state first, so this route
         * is asked the same question the first one was rather than an easier
         * one left over from the reset above. */
        free(inflected);
        inflected = NULL;
        ni = pmod_render(&api, chan, loud, &inflected);
        t0 = wall_ms();
        call_aligned1((void *)closechan, chan);
        if (call_aligned1((void *)openchan, &fresh) || !fresh) {
            fprintf(stderr, "  after reopen       the engine would not open a "
                            "second channel\n");
            rc = 1;
        } else {
            call_aligned3((void *)use, fresh, &spec, cf_pinned(voicedir));
            reopenMs = wall_ms() - t0;
            no = pmod_render(&api, fresh, text, &afterReopen);
            reopenRenderMs = g_pmod_ms;
            fprintf(stderr, "  after reopen       %d frames, %s -- the reopen "
                            "calls took %.0f ms, the render %.0f ms "
                            "(baseline %.0f)\n", no,
                    pmod_same(base, nb, afterReopen, no)
                        ? "IDENTICAL to baseline" : "still not the baseline",
                    reopenMs, reopenRenderMs, baseMs);
        }
    }

    fprintf(stderr, "\nRESULT: ");
    if (pmod_same(base, nb, afterReset, nr))
        fprintf(stderr, "soReset plus SEUseVoice is the way back.\n"
                        "  the reset calls    %.0f ms\n"
                        "  the render after   %.0f ms, against %.0f ms for "
                        "the baseline render\n"
                        "An in-process host does not need a new process to "
                        "return inflection to its default.%s\n",
                resetMs, resetRenderMs, baseMs,
                /* A lazy bank reload would show up here and nowhere else.
                 * Half again plus a quarter of a second is far outside the
                 * run-to-run spread of a render and far inside a 2887 ms
                 * reload, so it cannot call one the other. */
                resetRenderMs > baseMs * 1.5 + 250.0
                    ? "\n\nBUT the render after the reset is markedly slower: "
                      "the cost did not vanish,\nit moved into the next "
                      "utterance.  Schedule the reset the way a restart is\n"
                      "scheduled today; do not call it freely."
                    : "");
    else if (pmod_same(base, nb, afterReopen, no))
        fprintf(stderr, "soReset is NOT enough, but reopening the channel is, "
                        "at %.0f ms.\nAn in-process host can still return "
                        "inflection to its default.\n", reopenMs);
    else
        fprintf(stderr, "neither soReset nor a fresh channel returns this "
                        "voice to its baseline.\nThe way back really is a new "
                        "engine, and the bridge has to answer for that.\n");

    free(base); free(inflected); free(after);
    free(afterReset); free(afterReopen);
    return rc;
}
