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

typedef struct {
    SESpeakBuffer_t   buffer;      /* one of these two is NULL */
    SESpeakCFString_t cfstring;
    const char       *which;       /* for the log line */
} speech_api;

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
    return a;
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
