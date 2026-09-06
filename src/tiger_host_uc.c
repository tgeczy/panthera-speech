/* tiger_host_uc.c -- the Unicorn seam: run the i386 engine under emulation.
 *
 * Part of tiger_host.c, which includes it under -DTIGER_UC; see there for why
 * this is one translation unit.
 *
 * On Windows and native x86 Linux the host *calls* the engine directly, because
 * the process is x86 and so is the engine.  On ARM it can't, so the same engine
 * runs inside Unicorn (QEMU TCG) while the shims stay native ARM -- the hybrid
 * architecture measured in docs/android-phase0.md.  This file is the whole
 * difference between the two: a handful of seam points, guarded by TIGER_UC,
 * redirect to the routines here, and nothing else in the loader changes.
 *
 *   native call into guest        -> uc_call / uc_run_init  (uc_emu_start)
 *   native shim address in a slot -> a guest trampoline that traps to the shim
 *   VirtualAlloc'd image segment  -> the same memory, mapped into the guest
 *   malloc/calloc/realloc         -> a guest arena the engine can dereference
 *
 * The one invariant that makes the shims survive untouched: the guest address
 * space is *identical* to the host's.  Every region below is VirtualAlloc'd in
 * this 32-bit process and then uc_mem_map_ptr'd at its own address, so a pointer
 * the engine holds and a pointer a shim holds are the same number.  cfobj,
 * _RuneLocale, the fake FILEs -- all the state the two sides share -- keep their
 * layouts and their addresses.  This is why the build is 32-bit and not a
 * preference (see build_uc.sh).
 *
 * MacinTalk renders on its own MP worker task, and its ScheduledSoundPlayer
 * ticks a completion proc on the pacer thread, so the engine is driven from
 * more than one host thread.  One uc_engine cannot be shared across threads, so
 * each thread gets its own -- all mapping the *same* host memory, so the
 * trampolines, the arena and every image are common.  A per-thread engine is
 * cheap: it is a uc_engine, its own stack, and a replay of the shared mappings.
 */

#include <stdint.h>
#include <unicorn/unicorn.h>

/* ---- guest address map (host == guest, identity) ----------------------- *
 * One contiguous block is reserved with VirtualAlloc(NULL) in uc_host_init --
 * before any image loads -- and carved into the trampoline table and the arena.
 * A fixed base can collide with the process's own low memory, so the base is
 * chosen at runtime; identity still holds because it is uc_mem_map_ptr'd at its
 * own host address.  Guest stacks are allocated per-engine out of the arena. */
#define UC_TRAMP_SZ     0x00010000u   /* 64 KB = 4096 trampoline slots          */
#define UC_TRAMP_STRIDE 16u
#define UC_ARENA_SZ     0x04000000u   /* 64 MB: arena + every engine's stack.   */
                                      /* Alex's bank comes via sh_mmap, not here */
#define UC_STACK_SZ     0x00100000u   /* 1 MB per guest stack                   */
#define UC_BLOCK_SZ     (UC_TRAMP_SZ + UC_ARENA_SZ)
#define UC_RETMAGIC     0x00ff0000u   /* pushed as the return address; emu stops */

/* Shared region bases, resolved once the block is placed (see uc_host_init). */
static unsigned g_uc_tramp, g_uc_arena, g_uc_arena_end;

/* Per-thread: each host thread drives its OWN engine over the shared memory.
 * The MP worker and the pacer's completion proc are separate threads, and
 * sharing one engine across threads is a data race.  Only the uc_engine, its
 * stack and its re-entrancy flag are per thread; everything the engine touches
 * is common host memory. */
static __declspec(thread) uc_engine *t_uc;
static __declspec(thread) unsigned   t_stack_top;
static __declspec(thread) int        t_running;   /* guards non-nested re-entry */

/* Most shims forward i386 stack words and classify the return location.
 * Mixed-width clock calls also need an argument signature: ARM aligns their
 * 64-bit argument differently from the packed i386 stack. */
enum { RC_INT = 0, RC_VOID, RC_I64, RC_I64_I_I64, RC_DBL, RC_FLT };

typedef struct { void *fn; const char *name; unsigned char rc; unsigned char missing; }
        uc_slot;

/* Shared, and safe to share: the trampoline table is populated at load time on
 * one thread and only read during a render; the arena is a bump allocator under
 * a lock; the region list is replayed into each new engine under a lock. */
static uc_slot   g_slots[UC_TRAMP_SZ / UC_TRAMP_STRIDE];
static int       g_nslots;
static unsigned  g_arena_next;
static CRITICAL_SECTION g_arena_cs;
static struct { unsigned addr, size; } g_regions[512];
static int       g_nregions;
static uc_engine *g_engines[16];
static int       g_nengines;
static CRITICAL_SECTION g_map_cs;         /* guards g_regions and g_engines */
static int       g_uc_cs_ready;

static void die(const char *fmt, ...);   /* tiger_host.c */

static void uc_must(uc_err e, const char *what)
{
    if (e != UC_ERR_OK) die("unicorn: %s: %s", what, uc_strerror(e));
}

/* ---- the guest arena --------------------------------------------------- *
 * The engine's malloc, in memory the guest can address.  An 8-byte header per
 * block -- size, then the free-list link -- and the payload after it.  Because
 * the region is identity-mapped, the address handed back is also a valid host
 * pointer, so a shim can fill it directly.  Locked, because the worker and
 * pacer engines allocate concurrently.
 *
 * **free is real, and has to be.**  It was a no-op, on the reasoning that a
 * render has no teardown path and one utterance never approaches 64 MB.  The
 * first half is true and the second is beside the point: a *session* of
 * utterances does, because nothing was ever given back.  Alex allocates enough
 * per utterance to get there in about thirty seconds of reading, and what that
 * looks like is not a tidy out-of-memory error -- it is
 *
 *     tiger_host_uc: arena exhausted (+3984)
 *     Fatal signal 11 (SIGSEGV) ... fault addr 0x0 (write)
 *
 * in the same millisecond: malloc answers null, the engine does not ask, and
 * memcpy writes 3984 bytes to address zero.  The native host has always given
 * the engine a real malloc and free and the engine has always behaved on it,
 * so this is matching that rather than trusting it with something new.
 *
 * First fit over a free list, splitting a block that is much too big, and no
 * coalescing: the workload is the same handful of sizes utterance after
 * utterance, which a free list serves almost exactly and a bump allocator
 * cannot serve at all. */
#define ARENA_HDR   8u          /* [0]=payload size, [4]=next free block */
#define ARENA_SPLIT 64u         /* leftover worth making a block of its own */

/* The free list is kept in ADDRESS order, which is the only reason coalescing
 * is possible -- and coalescing is the difference between working and not.
 *
 * Measured without it, on the soak: 63 MB handed out, 52 MB of that already
 * back on the free list, and the allocation that killed the process was 89 KB.
 * There was four times the memory needed and not one piece of it big enough.
 * A free list without coalescing does not run out of memory, it runs out of
 * *shapes*, and the failure looks exactly like a leak from the outside. */
#define ARENA_SIZE(h)   (*(unsigned *)(uintptr_t)(h))
#define ARENA_NEXT(h)   (*(unsigned *)(uintptr_t)((h) + 4))
#define ARENA_END(h)    ((h) + ARENA_HDR + ARENA_SIZE(h))

static unsigned g_arena_free;   /* lowest free block's header, 0 = none */

/* Put a block back, in address order, merged with either neighbour it touches.
 * Caller holds the lock. */
static void arena_release(unsigned hdr)
{
    unsigned prev = 0, cur = g_arena_free;
    while (cur && cur < hdr) { prev = cur; cur = ARENA_NEXT(cur); }
    ARENA_NEXT(hdr) = cur;
    if (prev) ARENA_NEXT(prev) = hdr;
    else      g_arena_free = hdr;
    if (cur && ARENA_END(hdr) == cur) {          /* merge forward */
        ARENA_SIZE(hdr) += ARENA_HDR + ARENA_SIZE(cur);
        ARENA_NEXT(hdr)  = ARENA_NEXT(cur);
    }
    if (prev && ARENA_END(prev) == hdr) {        /* and backward */
        ARENA_SIZE(prev) += ARENA_HDR + ARENA_SIZE(hdr);
        ARENA_NEXT(prev)  = ARENA_NEXT(hdr);
    }
}

static void *arena_alloc(size_t n)
{
    unsigned hdr, p, cur, prev = 0;
    void *ret = NULL;
    n = (n + 7u) & ~(size_t)7u;
    if (g_uc_cs_ready) EnterCriticalSection(&g_arena_cs);
    for (cur = g_arena_free; cur; ) {
        unsigned sz   = ARENA_SIZE(cur);
        unsigned next = ARENA_NEXT(cur);
        if (sz >= (unsigned)n) {
            /* Split when the leftover is worth a header of its own.  The tail
             * sits immediately after this block and before `next`, so it takes
             * this block's place in the list and the ordering still holds. */
            if (sz >= (unsigned)n + ARENA_HDR + ARENA_SPLIT) {
                unsigned tail = cur + ARENA_HDR + (unsigned)n;
                ARENA_SIZE(tail) = sz - (unsigned)n - ARENA_HDR;
                ARENA_NEXT(tail) = next;
                ARENA_SIZE(cur)  = (unsigned)n;
                next = tail;
            }
            if (prev) ARENA_NEXT(prev) = next;
            else      g_arena_free = next;
            ret = (void *)(uintptr_t)(cur + ARENA_HDR);
            break;
        }
        prev = cur;
        cur  = next;
    }
    if (!ret && (size_t)g_arena_next + ARENA_HDR + n <= g_uc_arena_end) {
        hdr = g_arena_next;
        p   = hdr + ARENA_HDR;
        *(unsigned *)(uintptr_t)hdr = (unsigned)n;
        g_arena_next = p + (unsigned)n;
        ret = (void *)(uintptr_t)p;
    }
    if (g_uc_cs_ready) LeaveCriticalSection(&g_arena_cs);
    if (!ret) fprintf(stderr, "tiger_host_uc: arena exhausted (+%u)\n",
                      (unsigned)n);
    return ret;
}

/* How much of the arena has ever been handed out, and how much is on the free
 * list waiting to be handed out again.  Reported per utterance: "still 8 MB and
 * steady" and "climbing 2 MB an utterance" are the same picture from a single
 * render and completely different problems. */
static void arena_usage(unsigned *bumped, unsigned *freed)
{
    unsigned cur, total = 0;
    if (g_uc_cs_ready) EnterCriticalSection(&g_arena_cs);
    for (cur = g_arena_free; cur; cur = *(unsigned *)(uintptr_t)(cur + 4))
        total += *(unsigned *)(uintptr_t)cur + ARENA_HDR;
    *bumped = g_arena_next - g_uc_arena;
    *freed  = total;
    if (g_uc_cs_ready) LeaveCriticalSection(&g_arena_cs);
}

static void * __cdecl sh_uc_malloc(size_t n)          { return arena_alloc(n); }
static void   __cdecl sh_uc_free(void *p)
{
    unsigned a = (unsigned)(uintptr_t)p, hdr;
    /* Only ours.  The engine frees pointers this allocator never handed out --
     * anything a shim returned from host memory, for one -- and threading those
     * onto the free list would hand the engine a block of somebody else's. */
    if (!p || a < g_uc_arena + ARENA_HDR || a >= g_uc_arena_end) return;
    hdr = a - ARENA_HDR;
    if (g_uc_cs_ready) EnterCriticalSection(&g_arena_cs);
    arena_release(hdr);
    if (g_uc_cs_ready) LeaveCriticalSection(&g_arena_cs);
}
static void * __cdecl sh_uc_calloc(size_t a, size_t b)
{
    size_t n = a * b;
    void *p = arena_alloc(n);
    if (p) memset(p, 0, n);
    return p;
}
static void * __cdecl sh_uc_realloc(void *old, size_t n)
{
    void *p;
    unsigned oldn;
    if (!old) return arena_alloc(n);
    oldn = *(unsigned *)(uintptr_t)((unsigned)(uintptr_t)old - ARENA_HDR);
    if (oldn >= n) return old;          /* already big enough; keep the block */
    p = arena_alloc(n);
    if (!p) return NULL;                /* the old block is still the caller's */
    if (oldn) memcpy(p, old, oldn);
    sh_uc_free(old);                    /* growing a buffer must not leak it */
    return p;
}

/* ---- data symbols the guest reads directly ----------------------------- *
 * A handful of imports are *data*, not code: their slot holds the address of a
 * variable the engine loads through.  The native shim table points those at
 * host .data, which the guest cannot see, so each is copied into the arena once
 * and every slot that names it gets the one arena address.  ___stack_chk_guard
 * is the hot one -- every stack-protected function in the engine reads it. */
static struct { const char *name; unsigned size; void *host; void *guest; }
        g_data_syms[] = {
    { "___stack_chk_guard", 4, NULL, NULL },
    { NULL, 0, NULL, NULL }
};

static void *uc_data_copy(const char *nm, void *hostptr, unsigned size)
{
    int i;
    for (i = 0; g_data_syms[i].name; i++) {
        if (strcmp(g_data_syms[i].name, nm)) continue;
        if (!g_data_syms[i].guest) {
            void *g = arena_alloc(size);
            if (g) memcpy(g, hostptr, size);
            g_data_syms[i].host  = hostptr;
            g_data_syms[i].guest = g;
        }
        return g_data_syms[i].guest;
    }
    return NULL;
}
static unsigned uc_data_size(const char *nm)
{
    int i;
    for (i = 0; g_data_syms[i].name; i++)
        if (!strcmp(g_data_syms[i].name, nm)) return g_data_syms[i].size;
    return 0;
}

/* ---- the return class of each shim ------------------------------------- *
 * Default is RC_INT (EAX): every pointer- and integer-returning libc, CF, MP,
 * Audio and SpeechDictionary-glue shim, which is nearly all of them.  Only the
 * floating-point math and the 64-bit AbsoluteTime clock need naming. */
static unsigned char uc_rc_for(const char *nm)
{
    /* double f(...) -- result in ST(0) */
    static const char *dbl[] = { "_sin","_cos","_pow","_sqrt","_log","_log10",
        "_floor","_ceil","_exp2","_sinh", NULL };
    /* float f(...) -- also ST(0); captured as float, widened for the hand-off */
    static const char *flt[] = { "_sinf","_cosf","_floorf","_ceilf","_expf",
        "_logf","_powf","_exp2f","_log2f", NULL };
    /* uint64 f(...) -- result in EDX:EAX */
    static const char *i64[] = { "_UpTime","___divdi3","___udivdi3","___moddi3",
        "___umoddi3", NULL };
    int i;
    /* These return EDX:EAX and their i386 arguments are packed as i32,i64.
     * ARM AAPCS aligns the i64 to an even register pair, so forwarding three
     * untyped words corrupts the timestamp as well as losing its high return. */
    if (!strcmp(nm, "_AddDurationToAbsolute") ||
        !strcmp(nm, "_SubDurationFromAbsolute")) return RC_I64_I_I64;
    for (i = 0; dbl[i]; i++) if (!strcmp(dbl[i], nm)) return RC_DBL;
    for (i = 0; flt[i]; i++) if (!strcmp(flt[i], nm)) return RC_FLT;
    for (i = 0; i64[i]; i++) if (!strcmp(i64[i], nm)) return RC_I64;
    return RC_INT;
}

/* ---- trampolines ------------------------------------------------------- *
 * bind() writes the guest address of a slot into the engine's import pointer.
 * When the engine calls it, the slot's first byte (a nop) is where the code
 * hook fires and does the real work; the slot's own `ret` (cdecl caller-cleans,
 * so bare) then returns to the engine.  An FP slot additionally holds
 * `fld qword [esp-8]`, executed after the hook has written the result just below
 * the guest's own stack top -- per-engine, so two engines never race on it, and
 * avoiding Unicorn's fiddly x87 register API.  The slot bytes live in the shared
 * block, so writing them through any one engine reaches every engine. */
static void uc_write_slot(int idx, int fp)
{
    unsigned char b[UC_TRAMP_STRIDE];
    unsigned at = g_uc_tramp + (unsigned)idx * UC_TRAMP_STRIDE;
    memset(b, 0x90, sizeof b);           /* nop fill; only the first is caught */
    if (fp) {
        b[1] = 0xdd; b[2] = 0x44; b[3] = 0x24; b[4] = 0xf8; /* fld qword [esp-8] */
        b[5] = 0xc3;                     /* ret */
    } else {
        b[1] = 0xc3;                     /* ret */
    }
    uc_must(uc_mem_write(t_uc, at, b, sizeof b), "write trampoline");
}

static void *uc_tramp_make(void *fn, const char *nm, unsigned char rc, int missing)
{
    int idx = g_nslots++;
    if ((unsigned)idx >= UC_TRAMP_SZ / UC_TRAMP_STRIDE)
        die("too many trampolines");
    g_slots[idx].fn      = fn;
    g_slots[idx].name    = nm;
    g_slots[idx].rc      = rc;
    g_slots[idx].missing = (unsigned char)missing;
    uc_write_slot(idx, rc == RC_DBL || rc == RC_FLT);
    return (void *)(uintptr_t)(g_uc_tramp + (unsigned)idx * UC_TRAMP_STRIDE);
}

/* The seam bind() calls in place of `*slot = fn` for a host shim: a data symbol
 * becomes its arena copy, a function becomes a trampoline. */
static void *uc_bind_target(const char *nm, void *fn)
{
    unsigned dsz = uc_data_size(nm);
    if (dsz) {
        void *g = uc_data_copy(nm, fn, dsz);
        return g ? g : fn;
    }
    return uc_tramp_make(fn, nm, uc_rc_for(nm), 0);
}

/* The seam make_thunk() returns under TIGER_UC: a trampoline that records the
 * unresolved name and answers 0, exactly as the native thunk did. */
static void *uc_missing_tramp(const char *nm)
{
    return uc_tramp_make(NULL, nm, RC_INT, 1);
}

/* ---- the dispatch hook ------------------------------------------------- */
typedef unsigned (__cdecl *fn_i)(unsigned,unsigned,unsigned,unsigned,unsigned,
    unsigned,unsigned,unsigned,unsigned,unsigned,unsigned,unsigned);
typedef void     (__cdecl *fn_v)(unsigned,unsigned,unsigned,unsigned,unsigned,
    unsigned,unsigned,unsigned,unsigned,unsigned,unsigned,unsigned);
typedef unsigned long long (__cdecl *fn_q)(unsigned,unsigned,unsigned,unsigned,
    unsigned,unsigned,unsigned,unsigned,unsigned,unsigned,unsigned,unsigned);
typedef double   (__cdecl *fn_d)(unsigned,unsigned,unsigned,unsigned,unsigned,
    unsigned,unsigned,unsigned,unsigned,unsigned,unsigned,unsigned);
typedef float    (__cdecl *fn_f)(unsigned,unsigned,unsigned,unsigned,unsigned,
    unsigned,unsigned,unsigned,unsigned,unsigned,unsigned,unsigned);

#define UC_ARGS a[0],a[1],a[2],a[3],a[4],a[5],a[6],a[7],a[8],a[9],a[10],a[11]

static int g_uc_missing_hits;   /* how many missing-shim calls, this session */
static const char *g_uc_last_shim;   /* name of the last shim dispatched (debug) */

/* Fires on whichever engine `u` executed the trampoline -- always the current
 * thread's engine, since an engine only ever runs on its own thread.  Reads the
 * cdecl args off that engine's stack, calls the native shim, and returns the
 * result the way its class requires. */
static void uc_dispatch(uc_engine *u, uint64_t address, uint32_t size,
                        void *user)
{
    unsigned off = (unsigned)address - g_uc_tramp;
    unsigned esp, a[12];
    uc_slot *s;
    (void)size; (void)user;
    if (off % UC_TRAMP_STRIDE) return;         /* mid-slot fld/ret; let it run */
    s = &g_slots[off / UC_TRAMP_STRIDE];

    uc_reg_read(u, UC_X86_REG_ESP, &esp);
    uc_mem_read(u, esp + 4, a, sizeof a);      /* args: cdecl, above the retaddr */

    g_uc_last_shim = s->name;
    if (s->missing) {
        unsigned zero = 0;
        g_uc_missing_hits++;
        if (g_verbose)
            fprintf(stderr, "  [uc] missing shim called: %s -> 0\n", s->name);
        uc_reg_write(u, UC_X86_REG_EAX, &zero);
        return;
    }
    switch (s->rc) {
    case RC_VOID: ((fn_v)s->fn)(UC_ARGS); break;
    case RC_INT: {
        unsigned r = ((fn_i)s->fn)(UC_ARGS);
        uc_reg_write(u, UC_X86_REG_EAX, &r);
        break; }
    case RC_I64_I_I64: {
        long long absolute = (long long)((unsigned long long)a[1] |
                                         ((unsigned long long)a[2] << 32));
        long long q = ((long long (__cdecl *)(int, long long))s->fn)((int)a[0], absolute);
        unsigned lo = (unsigned)q, hi = (unsigned)((unsigned long long)q >> 32);
        uc_reg_write(u, UC_X86_REG_EAX, &lo);
        uc_reg_write(u, UC_X86_REG_EDX, &hi);
        break; }
    case RC_I64: {
        unsigned long long q = ((fn_q)s->fn)(UC_ARGS);
        unsigned lo = (unsigned)q, hi = (unsigned)(q >> 32);
        uc_reg_write(u, UC_X86_REG_EAX, &lo);
        uc_reg_write(u, UC_X86_REG_EDX, &hi);
        break; }
    case RC_DBL: {
        double d = ((fn_d)s->fn)(UC_ARGS);
        uc_mem_write(u, esp - 8, &d, 8);       /* the slot's `fld qword [esp-8]` */
        break; }
    case RC_FLT: {
        double d = (double)((fn_f)s->fn)(UC_ARGS);
        uc_mem_write(u, esp - 8, &d, 8);
        break; }
    }
}
#undef UC_ARGS

/* An invalid-access hook: name the faulting address, so a fault says what it
 * touched instead of only where the code was.  Returns false -- the access
 * still fails; this only reports it. */
/* Defined later in tiger_host_fault.c (same TU); names a guest address as
 * image+offset, or the nearest preceding symbol. */
static const char *engine_symbol(void *addr);

static bool uc_on_badmem(uc_engine *u, uc_mem_type type, uint64_t address,
                         int size, int64_t value, void *user)
{
    unsigned eip = 0;
    const char *k = type == UC_MEM_READ_UNMAPPED  ? "read"  :
                    type == UC_MEM_WRITE_UNMAPPED ? "write" :
                    type == UC_MEM_FETCH_UNMAPPED ? "fetch" : "prot";
    (void)user;
    uc_reg_read(u, UC_X86_REG_EIP, &eip);
    fprintf(stderr, "  [uc] BAD %s addr=%08x size=%d value=%08x eip=%08x  %s\n",
            k, (unsigned)address, size, (unsigned)value, eip,
            engine_symbol((void *)(uintptr_t)eip));
    {   unsigned r[8]; static const char *nm[8] =
            { "eax","ebx","ecx","edx","esi","edi","ebp","esp" };
        int reg[8] = { UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ECX,
                       UC_X86_REG_EDX, UC_X86_REG_ESI, UC_X86_REG_EDI,
                       UC_X86_REG_EBP, UC_X86_REG_ESP };
        int i;
        for (i = 0; i < 8; i++) uc_reg_read(u, reg[i], &r[i]);
        fprintf(stderr, "  [uc]   %s=%08x %s=%08x %s=%08x %s=%08x\n"
                        "  [uc]   %s=%08x %s=%08x %s=%08x %s=%08x\n",
                nm[0], r[0], nm[1], r[1], nm[2], r[2], nm[3], r[3],
                nm[4], r[4], nm[5], r[5], nm[6], r[6], nm[7], r[7]);
    }
    return false;
}

/* Debug: remember the last basic blocks, to place a host crash.  Off unless
 * TIGER_UC_TRACE is set (it runs on every block). */
static unsigned g_uc_block_ring[8];
static int      g_uc_block_i;

static void uc_trace_block(uc_engine *u, uint64_t address, uint32_t size,
                           void *user)
{
    (void)u; (void)size; (void)user;
    g_uc_block_ring[g_uc_block_i++ & 7] = (unsigned)address;
}

/* A report-only host exception handler.  A genuine host crash (a shim
 * dereferencing something bad, or TCG itself) never reaches uc_emu_start's
 * error return, so without this it is a silent SIGSEGV.  This prints the fault
 * and returns CONTINUE_SEARCH, so Unicorn's own handler still gets recoverable
 * guest faults -- it only observes, never intercepts.
 *
 * Windows-only: it is a vectored handler reading an i386 CONTEXT.  On Android a
 * host sigaction would fight Unicorn's own signal handler, so none is installed
 * and a host crash is left to the default disposition. */
#ifdef _WIN32
static LONG CALLBACK uc_veh(EXCEPTION_POINTERS *ep)
{
    EXCEPTION_RECORD *er = ep->ExceptionRecord;
    if (er->ExceptionCode == EXCEPTION_ACCESS_VIOLATION) {
        int i;
        fprintf(stderr, "  [uc] HOST AV eip=%08x %s addr=%08x\n",
                (unsigned)ep->ContextRecord->Eip,
                er->ExceptionInformation[0] ? "write" : "read",
                (unsigned)er->ExceptionInformation[1]);
        fprintf(stderr, "  [uc] last guest blocks (oldest first):");
        for (i = 0; i < 8; i++)
            fprintf(stderr, " %08x", g_uc_block_ring[(g_uc_block_i + i) & 7]);
        fprintf(stderr, "\n  [uc] last shim dispatched: %s\n",
                g_uc_last_shim ? g_uc_last_shim : "(none)");
    }
    return EXCEPTION_CONTINUE_SEARCH;
}
#endif /* _WIN32 */

/* ---- engines and mapping ---------------------------------------------- */

/* Every guest region is recorded here and replayed into each new engine, so a
 * worker created after the images loaded still sees them.  Identity mapping
 * makes the replay trivial: the host pointer is the guest address.  Called on
 * whatever thread does the mapping (sh_mmap may run on a worker), so it locks. */
static void uc_add_region(unsigned addr, unsigned size)
{
    int i;
    if (!size) return;
    EnterCriticalSection(&g_map_cs);
    if (g_nregions < (int)(sizeof g_regions / sizeof g_regions[0])) {
        g_regions[g_nregions].addr = addr;
        g_regions[g_nregions].size = size;
        g_nregions++;
    } else die("too many guest regions");
    for (i = 0; i < g_nengines; i++)
        uc_must(uc_mem_map_ptr(g_engines[i], addr, size, UC_PROT_ALL,
                (void *)(uintptr_t)addr), "map region into engine");
    LeaveCriticalSection(&g_map_cs);
}

/* map_image: give the guest the very same bytes at the very same address. */
static void uc_map_segment(void *at, unsigned vmsize)
{
    uc_add_region((unsigned)(uintptr_t)at, (vmsize + 0xfffu) & ~0xfffu);
}

/* sh_mmap: the engine memory-maps its dictionary and voice files, and the view
 * (64 KB-aligned) has to be visible to the guest that reads it.  This is the
 * seam that carries Alex's demand-paged bank on a device -- TCG faults the
 * pages in transparently. */
static void uc_map_extern(void *base, unsigned len)
{
    uc_add_region((unsigned)(uintptr_t)base, (len + 0xfffu) & ~0xfffu);
}
static void uc_unmap_extern(void *base, unsigned len)
{
    unsigned addr = (unsigned)(uintptr_t)base, sz = (len + 0xfffu) & ~0xfffu;
    int i, j;
    if (!sz) return;
    EnterCriticalSection(&g_map_cs);
    for (i = 0; i < g_nengines; i++)
        uc_mem_unmap(g_engines[i], addr, sz);
    for (j = 0; j < g_nregions; j++)
        if (g_regions[j].addr == addr) { g_regions[j] = g_regions[--g_nregions]; break; }
    LeaveCriticalSection(&g_map_cs);
}

/* A fresh engine for the current thread, mapping the same host memory as every
 * other and carrying the same trampolines.  The region replay and the engine
 * registration happen under one lock so a region added concurrently either is
 * replayed here or maps into this engine there, never neither. */
static uc_engine *uc_new_engine(void)
{
    uc_engine *u;
    uc_hook hd, hm, hb;
    int i;
    uc_must(uc_open(UC_ARCH_X86, UC_MODE_32, &u), "uc_open");
    EnterCriticalSection(&g_map_cs);
    for (i = 0; i < g_nregions; i++)
        uc_must(uc_mem_map_ptr(u, g_regions[i].addr, g_regions[i].size,
                UC_PROT_ALL, (void *)(uintptr_t)g_regions[i].addr),
                "replay region");
    if (g_nengines < (int)(sizeof g_engines / sizeof g_engines[0]))
        g_engines[g_nengines++] = u;
    else die("too many engines");
    LeaveCriticalSection(&g_map_cs);
    uc_must(uc_hook_add(u, &hd, UC_HOOK_CODE, (void *)uc_dispatch, NULL,
            g_uc_tramp, g_uc_tramp + UC_TRAMP_SZ - 1), "dispatch hook");
    uc_hook_add(u, &hm, UC_HOOK_MEM_READ_UNMAPPED | UC_HOOK_MEM_WRITE_UNMAPPED |
            UC_HOOK_MEM_FETCH_UNMAPPED, (void *)uc_on_badmem, NULL, 1, 0);
    if (getenv("TIGER_UC_TRACE"))
        uc_hook_add(u, &hb, UC_HOOK_BLOCK, (void *)uc_trace_block, NULL, 1, 0);
    return u;
}

/* This thread's engine, created on first use with its own stack.  The pacer
 * thread reaches this the first time the completion proc calls back into the
 * guest -- no special case needed. */
static void uc_ensure_engine(void)
{
    void *stk;
    if (t_uc) return;
    t_uc = uc_new_engine();
    stk = arena_alloc(UC_STACK_SZ);
    if (!stk) die("no arena for a guest stack");
    t_stack_top = ((unsigned)(uintptr_t)stk + UC_STACK_SZ) & ~15u;
}

/* Call a guest function as if from a `call` that pushed UC_RETMAGIC, and stop
 * when it returns there.  Darwin i386 wants ESP 16-aligned at the call, i.e.
 * ESP % 16 == 12 at the callee's first instruction; the base is chosen for it.
 * Returns EAX (callers wanting a wider result read the registers themselves). */
static int uc_call(void *fn, int argc, const unsigned *argv)
{
    unsigned base, sp, magic = UC_RETMAGIC, eax = 0;
    uc_err e;
    uc_ensure_engine();
    if (t_running)
        die("uc_call re-entered on one thread's engine without nesting -- a "
            "shim called back into the guest via the wrong path");
    base = (t_stack_top - 256u) & ~15u;
    sp   = base - 4u;
    if (argc > 0)
        uc_must(uc_mem_write(t_uc, base, argv, (size_t)argc * 4), "push args");
    uc_must(uc_mem_write(t_uc, sp, &magic, 4), "push retaddr");
    uc_must(uc_reg_write(t_uc, UC_X86_REG_ESP, &sp), "set esp");

    t_running = 1;
    e = uc_emu_start(t_uc, (uint64_t)(uintptr_t)fn, UC_RETMAGIC, 0, 0);
    t_running = 0;
    if (e != UC_ERR_OK) {
        unsigned eip = 0;
        uc_reg_read(t_uc, UC_X86_REG_EIP, &eip);
        /* Name the last shim that ran, because a guest fault is very often the
         * *previous* shim's fault: something marshalled correctly for an i386
         * host and not for an ARM one hands back a plausible-looking zero, and
         * the engine dereferences it a few instructions later.  That is exactly
         * how the 64-bit clock bug read, and how Leopard's worker reads now.
         *
         * Trust the addresses over the names either side of it: engine_symbol
         * takes the nearest preceding export and will cheerfully name something
         * forty kilobytes away, or something that is not the engine's at all. */
        fprintf(stderr, "tiger_host: guest fault: %s\n"
                        "  at eip=%08x  %s\n  entry %08x  %s\n"
                        "  last shim dispatched: %s\n",
                uc_strerror(e), eip, engine_symbol((void *)(uintptr_t)eip),
                (unsigned)(uintptr_t)fn, engine_symbol(fn),
                g_uc_last_shim ? g_uc_last_shim : "(none)");
        die("guest fault: %s at eip=%08x (entry %08x)",
            uc_strerror(e), eip, (unsigned)(uintptr_t)fn);
    }
    uc_reg_read(t_uc, UC_X86_REG_EAX, &eax);
    return (int)eax;
}

/* run_initializers' replacement for `((void(__cdecl*)(void))fn)()`. */
static void uc_run_init(void *fn) { uc_call(fn, 0, NULL); }

/* A SYNCHRONOUS host->guest callback from inside a shim -- pthread_once's init
 * routine is the first -- which must run on the guest now, while the outer
 * uc_emu_start is suspended in the dispatch hook.  The full CPU context is saved
 * and restored around a re-entrant uc_emu_start on this thread's engine; the
 * callback runs on a fresh frame just below the current guest stack (a normal
 * nested call would), so the suspended outer frames are untouched.  This is the
 * synchronous, same-thread cousin of the MP worker, which instead gets its own
 * engine on its own thread. */
static int uc_call_nested_args(void *fn, int argc, const unsigned *argv)
{
    uc_context *ctx;
    unsigned esp, base, sp, magic = UC_RETMAGIC, eax = 0;
    uc_err e;
    uc_must(uc_context_alloc(t_uc, &ctx), "context alloc");
    uc_must(uc_context_save(t_uc, ctx), "context save");
    uc_reg_read(t_uc, UC_X86_REG_ESP, &esp);
    base = (esp - 512u) & ~15u;                  /* 16-aligned at the call */
    sp   = base - 4u;
    if (argc > 0)
        uc_must(uc_mem_write(t_uc, base, argv, (size_t)argc * 4), "nested args");
    uc_must(uc_mem_write(t_uc, sp, &magic, 4), "nested retaddr");
    uc_must(uc_reg_write(t_uc, UC_X86_REG_ESP, &sp), "nested esp");
    e = uc_emu_start(t_uc, (uint64_t)(uintptr_t)fn, UC_RETMAGIC, 0, 0);
    if (e == UC_ERR_OK) uc_reg_read(t_uc, UC_X86_REG_EAX, &eax);
    uc_must(uc_context_restore(t_uc, ctx), "context restore");
    uc_context_free(ctx);
    if (e != UC_ERR_OK) {
        unsigned eip = 0;
        uc_reg_read(t_uc, UC_X86_REG_EIP, &eip);
        die("nested guest fault: %s at eip=%08x (entry %08x)",
            uc_strerror(e), eip, (unsigned)(uintptr_t)fn);
    }
    return (int)eax;
}

static void uc_call_nested(void *fn) { (void)uc_call_nested_args(fn, 0, NULL); }

/* The audio callbacks the engine hands us, called back from inside a shim and
 * therefore always nested: the Sound Manager's fill proc (Vicki) and the
 * AudioConverter's input proc (Alex).  uc_call would refuse these -- it is for
 * entering the guest from a thread that is not already in it. */
static int uc_cb2(void *fn, void *a, void *b)
{ unsigned v[2]; v[0]=(unsigned)(uintptr_t)a; v[1]=(unsigned)(uintptr_t)b;
  return uc_call_nested_args(fn, 2, v); }
static int uc_cb5(void *fn, void *a, void *b, void *c, void *d, void *e)
{ unsigned v[5]; v[0]=(unsigned)(uintptr_t)a; v[1]=(unsigned)(uintptr_t)b;
  v[2]=(unsigned)(uintptr_t)c; v[3]=(unsigned)(uintptr_t)d;
  v[4]=(unsigned)(uintptr_t)e; return uc_call_nested_args(fn, 5, v); }

/* ---- argument marshalling ---------------------------------------------- *
 * The engine dereferences the pointers it is handed, so a host buffer passed to
 * it has to have a guest home.  The arena is identity-mapped and never freed, so
 * a guest slot is also a valid host pointer -- an out-parameter can just be read
 * back with `*slot` after the call, no copy-back list.  The call sites use these
 * through the UC_IN/UC_OUT macros (tiger_host.c), which are identities in the
 * native build so each site is written once for both.  Direction and size are
 * known at the site, which is exactly what a general auto-bounce would have to
 * guess -- and guessing an integer for a pointer would corrupt host state. */
static void *uc_in(const void *host, size_t n)
{ void *g = arena_alloc(n); if (g) memcpy(g, host, n); return g; }
static void *uc_in_str(const char *s, size_t n)
{ char *g = (char *)arena_alloc(n + 1); if (g) { memcpy(g, s, n); g[n] = 0; } return g; }
static void *uc_out(size_t n)
{ void *g = arena_alloc(n); if (g) memset(g, 0, n); return g; }

/* call_aligned1..4's replacements (tiger_host_shims.c routes to these). */
static int uc_call1(void *fn, void *a)
{ unsigned v[1]; v[0]=(unsigned)(uintptr_t)a; return uc_call(fn,1,v); }
static int uc_call2(void *fn, void *a, void *b)
{ unsigned v[2]; v[0]=(unsigned)(uintptr_t)a; v[1]=(unsigned)(uintptr_t)b;
  return uc_call(fn,2,v); }
static int uc_call3(void *fn, void *a, void *b, void *c)
{ unsigned v[3]; v[0]=(unsigned)(uintptr_t)a; v[1]=(unsigned)(uintptr_t)b;
  v[2]=(unsigned)(uintptr_t)c; return uc_call(fn,3,v); }
static int uc_call4(void *fn, void *a, void *b, void *c, void *d)
{ unsigned v[4]; v[0]=(unsigned)(uintptr_t)a; v[1]=(unsigned)(uintptr_t)b;
  v[2]=(unsigned)(uintptr_t)c; v[3]=(unsigned)(uintptr_t)d;
  return uc_call(fn,4,v); }
/* Five, for the AudioConverter input callback -- the one Alex's decode runs
 * through.  It is a host->guest call like the others, and had been a plain C
 * call: correct on an i386 host, and anywhere else a jump into i386 bytes as
 * though they were the host's own instructions. */
static int uc_call5(void *fn, void *a, void *b, void *c, void *d, void *e)
{ unsigned v[5]; v[0]=(unsigned)(uintptr_t)a; v[1]=(unsigned)(uintptr_t)b;
  v[2]=(unsigned)(uintptr_t)c; v[3]=(unsigned)(uintptr_t)d;
  v[4]=(unsigned)(uintptr_t)e; return uc_call(fn,5,v); }

/* ---- bring-up ---------------------------------------------------------- *
 * Reserve one contiguous block for the trampolines and the arena, in this
 * process, before a single image loads, and register it so the main engine (and
 * every later one) maps it.  The main engine is created here; the worker and
 * pacer engines create themselves lazily on their own threads. */
static void uc_host_init(void)
{
    unsigned char *blk;
    unsigned base;

    InitializeCriticalSection(&g_arena_cs);
    InitializeCriticalSection(&g_map_cs);
    g_uc_cs_ready = 1;

    blk = (unsigned char *)VirtualAlloc(NULL, UC_BLOCK_SZ,
                                        MEM_RESERVE | MEM_COMMIT,
                                        PAGE_EXECUTE_READWRITE);
    if (!blk) die("cannot reserve %u bytes for the guest regions", UC_BLOCK_SZ);
    base = (unsigned)(uintptr_t)blk;

    g_uc_tramp     = base;
    g_uc_arena     = base + UC_TRAMP_SZ;
    g_uc_arena_end = g_uc_arena + UC_ARENA_SZ;
    g_arena_next   = g_uc_arena;

#ifdef _WIN32
    AddVectoredExceptionHandler(1, uc_veh);   /* process-wide; report-only */
#endif
    uc_add_region(base, UC_BLOCK_SZ);         /* records; no engine yet */
    uc_ensure_engine();                       /* the main thread's engine */

    if (g_verbose)
        fprintf(stderr, "tiger_host_uc: Unicorn x86-32 up; block at %08x, "
                        "arena %u MB at %08x, trampolines at %08x\n",
                base, UC_ARENA_SZ >> 20, g_uc_arena, g_uc_tramp);
}
