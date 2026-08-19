/* tiger_host_fault.c -- fault reporting, surviving divide-by-zero, and thunks.
 *
 * Part of tiger_host.c, which includes it; see there for why this is one
 * translation unit. */

/* The symbol containing `addr`, or NULL.  Both images carry a full symbol
 * table -- these are unstripped bundles with C++ names in them -- so a fault
 * can name the function it happened in rather than only the file.  Linear over
 * a few thousand symbols, which is free at the one moment it runs. */
static const char *nearest_symbol(const image *im, unsigned addr,
                                  unsigned *symaddr)
{
    const char *best = NULL;
    unsigned bestaddr = 0, k;
    for (k = 0; k < im->nsyms; k++) {
        const nlist *sy = &im->syms[k];
        if (sy->n_type & N_STAB) continue;
        if ((sy->n_type & N_TYPE) != 0x0e) continue;      /* N_SECT */
        if (!sy->n_value || sy->n_value > addr) continue;
        if (sy->n_value >= bestaddr) {
            bestaddr = sy->n_value;
            best = im->strs + sy->n_strx;
        }
    }
    if (best && symaddr) *symaddr = bestaddr;
    return best;
}

/* ---- fault reporting --------------------------------------------------- */
/*
 * A bare access violation here is nearly useless: the address means nothing
 * until it is expressed as an offset into one of the two images, which is what
 * makes it findable in a disassembly.  Doing that translation at the moment of
 * the fault has already saved more time than it cost to write.
 */
static image *g_images[4];
static int    g_nimages;

/* ---- surviving the engine's own divide by zero ------------------------- */
/*
 * MacinTalk 3 divides by (index2 - index1) when interpolating segment
 * durations, and above roughly 320 words per minute those two indices can
 * collapse onto each other.  On PowerPC `divw` by zero does not trap -- it
 * leaves an undefined result and carries on -- so this latent bug was harmless
 * on every Mac Apple shipped it for.  On x86 `idiv` raises #DE and the process
 * dies, which the user sees as a chunk of speech silently missing.
 *
 * So do what the original hardware did: give the division a harmless divisor
 * and let the engine continue.  Writing 1 and re-executing beats skipping the
 * instruction, because the quotient stays defined and in range.
 *
 * The alternative was to cap the rate, and that is a real cost -- fast speech
 * is exactly what experienced screen reader users want.
 */
static int reg_of(const CONTEXT *c, int i)
{
    switch (i) {
    case 0: return (int)c->Eax; case 1: return (int)c->Ecx;
    case 2: return (int)c->Edx; case 3: return (int)c->Ebx;
    case 4: return (int)c->Esp; case 5: return (int)c->Ebp;
    case 6: return (int)c->Esi; default: return (int)c->Edi;
    }
}

/* Find the divisor of a div/idiv at `pc`.  -> its address, or NULL if this is
 * not a shape we understand, in which case the fault is left to stand. */
static void *divisor_operand(const unsigned char *pc, CONTEXT *c, int *width)
{
    unsigned char modrm;
    int mod, rm, base;
    const unsigned char *p = pc;

    *width = 4;
    while (*p == 0x66 || *p == 0x67 || *p == 0x2e || *p == 0x36 ||
           *p == 0x3e || *p == 0x26 || *p == 0x64 || *p == 0x65) {
        if (*p == 0x66) *width = 2;
        p++;
    }
    if (*p == 0xf6) *width = 1;
    else if (*p != 0xf7) return NULL;
    p++;

    modrm = *p++;
    if (((modrm >> 3) & 7) < 6) return NULL;      /* not div or idiv */
    mod = modrm >> 6;
    rm = modrm & 7;

    if (mod == 3) return NULL;                    /* register divisor */
    if (rm == 4) return NULL;                     /* SIB: not seen here */
    if (mod == 0 && rm == 5)
        return (void *)(intptr_t)(*(const int *)p);
    base = reg_of(c, rm);
    if (mod == 1) return (void *)(intptr_t)(base + (signed char)*p);
    if (mod == 2) return (void *)(intptr_t)(base + *(const int *)p);
    return (void *)(intptr_t)base;
}

static volatile long g_divzero;

static int survive_divide_by_zero(EXCEPTION_POINTERS *ep)
{
    CONTEXT *c = ep->ContextRecord;
    int width = 4;
    void *at = divisor_operand((const unsigned char *)c->Eip, c, &width);
    if (!at) return 0;
    if (IsBadWritePtr(at, (ULONG)width)) return 0;
    if (width == 1) *(unsigned char *)at = 1;
    else if (width == 2) *(unsigned short *)at = 1;
    else *(unsigned *)at = 1;
    if (InterlockedIncrement(&g_divzero) == 1)
        if (g_verbose) printf("  [engine] divide by zero at MacinTalk + 0x%x -- divisor set "
               "to 1 and resumed (PowerPC would not have trapped)\n",
               g_primary ? (unsigned)(c->Eip - g_primary->slide)
                         : (unsigned)c->Eip);
    return 1;
}

/* ---- naming an address that is not in an engine image ------------------ */
/*
 * `g_images` holds the Mach-O images and nothing else, so a fault anywhere in
 * the host's own code printed a bare address and no name.  That includes every
 * CRT function the shim table hands the engine -- `strlen`, `memcpy`, `strcmp`
 * -- which is where a crash lands when a stubbed shim answers NULL and the
 * engine passes that answer straight on.  The report from the field was:
 *
 *     tiger_host: CRASHED -- exception c0000005 at 009f5b70
 *     tiger_host:   reading address 00000000
 *
 * Two lines, no name, nothing to look up: an address means nothing without the
 * base it came from, and the base was not in the report.
 *
 * Windows knows it.  The address is inside a loaded module and
 * `GetModuleHandleEx` will say which one, so the offset becomes an RVA that a
 * map file resolves -- which turned that report into `strlen + 0x30`, and the
 * diagnosis with it.
 */
static int host_module_for(unsigned addr, char *name, size_t namesz,
                           unsigned *rva)
{
    HMODULE mod = NULL;
    char path[MAX_PATH];
    const char *base;
    if (!GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                            GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                            (LPCSTR)(ULONG_PTR)addr, &mod) || !mod)
        return 0;
    if (!GetModuleFileNameA(mod, path, (DWORD)sizeof(path)))
        return 0;
    base = strrchr(path, '\\');
    if (!base) base = strrchr(path, '/');
    _snprintf(name, namesz, "%s", base ? base + 1 : path);
    name[namesz - 1] = 0;
    *rva = addr - (unsigned)(ULONG_PTR)mod;
    return 1;
}

/* Every line of a crash report carries the program's own name.
 *
 * The driver files anything without that prefix at debug level, and that rule
 * is right for the loader's ordinary commentary -- several hundred lines an
 * utterance.  A crash is the one place it is wrong.  It happens once, it costs
 * the user an utterance, and the lines that say *where* are the entire value
 * of the report.  Without the prefix the only thing that ever reached a log
 * was the two lines above; the register dump, the stack walk with symbol
 * names and the missing-shim tally -- all of it already written, all of it
 * printed -- went to debug and nobody ever saw them.
 *
 * A line assembled from several calls needs the prefix only on its first
 * fragment: the driver reads whole lines. */
#define crashf(...) (fprintf(stderr, "tiger_host: "), fprintf(stderr, __VA_ARGS__))

static volatile LONG g_faulted;
/* Which thread owns the report, so a fault raised *by the reporter* can be
 * told apart from another engine thread piling in behind it.  See on_fault. */
static volatile DWORD g_faulting_thread;
/* Raised only around the two walks below that read addresses nothing has
 * vouched for.  It is what lets a nested fault be handed to ordinary SEH --
 * where those walks have an __except waiting -- instead of ending the report. */
static volatile long g_in_walk;

static LONG CALLBACK on_fault(EXCEPTION_POINTERS *ep)
{
    unsigned pc = ep->ContextRecord->Eip;
    unsigned code = ep->ExceptionRecord->ExceptionCode;
    int i;

    /* The engine's own divide by zero is survivable and expected at high
     * speech rates; fix the divisor and resume rather than dying. */
    if (code == EXCEPTION_INT_DIVIDE_BY_ZERO && survive_divide_by_zero(ep))
        return EXCEPTION_CONTINUE_EXECUTION;

    /* Report the first fault only.  The engine runs worker threads and a
     * pacer, and once one of them is wedged the others pile in and bury the
     * one report that mattered. */
    if (InterlockedExchange(&g_faulted, 1)) {
        /* **A reporter that faults must not park itself.**
         *
         * This is a vectored handler, so it sees every fault in the process
         * first -- including one raised by this function while it is writing
         * the report.  `Sleep(INFINITE)` on that path hung the host for ever,
         * which is worse than the crash it was describing: the process never
         * exits, the driver blocks reading a pipe that will never answer, and
         * the screen reader goes quiet with half a report and no exit code.
         *
         * Reproduced: the frame walk below reads [ebp+0c], finds 53005327
         * there, and hands it to IsBadReadPtr, which faults.  The report
         * stopped mid-line and the process sat there until it was killed.
         *
         * Inside one of the guarded walks, hand the fault to ordinary SEH:
         * the walk's own __except ends that section and the rest of the
         * report -- the stack trace, the missing-shim tally -- still gets
         * written.  Anywhere else in the reporter there is nothing to catch
         * it, and leaving it to the default handler risks a WER dialog this
         * process has no way to dismiss, so end it here instead. */
        if (GetCurrentThreadId() == g_faulting_thread) {
            if (g_in_walk) return EXCEPTION_CONTINUE_SEARCH;
            ExitProcess(3);
        }
        /* Another engine thread arriving behind the first: park it so the
         * report is not interleaved with a second one. */
        Sleep(INFINITE);
    }
    g_faulting_thread = GetCurrentThreadId();
    /* Prefixed with the program's own name, and deliberately so: the driver
     * sends everything this writes to NVDA's log, but files anything without
     * that prefix at debug level -- which is off by default.  A crash report
     * that only appears when the user has already turned on debug logging is
     * a crash report nobody will ever send.  Every line of the report below
     * carries it too, through crashf: a crash happens once, and the lines
     * that say *where* are the reason to write any of this down. */
    fprintf(stderr, "tiger_host: CRASHED -- exception %08x at %08x\n", code, pc);
    printf("\n*** exception %08x at %08x\n", code, pc);
    if (code == EXCEPTION_ACCESS_VIOLATION)
        fprintf(stderr, "tiger_host:   %s address %08x\n",
               ep->ExceptionRecord->ExceptionInformation[0] ? "writing"
                                                            : "reading",
               (unsigned)ep->ExceptionRecord->ExceptionInformation[1]);
    {
        int named = 0;
        for (i = 0; i < g_nimages; i++) {
            image *im = g_images[i];
            if (pc >= im->lo + im->slide && pc < im->hi + im->slide) {
                fprintf(stderr, "tiger_host:   in %s + 0x%x\n",
                        im->path, pc - im->slide);
                named = 1;
            }
        }
        if (!named) {
            char mname[MAX_PATH];
            unsigned rva;
            if (host_module_for(pc, mname, sizeof(mname), &rva))
                fprintf(stderr, "tiger_host:   in %s + 0x%x -- the host's own "
                                "code, not the engine's\n", mname, rva);
            else
                fprintf(stderr, "tiger_host:   in no loaded module\n");
        }
    }
    crashf("    eax=%08x ebx=%08x ecx=%08x edx=%08x\n",
           ep->ContextRecord->Eax, ep->ContextRecord->Ebx,
           ep->ContextRecord->Ecx, ep->ContextRecord->Edx);
    crashf("    esi=%08x edi=%08x ebp=%08x esp=%08x\n",
           ep->ContextRecord->Esi, ep->ContextRecord->Edi,
           ep->ContextRecord->Ebp, ep->ContextRecord->Esp);
    /* When the pc is not inside any image, the call went through a bad
     * pointer and the interesting thing is the caller's arguments -- the
     * object whose dispatch table we jumped through.  ebp is still the
     * caller's frame, because the callee never got to push its own. */
    g_in_walk = 1;
    __try {
        const unsigned *fp = (const unsigned *)ep->ContextRecord->Ebp;
        int inside = 0, a;
        for (i = 0; i < g_nimages; i++)
            if (pc >= g_images[i]->lo + g_images[i]->slide &&
                pc < g_images[i]->hi + g_images[i]->slide) inside = 1;
        if (!inside && !IsBadReadPtr(fp, 32)) {
            for (a = 2; a <= 4; a++) {
                const unsigned *obj = (const unsigned *)fp[a];
                crashf("    [ebp+%02x] = %08x", a * 4, fp[a]);
                if (!IsBadReadPtr(obj, 4)) {
                    const unsigned *tbl = (const unsigned *)obj[0];
                    printf("  -> first word %08x", obj[0]);
                    if (!IsBadReadPtr(tbl, 24)) {
                        int w;
                        printf("  table:");
                        for (w = 0; w < 6; w++) printf(" %08x", tbl[w]);
                    }
                }
                printf("\n");
            }
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        printf("\n");
        crashf("    (the frame walk faulted; that part of the report is "
               "missing)\n");
    }
    g_in_walk = 0;

    /* A call through a null pointer lands at pc 0 with nothing to name, so the
     * only way to find the caller is to read the return address back off the
     * stack.  Anything on it that lands inside an image is worth printing.
     *
     * Two refinements, both bought by an hour of reading the wrong thing:
     *
     * Most values on the stack that point into an image are *not* return
     * addresses.  This code is position independent, so every function begins
     * by calling the next instruction and popping it into ebx -- and that PIC
     * base gets spilled.  A return address has a `call` immediately before it;
     * a spilled PIC base has a `pop`.  Checking the preceding bytes separates
     * them, and mistaking one for the other sent me looking for a caller in a
     * function that never called anything.
     *
     * And naming the offset is not enough when the image has a symbol table.
     * The nearest preceding symbol turns "SpeechDictionary + 0xf59" into
     * "SLCartDict::SymtabRead + 0x17", which is the difference between a
     * morning of disassembly and a glance. */
    g_in_walk = 1;
    __try {
        const unsigned *sp = (const unsigned *)ep->ContextRecord->Esp;
        int k;
        crashf("    stack:\n");
        for (k = 0; k < 64; k++) {
            unsigned v = sp[k];
            for (i = 0; i < g_nimages; i++) {
                image *im = g_images[i];
                const unsigned char *before;
                const char *base, *sym;
                unsigned symaddr;
                int call = 0;
                if (v < im->lo + im->slide || v >= im->hi + im->slide) continue;
                /* A near call is 5 bytes (e8 rel32); an indirect call through
                 * a register or memory is 2 to 7 and always has ff /2 or /3
                 * somewhere in that window. */
                before = (const unsigned char *)v;
                if (!IsBadReadPtr(before - 7, 7)) {
                    if (before[-5] == 0xe8) call = 1;
                    else {
                        int b;
                        for (b = 2; b <= 7 && !call; b++)
                            if (before[-b] == 0xff &&
                                ((before[-b + 1] >> 3) & 7) >= 2 &&
                                ((before[-b + 1] >> 3) & 7) <= 3)
                                call = 1;
                    }
                }
                if (!call) break;               /* a spilled PIC base, not a caller */
                base = strrchr(im->path, '/');
                sym = nearest_symbol(im, v - im->slide, &symaddr);
                if (sym)
                    crashf("      [esp+%02x] %08x  %s + 0x%x  <- %s + 0x%x\n",
                           k * 4, v, base ? base + 1 : im->path, v - im->slide,
                           sym, (v - im->slide) - symaddr);
                else
                    crashf("      [esp+%02x] %08x  %s + 0x%x\n", k * 4, v,
                           base ? base + 1 : im->path, v - im->slide);
                break;
            }
        }
    } __except (EXCEPTION_EXECUTE_HANDLER) {
        crashf("    (the stack walk faulted; that part of the report is "
               "missing)\n");
    }
    g_in_walk = 0;
    for (i = 0; i < g_nmissing; i++)
        if (g_missing_hits[i])
            crashf("    shim %6d x %s -- stubbed out, and the engine "
                   "called it\n", g_missing_hits[i], g_missing[i]);
    fflush(stdout);
    ExitProcess(3);
    return EXCEPTION_CONTINUE_SEARCH;
}

/* ---- thunks ------------------------------------------------------------ */
/*
 *   push imm32 <index>
 *   call rel32 shim_missing        ; cdecl, returns 0 in eax
 *   add  esp, 4
 *   ret
 *
 * ebx/esi/edi/ebp survive because shim_missing is an ordinary C function, and
 * cdecl lets the thunk clobber eax/ecx/edx freely.
 */
static unsigned char *g_thunks;
static int g_nthunks;
#define THUNK_SZ 16

static void *make_thunk(const char *name)
{
    unsigned char *t = g_thunks + (size_t)g_nthunks * THUNK_SZ;
    int idx = g_nmissing;
    if (g_nmissing >= MAX_MISSING) die("too many missing symbols");
    g_missing[g_nmissing++] = name;
    t[0] = 0x68; *(int *)(t + 1) = idx;                       /* push idx   */
    t[5] = 0xe8;
    *(int *)(t + 6) = (int)((unsigned char *)shim_missing - (t + 10));
    t[10] = 0x83; t[11] = 0xc4; t[12] = 0x04;                 /* add esp,4  */
    t[13] = 0xc3;                                             /* ret        */
    g_nthunks++;
    return t;
}
