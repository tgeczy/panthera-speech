/* tiger_host_cxx.c -- the libstdc++ and libSystem that Leopard's build wants.
 *
 * Part of tiger_host.c, which includes it; see there for why this is one
 * translation unit. */

/* ---- the little of libstdc++ that Leopard's engine needs --------------- */
/*
 * Tiger's MacinTalk carries its own C++ runtime; Leopard's links against
 * /usr/lib/libstdc++.6.dylib and imports 25 symbols from it.  Most are error
 * and debug paths -- `logic_error`, `__throw_bad_alloc`, the ostringstream the
 * engine's DebugLog builds its messages in -- and a thunk returning zero is a
 * fine answer for those as long as nothing calls them.
 *
 * `operator new` is not one of those.  A thunk returns 0, the engine writes
 * through it, and the whole thing stops in SEOpenSpeechChannel before it has
 * done anything.  These are the ones worth writing out.
 *
 * If std::string ever turns out to be on a path that matters, the answer is
 * not to reimplement it -- GCC 4.0.1's `basic_string` layout would have to
 * match exactly, and the engine has inlined code that touches it -- but to
 * load Leopard's own libstdc++ as a third image, the way SpeechDictionary is
 * already loaded.
 */
/* operator new returns memory the guest CONSTRUCTS OBJECTS IN and dereferences
 * (unlike a cfobj, which the guest holds opaquely), so under emulation it must
 * come from the guest arena, not the host heap.  Tiger never reached here -- its
 * operator new is internal to MacinTalk and already routes through the guest's
 * own _malloc -- but Leopard imports libstdc++'s __Znwm, which lands on this. */
static void * __cdecl sh_cxx_new(unsigned n)
{
#ifdef TIGER_UC
    void *p = sh_uc_malloc(n ? n : 1);
#else
    void *p = malloc(n ? n : 1);
#endif
    if (!p) die("out of memory: the engine asked for %u bytes", n);
    return p;
}
#ifdef TIGER_UC
static void __cdecl sh_cxx_delete(void *p) { sh_uc_free(p); }
#else
static void __cdecl sh_cxx_delete(void *p) { free(p); }
#endif

/* std::_List_node_base is two pointers, {next, prev}, and its members are the
 * plain linked-list splices.  Written out rather than thunked because a list
 * that does not unhook corrupts itself quietly. */
typedef struct list_node { gptr next, prev; } list_node;
static list_node *list_at(gptr p) { return (list_node *)GHOST(p); }

static void __cdecl sh_list_unhook(list_node *self)
{
    list_at(self->prev)->next = self->next;
    list_at(self->next)->prev = self->prev;
}
static void __cdecl sh_list_hook(list_node *self, list_node *pos)
{
    self->next = GP(pos);
    self->prev = pos->prev;
    list_at(pos->prev)->next = GP(self);
    pos->prev = GP(self);
}
static void __cdecl sh_list_swap(list_node *x, list_node *y)
{
    if (x->next != GP(x)) {
        if (y->next != GP(y)) {
            gptr t;
            t = x->next; x->next = y->next; y->next = t;
            t = x->prev; x->prev = y->prev; y->prev = t;
            list_at(x->next)->prev = list_at(x->prev)->next = GP(x);
            list_at(y->next)->prev = list_at(y->prev)->next = GP(y);
        } else {
            y->next = x->next;
            y->prev = x->prev;
            list_at(y->next)->prev = list_at(y->prev)->next = GP(y);
            x->next = x->prev = GP(x);
        }
    } else if (y->next != GP(y)) {
        x->next = y->next;
        x->prev = y->prev;
        list_at(x->next)->prev = list_at(x->prev)->next = GP(x);
        y->next = y->prev = GP(y);
    }
}

/* ---- the rest of libSystem, as Leopard's build asks for it ------------- */
/*
 * Tiger's engine reached 44 undefined symbols and actually called 6 of them.
 * Leopard's links against 236 more, most of which are error paths, the
 * `say -o` file-writing path, or C++ ABI machinery for exceptions that never
 * get thrown.  Rather than guess which, the cheap ones are written out here
 * and the fault report's "shim N x name" list says which of the rest the
 * engine really reaches.  Measuring the executed surface beats reading the
 * linked one.
 */
#define COND_MAGIC 0x54494743u          /* 'TIGC' */
#if GUEST_LOW
typedef struct { unsigned magic; } cnd;
static CONDITION_VARIABLE *cnd_native(cnd *c)
{ return &guest_sync_get(c, 1)->native.cond; }
static void cnd_ready(cnd *c) { (void)cnd_native(c); c->magic = COND_MAGIC; }
static int __cdecl sh_cond_init(void *c, void *attr)
{ (void)attr; guest_sync_destroy(c, 1); cnd_ready((cnd *)c); return 0; }
#else
typedef struct { unsigned magic; CONDITION_VARIABLE cv; } cnd;
static CONDITION_VARIABLE *cnd_native(cnd *c) { return &c->cv; }
static void cnd_ready(cnd *c)
{
    if (c->magic != COND_MAGIC) {
        InitializeConditionVariable(&c->cv);
        c->magic = COND_MAGIC;
    }
}
static int __cdecl sh_cond_init(void *c, void *attr)
{ (void)attr; ((cnd *)c)->magic = 0; cnd_ready((cnd *)c); return 0; }
#endif
typedef char guest_condition_must_fit[sizeof(cnd) <= 28 ? 1 : -1];
static int __cdecl sh_cond_wait(void *c, void *m)
{
    cnd_ready((cnd *)c);
    mtx_ready((mtx *)m);
    {   double t0 = tick_ms();
        g_w_cnd_n++;
        SleepConditionVariableCS(cnd_native((cnd *)c), mtx_native((mtx *)m), INFINITE);
        g_w_cnd_ms += tick_ms() - t0; }
    return 0;
}
static int __cdecl sh_cond_signal(void *c)
{ cnd_ready((cnd *)c); WakeConditionVariable(cnd_native((cnd *)c)); return 0; }
static int __cdecl sh_cond_destroy(void *c)
{
#if GUEST_LOW
    guest_sync_destroy(c, 1);
    ((cnd *)c)->magic = 0;
#else
    (void)c;
#endif
    return 0;
}
static int __cdecl sh_mutex_destroy(void *m)
{
#if GUEST_LOW
    guest_sync_destroy(m, 0);
    ((mtx *)m)->magic = 0;
#else
    (void)m;
#endif
    return 0;
}
static unsigned __cdecl sh_pthread_self(void) { return GetCurrentThreadId(); }

/* sh_stat sits with the rest of the file layer, in tiger_host_files.c: what it
 * reports about a file's identity is a file-layer concern, and a subtle one. */

static unsigned g_preads, g_pread_bytes, g_pread_short;

/* pread has to be atomic, not merely offset-correct.
 *
 * The obvious implementation -- seek, read, seek back -- is neither. Alex runs
 * two worker tasks alongside the main thread and they read his 701 MB sample
 * bank through one descriptor: the engine maps only the first 77 MB, which is
 * the index, and pulls every waveform grain out of the rest with pread. Two of
 * those overlapping meant one thread moved the file position out from under
 * the other, and the grain that came back belonged somewhere else. The voice
 * stayed recognisably Alex, because the bytes were still his recordings; they
 * were simply the wrong ones, and it stuttered its way through the wrong word.
 *
 * ReadFile with an OVERLAPPED offset reads from where it is told without
 * depending on the shared position, which is what Darwin's pread promises.
 */
static int __cdecl sh_pread(int fd, void *buf, unsigned n,
                            unsigned off_lo, unsigned off_hi)
{
    long long want = ((long long)off_hi << 32) | (unsigned)off_lo;
    HANDLE h = (HANDLE)_get_osfhandle(fd);
    OVERLAPPED ov;
    DWORD got = 0;

    if (h == INVALID_HANDLE_VALUE) return -1;
    memset(&ov, 0, sizeof(ov));
    ov.Offset     = (DWORD)off_lo;
    ov.OffsetHigh = (DWORD)off_hi;
    if (!ReadFile(h, buf, n, &got, &ov) && GetLastError() != ERROR_HANDLE_EOF)
        return -1;

    g_preads++;
    if (got > 0) g_pread_bytes += got;
    /* A short read here is invisible and catastrophic: the engine decodes a
     * truncated grain, which still sounds like speech and is the wrong length.
     * Never checked before, so check it loudly. */
    if (got != n) {
        g_pread_short++;
        if (g_pread_short <= 8)
            fprintf(stderr, "tiger_host: SHORT pread fd %d wanted %u got %u "
                            "at offset %lld\n", fd, n, (unsigned)got, want);
    }
    if (g_verbose && g_preads <= 6)
        printf("  [pread] fd %d %u bytes at %lld -> %u\n", fd, n, want,
               (unsigned)got);
    return (int)got;
}
static int __cdecl sh_fcntl(int fd, int cmd, int arg)
{ (void)fd; (void)cmd; (void)arg; return 0; }
static int __cdecl sh_getpagesize(void) { return 4096; }
static void __cdecl sh_exit_(int code) { exit(code); }
static void __cdecl sh_assert_rtn(const char *fn, const char *file, int line,
                                  const char *expr)
{
    die("the engine's assertion failed: %s, %s:%d: %s",
        fn ? fn : "?", file ? file : "?", line, expr ? expr : "?");
}
static void __cdecl sh_pure_virtual(void)
{ die("a pure virtual call -- an object was used before its constructor ran"); }

/* Everything the engine prints goes to stderr, where every other diagnostic
 * in this program already goes. */
static int __cdecl sh_fputs(const char *s, void *f)
{ (void)f; printf("  [engine] %s\n", s ? s : "(null)"); return 0; }
static int __cdecl sh_fputc(int c, void *f) { (void)f; fputc(c, stderr); return c; }
static int __cdecl sh_putchar(int c) { fputc(c, stderr); return c; }
static unsigned __cdecl sh_fwrite(const void *p, unsigned sz, unsigned n,
                                  void *f)
{ (void)f; return (unsigned)fwrite(p, sz, n, stderr); }
static int __cdecl sh_fflush(void *f) { (void)f; fflush(stderr); return 0; }

/* One-time static initialisation.  The guard is a 64-bit word whose first byte
 * says "done"; the engine only ever runs single-threaded through these. */
static int __cdecl sh_guard_acquire(unsigned char *g)
{ return g && !g[0]; }
static void __cdecl sh_guard_release(unsigned char *g) { if (g) g[0] = 1; }
static int __cdecl sh_cxa_atexit(void *f, void *a, void *d)
{ (void)f; (void)a; (void)d; return 0; }
static void __cdecl sh_memory_barrier(void) { MemoryBarrier(); }

/* `__stderrp` and `__stdoutp` are pointer *variables*, unlike the `__sF` array
 * next to them: the code takes the slot's value as their address and
 * dereferences that to reach the FILE.  Binding them straight to the fake
 * stream would hand back a FILE* where a FILE** was wanted. */
static void *g_stderrp = g_fake_sF + 2 * 88;
static void *g_stdoutp = g_fake_sF + 1 * 88;

static char * __cdecl sh_strtok_r(char *s, const char *sep, char **save)
{
    char *p;
    if (!s) s = *save;
    if (!s) return NULL;
    s += strspn(s, sep);
    if (!*s) { *save = NULL; return NULL; }
    p = s + strcspn(s, sep);
    if (*p) { *p = 0; *save = p + 1; } else *save = NULL;
    return s;
}

static void __cdecl sh_throw_bad_alloc(void)
{ die("the engine threw std::bad_alloc"); }
static void __cdecl sh_throw_length_error(const char *w)
{ die("the engine threw std::length_error: %s", w ? w : "?"); }
static void __cdecl sh_terminate(void)
{ die("the engine called std::terminate"); }

/* Mach-O's own section lookup: the engine finds embedded tables this way, so
 * it must answer from the *mapped* image rather than from the file. */
static void * __cdecl sh_getsectdatafromheader(void *mh, const char *seg,
                                               const char *sect, unsigned *size)
{
    int i;
    (void)mh;
    for (i = 0; i < g_primary->nsects; i++) {
        const section *s = &g_primary->sects[i];
        if (!strncmp(s->segname, seg, 16) && !strncmp(s->sectname, sect, 16)) {
            if (size) *size = s->size;
            return (void *)(s->addr + g_primary->slide);
        }
    }
    if (size) *size = 0;
    return NULL;
}
