/* tiger_plat_posix.c -- the POSIX implementation of tiger_plat.h.
 *
 * Part of tiger_host.c, which includes it; see there for why the whole host is
 * one translation unit.  Declarations live in tiger_plat.h; this file is the
 * bodies.  Nothing here depends on any other seam file, so it is included
 * first, as the platform floor everything else stands on.
 *
 * The one idea worth stating up front: a Win32 HANDLE is polymorphic -- a
 * thread, an event, a semaphore, an open file, a file mapping -- and
 * WaitForSingleObject and CloseHandle are expected to work on any of them.  So
 * every handle here is one tagged object (struct plat_handle), and those two
 * functions switch on its kind.  Threads, events and semaphores are all a
 * mutex + condition variable underneath, which is why they share the struct.
 */

#include <sys/types.h>

/* ---- the one handle object -------------------------------------------- */
enum { H_THREAD = 1, H_EVENT, H_SEM, H_FILE, H_FILEMAP };

struct plat_handle {
    int             kind;
    pthread_mutex_t mtx;
    pthread_cond_t  cond;
    /* event */
    int             signaled;
    int             manual;
    /* semaphore */
    long            count;
    /* thread */
    pthread_t       tid;
    int             done;
    plat_thread_fn  fn;
    void           *arg;
    volatile long   refs;       /* thread: 2 (owner + wrapper), else 1 */
    /* file / filemap */
    int             fd;
    int             close_fd;   /* CloseHandle should close(fd) */
    int             writable;
};

/* mtx + cond, the cond clocked on CLOCK_MONOTONIC so a wall-clock step can
 * never stretch a timed wait (the pacer and MPWaitOnQueue both time out). */
static void h_init_wait(struct plat_handle *h)
{
    pthread_condattr_t a;
    pthread_mutex_init(&h->mtx, NULL);
    pthread_condattr_init(&a);
    pthread_condattr_setclock(&a, CLOCK_MONOTONIC);
    pthread_cond_init(&h->cond, &a);
    pthread_condattr_destroy(&a);
}

static struct plat_handle *h_new(int kind)
{
    struct plat_handle *h = (struct plat_handle *)calloc(1, sizeof(*h));
    if (!h) return NULL;
    h->kind = kind;
    h->refs = 1;
    h_init_wait(h);
    return h;
}

static void h_free(struct plat_handle *h)
{
    pthread_cond_destroy(&h->cond);
    pthread_mutex_destroy(&h->mtx);
    free(h);
}

static void ms_to_abstime(DWORD ms, struct timespec *ts)
{
    clock_gettime(CLOCK_MONOTONIC, ts);
    ts->tv_sec  += ms / 1000u;
    ts->tv_nsec += (long)(ms % 1000u) * 1000000L;
    if (ts->tv_nsec >= 1000000000L) { ts->tv_sec++; ts->tv_nsec -= 1000000000L; }
}

/* ---- memory ------------------------------------------------------------ */
/* VirtualAlloc reserves (PROT_NONE) or commits (mprotect) a region; the loader
 * reserves an image at a chosen base and commits its segments into it.  A
 * reservation at a specific base is a *hint*, never MAP_FIXED: MAP_FIXED would
 * silently unmap whatever was already there, and on the old kernels these
 * watches run MAP_FIXED_NOREPLACE is not dependable.  If the hint is not
 * honoured we fail, exactly as Win32 does, and the caller falls back to
 * reserving anywhere and sliding.
 *
 * VirtualFree(MEM_RELEASE) is handed a base and a size of 0, so the length has
 * to be remembered from the reservation. */
#define PLAT_MAX_RESV 128
static struct { void *base; size_t len; } g_resv[PLAT_MAX_RESV];
static int g_nresv;
static pthread_mutex_t g_resv_lk = PTHREAD_MUTEX_INITIALIZER;

static void resv_add(void *base, size_t len)
{
    pthread_mutex_lock(&g_resv_lk);
    if (g_nresv < PLAT_MAX_RESV) {
        g_resv[g_nresv].base = base;
        g_resv[g_nresv].len  = len;
        g_nresv++;
    }
    pthread_mutex_unlock(&g_resv_lk);
}
static size_t resv_take(void *base)
{
    size_t len = 0;
    int i;
    pthread_mutex_lock(&g_resv_lk);
    for (i = 0; i < g_nresv; i++)
        if (g_resv[i].base == base) {
            len = g_resv[i].len;
            g_resv[i] = g_resv[--g_nresv];
            break;
        }
    pthread_mutex_unlock(&g_resv_lk);
    return len;
}

static int win_prot(DWORD protect)
{
    switch (protect & 0xffu) {
    case PAGE_NOACCESS:            return PROT_NONE;
    case PAGE_READONLY:            return PROT_READ;
    case PAGE_READWRITE:           return PROT_READ | PROT_WRITE;
    case PAGE_WRITECOPY:           return PROT_READ | PROT_WRITE;
    case PAGE_EXECUTE:             return PROT_EXEC;
    case PAGE_EXECUTE_READ:        return PROT_READ | PROT_EXEC;
    case PAGE_EXECUTE_READWRITE:   return PROT_READ | PROT_WRITE | PROT_EXEC;
    default:                       return PROT_READ | PROT_WRITE;
    }
}

void *VirtualAlloc(void *addr, size_t size, DWORD type, DWORD protect)
{
    size_t pg = (size_t)sysconf(_SC_PAGESIZE);
    size_t sz = (size + pg - 1) & ~(pg - 1);

    if (type & MEM_RESERVE) {
        int   prot  = (type & MEM_COMMIT) ? win_prot(protect) : PROT_NONE;
        void *want  = addr;
        void *p     = mmap(want, sz, prot,
                           MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
        if (p == MAP_FAILED) return NULL;
        if (want && p != want) { munmap(p, sz); return NULL; }
        resv_add(p, sz);
        return p;
    }
    if (type & MEM_COMMIT) {                 /* commit within a reservation */
        uintptr_t a   = (uintptr_t)addr & ~(uintptr_t)(pg - 1);
        uintptr_t end = ((uintptr_t)addr + size + pg - 1) & ~(uintptr_t)(pg - 1);
        if (mprotect((void *)a, (size_t)(end - a), win_prot(protect)) != 0)
            return NULL;
        return addr;
    }
    return NULL;
}

BOOL VirtualFree(void *addr, size_t size, DWORD type)
{
    if (type & MEM_RELEASE) {
        size_t len = resv_take(addr);
        if (len) munmap(addr, len);
        return TRUE;
    }
    if (type & MEM_DECOMMIT) {
        size_t pg = (size_t)sysconf(_SC_PAGESIZE);
        size_t sz = (size + pg - 1) & ~(pg - 1);
        mprotect(addr, sz, PROT_NONE);
        return TRUE;
    }
    return TRUE;
}

/* ---- critical sections (recursive) ------------------------------------- */
void InitializeCriticalSection(CRITICAL_SECTION *cs)
{
    pthread_mutexattr_t a;
    pthread_mutexattr_init(&a);
    pthread_mutexattr_settype(&a, PTHREAD_MUTEX_RECURSIVE);
    pthread_mutex_init(cs, &a);
    pthread_mutexattr_destroy(&a);
}
void EnterCriticalSection(CRITICAL_SECTION *cs) { pthread_mutex_lock(cs); }
void LeaveCriticalSection(CRITICAL_SECTION *cs) { pthread_mutex_unlock(cs); }
void DeleteCriticalSection(CRITICAL_SECTION *cs) { pthread_mutex_destroy(cs); }
BOOL TryEnterCriticalSection(CRITICAL_SECTION *cs)
{ return pthread_mutex_trylock(cs) == 0; }

/* ---- condition variables ----------------------------------------------- */
/* These are Windows condition variables, used by libstdc++'s std::condition
 * variable shim (tiger_host_cxx.c), always with an INFINITE wait.  The timed
 * branch is here for completeness; the untimed one is what runs. */
void InitializeConditionVariable(CONDITION_VARIABLE *cv)
{
    pthread_condattr_t a;
    pthread_condattr_init(&a);
    pthread_condattr_setclock(&a, CLOCK_MONOTONIC);
    pthread_cond_init(cv, &a);
    pthread_condattr_destroy(&a);
}
BOOL SleepConditionVariableCS(CONDITION_VARIABLE *cv, CRITICAL_SECTION *cs, DWORD ms)
{
    if (ms == INFINITE) return pthread_cond_wait(cv, cs) == 0;
    {
        struct timespec ts;
        ms_to_abstime(ms, &ts);
        return pthread_cond_timedwait(cv, cs, &ts) == 0;
    }
}
void WakeConditionVariable(CONDITION_VARIABLE *cv)    { pthread_cond_signal(cv); }
void WakeAllConditionVariable(CONDITION_VARIABLE *cv) { pthread_cond_broadcast(cv); }

/* ---- threads ----------------------------------------------------------- */
/* CloseHandle on a running thread must *detach*, never join: the call sites
 * close a worker's handle while it may still be blocked in WaitOnQueue (task
 * teardown) or after only a timed wait (api.c).  A join there would hang.  So
 * the handle is reference counted -- the owner and the running wrapper each
 * hold one -- and whichever drops the last reference frees it; the wrapper's
 * "done" broadcast can then never touch freed memory. */
static void *thread_trampoline(void *p)
{
    struct plat_handle *h = (struct plat_handle *)p;
    h->fn(h->arg);
    pthread_mutex_lock(&h->mtx);
    h->done = 1;
    pthread_cond_broadcast(&h->cond);
    pthread_mutex_unlock(&h->mtx);
    if (__atomic_sub_fetch(&h->refs, 1, __ATOMIC_SEQ_CST) == 0) h_free(h);
    return NULL;
}

HANDLE CreateThread(void *sec, size_t stack, plat_thread_fn fn, void *arg,
                    DWORD flags, DWORD *tid)
{
    pthread_attr_t a;
    struct plat_handle *h;
    size_t st;
    (void)sec; (void)flags;

    h = h_new(H_THREAD);
    if (!h) return NULL;
    h->refs = 2;
    h->fn = fn;
    h->arg = arg;

    /* On Windows dwStackSize is only the commit; the reserve is the exe
     * default of 1 MB, so every worker actually had 1 MB.  On pthreads the
     * number is the whole stack -- and under emulation this host thread runs
     * TCG, not the engine's small budget -- so never go below 1 MB. */
    st = stack ? stack : 0;
    if (st < (1u << 20)) st = (1u << 20);

    pthread_attr_init(&a);
    pthread_attr_setstacksize(&a, st);
    if (pthread_create(&h->tid, &a, thread_trampoline, h) != 0) {
        pthread_attr_destroy(&a);
        h_free(h);
        return NULL;
    }
    pthread_attr_destroy(&a);
    if (tid) *tid = (DWORD)(uintptr_t)h->tid;
    return h;
}

/* ---- events ------------------------------------------------------------ */
HANDLE CreateEventA(void *sec, BOOL manual, BOOL initial, const char *name)
{
    struct plat_handle *h;
    (void)sec; (void)name;
    h = h_new(H_EVENT);
    if (!h) return NULL;
    h->manual   = manual  ? 1 : 0;
    h->signaled = initial ? 1 : 0;
    return h;
}
BOOL SetEvent(HANDLE ho)
{
    struct plat_handle *h = (struct plat_handle *)ho;
    pthread_mutex_lock(&h->mtx);
    h->signaled = 1;
    pthread_cond_broadcast(&h->cond);
    pthread_mutex_unlock(&h->mtx);
    return TRUE;
}
BOOL ResetEvent(HANDLE ho)
{
    struct plat_handle *h = (struct plat_handle *)ho;
    pthread_mutex_lock(&h->mtx);
    h->signaled = 0;
    pthread_mutex_unlock(&h->mtx);
    return TRUE;
}
/* Named cross-process events (serve mode's external cancel signal) have no
 * standalone equivalent here, and the WAV path never uses one.  Report "no
 * such event"; a NULL handle then reads as never-signalled at the wait. */
HANDLE OpenEventA(DWORD access, BOOL inherit, const char *name)
{
    (void)access; (void)inherit; (void)name;
    return NULL;
}

/* ---- semaphores -------------------------------------------------------- */
HANDLE CreateSemaphoreA(void *sec, long initial, long maximum, const char *name)
{
    struct plat_handle *h;
    (void)sec; (void)maximum; (void)name;
    h = h_new(H_SEM);
    if (!h) return NULL;
    h->count = initial;
    return h;
}
BOOL ReleaseSemaphore(HANDLE ho, long count, long *previous)
{
    struct plat_handle *h = (struct plat_handle *)ho;
    long i;
    pthread_mutex_lock(&h->mtx);
    if (previous) *previous = h->count;
    h->count += count;
    for (i = 0; i < count; i++) pthread_cond_signal(&h->cond);
    pthread_mutex_unlock(&h->mtx);
    return TRUE;
}

/* ---- WaitForSingleObject / CloseHandle: the polymorphic pair ----------- */
DWORD WaitForSingleObject(HANDLE ho, DWORD ms)
{
    struct plat_handle *h = (struct plat_handle *)ho;
    DWORD rc = WAIT_OBJECT_0;
    struct timespec ts;
    int timed = (ms != INFINITE);

    if (!h || ho == INVALID_HANDLE_VALUE) return WAIT_FAILED;
    if (timed) ms_to_abstime(ms, &ts);
    pthread_mutex_lock(&h->mtx);
    switch (h->kind) {
    case H_EVENT:
        while (!h->signaled) {
            if (!timed) { pthread_cond_wait(&h->cond, &h->mtx); continue; }
            if (pthread_cond_timedwait(&h->cond, &h->mtx, &ts) == ETIMEDOUT) {
                rc = WAIT_TIMEOUT; break;
            }
        }
        if (rc == WAIT_OBJECT_0 && !h->manual) h->signaled = 0;
        break;
    case H_THREAD:
        while (!h->done) {
            if (!timed) { pthread_cond_wait(&h->cond, &h->mtx); continue; }
            if (pthread_cond_timedwait(&h->cond, &h->mtx, &ts) == ETIMEDOUT) {
                rc = WAIT_TIMEOUT; break;
            }
        }
        break;
    case H_SEM:
        while (h->count <= 0) {
            if (!timed) { pthread_cond_wait(&h->cond, &h->mtx); continue; }
            if (pthread_cond_timedwait(&h->cond, &h->mtx, &ts) == ETIMEDOUT) {
                rc = WAIT_TIMEOUT; break;
            }
        }
        if (rc == WAIT_OBJECT_0) h->count--;
        break;
    default:
        rc = WAIT_FAILED;
        break;
    }
    pthread_mutex_unlock(&h->mtx);
    return rc;
}

BOOL CloseHandle(HANDLE ho)
{
    struct plat_handle *h = (struct plat_handle *)ho;
    if (!h || ho == INVALID_HANDLE_VALUE) return FALSE;
    switch (h->kind) {
    case H_THREAD:
        pthread_detach(h->tid);
        if (__atomic_sub_fetch(&h->refs, 1, __ATOMIC_SEQ_CST) == 0) h_free(h);
        return TRUE;
    case H_FILE:
    case H_FILEMAP:
        if (h->close_fd) close(h->fd);
        h_free(h);
        return TRUE;
    default:
        /* Events and semaphores.  Freeing one, or destroying its cond/mutex,
         * while a thread is blocked inside it is undefined and hangs at exit --
         * which would be misread later as a render bug.  There are only a
         * handful per process, so the safe thing is not to free them at all;
         * leak the struct.  (Fred never reaches this -- the MP semaphore lives
         * for the whole process and only Lion's GCD closes an event.) */
        return TRUE;
    }
}

/* ---- thread-local storage ---------------------------------------------- */
DWORD TlsAlloc(void)
{
    pthread_key_t k;
    if (pthread_key_create(&k, NULL) != 0) return 0xFFFFFFFFu; /* TLS_OUT_OF_INDEXES */
    return (DWORD)k;
}
BOOL  TlsFree(DWORD i)          { return pthread_key_delete((pthread_key_t)i) == 0; }
void *TlsGetValue(DWORD i)      { return pthread_getspecific((pthread_key_t)i); }
BOOL  TlsSetValue(DWORD i, void *v) { return pthread_setspecific((pthread_key_t)i, v) == 0; }
DWORD GetCurrentThreadId(void)  { return (DWORD)(uintptr_t)pthread_self(); }

/* ---- timing ------------------------------------------------------------ */
DWORD GetTickCount(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (DWORD)((unsigned long long)ts.tv_sec * 1000ULL + ts.tv_nsec / 1000000ULL);
}
unsigned long long GetTickCount64(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (unsigned long long)ts.tv_sec * 1000ULL + (unsigned long long)ts.tv_nsec / 1000000ULL;
}
BOOL QueryPerformanceCounter(LARGE_INTEGER *c)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    c->QuadPart = (long long)ts.tv_sec * 1000000000LL + ts.tv_nsec;
    return TRUE;
}
BOOL QueryPerformanceFrequency(LARGE_INTEGER *f)
{
    f->QuadPart = 1000000000LL;           /* QPC is in nanoseconds above */
    return TRUE;
}
void Sleep(DWORD ms)
{
    struct timespec ts;
    ts.tv_sec  = ms / 1000u;
    ts.tv_nsec = (long)(ms % 1000u) * 1000000L;
    nanosleep(&ts, NULL);
}
/* Wall clock as a Win32 FILETIME: 100-ns ticks since 1601-01-01.  Only used to
 * seed a relative timestamp (CFAbsoluteTime), so the epoch need only be right,
 * not precise. */
void GetSystemTimeAsFileTime(FILETIME *ft)
{
    struct timespec ts;
    unsigned long long t;
    clock_gettime(CLOCK_REALTIME, &ts);
    t = ((unsigned long long)ts.tv_sec + 11644473600ULL) * 10000000ULL
        + (unsigned long long)ts.tv_nsec / 100ULL;
    ft->dwLowDateTime  = (DWORD)t;
    ft->dwHighDateTime = (DWORD)(t >> 32);
}

/* ---- last error (only ReadFile/GetLastError use it) -------------------- */
static __thread DWORD g_last_error;
DWORD GetLastError(void)        { return g_last_error; }
void  SetLastError(DWORD e)     { g_last_error = e; }

/* ---- files ------------------------------------------------------------- */
/* _get_osfhandle wraps an fd in a handle so ReadFile/CreateFileMapping can take
 * it.  Alex pulls his 701 MB bank through pread and calls this on every read,
 * so the handle is cached per fd -- never a fresh allocation per call. */
#define PLAT_FDCACHE 256
static struct plat_handle *g_fdh[PLAT_FDCACHE];
static pthread_mutex_t g_fdh_lk = PTHREAD_MUTEX_INITIALIZER;

HANDLE _get_osfhandle(int fd)
{
    struct plat_handle *h;
    if (fd < 0) return INVALID_HANDLE_VALUE;
    if (fd >= PLAT_FDCACHE) {              /* rare; small, one-off leak is fine */
        h = h_new(H_FILE);
        if (h) { h->fd = fd; h->close_fd = 0; }
        return h ? (HANDLE)h : INVALID_HANDLE_VALUE;
    }
    pthread_mutex_lock(&g_fdh_lk);
    if (!g_fdh[fd]) {
        h = h_new(H_FILE);
        if (h) { h->fd = fd; h->close_fd = 0; }
        g_fdh[fd] = h;
    }
    h = g_fdh[fd];
    pthread_mutex_unlock(&g_fdh_lk);
    return h ? (HANDLE)h : INVALID_HANDLE_VALUE;
}

int _open_osfhandle(intptr_t ho, int flags)
{
    struct plat_handle *h = (struct plat_handle *)ho;
    (void)flags;
    if (!h || (HANDLE)ho == INVALID_HANDLE_VALUE) return -1;
    h->close_fd = 0;                      /* ownership moves to the returned fd */
    return h->fd;
}

HANDLE CreateFileA(const char *path, DWORD access, DWORD share, void *sec,
                   DWORD disposition, DWORD flags, HANDLE tmpl)
{
    struct plat_handle *h;
    int fd;
    (void)access; (void)share; (void)sec; (void)disposition; (void)flags; (void)tmpl;
    /* Only ever opened here to read attributes (file identity); O_RDONLY opens
     * a directory too, which FILE_FLAG_BACKUP_SEMANTICS is what asked for. */
    fd = open(path, O_RDONLY);
    if (fd < 0) return INVALID_HANDLE_VALUE;
    h = h_new(H_FILE);
    if (!h) { close(fd); return INVALID_HANDLE_VALUE; }
    h->fd = fd;
    h->close_fd = 1;
    return h;
}

BOOL GetFileInformationByHandle(HANDLE ho, BY_HANDLE_FILE_INFORMATION *bi)
{
    struct plat_handle *h = (struct plat_handle *)ho;
    struct stat st;
    if (!h || ho == INVALID_HANDLE_VALUE) return FALSE;
    if (fstat(h->fd, &st) != 0) return FALSE;
    memset(bi, 0, sizeof(*bi));
    bi->dwVolumeSerialNumber = (DWORD)st.st_dev;
    bi->nFileIndexHigh = (DWORD)((unsigned long long)st.st_ino >> 32);
    bi->nFileIndexLow  = (DWORD)((unsigned long long)st.st_ino);
    bi->nFileSizeHigh  = (DWORD)((unsigned long long)st.st_size >> 32);
    bi->nFileSizeLow   = (DWORD)((unsigned long long)st.st_size);
    bi->nNumberOfLinks = (DWORD)st.st_nlink;
    return TRUE;
}

BOOL ReadFile(HANDLE ho, void *buf, DWORD n, DWORD *got, OVERLAPPED *ov)
{
    struct plat_handle *h = (struct plat_handle *)ho;
    ssize_t r;
    if (!h || ho == INVALID_HANDLE_VALUE) { SetLastError(6); return FALSE; }
    if (ov) {
        off64_t off = ((off64_t)ov->OffsetHigh << 32) | (off64_t)ov->Offset;
        r = pread64(h->fd, buf, n, off);
    } else {
        r = read(h->fd, buf, n);
    }
    if (r < 0) { if (got) *got = 0; SetLastError(5); return FALSE; }
    if (got) *got = (DWORD)r;             /* a short read at EOF is success */
    return TRUE;
}

/* ---- file mappings (mmap under the CreateFileMapping/MapViewOfFile pair) - */
#define PLAT_MAX_VIEW 64
static struct { void *base; size_t len; } g_views[PLAT_MAX_VIEW];
static int g_nviews;
static pthread_mutex_t g_view_lk = PTHREAD_MUTEX_INITIALIZER;

static void view_add(void *base, size_t len)
{
    pthread_mutex_lock(&g_view_lk);
    if (g_nviews < PLAT_MAX_VIEW) {
        g_views[g_nviews].base = base;
        g_views[g_nviews].len  = len;
        g_nviews++;
    }
    pthread_mutex_unlock(&g_view_lk);
}
static size_t view_take(void *base)
{
    size_t len = 0;
    int i;
    pthread_mutex_lock(&g_view_lk);
    for (i = 0; i < g_nviews; i++)
        if (g_views[i].base == base) {
            len = g_views[i].len;
            g_views[i] = g_views[--g_nviews];
            break;
        }
    pthread_mutex_unlock(&g_view_lk);
    return len;
}

HANDLE CreateFileMappingA(HANDLE fho, void *sec, DWORD protect,
                          DWORD sizeHigh, DWORD sizeLow, const char *name)
{
    struct plat_handle *fh = (struct plat_handle *)fho;
    struct plat_handle *h;
    (void)sec; (void)sizeHigh; (void)sizeLow; (void)name;
    if (!fh || fho == INVALID_HANDLE_VALUE) return NULL;
    h = h_new(H_FILEMAP);
    if (!h) return NULL;
    h->fd = fh->fd;                       /* borrowed; the view keeps it open */
    h->close_fd = 0;
    h->writable = (protect == PAGE_WRITECOPY);
    return h;
}

void *MapViewOfFile(HANDLE mho, DWORD access, DWORD offHigh, DWORD offLow,
                    size_t nbytes)
{
    struct plat_handle *mh = (struct plat_handle *)mho;
    off64_t off = ((off64_t)offHigh << 32) | (off64_t)offLow;
    int prot = (access == FILE_MAP_COPY) ? (PROT_READ | PROT_WRITE) : PROT_READ;
    void *p;
    if (!mh || mho == INVALID_HANDLE_VALUE || nbytes == 0) return NULL;
    p = mmap64(NULL, nbytes, prot, MAP_PRIVATE, mh->fd, off);
    if (p == MAP_FAILED) return NULL;
    view_add(p, nbytes);
    return p;
}

BOOL UnmapViewOfFile(void *base)
{
    size_t len = view_take(base);
    if (len) munmap(base, len);
    return TRUE;
}

void GetSystemInfo(SYSTEM_INFO *si)
{
    memset(si, 0, sizeof(*si));
    si->dwPageSize = (unsigned)sysconf(_SC_PAGESIZE);
    si->dwAllocationGranularity = 65536;  /* matches Windows; a page multiple */
    si->dwNumberOfProcessors = (unsigned)sysconf(_SC_NPROCESSORS_ONLN);
}

/* GetModuleFileNameA(NULL, ...) asks for this program's own path -- used to
 * find the directory it sits in.  Named-module lookup (a non-NULL handle) is
 * only in the Windows-only crash reporter, so it is unsupported here. */
DWORD GetModuleFileNameA(HMODULE mod, char *buf, DWORD size)
{
    ssize_t r;
    if (mod) return 0;
    r = readlink("/proc/self/exe", buf, (size_t)size - 1);
    if (r <= 0) return 0;
    buf[r] = 0;
    return (DWORD)r;
}

/* ---- dynamic loader ---------------------------------------------------- */
HMODULE LoadLibraryA(const char *name)   { return dlopen(name, RTLD_NOW | RTLD_LOCAL); }
void   *GetProcAddress(HMODULE h, const char *name) { return dlsym(h, name); }
BOOL    FreeLibrary(HMODULE h)           { return dlclose(h) == 0; }
