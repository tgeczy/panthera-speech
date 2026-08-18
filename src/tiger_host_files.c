/* tiger_host_files.c -- the file layer, and real memory mapping.
 *
 * Part of tiger_host.c, which includes it; see there for why this is one
 * translation unit. */

/* ---- the file layer ---------------------------------------------------- */
/*
 * Darwin's `struct stat` for i386 is 96 bytes with st_size at offset 48, which
 * is not assumed: ReadVoiceData passes a buffer at [ebp-0x78] and reads the
 * size from [ebp-0x48], and 0x78 - 0x48 is 0x30.
 */
#define DARWIN_STAT_SIZE   96
#define DARWIN_ST_SIZE_OFF 48

static int __cdecl sh_open(const char *path, int flags, int mode)
{
    int fd;
    (void)flags; (void)mode;
    if (!path) { printf("  [open] NULL path\n"); return -1; }
    fd = _open(path, _O_RDONLY | _O_BINARY);
    if (fd < 0) printf("  [open] FAILED errno=%d: %s\n", errno, path);
    else if (g_verbose) printf("  [open] fd %d <- %s\n", fd, path);
    return fd;
}
static int __cdecl sh_close(int fd) { return _close(fd); }
static int __cdecl sh_read(int fd, void *b, unsigned n)
{ return _read(fd, b, n); }
static int __cdecl sh_write(int fd, const void *b, unsigned n)
{ (void)fd; return (int)fwrite(b, 1, n, stderr); }

static int __cdecl sh_fstat(int fd, unsigned char *st)
{
    struct _stat64 s;
    if (!st) return -1;
    if (_fstat64(fd, &s) != 0) return -1;
    memset(st, 0, DARWIN_STAT_SIZE);
    *(long long *)(st + DARWIN_ST_SIZE_OFF) = s.st_size;
    return 0;
}

/* A real file mapping, because for the big voices that is the whole point.
 *
 * The engine maps a voice bundle whole and then reads only the units the
 * sentence in front of it needs.  Reading the file in instead turns that into
 * a full copy: fine for Vicki's 29 MB, hopeless for Alex, who is 669 MB in
 * Leopard and 911 MB today -- a third of a 32-bit address space spent on bytes
 * nobody asked for, and a second or two of disk before the first word.  Mapped,
 * the same call costs address space and nothing else; the pages the engine
 * touches arrive as it touches them, which is what Apple's own build did.
 *
 * Writes go to a copy, never to the file.  Nothing here should be able to
 * modify the user's own extracted voices, whatever the engine asks for.
 */
#define MAX_MAPS 64
static struct { void *base; void *ptr; size_t len; } g_maps[MAX_MAPS];
static int g_nmaps;

static void *mmap_fallback(unsigned len, int fd, unsigned off_lo)
{
    void *p = malloc(len ? len : 1);
    if (!p) return (void *)-1;
    if (_lseek(fd, (long)off_lo, SEEK_SET) < 0 ||
        _read(fd, p, len) != (int)len) { free(p); return (void *)-1; }
    return p;
}

static void * __cdecl sh_mmap(void *addr, unsigned len, int prot, int flags,
                              int fd, unsigned off_lo, unsigned off_hi)
{
    HANDLE fh, mh;
    unsigned char *view;
    unsigned long long off = ((unsigned long long)off_hi << 32) | off_lo;
    unsigned long long aligned;
    SYSTEM_INFO si;
    unsigned slack;
    int writable = (prot & 2) != 0;      /* PROT_WRITE */

    (void)addr; (void)flags;
    GetSystemInfo(&si);
    aligned = off - (off % si.dwAllocationGranularity);
    slack = (unsigned)(off - aligned);

    fh = (HANDLE)_get_osfhandle(fd);
    if (fh == INVALID_HANDLE_VALUE) goto fallback;
    mh = CreateFileMappingA(fh, NULL,
                            writable ? PAGE_WRITECOPY : PAGE_READONLY,
                            0, 0, NULL);
    if (!mh) goto fallback;
    view = (unsigned char *)MapViewOfFile(mh,
                                          writable ? FILE_MAP_COPY
                                                   : FILE_MAP_READ,
                                          (DWORD)(aligned >> 32),
                                          (DWORD)aligned,
                                          len ? len + slack : 0);
    /* The handle can go now: the view holds the mapping open. */
    CloseHandle(mh);
    if (!view) goto fallback;
    if (g_nmaps < MAX_MAPS) {
        g_maps[g_nmaps].base = view;
        g_maps[g_nmaps].ptr = view + slack;
        g_maps[g_nmaps].len = len;
        g_nmaps++;
    }
    if (g_verbose)
        printf("  [mmap] %u bytes of fd %d mapped -> %p\n", len, fd,
               (void *)(view + slack));
    return view + slack;

fallback:
    /* Never fatal: a mapping that cannot be made is still a file that can be
     * read, and for every voice but Alex the difference is only speed. */
    {
        void *p = mmap_fallback(len, fd, off_lo);
        if (g_verbose)
            printf("  [mmap] %u bytes of fd %d READ IN (no mapping) -> %p\n",
                   len, fd, p);
        return p;
    }
}

static int __cdecl sh_munmap(void *p, unsigned len)
{
    int i;
    (void)len;
    for (i = 0; i < g_nmaps; i++) {
        if (g_maps[i].ptr == p) {
            UnmapViewOfFile(g_maps[i].base);
            g_maps[i] = g_maps[--g_nmaps];
            return 0;
        }
    }
    free(p);                              /* it came from the fallback */
    return 0;
}
static int __cdecl sh_advise_ok(void *p, unsigned l, int a)
{ (void)p; (void)l; (void)a; return 0; }

/* Engine diagnostics.  MacinTalk carries strings like "MacinTalk Fatal Error:
 * This voice is broken beyond repair." and printing them is worth far more
 * than the ten lines it costs. */
static char g_fake_sF[1024];             /* ___sF; stderr is &__sF[2] */
static int  g_errno_storage;
static int * __cdecl sh_error(void) { return &g_errno_storage; }

static int __cdecl sh_fprintf(void *f, const char *fmt, ...)
{
    va_list ap; int n;
    (void)f;
    va_start(ap, fmt);
    printf("  [engine] ");
    n = vfprintf(stderr, fmt, ap);
    va_end(ap);
    return n;
}
static int __cdecl sh_printf(const char *fmt, ...)
{
    va_list ap; int n;
    va_start(ap, fmt);
    printf("  [engine] ");
    n = vfprintf(stderr, fmt, ap);
    va_end(ap);
    return n;
}
static int __cdecl sh_puts(const char *s)
{ printf("  [engine] %s\n", s ? s : "(null)"); return 0; }

/* sprintf writes into the engine's own buffer, but what it writes is often a
 * diagnostic on its way to a log that goes nowhere here.  Echoing it costs
 * nothing and is the cheapest window into what the engine thinks. */
static int __cdecl sh_sprintf(char *buf, const char *fmt, ...)
{
    va_list ap; int n;
    va_start(ap, fmt);
    n = vsprintf(buf, fmt, ap);
    va_end(ap);
    if (g_verbose) printf("  [engine sprintf] %s\n", buf);
    return n;
}
static int __cdecl sh_vsprintf(char *buf, const char *fmt, va_list ap)
{
    int n = vsprintf(buf, fmt, ap);
    printf("  [engine vsprintf] %s\n", buf);
    return n;
}
