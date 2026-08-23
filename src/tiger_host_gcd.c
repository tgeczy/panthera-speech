/* tiger_host_gcd.c -- just enough libdispatch and blocks for Lion.
 *
 * Part of tiger_host.c, which includes it; see there for why this is one
 * translation unit.
 *
 * Leopard's MacinTalk uses Multiprocessing Services -- MPCreateTask,
 * MPWaitOnQueue -- and those are shimmed in tiger_host_shims.c. **Lion's uses
 * Grand Central Dispatch and blocks**, which is one of the larger differences
 * between the two engines and the reason 14 of Lion's stubbed symbols get
 * called where Leopard's equivalents never were.
 *
 * A stubbed `dispatch_sync` is not a no-op in the harmless sense: the work is
 * *in* the block, so the call returns having done nothing and the engine
 * carries on as though it had. That is how the render reached its `Samples`
 * stage and produced a 46-byte wav -- a header and no audio, with no error
 * anywhere.
 *
 * **Everything here runs the block on the calling thread.** `dispatch_async`
 * included, which is a real semantic change -- a queue that is supposed to
 * return immediately does not. It is the right first answer: this host renders
 * one utterance at a time and already serialises the engine's own worker, so
 * running the work early is at worst wasted parallelism, where deferring it
 * with nothing to run it later is silence. If something turns out to depend on
 * async being asynchronous it will deadlock rather than go quiet, which is the
 * failure worth having.
 */

/* Defined in tiger_host_fault.c, which is included after this one. */
static const char *engine_symbol(void *addr);

/* **Every call from here into engine code goes through this.**
 *
 * Darwin's i386 ABI requires esp to be 16-byte aligned *at the call*, and the
 * engine relies on it: `MTBEAudioUnitSoundOutput::QueueSamples` stores a pair
 * of doubles to `[ebp-0x78]` with `movaps` almost as soon as it is entered,
 * and that faults outright on a stack Windows aligned to four.  Windows
 * reports the fault as an access violation reading **0xffffffff**, which names
 * neither alignment nor the thread that broke it.
 *
 * `tiger_host_audio.c` learned this for the pacer thread.  The GCD paths are
 * new with Lion and are a second front door into exactly the same code -- so
 * there is one door here, rather than four call sites each remembering. */
static void enter_engine(void (__cdecl *fn)(void *), void *arg)
{
    if (fn) call_aligned1((void *)fn, arg);
}

/* A block is an object whose fourth word is the function to call, and which
 * passes itself as the first argument.  The layout is fixed ABI:
 *
 *     struct Block_layout { void *isa; int flags; int reserved;
 *                           void (*invoke)(void *, ...); ... };
 */
static void block_invoke(void *block)
{
    if (!block) return;
    enter_engine((void (__cdecl *)(void *))((void **)block)[3], block);
}

/* `Block_copy` moves a stack block to the heap so it can outlive its scope.
 * Returning the same pointer is correct for every use here, because nothing
 * in this file defers a block past the call that supplied it -- and it stops
 * being correct the moment `dispatch_async` really is asynchronous. */
static void * __cdecl sh_Block_copy(void *block)   { return block; }
static void   __cdecl sh_Block_release(void *block) { (void)block; }

static void __cdecl sh_dispatch_sync(void *queue, void *block)
{
    (void)queue;
    block_invoke(block);
}

static void __cdecl sh_dispatch_async(void *queue, void *block)
{
    (void)queue;
    block_invoke(block);
}

/* `dispatch_once` with a stub predicate never ran the block at all, which is
 * the quietest possible way to leave a subsystem uninitialised.  The predicate
 * is a long the caller owns; anything non-zero means done. */
static void __cdecl sh_dispatch_once(long *pred, void *block)
{
    if (!pred) { block_invoke(block); return; }
    if (*pred) return;
    *pred = 1;
    block_invoke(block);
}

/* The `_f` forms take a context and a plain function instead of a block. */
static void __cdecl sh_dispatch_sync_f(void *queue, void *ctx,
                                       void (__cdecl *fn)(void *))
{
    (void)queue;
    enter_engine(fn, ctx);
}

static void __cdecl sh_dispatch_async_f(void *queue, void *ctx,
                                        void (__cdecl *fn)(void *))
{
    (void)queue;
    enter_engine(fn, ctx);
}

/* Queues and sources are handles the engine only ever passes back to us, so
 * they can be any distinct non-NULL pointer.  Returning NULL is what makes a
 * later `dispatch_resume(NULL)` look like a crash in libdispatch.
 *
 * A source is more than a handle, though.  **Lion drives its render loop off a
 * dispatch timer**: it creates a source, gives it a context and an event
 * handler, sets an interval, and resumes it -- and then expects that handler
 * to keep being called.  With all five of those stubbed the engine wrote one
 * frame and stopped, which is a 46-byte wav and no error.
 */
#define DSRC_MAGIC 0x44535243u          /* 'DSRC' */

/* **Lion uses the timer as a one-shot alarm, not as a metronome.**  Every
 * `dispatch_source_set_timer` it makes passes `DISPATCH_TIME_FOREVER` as the
 * interval, so the source is meant to fire once at `start` and then wait to be
 * re-armed -- which is how a render pump asks to be called back when it next
 * has work, rather than polling.
 *
 * A thread that fires immediately on resume and exits therefore gets exactly
 * one frame out of the engine and is not there when the re-arm arrives. So the
 * thread has to outlive its firing and wait on an event the re-arm signals,
 * and `start` has to be honoured rather than ignored.
 */
typedef struct {
    unsigned  magic;
    void     *ctx;
    void (__cdecl *handler)(void *);
    volatile unsigned period_ms;        /* 0 = do not repeat after firing */
    volatile unsigned delay_ms;         /* how long until the next firing */
    volatile long     armed;            /* set by set_timer, cleared on fire */
    HANDLE    rearm;                    /* signalled by set_timer */
    HANDLE    thread;
    volatile long running;
    unsigned  fired;
    int       reaps_graph;              /* the deferred audio-graph stop */
} dsource;

static dsource g_sources[16];
static int g_nsources;
static int g_dispatch_handles[64];
static int g_ndispatch;

static void *dispatch_handle(void)
{
    if (g_ndispatch >= (int)(sizeof(g_dispatch_handles) /
                             sizeof(g_dispatch_handles[0])))
        return &g_dispatch_handles[0];
    return &g_dispatch_handles[g_ndispatch++];
}

static dsource *as_source(void *o)
{
    dsource *s = (dsource *)o;
    return (s && s->magic == DSRC_MAGIC) ? s : NULL;
}

static DWORD WINAPI source_thread(LPVOID param)
{
    dsource *s = (dsource *)param;
    while (s->running) {
        /* Disarmed means "nothing scheduled": wait for a re-arm rather than
         * spinning or exiting.  Armed means wait out the delay, unless a
         * re-arm arrives first and changes it. */
        DWORD ms, r;
        /* A timer that is already due is delivered without asking Windows
         * for anything: waiting nought milliseconds on an event still costs
         * the whole syscall pair, and Lion arms this one for *now* out of its
         * own handler.
         *
         * **This is not what made Lion slow, and the first version of this
         * comment said it was.**  The pair looked like the whole cost -- 5.4
         * million of them for one sentence against a 2.5 second render -- and
         * removing it changed the render time by nothing at all, because the
         * loop simply spun faster: 13.1 million iterations instead of 5.4, and
         * the same 2.46 seconds.  The clock was the cause (see
         * `sh_gettimeofday`), and this is worth keeping only because a
         * due-now timer should not cost two syscalls.
         *
         * The yield matters more than the saving: the pacer thread is the
         * engine's clock, and a loop that never yields can starve it. */
        if (s->armed && s->delay_ms == 0) {
            s->armed = 0;
            g_w_src_zero++;
            if (s->handler) { g_gcd_handler = (void *)s->handler;
                              enter_engine(s->handler, s->ctx); s->fired++; }
            if (s->period_ms) { s->delay_ms = s->period_ms; s->armed = 1; }
            if ((s->fired & 0x3f) == 0) SwitchToThread();
            continue;
        }
        ms = s->armed ? s->delay_ms : INFINITE;
        {   double t0 = tick_ms();
            r = WaitForSingleObject(s->rearm, ms);
            if (ms != INFINITE) { g_w_src_n++; g_w_src_ms += tick_ms() - t0;
                if (r == WAIT_TIMEOUT) g_w_src_fire++; else g_w_src_rearm++; } }
        if (!s->running) break;
        if (r != WAIT_TIMEOUT) continue;        /* re-armed; re-read the delay */
        s->armed = 0;
        /* This thread is ours, so Windows aligned its stack to four; the
         * engine's render handler needs sixteen. */
        if (s->handler) {
            static void *seen[8]; static int nseen; int k, known = 0;
            for (k = 0; k < nseen; k++) if (seen[k] == (void *)s->handler) known = 1;
            if (!known && nseen < 8) { seen[nseen++] = (void *)s->handler;
                fprintf(stderr, "tiger_host: gcd source %p fired on time, "
                                "handler %s\n", (void *)s,
                        engine_symbol((void *)s->handler)); }
            g_gcd_handler = (void *)s->handler;
            /* Say so on the way in *and* on the way out.  A handler that fires
             * seconds after the last utterance and never returns is how the
             * channel wedges while the host still looks alive, and the two
             * cases -- ran and returned, versus went in and stayed -- are
             * indistinguishable from a single line. */
            if (g_gcd_log)
                fprintf(stderr, "tiger_host: gcd %p entering %s\n",
                        (void *)s, engine_symbol((void *)s->handler));
            enter_engine(s->handler, s->ctx); s->fired++;
            if (g_gcd_log)
                fprintf(stderr, "tiger_host: gcd %p returned after %u "
                                "firing(s)\n", (void *)s, s->fired); }
        if (s->period_ms) {                     /* a real repeating timer */
            s->delay_ms = s->period_ms;
            s->armed = 1;
        }
    }
    if (g_verbose)
        printf("  [gcd] source %p ended after %u firing(s)\n",
               (void *)s, s->fired);
    return 0;
}

static void * __cdecl sh_dispatch_source_create(void *type, unsigned long handle,
                                                unsigned long mask, void *queue)
{
    dsource *s;
    (void)type; (void)handle; (void)mask; (void)queue;
    if (g_nsources >= (int)(sizeof(g_sources) / sizeof(g_sources[0])))
        return dispatch_handle();
    s = &g_sources[g_nsources++];
    memset(s, 0, sizeof(*s));
    s->magic = DSRC_MAGIC;
    if (g_verbose) printf("  [gcd] source %p created\n", (void *)s);
    return s;
}

static void __cdecl sh_dispatch_set_context(void *o, void *ctx)
{
    dsource *s = as_source(o);
    if (s) s->ctx = ctx;
}

static void * __cdecl sh_dispatch_get_context(void *o)
{
    dsource *s = as_source(o);
    return s ? s->ctx : NULL;
}

/* **The audio graph must not be torn down between utterances.**
 *
 * A few seconds after it stops speaking, Lion's engine arms a one-shot timer
 * on `_MTBEAudioUnitDeferredStopAudioGraph`, which calls `AUGraphStop` and puts
 * `MTBEAudioUnitSoundOutput` into its stopped state.  On a Mac that is good
 * manners: it hands the audio device back so the machine can idle.
 *
 * Here it wedges the engine.  Once it has fired, the next `SESpeakBuffer` does
 * not return -- measured, a fresh host speaks fine, sits quiet for five
 * seconds and then never speaks again.  That is Jerry's report exactly ("if I
 * don't do anything for a minute or two, then the next thing I do doesn't
 * talk"), and it is why Lion drops focus announcements where Leopard does not:
 * 10.5's MacinTalk has no GCD at all and so has no deferred anything.
 *
 * The graph it is being polite about does not exist.  There is no device, no
 * power to save and nothing to hand back -- `tiger_host_audio.c` is a fake
 * AUGraph that only collects buffers.  So the stop costs us the bug and buys
 * nothing, and refusing to arm it leaves the engine in the state it is in
 * while speech is flowing, which is the state every measurement in this
 * repository was taken in.
 *
 * Refused at the source rather than inside `sh_AUGraphStop`, because the
 * engine sets its own stopped flag straight after that call returns and we
 * cannot decline that part.  Nothing else stops the graph: `[au] stop` is 0 on
 * 96 of 96 Lion utterances.
 *
 * Named, not addressed -- the load address moves every run.  The lookup is
 * done once, here, because `set_timer` is called two and a half thousand times
 * an utterance and `engine_symbol` walks the symbol table. */
static void __cdecl sh_dispatch_source_set_event_handler_f(
        void *o, void (__cdecl *fn)(void *))
{
    dsource *s = as_source(o);
    if (s) {
        s->handler = fn;
        s->reaps_graph = fn && strstr(engine_symbol((void *)fn),
                                      "DeferredStopAudioGraph") != NULL;
        if (s->reaps_graph)
            fprintf(stderr, "tiger_host: the engine's deferred audio-graph "
                            "stop is disarmed for the life of this host\n");
    }
    if (g_verbose)
        printf("  [gcd] handler %p on source %p%s\n", (void *)fn, o,
               s ? "" : "  <- NOT one of ours");
}

/* `start` is an absolute dispatch_time_t and the other two are nanoseconds; on
 * i386 each arrives as two words.
 *
 * **`start` is the whole point of the call and was being ignored.** Lion arms
 * this as a one-shot -- "call me back in n milliseconds" -- so a version that
 * fires immediately and a version that never fires are both wrong in the same
 * way: the engine's sense of when it may produce the next slice comes from
 * here.
 */
static void __cdecl sh_dispatch_source_set_timer(void *o,
                                                 unsigned __int64 start,
                                                 unsigned __int64 interval,
                                                 unsigned __int64 leeway)
{
    dsource *s = as_source(o);
    unsigned __int64 now;
    (void)leeway;
    if (!s) return;
    /* See `sh_dispatch_source_set_event_handler_f`.  The engine arms and
     * disarms this one freely; we simply never let it become due.  The delay
     * it asks for is the engine's own patience with a silent listener, and it
     * is the number the regression test has to outwait. */
    if (s->reaps_graph && !g_deferred_stop) {
        if (g_gcd_log) {
            unsigned __int64 t = (unsigned __int64)GetTickCount64() * 1000000ULL;
            fprintf(stderr, "tiger_host: refusing a deferred graph stop "
                            "%.0f ms out\n",
                    start > t ? (double)(start - t) / 1e6 : 0.0);
        }
        s->armed = 0;
        return;
    }

    /* `DISPATCH_TIME_FOREVER` arrives as INT64_MAX, not as ~0ull, and a
     * threshold set at the unsigned end missed it -- turning "never repeat"
     * into a 24-day period by way of a truncating divide. The test is about
     * what is plausible for an utterance rather than about one constant. */
    if (interval == 0 || interval > 3600ULL * 1000000000ULL)
        s->period_ms = 0;
    else {
        unsigned ms = (unsigned)(interval / 1000000ULL);
        s->period_ms = ms ? ms : 1;     /* never spin */
    }

    /* The same clock `sh_dispatch_time` hands out, so the subtraction means
     * something.  `DISPATCH_TIME_NOW` is 0 and any start already past is due
     * immediately rather than overdue by a very large unsigned number.
     *
     * **A start of `DISPATCH_TIME_FOREVER` means disarm**, and it was being
     * read as a delay and clamped -- so "never fire this" became "fire this in
     * a minute".  `MTBEAudioUnitSoundOutput::StartAudioGraph` disarms the
     * deferred stop exactly this way, with `~0ull` for the start, which is the
     * one caller where getting it wrong is a timer going off behind the
     * engine's back.  Same bug the interval already had a guard for, one
     * argument along. */
    now = (unsigned __int64)GetTickCount64() * 1000000ULL;
    if (start > (unsigned __int64)3600 * 24 * 1000000000ULL + now) {
        s->armed = 0;
        s->delay_ms = 0;
        g_w_settimer++;
        if (g_verbose) printf("  [gcd] timer on %p: disarmed\n", o);
        return;
    }
    if (start == 0 || start <= now)
        s->delay_ms = 0;
    else {
        unsigned __int64 d = (start - now) / 1000000ULL;
        s->delay_ms = d > 60000ULL ? 60000u : (unsigned)d;  /* a minute is
                                                             * already absurd */
    }
    s->armed = 1;
    g_w_settimer++;
    if (s->delay_ms == 0) g_w_settimer_now++;
    if (s->rearm) SetEvent(s->rearm);   /* wake the wait to re-read it */
    if (g_verbose)
        printf("  [gcd] timer on %p: fire in %u ms, then every %u ms\n",
               o, s->delay_ms, s->period_ms);
}

static void __cdecl sh_dispatch_resume(void *o)
{
    dsource *s = as_source(o);
    if (!s) { if (g_verbose) printf("  [gcd] resume(%p) not a source\n", o);
              return; }
    if (s->running) return;
    /* Auto-reset: each `set_timer` wakes the wait exactly once. */
    if (!s->rearm) s->rearm = CreateEvent(NULL, FALSE, FALSE, NULL);
    s->running = 1;
    s->thread = CreateThread(NULL, 0, source_thread, s, 0, NULL);
    if (g_verbose)
        printf("  [gcd] resumed %p (handler %p, armed=%ld, %u ms)\n", o,
               (void *)s->handler, s->armed, s->delay_ms);
}

static void __cdecl sh_dispatch_suspend(void *o)
{
    dsource *s = as_source(o);
    if (s) s->running = 0;
}

static void __cdecl sh_dispatch_source_cancel(void *o)
{
    dsource *s = as_source(o);
    if (s) s->running = 0;
}

/* Nanoseconds since the process started, which is all either of these is used
 * for here -- the engine only ever adds a delta and hands the result back. */
static unsigned __int64 __cdecl sh_dispatch_time(unsigned __int64 when,
                                                 __int64 delta)
{
    (void)when;
    return (unsigned __int64)GetTickCount64() * 1000000ULL + delta;
}

static unsigned __int64 __cdecl sh_dispatch_walltime(void *when, __int64 delta)
{
    (void)when;
    return (unsigned __int64)GetTickCount64() * 1000000ULL + delta;
}

static void * __cdecl sh_dispatch_queue_create(const char *label, void *attr)
{
    (void)label; (void)attr;
    return dispatch_handle();
}

static void * __cdecl sh_dispatch_get_global_queue(long pri, unsigned long f)
{ (void)pri; (void)f; return dispatch_handle(); }

static void __cdecl sh_dispatch_release(void *o) { (void)o; }
static void __cdecl sh_dispatch_retain(void *o)  { (void)o; }

/* `OSAtomicAdd32Barrier` is a real atomic and has to stay one: the engine uses
 * it to hand buffers between its own threads. */
static int __cdecl sh_OSAtomicAdd32Barrier(int amount, volatile long *value)
{
    return (int)InterlockedExchangeAdd(value, amount) + amount;
}

static int __cdecl sh_OSAtomicAdd32(int amount, volatile long *value)
{
    return (int)InterlockedExchangeAdd(value, amount) + amount;
}
