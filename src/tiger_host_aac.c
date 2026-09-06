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
    unsigned      prime_left;           /* priming still to drop this stream */
    int           ac_live;              /* an AudioConverter stream is open  */
    unsigned      st_fed;               /* access units fed this stream      */
    unsigned      st_given;             /* samples handed the engine, ditto  */
    unsigned char *lastpkt;             /* the stream's newest access unit   */
    unsigned      lastpkt_len, lastpkt_cap;
    unsigned      resets;               /* AudioConverterReset calls */
    unsigned      lost;                 /* access units the decoder refused */
    int           complained;
    int           quiet;                /* one complaint per run is plenty  */
} sndconv;

static sndconv g_sc;

/* Roughness of the decoder's own output, under TIGER_PCM_STATS. */
static double g_pcmstat_abs, g_pcmstat_d;
static unsigned g_pcmstat_n;

/* Packets fed to the decoder, and samples handed to the engine.  One AAC
 * packet is 1024 samples, so if the second exceeds the first times 1024 we
 * are giving the engine audio twice -- which is what an inserted phantom
 * fragment inside a word would be. */
static unsigned g_pkts_fed, g_frames_out;
/* TIGER_AAC_TRACE: one line per refill, and a count of streams that
 * produced nothing at all -- a silent stream is a silent *word*, and
 * the total duration stays right, so it is invisible in a waveform
 * length and obvious in a transcript. */
static int g_ac_trace = -1;
static unsigned g_ac_silent_streams;
/* TIGER_SIM_WIN7: pretend to be Windows 7's AAC decoder; see aac_end. */
static int g_sim_win7 = -1;


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

/* ---- the decoder itself, one backend per platform ---------------------- */
/*
 * Everything above is the same whoever decodes: the converter state, the PCM
 * sink, and the ADTS dump that lets this be checked against a decoder that is
 * not ours.  Everything below is the same too -- the priming, the unit loop and
 * the fourteen shims.  Only the decode is the platform's own, and it is always
 * the platform's own: Windows has shipped an AAC decoder since 7 and Android
 * since 4.1, so neither build carries one, exactly as neither carries SQLite
 * (see tiger_host_sqlite.c).
 */
#ifdef TIGER_AAC_NDK
#include "tiger_host_aac_ndk.c"        /* Android: AMediaCodec */
#else
#include "tiger_host_aac_mf.c"         /* Windows: Media Foundation */
#endif

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

/* Close the stream, then say how much of what came back is really audio.
 *
 * The flush is the backend's (aac_end_stream); the arithmetic after it is not,
 * and deliberately so.  How many samples a decoder withholds is a property of
 * that decoder, but what to *do* about it is a property of AAC, and the moment
 * two backends each keep a copy of this they can come to disagree about where a
 * voice starts -- which is not a crash, it is Vicki's syllables running
 * together, and it took a Windows 7 machine to find the first time.
 *
 * TIGER_SIM_WIN7 reproduces the decoder that withholds the newest frame even
 * through a drain, on a machine that has no Windows 7. */
static void aac_end(void)
{
    unsigned before = g_sc.pcm_n;
    aac_end_stream();
    if (g_sim_win7 < 0) g_sim_win7 = getenv("TIGER_SIM_WIN7") ? 1 : 0;
    if (g_sim_win7) {
        unsigned got = g_sc.pcm_n - before;
        unsigned hold = AAC_FRAME * (g_sc.channels ? g_sc.channels : 1);
        if (hold > got) hold = got;
        g_sc.pcm_n -= hold;
    }
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
            /* The destination format matters and was never being read: if the
             * engine wants float and gets 16-bit integers, every frame is
             * misread and the result is speech with noise laid over it. */
            if (out) {
                char g[5];
                fourcc(g, out->mFormatID);
                printf("  [ac] dest: '%s' flags %08x, %u bits, %u bytes/frame,"
                       " %u frames/packet\n", g, (unsigned)out->mFormatFlags,
                       (unsigned)out->mBitsPerChannel,
                       (unsigned)out->mBytesPerFrame,
                       (unsigned)out->mFramesPerPacket);
            }
        }
    }
    if (conv) *conv = (void *)AC_MAGIC;
    return 0;
}

static int __cdecl sh_AudioConverterDispose(void *conv)
{
    (void)conv;
    if (g_sc.ac_live && g_aac) {
        IMFTransform_ProcessMessage(g_aac, MFT_MESSAGE_COMMAND_FLUSH, 0);
        g_sc.ac_live = 0;
    }
    g_sc.pcm_n = g_sc.pcm_pos = 0;
    return 0;
}

/* Reset means "forget the stream", and now that the decoder is kept open
 * across refills it has to mean that here too -- otherwise the next utterance
 * would begin with the overlap tail of the last one. */
static int __cdecl sh_AudioConverterReset(void *conv)
{
    (void)conv;
    g_sc.pcm_n = g_sc.pcm_pos = 0;
    if (g_sc.ac_live && g_aac) {
        IMFTransform_ProcessMessage(g_aac, MFT_MESSAGE_COMMAND_FLUSH, 0);
        g_sc.ac_live = 0;
    }
    /* Whether this is ever called decides whether the packets the engine feeds
     * are one continuous recording or a series of unrelated grains -- and so
     * whether keeping one decoder stream open across them is right or is
     * overlapping each grain onto the wrong neighbour. */
    g_sc.resets++;
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
        /* TIGER_FEXF overrides the answer, because it is 1.0.6-only machinery
         * that we force on: a nonzero reply makes MEOWACDecoder store -1 as its
         * frame size, which puts MEOWACIterator into its packet-table branch
         * and has it read 16-bit big-endian packet sizes out of the compressed
         * blob.  Vicki (1.0.4) never goes near that path; Alex does.
         *   unset or 1 : externally framed (the current answer)
         *   0          : not externally framed
         *   2          : fail the property entirely, as an unimplemented one
         *                would */
        const char *e = getenv("TIGER_FEXF");
        int mode = e ? atoi(e) : 1;
        if (mode == 2) { if (iosize) *iosize = 0; return -50; }
        if (out && iosize && *iosize >= 4) *(unsigned *)out = (unsigned)mode;
        if (iosize) *iosize = 4;
        return 0;
    }
    if (iosize) *iosize = 0;
    return -50;
}

/* The engine's callback hands over one blob and the packet boundaries inside
 * it, then reports no more.  Decode the lot, then dole it out: the engine
 * calls back until it has the frames it asked for. */
static int __cdecl sh_AudioConverterFillComplexBuffer_inner(void *conv,
                                                      ac_input_proc proc,
                                                      void *user,
                                                      unsigned *iopackets,
                                                      au_bufferlist *outdata,
                                                      au_packetdesc *outdesc)
{
    unsigned want, give = 0, cap;
    (void)conv; (void)outdesc;
    if (!iopackets || !outdata || !outdata->mNumberBuffers) return -50;
    want = *iopackets;
    cap = outdata->mBuffers[0].mDataByteSize / 2;
    if (want > cap) want = cap;

    /* **Fill the whole request, not one decode round of it.**
     *
     * A decoded AAC packet is 1024 samples and 10.7's engine asks for 2257 at
     * a time, so returning after a single round handed back 1024 frames and
     * left the remaining 1233 as whatever the buffer already held -- zero.
     * The engine believed the count, advanced its clock over the lot, and
     * emitted a slice that was half silence.
     *
     * Leopard's engine asks again when it is short-changed and so never
     * noticed; Lion's does not. That one difference cost **two thirds of
     * Alex**: 335 of 508 slices came back entirely silent, at full duration,
     * with the roughness *improving* as the silence grew. */
    while (give < want) {
    if (g_sc.pcm_pos >= g_sc.pcm_n && proc && aac_open()) {
        int rounds = 0;
        if (g_ac_trace < 0) g_ac_trace = getenv("TIGER_AAC_TRACE") ? 1 : 0;
        g_sc.pcm_n = g_sc.pcm_pos = 0;
        /* Keep asking until there is something to hand back.
         *
         * The decoder holds a frame: feed it one packet and it returns
         * nothing, because an AAC frame is not finished until the next one
         * overlaps it.  Asking the engine once per refill and giving up
         * therefore returned zero frames every time, and Alex fell silent
         * altogether -- which looked far worse than the stutter it replaced,
         * and was one step closer. */
        while (g_sc.pcm_n == 0 && rounds++ < 64) {
            au_bufferlist in;
            au_packetdesc *descs = NULL;
            unsigned packets = 0;
            memset(&in, 0, sizeof in);
            in.mNumberBuffers = 1;
            proc(conv, &packets, &in, &descs, user);
            if (packets && descs && in.mBuffers[0].mData) {
                const unsigned char *base =
                    (const unsigned char *)in.mBuffers[0].mData;
                unsigned i;
                /* **Open the stream once, not once per refill.**
                 *
                 * AAC frames overlap: each one's samples are finished by the
                 * next, because the codec adds consecutive MDCT windows
                 * together.  aac_begin() sends COMMAND_FLUSH, which throws that
                 * carry-over away, so calling it for every refill puts a seam
                 * at every single 1024-sample boundary.  Alex counted to seven
                 * correctly and stuttered all the way there -- a gap per frame,
                 * which is exactly what a gap every 1024 samples sounds like.
                 *
                 * The engine pulls one utterance through one converter: it asks
                 * for 19083 frames and counts down 18059, 17035, 16011.  That
                 * is a stream, so treat it as one and let the decoder keep its
                 * overlap. */
                if (!g_sc.ac_live) {
                    aac_begin();
                    g_sc.ac_live = 1;
                    g_sc.sessions++;
                    g_sc.prime_left = AAC_PRIMING;
                    g_sc.st_fed = 0;
                    g_sc.st_given = 0;
                    g_sc.lastpkt_len = 0;
                }
                for (i = 0; i < packets; i++) {
                    if (descs[i].mStartOffset < 0 || !descs[i].mDataByteSize)
                        continue;
                    if ((unsigned)descs[i].mStartOffset + descs[i].mDataByteSize >
                        in.mBuffers[0].mDataByteSize &&
                        in.mBuffers[0].mDataByteSize)
                        break;
                    if (!aac_feed(base + (unsigned)descs[i].mStartOffset,
                                  descs[i].mDataByteSize))
                        g_sc.lost++;
                    else {
                        g_pkts_fed++;
                        g_sc.st_fed++;
                        /* Kept so the close below can feed it once more.  A
                         * copy, because the engine's buffer is its own and
                         * gone by then. */
                        if (descs[i].mDataByteSize > g_sc.lastpkt_cap) {
                            unsigned char *grown = (unsigned char *)
                                realloc(g_sc.lastpkt, descs[i].mDataByteSize);
                            if (grown) {
                                g_sc.lastpkt = grown;
                                g_sc.lastpkt_cap = descs[i].mDataByteSize;
                            }
                        }
                        if (g_sc.lastpkt_cap >= descs[i].mDataByteSize) {
                            memcpy(g_sc.lastpkt,
                                   base + (unsigned)descs[i].mStartOffset,
                                   descs[i].mDataByteSize);
                            g_sc.lastpkt_len = descs[i].mDataByteSize;
                        } else
                            g_sc.lastpkt_len = 0;
                    }
                }
                /* Collect what is ready without ending the stream.
                 *
                 * Not aac_end(), which would drain *and* close, and not
                 * aac_flush_delay(), which re-feeds the last packet to shake
                 * Windows 7's held frame loose: mid-stream that duplicate
                 * would be payload, and it was -- one packet arrived three
                 * times over and the engine got the third copy.  The re-feed
                 * now lives where it is safe, in the no-more-data close
                 * below, fenced by arithmetic that cuts everything past the
                 * stream's true end.
                 *
                 * The priming, though, does have to come off, and for a long
                 * time it did not.
                 *
                 * The argument for leaving it was that Apple sets
                 * kAudioConverterPrimeMethod to None on this converter -- the
                 * 'prmm' SetProperty above -- so there is no priming to drop.
                 * That confuses two different things.  'prmm' None describes
                 * what *Apple's* decoder does; Media Foundation's emits the
                 * 2112-sample codec delay whatever Apple's API was told, and
                 * the engine sizes its buffer at frameCount * 1024 - 2112,
                 * which is Apple's priming written into the arithmetic.  So
                 * every unit reached the engine 2112 samples -- 96 ms -- late,
                 * 23 units to an utterance.  Individually the words survive
                 * that, which is why it stayed intelligible and merely sounded
                 * as though it were skipping.
                 *
                 * The other half of the old argument was real: taking 2112 off
                 * a 1024-sample refill would delete it outright.  So the trim
                 * belongs to the *stream*, not the refill -- carried across
                 * refills until it is used up, which is exactly what
                 * aac_decode_unit does on the SoundConverter side, and that
                 * side is byte-perfect. */
                aac_drain();
                if (g_sc.prime_left && g_sc.pcm_n) {
                    unsigned drop = g_sc.prime_left < g_sc.pcm_n
                                  ? g_sc.prime_left : g_sc.pcm_n;
                    memmove(g_sc.pcm, g_sc.pcm + drop,
                            (g_sc.pcm_n - drop) * sizeof(short));
                    g_sc.pcm_n      -= drop;
                    g_sc.prime_left -= drop;
                }
                /* Is the noise already here, or does the engine add it?
                 *
                 * Roughness -- mean|x[n+1]-x[n]| over mean|x[n]| -- separates
                 * the two without guessing. Clean speech through this same
                 * output path measures about 0.10; Alex's finished audio
                 * measures 0.30. If the decoder's own output is near 0.10 the
                 * noise is downstream of it, and if it is near 0.30 it is the
                 * codec path. */
                /* And optionally the samples themselves, so the decode can
                 * be listened to on its own, before the engine has touched
                 * it. A number can say "rougher"; only the ear says why. */
                if (getenv("TIGER_PCM_DUMP") && g_sc.pcm_n) {
                    FILE *f = fopen(getenv("TIGER_PCM_DUMP"), "ab");
                    if (f) {
                        fwrite(g_sc.pcm, 2, g_sc.pcm_n, f);
                        fclose(f);
                    }
                }
                if (getenv("TIGER_PCM_STATS")) {
                    unsigned k;
                    for (k = 0; k + 1 < g_sc.pcm_n; k++) {
                        int a = g_sc.pcm[k], b = g_sc.pcm[k + 1];
                        g_pcmstat_abs += (double)(a < 0 ? -a : a);
                        g_pcmstat_d   += (double)((b - a) < 0 ? (a - b) : (b - a));
                        g_pcmstat_n++;
                    }
                }
                if (g_verbose && g_sc.sessions <= 1 && rounds <= 3)
                    printf("  [ac] round %d: %u packet(s) -> %u frames, "
                           "asked for %u\n", rounds, packets, g_sc.pcm_n, want);
                /* TIGER_AAC_TRACE: every refill of every stream, not just the
                 * first three of the first one.  Lion opens twenty-five short
                 * streams where Leopard opens fifteen long ones, so the
                 * interesting behaviour is never in stream 1. */
                if (g_ac_trace)
                    fprintf(stderr, "  [ac] s%u r%d: %u pkt -> %u frames, "
                            "asked %u\n", g_sc.sessions, rounds, packets,
                            g_sc.pcm_n, want);
            } else {
                /* The engine has no more compressed data: flush the decoder's
                 * tail and close the stream, so the next utterance starts
                 * clean rather than with this one's overlap.
                 *
                 * And shake loose the frame Windows 7's decoder holds back.
                 * That decoder withholds its newest frame even through
                 * COMMAND_DRAIN, so on Windows 7 every stream used to end one
                 * AAC frame -- 46 ms -- short, and Lion opens twenty-five
                 * streams to an utterance: the tails of words went missing
                 * (issue #13).  The unit path has re-fed the last packet for
                 * this since Tiger; here that was long unsafe, because
                 * mid-stream the duplicate would be *payload* -- it once
                 * arrived three times over.  At the close it is safe, because
                 * the arithmetic below knows exactly where the stream ends:
                 * fed access units say how many samples exist, and everything
                 * past that is the duplicate, cut before the engine sees it.
                 * A decoder that withheld nothing therefore loses only the
                 * duplicate, and Windows 7 gets its real tail back. */
                if (g_sc.ac_live) {
                    unsigned expect, given, avail;
                    int k;
                    for (k = 0; k < 2 && g_sc.lastpkt_len; k++)
                        aac_feed(g_sc.lastpkt, g_sc.lastpkt_len);
                    aac_end();
                    /* The drains above bypassed the collection point, so the
                     * codec delay of a stream this short comes off here --
                     * without this, a two-packet stream would hand the engine
                     * priming as payload and the clamp would then cut real
                     * samples off its tail. */
                    if (g_sc.prime_left && g_sc.pcm_n) {
                        unsigned drop = g_sc.prime_left < g_sc.pcm_n
                                      ? g_sc.prime_left : g_sc.pcm_n;
                        memmove(g_sc.pcm, g_sc.pcm + drop,
                                (g_sc.pcm_n - drop) * sizeof(short));
                        g_sc.pcm_n      -= drop;
                        g_sc.prime_left -= drop;
                    }
                    expect = g_sc.st_fed * AAC_FRAME;
                    expect = expect > AAC_PRIMING ? expect - AAC_PRIMING : 0;
                    given = g_sc.st_given;
                    avail = g_sc.pcm_n - g_sc.pcm_pos;
                    if (given + avail > expect)
                        g_sc.pcm_n = g_sc.pcm_pos +
                                     (expect > given ? expect - given : 0);
                    g_sc.ac_live = 0;
                }
                break;
            }
        }
        /* Nothing at all, after sixty-four tries.  The engine still
         * advances its clock for this unit, so the render keeps its
         * full length and simply has a hole in it -- which is why a
         * missing word never shortens the wav and never looks like a
         * fault until something reads the words back. */
        if (g_sc.pcm_n == 0) g_ac_silent_streams++;
    }

    if (g_sc.pcm_pos >= g_sc.pcm_n)
        break;                      /* the source is dry; a short fill is
                                     * now the honest answer */
    {
        unsigned take = g_sc.pcm_n - g_sc.pcm_pos;
        if (take > want - give) take = want - give;
        memcpy((unsigned char *)outdata->mBuffers[0].mData + give * 2,
               g_sc.pcm + g_sc.pcm_pos, take * 2);
        g_sc.pcm_pos += take;
        g_frames_out += take;
        g_sc.st_given += take;
        give += take;
    }
    }
    /* A short fill is the honest answer at a stream's end -- but Lion's
     * engine spends its whole request regardless of the count it is handed,
     * so the frames past `give` are read whether or not anything was written
     * there.  Left alone they are whatever the last fill put in this buffer,
     * which the engine then plays: under the Windows 7 simulation that came
     * out audibly, as a render that differed run to run.  Silence is what a
     * short fill means, so write it down. */
    if (give < want)
        memset((unsigned char *)outdata->mBuffers[0].mData + give * 2, 0,
               (want - give) * 2);
    outdata->mBuffers[0].mDataByteSize = give * 2;
    *iopackets = give;
    return 0;
}

/* The same call, timed; see g_t_aac in tiger_host_shims.c. */
static int __cdecl sh_AudioConverterFillComplexBuffer(void *conv,
                                                      ac_input_proc proc,
                                                      void *user,
                                                      unsigned *iopackets,
                                                      au_bufferlist *outdata,
                                                      au_packetdesc *outdesc)
{
    __int64 t0 = prof_now();
    int rc = sh_AudioConverterFillComplexBuffer_inner(conv, proc, user,
                                                      iopackets, outdata,
                                                      outdesc);
    g_t_aac += prof_now() - t0; g_n_aac++;
    return rc;
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
        if (!p[k]) { fprintf(stderr, "  [snd] %s format: NULL\n", k ? "out" : "in"); continue; }
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
{ (void)u; (void)s; (void)e; fprintf(stderr, "  [au] Reset\n"); return 0; }
static int __cdecl sh_SpeechBusy(void) { return 0; }
