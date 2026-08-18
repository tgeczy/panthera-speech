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
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>
#include <math.h>
#include <io.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <ctype.h>
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
} image;

/* ---- shared state ------------------------------------------------------ */
static image *g_primary;        /* MacinTalk; the image addresses resolve against */
static int g_verbose = 1;
static unsigned g_mp_waits;     /* how many times a worker has blocked */
static volatile long g_stopped; /* AUGraphStop: the engine's end-of-utterance */

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

static unsigned bswap(unsigned v)
{
    return (v >> 24) | ((v >> 8) & 0xff00) | ((v << 8) & 0xff0000) | (v << 24);
}


/* The rest of the host, in the order it has to be compiled.  One
 * translation unit on purpose: these parts share a great deal of static
 * state and splitting them into real objects would mean publishing all of
 * it.  This way the files are readable and the compiler still sees exactly
 * what it saw when every one of these lines was debugged. */
#include "tiger_host_shims.c"
#include "tiger_host_cf.c"
#include "tiger_host_files.c"
#include "tiger_host_audio.c"
#include "tiger_host_aac.c"
#include "tiger_host_cxx.c"
#include "tiger_host_accel.c"
#include "tiger_host_shimtab.c"
#include "tiger_host_fault.c"
#include "tiger_host_macho.c"
#include "tiger_host_serve.c"

/* ---- main -------------------------------------------------------------- */

typedef int (__cdecl *SEOpen_t)(void **chan);

int main(int argc, char **argv)
{
    image mt, sd, ls;
    int have_ls = 0;
    SEOpen_t open_chan;
    void *chan = NULL;
    int err, i;
    /* Defaults speak Fred, because he is the voice everyone means.  The ids
     * come from each bundle's VoiceDescription: 'mtk3' 1 is Fred, 'gala' 100
     * is Bruce, 'meow' 200 is Vicki. */
    const char *voicedir;
    const char *servedir = NULL;
    unsigned creator = 'mtk3';
    int voiceid = 1;

    /* --serve <MacinTalk> <SpeechDictionary> <VoicesDir> : stay resident and
     * answer requests on stdin/stdout.  Otherwise render one utterance and
     * write a wav, which is the shape that made every fix above findable. */
    if (argc > 1 && !strcmp(argv[1], "--aac-check")) {
        setvbuf(stderr, NULL, _IONBF, 0);
        return aac_check();
    }
    if (argc > 1 && !strcmp(argv[1], "--serve")) {
        if (argc < 5) {
            fprintf(stderr, "usage: tiger_host --serve <MacinTalk> "
                            "<SpeechDictionary> <VoicesDir>\n");
            return 2;
        }
        servedir = argv[4];
        argv++; argc--;                  /* shift so the paths line up */
        /* Quiet from the very first line, not from the point serve() takes
         * over: the driver puts everything this writes into NVDA's log, and
         * the loader's commentary is several hundred lines of it. */
        g_verbose = 0;
    }
    voicedir = (argc > 3) ? argv[3] : NULL;
    if (argc > 4 && !servedir) creator = (unsigned)strtoul(argv[4], NULL, 16);
    if (argc > 5 && !servedir) voiceid = atoi(argv[5]);

    /* Unbuffered stderr: this program's other job is to crash informatively,
     * and buffered output is discarded when it does. */
    setvbuf(stderr, NULL, _IONBF, 0);
    { const char *e = getenv("TIGER_SPEED");
      if (e && atof(e) > 0.0) g_speed = atof(e);
      g_pace = 100.0 / g_speed;              /* pacer follows the clock */
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

    if (argc < 4) {
        fprintf(stderr,
                "usage:\n"
                "  tiger_host <MacinTalk> <SpeechDictionary> "
                "<Voice.SpeechVoice> [creator-hex] [voice-id]\n"
                "      render one utterance and write tiger-out.wav\n"
                "  tiger_host --serve <MacinTalk> <SpeechDictionary> "
                "<VoicesDir>\n"
                "      stay resident and answer requests on stdin/stdout\n"
                "\n"
                "MacinTalk and SpeechDictionary come from your own Mac OS X\n"
                "10.4 install; nothing of Apple's ships with this.\n");
        return 2;
    }

    g_thunks = (unsigned char *)VirtualAlloc(NULL, MAX_MISSING * THUNK_SZ,
                                             MEM_RESERVE | MEM_COMMIT,
                                             PAGE_EXECUTE_READWRITE);
    if (!g_thunks) die("cannot allocate thunk area");

    AddVectoredExceptionHandler(1, on_fault);

    /* An optional third image, which so far only Leopard has wanted.  Loaded
     * first so its initializers run before anything that calls into it. */
    {
        char lspath[CFPATH];
        if (find_libstdcxx(argv[2], lspath, sizeof(lspath))) {
            load(&ls, lspath);
            g_images[g_nimages++] = &ls;
            have_ls = 1;
        } else if (g_verbose) {
            printf("no libstdc++ beside the engine; Tiger needs none\n");
        }
    }

    load(&sd, argv[2]);
    load(&mt, argv[1]);
    g_images[g_nimages++] = &sd;
    g_images[g_nimages++] = &mt;
    g_primary = &mt;

    /* The dictionary bundle is the directory holding the framework binary:
     * .../SpeechDictionary.framework/Versions/A, whose Resources carry the
     * 2.1 MB StdDictionary. */
    {
        char dir[CFPATH];
        char *cut;
        strncpy(dir, argv[2], sizeof(dir) - 1);
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

    if (have_ls) {
        if (g_verbose) printf("binding libstdc++:\n");
        bind(&ls, NULL);
        apply_ext_relocs(&ls, NULL);
    }
    if (g_verbose) printf("binding SpeechDictionary:\n");
    bind(&sd, NULL);
    apply_ext_relocs(&sd, NULL);
    if (g_verbose) printf("binding MacinTalk:\n");
    bind(&mt, &sd);
    apply_ext_relocs(&mt, &sd);

    if (g_verbose) printf("running initializers:\n");
    if (have_ls) run_initializers(&ls);
    run_initializers(&sd);
    run_initializers(&mt);

    open_chan = (SEOpen_t)find_export(&mt, "_SEOpenSpeechChannel");
    if (!open_chan) die("SEOpenSpeechChannel not found");
    if (g_verbose) printf("\nSEOpenSpeechChannel at %p\n", (void *)open_chan);

    /* Every entry into the engine goes through an aligning trampoline:
     * Darwin i386 guarantees ESP is 16-byte aligned at each call and
     * Leopard's engine spends that guarantee on movapd. */
    err = call_aligned1((void *)open_chan, &chan);
    if (g_verbose) printf("  -> OSErr %d, channel %p\n", err, chan);
    if (err || !chan) goto report;

    if (servedir) {
        fprintf(stderr, "tiger_host: ready, voices in %s\n", servedir);
        return serve(&mt, chan, servedir);
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
        SEUseVoice_t use = (SEUseVoice_t)find_export(&mt, "_SEUseVoice");
        cfobj *bundle = cf_pinned(voicedir);
        spec.creator = creator;
        spec.id      = voiceid;
        if (!use) die("SEUseVoice not found");
        printf("\nSEUseVoice at %p, spec {'%c%c%c%c', %d}\n  bundle %s\n",
               (void *)use, (creator >> 24) & 0xff, (creator >> 16) & 0xff,
               (creator >> 8) & 0xff, creator & 0xff, voiceid, voicedir);
        err = call_aligned3((void *)use, chan, &spec, bundle);
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
        typedef int (__cdecl *SESpeak_t)(void *chan, const void *buf,
                                         long len, long flags);
        SESpeak_t speak = (SESpeak_t)find_export(&mt, "_SESpeakBuffer");
        if (!speak) die("SESpeakBuffer not found");
        printf("\nSESpeakBuffer at %p, %d bytes of text\n",
               (void *)speak, (int)(sizeof(text) - 1));
        err = call_aligned4((void *)speak, chan, (void *)text,
                            (void *)textlen, (void *)0);
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
