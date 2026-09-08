/* tiger_host_audio.c -- AUGraph, where the audio arrives, and the pacer.
 *
 * Part of tiger_host.c, which includes it; see there for why this is one
 * translation unit. */

/* ---- AUGraph, instrumented -------------------------------------------- */
/*
 * Not an implementation yet -- an experiment.  The question it answers is
 * whether `SESpeakBuffer` returning noErr means "synthesised" or merely
 * "accepted", because the previous run built a graph and then immediately
 * called DisposeAUGraph, and those two readings are indistinguishable from
 * the outside.
 *
 * The stub thunks returned noErr without writing any out-parameter, so the
 * engine received an uninitialised node and audio unit.  Handing back real
 * tagged objects removes that as an explanation: whatever it does next, it
 * does with valid handles.
 */
typedef struct { unsigned tag; int id; } au_obj;
/* The engine holds these as handles, so their addresses have to fit in the
 * guest's 32 bits -- see GUEST_STATIC in tiger_host.c.  'AUGR' is set in
 * au_guest_init rather than an initialiser, because on a 64-bit host the
 * storage does not exist until then. */
GUEST_STATIC(au_obj, g_graph, 1);
GUEST_STATIC(au_obj, g_units, 8);

static void au_guest_init(void)
{
    GUEST_STATIC_INIT(g_graph);
    GUEST_STATIC_INIT(g_units);
    if (g_graph) { g_graph->tag = 0x41554752u; g_graph->id = 0; }   /* 'AUGR' */
}
static int    g_nunits;

static void fourcc(char *out, unsigned v)
{
    out[0] = (char)(v >> 24); out[1] = (char)(v >> 16);
    out[2] = (char)(v >> 8);  out[3] = (char)v; out[4] = 0;
    for (v = 0; v < 4; v++)
        if (out[v] < 32 || out[v] > 126) out[v] = '.';
}

static int __cdecl sh_NewAUGraph(gptr *out)
{
    if (out) *out = GP(g_graph);
    if (g_verbose) printf("  [au] NewAUGraph -> %p\n", (void *)g_graph);
    return 0;
}
static int __cdecl sh_AUGraphNewNode(void *g, const unsigned *desc,
                                     unsigned sz, const void *data, int *node)
{
    char t[5], s[5], m[5];
    (void)g; (void)sz; (void)data;
    if (desc) {
        fourcc(t, desc[0]); fourcc(s, desc[1]); fourcc(m, desc[2]);
        if (g_verbose) printf("  [au] NewNode type='%s' subtype='%s' manuf='%s'\n", t, s, m);
    } else fprintf(stderr, "  [au] NewNode (no description)\n");
    if (node) *node = ++g_nunits;               /* 1-based node ids */
    return 0;
}
static int __cdecl sh_AUGraphGetNodeInfo(void *g, int node, unsigned *desc,
                                         unsigned *csize, gptr *cdata,
                                         gptr *unit)
{
    (void)g; (void)desc; (void)csize; (void)cdata;
    if (node < 1 || node > 8) return -50;
    g_units[node - 1].tag = 0x41554e54u;        /* 'AUNT' */
    g_units[node - 1].id  = node;
    if (unit) *unit = GP(&g_units[node - 1]);
    if (g_verbose) printf("  [au] GetNodeInfo node %d -> unit %p\n", node,
           (void *)&g_units[node - 1]);
    return 0;
}
/* The same two calls, as Lion spells them.
 *
 * `AUGraphNewNode` and `AUGraphGetNodeInfo` were deprecated in 10.5, and
 * Lion's engine uses `AUGraphAddNode` and `AUGraphNodeInfo` instead -- **which
 * are not aliases**: the class-data arguments are gone, so the arities differ,
 * 3 and 4 against 5 and 6. Stubbed, the first returned a node id of zero and
 * the second never handed back an AudioUnit -- and the graph still reported
 * itself connected, opened and initialised, because *those* calls are shimmed
 * under names that did not change. The render then had nothing to render into.
 *
 * Delegating rather than reimplementing, so one set of node bookkeeping serves
 * both spellings and they cannot drift into disagreeing about what a node is.
 */
static int __cdecl sh_AUGraphAddNode(void *g, const unsigned *desc, int *node)
{
    return sh_AUGraphNewNode(g, desc, 0, NULL, node);
}

static int __cdecl sh_AUGraphNodeInfo(void *g, int node, unsigned *desc,
                                      gptr *unit)
{
    return sh_AUGraphGetNodeInfo(g, node, desc, NULL, NULL, unit);
}

static int __cdecl sh_AUGraphConnectNodeInput(void *g, int src, unsigned so,
                                              int dst, unsigned di)
{
    (void)g;
    if (g_verbose) printf("  [au] Connect node %d out %u -> node %d in %u\n", src, so, dst, di);
    return 0;
}
static int __cdecl sh_AUGraphOpen(void *g)
{ (void)g; if (g_verbose) printf("  [au] Open\n"); return 0; }
static int __cdecl sh_AUGraphInitialize(void *g)
{ (void)g; if (g_verbose) printf("  [au] Initialize\n"); return 0; }
static unsigned g_au_start, g_au_stop, g_au_uninit, g_au_isinit;
/* The widest silence between two slices of one utterance, in ms.  What
 * the serve loop's quiet period has to be longer than, measured rather
 * than guessed -- 10.7 never calls AUGraphStop, so on Lion that period
 * is the only thing that ends an utterance. */
static double g_slice_gap_max, g_slice_prev_ms;
static int __cdecl sh_AUGraphStart(void *g)
{ (void)g; g_au_start++; if (g_verbose) printf("  [au] START\n"); return 0; }
/* The engine stops its graph when the utterance is finished, which makes this
 * the natural end-of-speech signal -- better than any timeout. */
static int __cdecl sh_AUGraphStop(void *g)
{
    (void)g;
    g_stopped = 1;
    g_au_stop++;
    if (g_verbose) printf("  [au] Stop\n");
    return 0;
}
/* 10.7 imports both of these and 10.5 imports neither.  Unshimmed they
 * fell through to a stub that answers 0 -- which is noErr, and for
 * `IsInitialized` also leaves the out-parameter holding whatever was on
 * the stack.  Neither turns out to be the end-of-utterance signal, but a
 * stub that returns success without doing anything is the shape of
 * failure this host has been caught by four times. */
static int __cdecl sh_AUGraphUninitialize(void *g)
{ (void)g; g_au_uninit++; return 0; }
static int __cdecl sh_AUGraphIsInitialized(void *g, unsigned char *out)
{ (void)g; g_au_isinit++; if (out) *out = 1; return 0; }
static int __cdecl sh_DisposeAUGraph(void *g)
{ (void)g; if (g_verbose) printf("  [au] Dispose\n"); return 0; }

/* ---- where the audio actually arrives ---------------------------------- */
/*
 * The graph is ScheduledSoundPlayer -> DefaultOutput, so MacinTalk does not
 * render through a pull callback: it hands over *finished* PCM by setting
 * kAudioUnitProperty_ScheduleAudioSlice (3300) with a ScheduledAudioSlice.
 * That is 92 bytes on i386, which is how the property was identified:
 *
 *      0  AudioTimeStamp mTimeStamp        (64)
 *     64  mCompletionProc
 *     68  mCompletionProcUserData
 *     72  mFlags
 *     76  mReserved
 *     80  mReserved2
 *     84  mNumberFrames
 *     88  AudioBufferList *mBufferList
 *
 * and a buffer list is {UInt32 mNumberBuffers; {UInt32 mNumberChannels;
 * UInt32 mDataByteSize; void *mData;} mBuffers[]}.
 */
#define kAUProp_StreamFormat        8
#define kAUProp_ScheduleAudioSlice  3300
#define kAUProp_ScheduleStartTime   3301

/* The slice begins with an AudioTimeStamp, whose first field is a Float64
 * sample time saying *where in the output* this slice belongs.  Appending in
 * arrival order ignored it, and the positions are not always consecutive. */
#define SLICE_SAMPLETIME_OFF 0
#define SLICE_TSFLAGS_OFF   56
#define kAudioTimeStampSampleTimeValid 1

#define SLICE_PROC_OFF    64
#define SLICE_DATA_OFF    68
#define SLICE_FLAGS_OFF   72
#define SLICE_FRAMES_OFF  84
#define SLICE_BUFLIST_OFF 88
#define SLICE_FLAG_COMPLETE 0x01

/* Four minutes.  "Far more than needed" was two, until a singing voice turned
 * out to spend sixty-five seconds on one social-media post -- a long article
 * read with Good News would have reached it.  Overrunning only truncates:
 * slices are still completed past this point, so the engine's clock keeps
 * ticking and the channel stays healthy. */
#define PCM_CAP (22050 * 240)
static float    g_pcm[PCM_CAP];
static unsigned g_pcm_n;
/* Where the engine's current scheduling epoch starts in g_pcm, and the last
 * sample time we saw, which is how a new epoch is recognised.  Both reset per
 * utterance, in serve(). */
static unsigned g_epoch_base;
static double   g_last_stime;
/* The sample time of this utterance's first slice, and whether one has
 * been seen yet.  Both reset per utterance, in serve(). */
static double   g_time_origin;
static int      g_have_origin;
/* The engine's restarts of the player, counted where they are asked for
 * (kAudioUnitProperty_ScheduleStartTimeStamp), and the count the last
 * collected slice was scheduled under.  A slice is tagged when it is
 * scheduled, on the engine's thread, so the tag is exact however late the
 * pacer collects it.  See collect_slice for what a restart means. */
static unsigned g_epoch_seq;
static unsigned g_last_epoch = ~0u;
/* How the current epoch began: where it started in g_pcm and how many
 * slices it has, for the one case where the next epoch replaces it. */
static unsigned g_epoch_slices, g_epoch_start;

/* Per utterance: the timeline starts over.  Called from serve(), the
 * library API and the pmod check, which used to repeat these by hand. */
static void timeline_reset(void)
{
    g_epoch_base = 0; g_last_stime = 0.0; g_have_origin = 0;
    g_last_epoch = ~0u; g_epoch_slices = 0; g_epoch_start = 0;
}
static unsigned g_slices;
static unsigned g_frames_seen;
static unsigned g_empty_run;          /* consecutive slices carrying nothing */
/* Roughness of the engine's own float output, under TIGER_FLOAT_STATS: the
 * decoded grains are clean and the finished wav is not, so this says which
 * side of the float-to-short conversion the noise arrives on. */
static int      g_float_stats;
static double   g_fstat_abs, g_fstat_d;
static unsigned g_fstat_n;
static unsigned g_fstat_zero;
static unsigned g_fstat_slices, g_fstat_dead, g_fstat_holed;
static unsigned g_last_hash, g_have_last, g_dup_slices;
/* Groundwork for streaming the response instead of accumulating it.
 *
 * Slices are written at an absolute sample position, so nothing here says a
 * slice cannot land *behind* the frames already collected -- and a response
 * that has already been sent cannot be taken back.  Whether it actually
 * happens, and by how far, is a measurement rather than an argument; these
 * count it.  See [[measure-dont-reason]]. */
static unsigned g_back_slices, g_back_max;
/* When the first and last slice of this utterance were collected, in
 * milliseconds since the SESpeakBuffer call.  Wall clock, deliberately:
 * sh_mach_absolute_time is scaled by g_speed, and the question here is what a
 * listener waits, not what the engine believes. */
static double   g_utt_t0, g_first_slice_ms, g_last_slice_ms;

static double wall_ms(void)
{
    static double freq;
    LARGE_INTEGER c;
    if (!freq) { LARGE_INTEGER l; QueryPerformanceFrequency(&l);
                 freq = (double)l.QuadPart; }
    QueryPerformanceCounter(&c);
    return (double)c.QuadPart * 1000.0 / freq;
}
/*
 * The spin guard counts *silent* slices, not slices.
 *
 * It used to stop after 4000 of any kind, which is roughly 916,000 frames --
 * and the singing voices reach that inside one ordinary sentence, because they
 * render far more audio per character than the rest: Good News makes 190,000
 * frames of a line that costs Fred 57,000.  Tripping it stops completing
 * slices, and slice completion is the engine's clock, so the worker blocked
 * mid-utterance and the channel never spoke again -- every later
 * SESpeakBuffer returned -231 and the host died soon after.  A screen reader
 * going permanently silent on a long message is the worst thing this code can
 * do, and the length of the message is no reason for it.
 *
 * What the guard is for is a pipeline producing nothing at all, and that is a
 * run of empty slices however long the utterance happens to be.
 */
#define SLICE_EMPTY_LIMIT 600
/* An absolute backstop far above any real utterance; the buffer fills first. */
#define SLICE_SPIN_LIMIT 200000
static double   g_rate = 22050.0;
static unsigned g_channels = 1;
/* The stream format the engine set, kept verbatim so the getter can return
 * exactly it rather than a reconstruction. */
static unsigned char g_asbd[64];
static unsigned      g_asbd_size;
static int           g_have_asbd;

typedef void (__cdecl *slice_done_t)(void *userData, void *slice);

/* ---- the pacer --------------------------------------------------------- */
/*
 * Playback is the engine's clock.  This stands in for it: a slice is reported
 * as played after the wall-clock time its frames would have taken, with a
 * floor so that empty ring slots still tick.  Without the floor an empty
 * pipeline spins; without the delay the worker never gets scheduled between
 * completions and never renders.
 */
/* Deep enough to absorb a cancelled utterance's burst: the engine runs
 * unthrottled then, and 64 was measured overflowing 260 times on one
 * interrupt.  512 slices is about ten seconds of audio in flight and 10 KB
 * of queue, which is nothing next to what a wedged channel costs. */
#define PACE_QCAP  512
/* How long queue_completion will wait for room before giving up and
 * dropping, in milliseconds.  Long enough that the pacer -- which pays an
 * emulated guest call per slice -- can always catch up; short enough that a
 * genuine stall still ends. */
#define PACE_QWAIT_MS 500
/* Tunable so the trade-off can be measured; TIGER_PACE is a percentage of
 * real time and TIGER_PACE_FLOOR a minimum in milliseconds. */
static double g_pace = 100.0;
/* No floor by default: with the clock scaled, a per-slice minimum of even a
 * few milliseconds becomes the entire cost of an utterance.  A zero floor
 * yields the thread instead of sleeping. */
static double g_pace_floor = 0.0;

typedef struct { slice_done_t proc; void *udata; void *slice; unsigned frames;
                 unsigned utt; unsigned epoch; }
        pending;

/* Which utterance the host is collecting for.  Bumped by serve() per request.
 *
 * Stopping the engine is not instant: SEStopSpeechAt returns, the response
 * ends, and the engine's own workers are still finishing slices for the
 * utterance nobody wants.  Those arrive while the *next* request is filling
 * g_pcm, and land in it -- heard as a fragment of the post above bleeding
 * into the head of the post below, which is exactly what reading quickly
 * through a timeline produces.
 *
 * The driver has the same guard on its audio queue, but it cannot help here:
 * by the time a chunk leaves this process the stale audio is already mixed
 * into it. */
static unsigned g_utt;

static pending  g_pending[PACE_QCAP];
static int      g_p_head, g_p_tail, g_p_count;
static CRITICAL_SECTION g_p_cs;
static volatile LONG    g_pacer_stop;
/* Set while the utterance in progress has been cancelled.
 *
 * The pacer sleeps out a slice's own duration before completing it, because
 * the engine's worker is flow-controlled by those completions and would
 * otherwise run arbitrarily far ahead of the sound.  That throttle is the
 * whole of why a cancelled utterance took twenty seconds to let go on the
 * watch: the engine was not being stubborn, it was waiting on ticks we were
 * deliberately holding back at realtime, and _SEStopSpeechAt could not be
 * delivered until its worker came up for air.  Measured on a Pixel Watch 2, a
 * 700-character request cost 20.15 s between the stop and the next utterance
 * reaching the engine, against ~28 s of audio still owed at g_pace 100.
 *
 * Once nobody wants the audio, pacing it is paying realtime for sound we are
 * about to throw away.  So while cancelling, drop the sleep and let the engine
 * run the remainder flat out.  Every slice is still completed -- that is the
 * part that matters, since a completion that never fires wedges the channel --
 * they are simply completed as fast as the worker can take them, and what they
 * carry is not collected, because it is audio no one will hear. */
static volatile LONG    g_au_cancel;
/* Set while the pacer holds a job it has not finished collecting.  An
 * utterance is complete only when the queue is empty *and* this is clear;
 * reading g_pcm before then is a snapshot of a half-collected timeline. */
static volatile LONG    g_p_busy;
static volatile LONG    g_p_reset; /* finish scheduled buffers promptly during reset */
/* A dropped slice is audio the engine produced and we threw away, and its
 * completion never fires, so the engine waits on a slice that will never
 * finish.  Count it and say it out loud rather than absorbing it. */
static unsigned         g_p_drops;
/* Slices that arrived for an utterance already answered.  Counted rather than
 * ignored: it is the measure of how far the engine runs on after being told to
 * stop, and it used to be audible. */
static unsigned         g_stale_slices;

/* Queue one slice's completion, waiting for room rather than dropping it.
 *
 * A full queue used to mean a dropped slice, and a dropped slice is a
 * completion that never fires.  Measured on a Pixel Watch 2: interrupt Leopard
 * and the queue overflows 260 times, and the next utterance then never starts
 * -- the engine sits in a loop calling AudioUnitReset, 118,760 times in
 * thirty-five seconds, reading no property and asking nothing, waiting for
 * slices that will never be finished.  The comment above g_p_drops said this
 * would happen; it took a second decoder generation to actually do it.
 *
 * It overflows during a cancellation specifically, because that is when the
 * engine is deliberately let run flat out (see g_au_cancel) while each
 * completion still costs an emulated call into the guest.  Producer beats
 * consumer, and the queue is the only thing between them.
 *
 * So: a bigger queue to absorb the burst, and then wait for room.  Waiting is
 * safe here -- this runs on the engine's worker, and the pacer that drains the
 * queue is a different thread -- but it is bounded anyway, because a wait that
 * cannot end is a worse bug than the one being fixed.  If the bound is ever
 * reached the slice is still dropped and still counted, and the count is in the
 * finish line where somebody will see it. */
static void queue_completion(slice_done_t p, void *u, void *s, unsigned frames)
{
    int waited = 0;
    for (;;) {
        int full;
        EnterCriticalSection(&g_p_cs);
        full = (g_p_count >= PACE_QCAP);
        if (!full) {
            g_pending[g_p_tail].proc = p;
            g_pending[g_p_tail].udata = u;
            g_pending[g_p_tail].slice = s;
            g_pending[g_p_tail].frames = frames;
            g_pending[g_p_tail].utt = g_utt;
            g_pending[g_p_tail].epoch = g_epoch_seq;
            g_p_tail = (g_p_tail + 1) % PACE_QCAP;
            g_p_count++;
        } else if (waited >= PACE_QWAIT_MS) {
            g_p_drops++;
        }
        LeaveCriticalSection(&g_p_cs);
        if (!full || waited >= PACE_QWAIT_MS) return;
        Sleep(1);
        waited++;
    }
}

/* True when the pacer has nothing left to collect. */
static int pacer_idle(void)
{
    int n;
    EnterCriticalSection(&g_p_cs);
    n = g_p_count;
    LeaveCriticalSection(&g_p_cs);
    return n == 0 && !g_p_busy;
}

/* Read a slice's audio at the moment it finishes "playing", not when it was
 * scheduled.
 *
 * kAudioUnitProperty_ScheduleAudioSlice means "play this buffer at this time".
 * A real ScheduledSoundPlayer reads the buffer when it plays it; the engine is
 * free to fill it after scheduling, and its worker does exactly that. Copying
 * at schedule time therefore captured whatever the buffer held *before* it was
 * filled -- the previous slice's audio -- which is heard as sounds inserted
 * where none belong and as speech skipping about, because the content is one
 * slice behind its own timestamp.
 *
 * The completion callback is the contract: after it, the engine may reuse the
 * buffer. Immediately before it, the audio is finished and correct.
 */
static void collect_slice(unsigned char *slice, unsigned epoch)
{
    unsigned frames = *(unsigned *)(slice + SLICE_FRAMES_OFF);
    unsigned char *bl = (unsigned char *)GHOST(*(gptr *)(slice + SLICE_BUFLIST_OFF));
    double stime = *(double *)(slice + SLICE_SAMPLETIME_OFF);
    unsigned tsflags = *(unsigned *)(slice + SLICE_TSFLAGS_OFF);
    unsigned nbufs, i;
    if (!bl || !frames) return;
    /* The engine schedules an utterance in epochs, and each epoch starts its
     * sample clock again at zero.
     *
     * A real ScheduledSoundPlayer does not care: it is playing into a device
     * that keeps running, and AudioUnitReset plus a fresh schedule simply
     * means "now play this".  We are accumulating into one buffer instead, so
     * an epoch that restarts at zero lands on top of everything collected so
     * far and silently erases it.  That is where the missing words went --
     * "One, two, three." was complete, and then the second sentence was
     * written over it from sample zero.
     *
     * It only bit half the time because the engine does not always split: the
     * same sentence came back as one continuous timeline of 98900 frames or
     * as two epochs of 48505 and 50395, run to run, and only the continuous
     * one had all the words.  Rebasing makes the two identical.
     *
     * Detected by the clock going backwards rather than by AudioUnitReset,
     * because the restart is what actually breaks us and it is visible right
     * here; a reset we failed to notice would put us straight back. */
    /* Where this utterance's clock starts.
     *
     * The engine's sample clock does not necessarily go back to zero between
     * utterances: it does when it resets the audio unit, and it does not when
     * it has just been stopped mid-sentence.  Treating the time as absolute
     * therefore prepended however far the clock had run -- seconds of silence
     * before the next thing the user asked for, which is heard as the wait
     * with no sound after interrupting.
     *
     * So the first slice of an utterance defines the origin, and everything
     * is placed relative to it. */
    /* A restart of the player begins a new epoch, whatever the clock says.
     *
     * The engine restarts the player -- ScheduleStartTimeStamp, "start
     * now" -- whenever it believes playback has drained, and the first
     * slice after a restart sits at sample time zero.  Under a slow
     * translator it was seen restarting twice within a quarter of a
     * millisecond at a phrase boundary, each restart's first slice at zero
     * and each carrying different audio: the tail of the phrase, then its
     * continuation.  Zero is not less than zero, so the clock check below
     * saw no new epoch and the continuation was written over the tail:
     * 208 to 229 frames of speech gone at a hard discontinuity, once or
     * twice a paragraph, only when the timing fell that way.  Box64's
     * interpreter on a Galaxy S22 gave 527060, 526833 and 527289 frames
     * for a paragraph that is 527288 natively; the per-slice trace showed
     * the two restarts and the overwrite, and showed the lost slice had
     * been scheduled, collected and then covered.
     *
     * So each slice carries the restart count it was scheduled under, and
     * a change of count starts the new epoch where the collected audio
     * ends.  One exception keeps native renders byte-identical: the
     * engine's usual way to start the player is a one-frame silent slice
     * at zero, a restart, then the audio at zero -- a kick, meant to be
     * replaced, and replaced here exactly as it always was. */
    if (epoch != g_last_epoch) {
        if (g_last_epoch != ~0u)
            g_epoch_base = (g_epoch_slices == 1 && g_pcm_n == g_epoch_start + 1 &&
                            g_pcm[g_epoch_start] == 0.0f)
                           ? g_epoch_start : g_pcm_n;
        g_time_origin = stime; g_have_origin = 1;
        g_last_stime = 0.0;
        g_last_epoch = epoch;
        g_epoch_slices = 0;
        g_epoch_start = g_epoch_base;
    }
    g_epoch_slices++;
    if (!g_have_origin) { g_time_origin = stime; g_have_origin = 1; }
    stime -= g_time_origin;
    if (stime < 0.0) {
        /* Earlier than what we took for the start of this utterance.
         *
         * That happens when a slice left over from the utterance just
         * abandoned arrives after the next request has begun: it carries the
         * old clock, which may be seconds ahead, and it set the origin.  The
         * real slices then compute a negative position.
         *
         * Clamping them to zero was wrong and audible -- every one of them
         * piled onto position zero, overwriting the opening of the post, so
         * it was heard starting partway through.  Treat it as a fresh epoch
         * instead: re-take the origin here and append.  Nothing is lost; at
         * worst the stray frames stay in front. */
        g_epoch_base = g_pcm_n;
        g_time_origin += stime;         /* i.e. back to this slice's own time */
        stime = 0.0;
    }
    if (stime < g_last_stime) g_epoch_base = g_pcm_n;
    g_last_stime = stime;
    nbufs = *(unsigned *)bl;
    for (i = 0; i < nbufs; i++) {
        unsigned char *b = bl + 4 + i * 12;
        unsigned bytes = *(unsigned *)(b + 4);
        const float *data = (const float *)GHOST(*(gptr *)(b + 8));
        unsigned n = bytes / sizeof(float), j, pos;
        if (i != 0 || !data) continue;
        if (frames < n) n = frames;
        pos = g_epoch_base + ((stime > 0.0) ? (unsigned)(stime + 0.5) : 0);
        if (!(tsflags & kAudioTimeStampSampleTimeValid))
            pos = g_pcm_n;
        if (pos < g_pcm_n) {
            unsigned back = g_pcm_n - pos;
            g_back_slices++;
            if (back > g_back_max) g_back_max = back;
        }
        { double t = wall_ms() - g_utt_t0;
          if (g_first_slice_ms < 0.0) g_first_slice_ms = t;
          else if (t - g_slice_prev_ms > g_slice_gap_max)
              g_slice_gap_max = t - g_slice_prev_ms;
          g_slice_prev_ms = t;
          g_last_slice_ms = t; }
        if (pos > g_pcm_n && pos < PCM_CAP)
            while (g_pcm_n < pos) g_pcm[g_pcm_n++] = 0.0f;
        for (j = 0; j < n && pos + j < PCM_CAP; j++)
            g_pcm[pos + j] = data[j];
        if (pos + n > g_pcm_n)
            g_pcm_n = (pos + n < PCM_CAP) ? pos + n : PCM_CAP;
    }
}

#ifndef TIGER_UC
/* Synthetic slices exercise the collector without an engine or voice bank.
 * In particular, two restarts at zero must preserve both pieces of speech;
 * the replaceable one-frame kick must actually be silent. */
static void timeline_check_slice(unsigned epoch, double time, const float *samples, unsigned count)
{
    union { double align; unsigned char bytes[SLICE_BUFLIST_OFF + sizeof(gptr)]; } slice;
    unsigned buffers[4];
    memset(&slice, 0, sizeof slice);
    buffers[0] = 1; buffers[1] = 1; buffers[2] = count * sizeof(float);
    buffers[3] = (gptr)(uintptr_t)samples;
    *(unsigned *)(slice.bytes + SLICE_FRAMES_OFF) = count;
    *(gptr *)(slice.bytes + SLICE_BUFLIST_OFF) = (gptr)(uintptr_t)buffers;
    *(double *)(slice.bytes + SLICE_SAMPLETIME_OFF) = time;
    *(unsigned *)(slice.bytes + SLICE_TSFLAGS_OFF) = kAudioTimeStampSampleTimeValid;
    collect_slice(slice.bytes, epoch);
}

static int timeline_check(void)
{
    const float zero = 0.0f;
    const float expected[] = {0.1f,0.2f,0.3f,0.4f,0.5f,0.6f,0.7f,0.8f};
    g_pcm_n = 0; timeline_reset();
    timeline_check_slice(10, 0, &zero, 1);
    timeline_check_slice(11, 0, expected, 2);
    timeline_check_slice(12, 0, expected + 2, 2);
    timeline_check_slice(13, 0, expected + 4, 1);
    timeline_check_slice(14, 0, expected + 5, 2);
    timeline_check_slice(14, 2, expected + 7, 1);
    if (g_pcm_n != 8 || memcmp(g_pcm, expected, sizeof expected)) return 1;
    g_pcm_n = 0; timeline_reset();
    timeline_check_slice(20, 500, expected, 2);
    timeline_check_slice(20, 502, expected + 2, 2);
    if (g_pcm_n != 4 || memcmp(g_pcm, expected, 4 * sizeof(float))) return 2;
    g_pcm_n = 0; timeline_reset();
    puts("PASS audio timeline: consecutive zero-time restarts, silent kick, nonzero one-frame slice, reset origin");
    return 0;
}
#endif

static DWORD WINAPI pacer_thread(LPVOID arg)
{
    (void)arg;
    tiger_thread_is_audio("pacer");
    while (!g_pacer_stop) {
        pending job;
        int have = 0;
        EnterCriticalSection(&g_p_cs);
        if (g_p_count) {
            job = g_pending[g_p_head];
            g_p_head = (g_p_head + 1) % PACE_QCAP;
            g_p_count--;
            have = 1;
            /* Marked inside the lock: otherwise there is a window where the
             * job is off the queue but not yet accounted for, and an observer
             * sees an empty queue with an idle pacer while a slice is in
             * flight. */
            g_p_busy = 1;
        }
        LeaveCriticalSection(&g_p_cs);
        if (!have) { Sleep(2); continue; }
        {
            /* Real playback would take frames/rate seconds, but the engine
             * only needs *enough* time for its worker to render ahead, and
             * native code renders far faster than a G4 played audio.  g_pace
             * is that fraction as a percentage; drop it too low and the
             * empty-slice spin returns, so it wants measuring, not guessing. */
            double ms = job.frames * 1000.0 / g_rate * (g_pace / 100.0);
            if (ms < g_pace_floor) ms = g_pace_floor;
            if (g_au_cancel || g_p_reset) ms = 0.0;   /* cancelled: owed nobody any time */
            if (ms >= 1.0) Sleep((DWORD)ms);
            else SwitchToThread();        /* still yield, or the worker never runs */
        }
        /* Audio for an utterance that has already been answered must not be
         * written into the buffer the next one is filling.  Complete the slice
         * regardless -- that is the engine's clock, and refusing to tick it is
         * how the channel wedges -- but do not collect what it carries. */
        if (job.utt == g_utt && !g_au_cancel)
            collect_slice((unsigned char *)job.slice, job.epoch);
        else
            g_stale_slices++;
        *(unsigned *)((unsigned char *)job.slice + SLICE_FLAGS_OFF)
            |= SLICE_FLAG_COMPLETE;
        /* Into engine code, so the stack must be 16-byte aligned at the call --
         * see call_aligned1.  Leopard's
         * MTBEAudioUnitSoundOutput::QueueSamples stores a pair of doubles with
         * movapd almost as soon as it is entered, and faults outright if this
         * thread hands it a stack Windows aligned to four. */
        call_aligned2((void *)job.proc, job.udata, job.slice);
        g_p_busy = 0;
    }
    return 0;
}

/* Finish reset callbacks before returning. The guest retires whatever
 * remains after AudioUnitReset, so a delayed completion would retire the
 * same slice twice. Drain through the existing pacer without pacing.
 * An ordinary reset also separates sentences: keep their queued audio.
 * Cancellation has its own flag and discards audio in the pacer above.
 * Treating every reset as cancellation lost a timing-dependent sentence tail
 * on slower hosts, while fast native renders had already collected it. */
static int reset_scheduled_audio(void)
{
    unsigned waited = 0;
    if (g_verbose) fprintf(stderr, "  [au] reset scheduled: queued=%d pcm=%u cancel=%ld\n", g_p_count, g_pcm_n, (long)g_au_cancel);
    InterlockedExchange(&g_p_reset, 1);
    while (!pacer_idle() && waited++ < 5000) Sleep(1);
    if (!pacer_idle()) die("audio reset timed out waiting for completion callbacks");
    InterlockedExchange(&g_p_reset, 0);
    return 0;
}

/* A cancelled channel may still schedule audio after SEStopSpeechAt returns.
 * Native Leopard settles quickly; amd64 emulation needs over 350 ms for the
 * same request. Never reset the next timeline merely because 200 ms elapsed.
 * Keep completing and discarding slices until the queue and producer settle.
 * The deadline is a failure bound, not a delay added to successful cancels. */
static int settle_cancelled_audio(void)
{
    unsigned last = g_slices, quiet = 0;
    double deadline = wall_ms() + 10000.0;
    while (quiet < 15 && wall_ms() < deadline) {
        Sleep(2);
        if (g_slices != last || !pacer_idle()) { last = g_slices; quiet = 0; }
        else quiet++;
    }
    if (quiet >= 15) return 1;
    fprintf(stderr, "panthera: cancelled audio did not settle; channel cannot be reused\n");
    return 0;
}

static void take_slice(unsigned char *slice)
{
    unsigned frames = *(unsigned *)(slice + SLICE_FRAMES_OFF);
    double stime = *(double *)(slice + SLICE_SAMPLETIME_OFF);
    unsigned tsflags = *(unsigned *)(slice + SLICE_TSFLAGS_OFF);
    unsigned char *bl = (unsigned char *)GHOST(*(gptr *)(slice + SLICE_BUFLIST_OFF));
    /* The completion routine is a GUEST function pointer and its user data a
     * guest address -- both four bytes, both read through a host-width type
     * until now, which on arm64 fetched each of them glued to its neighbour. */
    slice_done_t done = (slice_done_t)GHOST(*(gptr *)(slice + SLICE_PROC_OFF));
    void *udata = GHOST(*(gptr *)(slice + SLICE_DATA_OFF));
    unsigned nbufs, i;

    g_slices++;
    if (g_float_stats) {
        static double prev = -1.0;
        static double expect = -1.0;
        int anomaly = (expect >= 0.0 && frames && stime != expect);
        if (anomaly)
            printf("  [au] TIMELINE %s: slice %u wants %.0f, previous ended "
                   "at %.0f (%+.0f)\n",
                   stime < expect ? "OVERLAP" : "GAP", g_slices, stime, expect,
                   stime - expect);
        if (frames) expect = stime + frames;
        if (g_slices <= 6 || (stime <= prev && frames))
            printf("  [au] slice %-4u frames %-5u sampleTime %12.1f%s%s\n",
                   g_slices, frames, stime,
                   (tsflags & kAudioTimeStampSampleTimeValid) ? "" : " (invalid)",
                   (stime <= prev && g_slices > 1) ? "  <-- NOT ADVANCING" : "");
        prev = stime;
    }
    /* Completing a slice the instant it is scheduled makes the engine schedule
     * the next one immediately, so an empty pipeline spins.  Log the first few
     * and anything that actually carries audio; stop feeding the loop once it
     * is clearly not producing. */
    if (frames) { g_frames_seen++; g_empty_run = 0; }
    else g_empty_run++;
    /* Quiet in serve mode: an utterance produces dozens of these, and the
     * driver sends stderr to the void, so it is pure cost. */
    if (g_verbose && (g_slices <= 3 || frames))
        if (g_verbose) printf("  [au] slice %u: %u frames%s\n", g_slices, frames,
               bl ? "" : ", no buffer list");
    if (g_empty_run == SLICE_EMPTY_LIMIT)
        fprintf(stderr, "tiger_host: %u empty slices in a row after %u frames "
                        "-- the engine has stopped producing\n",
                g_empty_run, g_pcm_n);
    if (g_empty_run >= SLICE_EMPTY_LIMIT) return;
    if (g_slices == SLICE_SPIN_LIMIT)
        if (g_verbose) printf("  [au] %u slices with %u carrying audio -- stopping\n",
               g_slices, g_frames_seen);
    if (g_slices >= SLICE_SPIN_LIMIT) return;
    if (!bl) return;
    nbufs = *(unsigned *)bl;
    for (i = 0; i < nbufs; i++) {
        unsigned char *b = bl + 4 + i * 12;
        unsigned bytes = *(unsigned *)(b + 4);
        const float *data = (const float *)GHOST(*(gptr *)(b + 8));
        unsigned n = bytes / sizeof(float), j;
        if (i == 0 && data) {
            /* The buffer's byte count is its capacity; `frames` is how much of
             * it the engine actually filled.  Taking the capacity appends
             * whatever was left in the buffer from last time -- stale audio,
             * at full amplitude, scattered through the utterance wherever a
             * slice came up short. */
            static unsigned mismatches;
            if (n != frames && mismatches < 8) {
                mismatches++;
                /* stderr, not stdout: this fires mid-render, and in serve
                 * mode stdout was the protocol until the stream learned to
                 * defend itself.  A complaint that corrupts the thing it is
                 * complaining about is how a game crashed. */
                fprintf(stderr, "  [au] slice %u: buffer holds %u frames, "
                        "slice says %u -- taking %u\n", g_slices, n, frames,
                        frames < n ? frames : n);
            }
            if (frames < n) n = frames;
            /* Roughness of the engine's own float output, before anything of
             * ours touches it.  The decoded grains are clean and the finished
             * wav is not, so the stage that adds the noise is somewhere
             * between -- and this says which side of the float-to-short
             * conversion it is on. */
            if (g_float_stats) {
                for (j = 0; j + 1 < n; j++) {
                    double a = data[j], b = data[j + 1];
                    g_fstat_abs += a < 0 ? -a : a;
                    g_fstat_d   += (b - a) < 0 ? (a - b) : (b - a);
                    g_fstat_n++;
                }
                /* **Silence, counted on the engine's side of the fence.**
                 *
                 * Lion's Alex arrives two thirds exact zeroes at full
                 * duration, and roughness cannot see that -- it *improves*
                 * as silence grows, which is how a render measured cleaner
                 * than Leopard's while saying a third of the words.
                 *
                 * This splits the problem: dense here and sparse in the wav
                 * means the audio is being lost on the way out; sparse here
                 * means the engine is emitting silence and the fault is
                 * upstream of anything in this file. */
                {
                    unsigned zeros = 0;
                    for (j = 0; j < n; j++)
                        if (data[j] == 0.0f) zeros++;
                    g_fstat_zero += zeros;
                    /* **Whole slices, or holes inside them?**  Two very
                     * different faults wear the same percentage: an engine
                     * emitting silent slices, and one filling each slice
                     * only part way.  Counting both separates them. */
                    g_fstat_slices++;
                    if (zeros == n) g_fstat_dead++;
                    else if (zeros > n / 10) g_fstat_holed++;
                }
            }
            /* Is any slice delivered twice?  "It inserts phantom fragments"
             * is exactly what a repeated slice sounds like, so hash each one
             * and count exact repeats rather than reasoning about it. */
            /* Refuse a slice whose audio is bit-identical to the one
             * before it.
             *
             * The engine works a ring of slice buffers and refills them from
             * its worker.  When the worker has not produced anything new it
             * schedules the previous buffer again, unchanged, and we were
             * recording every one: "leopardspeech-0.1.0.nvda-addon  7 of 11"
             * came to 679 slices of which 62 were exact repeats, which is
             * heard as a fragment stuttering over and over in the middle of a
             * word.  The existing guard counts *empty* slices and never sees
             * this, because these are full of perfectly good audio -- just the
             * same audio twice.
             *
             * Two hundred-odd floats of synthesised speech matching to the bit
             * by coincidence is not a thing that happens; identical means
             * resent.  The slice is still completed either way, because
             * completion is the engine's clock and skipping that is what once
             * left the channel wedged mid-utterance. */
            /* Put the audio where the engine says it goes.
             *
             * Every slice carries an AudioTimeStamp whose sample time is its
             * position in the output, and appending in arrival order quietly
             * assumed those were always consecutive.  They are not: the first
             * two both sit at 0, and the engine re-sends a buffer when its
             * worker has produced nothing new.
             *
             * Refusing an identical slice was worse than recording it.  The
             * re-sent ones carry *advancing* sample times, so dropping them
             * left the timeline short by 229 frames apiece and pulled
             * everything after them forward -- which is what a skipping CD
             * sounds like, and it was my doing rather than the engine's.
             *
             * Writing at the stated offset handles all of it: a slice resent
             * at the same time overwrites, a slice at a new time lands where
             * it belongs, and a gap the engine leaves stays a gap instead of
             * silently closing up. */
            (void)j; (void)data;        /* read at completion, not here */
        }
    }
    /* Do NOT complete the slice here.
     *
     * A ScheduledSoundPlayer's completion fires from the render thread once
     * the audio has actually played, roughly frames/rate seconds later.
     * Calling it inline says "that played" microseconds after scheduling, so
     * the engine refills before its worker has rendered anything, gets an
     * empty buffer, schedules it, and spins -- which is exactly the one
     * symptom that survived every other fix.  Queue it for the pacer instead;
     * arriving on another thread is also closer to the truth, and is why the
     * engine guards this path with MPEnterCriticalRegion. */
    if (done) queue_completion(done, udata, slice, frames);
}

static int __cdecl sh_AudioUnitSetProperty(au_obj *unit, unsigned id,
                                           unsigned scope, unsigned elem,
                                           const void *data, unsigned size)
{
    (void)unit; (void)elem;
    if (id == kAUProp_StreamFormat && size >= 40 && data) {
        const unsigned char *p = (const unsigned char *)data;
        char fid[5];
        /* Kept so the getter can hand back exactly what was set.  Leopard's
         * engine asks for this again later, and an answer it did not
         * recognise would be worse than no answer at all. */
        if (size <= sizeof(g_asbd)) {
            memcpy(g_asbd, data, size);
            g_asbd_size = size;
            g_have_asbd = 1;
        }
        g_rate = *(const double *)p;
        fourcc(fid, *(const unsigned *)(p + 8));
        g_channels = *(const unsigned *)(p + 28);
        if (g_verbose) printf("  [au] StreamFormat scope=%u: %.0f Hz, '%s', flags 0x%x, "
               "%u ch, %u bits\n", scope, g_rate, fid,
               *(const unsigned *)(p + 12), g_channels,
               *(const unsigned *)(p + 32));
    } else if (id == kAUProp_ScheduleAudioSlice && data) {
        /* Accepted slices must receive their completion even during cancel.
         * The pacer discards cancelled audio and removes its playback delay.
         * This keeps completion accounting valid; it does not abort synthesis.
         * Returning an error after accepting a slice was tried and removed:
         * Leopard treated it as unscheduled while our completion still fired,
         * leaving the next utterance wedged. */
        take_slice((unsigned char *)data);
    } else if (id == kAUProp_ScheduleStartTime) {
        /* "Start now": the player's timeline begins again, and so does
         * ours -- for the slices scheduled from here on, not for any
         * still queued from before.  Hence a count, not a flag. */
        g_epoch_seq++;
        if (g_verbose) printf("  [au] ScheduleStartTime sampleTime %.1f\n",
               data ? *(const double *)data : 0.0);
    } else {
        if (g_verbose) printf("  [au] SetProperty id=%u scope=%u size=%u\n", id, scope, size);
    }
    return 0;
}

/* ---- reading state back out of the audio unit -------------------------- */
/*
 * Tiger's engine never asks.  Its imports are AudioUnitSetProperty and
 * AudioUnitReset and nothing else, so a write-only fake AUGraph is a complete
 * one for it -- which is why Vicki renders byte-perfect through Tiger's
 * engine and roughly through Leopard's, on the same voice data and the same
 * host.
 *
 * Leopard's engine imports AudioUnitGetProperty, AudioUnitGetPropertyInfo and
 * AudioUnitAddPropertyListener as well, and we had none of them.  They fell
 * through to a stub that returns noErr without writing the out-parameter, so
 * the engine asked where playback had reached, was told the call succeeded,
 * and read whatever was already on its stack.
 *
 * An unimplemented property must therefore fail rather than succeed quietly:
 * kAudioUnitErr_InvalidProperty is a documented answer that callers handle,
 * and a wrong answer dressed as a right one is the thing that cost a night.
 */
#define kAUProp_ClassInfo            0
#define kAUProp_SampleRate           2
#define kAUProp_MaxFramesPerSlice   14
#define kAUProp_LastRenderError     22
#define kAUProp_CurrentPlayTime    3302
#define kAudioUnitErr_InvalidProperty (-10879)

/* Which properties we have actually been asked for, said once each: the
 * linked surface is 3 symbols, the reached surface is what matters. */
static void note_get(unsigned id, unsigned scope, int answered)
{
    static unsigned seen[16];
    static int nseen;
    int i;
    for (i = 0; i < nseen; i++)
        if (seen[i] == id) return;
    if (nseen < 16) seen[nseen++] = id;
    fprintf(stderr, "tiger_host: the engine read audio unit property %u "
                    "(scope %u) -- %s\n", id, scope,
            answered ? "answered" : "NOT IMPLEMENTED, reported as unsupported");
}

static int __cdecl sh_AudioUnitGetProperty(au_obj *unit, unsigned id,
                                           unsigned scope, unsigned elem,
                                           void *data, unsigned *iosize)
{
    unsigned want = 0;
    (void)unit; (void)elem;
    if (!data || !iosize) return -50;
    switch (id) {
    case kAUProp_StreamFormat:
        if (!g_have_asbd) break;
        want = g_asbd_size;
        if (*iosize < want) return -50;
        memcpy(data, g_asbd, want);
        *iosize = want;
        note_get(id, scope, 1);
        return 0;
    case kAUProp_SampleRate:
        want = sizeof(double);
        if (*iosize < want) return -50;
        *(double *)data = g_rate;
        *iosize = want;
        note_get(id, scope, 1);
        return 0;
    case kAUProp_CurrentPlayTime: {
        /* A ScheduledSoundPlayer reports where playback has reached on the
         * timeline it was given, which for us is the sample we have collected
         * up to within the current epoch.  It must never go backwards. */
        unsigned char *ts = (unsigned char *)data;
        want = 64;                          /* an AudioTimeStamp */
        if (*iosize < want) return -50;
        memset(ts, 0, want);
        *(double *)(ts + SLICE_SAMPLETIME_OFF) =
            (double)(g_pcm_n > g_epoch_base ? g_pcm_n - g_epoch_base : 0);
        *(unsigned *)(ts + SLICE_TSFLAGS_OFF) = kAudioTimeStampSampleTimeValid;
        *iosize = want;
        note_get(id, scope, 1);
        return 0;
    }
    case kAUProp_LastRenderError:
        want = sizeof(int);
        if (*iosize < want) return -50;
        *(int *)data = 0;
        *iosize = want;
        note_get(id, scope, 1);
        return 0;
    case kAUProp_MaxFramesPerSlice:
        want = sizeof(unsigned);
        if (*iosize < want) return -50;
        *(unsigned *)data = 4096;
        *iosize = want;
        note_get(id, scope, 1);
        return 0;
    default:
        break;
    }
    note_get(id, scope, 0);
    return kAudioUnitErr_InvalidProperty;
}

static int __cdecl sh_AudioUnitGetPropertyInfo(au_obj *unit, unsigned id,
                                               unsigned scope, unsigned elem,
                                               unsigned *outsize,
                                               unsigned char *writable)
{
    unsigned size = 0;
    (void)unit; (void)elem;
    switch (id) {
    case kAUProp_StreamFormat:   size = g_have_asbd ? g_asbd_size : 0; break;
    case kAUProp_SampleRate:     size = sizeof(double);   break;
    case kAUProp_CurrentPlayTime: size = 64;              break;
    case kAUProp_LastRenderError: size = sizeof(int);     break;
    case kAUProp_MaxFramesPerSlice: size = sizeof(unsigned); break;
    default: break;
    }
    if (!size) {
        note_get(id, scope, 0);
        return kAudioUnitErr_InvalidProperty;
    }
    if (outsize) *outsize = size;
    if (writable) *writable = (unsigned char)(id != kAUProp_CurrentPlayTime);
    return 0;
}

/* Nothing here ever changes a property behind the engine's back, so there is
 * nothing to notify.  Accepting the registration is the honest answer. */
static int __cdecl sh_AudioUnitAddPropertyListener(au_obj *unit, unsigned id,
                                                   void *proc, void *udata)
{
    (void)unit; (void)proc; (void)udata;
    if (g_verbose) printf("  [au] AddPropertyListener for property %u\n", id);
    return 0;
}
/* 32-bit float in, 16-bit PCM out, because that is what everything downstream
 * of here wants -- NVDA's WavePlayer included. */
static void write_wav(const char *path)
{
    FILE *f = fopen(path, "wb");
    unsigned rate = (unsigned)(g_rate + 0.5), i;
    unsigned data_bytes = g_pcm_n * 2, riff = 36 + data_bytes;
    unsigned byte_rate = rate * g_channels * 2;
    unsigned short block = (unsigned short)(g_channels * 2), fmt = 1,
                   chans = (unsigned short)g_channels, bits = 16;
    unsigned fmt_size = 16;
    if (!f) { printf("cannot write %s\n", path); return; }
    fwrite("RIFF", 1, 4, f); fwrite(&riff, 4, 1, f); fwrite("WAVE", 1, 4, f);
    fwrite("fmt ", 1, 4, f); fwrite(&fmt_size, 4, 1, f);
    fwrite(&fmt, 2, 1, f);  fwrite(&chans, 2, 1, f);
    fwrite(&rate, 4, 1, f); fwrite(&byte_rate, 4, 1, f);
    fwrite(&block, 2, 1, f); fwrite(&bits, 2, 1, f);
    fwrite("data", 1, 4, f); fwrite(&data_bytes, 4, 1, f);
    for (i = 0; i < g_pcm_n; i++) {
        double v = g_pcm[i];
        short s;
        if (v > 1.0) v = 1.0;
        if (v < -1.0) v = -1.0;
        s = (short)(v * 32767.0);
        fwrite(&s, 2, 1, f);
    }
    fclose(f);
    printf("\nwrote %s -- %u frames, %.2f s at %u Hz\n", path, g_pcm_n,
           g_pcm_n / g_rate, rate);
}
