/* tiger_host_speech.c -- the Speech Manager plugin API, across generations.
 *
 * Part of tiger_host.c, which includes it; see there for why this is one
 * translation unit.
 *
 * **Lion changed the plugin API.**  Leopard exports twelve `SE...` entry
 * points and Lion thirteen, and only eight are common to both:
 *
 *   gone in Lion   SESpeakBuffer, SETextToPhonemes, SEUseDictionary,
 *                  SESpeechStatus
 *   new in Lion    SESpeakCFString, SECopyPhonemesFromText,
 *                  SECopySpeechProperty, SESetSpeechProperty,
 *                  SEUseSpeechDictionary
 *   kept           SEOpenSpeechChannel, SECloseSpeechChannel, SEUseVoice,
 *                  SEGetSpeechInfo, SESetSpeechInfo, SEStopSpeechAt,
 *                  SEPauseSpeechAt, SEContinueSpeech
 *
 * The important half of that is what survived: `SEGetSpeechInfo` and
 * `SESetSpeechInfo` are still there, so every `soXxx` selector the drivers use
 * for rate, pitch, volume and inflection keeps working unchanged.  Only the
 * call that hands over text is different -- Lion takes a CFString where
 * Leopard took a pointer and a length.
 *
 * So this file resolves the entry points once, per image, and offers the rest
 * of the host one `speak_text`.  Adding Snow Leopard or anything later should
 * mean another row here rather than a branch at each call site: there are two
 * of those already (the one-shot render and serve mode) and they would drift.
 */

/* Leopard and earlier: a buffer and a length. */
typedef int (__cdecl *SESpeakBuffer_t)(void *chan, const void *buf,
                                       long len, long flags);
/* Lion: a CFStringRef and an options dictionary, which may be NULL. */
typedef int (__cdecl *SESpeakCFString_t)(void *chan, const void *cfstr,
                                         const void *options);

/* Parameters moved house in the same release, and separately from the text.
 *
 * Leopard and earlier: `SESetSpeechInfo(chan, 'rate', &Fixed)`.
 * Lion: `SESetSpeechProperty(chan, kSpeechRateProperty, CFNumber)`. Its
 * `SetSpeechInfo` compares nine internal selectors and answers **OSErr -231**
 * to `rate` and `pbas` -- which reads as "the engine refused the rate" and
 * leaves the user at the engine's own 180 wpm with no way off it.
 *
 * Both ends are told in `Fixed`, so no caller has to know which generation it
 * is talking to: the property form divides by 65536 going in, because the
 * engine multiplies by 65536 coming out. That constant was read out of
 * `__const` rather than assumed. */
typedef int (__cdecl *SESetSpeechInfo_t)(void *chan, unsigned sel, void *val);
typedef int (__cdecl *SEGetSpeechInfo_t)(void *chan, unsigned sel, void *val);
typedef int (__cdecl *SESetSpeechProperty_t)(void *chan, const void *key,
                                             const void *val);
typedef int (__cdecl *SECopySpeechProperty_t)(void *chan, const void *key,
                                              const void **out);

typedef struct {
    SESpeakBuffer_t   buffer;      /* one of these two is NULL */
    SESpeakCFString_t cfstring;
    const char       *which;       /* for the log line */
    SESetSpeechInfo_t      setinfo;      /* 10.6 and earlier */
    SEGetSpeechInfo_t      getinfo;
    SESetSpeechProperty_t  setprop;      /* 10.7 */
    SECopySpeechProperty_t copyprop;
    const char            *whichparam;
} speech_api;

/* One row per parameter, in both spellings, so a call site names the
 * parameter and never the generation. */
#define PARAM_RATE   0
#define PARAM_PITCH  1
#define PARAM_VOLUME 2
#define PARAM_INFLEC 3
static const struct { unsigned sel; int key; const char *name; } PARAMS[] = {
    { 0x72617465u, SPK_RATE,      "rate"   },   /* 'rate' */
    { 0x70626173u, SPK_PITCHBASE, "pitch"  },   /* 'pbas' */
    { 0x766f6c6du, SPK_VOLUME,    "volume" },   /* 'volm' */
    { 0x706d6f64u, SPK_PITCHMOD,  "inflec" },   /* 'pmod' */
};

/* Resolve the text-submitting entry point of whichever generation this is.
 *
 * Deliberately not cached in a global: the host loads exactly one engine per
 * process, but a lookup that silently answers for a different image is the
 * kind of bug this codebase has spent whole evenings on.
 */
static speech_api speech_api_of(image *mt)
{
    speech_api a;
    a.buffer = (SESpeakBuffer_t)find_export(mt, "_SESpeakBuffer");
    a.cfstring = (SESpeakCFString_t)find_export(mt, "_SESpeakCFString");
    a.which = a.buffer ? "SESpeakBuffer"
                       : (a.cfstring ? "SESpeakCFString" : NULL);
    if (!a.which)
        die("%s exports neither SESpeakBuffer nor SESpeakCFString", mt->path);
    /* Both would mean a generation this table does not describe, and picking
     * one by accident is worse than saying so. */
    if (a.buffer && a.cfstring)
        printf("  note: this engine exports both text entry points; "
               "using SESpeakBuffer\n");

    a.setinfo  = (SESetSpeechInfo_t)find_export(mt, "_SESetSpeechInfo");
    a.getinfo  = (SEGetSpeechInfo_t)find_export(mt, "_SEGetSpeechInfo");
    a.setprop  = (SESetSpeechProperty_t)
                 find_export(mt, "_SESetSpeechProperty");
    a.copyprop = (SECopySpeechProperty_t)
                 find_export(mt, "_SECopySpeechProperty");
    /* **Preferred, not merely present.** Lion exports SESetSpeechInfo too --
     * it simply refuses `rate` and `pbas` through it. Choosing by which
     * symbol exists would pick the one that answers -231, which is how this
     * was found: as "the engine refused 180 wpm". */
    a.whichparam = a.setprop ? "SESetSpeechProperty"
                             : (a.setinfo ? "SESetSpeechInfo" : NULL);
    if (a.setprop) speech_keys_init();
    if (g_verbose && a.whichparam)
        printf("  parameters via %s\n", a.whichparam);
    return a;
}

/* Set one parameter, in `Fixed`, whichever way this generation takes it.
 * -> OSErr, and 0 is success in both spellings. */
static int set_param(const speech_api *a, void *chan, int which, unsigned fx)
{
    if (a->setprop) {
        /* The engine reads this back as Float32 and multiplies by 65536, so
         * hand it the same number divided by 65536. Not freed: the engine may
         * hold the value, and one CFNumber per utterance is not a leak worth
         * a use-after-free. */
        cfobj *n = cf_number((double)fx / 65536.0);
        if (!n) return -108;                    /* memFullErr */
        return call_aligned3((void *)a->setprop, chan,
                             g_speech_key[PARAMS[which].key], n);
    }
    if (a->setinfo)
        return call_aligned3((void *)a->setinfo, chan,
                             (void *)PARAMS[which].sel, &fx);
    return -231;                                /* siUnknownInfoType */
}

/* Read one parameter back, in `Fixed`.  -> non-zero on success. */
static int get_param(const speech_api *a, void *chan, int which, unsigned *out)
{
    if (!out) return 0;
    if (a->copyprop) {
        const void *v = NULL;
        float f = 0.0f;
        if (call_aligned3((void *)a->copyprop, chan,
                          g_speech_key[PARAMS[which].key], &v) != 0 || !v)
            return 0;
        if (!sh_CFNumberGetValue(v, 5 /* kCFNumberFloat32Type */, &f))
            return 0;
        *out = (unsigned)(f * 65536.0f);
        return 1;
    }
    if (a->getinfo)
        return call_aligned3((void *)a->getinfo, chan,
                             (void *)PARAMS[which].sel, out) == 0;
    return 0;
}

/* Hand `text` to the engine.  -> OSErr
 *
 * The CFString path builds one of the host's own CFString objects. The engine
 * holds it as an opaque CFStringRef and reaches back through the shims, which
 * is the same arrangement the voice loader has used since Tiger -- see
 * tiger_host_cf.c for why the string field has to come first in the object.
 */
static int speak_text(const speech_api *a, void *chan,
                      const char *text, size_t len)
{
    if (a->buffer)
        return call_aligned4((void *)a->buffer, chan, (void *)text,
                             (void *)len, (void *)0);
    {
        /* Not freed: the engine keeps the text for the length of the
         * utterance and this host renders one and exits, or serves and
         * reuses. Releasing it here would be a use-after-free with a very
         * long fuse. */
        cfobj *s = cf_new(text);
        return call_aligned3((void *)a->cfstring, chan, s, (void *)0);
    }
}
