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
/* 'TGR4' asks for the same audio, streamed.  A separate magic rather than a
 * flag because the failure it guards against is a stale tiger_host.exe left in
 * an add-on folder: a host that cannot stream must refuse the request outright
 * rather than answer it in a shape the driver will misread.  That is why the
 * request magic carries a version at all. */
#define REQ_MAGIC_STREAM 0x54475234u    /* 'TGR4' */
#define RSP_MAGIC 0x54475253u           /* 'TGRS' */
/* How far behind the collected frontier a streamed chunk stops.
 *
 * Measured rather than chosen: across Alex, Fred and Vicki, at 120, 180 and
 * 300 wpm, on text long enough to span many epochs, slices land behind the
 * frontier 19 to 53 times per utterance and never by more than *one* frame --
 * the single-frame probe that opens each epoch, covered again immediately by
 * the 229-frame slice at the same position.  Sent audio cannot be unsent, so
 * hold back a margin far larger than anything observed; 512 frames is 23 ms,
 * which no listener notices and no measurement came close to. */
#define STREAM_LOOKBEHIND 512u

/* Abandoning an utterance the listener has interrupted.
 *
 * Measured on Tomi's machine: 38% of utterances waited more than 200 ms before
 * they even began rendering, the worst 931 ms, because the worker was still
 * reading out the *previous* response.  Cancelling stops the sound instantly,
 * but the engine carried on synthesising all seven seconds of audio nobody
 * would ever hear, and the next thing the user asked for queued behind it.
 * Streaming made the first sound arrive in 20 ms and this hid the win.
 *
 * A Windows event rather than anything on the pipe: the driver's cancel()
 * runs on NVDA's main thread -- the one that turns keystrokes into speech --
 * so it must never block, and SetEvent cannot.  The name comes in on the
 * environment when the host is started. */
static HANDLE g_cancel_ev;
/* Whether an interrupted channel is reset with soReset.
 *
 * It flushes what is left, but it resets the channel to its defaults --
 * and for Alex that means the voice, whose sample bank is 701 MB.  The
 * driver's own test caught it: interrupting cost 2887 ms afterwards.
 * TIGER_RESET=1 puts it back for measuring; the settle below is what
 * carries the load. */
static int g_use_reset;

static int cancel_requested(void)
{
    return g_cancel_ev &&
           WaitForSingleObject(g_cancel_ev, 0) == WAIT_OBJECT_0;
}

/* StopSpeechAt with kImmediate: give up on this utterance now.  The engine
 * stops producing, the wait loop below falls out, and the response ends -- so
 * the pipe stays in step and the driver is free for the next utterance. */
typedef int (*SEStop_t)(void *chan, unsigned where);

/* SpeechStatus: the first long of the struct is outputBusy.
 *
 * Needed because stopping is not the same as having stopped.  Proved with
 * Whisper: interrupt a sentence, ask for the next one, and the engine speaks
 * the *remainder of the abandoned text first* --
 *
 *   "or the one after that, or the one after that, what I read is that the
 *    infrared cameras are intended to capture detail."
 *
 * -- which is the fragment of the post above arriving at the head of the post
 * below, exactly as reported.  SEStopSpeechAt(kImmediate) returns before the
 * channel is idle, and text handed to a still-busy channel queues behind what
 * is already in it.  So wait for it. */
typedef int (*SEStatus_t)(void *chan, void *info);
#define SEL_RATE  0x72617465u           /* 'rate' -- soRate, Fixed wpm */
#define SEL_PITCH 0x70626173u           /* 'pbas' -- soPitchBase, Fixed */
#define SEL_DELIM 0x646c696du           /* 'dlim' -- soCommandDelimiter */
#define SEL_RESET 0x72736574u           /* 'rset' -- soReset */
#define SEL_STATUS 0x73746174u          /* 'stat' -- soStatus */

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

/* Break a long unbroken run of one letter, because the morphology cannot
 * survive one.
 *
 * `SLPrefixMorph::AddAffix` keeps a saved word's length in a signed byte and
 * adds each affix to it unchecked (the disassembly is quoted in
 * tiger_host_shims.c).  A run of the same letter is what makes that overflow
 * reachable: every position in the run offers the morphology the same prefix
 * match, so the decompositions multiply, the byte climbs past 127 and reads
 * back negative.  Twenty x's followed by "the" is enough -- issue #4 -- and it
 * fails two different ways depending on how far it gets: a memmove of four
 * gigabytes, or a quieter overrun of one record into the next that surfaces
 * later as a null dereference in the synthesis path.
 *
 * A run this long is not a word in any language.  The longest genuine repeat
 * in English is two letters, three in a compound, and words that *are* long --
 * `antidisestablishmentarianism`, `supercalifragilisticexpialidocious` -- do
 * not trigger it, because they are not repetitive.  So ten is far above
 * anything real and far below the threshold: measured, Alex survives twelve
 * and dies at sixteen.
 *
 * A space rather than a truncation, deliberately: every character the user has
 * on screen is still spoken, in the same order.  The engine sees two shorter
 * tokens instead of one impossible one, which is the whole of the fix.
 */
#define MAX_LETTER_RUN 10

static int is_letter(unsigned char c)
{
    return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z');
}

static char *break_letter_runs(char *text, unsigned *len)
{
    unsigned n = *len, i, j = 0, run = 0;
    char *out;
    if (!text || !n) return text;
    out = (char *)malloc(n + n / MAX_LETTER_RUN + 2);
    if (!out) return text;                  /* the crash is better than none */
    for (i = 0; i < n; i++) {
        if (i && text[i] == text[i - 1] && is_letter((unsigned char)text[i]))
            run++;
        else
            run = 0;
        if (run >= MAX_LETTER_RUN) { out[j++] = ' '; run = 0; }
        out[j++] = text[i];
    }
    out[j] = 0;
    if (j != n)
        fprintf(stderr, "tiger_host: broke %u long letter run(s) in this "
                        "utterance -- the dictionary's morphology overflows on "
                        "them (issue #4); every character is still spoken\n",
                j - n);
    free(text);
    *len = j;
    return out;
}

/* 32-bit float to 16-bit PCM, the one place the conversion is written.
 *
 * Streamed and blocking responses have to produce identical bytes -- that is
 * the invariant the streaming test rests on -- so they cannot each carry their
 * own copy of this loop. */
static void put_frames(unsigned from, unsigned to)
{
    unsigned i;
    for (i = from; i < to; i++) {
        double v = g_pcm[i];
        short s;
        if (v > 1.0) v = 1.0;
        if (v < -1.0) v = -1.0;
        s = (short)(v * 32767.0);
        fwrite(&s, 2, 1, stdout);
    }
}

/* Send frames [sent, upto) as one chunk and return the new frontier.  A chunk
 * is a frame count followed by that many samples; a count of zero ends the
 * response, so a chunk is never written empty. */
static unsigned stream_chunk(unsigned sent, unsigned upto)
{
    unsigned n;
    if (upto <= sent) return sent;
    n = upto - sent;
    fwrite(&n, 4, 1, stdout);
    put_frames(sent, upto);
    fflush(stdout);
    return upto;
}

static int serve(image *mt, void *chan, const char *voicesdir)
{
    SEUseVoice_t use     = (SEUseVoice_t)find_export(mt, "_SEUseVoice");
    /* **The shared layer, not a second copy of it.**  This function used to
     * resolve `_SESpeakBuffer` and `_SESetSpeechInfo` for itself -- which is
     * exactly the drift tiger_host_speech.c warns about at the top, and it
     * cost both halves of Lion's serve mode: 10.7 exports no `SESpeakBuffer`
     * at all, so the text call jumped to address zero, and its
     * `SESetSpeechInfo` answers -231 to `rate`, so every utterance would have
     * come out at the engine's own 180 wpm. The one-shot render has used this
     * layer since Lion's text path was written; serve mode had not. */
    speech_api   api     = speech_api_of(mt);
    SESetInfo_t  setinfo = (SESetInfo_t)find_export(mt, "_SESetSpeechInfo");
    SEStop_t     stopnow = (SEStop_t)find_export(mt, "_SEStopSpeechAt");
    SEStatus_t   status  = (SEStatus_t)find_export(mt, "_SESpeechStatus");
    {
        const char *evname = getenv("TIGER_CANCEL_EVENT");
        if (evname && *evname) {
            g_cancel_ev = OpenEventA(SYNCHRONIZE, FALSE, evname);
            if (!g_cancel_ev)
                fprintf(stderr, "tiger_host: cannot open the cancel event, so "
                                "an interrupted utterance will be rendered to "
                                "the end\n");
        }
    }
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
        int streaming;
        int cancelled = 0;
        unsigned sent = 0;
        double speak_ms = 0.0;
        char name[128];
        char *text;

        if (!read_all(stdin, &magic, 4)) return 0;      /* driver went away */
        if (magic != REQ_MAGIC && magic != REQ_MAGIC_STREAM) return 1;
        streaming = (magic == REQ_MAGIC_STREAM);
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
        text = break_letter_runs(text, &textlen);

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
                    if (!get_param(&api, chan, PARAM_PITCH, &basepitch))
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
        if (!err && wpm > 0) {
            unsigned fixed = (unsigned)wpm << 16;       /* Fixed 16.16 wpm */
            int rc = set_param(&api, chan, PARAM_RATE, fixed);
            if (rc == 0) currate = wpm;
            else
                /* Worth saying out loud.  A rate that fails to apply is not a
                 * subtle fault: the engine falls back to its own 180 wpm, and
                 * someone reading at 400 hears the whole post crawl.  That was
                 * reported as lag, and looked like one. */
                fprintf(stderr, "tiger_host: the engine refused %d wpm "
                                "(OSErr %d), so this utterance is at its own "
                                "default rate\n", wpm, rc);
        }
        /* Pitch is re-applied whenever it changes *or* the voice changed: a
         * new voice arrives with its own pitch and would otherwise keep it
         * until the user happened to move the slider. */
        if (!err && basepitch) {
            /* offset is tenths of a semitone, and the scale is semitones */
            unsigned fx = (unsigned)((int)basepitch + (pitch * 65536) / 10);
            if (set_param(&api, chan, PARAM_PITCH, fx) == 0) curpitch = pitch;
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


        g_pcm_n = 0; g_slices = 0; g_stopped = 0; g_empty_run = 0;
        g_dup_slices = 0; g_have_last = 0; g_p_drops = 0;
        g_epoch_base = 0; g_last_stime = 0.0; g_have_origin = 0;
        /* A new utterance: anything still in flight for the last one is stale
         * from here on, and the pacer will complete those slices without
         * collecting them. */
        g_utt++; g_stale_slices = 0;
        /* Consume any cancel left over from before this request.
         *
         * The driver clears the event before it writes a request, but it
         * cannot close the gap: arrowing quickly through a timeline sends
         * cancels faster than requests, and one landing between that clear
         * and this loop aborted an utterance nobody had cancelled.  The user
         * hears nothing at all for it -- which is how a fix for the lag came
         * to *be* the lag.
         *
         * A cancel that arrives from here on is genuinely for this utterance,
         * because this is where it starts. */
        (void)cancel_requested();
        g_sc.sessions = 0; g_sc.resets = 0; g_sc.lost = 0; g_pkts_fed = 0;
        g_sql_rows = 0;
        g_back_slices = 0; g_back_max = 0;
        g_utt_t0 = wall_ms(); g_first_slice_ms = -1.0; g_last_slice_ms = 0.0;
        g_slice_gap_max = 0.0; g_slice_prev_ms = 0.0;
        g_stat_ok = g_stat_refused = g_stat_idle = 0;
        /* SESpeakBuffer's fourth argument is the Speech Manager's own flags
         * word, and kNoEndingProsody (1) looked like the lever for a screen
         * reader: NVDA speaks in fragments, and each one gets the falling
         * pitch that ends a sentence whether or not one ended.  Measured, it
         * changes nothing at all -- byte-identical on a fragment and on a
         * paragraph -- and this request's own flags word already means
         * REQF_COMMANDS, so forwarding it would have made "accept embedded
         * commands" quietly mean kNoEndingProsody as well.  Zero it stays. */
        if (g_gcd_log)
            fprintf(stderr, "tiger_host: [%u] speaking %u byte(s)\n",
                    g_utt, textlen);
        if (!err) err = speak_text(&api, chan, text, textlen);
        speak_ms = wall_ms() - g_utt_t0;
        if (g_gcd_log)
            fprintf(stderr, "tiger_host: [%u] speak returned %d at %.1f ms\n",
                    g_utt, err, speak_ms);
        free(text);

        /* A streamed response says how it went before it says anything else.
         *
         * SESpeakBuffer returns in about a tenth of a millisecond and the
         * first slice arrives a millisecond after that, so `err` is known long
         * before the audio is, and the driver can start listening for chunks
         * immediately instead of waiting out the render. */
        if (streaming) {
            magic = RSP_MAGIC;
            fwrite(&magic, 4, 1, stdout);
            fwrite(&err, 4, 1, stdout);
            fflush(stdout);
        }

        /* AUGraphStop is the engine's own end-of-utterance signal, with a
         * quiet-period fallback in case an utterance ends another way.
         *
         * **On 10.7 the fallback is not a fallback, it is the only path.**
         * Tiger and Leopard start and stop the graph once per utterance --
         * measured, 92 of 92 and 96 of 96 -- and leave here the moment the
         * engine stops it.  Lion starts the graph once for the whole session
         * and never stops it at all, 0 of 96, so every Lion utterance sits out
         * the entire quiet period after its audio is already complete.  That
         * is a fixed 300 ms on the end of every Lion render, and it is why
         * Lion measures 16x real time where Leopard measures 87x.
         *
         * **Shortening it was tried, and reverted.**  The widest silence
         * between two slices of one utterance, over 284 utterances across all
         * three generations and every voice, was 40.2 ms -- so 150 ms looked
         * like three and a half times the worst case, and Tiger's and
         * Leopard's renders came back byte-identical with it.
         *
         * They were the wrong 284 utterances.  Every one was an ordinary
         * sentence, and the gap that matters does not live in ordinary
         * sentences: an unbroken token of 370 characters gives Alex enough
         * morphology to go quiet for longer than 150 ms in the middle of one
         * utterance, and at 150 the four cases in
         * `tests/leopard/test_long_tokens.py` fail.  Those are Brandon's
         * issue #4, and they are in the suite precisely because nobody knows
         * which change cured it.
         *
         * So the number stays where a reported bug says it has to be, and
         * Lion pays 300 ms it should not have to.  The honest fix is not a
         * shorter guess -- it is `kSpeechStatusOutputBusy`, which 10.7 does
         * export and which would answer the question directly instead of
         * inferring it from silence. */
        if (!err) {
            unsigned last = 0, quiet = 0, ticks = 0;
            while (!g_stopped && quiet < 30 && ticks < 900) {
                Sleep(10); ticks++;
                /* **Ask, rather than infer from silence.**
                 *
                 * The quiet period below is a guess that costs 300 ms on
                 * every 10.7 utterance, because 10.7 never calls AUGraphStop
                 * and there is nothing else to end on.  The Speech Manager's
                 * own `stat` selector answers the question directly: its
                 * first long is `outputBusy`.
                 *
                 * Guarded on audio having arrived, because the engine has not
                 * necessarily started when the first tick runs and an idle
                 * answer then would end the utterance before it began. */
                if (g_ask_status && api.getinfo && g_pcm_n) {
                    long st[4];
                    memset(st, 0, sizeof(st));
                    if (call_aligned3((void *)api.getinfo, chan,
                                      (void *)SEL_STATUS, st) == 0) {
                        g_stat_ok++;
                        if (st[0] == 0) { g_stat_idle++; break; }
                    } else {
                        g_stat_refused++;
                    }
                }
                if (cancel_requested()) {
                    /* The listener has moved on.  Stop the engine rather than
                     * render the rest of a sentence nobody will hear -- the
                     * driver cannot start the next utterance until this
                     * response ends, so finishing it politely *is* the lag. */
                    if (stopnow)
                        call_aligned2((void *)stopnow, chan, (void *)0);
                    /* Stopping the channel loses its rate and pitch.
                     *
                     * These are cached so that an unchanged setting costs no
                     * call, and the cache does not know the engine has been
                     * reset underneath it -- so the utterance *after* an
                     * interruption was spoken at the engine's own default.
                     * Measured at 280 wpm: 1.64x and 2.66x longer than it
                     * should be, which at a fast reading rate is the whole
                     * post crawling.  Invisible at 180 wpm, because 180 is
                     * what it falls back to.
                     *
                     * Forget what we think the channel holds; the next
                     * utterance sets it again. */
                    currate = -1;
                    curpitch = -1;
                    /* Stopping is not the same as having stopped.  Wait for
                     * the channel to go idle, or the text of the *next*
                     * utterance queues behind what is left of this one and is
                     * spoken after it.  Bounded: an engine that never goes
                     * idle must not wedge the driver, and speaking something
                     * stale is better than speaking nothing ever again. */
                    /* Then throw away what is left in the channel.
                     *
                     * 'rset' is the Speech Manager's own reset selector, and
                     * it is what actually discards the remainder: waiting for
                     * outputBusy to clear does not work, because it never
                     * does -- measured, the channel still reports busy 400 ms
                     * after being stopped, and the wait alone was hiding the
                     * fault by giving the engine time to drain.  A fix that
                     * works by being slow is the fault wearing a hat. */
                    if (setinfo && g_use_reset) {
                        unsigned zero = 0;
                        call_aligned3((void *)setinfo, chan, (void *)SEL_RESET,
                                      &zero);
                    }
                    /* Then let the channel settle -- and this one is honest
                     * about what it is.
                     *
                     * It asks GetSpeechInfo 'stat', whose first long is
                     * outputBusy, and gives up after a hundred milliseconds.
                     * Measured on this engine, outputBusy *never* clears: the
                     * loop runs its full count every time.  So this is a
                     * bounded wait wearing a poll's clothing, kept because
                     * removing it brings the fragment back (0 of 8 with it,
                     * 1 of 8 without) and because the poll costs nothing if a
                     * future engine does report itself idle.
                     *
                     * A hundred milliseconds against 2255 ms of the original
                     * fault is a trade worth making, but it is a delay, and
                     * calling it a status check would be a lie. */
                    {
                        /* Wait for the stragglers to stop, rather than for a
                         * fixed time.
                         *
                         * The engine keeps handing over slices for a little
                         * while after being stopped -- the host counts them,
                         * and it is four or five -- and any that arrive once
                         * the next request has begun are stamped as *its*
                         * audio and are heard at the head of it.  A fixed
                         * hundred milliseconds caught most and missed some:
                         * the residue was two words, "after that.", still
                         * riding in front of the next post.
                         *
                         * So watch the slice counter instead and leave when it
                         * has been still for a moment.  Usually quicker than
                         * the fixed wait, and it does not guess. */
                        unsigned last = g_slices, quiet = 0;
                        int spin;
                        long info[4];
                        for (spin = 0; spin < 100 && quiet < 15; spin++) {
                            Sleep(2);
                            if (g_slices != last) { last = g_slices; quiet = 0; }
                            else quiet++;
                            /* 10.7 moved `stat` to kSpeechStatusProperty as
                             * well, so this asks and is refused there. The
                             * loop is a bounded settle either way -- it only
                             * costs the full 200 ms instead of stopping as
                             * soon as the engine says it is idle. */
                            if (api.getinfo) {
                                memset(info, 0, sizeof(info));
                                if (call_aligned3((void *)api.getinfo, chan,
                                                  (void *)SEL_STATUS,
                                                  info) == 0 && info[0] == 0)
                                    break;      /* it says it is idle */
                            }
                        }
                        if (g_float_stats)
                            fprintf(stderr, "  [se] settled after %d ms\n",
                                    spin * 2);
                    }
                    cancelled = 1;
                    break;
                }
                if (g_slices != last) { last = g_slices; quiet = 0; }
                else quiet++;
                /* Send what has settled, every tick.  The engine runs at about
                 * ninety times real time, so after the first chunk there are
                 * seconds of audio buffered ahead and playback cannot underrun
                 * however long the text is. */
                if (streaming && g_pcm_n > STREAM_LOOKBEHIND)
                    sent = stream_chunk(sent, g_pcm_n - STREAM_LOOKBEHIND);
            }
            /* The engine has stopped *scheduling*, which is not the same as
             * the audio having been read.
             *
             * g_slices counts slices as they arrive; collect_slice runs a beat
             * later on the pacer thread, because a ScheduledSoundPlayer's
             * completion fires when the audio has played rather than when it
             * was queued.  Returning as soon as scheduling goes quiet takes a
             * snapshot of a timeline the pacer is still filling in, so the
             * tail of the utterance is missing -- and then the next request
             * sets g_pcm_n back to 0 while the pacer is still writing, which
             * drops one utterance's audio into the middle of the next.
             *
             * It is timing, so it did not look like a bug: the same text came
             * back 2.29 s in a warm session and 4.49 s in a cold one, and only
             * the long one had all the words in it.
             *
             * Wait for the queue to drain.  Two-millisecond ticks, a second's
             * worth: an utterance's worth of slices drains in a few of them at
             * the default clock, and if it ever does not, say so. */
            if (g_gcd_log)
                fprintf(stderr, "tiger_host: [%u] scheduling ended, %u slice(s)"
                                ", %u frame(s)\n", g_utt, g_slices, g_pcm_n);
            for (ticks = 0; !pacer_idle() && ticks < 500; ticks++) Sleep(2);
            if (!pacer_idle())
                fprintf(stderr, "tiger_host: the pacer was still collecting "
                                "audio a second after the engine stopped\n");
            if (cancelled)
                printf("  [au] utterance abandoned at %u frames -- the "
                       "listener interrupted\n", g_pcm_n);
            if (g_stale_slices)
                printf("  [au] %u slice(s) arrived for an utterance already "
                       "answered, and were not collected\n", g_stale_slices);
        }

        /* Anything the engine did that a user would notice goes out at a
         * level they will actually see.
         *
         * The driver files a line beginning "tiger_host:" at WARNING and
         * everything else at DEBUG, and DEBUG is off by default and awkward to
         * turn on -- which is precisely how a broken start-up dialog sat in a
         * log for months.  A diagnostic nobody can read has not been reported.
         * So say it once per affected utterance, plainly, and stay silent when
         * there is nothing wrong. */
        if (g_dup_slices)
            fprintf(stderr, "tiger_host: %u repeated slice(s) refused in this "
                            "utterance -- the engine re-sent audio it had "
                            "already given us\n", g_dup_slices);
        if (g_empty_run >= SLICE_EMPTY_LIMIT)
            fprintf(stderr, "tiger_host: the engine stopped producing audio "
                            "after %u frames\n", g_pcm_n);
        if (g_p_drops)
            fprintf(stderr, "tiger_host: %u slice(s) dropped -- the pacer "
                            "queue overflowed and that audio is lost\n",
                    g_p_drops);
        /* The two decoder drivers side by side.  Tiger's engine drives
         * SoundConverter, which decodes one self-contained unit at a time and
         * is byte-perfect; Leopard's drives AudioConverter, which streams.
         * Same voice bytes, same decode core -- so the profile is where the
         * difference has to show. */
        if (g_float_stats)
            fprintf(stderr, "  [aac] %u session(s), %u reset(s), %u packet(s) "
                            "fed, %u lost\n",
                    g_sc.sessions, g_sc.resets, g_pkts_fed, g_sc.lost);
        if (g_float_stats)
            fprintf(stderr, "  [sql] %u database(s) open, %u phrasing row(s) "
                            "matched\n", g_sql_opens, g_sql_rows);
        /* What a streamed response would need to know: whether SESpeakBuffer
         * returns before the audio exists (so the wait loop is free to send
         * what has arrived), how long the audio kept coming, and whether any
         * slice landed behind frames already collected -- which is the one
         * thing streaming cannot survive, because sent audio cannot be
         * unsent. */
        if (g_float_stats)
            fprintf(stderr, "  [wait] usleep %u/%.0f ms, mpqueue %u/%.0f ms, "
                            "gcd timer %u/%.0f ms, condvar %u/%.0f ms\n",
                    g_w_usleep_n, g_w_usleep_ms, g_w_mpq_n, g_w_mpq_ms,
                    g_w_src_n, g_w_src_ms, g_w_cnd_n, g_w_cnd_ms);
        if (g_float_stats && g_gcd_handler)
            fprintf(stderr, "  [gcd] handler %s\n",
                    engine_symbol(g_gcd_handler));
        if (g_float_stats)
            fprintf(stderr, "  [stat] answered %u, refused %u, said idle %u\n",
                    g_stat_ok, g_stat_refused, g_stat_idle);
        if (g_float_stats)
            fprintf(stderr, "  [au] start %u, stop %u, uninitialize %u, "
                            "isInitialized %u, widest gap between slices "
                            "%.1f ms\n",
                    g_au_start, g_au_stop, g_au_uninit, g_au_isinit,
                    g_slice_gap_max);
        if (g_float_stats)
            fprintf(stderr, "  [clock] gettimeofday %u, UpTime %u\n",
                    g_c_gtod, g_c_uptime);
        if (g_float_stats)
            fprintf(stderr, "  [gcd] set_timer %u (%u for now), waits: %u zero-delay, %u fired, %u re-armed\n",
                    g_w_settimer, g_w_settimer_now, g_w_src_zero,
                    g_w_src_fire, g_w_src_rearm);
        if (g_float_stats)
            fprintf(stderr, "  [str] speak returned at %.1f ms, first slice "
                            "%.1f ms, last %.1f ms, %u frame(s); %u slice(s) "
                            "landed behind the frontier, furthest %u frame(s)"
                            "\n",
                    speak_ms, g_first_slice_ms, g_last_slice_ms, g_pcm_n,
                    g_back_slices, g_back_max);

        if (streaming) {
            /* The tail, then a zero-length chunk to say that is all of it.
             * The margin held back during the render is released here, so a
             * streamed response carries exactly the frames a blocking one
             * would -- which is what the streaming test checks. */
            unsigned zero = 0;
            (void)i; (void)nframes;
            /* An abandoned utterance sends no tail.  Every frame of it is
             * audio the listener has already declined, and pushing what may
             * be megabytes of it through the pipe for the driver to discard
             * is most of the delay the cancel exists to remove -- measured,
             * it was the difference between 446 ms and not noticing. */
            if (!cancelled)
                sent = stream_chunk(sent, g_pcm_n);
            fwrite(&zero, 4, 1, stdout);
            fflush(stdout);
        } else {
            nframes = g_pcm_n;
            magic = RSP_MAGIC;
            fwrite(&magic, 4, 1, stdout);
            fwrite(&err, 4, 1, stdout);
            fwrite(&nframes, 4, 1, stdout);
            put_frames(0, nframes);
            fflush(stdout);
        }
    }
}
