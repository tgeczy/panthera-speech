/* tiger_host_aac_stub.c -- the AAC path, absent.
 *
 * Part of tiger_host.c, which includes this in place of tiger_host_aac.c when
 * TIGER_NO_AAC is set (see there).  Vicki's and the modern voices' sample banks
 * are AAC; on Windows that decode is Media Foundation, and on Android it will be
 * the system decoder (AMediaCodec) as a third file behind this same switch.
 *
 * Fred -- MacinTalk's formant voice -- never touches any of this, so the
 * first ARM build leaves it out entirely.  What remains is only what the rest
 * of the translation unit references: the stats block the serve loop and the
 * end-of-run summary read, the packet counter, the --aac-check entry point, and
 * the fourteen converter shims the shim table binds.  Each shim only has to
 * link and, if a voice that needs it is ever loaded in this build, fail
 * cleanly rather than hand back silence that looks like success.
 */

/* Only the four fields read outside the AAC code (tiger_host_serve.c and the
 * summary in tiger_host.c): a full sndconv lives in tiger_host_aac.c. */
typedef struct {
    unsigned magic;
    unsigned sessions;
    unsigned resets;
    unsigned lost;
} sndconv;

static sndconv g_sc;

/* Counters the end-of-run summary (tiger_host.c) and serve loop read; all zero
 * because no AAC ever ran. */
static unsigned g_pkts_fed, g_frames_out, g_ac_silent_streams;
static double   g_pcmstat_abs, g_pcmstat_d;
static unsigned g_pcmstat_n;

/* --aac-check reports the decoder's health; there is no decoder in this build. */
static int aac_check(void)
{
    fprintf(stderr, "tiger_host: this build has no AAC decoder "
                    "(TIGER_NO_AAC); Fred does not need one\n");
    return 2;
}

/* kSoundConverterErr / unimpErr territory: any voice that reaches these in a
 * no-AAC build should fail its open, not render wrong audio. */
#define AAC_UNAVAIL (-4)                  /* unimpErr */

static int  __cdecl sh_SoundConverterOpen(void)            { return AAC_UNAVAIL; }
static int  __cdecl sh_SoundConverterSetInfo(void)         { return AAC_UNAVAIL; }
static int  __cdecl sh_SoundConverterClose(void)           { return AAC_UNAVAIL; }
static int  __cdecl sh_SoundConverterBeginConversion(void) { return AAC_UNAVAIL; }
static int  __cdecl sh_SoundConverterFillBuffer(void)      { return AAC_UNAVAIL; }
static int  __cdecl sh_SoundConverterConvertBuffer(void)   { return AAC_UNAVAIL; }
static int  __cdecl sh_SoundConverterEndConversion(void)   { return AAC_UNAVAIL; }

static int  __cdecl sh_AudioConverterNew(void)             { return AAC_UNAVAIL; }
static int  __cdecl sh_AudioConverterDispose(void)         { return AAC_UNAVAIL; }
static int  __cdecl sh_AudioConverterReset(void)           { return AAC_UNAVAIL; }
static int  __cdecl sh_AudioConverterSetProperty(void)     { return AAC_UNAVAIL; }
static int  __cdecl sh_AudioConverterGetProperty(void)     { return AAC_UNAVAIL; }
static int  __cdecl sh_AudioConverterGetPropertyInfo(void) { return AAC_UNAVAIL; }
static int  __cdecl sh_AudioConverterFillComplexBuffer(void){ return AAC_UNAVAIL; }
static int  __cdecl sh_AudioFormatGetProperty(void)        { return AAC_UNAVAIL; }
static int  __cdecl sh_AudioUnitReset(void)                { return 0; }

/* A UPP is just the callback pointer in this host; the real one passes it
 * through, so the stub does too (harmless if a voice ever reaches it). */
static void * __cdecl sh_NewFillBufferUPP(void *proc)      { return proc; }
static void   __cdecl sh_DisposeFillBufferUPP(void *upp)   { (void)upp; }

/* Not AAC at all -- it just happens to live in the AAC file: whether speech is
 * in progress, and Apple's engines answer this the same way in every build. */
static int  __cdecl sh_SpeechBusy(void)                    { return 0; }
