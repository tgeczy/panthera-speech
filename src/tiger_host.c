/* tiger_host -- load Apple's i386 MacinTalk into a 32-bit Windows process and
 * call it directly.
 *
 * The engine is a Mach-O bundle exporting the Speech Manager plugin API as
 * twelve plain C functions.  Nothing about it needs emulation: it is x86 code,
 * and this process is x86.  What it needs is a loader -- something to map its
 * segments, apply its relocations, and fill the pointer slots dyld would have
 * filled.  That is all this file is.
 *
 * NVDA is 64-bit, which is the *only* reason this is a separate process rather
 * than a DLL.  Nothing of Apple's ships with it: the engine stays wherever the
 * user extracted it and is opened by path at runtime.
 *
 * How the binding works, since it is the part that looks harder than it is:
 * calls go through 25-byte PIC stubs in __picsymbolstub2, and each stub is
 *
 *     call  <pic base helper>          ; eax = address of the next instruction
 *     mov   edx, [eax + disp]          ; load the lazy pointer
 *     jmp   edx
 *     ...lazy-binding fallback we never reach...
 *
 * so writing a real address into __la_sym_ptr2 makes the stub jump straight to
 * it.  Non-lazy pointers are read directly.  Fill both and the engine never
 * asks dyld for anything.
 */
#define WIN32_LEAN_AND_MEAN
#ifdef _WIN32
#include <windows.h>
#include <io.h>
#include <mmsystem.h>

/* Media Foundation, for the one thing this host cannot do itself: Vicki's
 * sample bank is AAC, and Windows already has a decoder for it.  COBJMACROS
 * gives the C spelling of the COM calls.  mfplat.dll is bound at run time, not
 * linked -- see aac_open(). */
#define COBJMACROS
#include <objbase.h>
#include <mfapi.h>
#include <mfidl.h>
#include <mftransform.h>
#include <mferror.h>
#include <wmcodecdsp.h>
#else
/* Not Windows: the POSIX platform seam maps the Win32 names this host is
 * written in onto pthreads, mmap, dlopen and clock_gettime, so the same source
 * cross-compiles for the Android NDK.  It must come before the printf redirect
 * below.  Media Foundation is Windows-only; the AAC path is the stub
 * (TIGER_NO_AAC) here, or the system decoder later. */
#include "tiger_plat.h"
#include <sys/resource.h>
#include <sched.h>
#include <sys/syscall.h>
#include <unistd.h>
#endif
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>
#include <math.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <ctype.h>
/* malloc.h, for the one question the guest's allocator has to ask the host's:
 * how large a block really is (_msize / malloc_usable_size), so that a
 * realloc fills what it grew by and not one byte more.  Not in the C
 * standard; present on all three toolchains this builds with. */
#include <malloc.h>

/* The in-process synthesis API the Android JNI layer calls -- declared up here
 * so main()'s --jni-check sees the prototypes; defined at the end of the TU
 * (tiger_host_jni.c), after host_open and the engine internals it uses. */
#if defined(TIGER_UC) || defined(TIGER_LIB)
#include "tiger_host_jni.h"
#endif

/* Every diagnostic in this file goes to stderr, without exception: in serve
 * mode stdout carries raw PCM to the driver, and one stray character would
 * corrupt an utterance in a way that is very hard to trace back to a printf.
 * Redirecting here is safer than remembering at each call site. */
#define printf(...) fprintf(stderr, __VA_ARGS__)

/* ---- Mach-O, only the parts used here ---------------------------------- */

#define FAT_CIGAM       0xbebafeca      /* fat header is big-endian */
#define MH_MAGIC        0xfeedface
#define CPU_TYPE_X86    7

#define LC_SEGMENT      0x1
#define LC_SYMTAB       0x2
#define LC_DYSYMTAB     0xb

/* Snow Leopard onwards.  From 10.6 the relocation tables are empty and two
 * bytecode streams carry the same information instead; see
 * tiger_host_dyldinfo.c.  Both spellings occur -- `_ONLY` additionally
 * promises the classic tables are absent, and for MacinTalk they are. */
#define LC_DYLD_INFO        0x22
#define LC_DYLD_INFO_ONLY   0x80000022

#define S_ZEROFILL              0x1
#define S_NON_LAZY_SYMBOL_PTR   0x6
#define S_LAZY_SYMBOL_PTR       0x7
#define S_SYMBOL_STUBS          0x8
#define S_MOD_INIT_FUNC         0x9

#define N_STAB  0xe0
#define N_TYPE  0x0e
#define N_EXT   0x01
#define N_UNDF  0x0
#define N_PBUD  0xc
/* An alias rather than a definition: `n_value` indexes the string table and
 * names the symbol this one stands for.  Lion's libstdc++ is full of them --
 * see lookup_in(). */
#define N_INDR  0xa

#define INDIRECT_SYMBOL_LOCAL 0x80000000
#define INDIRECT_SYMBOL_ABS   0x40000000

#define MH_SPLIT_SEGS           0x20
#define MH_PREBOUND             0x10
#define R_SCATTERED             0x80000000
#define GENERIC_RELOC_VANILLA   0
#define GENERIC_RELOC_PB_LA_PTR 3

#pragma pack(push, 1)
typedef struct { unsigned magic, cputype, cpusubtype, filetype, ncmds,
                          sizeofcmds, flags; } mach_header;
typedef struct { unsigned cmd, cmdsize; } load_command;
typedef struct { unsigned cmd, cmdsize; char segname[16];
                 unsigned vmaddr, vmsize, fileoff, filesize;
                 unsigned maxprot, initprot, nsects, flags; } segment_command;
typedef struct { char sectname[16], segname[16];
                 unsigned addr, size, offset, align, reloff, nreloc, flags,
                          reserved1, reserved2; } section;
typedef struct { unsigned cmd, cmdsize, symoff, nsyms, stroff, strsize; }
        symtab_command;
typedef struct { unsigned cmd, cmdsize;
                 unsigned ilocalsym, nlocalsym, iextdefsym, nextdefsym,
                          iundefsym, nundefsym, tocoff, ntoc,
                          modtaboff, nmodtab, extrefsymoff, nextrefsyms,
                          indirectsymoff, nindirectsyms,
                          extreloff, nextrel, locreloff, nlocrel; }
        dysymtab_command;
typedef struct { unsigned n_strx; unsigned char n_type, n_sect;
                 short n_desc; unsigned n_value; } nlist;
typedef struct { int r_address; unsigned r_info; } reloc;
typedef struct { unsigned cmd, cmdsize;
                 unsigned rebase_off, rebase_size, bind_off, bind_size,
                          weak_off, weak_size, lazy_off, lazy_size,
                          export_off, export_size; } dyld_info_command;
#pragma pack(pop)

/* ---- the loaded image -------------------------------------------------- */

typedef struct {
    const char      *path;
    unsigned char   *file;      /* whole file, as read */
    unsigned char   *slice;     /* start of the i386 slice */
    unsigned         lo, hi;    /* vmaddr range of the image */
    unsigned         data_vmaddr;/* first writable segment; split-seg reloc base */
    unsigned         slide;     /* add to any vmaddr to get a real address */
    const nlist     *syms;
    unsigned         nsyms;
    const char      *strs;
    const dysymtab_command *dys;
    section          sects[64];
    int              nsects;
    /* The dyld info streams address a slot as (segment index, offset), and
     * the index counts **every** LC_SEGMENT in load-command order -- including
     * __PAGEZERO and any with vmsize 0, which nothing else here cares about.
     * Filtering this list the way `sects` is filtered would shift every index
     * after the first skipped segment and land each fixup in the wrong place. */
    unsigned         segaddr[16];
    int              nsegs;
    const dyld_info_command *info;  /* NULL for Tiger and Leopard */
} image;

/* Storage the guest must be able to hold or dereference.
 *
 * The identity mapping means a pointer the engine holds and a pointer a shim
 * holds are the same number -- and the engine's numbers are 32 bits wide.  On a
 * 32-bit host every address already qualifies and a plain static is fine.  On a
 * 64-bit host a static lands wherever the dynamic linker put the library, which
 * is nowhere near the low 4 GB, and handing its address to the guest truncates
 * it into a fault a long way from here.
 *
 * So on 64-bit these objects are allocated out of the same low region the arena
 * and the images live in.  Declaring them as POINTERS either way is what keeps
 * the diff small: `g_units[i]` and `&g_units[n]` read the same whether the name
 * is an array or a pointer to one, so only the declaration changes.
 *
 * Not a copy -- they have to *live* there.  g_errno_storage is written by a
 * shim and read by the engine; two of it would be a bug that looks like a
 * miracle.  GUEST_STATIC therefore allocates once and everything uses the
 * result. */
#if defined(TIGER_UC) && UINTPTR_MAX > 0xffffffffu
#define GUEST_LOW 1
#else
#define GUEST_LOW 0
#endif

/* Where the native mutex and condition variable live.
 *
 * Darwin i386 gives a pthread_mutex_t 44 opaque bytes and a pthread_cond_t 28,
 * and the engine allocates that storage itself -- so a host lock small enough
 * to sit inside it can, which saves a lookup on every acquire.  Whether it is
 * small enough is a property of the HOST'S C LIBRARY, not of its word size:
 *
 *     Windows      CONDITION_VARIABLE   4 bytes    fits
 *     bionic 32    pthread_cond_t       4 bytes    fits
 *     bionic 64    pthread_cond_t      48 bytes    does not
 *     glibc i386   pthread_cond_t      48 bytes    does not
 *
 * The third line is why arm64 needed a side table.  The fourth is why the
 * first native Linux build would not compile: 32-bit was assumed to imply
 * "small", and on glibc it does not.  So the objects go beside the guest's
 * storage, in a table keyed by guest address, on any host where they will not
 * fit -- and the static asserts further down stay exactly where they are, as
 * the tripwire that caught this. */
#if GUEST_LOW || defined(__GLIBC__)
#define GUEST_SYNC_SIDE 1
#else
#define GUEST_SYNC_SIDE 0
#endif

/* Native builds use static storage. Under emulation, allocate in the mapped
 * guest arena even on 32-bit hosts: low host addresses alone are not mapped. */
#ifdef TIGER_UC
#define GUEST_STATIC(type, name, count)     static type *name;     static const unsigned name##_guest_count = (count)
#else
#define GUEST_STATIC(type, name, count)     static type name##_storage[(count)];     static type *name = name##_storage;     static const unsigned name##_guest_count = (count)
#endif

/* Fill in one under emulation; a no-op natively. Called before any image
 * loads, so the engine can never see an unset pointer. */
#ifdef TIGER_UC
#define GUEST_STATIC_INIT(name)     do { if (!name) { name = (void *)arena_alloc(sizeof *name * name##_guest_count);                       if (name) memset(name, 0, sizeof *name * name##_guest_count); } } while (0)
#else
#define GUEST_STATIC_INIT(name) ((void)0)
#endif

/* ---- the guest's own widths -------------------------------------------- *
 *
 * A struct BOTH SIDES touch has to be laid out the way the engine lays it out,
 * and the engine is i386: a pointer is four bytes, and so is `long`.  Written
 * with the host's own types, every field after the first eight-byte one sits
 * at the wrong offset on a 64-bit host -- and that is not a crash at the
 * struct.  It is a crash much later, holding a value assembled from two
 * unrelated halves, with nothing left to say where it came from.  The first
 * three bugs of this port were all that shape.
 *
 * `gptr` is deliberately an INTEGER rather than a pointer type.  A `void *`
 * that is secretly four bytes wide would keep compiling at every site and go
 * on being wrong; an `unsigned` makes the compiler refuse each assignment and
 * each dereference by name, which turns the audit from a reading exercise
 * into a build log.
 *
 * On a 32-bit host `gptr` is exactly as wide as a pointer and every macro
 * below is the identity, so the shipping ARMv7 build cannot change behaviour.
 */
typedef unsigned gptr;          /* a pointer as the guest holds it: 4 bytes */
typedef int      glong;         /* the guest's `long`, likewise: 4 bytes    */

#if GUEST_LOW
/* Name the site that MADE the bad pointer, not the fault it becomes.
 *
 * A host-heap address handed to the guest truncates into a number that still
 * looks like an address, and faults later somewhere with no memory of where
 * it came from -- `strlen` inside an `fprintf` inside a bundle lookup, to
 * pick the one that cost this afternoon.  So complain here, once per site,
 * and carry on: one run then lists every offender instead of spending a whole
 * build-push-run cycle discovering them one at a time. */
static unsigned gp_check(const void *p, const char *file, int line)
{
    uintptr_t v = (uintptr_t)p;
    if (v >> 32) {
        static struct { const char *file; int line; } seen[64];
        static int nseen;
        int i;
        for (i = 0; i < nseen; i++)
            if (seen[i].line == line && seen[i].file == file) return (unsigned)v;
        if (nseen < (int)(sizeof seen / sizeof seen[0])) {
            seen[nseen].file = file;
            seen[nseen].line = line;
            nseen++;
        }
        fprintf(stderr, "tiger_host: %s:%d gave the guest a host pointer "
                        "(%p) -- it does not fit in 32 bits\n", file, line, p);
    }
    return (unsigned)v;
}
#define GP(p)   gp_check((const void *)(p), __FILE__, __LINE__)
#else
#define GP(p)   ((gptr)(uintptr_t)(const void *)(p))
#endif
#define GHOST(g)    ((void *)(uintptr_t)(gptr)(g))

/* A float the guest passed BY VALUE.
 *
 * The trampoline forwards i386 stack words as integers, and on a host whose
 * ABI puts float arguments in the integer registers -- i386, and Android's
 * softfp armeabi-v7a -- a shim can simply declare `float` and be handed the
 * right bits.  AArch64 has no such mode: a `float` parameter is read from
 * v0..v7, which nothing here ever wrote, and every argument after it shifts
 * up by one.  `cblas_sscal(n, a, x, incx)` therefore took the bit pattern of
 * `a` as its `x` pointer and dereferenced 0x38800000.
 *
 * So the shims that take a float by value take its BITS instead, and say so
 * in their parameter names.  Reinterpret, never convert: `(float)bits` would
 * turn the pattern into the number it spells. */
static float GFLOAT(unsigned bits)
{
    float f;
    memcpy(&f, &bits, sizeof f);
    return f;
}

/* A 64-bit value the guest passed BY VALUE, as the two words it pushed.
 *
 * Same fact as GFLOAT and a worse blast radius.  The engine pushes eight
 * bytes as two stack words; a shim declaring `long long` is handed ONE
 * register on AArch64, so it reads the low half as the whole value and every
 * argument after it shifts by one.  `___divdi3` -- which is every 64-bit
 * division the engine performs -- was taking its divisor from the dividend's
 * high word.
 *
 * The pair was already known here: AddDurationToAbsolute and
 * SubDurationFromAbsolute have their own return class in the dispatcher, with
 * a comment about exactly this alignment.  What was missed is that the same
 * thing is true of every OTHER function taking a 64-bit argument, whatever it
 * returns.  Taking the words is right on i386 too, where they are the same
 * eight bytes in the same order. */
#define GI64(lo, hi) \
    ((long long)(((unsigned long long)(unsigned)(hi) << 32) | (unsigned)(lo)))
#define GU64(lo, hi) \
    ((((unsigned long long)(unsigned)(hi)) << 32) | (unsigned)(lo))

/* Memory whose ADDRESS the guest will hold.
 *
 * A shim returns its result in EAX, which is four bytes wide, so anything a
 * shim hands back has to live below 4 GB.  On a 32-bit host every address
 * already does and these are the libc functions unchanged; on a 64-bit host
 * the library's heap is nowhere near it, so they come out of the same low
 * arena the guest's own malloc uses.  Zeroed either way -- the call sites
 * were written against `calloc` and one of them counts on it. */
#ifdef TIGER_UC
#define GMEM_ALLOC(n)       sh_uc_calloc(1, (n))
#define GMEM_REALLOC(p, n)  sh_uc_realloc((p), (n))
#define GMEM_FREE(p)        sh_uc_free((void *)(p))
#else
#define GMEM_ALLOC(n)       calloc(1, (n))
#define GMEM_REALLOC(p, n)  realloc((p), (n))
#define GMEM_FREE(p)        free((void *)(p))
#endif

/* ---- shared state ------------------------------------------------------ */
static image *g_primary;        /* MacinTalk; the image addresses resolve against */
static int g_verbose = 1;
/* Opt-in cancellation/lock timing, independent of per-sample audio tracing. */
static int g_cancel_trace;
static unsigned g_mp_waits;     /* how many times a worker has blocked */
static volatile long g_stopped; /* AUGraphStop: the engine's end-of-utterance */
/* "Expand abbreviations", off by TIGER_NO_ABBREV; see tiger_host_regex.c.
 * Read once at startup so the answer cannot change mid-utterance. */
static int g_no_abbrev;

/* Tell the scheduler that a thread carries speech.
 *
 * The emulated engine runs on three host threads -- the synthesis thread, the
 * engine's MP worker, and the pacer that ticks its slice completions -- and all
 * three are on the path between a key press and a word.  Android's scheduler
 * places by priority, so a thread that never says it is latency-critical is a
 * thread it is free to put on the slowest core it has.
 *
 * That is worth nothing on the watch this was measured on -- a Pixel Watch 2 is
 * four identical Cortex-A53s at 1.708 GHz, already pinned at maximum by the
 * governor during a render, with no faster core to be moved to.  It is worth a
 * great deal on a heterogeneous one: a Galaxy Watch pairs a Cortex-A78 with
 * A55s, and an in-order A55 is close to the worst case for QEMU's threaded code
 * while an out-of-order A78 is not.  Being placed on the wrong one of those is
 * the difference between Alex answering and Alex being a joke.
 *
 * Best effort by construction: an app may not always renice its own threads,
 * and failing to is not a reason to refuse to speak. */
/* The fastest cores this machine has, as a CPU mask, or 0 if they are all the
 * same.  Read once from cpufreq: cpuinfo_max_freq is per core, and a watch that
 * pairs one out-of-order core with four in-order ones reports exactly that.
 *
 * Measured on a Galaxy Watch (SM-L350): one Cortex-A78 at 2.112 GHz and four
 * Cortex-A55 at 1.958 GHz.  The clocks are within eight percent of each other
 * and the cores are not: emulated code is branchy and dependency-chained, which
 * an in-order core stalls on, so the same soak utterance took 5.1 s on one run
 * and 14.5 s on the next, and the same 637 access units decoded in 26 ms and
 * then 362 ms.  That is not thermal and not load, it is which core the thread
 * happened to be on.
 */
static unsigned long tiger_fast_cpu_mask(void)
{
    static unsigned long mask;
    static int done;
    unsigned long best = 0, m = 0;
    int i, total = 0, fast = 0;
    if (done) return mask;
    done = 1;
    for (i = 0; i < 32; i++) {
        char path[128];
        FILE *f;
        unsigned long khz = 0;
        sprintf(path, "/sys/devices/system/cpu/cpu%d/cpufreq/cpuinfo_max_freq", i);
        f = fopen(path, "r");
        if (!f) continue;
        if (fscanf(f, "%lu", &khz) != 1) khz = 0;
        fclose(f);
        if (!khz) continue;
        total++;
        if (khz > best) { best = khz; m = 0; }
        if (khz == best) m |= 1UL << i;
    }
    for (i = 0; i < 32; i++) if (m & (1UL << i)) fast++;
    /* Every core the same speed -- a Pixel Watch 2 is four identical A53s -- so
     * there is no faster one to ask for and pinning would only take choices
     * away from a scheduler that knows more than we do.  Compared against the
     * machine's own core count rather than a guessed number: a phone with four
     * big cores and four little ones is heterogeneous and worth pinning on, and
     * counting to four would have called it homogeneous and skipped it. */
    if (!total || fast == total) m = 0;
    mask = m;
    return mask;
}

/* Ask for a fast core as well as a high priority.
 *
 * Priority alone did not hold it: with THREAD_PRIORITY_AUDIO set, the same
 * utterance still ran three times slower on one pass than the next, because the
 * scheduler is free to move a thread and an A55 is a different machine from an
 * A78 for this workload.  So the threads that actually run guest code say
 * where they want to be.
 *
 * Only the renderers -- the MP worker and the synthesis thread.  The pacer is
 * deliberately left free: it does a few microseconds per slice and pinning it
 * to the same single core as the worker would serialise the two against each
 * other, which is the one way this could be made worse.
 *
 * TIGER_NO_AFFINITY=1 turns it off, because "is the pinning helping?" is a
 * question somebody will want to answer without rebuilding. */
static void tiger_thread_wants_fast_core(const char *what)
{
#ifdef __ANDROID__
    static int off = -1;
    unsigned long mask = tiger_fast_cpu_mask();
    if (off < 0) { const char *e = getenv("TIGER_NO_AFFINITY"); off = e && atoi(e); }
    if (off || !mask) return;
    /* Through the syscall rather than the libc wrapper: bionic only declares
     * sched_setaffinity under _GNU_SOURCE, and defining that for the whole
     * translation unit to reach one call would change more than it fixes. */
    if (syscall(__NR_sched_setaffinity, 0, sizeof mask, &mask) != 0) {
        if (g_verbose) printf("  [sched] %s could not ask for cpu mask %lx\n", what, mask);
    } else if (g_verbose) {
        printf("  [sched] %s pinned to cpu mask %lx\n", what, mask);
    }
#else
    (void)what;
#endif
}

static void tiger_thread_is_audio(const char *what)
{
#ifdef __ANDROID__
    /* -16 is THREAD_PRIORITY_AUDIO, the value the framework's own audio threads
     * use.  Not URGENT_AUDIO: this is a renderer, not a mixer, and starving the
     * mixer to feed it would be a poor trade. */
    if (setpriority(PRIO_PROCESS, 0, -16) != 0 && g_verbose)
        printf("  [sched] %s stays at default priority\n", what);
#else
    (void)what;
#endif
}

/* TIGER_PREF_LOG: name every tuning parameter the engine asks for. */
static int g_pref_log;
/* TIGER_DEFERRED_STOP=1 lets Lion's deferred audio-graph stop fire, which
 * wedges the engine; see tiger_host_gcd.c.  Here so the bug can be reproduced
 * on demand rather than only remembered. */
static int g_deferred_stop;
/* TIGER_GCD_LOG: narrate every GCD handler and every stage of a request.
 *
 * For hangs rather than for wrong answers.  Everything in the end-of-utterance
 * summary prints when the utterance ends, which is no help at all when the
 * complaint is that it never does. */
static int g_gcd_log;


static void die(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "tiger_host: ");
    vfprintf(stderr, fmt, ap);
    fprintf(stderr, "\n");
    va_end(ap);
    exit(1);
}

/* Defined in tiger_host_dyldinfo.c, which is included after the loader
 * because it needs the whole lookup chain -- and which the loader has to be
 * able to call.  Two declarations beat reordering the includes. */
static void apply_rebases(image *im);
static void apply_binds(image *im, image *dep);

static unsigned bswap(unsigned v)
{
    return (v >> 24) | ((v >> 8) & 0xff00) | ((v << 8) & 0xff0000) | (v << 24);
}


/* The rest of the host, in the order it has to be compiled.  One
 * translation unit on purpose: these parts share a great deal of static
 * state and splitting them into real objects would mean publishing all of
 * it.  This way the files are readable and the compiler still sees exactly
 * what it saw when every one of these lines was debugged. */
#ifndef _WIN32
/* The platform floor: Win32 names on pthreads/mmap.  First of all, so every
 * seam file below sees the same VirtualAlloc, CreateThread and CRITICAL_SECTION
 * it would on Windows. */
#include "tiger_plat_posix.c"
#endif
#ifdef TIGER_UC
/* The Unicorn seam, first so its uc_* helpers are visible to every seam point
 * below -- call_aligned in the shims, the arena rows in the shim table, the
 * trampolines and uc_run_init in the Mach-O loader. */
#include "tiger_host_uc.c"
/* Marshalling a host buffer into the guest for an engine call.  In the emulated
 * build these place the buffer in the guest arena; in the native build they are
 * identities, so a call site reads the same in both. */
#define UC_IN(host, n)        uc_in((host), (n))
#define UC_IN_STR(s, n)       uc_in_str((s), (n))
#define UC_OUT(var)           uc_out(sizeof(var))
#define UC_OUT_GET(slot, var) ((var) = *(slot))
/* An out-parameter the guest fills with a POINTER.
 *
 * sizeof(var) is the wrong size for one of those on a 64-bit host: the guest
 * writes four bytes and UC_OUT_GET would read eight, taking whatever sits next
 * to it as the top half.  That is not a subtle corruption -- the first one cost
 * a fault at 0xa6d0d7c000000000, which is a perfectly good guest address
 * shifted into the high word. */
#define UC_OUT_PTR(var)           uc_out(4)
#define UC_OUT_PTR_GET(slot, var)         ((var) = (void *)(uintptr_t)*(unsigned *)(slot))
/* Calling back into the guest from *inside* a shim -- the audio callbacks the
 * engine hands us.  These are nested by construction: the guest is already
 * running up the stack, suspended in the dispatch hook, so they take the
 * context-saving path rather than uc_call, which refuses re-entry. */
#define CALL_GUEST2(fn, a, b)             uc_cb2((fn), (a), (b))
#define CALL_GUEST5(fn, a, b, c, d, e)    uc_cb5((fn), (a), (b), (c), (d), (e))
/* Give a marshalling slot back.  UC_IN and UC_OUT take arena memory, and a
 * call site that runs once per utterance can forget to; one that runs hundreds
 * of times per utterance -- the audio callbacks -- cannot, because the arena is
 * finite and what exhausting it looks like is a null malloc the engine does not
 * check.  A no-op in the native build, where the "slot" is a stack address. */
#define UC_FREE(p)            sh_uc_free((void *)(p))
#else
#define UC_IN(host, n)        ((void *)(host))
#define UC_IN_STR(s, n)       ((void *)(s))
#define UC_OUT(var)           (&(var))
#define UC_OUT_GET(slot, var) ((void)0)
#define UC_OUT_PTR(var)       (&(var))
#define UC_OUT_PTR_GET(slot, var) ((void)0)
#define CALL_GUEST2(fn, a, b)             call_aligned2((fn), (a), (b))
#define CALL_GUEST5(fn, a, b, c, d, e)    call_aligned5((fn), (a), (b), (c), (d), (e))
#define UC_FREE(p)            ((void)0)
#endif
#include "tiger_host_shims.c"
#include "tiger_host_cf.c"
#include "tiger_host_files.c"
#include "tiger_host_audio.c"
#ifdef TIGER_NO_AAC
#include "tiger_host_aac_stub.c"
#else
#include "tiger_host_aac.c"
#endif
#include "tiger_host_gcd.c"
#include "tiger_host_cxx.c"
#include "tiger_host_accel.c"
#include "tiger_host_linalg.c"
#include "tiger_host_sqlite.c"
#include "tiger_host_regex.c"
#include "tiger_host_numbers.c"
#include "tiger_host_shimtab.c"
#include "tiger_host_fault.c"
#include "tiger_host_sllog.c"
#include "tiger_host_macho.c"
#include "tiger_host_dyldinfo.c"
#include "tiger_host_speech.c"
#include "tiger_host_volume.c"
#include "tiger_host_serve.c"
#include "tiger_host_pmod.c"

/* ---- bringing the host up ---------------------------------------------- */

typedef int (__cdecl *SEOpen_t)(void **chan);

/* The images, and the channel opened on them.
 *
 * These were `main`'s locals until the DLL arrived.  `tiger_host_api.c` has to
 * perform the identical bring-up from a thread it starts itself, and the one
 * thing that must never happen is two bring-up sequences that drift apart:
 * much of the loader's correctness is in the *order* of these steps --
 * libc++abi before libstdc++ before the engines, every image resolved before
 * any initializer runs -- and a second copy would be a second chance to get
 * that order wrong, discovered months later in whichever half nobody was
 * looking at.  So there is one copy and both entry points call it. */
static image g_mt, g_sd, g_ls, g_ab;
static int g_have_ls, g_have_ab;
static void *g_chan;

/* Serve mode is quiet from the very first line, not from the point `serve`
 * takes over: the driver puts everything this writes into NVDA's log, and the
 * loader's commentary is several hundred lines of it.
 *
 * Unless the user has actually asked for debug logging, in which case they
 * should get ours too.  A synthesizer that stays silent when someone has
 * deliberately turned the logging up is no easier to diagnose than one with no
 * logging at all -- and the driver only passes this when NVDA's own level is
 * DEBUG, so nobody pays for it by accident. */
static void host_quiet(void)
{
    if (getenv("TIGER_HOST_VERBOSE"))
        fprintf(stderr, "tiger_host: verbose logging on, at NVDA's "
                        "request\n");
    else
        g_verbose = 0;
}

/* Everything between "a process exists" and "a speech channel is open".
 *
 * Returns 0 with `g_chan` set, or the engine's OSErr.  Nothing in here is
 * specific to being an executable, which is the point: the DLL runs exactly
 * these steps, in exactly this order, on the thread that goes on to serve.
 *
 * **Once per process.**  There is no teardown path -- `VirtualFree` appears in
 * this loader only as cleanup for a reservation that failed -- so calling this
 * twice would map a second copy of every image and, with Alex's 701 MB bank
 * among them, exhaust a 2 GB address space rather than reuse it. */
/* Give every guest-visible static an address the guest can hold.
 *
 * One place rather than scattered lazy initialisation, and it runs immediately
 * after uc_host_init -- the arena has to exist, and no image may have loaded,
 * because from the first initialiser onwards the engine can reach these.
 *
 * A no-op on a 32-bit host, where each of these is the static it always was. */
static void guest_statics_init(void)
{
    au_guest_init();                       /* g_graph, g_units, and 'AUGR' */
    GUEST_STATIC_INIT(g_cfstring_class);
    GUEST_STATIC_INIT(g_errno_storage);
    GUEST_STATIC_INIT(g_dispatch_handles);
    GUEST_STATIC_INIT(g_sources);
    GUEST_STATIC_INIT(g_the_locale);
    GUEST_STATIC_INIT(g_speech_obj);
}

static int host_open(const char *mtpath, const char *sdpath)
{
    SEOpen_t open_chan;
    int err;

    /* Unbuffered stderr: this program's other job is to crash informatively,
     * and buffered output is discarded when it does. */
    setvbuf(stderr, NULL, _IONBF, 0);
    g_cancel_trace = getenv("TIGER_CANCEL_TRACE") ? 1 : 0;
#ifdef TIGER_UC
    /* Bring the emulator up before a single image loads: it reserves the guest
     * regions at their own addresses, which a later image slide must never be
     * handed. */
    uc_host_init();
    guest_statics_init();       /* needs the arena; must precede any image */
#endif
    if (getenv("TIGER_CF_LOG")) g_cflog = 1;
    { const char *e = getenv("TIGER_SPEED");
      if (e && atof(e) > 0.0) g_speed = atof(e);
      g_pace = 100.0 / g_speed;              /* pacer follows the clock */
      e = getenv("TIGER_STATUS");
      if (e) g_ask_status = atoi(e) != 0;
      e = getenv("TIGER_RESET");
      if (e) g_use_reset = atoi(e) != 0;
      e = getenv("TIGER_PACE_FLOOR");
      if (e) g_pace_floor = atof(e); }
    /* Windows sleeps in 15.6 ms steps by default, so a 1 ms pace tick really
     * costs 15 ms and an utterance renders at wall-clock speed no matter how
     * low the pace goes.  Ask for 1 ms resolution and the pacer means what it
     * says. */
    timeBeginPeriod(1);
    init_rune_locale();
    InitializeCriticalSection(&g_p_cs);
    CreateThread(NULL, 0, pacer_thread, NULL, 0, NULL);

    g_thunks = (unsigned char *)VirtualAlloc(NULL, MAX_MISSING * THUNK_SZ,
                                             MEM_RESERVE | MEM_COMMIT,
                                             PAGE_EXECUTE_READWRITE);
    if (!g_thunks) die("cannot allocate thunk area");

    g_float_stats = getenv("TIGER_FLOAT_STATS") ? 1 : 0;
    cf_params_init();       /* TIGER_PARAMS; does nothing when it is unset */
    g_no_abbrev = getenv("TIGER_NO_ABBREV") ? 1 : 0;
    g_pref_log  = getenv("TIGER_PREF_LOG") ? 1 : 0;
    g_gcd_log   = getenv("TIGER_GCD_LOG") ? 1 : 0;
    g_deferred_stop = getenv("TIGER_DEFERRED_STOP") ? 1 : 0;
    if (g_no_abbrev)
        fprintf(stderr, "tiger_host: abbreviation rules are off\n");

#ifndef TIGER_UC
    /* Not under emulation: Unicorn/TCG runs the guest in JIT-generated host code
     * and installs its own handler to turn a guest memory fault into a clean
     * uc_emu_start error.  A first-priority host handler here would intercept
     * that fault first and report a host crash for what is really a guest one --
     * so the guest's faults surface through uc (and uc_on_badmem) instead. */
#ifdef _WIN32
    AddVectoredExceptionHandler(1, on_fault);
#elif defined(__linux__) && defined(__i386__)
    if (!install_native_divzero(native_fault_in_guest))
        die("cannot install native guest divide-by-zero recovery");
#endif
#endif

    /* The optional runtime images, loaded before the engines so their
     * initializers run before anything calls into them.
     *
     * libc++abi first: from 10.7 the C++ ABI lives there and libstdc++
     * re-exports it, so libstdc++ is the one with the dependency.  Leopard
     * wants neither ordering nor the library -- its 6.0.4 implements the ABI
     * itself -- so its absence is reported and then ignored. */
    {
        char path[CFPATH];
        if (find_libcxxabi(sdpath, path, sizeof(path))) {
            load(&g_ab, path);
            g_images[g_nimages++] = &g_ab;
            g_have_ab = 1;
        } else if (g_verbose) {
            printf("no libc++abi beside the engine; only 10.7 wants one\n");
        }
        if (find_libstdcxx(sdpath, path, sizeof(path))) {
            load(&g_ls, path);
            g_images[g_nimages++] = &g_ls;
            g_have_ls = 1;
        } else if (g_verbose) {
            printf("no libstdc++ beside the engine; Tiger needs none\n");
        }
    }

    load(&g_sd, sdpath);
    load(&g_mt, mtpath);
    g_images[g_nimages++] = &g_sd;
    g_images[g_nimages++] = &g_mt;
    g_primary = &g_mt;

    /* The dictionary bundle is the directory holding the framework binary:
     * .../SpeechDictionary.framework/Versions/A, whose Resources carry the
     * 2.1 MB StdDictionary. */
    {
        char dir[CFPATH];
        char *cut;
        strncpy(dir, sdpath, sizeof(dir) - 1);
        dir[sizeof(dir) - 1] = 0;
        /* Take the LAST of either separator.  Searching for '/' first and only
         * falling back to '\\' cuts a *mixed* path at the wrong place -- and a
         * mixed path is exactly what Python's os.path.join produces from a
         * forward-slash root: "<root>/x86\\SpeechDictionary.framework\\Versions
         * \\A\\SpeechDictionary" cut at the last '/' -- the one inside <root>
         * -- so the dictionary was never found and the engine died inside a
         * lookup, a long way from here. */
        {
            char *a = strrchr(dir, '/');
            char *b = strrchr(dir, '\\');
            cut = (a > b) ? a : b;
        }
        if (cut) *cut = 0;
        g_dict_bundle = cf_pinned(dir);
        if (g_verbose) printf("dictionary bundle: %s\n", dir);
    }

    if (g_have_ab) {
        if (g_verbose) printf("binding libc++abi:\n");
        resolve(&g_ab, NULL);
    }
    if (g_have_ls) {
        if (g_verbose) printf("binding libstdc++:\n");
        resolve(&g_ls, NULL);
    }
    if (g_verbose) printf("binding SpeechDictionary:\n");
    resolve(&g_sd, NULL);
    if (g_verbose) printf("binding MacinTalk:\n");
    resolve(&g_mt, &g_sd);

    if (g_verbose) printf("running initializers:\n");
    if (g_have_ab) run_initializers(&g_ab);
    if (g_have_ls) run_initializers(&g_ls);
    run_initializers(&g_sd);
    run_initializers(&g_mt);

    open_chan = (SEOpen_t)find_export(&g_mt, "_SEOpenSpeechChannel");
    if (!open_chan) die("SEOpenSpeechChannel not found");
    if (g_verbose) printf("\nSEOpenSpeechChannel at %p\n", (void *)open_chan);

    /* Every entry into the engine goes through an aligning trampoline:
     * Darwin i386 guarantees ESP is 16-byte aligned at each call and
     * Leopard's engine spends that guarantee on movapd.  The channel is an
     * out-parameter: the engine writes it through the pointer we pass, which
     * under emulation must be a guest address (UC_OUT), read back after. */
    {
        void *slot = UC_OUT_PTR(g_chan);
        err = call_aligned1((void *)open_chan, slot);
        UC_OUT_PTR_GET(slot, g_chan);
    }
    if (g_verbose) printf("  -> OSErr %d, channel %p\n", err, g_chan);
    /* A zero OSErr with a null channel has never been seen, but everything
     * downstream dereferences it, so it is refused here as an error rather
     * than arriving later as a fault. */
    if (!err && !g_chan) err = -1;
    return err;
}

/* ---- main -------------------------------------------------------------- */

/* The executable's entry point only.  The DLL build has its own, in
 * tiger_host_api.c, and a DLL with a `main` in it links but confuses every
 * tool that looks at one.  The JNI shared library likewise carries no `main`;
 * it is entered through the panthera_* API in tiger_host_jni.c. */
#if !defined(PT_DLL) && !defined(TIGER_JNI) && !defined(TIGER_SHARED)

#include "tiger_host_cli.c"

int main(int argc, char **argv)
{
    int err, i;
    /* The VoiceSpec the engine is told to use.  It falls back to Fred --
     * 'mtk3' 1, the voice everyone means -- but only when the voice bundle
     * cannot say who it is; see below.  The other two families, for reading
     * the logs: 'gala' 100 is Bruce, 'meow' 200 is Vicki. */
    const char *voicedir;
    const char *servedir = NULL;
    int pmodcheck = 0;
    unsigned creator = 'mtk3';
    int voiceid = 1;
    int spec_given = 0;

    if(argc>1&&!strcmp(argv[1],"--help")){cli_help();return 0;}
    if(argc>1&&!strcmp(argv[1],"--render"))return cli_render(argc,argv);
#ifndef TIGER_UC
    if(argc>1&&!strcmp(argv[1],"--audio-timeline-check"))return timeline_check();
#endif

    /* --serve <MacinTalk> <SpeechDictionary> <VoicesDir> : stay resident and
     * answer requests on stdin/stdout.  Otherwise render one utterance and
     * write a wav, which is the shape that made every fix above findable. */
    if (argc == 5 && !strcmp(argv[1], "--volume-value")) {
        fprintf(stdout, "%d\n", volume_milli(atoi(argv[2]), argv[3], argv[4]));
        return 0;
    }
    if (argc > 1 && !strcmp(argv[1], "--capabilities")) {
        int available = aac_check() == 0;
#if defined(TIGER_NO_AAC)
        const char *backend = "none";
#elif defined(TIGER_AAC_FALLBACK)
        const char *backend = aac_backend_name();
#elif defined(TIGER_AAC_GLINT)
        const char *backend = "glint";
#elif defined(TIGER_AAC_FAAD)
        const char *backend = "faad2";
#elif defined(__ANDROID__)
        const char *backend = "mediacodec";
#else
        const char *backend = "media-foundation";
#endif
        fprintf(stdout, "{\"protocols\":[\"TGR3\",\"TGR4\"],"
               "\"ready_handshake\":true,\"aac_backend\":\"%s\","
               "\"aac_available\":%s,\"guest_execution\":\"%s\","
               "\"number_styles\":[\"off\",\"fix\",\"words\"],"
               "\"number_flags\":{\"fix\":2,\"words\":4},"
               "\"render_cli\":true,\"cancel_signal\":%s}\n", backend,
               available ? "true" : "false",
#ifdef TIGER_UC
               "unicorn",
#else
               "native",
#endif
#ifdef _WIN32
               "null"
#else
               "\"SIGUSR1\""
#endif
        );
        return 0;
    }
    if (argc > 1 && !strcmp(argv[1], "--aac-check")) {
        setvbuf(stderr, NULL, _IONBF, 0);
        return aac_check();
    }
    if (argc > 1 && !strcmp(argv[1], "--regex-check")) {
        setvbuf(stderr, NULL, _IONBF, 0);
        return re_check();
    }
    if (argc > 1 && !strcmp(argv[1], "--numbers-check")) {
        setvbuf(stderr, NULL, _IONBF, 0);
        return num_check();
    }
    /* `--numbers <style> <hex>`: one rewrite, printed.  The oracle harness
     * drives this a thousand times and diffs it against the Python.
     *
     * The text arrives HEX-ENCODED, which is not ceremony: this host speaks
     * MacRoman and a Windows command line does not, so an accented byte handed
     * over as an argument arrives re-encoded and the harness ends up diffing
     * its own transport.  It did, for 81 cases that printed identically. */
    if (argc > 3 && !strcmp(argv[1], "--numbers")) {
        const char *hex = argv[3];
        size_t hn = strlen(hex) / 2, k;
        char *in = (char *)malloc(hn + 1), *out;
        if (!in) return 2;
        for (k = 0; k < hn; k++) {
            unsigned byte = 0;
            sscanf(hex + k * 2, "%2x", &byte);
            in[k] = (char)byte;
        }
        in[hn] = 0;
        out = num_expand(in, num_style_of(argv[2]));
        /* fprintf(stdout), not printf: this file redefines printf onto stderr
         * so that no stray diagnostic can ever reach the serve protocol (see
         * [[stdout-was-the-protocol]]).  Here the answer IS the output, so it
         * has to name stdout out loud. */
        if (out) for (k = 0; out[k]; k++)
            fprintf(stdout, "%02x", (unsigned char)out[k]);
        free(in);
        free(out);
        return 0;
    }
#if defined(TIGER_UC) || defined(TIGER_LIB)
    /* Render through the Android synthesis API (tiger_host_jni.c) rather than
     * inline, and write the same wav -- so the on-device APK path is exercised
     * on the desktop and its output can be byte-diffed against the oracle.
     *   --jni-check <MacinTalk> <SpeechDictionary> <Voice.SpeechVoice>
     *               [creator-hex] [voice-id] [wpm] */
    if (argc > 4 && !strcmp(argv[1], "--jni-check")) {
        unsigned creator = (argc > 5) ? (unsigned)strtoul(argv[5], NULL, 16)
                                      : (unsigned)'mtk3';
        int voiceid = (argc > 6) ? atoi(argv[6]) : 1;
        /* Rate, so a desktop render can be compared with a device one.  The
         * Android service asks for a wpm -- 180 at speechRate 100 -- and
         * rendering here at the engine's own default instead compares two
         * different utterances and calls the difference a fault.  Voices do not
         * share a default: Vicki's is 180 and Fred's is not, which is exactly
         * the trap this argument exists to avoid. */
        int wpm = (argc > 7) ? atoi(argv[7]) : 0;
        const char *e = getenv("TIGER_TEXT");
        const char *txt = (e && *e) ? e : "Hello there.";
        short *pcm = 0; unsigned nf = 0;
        int rc = panthera_init(argv[2], argv[3]);
        if (rc) { fprintf(stderr, "panthera_init -> %d\n", rc); return 2; }
        rc = panthera_render(argv[4], creator, voiceid, txt, wpm, &pcm, &nf);
        if (rc) { fprintf(stderr, "panthera_render -> %d\n", rc); return 2; }
        free(pcm);
        if (g_pcm_n) write_wav("tiger-out.wav");
        fprintf(stderr, "jni-check: %u frames at %d Hz\n",
                nf, panthera_sample_rate());
        return 0;
    }
#endif
    if (argc > 1 && !strcmp(argv[1], "--libc-check")) {
#ifdef TIGER_UC
        uc_host_init();
        guest_statics_init();
#endif
        return libc_check();
    }
    /* The CFString formatter, on the four table names Lion's dictionary is
     * built out of; see panthera/tests/test_cf_format.py. */
    if (argc > 1 && !strcmp(argv[1], "--cf-check")) {
#ifdef TIGER_UC
        uc_host_init();
        guest_statics_init();
#endif
        setvbuf(stderr, NULL, _IONBF, 0);
        return cf_check();
    }
    /* Both `struct stat` layouts -- 10.5's and 10.6's `$INODE64` -- decoded at
     * the offsets the engines read; see panthera/tests/test_stat_layout.py. */
    if (argc > 1 && !strcmp(argv[1], "--stat-check")) {
        setvbuf(stderr, NULL, _IONBF, 0);
        return stat_check();
    }
    /* Lion's Accelerate surface -- the FFT its WSOLA correlates with, and the
     * vector helpers around it -- printed for numpy to check; see
     * panthera/tests/test_vdsp.py. */
    if (argc > 1 && (!strcmp(argv[1], "--linalg-check") || !strcmp(argv[1], "--sqlite-check"))) {
#ifdef TIGER_UC
        uc_host_init();
#endif
        return !strcmp(argv[1], "--sqlite-check") ? sqlite_check() : linalg_check();
    }
    if (argc > 1 && !strcmp(argv[1], "--vdsp-check")) {
#ifdef TIGER_UC
        uc_host_init();
        guest_statics_init();
#endif
        setvbuf(stderr, NULL, _IONBF, 0);
        return vdsp_check();
    }
    /* Print the compressed dyld info of one Mach-O and stop.  Compared
     * against tools/machodyld.py, which reads the same streams by a
     * different route; see panthera/tests/test_dyld_info.py. */
    if (argc > 2 && !strcmp(argv[1], "--dyld-check")) {
        setvbuf(stderr, NULL, _IONBF, 0);
        return dyld_check(argv[2]);
    }
    /* Unlike the checks above, this one needs a loaded engine and an open
     * channel, so it is picked up here and acted on after host_open. */
    if (argc > 1 && !strcmp(argv[1], "--pmod-check")) {
        pmodcheck = 1;
        argv++; argc--;                  /* shift so the paths line up */
        host_quiet();
    }
    if (argc > 1 && !strcmp(argv[1], "--serve")) {
        if (argc < 5) {
            fprintf(stderr, "usage: tiger_host --serve <MacinTalk> "
                            "<SpeechDictionary> <VoicesDir>\n");
            return 2;
        }
        servedir = argv[4];
        argv++; argc--;                  /* shift so the paths line up */
        host_quiet();
    }
    voicedir = (argc > 3) ? argv[3] : NULL;
    if (argc > 4 && !servedir) {
        creator = (unsigned)strtoul(argv[4], NULL, 16);
        spec_given = 1;
    }
    if (argc > 5 && !servedir) voiceid = atoi(argv[5]);

    /* Ask the voice bundle who it is, unless the command line already said.
     *
     * This used to default to 'mtk3' 1 whatever bundle it was pointed at, so
     * `tiger_host ... Vicki.SpeechVoice` told the engine "MacinTalk 3, voice
     * 1" and handed it Vicki -- a `meow` voice.  SEUseVoice returns 0 to
     * that, and the crash arrives much later and somewhere else, in
     * MEOWReader::GetWordEntry reading a field of an object the mtk3 path
     * never set up.  Leopard's AAC voices therefore "segfaulted on Linux",
     * which was true, and had nothing to do with Linux: the same command
     * crashes the same way on Windows, and serve mode -- which has always
     * called voice_spec -- was fine on both.
     *
     * The diagnostic entry point is where a wrong voice is hardest to notice
     * and most expensive to misread, since its whole job is to tell you what
     * the engine did.  So it now reads the VoiceDescription exactly as serve
     * mode does, and Fred remains the answer only when there is no bundle to
     * ask.  An explicit creator still wins: pointing the wrong engine at a
     * voice on purpose is a legitimate experiment, and it is how the above
     * was confirmed. */
    if (!servedir && !pmodcheck && voicedir && !spec_given) {
        unsigned c;
        int id;
        if (voice_spec(voicedir, &c, &id)) {
            creator = c;
            voiceid = id;
        } else {
            /* No VoiceDescription to read -- almost always a path that does
             * not exist.  Say so and stop, because the old behaviour here was
             * to quietly fall back to Fred and hand him the missing bundle,
             * which segfaults several thousand instructions later.  Every
             * MacinTalk voice bundle has this file; serve mode has always
             * required it. */
            setvbuf(stderr, NULL, _IONBF, 0);
            fprintf(stderr, "tiger_host: no voice at %s\n"
                            "  (expected %s/Contents/Resources/"
                            "VoiceDescription)\n", voicedir, voicedir);
            return 2;
        }
    }

    if (argc < 4) {
        setvbuf(stderr, NULL, _IONBF, 0);
        fprintf(stderr,
                "usage:\n"
                "  tiger_host <MacinTalk> <SpeechDictionary> "
                "<Voice.SpeechVoice> [creator-hex] [voice-id]\n"
                "      render one utterance and write tiger-out.wav\n"
                "      creator and id come from the voice bundle unless "
                "given\n"
                "  tiger_host --serve <MacinTalk> <SpeechDictionary> "
                "<VoicesDir>\n"
                "      stay resident and answer requests on stdin/stdout\n"
                "\n"
                "MacinTalk and SpeechDictionary come from your own Mac OS X\n"
                "10.4 install; nothing of Apple's ships with this.\n");
        return 2;
    }

    err = host_open(argv[1], argv[2]);
    if (err) goto report;

    if (servedir) {
        fprintf(stderr, "tiger_host: ready, voices in %s\n", servedir);
        return serve(&g_mt, g_chan, servedir);
    }

    if (pmodcheck) {
        if (!voicedir) {
            fprintf(stderr, "usage: tiger_host --pmod-check <MacinTalk> "
                            "<SpeechDictionary> <Voice.SpeechVoice>\n");
            return 2;
        }
        return pmod_check(&g_mt, g_chan, voicedir);
    }

    /* Pick a voice.  Apple's Speech Manager always does this before speaking,
     * and skipping it leaves the channel's voice pointer null -- which shows
     * up as a fault deep in the speak path rather than as an error here.
     *
     * Argument 2 is a VoiceSpec: creator OSType then id, big-endian in the
     * file but native here.  Proven at 0x5fbc, which compares its [0] and [4]
     * against the channel's +0xa8 and +0xac. */
    {
        typedef int (__cdecl *SEUseVoice_t)(void *chan, const void *spec,
                                            const void *bundle);
        struct { unsigned creator; int id; } spec;
        SEUseVoice_t use = (SEUseVoice_t)find_export(&g_mt, "_SEUseVoice");
        cfobj *bundle = cf_pinned(voicedir);
        spec.creator = creator;
        spec.id      = voiceid;
        if (!use) die("SEUseVoice not found");
        printf("\nSEUseVoice at %p, spec {'%c%c%c%c', %d}\n  bundle %s\n",
               (void *)use, (creator >> 24) & 0xff, (creator >> 16) & 0xff,
               (creator >> 8) & 0xff, creator & 0xff, voiceid, voicedir);
        err = call_aligned3((void *)use, g_chan, UC_IN(&spec, sizeof spec),
                            bundle);
        printf("  -> OSErr %d\n", err);
        if (err) goto report;
    }

    /* Speak.  The point of this call is not to hear anything -- nothing is
     * wired to an audio device yet -- but to find out which shims the render
     * path actually reaches.  That is the difference between AudioToolbox
     * being output plumbing we can replace and it being structural. */
    {
        /* Overridable, because "does the output track the input" is the
         * first question to ask of a voice that speaks the wrong words. */
        const char *envtext = getenv("TIGER_TEXT");
        static const char deftext[] = "Hello there.";
        const char *text = envtext && *envtext ? envtext : deftext;
        size_t textlen = strlen(text);
        speech_api api = speech_api_of(&g_mt);
        /* TIGER_RATE, in words per minute, so the amount of time-scaling can
         * be varied deliberately.
         *
         * Alex is concatenative: a rate the recordings were not made at means
         * MTMBModRateWsola has to stretch them, and that is the one stage
         * whose maths is ours rather than Apple's.  If the crackle follows the
         * amount of stretching, it is in there; if it is the same at every
         * rate, it is not. */
        {
            const char *rt = getenv("TIGER_RATE");
            if (rt && *rt) {
                typedef int (__cdecl *SESetInfo_t)(void *, unsigned,
                                                   const void *);
                SESetInfo_t setinfo =
                    (SESetInfo_t)find_export(&g_mt, "_SESetSpeechInfo");
                unsigned fixed = (unsigned)atoi(rt) << 16;   /* Fixed 16.16 */
                if (setinfo) {
                    int r = call_aligned3((void *)setinfo, g_chan,
                                          (void *)0x72617465u,
                                          UC_IN(&fixed, sizeof fixed));
                    printf("\nSESetSpeechInfo 'rate' %d wpm -> OSErr %d\n",
                           atoi(rt), r);
                }
            }
        }
        printf("\n%s, %d bytes of text\n", api.which, (int)textlen);
        err = speak_text(&api, g_chan, text, textlen);
        printf("  -> OSErr %d\n", err);

        /* SESpeakBuffer returns as soon as the utterance is accepted; the
         * slices arrive from the engine's own worker task.  Wait until they
         * stop coming rather than guessing a duration. */
        {
            unsigned last = 0, quiet = 0, ticks = 0;
            while (quiet < 40 && ticks < 300) {  /* <= 15 s, then give up */
                Sleep(50); ticks++;
                if (g_slices != last) { last = g_slices; quiet = 0; }
                else quiet++;
            }
            printf("  %u slice(s), %u frames total\n", g_slices, g_pcm_n);
            if (g_sc.magic || g_sc.sessions || g_sc.resets)
                printf("  [ac] %u decoder stream(s), %u reset(s)\n",
                       g_sc.sessions, g_sc.resets);
            if (g_pkts_fed)
                printf("  [ac] %u packets fed = %u samples of compressed "
                       "audio; %u samples handed to the engine (%+d)\n",
                       g_pkts_fed, g_pkts_fed * 1024, g_frames_out,
                       (int)g_frames_out - (int)(g_pkts_fed * 1024));
            /* The number that matters more than the deficit: a refill the
             * decoder answered with nothing is a **silent word**, and the
             * engine advances its clock over it regardless -- so the render
             * keeps its full length and simply has a hole. Nothing about the
             * duration, the peak or the roughness shows it. */
            if (g_ac_silent_streams)
                printf("  [ac] %u refill(s) produced NO audio -- that is a "
                       "hole in the speech, not a shorter render\n",
                       g_ac_silent_streams);
            if (g_preads)
                printf("  [io] %u pread(s), %u short\n",
                       g_preads, g_pread_short);
            if (g_dup_slices)
                printf("  [au] %u repeated slice(s) refused\n", g_dup_slices);
            if (g_fstat_n)
                printf("  [flt] engine float output roughness %.3f over %u "
                       "samples\n",
                       g_fstat_d / (g_fstat_abs > 1e-9 ? g_fstat_abs : 1.0),
                       g_fstat_n);
            /* Silence on the engine's side of the fence.  Roughness cannot
             * see it -- roughness *improves* as silence grows, which is how
             * Lion's Alex measured cleaner than Leopard's while saying a
             * third of the words. */
            if (g_fstat_n)
                printf("  [flt] engine float output is %.1f%% exact zero "
                       "(%u of %u samples)\n",
                       100.0 * g_fstat_zero / (double)g_fstat_n,
                       g_fstat_zero, g_fstat_n);
            if (g_fstat_slices)
                printf("  [flt] of %u slice(s): %u entirely silent, %u more "
                       "than a tenth silent\n",
                       g_fstat_slices, g_fstat_dead, g_fstat_holed);
            if (g_pcmstat_n)
                printf("  [pcm] decoder output roughness %.3f over %u samples "
                       "(clean speech is about 0.10)\n",
                       g_pcmstat_d / (g_pcmstat_abs > 1.0 ? g_pcmstat_abs : 1.0),
                       g_pcmstat_n);
        }
        if (g_pcm_n) write_wav("tiger-out.wav");
    }

report:
    printf("\nshims actually called:\n");
    {
        int total = 0;
        for (i = 0; i < g_nmissing; i++) {
            if (!g_missing_hits[i]) continue;
            printf("  %6d x  %s\n", g_missing_hits[i], g_missing[i]);
            total++;
        }
        printf("  (%d of %d stubbed symbols were reached)\n", total,
               g_nmissing);
    }
    return 0;
}

#endif  /* !PT_DLL */

/* The DLL's entry points, which is the same `serve` over a pair of private
 * pipes instead of the process's own.  Last, because it calls `host_open` and
 * `serve` and this is one translation unit. */
#ifdef PT_DLL
#include "tiger_host_api.c"
#endif

/* The in-process synthesis API the Android JNI layer calls -- and the same one
 * a Linux port would drive into ALSA or PipeWire, since it hands back raw PCM
 * and leaves playback to the caller.  Present in every emulated build (the .so
 * binds to it, and --jni-check reaches it on the desktop); TIGER_LIB pulls it
 * into a native (non-emulated) library build too. */
#if defined(TIGER_UC) || defined(TIGER_LIB)
#include "tiger_host_jni.c"
#endif
