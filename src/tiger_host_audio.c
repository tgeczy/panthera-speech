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
static au_obj g_graph = { 0x41554752u, 0 };     /* 'AUGR' */
static au_obj g_units[8];
static int    g_nunits;

static void fourcc(char *out, unsigned v)
{
    out[0] = (char)(v >> 24); out[1] = (char)(v >> 16);
    out[2] = (char)(v >> 8);  out[3] = (char)v; out[4] = 0;
    for (v = 0; v < 4; v++)
        if (out[v] < 32 || out[v] > 126) out[v] = '.';
}

static int __cdecl sh_NewAUGraph(au_obj **out)
{
    if (out) *out = &g_graph;
    if (g_verbose) printf("  [au] NewAUGraph -> %p\n", (void *)&g_graph);
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
    } else printf("  [au] NewNode (no description)\n");
    if (node) *node = ++g_nunits;               /* 1-based node ids */
    return 0;
}
static int __cdecl sh_AUGraphGetNodeInfo(void *g, int node, unsigned *desc,
                                         unsigned *csize, void **cdata,
                                         au_obj **unit)
{
    (void)g; (void)desc; (void)csize; (void)cdata;
    if (node < 1 || node > 8) return -50;
    g_units[node - 1].tag = 0x41554e54u;        /* 'AUNT' */
    g_units[node - 1].id  = node;
    if (unit) *unit = &g_units[node - 1];
    if (g_verbose) printf("  [au] GetNodeInfo node %d -> unit %p\n", node,
           (void *)&g_units[node - 1]);
    return 0;
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
static int __cdecl sh_AUGraphStart(void *g)
{ (void)g; if (g_verbose) printf("  [au] START\n"); return 0; }
/* The engine stops its graph when the utterance is finished, which makes this
 * the natural end-of-speech signal -- better than any timeout. */
static int __cdecl sh_AUGraphStop(void *g)
{
    (void)g;
    g_stopped = 1;
    if (g_verbose) printf("  [au] Stop\n");
    return 0;
}
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
static unsigned g_slices;
static unsigned g_frames_seen;
static unsigned g_empty_run;            /* consecutive slices carrying nothing */
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

typedef void (__cdecl *slice_done_t)(void *userData, void *slice);

/* ---- the pacer --------------------------------------------------------- */
/*
 * Playback is the engine's clock.  This stands in for it: a slice is reported
 * as played after the wall-clock time its frames would have taken, with a
 * floor so that empty ring slots still tick.  Without the floor an empty
 * pipeline spins; without the delay the worker never gets scheduled between
 * completions and never renders.
 */
#define PACE_QCAP  64
/* Tunable so the trade-off can be measured; TIGER_PACE is a percentage of
 * real time and TIGER_PACE_FLOOR a minimum in milliseconds. */
static double g_pace = 100.0;
/* No floor by default: with the clock scaled, a per-slice minimum of even a
 * few milliseconds becomes the entire cost of an utterance.  A zero floor
 * yields the thread instead of sleeping. */
static double g_pace_floor = 0.0;

typedef struct { slice_done_t proc; void *udata; void *slice; unsigned frames; }
        pending;

static pending  g_pending[PACE_QCAP];
static int      g_p_head, g_p_tail, g_p_count;
static CRITICAL_SECTION g_p_cs;
static volatile LONG    g_pacer_stop;

static void queue_completion(slice_done_t p, void *u, void *s, unsigned frames)
{
    EnterCriticalSection(&g_p_cs);
    if (g_p_count < PACE_QCAP) {
        g_pending[g_p_tail].proc = p;
        g_pending[g_p_tail].udata = u;
        g_pending[g_p_tail].slice = s;
        g_pending[g_p_tail].frames = frames;
        g_p_tail = (g_p_tail + 1) % PACE_QCAP;
        g_p_count++;
    }
    LeaveCriticalSection(&g_p_cs);
}

static DWORD WINAPI pacer_thread(LPVOID arg)
{
    (void)arg;
    while (!g_pacer_stop) {
        pending job;
        int have = 0;
        EnterCriticalSection(&g_p_cs);
        if (g_p_count) {
            job = g_pending[g_p_head];
            g_p_head = (g_p_head + 1) % PACE_QCAP;
            g_p_count--;
            have = 1;
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
            if (ms >= 1.0) Sleep((DWORD)ms);
            else SwitchToThread();
        }
        *(unsigned *)((unsigned char *)job.slice + SLICE_FLAGS_OFF)
            |= SLICE_FLAG_COMPLETE;
        job.proc(job.udata, job.slice);
    }
    return 0;
}

static void take_slice(unsigned char *slice)
{
    unsigned frames = *(unsigned *)(slice + SLICE_FRAMES_OFF);
    unsigned char *bl = *(unsigned char **)(slice + SLICE_BUFLIST_OFF);
    slice_done_t done = *(slice_done_t *)(slice + SLICE_PROC_OFF);
    void *udata = *(void **)(slice + SLICE_DATA_OFF);
    unsigned nbufs, i;

    g_slices++;
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
        const float *data = *(const float **)(b + 8);
        unsigned n = bytes / sizeof(float), j;
        if (i == 0 && data) {
            for (j = 0; j < n && g_pcm_n < PCM_CAP; j++)
                g_pcm[g_pcm_n++] = data[j];
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
        g_rate = *(const double *)p;
        fourcc(fid, *(const unsigned *)(p + 8));
        g_channels = *(const unsigned *)(p + 28);
        if (g_verbose) printf("  [au] StreamFormat scope=%u: %.0f Hz, '%s', flags 0x%x, "
               "%u ch, %u bits\n", scope, g_rate, fid,
               *(const unsigned *)(p + 12), g_channels,
               *(const unsigned *)(p + 32));
    } else if (id == kAUProp_ScheduleAudioSlice && data) {
        take_slice((unsigned char *)data);
    } else if (id == kAUProp_ScheduleStartTime) {
        if (g_verbose) printf("  [au] ScheduleStartTime sampleTime %.1f\n",
               data ? *(const double *)data : 0.0);
    } else {
        if (g_verbose) printf("  [au] SetProperty id=%u scope=%u size=%u\n", id, scope, size);
    }
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
