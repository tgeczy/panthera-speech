/* tiger_host_serve.c -- serve mode: the protocol the driver speaks.
 *
 * Part of tiger_host.c, which includes it; see there for why this is one
 * translation unit. */

/* ---- serve mode -------------------------------------------------------- */
/*
 * One long-lived process behind the NVDA driver.  Opening a channel costs a
 * 2.1 MB dictionary map and a voice load, so it happens once; after that a
 * request is text in, PCM out, over stdin/stdout.
 *
 * The voice is named rather than numbered, and the host reads the creator
 * OSType and id straight out of the bundle's VoiceDescription -- so nothing
 * here carries a table that could disagree with the files on disk.
 *
 *   request   'TGR3' | i32 wpm | i32 pitch | u32 flags | u32 namelen
 *                      | u32 textlen | name | text
 *   response  'TGRS' | i32 status | u32 nframes | i16 pcm[nframes]
 *
 * `pitch` is an **offset in tenths of a semitone** from the voice's own pitch,
 * not an absolute value.  Every voice has its own natural pitch -- Fred sits
 * near 127 Hz and Bruce near 135 -- so an absolute scale would mean the middle
 * of the slider sounded different for each one.  The host asks the engine for
 * the voice's default with 'pbas' and applies the offset to that, which keeps
 * the knowledge of the scale where the query lives.
 *
 * The scale is semitones: measured, 'pbas' 40 -> 109 Hz and 50 -> 193 Hz, a
 * ratio of 1.77 over ten units, so ten units is very close to an octave.
 *
 * The magic changed with the field: a stale host in the add-on folder should
 * fail loudly rather than misread a request by one word and speak nonsense.
 */
#define REQ_MAGIC 0x54475233u           /* 'TGR3' */
#define RSP_MAGIC 0x54475253u           /* 'TGRS' */
#define SEL_RATE  0x72617465u           /* 'rate' -- soRate, Fixed wpm */
#define SEL_PITCH 0x70626173u           /* 'pbas' -- soPitchBase, Fixed */
#define SEL_DELIM 0x646c696du           /* 'dlim' -- soCommandDelimiter */

/* Flags word in the request. */
#define REQF_COMMANDS 0x1               /* honour [[...]] in the text */

typedef int (__cdecl *SEUseVoice_t)(void *, const void *, const void *);
typedef int (__cdecl *SESpeak_t)(void *, const void *, long, long);
typedef int (__cdecl *SESetInfo_t)(void *, unsigned, const void *);
typedef int (__cdecl *SEGetInfo_t)(void *, unsigned, void *);

static int read_all(FILE *f, void *buf, size_t n)
{
    return n == 0 || fread(buf, 1, n, f) == n;
}

/* creator and id live at +4 and +8 of a VoiceDescription, big-endian. */
static int voice_spec(const char *dir, unsigned *creator, int *id)
{
    char path[CFPATH];
    unsigned char h[12];
    FILE *f;
    _snprintf(path, sizeof(path), "%s/Contents/Resources/VoiceDescription", dir);
    path[sizeof(path) - 1] = 0;
    f = fopen(path, "rb");
    if (!f) return 0;
    if (fread(h, 1, sizeof(h), f) != sizeof(h)) { fclose(f); return 0; }
    fclose(f);
    *creator = ((unsigned)h[4] << 24) | (h[5] << 16) | (h[6] << 8) | h[7];
    *id      = ((int)h[8] << 24) | (h[9] << 16) | (h[10] << 8) | h[11];
    return 1;
}

static int serve(image *mt, void *chan, const char *voicesdir)
{
    SEUseVoice_t use     = (SEUseVoice_t)find_export(mt, "_SEUseVoice");
    SESpeak_t    speak   = (SESpeak_t)find_export(mt, "_SESpeakBuffer");
    SESetInfo_t  setinfo = (SESetInfo_t)find_export(mt, "_SESetSpeechInfo");
    SEGetInfo_t  getinfo = (SEGetInfo_t)find_export(mt, "_SEGetSpeechInfo");
    unsigned basepitch = 0;              /* the current voice's own pitch */
    char curvoice[128] = "";
    int  currate = -1;
    int  curpitch = -1;

    g_verbose = 0;                       /* the pipe carries audio, not chat */
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);

    for (;;) {
        unsigned magic, namelen, textlen, nframes, i;
        int wpm, pitch, err = 0, voicechanged;
        unsigned flags;
        char name[128];
        char *text;

        if (!read_all(stdin, &magic, 4)) return 0;      /* driver went away */
        if (magic != REQ_MAGIC) return 1;
        if (!read_all(stdin, &wpm, 4) ||
            !read_all(stdin, &pitch, 4) ||
            !read_all(stdin, &flags, 4) ||
            !read_all(stdin, &namelen, 4) ||
            !read_all(stdin, &textlen, 4)) return 1;
        if (namelen >= sizeof(name)) return 1;
        if (!read_all(stdin, name, namelen)) return 1;
        name[namelen] = 0;
        text = (char *)malloc(textlen + 1);
        if (!text || !read_all(stdin, text, textlen)) { free(text); return 1; }
        text[textlen] = 0;

        voicechanged = 0;
        if (strcmp(name, curvoice) != 0) {
            char dir[CFPATH];
            struct { unsigned creator; int id; } spec;
            _snprintf(dir, sizeof(dir), "%s/%s.SpeechVoice", voicesdir, name);
            dir[sizeof(dir) - 1] = 0;
            if (voice_spec(dir, &spec.creator, &spec.id)) {
                err = call_aligned3((void *)use, chan, &spec,
                                    cf_pinned(dir));
                if (!err) {
                    strcpy(curvoice, name);
                    voicechanged = 1;
                    /* Ask the voice what it sounds like before changing it. */
                    basepitch = 0;
                    if (getinfo && call_aligned3((void *)getinfo, chan,
                            (void *)SEL_PITCH, &basepitch) != 0)
                        basepitch = 0;
                    if (g_verbose)
                        printf("  [pitch] %s base %.2f\n", name,
                               basepitch / 65536.0);
                }
            } else err = -244;                          /* voiceNotFound */
        }
        /* Rate and pitch are re-applied on EVERY utterance, not only when
         * they change.  Text may contain embedded commands -- "[[rate 100]]"
         * works, and measurably -- and those change the channel for good.  A
         * stray command in a web page would otherwise slow every later
         * utterance until the user happened to move a slider.  Two cheap
         * setter calls buy immunity from that. */
        if (!err && wpm > 0 && setinfo) {
            unsigned fixed = (unsigned)wpm << 16;       /* Fixed 16.16 wpm */
            if (call_aligned3((void *)setinfo, chan, (void *)SEL_RATE,
                              &fixed) == 0) currate = wpm;
        }
        /* Pitch is re-applied whenever it changes *or* the voice changed: a
         * new voice arrives with its own pitch and would otherwise keep it
         * until the user happened to move the slider. */
        if (!err && setinfo && basepitch) {
            /* offset is tenths of a semitone, and the scale is semitones */
            unsigned fx = (unsigned)((int)basepitch + (pitch * 65536) / 10);
            if (call_aligned3((void *)setinfo, chan, (void *)SEL_PITCH,
                              &fx) == 0) curpitch = pitch;
        }

        /* Embedded commands -- "[[rate 100]]", "[[volm 0.5]]", even
         * "[[inpt TUNE]]" for singing -- are a real feature of this front end,
         * all measured working.  Whether to honour them is the driver's
         * decision, and it enforces it by removing them from the text: the
         * `soCommandDelimiter` selector looked like the tidier lever, but
         * zeroing the delimiters made text containing "[[" produce silence
         * rather than speaking it literally, and an unlikely delimiter
         * character did the same.  Stripping in the driver is deterministic
         * and testable, which this was not.
         *
         * What the host does guarantee is that a command cannot outlive its
         * utterance: rate and pitch are re-applied above, every time.
         */
        (void)flags;

        g_pcm_n = 0; g_slices = 0; g_stopped = 0; g_empty_run = 0;
        if (!err) err = call_aligned4((void *)speak, chan, text,
                                      (void *)textlen, (void *)0);
        free(text);

        /* AUGraphStop is the engine's own end-of-utterance signal, with a
         * quiet-period fallback in case an utterance ends another way. */
        if (!err) {
            unsigned last = 0, quiet = 0, ticks = 0;
            while (!g_stopped && quiet < 30 && ticks < 900) {
                Sleep(10); ticks++;
                if (g_slices != last) { last = g_slices; quiet = 0; }
                else quiet++;
            }
        }

        nframes = g_pcm_n;
        magic = RSP_MAGIC;
        fwrite(&magic, 4, 1, stdout);
        fwrite(&err, 4, 1, stdout);
        fwrite(&nframes, 4, 1, stdout);
        for (i = 0; i < nframes; i++) {
            double v = g_pcm[i];
            short s;
            if (v > 1.0) v = 1.0;
            if (v < -1.0) v = -1.0;
            s = (short)(v * 32767.0);
            fwrite(&s, 2, 1, stdout);
        }
        fflush(stdout);
    }
}
