/* tiger_host_shims.c -- libc, pthreads, Multiprocessing, the rune locale and the BLAS.
 *
 * Part of tiger_host.c, which includes it; see there for why this is one
 * translation unit. */

/* ---- shims ------------------------------------------------------------- */
/*
 * Anything not in this table gets a thunk that records the call and returns 0.
 * Aborting on the first miss would tell us one name per run; recording and
 * carrying on tells us the whole runtime set in one go, and the engine's own
 * error handling usually turns a NULL into a clean OSErr rather than a crash.
 * Which of the ~96 Apple imports actually matter is a question for measurement,
 * not for reading the import table.
 */
#define MAX_MISSING 512
static const char *g_missing[MAX_MISSING];
static int g_missing_hits[MAX_MISSING];
static int g_nmissing;

static int __cdecl shim_missing(int idx)
{
    if (idx >= 0 && idx < g_nmissing) {
        if (g_missing_hits[idx]++ == 0 && g_verbose)
            printf("  [shim] first call: %s\n", g_missing[idx]);
    }
    return 0;
}

/* bcopy and bzero take their arguments in an order memcpy/memset do not. */
static void  __cdecl sh_bcopy(const void *s, void *d, size_t n) { memmove(d, s, n); }
static void  __cdecl sh_bzero(void *d, size_t n)                { memset(d, 0, n); }
static int   __cdecl sh_abort_(void) { die("engine called abort()"); return 0; }

/* Thread-local storage, which libstdc++ keeps its locale and exception state
 * in.
 *
 * Stubbed, `pthread_key_create` reports success without allocating a key and
 * `pthread_getspecific` then answers NULL forever -- so the library believes
 * it has per-thread state and never finds any. What that produces is not a
 * null dereference but **heap corruption**, somewhere later and in someone
 * else's allocation, because the object it thought it had cached gets built
 * again and freed twice.
 *
 * Windows has exactly this, under different names. Leopard's 6.0.4 never
 * reached these paths; Lion's 6.0.9 does.
 */
static int __cdecl sh_pthread_key_create(unsigned *key, void *dtor)
{
    DWORD k;
    (void)dtor;                 /* no destructors: this host never joins */
    if (!key) return 22;        /* EINVAL */
    k = TlsAlloc();
    if (k == TLS_OUT_OF_INDEXES) return 12;                 /* ENOMEM */
    *key = (unsigned)k;
    return 0;
}
static int __cdecl sh_pthread_key_delete(unsigned key)
{ return TlsFree((DWORD)key) ? 0 : 22; }
static void * __cdecl sh_pthread_getspecific(unsigned key)
{ return TlsGetValue((DWORD)key); }
static int __cdecl sh_pthread_setspecific(unsigned key, const void *val)
{ return TlsSetValue((DWORD)key, (LPVOID)val) ? 0 : 22; }

/* The stack canary is a *data* symbol.  Bound to a thunk it is the address of
 * some code, which happens to read back the same both times a function checks
 * it -- so it works by accident until the day that memory is written. A real
 * word costs nothing and makes the guard mean what it says. */
static unsigned long g_stack_chk_guard = 0xDEADC0DEu;
static void __cdecl sh_stack_chk_fail(void)
{ die("stack check failed inside the engine"); }

/* 64-bit division, which a 32-bit compiler emits calls to rather than code.
 *
 * These are libgcc's, and stubbing them is not a missing feature -- it is
 * arithmetic that silently returns nothing. Lion's engine works out how many
 * frames an utterance is in 64 bits, so `__udivdi3` answering zero made it
 * schedule **one frame** and call the utterance done: a 46-byte wav, `OSErr
 * 0`, and no error anywhere. Leopard's engine never calls them.
 *
 * MSVC generates the same operations inline for `long long`, so each of these
 * is one line and exactly right rather than approximately so.
 */
static long long __cdecl sh_divdi3(long long a, long long b)
{ return b ? a / b : 0; }
static unsigned long long __cdecl sh_udivdi3(unsigned long long a,
                                             unsigned long long b)
{ return b ? a / b : 0; }
static long long __cdecl sh_moddi3(long long a, long long b)
{ return b ? a % b : 0; }
static unsigned long long __cdecl sh_umoddi3(unsigned long long a,
                                             unsigned long long b)
{ return b ? a % b : 0; }

/* pthreads, on top of critical sections.
 *
 * Darwin's pthread_mutex_t is 44 bytes of opaque storage, which is room enough
 * to keep a CRITICAL_SECTION inside it rather than off to the side.  The magic
 * word means a mutex that was never passed to pthread_mutex_init -- a static
 * PTHREAD_MUTEX_INITIALIZER -- still works, because lock initialises it on
 * first use. */
#define MTX_MAGIC 0x5449474d            /* 'TIGM' */
typedef struct { unsigned magic; CRITICAL_SECTION cs; } mtx;

static void mtx_ready(mtx *m)
{
    if (m->magic != MTX_MAGIC) {
        InitializeCriticalSection(&m->cs);
        m->magic = MTX_MAGIC;
    }
}
static int __cdecl sh_mutex_init(void *m, void *attr)
{ (void)attr; ((mtx *)m)->magic = 0; mtx_ready((mtx *)m); return 0; }
static int __cdecl sh_mutex_lock(void *m)
{ mtx_ready((mtx *)m); EnterCriticalSection(&((mtx *)m)->cs); return 0; }
static int __cdecl sh_mutex_unlock(void *m)
{ mtx_ready((mtx *)m); LeaveCriticalSection(&((mtx *)m)->cs); return 0; }
static int __cdecl sh_mutexattr_init(void *a) { (void)a; return 0; }
/* The attribute is only ever set to PTHREAD_MUTEX_RECURSIVE, and every mutex
 * here is a CRITICAL_SECTION, which is recursive already. */
static int __cdecl sh_mutexattr_settype(void *a, int t)
{ (void)a; (void)t; return 0; }

/* `SLMorphTraits` opens a locale in its constructor and keeps the `locale_t`
 * at `this+4`, handing it to the `_l` family from then on.  Those all ignore
 * it here -- there is one locale -- but the token still has to be non-NULL:
 * a NULL `locale_t` is how the C library says the call failed, and code that
 * checks will take a different path for the rest of the run.
 *
 * So hand back the address of a real object.  Nothing dereferences it, and if
 * anything ever does it finds zeroes rather than an address that was never
 * mapped. */
static struct { int mask; char name[32]; } g_the_locale;

static void * __cdecl sh_newlocale(int mask, const char *name, void *base)
{
    (void)base;
    g_the_locale.mask = mask;
    if (name) {
        strncpy(g_the_locale.name, name, sizeof(g_the_locale.name) - 1);
        g_the_locale.name[sizeof(g_the_locale.name) - 1] = 0;
    }
    return &g_the_locale;
}
static void __cdecl sh_freelocale(void *loc) { (void)loc; }

/* Time of day, for a caller that only ever measures intervals with it. */
static int __cdecl sh_gettimeofday(void *tv, void *tz)
{
    /* struct timeval on i386 Darwin is two 32-bit words: seconds, then
     * microseconds. */
    unsigned *out = (unsigned *)tv;
    FILETIME ft;
    unsigned long long t;
    (void)tz;
    if (!out) return -1;
    GetSystemTimeAsFileTime(&ft);
    t = ((unsigned long long)ft.dwHighDateTime << 32) | ft.dwLowDateTime;
    t /= 10;                                   /* 100 ns ticks -> microseconds */
    t -= 11644473600000000ULL;                 /* 1601 epoch -> 1970 epoch */
    out[0] = (unsigned)(t / 1000000ULL);
    out[1] = (unsigned)(t % 1000000ULL);
    return 0;
}
/* pthread_once_t is NOT zero-initialised on Darwin: PTHREAD_ONCE_INIT puts the
 * signature 0x30B1BCBA in the first word.  Treating that word as a boolean
 * makes every once-routine look as though it had already run -- which silently
 * skipped the one that allocates MacinTalk's global scheduler objects, and
 * showed up much later as a null dereference in the speak path. */
#define ONCE_SIG_INIT 0x30b1bcbau
#define ONCE_SIG_DONE 0x54494731u       /* 'TIG1' */
static int __cdecl sh_once(unsigned *ctl, void (__cdecl *fn)(void))
{
    if (!ctl) return 0;
    if (*ctl != ONCE_SIG_DONE) {
        *ctl = ONCE_SIG_DONE;           /* before the call, against recursion */
        if (fn) fn();
    }
    return 0;
}

/* Multiprocessing Services critical regions, which are just mutexes with a
 * timeout argument the engine always passes as kDurationForever. */
static int __cdecl sh_mp_create_region(void **id)
{
    CRITICAL_SECTION *cs = (CRITICAL_SECTION *)calloc(1, sizeof(*cs));
    if (!cs) return -108;               /* memFullErr */
    InitializeCriticalSection(cs);
    if (id) *id = cs;
    return 0;
}
static int __cdecl sh_mp_enter_region(void *id, int timeout)
{ (void)timeout; if (id) EnterCriticalSection((CRITICAL_SECTION *)id); return 0; }
static int __cdecl sh_mp_exit_region(void *id)
{ if (id) LeaveCriticalSection((CRITICAL_SECTION *)id); return 0; }

/* Multiprocessing Services tasks and queues.
 *
 * The engine's back end is not a function you call and wait on -- it starts
 * worker tasks and talks to them through message queues (MTBEWorkerStartMPTask
 * is one of its own exports).  Stubbing these out returns success while
 * handing back a null queue, which is worse than failing: the engine believes
 * it has a worker and dereferences the queue later.
 *
 * A queue is a bounded FIFO of three-word messages guarded by a critical
 * section, with a semaphore for the waiter.  MP messages are always exactly
 * three pointers wide.
 */
#define MPQ_CAP 256
typedef struct {
    CRITICAL_SECTION cs;
    HANDLE           sem;
    void            *msg[MPQ_CAP][3];
    int              head, tail, count;
} mpqueue;

static int __cdecl sh_mp_create_queue(mpqueue **out)
{
    mpqueue *q = (mpqueue *)calloc(1, sizeof(*q));
    if (!q) return -108;
    InitializeCriticalSection(&q->cs);
    q->sem = CreateSemaphoreA(NULL, 0, MPQ_CAP, NULL);
    if (!q->sem) { free(q); return -108; }
    if (out) *out = q;
    if (g_verbose) printf("  [mp] CreateQueue -> %p\n", (void *)q);
    return 0;
}

static int __cdecl sh_mp_notify_queue(mpqueue *q, void *p1, void *p2, void *p3)
{
    if (!q) return -50;                         /* paramErr */
    EnterCriticalSection(&q->cs);
    if (q->count >= MPQ_CAP) { LeaveCriticalSection(&q->cs); return -1; }
    q->msg[q->tail][0] = p1;
    q->msg[q->tail][1] = p2;
    q->msg[q->tail][2] = p3;
    q->tail = (q->tail + 1) % MPQ_CAP;
    q->count++;
    LeaveCriticalSection(&q->cs);
    ReleaseSemaphore(q->sem, 1, NULL);
    return 0;
}

/* How much faster than real time the engine is allowed to run.
 *
 * The engine schedules against the wall clock: its worker computes a delay
 * from UpTime to its next event and waits that long.  Completing slices early
 * therefore does nothing on its own -- the engine simply waits and emits empty
 * slices, which is why every pace below 100% rendered silence.  Speed up its
 * clock by the same factor and the whole timeline compresses with it.
 *
 * 128 is measured, not chosen: output is byte-identical to a 1x render for
 * every voice, and the time curve flattens just past here because what is
 * left is the actual synthesis rather than any waiting.  TIGER_SPEED overrides
 * it -- 1 renders in true real time, which is the honest baseline to compare
 * against if audio ever looks wrong. */
static double g_speed = 128.0;

/* Duration: positive is milliseconds, negative is negated microseconds,
 * kDurationForever is 0x7fffffff and kDurationImmediate is 0.  Engine
 * durations are in engine time, so a real wait is that divided by g_speed. */
static DWORD duration_ms(int d)
{
    double ms;
    if (d == 0x7fffffff) return INFINITE;
    ms = (d < 0) ? (-(double)d / 1000.0) : (double)d;
    ms /= g_speed;
    return (DWORD)(ms + 0.999);
}

static int __cdecl sh_mp_wait_on_queue(mpqueue *q, void **p1, void **p2,
                                       void **p3, int timeout)
{
    if (!q) return -50;
    if (g_mp_waits++ < 6)
        if (g_verbose) printf("  [mp] WaitOnQueue %p timeout=%d\n", (void *)q, timeout);
    if (WaitForSingleObject(q->sem, duration_ms(timeout)) != WAIT_OBJECT_0)
        return -30988;                          /* kMPTimeoutErr */
    EnterCriticalSection(&q->cs);
    if (p1) *p1 = q->msg[q->head][0];
    if (p2) *p2 = q->msg[q->head][1];
    if (p3) *p3 = q->msg[q->head][2];
    q->head = (q->head + 1) % MPQ_CAP;
    q->count--;
    LeaveCriticalSection(&q->cs);
    return 0;
}

typedef int (__cdecl *mp_taskproc)(void *param);
typedef struct {
    mp_taskproc entry;
    void       *param;
    mpqueue    *notify;
    void       *t1, *t2;
    HANDLE      thread;
} mptask;

/* Darwin's i386 ABI requires ESP to be 16-byte aligned at every call
 * instruction.  Windows requires four.  Apple's compiler took that guarantee
 * and used it: `MTBEWorker::Timestamp` stores a pair of doubles into its own
 * frame with `movapd`, and `movapd` faults outright on a misaligned address.
 *
 * Inside the engine this can never break, because every frame it builds keeps
 * the alignment it was given.  It breaks where we hand control over -- and a
 * thread entry point is the worst place for it, since the alignment a Windows
 * thread starts with is neither ours to choose nor the same every run.
 * Leopard's engine starts two worker tasks and dies at the first `movapd` one
 * of them reaches.
 *
 * Tiger's engine never showed this.  That is not evidence that it was safe --
 * only that one compiler declined to vectorise one function.
 */
static __declspec(naked) int call_aligned1(void *fn, void *a)
{
    __asm {
        push ebp
        mov  ebp, esp
        push ebx                     /* callee-saved in both ABIs */
        mov  ebx, esp                /* remember the real stack */
        mov  eax, [ebp + 8]          /* fn */
        mov  ecx, [ebp + 12]         /* a  */
        and  esp, -16                /* 16-byte aligned from here */
        sub  esp, 16                 /* room for the argument, still aligned */
        mov  [esp], ecx
        call eax                     /* ESP is 0 mod 16 at the call */
        mov  esp, ebx                /* give back whatever alignment cost */
        pop  ebx
        pop  ebp
        ret
    }
}

/* The same for two arguments.  Eight bytes of argument and eight of padding:
 * the ABI cares that ESP is 16-byte aligned at the call, not that the
 * arguments fill the space. */
static __declspec(naked) int call_aligned2(void *fn, void *a, void *b)
{
    __asm {
        push ebp
        mov  ebp, esp
        push ebx
        mov  ebx, esp
        mov  eax, [ebp + 8]          /* fn */
        mov  ecx, [ebp + 12]         /* a  */
        mov  edx, [ebp + 16]         /* b  */
        and  esp, -16
        sub  esp, 16
        mov  [esp], ecx
        mov  [esp + 4], edx
        call eax
        mov  esp, ebx
        pop  ebx
        pop  ebp
        ret
    }
}

/* Three and four.  Sixteen bytes covers both without a second adjustment, and
 * these are the shapes the exported entry points take: SEUseVoice and
 * SESetSpeechInfo are three, SESpeakBuffer is four. */
static __declspec(naked) int call_aligned3(void *fn, void *a, void *b, void *c)
{
    __asm {
        push ebp
        mov  ebp, esp
        push ebx
        mov  ebx, esp
        mov  eax, [ebp + 8]
        mov  ecx, [ebp + 12]
        mov  edx, [ebp + 16]
        and  esp, -16
        sub  esp, 16
        mov  [esp], ecx
        mov  [esp + 4], edx
        mov  ecx, [ebp + 20]
        mov  [esp + 8], ecx
        call eax
        mov  esp, ebx
        pop  ebx
        pop  ebp
        ret
    }
}

static __declspec(naked) int call_aligned4(void *fn, void *a, void *b, void *c,
                                           void *d)
{
    __asm {
        push ebp
        mov  ebp, esp
        push ebx
        mov  ebx, esp
        mov  eax, [ebp + 8]
        mov  ecx, [ebp + 12]
        mov  edx, [ebp + 16]
        and  esp, -16
        sub  esp, 16
        mov  [esp], ecx
        mov  [esp + 4], edx
        mov  ecx, [ebp + 20]
        mov  edx, [ebp + 24]
        mov  [esp + 8], ecx
        mov  [esp + 12], edx
        call eax
        mov  esp, ebx
        pop  ebx
        pop  ebp
        ret
    }
}

/* CreateThread wants __stdcall; the engine's entry point is Mach-O i386 and so
 * is __cdecl.  This trampoline is the whole reason it exists -- and the place
 * the alignment above has to be established. */
static DWORD WINAPI mp_thunk(LPVOID arg)
{
    mptask *t = (mptask *)arg;
    int status = call_aligned1(t->entry, t->param);
    if (t->notify)
        sh_mp_notify_queue(t->notify, t->t1, t->t2, (void *)(intptr_t)status);
    return (DWORD)status;
}

static int __cdecl sh_mp_create_task(mp_taskproc entry, void *param,
                                     unsigned stacksize, mpqueue *notify,
                                     void *t1, void *t2, unsigned options,
                                     mptask **out)
{
    mptask *t;
    (void)options;
    if (!entry) return -50;
    t = (mptask *)calloc(1, sizeof(*t));
    if (!t) return -108;
    t->entry = entry; t->param = param;
    t->notify = notify; t->t1 = t1; t->t2 = t2;
    if (g_verbose) printf("  [mp] CreateTask entry=%p param=%p notify=%p\n",
           (void *)entry, param, (void *)notify);
    t->thread = CreateThread(NULL, stacksize, mp_thunk, t, 0, NULL);
    if (!t->thread) { free(t); return -108; }
    if (out) *out = t;
    return 0;
}

static int __cdecl sh_mp_terminate_task(mptask *t, int status)
{
    (void)status;
    if (t && t->thread) { CloseHandle(t->thread); t->thread = NULL; }
    return 0;
}

/* UpTime returns an AbsoluteTime, a 64-bit value in an unspecified unit; the
 * Duration converters below define what it means.  QPC is the same shape. */
static long long qpc_freq(void)
{
    static long long f;
    if (!f) { LARGE_INTEGER l; QueryPerformanceFrequency(&l); f = l.QuadPart; }
    return f;
}
static long long __cdecl sh_uptime(void)
{
    LARGE_INTEGER c;
    QueryPerformanceCounter(&c);
    return (long long)(c.QuadPart * g_speed);
}

/* A Duration is milliseconds when positive and negated microseconds when
 * negative, so sub-millisecond intervals survive.  Everything below shares one
 * clock, so the absolute unit does not matter as long as it is consistent. */
static int ticks_to_duration(long long ticks)
{
    double ms = (double)ticks * 1000.0 / (double)qpc_freq();
    if (ms > 2147483000.0) return 0x7fffffff;
    if (ms < -2147483000.0) return -0x7ffffffe;
    if (ms > -1.0 && ms < 1.0) return (int)(-ms * 1000.0);   /* microseconds */
    return (int)ms;
}
static long long duration_to_ticks(int d)
{
    if (d == 0x7fffffff) return qpc_freq() * 3600;
    if (d < 0) return (long long)(-(double)d * (double)qpc_freq() / 1e6);
    return (long long)((double)d * (double)qpc_freq() / 1e3);
}

/* AbsoluteDeltaToDuration takes TWO AbsoluteTimes -- four words on the stack.
 * Declaring it with one made the worker compute a wake-up 52 hours out and
 * sleep through every utterance, which presents as an engine that runs
 * perfectly and emits nothing. */
static int __cdecl sh_abs_delta_to_duration(long long a, long long b)
{ return ticks_to_duration(a - b); }
static int __cdecl sh_abs_to_duration(long long a)
{ return ticks_to_duration(a); }
static long long __cdecl sh_add_duration(int d, long long a)
{ return a + duration_to_ticks(d); }
static long long __cdecl sh_sub_duration(int d, long long a)
{ return a - duration_to_ticks(d); }

/* ---- __DefaultRuneLocale ----------------------------------------------- */
/*
 * BSD ctype is a table lookup the compiler inlines, so this is a *data*
 * symbol the engine indexes directly -- a function shim cannot stand in for
 * it.  The layout is not guessed: MacinTalk's inlined isalpha() reads
 *
 *      test byte ptr [rune + edx*4 + 0x35], 1
 *
 * so __runetype is at 0x34 and the tested bit is 0x100, which the slow path
 * confirms by calling ___maskrune(c, 0x100).  __maplower and __mapupper follow
 * it, each 256 entries of 4 bytes.
 */
#define RUNE_RUNETYPE 0x34
#define RUNE_MAPLOWER (RUNE_RUNETYPE + 256 * 4)
#define RUNE_MAPUPPER (RUNE_MAPLOWER + 256 * 4)
#define RUNE_SIZE     (RUNE_MAPUPPER + 256 * 4 + 64)

#define _CTYPE_A 0x00000100  /* alpha  */
#define _CTYPE_C 0x00000200  /* control*/
#define _CTYPE_D 0x00000400  /* digit  */
#define _CTYPE_G 0x00000800  /* graph  */
#define _CTYPE_L 0x00001000  /* lower  */
#define _CTYPE_P 0x00002000  /* punct  */
#define _CTYPE_S 0x00004000  /* space  */
#define _CTYPE_U 0x00008000  /* upper  */
#define _CTYPE_X 0x00010000  /* xdigit */
#define _CTYPE_B 0x00020000  /* blank  */
#define _CTYPE_R 0x00040000  /* print  */

static unsigned char g_rune_locale[RUNE_SIZE];

static unsigned rune_mask(int c)
{
    unsigned m = 0;
    if (c < 0 || c > 255) return 0;
    if (isalpha(c))  m |= _CTYPE_A;
    if (iscntrl(c))  m |= _CTYPE_C;
    if (isdigit(c))  m |= _CTYPE_D;
    if (isgraph(c))  m |= _CTYPE_G;
    if (islower(c))  m |= _CTYPE_L;
    if (ispunct(c))  m |= _CTYPE_P;
    if (isspace(c))  m |= _CTYPE_S;
    if (isupper(c))  m |= _CTYPE_U;
    if (isxdigit(c)) m |= _CTYPE_X;
    if (c == ' ' || c == '\t') m |= _CTYPE_B;
    if (isprint(c))  m |= _CTYPE_R;
    /* The low eight bits carry the digit value, which is how the xdigit
     * conversions read a hex digit straight out of the table. */
    if (isdigit(c))  m |= (unsigned)(c - '0');
    else if (isxdigit(c)) m |= (unsigned)(tolower(c) - 'a' + 10);
    return m;
}

static void init_rune_locale(void)
{
    int c;
    memcpy(g_rune_locale, "RuneMagi", 8);
    strcpy((char *)g_rune_locale + 8, "NONE");
    for (c = 0; c < 256; c++) {
        *(unsigned *)(g_rune_locale + RUNE_RUNETYPE + c * 4) = rune_mask(c);
        *(unsigned *)(g_rune_locale + RUNE_MAPLOWER + c * 4) =
            (unsigned)tolower(c);
        *(unsigned *)(g_rune_locale + RUNE_MAPUPPER + c * 4) =
            (unsigned)toupper(c);
    }
}

static unsigned __cdecl sh_maskrune(int c, unsigned f) { return rune_mask(c) & f; }
/* Lion's front end reaches for the locale-taking forms of the character and
 * string classifiers.  The extra argument is a `locale_t`, and this host has
 * exactly one locale, so each is the base function with the locale ignored --
 * and ignoring a *trailing* argument is free on cdecl, where the caller cleans
 * the stack.  That is why the whole `_l` family costs a line each rather than
 * a thunk each. */
static unsigned __cdecl sh_maskrune_l(int c, unsigned f, void *loc)
{ (void)loc; return rune_mask(c) & f; }
static int __cdecl sh_strncasecmp_l(const char *a, const char *b, size_t n,
                                    void *loc)
{ (void)loc; return _strnicmp(a, b, n); }
static int __cdecl sh_strcasecmp_l(const char *a, const char *b, void *loc)
{ (void)loc; return _stricmp(a, b); }
/* Microsoft's CRT has no exp2.  `log2f` and `sinh` are reached on Lion's Alex
 * path, which does not synthesise yet -- they are here because both are one
 * line and neither Tiger nor Leopard imports either, so nothing else can see
 * the difference. */
static double __cdecl sh_exp2(double x) { return pow(2.0, x); }
static float  __cdecl sh_log2f(float x) { return (float)(log(x) / log(2.0)); }
static int __cdecl sh_tolower_(int c) { return tolower(c); }
static int __cdecl sh_toupper_(int c) { return toupper(c); }
static int __cdecl sh_isdigit_(int c) { return isdigit(c); }

/* ---- the BLAS the engine actually uses --------------------------------- */
/*
 * Single-precision level 1 and 2, no vDSP.  These are not optional: the
 * synthesiser calls isamax to find a block's peak and sscal to scale by it,
 * so stubbing them leaves every sample unnormalised and the output pins to
 * full scale.  That sounds like a synthesiser working perfectly into a
 * clipped channel -- which is exactly what it is.
 */
#define CBLAS_ROWMAJOR 101
#define CBLAS_NOTRANS  111

static int __cdecl sh_isamax(int n, const float *x, int incx)
{
    int i, best = 0;
    float bv;
    if (n < 1 || incx <= 0 || !x) return 0;
    bv = (float)fabs(x[0]);
    for (i = 1; i < n; i++) {
        float v = (float)fabs(x[i * incx]);
        if (v > bv) { bv = v; best = i; }
    }
    return best;
}
static void __cdecl sh_sscal(int n, float a, float *x, int incx)
{
    int i;
    if (n < 1 || incx <= 0 || !x) return;
    for (i = 0; i < n; i++) x[i * incx] *= a;
}
static void __cdecl sh_scopy(int n, const float *x, int incx, float *y, int incy)
{
    int i;
    if (!x || !y) return;
    for (i = 0; i < n; i++) y[i * incy] = x[i * incx];
}
static void __cdecl sh_saxpy(int n, float a, const float *x, int incx,
                             float *y, int incy)
{
    int i;
    if (!x || !y) return;
    for (i = 0; i < n; i++) y[i * incy] += a * x[i * incx];
}
static float __cdecl sh_sdot(int n, const float *x, int incx,
                             const float *y, int incy)
{
    int i; double s = 0.0;
    if (!x || !y) return 0.0f;
    for (i = 0; i < n; i++) s += (double)x[i * incx] * y[i * incy];
    return (float)s;
}
static float __cdecl sh_snrm2(int n, const float *x, int incx)
{
    int i; double s = 0.0;
    if (!x) return 0.0f;
    for (i = 0; i < n; i++) { double v = x[i * incx]; s += v * v; }
    return (float)sqrt(s);
}
static void __cdecl sh_sgemv(int order, int trans, int m, int n, float alpha,
                             const float *a, int lda, const float *x, int incx,
                             float beta, float *y, int incy)
{
    int i, j;
    int leny = (trans == CBLAS_NOTRANS) ? m : n;
    int lenx = (trans == CBLAS_NOTRANS) ? n : m;
    if (!a || !x || !y) return;
    for (i = 0; i < leny; i++) y[i * incy] *= beta;
    for (i = 0; i < leny; i++) {
        double s = 0.0;
        for (j = 0; j < lenx; j++) {
            const float *e;
            if (order == CBLAS_ROWMAJOR)
                e = (trans == CBLAS_NOTRANS) ? &a[i * lda + j] : &a[j * lda + i];
            else
                e = (trans == CBLAS_NOTRANS) ? &a[j * lda + i] : &a[i * lda + j];
            s += (double)(*e) * x[j * incx];
        }
        y[i * incy] += (float)(alpha * s);
    }
}

static char * __cdecl sh_getenv(const char *n) { (void)n; return NULL; }
static long   __cdecl sh_random(void)          { return rand(); }
static void   __cdecl sh_srandom(unsigned s)   { srand(s); }
static void   __cdecl sh_usleep(unsigned us)   { Sleep(us / 1000); }

/* ---- memmove and memcpy, with the engine's own arithmetic distrusted ---- */
/*
 * `SLPrefixMorph::AddAffix` keeps a saved word's length in a **signed byte**:
 *
 *     movsx eax, byte ptr [edi + 0xc]    ; length
 *     lea   esi, [edi + 0xe]             ; the text
 *     dec   eax                          ; n = length - 1
 *     ...   call memmove                 ; shift it right by the affix
 *     movzx eax, byte ptr [edx + 0x18]
 *     add   byte ptr [edi + 0xc], al     ; length += affix, unchecked
 *
 * The record is 0x4c bytes with its text at +0xe, so there are 62 bytes behind
 * that byte -- and every prefix the morphology tries adds to it. Feed it a long
 * unbroken run of one letter ending in an affix the dictionary knows
 * ("the", "ing", "pre", "able") and the byte climbs past 127, reads back
 * negative, `dec` makes it worse, and memmove receives it unsigned: Brandon
 * measured a single call asking for **4,294,956,106 bytes** (issue #4), which
 * walks forward until it reaches an unmapped page.
 *
 * There is nothing to fix in that code -- it is Apple's, and the overflow is
 * inside it. What we can do is decline to carry out an instruction that cannot
 * be meant. A 32-bit process has no legitimate copy of a gigabyte, so a length
 * that large is not a copy, it is a symptom; performing it destroys the heap
 * and everything after it is noise.
 *
 * This is the `survive_divide_by_zero` bargain again: the engine is wrong, the
 * wrongness is survivable, and dying helps nobody. It is a containment, not a
 * cure -- a length between 63 and 127 still overruns one record into the next
 * without tripping this -- so it is counted and reported rather than absorbed
 * silently.
 */
#define COPY_SANITY_LIMIT 0x40000000u          /* 1 GB; see above */
static unsigned g_bad_copies;

static void refuse_copy(const char *what, unsigned n)
{
    g_bad_copies++;
    if (g_bad_copies <= 4)
        fprintf(stderr, "tiger_host: refused a %s of %u bytes -- the engine's "
                        "word length overflowed its byte (issue #4); the "
                        "word this came from will be wrong, the rest is "
                        "unaffected\n", what, n);
}

static void * __cdecl sh_memmove(void *dst, const void *src, unsigned n)
{
    if (n > COPY_SANITY_LIMIT) { refuse_copy("memmove", n); return dst; }
    return memmove(dst, src, n);
}
static void * __cdecl sh_memcpy(void *dst, const void *src, unsigned n)
{
    if (n > COPY_SANITY_LIMIT) { refuse_copy("memcpy", n); return dst; }
    return memcpy(dst, src, n);
}
