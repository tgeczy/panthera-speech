/* tiger_host_aac_faad.c -- AAC decoded in this process, by FAAD2.
 *
 * The third backend behind tiger_host_aac.c; see tiger_host_aac_mf.c for the
 * contract.  The other two ask the operating system for a decoder -- Media
 * Foundation on Windows, AMediaCodec on Android -- which is the right instinct
 * and, on Android, the wrong result.
 *
 * AMediaCodec is out of process.  Every access unit costs a handful of Binder
 * round trips to mediaswcodec, and Alex is a unit-selection voice: one ordinary
 * sentence decoded 1630 access units and spent 4837 ms doing it, for 5.1
 * seconds of audio.  That is realtime in the decoder alone, before the engine
 * has done any of its own work, and no amount of tuning the polling gets past
 * a per-buffer IPC.  In process, the same decode is a function call.
 *
 * It also answers a question the system decoder could not: an AOSP build with
 * no AAC codec, or a device whose codec refuses raw AAC-LC, has no Vicki and no
 * Alex at all.  With this one it does.
 *
 * FAAD2 is GPLv2 and so is this APK -- Unicorn made that decision for us -- so
 * the licences agree.  Its source is not vendored here: android/harness/
 * build_faad2.sh fetches a pinned tag and compiles it, the same arrangement
 * Unicorn already has, so the repository carries no third-party source it would
 * then have to maintain.
 *
 * The whole backend is smaller than either of the others because there is no
 * pipeline to manage: NeAACDecDecode takes an access unit and returns its
 * samples.  aac_drain and aac_end_stream have nothing to do, which is what a
 * synchronous decoder is worth.
 */
#include <neaacdec.h>

static NeAACDecHandle g_faad;
static int            g_aac_state;      /* 0 untried, 1 ready, -1 no decoder */
static int            g_faad_said;
static int            g_faad_shown;
static short         *g_faad_mono;   /* one channel of an upmixed frame */
static unsigned       g_faad_mono_cap;
static int            g_faad_primed;  /* the swallowed first frame */      /* the wrong-length complaint, said once */

static int aac_is_open(void)
{
    return g_faad != NULL;
}

static void aac_reset_decoder(void)
{
    if (g_faad) NeAACDecClose(g_faad);
    g_faad = NULL;
    g_aac_state = 0;
}

static int aac_open(void);
static int aac_open(void)
{
    NeAACDecConfigurationPtr cfg;
    unsigned long rate = 0;
    unsigned char chans = 0;

    if (g_aac_state) return g_aac_state > 0;
    g_aac_state = -1;                    /* pessimistic until it works */

    if (g_sc.asclen < 2) {
        fprintf(stderr, "  [aac] no AudioSpecificConfig in the voice's "
                        "'wave' atom\n");
        return 0;
    }
    g_faad = NeAACDecOpen();
    if (!g_faad) {
        fprintf(stderr, "  [aac] NeAACDecOpen failed\n");
        return 0;
    }
    cfg = NeAACDecGetCurrentConfiguration(g_faad);
    if (cfg) {
        /* 16-bit PCM, and no downmix: the banks are mono and the engine's
         * arithmetic counts samples, not frames. */
        cfg->outputFormat = FAAD_FMT_16BIT;
        cfg->downMatrix   = 0;
        /* **Do not upsample for implicit SBR**, which FAAD2 does by default and
         * which is wrong for every bank here.  It returns 2048 samples where
         * the engine's arithmetic says 1024, at the same nominal rate, so the
         * engine reads the first half of each unit and gets audio an octave
         * down and a unit cut in half -- heard, exactly, as "a slowed down
         * cassette with stuttering".  No length check catches it: there is more
         * audio than asked for, not less, and the short-decode alarm only fires
         * on less. */
        cfg->dontUpSampleImplicitSBR = 1;
        NeAACDecSetConfiguration(g_faad, cfg);
    }
    /* Init2 takes the AudioSpecificConfig itself, which is exactly what the
     * voice's 'wave' atom carries and what grab_asc already extracted -- no
     * ADTS wrapper, no container, the same two bytes the other two backends
     * are handed. */
    if (NeAACDecInit2(g_faad, (unsigned char *)g_sc.asc,
                      (unsigned long)g_sc.asclen, &rate, &chans) < 0) {
        fprintf(stderr, "  [aac] NeAACDecInit2 refused the voice's config\n");
        aac_reset_decoder();
        return 0;
    }
    if (g_verbose)
        printf("  [aac] FAAD2 ready: %lu Hz, %u channel(s); the voice says "
               "%u Hz, %u\n", rate, chans, g_sc.rate, g_sc.channels);
    g_aac_state = 1;
    g_faad_primed = 0;
    return 1;
}

static int aac_feed(const unsigned char *data, unsigned len)
{
    NeAACDecFrameInfo info;
    void *pcm;
    double t0;
    if (!g_faad) return 0;
    t0 = (g_aac_depth++ == 0) ? wall_ms() : 0.0;
    g_aac_units++;
    memset(&info, 0, sizeof info);
    pcm = NeAACDecDecode(g_faad, &info, (unsigned char *)data, len);
    /* **Stand in for the frame this decoder eats.**  FAAD2 answers zero samples
     * for the first access unit after an init -- it primes its overlap buffer
     * with it rather than emitting it -- where Media Foundation hands that
     * frame over like any other.  The engine then skips a fixed 2112 samples of
     * codec delay off the front of every unit, so one decoder's stream is a
     * whole frame out of step with the other's and each unit is read from 1024
     * samples too far in.  There is no global lag to find afterwards, because
     * every unit is shifted inside itself: cross-correlating the finished
     * utterance against the desktop gave 0.22 where the system decoder gives
     * 1.00.  So put the frame back, as silence, and the arithmetic lines up. */
    if (g_faad_primed == 0) {
        g_faad_primed = 1;
        if (!info.error && info.samples == 0) {
            static const short quiet[AAC_FRAME] = { 0 };
            pcm_append((const unsigned char *)quiet, AAC_FRAME * 2u);
        }
    }
    /* An access unit is AAC_FRAME samples per channel and the engine's whole
     * arithmetic depends on it.  Say so once if it ever is not: too FEW is
     * already caught downstream, too MANY is not, and too many is what implicit
     * SBR upsampling produces -- silently, and audibly. */
    /* The first few decodes after an init, unconditionally, because a decoder
     * that answers ZERO samples for the first frame -- priming its overlap
     * buffer rather than emitting it -- shifts every unit by 1024 samples and
     * is invisible to any check that only looks at non-empty frames. */
    if (g_faad_shown < 4) {
        g_faad_shown++;
        fprintf(stderr, "  [aac] FAAD2 decode %d: %lu samples, %u ch, %lu Hz, "
                        "err %u, consumed %lu of %u\n", g_faad_shown,
                info.samples, info.channels, info.samplerate, info.error,
                info.bytesconsumed, len);
    }
    if (!info.error && info.samples) {
        unsigned want = AAC_FRAME * (info.channels ? info.channels : 1u);
        if ((unsigned)info.samples != want && !g_faad_said) {
            g_faad_said = 1;
            fprintf(stderr, "  [aac] FAAD2 returned %lu samples for one access "
                            "unit, expected %u (%u ch at %lu Hz). The voice "
                            "will be pitched wrong.\n",
                    info.samples, want, info.channels, info.samplerate);
        }
    }
    if (!info.error && pcm && info.samples) {
        /* **Take one channel when the decoder invents two.**  The banks are
         * mono -- the AudioSpecificConfig says channelConfig 1 -- and FAAD2
         * still answers two channels, because implicit parametric stereo
         * upmixes it.  info.samples counts interleaved samples, so 2048 of them
         * are 1024 frames, and handing all 2048 to an engine expecting 1024
         * mono ones plays every frame at half speed an octave down: heard,
         * exactly, as a slowed cassette.
         *
         * Nothing downstream can catch it.  There is more audio than asked for
         * rather than less, so the short-decode alarm stays quiet, and the
         * length the engine finally writes is set by its own arithmetic, so
         * even the WAV comes out the right size.  Only the ear, and a
         * cross-correlation against the same utterance rendered on the desktop,
         * which came back at 0.12 where the system decoder gives 1.00. */
        const short *src = (const short *)pcm;
        unsigned n = (unsigned)info.samples;
        if (info.channels > 1 && g_sc.channels <= 1) {
            unsigned frames = n / info.channels, i;
            if (frames > g_faad_mono_cap) {
                short *g = (short *)realloc(g_faad_mono, frames * 2u);
                if (!g) { if (--g_aac_depth == 0) g_aac_ms += wall_ms() - t0; return 0; }
                g_faad_mono = g;
                g_faad_mono_cap = frames;
            }
            for (i = 0; i < frames; i++) g_faad_mono[i] = src[i * info.channels];
            src = g_faad_mono;
            n   = frames;
        }
        pcm_append((const unsigned char *)src, n * 2u);
    }
    if (--g_aac_depth == 0) g_aac_ms += wall_ms() - t0;
    if (info.error) {
        if (!g_sc.quiet++)
            fprintf(stderr, "  [aac] FAAD2: %s\n",
                    NeAACDecGetErrorMessage(info.error));
        return 0;
    }
    return 1;
}

/* Nothing is held back, so there is nothing to take.  Both of these exist
 * because a pipelined decoder needs them; a synchronous one has already handed
 * over everything it owes by the time aac_feed returns. */
static void aac_drain(void) { }
static void aac_end_stream(void) { }

/* Between units, and it has to be a real reset.
 *
 * The engine treats each unit as its own stream, and an AAC frame is finished
 * by the one after it -- the decoder carries an overlap buffer forward.  Media
 * Foundation is sent COMMAND_FLUSH here, which discards that.
 * NeAACDecPostSeekReset does not discard enough: with it, every unit after the
 * first is decoded on top of the previous unit's tail, which is not noise and
 * not silence but a different voice entirely.
 *
 * So the decoder is rebuilt.  In process that costs a NeAACDecOpen and an
 * Init2 against two bytes of config -- microseconds, and nothing like the
 * per-buffer IPC this backend exists to avoid. */
static void aac_flush_now(void)
{
    if (!g_faad) return;
    NeAACDecClose(g_faad);
    g_faad = NULL;
    g_aac_state = 0;
    g_faad_primed = 0;
    (void)aac_open();
}

static void aac_begin(void)
{
    g_sc.pcm_n = g_sc.pcm_pos = 0;
    aac_flush_now();
}

static int aac_check(void)
{
    NeAACDecHandle h = NeAACDecOpen();
    if (!h) {
        fprintf(stderr, "RESULT: FAAD2 would not open.  Vicki and Alex cannot\n"
                        "be decoded; every formant voice is unaffected.\n");
        return 2;
    }
    NeAACDecClose(h);
    fprintf(stderr, "RESULT: FAAD2 is built in and available, so the AAC\n"
                    "voices do not depend on a system codec.\n");
    return 0;
}
