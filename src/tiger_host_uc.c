/* tiger_host_uc.c -- the Unicorn seam: run the i386 engine under emulation.
 *
 * Part of tiger_host.c, which includes it under -DTIGER_UC; see there for why
 * this is one translation unit.
 *
 * On Windows and native x86 Linux the host *calls* the engine directly, because
 * the process is x86 and so is the engine.  On ARM it can't, so the same engine
 * runs inside Unicorn (QEMU TCG) while the shims stay native ARM -- the hybrid
 * architecture measured in docs/android-phase0.md.  This file is the whole
 * difference between the two: six seam points, guarded by TIGER_UC, redirect to
 * the routines here, and nothing else in the loader changes.
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
 */

#include <stdint.h>
#include <unicorn/unicorn.h>

/* ---- guest address map (host == guest, identity) ----------------------- *
 * One contiguous block is reserved with VirtualAlloc(NULL) in uc_host_init --
 * before any image loads -- and carved into these regions.  A fixed base can
 * collide with the process's own low memory, so the base is chosen at runtime
 * and the region addresses are offsets from it; identity still holds because
 * each is uc_mem_map_ptr'd at its own host address.  A later image slide
 * (MacinTalk is based at 0) gets a different VirtualAlloc(NULL) region, and
 * SpeechDictionary's prebound 0x96d0c000 is far above this block. */
#define UC_SCRATCH_SZ 0x00001000u   /* one page; 8 bytes used for FP returns    */
#define UC_TRAMP_SZ   0x00010000u   /* 64 KB = 4096 trampoline slots            */
#define UC_TRAMP_STRIDE 16u
#define UC_STACK_SZ   0x00200000u   /* 2 MB guest stack                         */
#define UC_ARENA_SZ   0x04000000u   /* 64 MB.  Fred needs little; Alex's bank   */
                                    /* comes in through sh_mmap, not here.      */
#define UC_BLOCK_SZ   (UC_SCRATCH_SZ + UC_TRAMP_SZ + UC_STACK_SZ + UC_ARENA_SZ)
#define UC_RETMAGIC   0x00ff0000u   /* pushed as the return address; emu stops  */

/* Region bases, resolved once the block is placed (see uc_host_init). */
static unsigned g_uc_scratch, g_uc_tramp, g_uc_stack_top, g_uc_arena, g_uc_arena_end;

/* Return class of a shim.  Arguments never need a class: i386 cdecl passes
 * everything on the stack as words, so the trampoline copies stack words
 * verbatim whatever the types are.  Only where the *result* lands differs --
 * EAX, EDX:EAX, or ST(0) -- and that is all this encodes. */
enum { RC_INT = 0, RC_VOID, RC_I64, RC_DBL, RC_FLT };

typedef struct { void *fn; const char *name; unsigned char rc; unsigned char missing; }
        uc_slot;

static uc_engine *g_uc;
static uc_hook    g_uc_hook;
/* MP worker tasks recorded but not spawned (see sh_mp_create_task): one
 * uc_engine can't be driven from two host threads.  void* because the mptask
 * type is defined later in the translation unit. */
static void      *g_uc_mp_tasks[8];
static int        g_uc_mp_ntasks;
static uc_slot    g_slots[UC_TRAMP_SZ / UC_TRAMP_STRIDE];
static int        g_nslots;
static int        g_uc_running;      /* guards against nesting uc_emu_start */
static unsigned   g_arena_next;      /* next free guest byte (== host byte) */

static void die(const char *fmt, ...);   /* tiger_host.c */

static void uc_must(uc_err e, const char *what)
{
    if (e != UC_ERR_OK) die("unicorn: %s: %s", what, uc_strerror(e));
}

/* ---- the guest arena --------------------------------------------------- *
 * A bump allocator with an 8-byte size header, so realloc knows the old size.
 * free is a no-op: a render has no teardown path here any more than the native
 * host does (see host_open), and one utterance never approaches 64 MB.  Because
 * the region is identity-mapped, the returned guest address is also a valid
 * host pointer, so a shim can fill it directly. */
static void *arena_alloc(size_t n)
{
    unsigned hdr, p;
    n = (n + 7u) & ~(size_t)7u;
    if ((size_t)g_arena_next + 8 + n > g_uc_arena_end) {
        fprintf(stderr, "tiger_host_uc: arena exhausted (+%u)\n", (unsigned)n);
        return NULL;
    }
    hdr = g_arena_next;
    p   = hdr + 8;
    *(unsigned *)(uintptr_t)hdr = (unsigned)n;
    g_arena_next = p + (unsigned)n;
    return (void *)(uintptr_t)p;
}

static void * __cdecl sh_uc_malloc(size_t n)          { return arena_alloc(n); }
static void   __cdecl sh_uc_free(void *p)             { (void)p; }
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
    oldn = *(unsigned *)(uintptr_t)((unsigned)(uintptr_t)old - 8);
    p = arena_alloc(n);
    if (p && oldn) memcpy(p, old, oldn < n ? oldn : n);
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
 * `fld qword [SCRATCH]`, executed after the hook has written the result there,
 * because writing ST(0) through Unicorn's register API is the fiddly path the
 * hook avoids. */
static void uc_write_slot(int idx, int fp)
{
    unsigned char b[UC_TRAMP_STRIDE];
    unsigned at = g_uc_tramp + (unsigned)idx * UC_TRAMP_STRIDE;
    memset(b, 0x90, sizeof b);           /* nop fill; only the first is caught */
    if (fp) {
        b[1] = 0xdd; b[2] = 0x05;        /* fld qword ptr [disp32] */
        *(unsigned *)(b + 3) = g_uc_scratch;
        b[7] = 0xc3;                     /* ret */
    } else {
        b[1] = 0xc3;                     /* ret */
    }
    uc_must(uc_mem_write(g_uc, at, b, sizeof b), "write trampoline");
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
    case RC_I64: {
        unsigned long long q = ((fn_q)s->fn)(UC_ARGS);
        unsigned lo = (unsigned)q, hi = (unsigned)(q >> 32);
        uc_reg_write(u, UC_X86_REG_EAX, &lo);
        uc_reg_write(u, UC_X86_REG_EDX, &hi);
        break; }
    case RC_DBL: {
        double d = ((fn_d)s->fn)(UC_ARGS);
        uc_mem_write(u, g_uc_scratch, &d, 8);
        break; }
    case RC_FLT: {
        double d = (double)((fn_f)s->fn)(UC_ARGS);
        uc_mem_write(u, g_uc_scratch, &d, 8);
        break; }
    }
}
#undef UC_ARGS

/* An invalid-access hook: name the faulting address, so a fault says what it
 * touched instead of only where the code was.  Returns false -- the access
 * still fails; this only reports it. */
static bool uc_on_badmem(uc_engine *u, uc_mem_type type, uint64_t address,
                         int size, int64_t value, void *user)
{
    unsigned eip = 0;
    const char *k = type == UC_MEM_READ_UNMAPPED  ? "read"  :
                    type == UC_MEM_WRITE_UNMAPPED ? "write" :
                    type == UC_MEM_FETCH_UNMAPPED ? "fetch" : "prot";
    (void)user;
    uc_reg_read(u, UC_X86_REG_EIP, &eip);
    fprintf(stderr, "  [uc] BAD %s addr=%08x size=%d value=%08x eip=%08x\n",
            k, (unsigned)address, size, (unsigned)value, eip);
    return false;
}

/* A report-only host exception handler.  A genuine host crash (a shim
 * dereferencing something bad, or TCG itself) never reaches uc_emu_start's
 * error return, so without this it is a silent SIGSEGV.  This prints the fault
 * and returns CONTINUE_SEARCH, so Unicorn's own handler still gets recoverable
 * guest faults -- it only observes, never intercepts. */
static unsigned g_uc_block_ring[8];   /* last basic blocks entered (debug) */
static int      g_uc_block_i;

static void uc_trace_block(uc_engine *u, uint64_t address, uint32_t size,
                           void *user)
{
    (void)u; (void)size; (void)user;
    g_uc_block_ring[g_uc_block_i++ & 7] = (unsigned)address;
}

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

/* ---- mapping and calling ---------------------------------------------- */

/* Called from map_image after a segment is committed and copied: give the
 * guest the very same bytes at the very same address. */
static void uc_map_segment(void *at, unsigned vmsize)
{
    unsigned sz = (vmsize + 0xfffu) & ~0xfffu;
    uc_must(uc_mem_map_ptr(g_uc, (uint64_t)(uintptr_t)at, sz, UC_PROT_ALL, at),
            "map segment");
}

/* Called from sh_mmap: the engine memory-maps its dictionary and voice files,
 * and the returned view has to be visible to the guest that reads it.  The base
 * is 64 KB-aligned (MapViewOfFile), so page alignment is free; the size is
 * rounded to a page.  This is the seam that carries Alex's demand-paged bank on
 * a device -- TCG faults the pages in transparently. */
static void uc_map_extern(void *base, unsigned len)
{
    unsigned sz = (len + 0xfffu) & ~0xfffu;
    if (!sz) return;
    uc_must(uc_mem_map_ptr(g_uc, (uint64_t)(uintptr_t)base, sz, UC_PROT_ALL, base),
            "map mapped file");
}
static void uc_unmap_extern(void *base, unsigned len)
{
    unsigned sz = (len + 0xfffu) & ~0xfffu;
    if (sz) uc_mem_unmap(g_uc, (uint64_t)(uintptr_t)base, sz);
}

/* Call a guest function as if from a `call` that pushed UC_RETMAGIC, and stop
 * when it returns there.  Darwin i386 wants ESP 16-aligned at the call, i.e.
 * ESP % 16 == 12 at the callee's first instruction; the base is chosen for it.
 * Returns EAX (callers that want a wider result read the registers/scratch
 * themselves; none do yet). */
static int uc_call(void *fn, int argc, const unsigned *argv)
{
    unsigned base = (g_uc_stack_top - 256u) & ~15u;  /* 16-aligned arg base */
    unsigned sp   = base - 4u;                       /* retaddr sits below  */
    unsigned magic = UC_RETMAGIC, eax = 0;
    uc_err e;

    if (g_uc_running)
        die("uc_call re-entered on the shared engine -- a shim called back "
            "into the guest (per-thread uc_engine not built yet)");
    if (argc > 0)
        uc_must(uc_mem_write(g_uc, base, argv, (size_t)argc * 4), "push args");
    uc_must(uc_mem_write(g_uc, sp, &magic, 4), "push retaddr");
    uc_must(uc_reg_write(g_uc, UC_X86_REG_ESP, &sp), "set esp");

    g_uc_running = 1;
    e = uc_emu_start(g_uc, (uint64_t)(uintptr_t)fn, UC_RETMAGIC, 0, 0);
    g_uc_running = 0;
    if (e != UC_ERR_OK) {
        unsigned eip = 0;
        uc_reg_read(g_uc, UC_X86_REG_EIP, &eip);
        die("guest fault: %s at eip=%08x (entry %08x)",
            uc_strerror(e), eip, (unsigned)(uintptr_t)fn);
    }
    uc_reg_read(g_uc, UC_X86_REG_EAX, &eax);
    return (int)eax;
}

/* run_initializers' replacement for `((void(__cdecl*)(void))fn)()`. */
static void uc_run_init(void *fn) { uc_call(fn, 0, NULL); }

/* A SYNCHRONOUS host->guest callback from inside a shim -- pthread_once's init
 * routine is the first -- which must run on the guest now, while the outer
 * uc_emu_start is suspended in the dispatch hook.  The full CPU context is saved
 * and restored around a re-entrant uc_emu_start; the callback runs on a fresh
 * frame just below the current guest stack (a normal nested call would), so the
 * suspended outer frames are untouched.  This is deliberate nesting on the one
 * engine -- distinct from the asynchronous MP worker, which gets its own engine
 * on its own thread; here there is only one logical thread. */
static void uc_call_nested(void *fn)
{
    uc_context *ctx;
    unsigned esp, sp, magic = UC_RETMAGIC;
    uc_err e;
    uc_must(uc_context_alloc(g_uc, &ctx), "context alloc");
    uc_must(uc_context_save(g_uc, ctx), "context save");
    uc_reg_read(g_uc, UC_X86_REG_ESP, &esp);
    sp = ((esp - 512u) & ~15u) - 4u;             /* 16-aligned at the call */
    uc_must(uc_mem_write(g_uc, sp, &magic, 4), "nested retaddr");
    uc_must(uc_reg_write(g_uc, UC_X86_REG_ESP, &sp), "nested esp");
    e = uc_emu_start(g_uc, (uint64_t)(uintptr_t)fn, UC_RETMAGIC, 0, 0);
    uc_must(uc_context_restore(g_uc, ctx), "context restore");
    uc_context_free(ctx);
    if (e != UC_ERR_OK) {
        unsigned eip = 0;
        uc_reg_read(g_uc, UC_X86_REG_EIP, &eip);
        die("nested guest fault: %s at eip=%08x (entry %08x)",
            uc_strerror(e), eip, (unsigned)(uintptr_t)fn);
    }
}

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

/* ---- bring-up ---------------------------------------------------------- *
 * Reserve one contiguous block for every guest region, in this process, before
 * a single image loads, and map it into the guest at its own address.  uc_open
 * first so uc_mem_write into the trampoline page works from the moment bind()
 * runs. */
static void uc_host_init(void)
{
    unsigned char *blk;
    unsigned base;

    uc_must(uc_open(UC_ARCH_X86, UC_MODE_32, &g_uc), "uc_open");

    blk = (unsigned char *)VirtualAlloc(NULL, UC_BLOCK_SZ,
                                        MEM_RESERVE | MEM_COMMIT,
                                        PAGE_EXECUTE_READWRITE);
    if (!blk) die("cannot reserve %u bytes for the guest regions", UC_BLOCK_SZ);
    base = (unsigned)(uintptr_t)blk;

    g_uc_scratch   = base;
    g_uc_tramp     = g_uc_scratch + UC_SCRATCH_SZ;
    g_uc_stack_top = g_uc_tramp + UC_TRAMP_SZ + UC_STACK_SZ;   /* stack grows down */
    g_uc_arena     = g_uc_stack_top;
    g_uc_arena_end = g_uc_arena + UC_ARENA_SZ;
    g_arena_next   = g_uc_arena;

    uc_must(uc_mem_map_ptr(g_uc, base, UC_BLOCK_SZ, UC_PROT_ALL, blk),
            "map guest regions");
    uc_must(uc_hook_add(g_uc, &g_uc_hook, UC_HOOK_CODE, (void *)uc_dispatch,
                        NULL, g_uc_tramp, g_uc_tramp + UC_TRAMP_SZ - 1),
            "install dispatch hook");
    {   /* diagnostics: name any unmapped access before the emu error surfaces */
        static uc_hook h;
        uc_hook_add(g_uc, &h, UC_HOOK_MEM_READ_UNMAPPED |
                    UC_HOOK_MEM_WRITE_UNMAPPED | UC_HOOK_MEM_FETCH_UNMAPPED,
                    (void *)uc_on_badmem, NULL, 1, 0);
    }
    AddVectoredExceptionHandler(1, uc_veh);   /* report-only; see uc_veh */
    if (getenv("TIGER_UC_TRACE")) {
        /* Per-block tracing to place a host crash; off by default (it runs a
         * hook on every basic block).  The last blocks print from uc_veh. */
        static uc_hook hb;
        uc_hook_add(g_uc, &hb, UC_HOOK_BLOCK, (void *)uc_trace_block,
                    NULL, 1, 0);
    }
    if (g_verbose)
        fprintf(stderr, "tiger_host_uc: Unicorn x86-32 up; block at %08x, "
                        "arena %u MB at %08x, trampolines at %08x\n",
                base, UC_ARENA_SZ >> 20, g_uc_arena, g_uc_tramp);
}
