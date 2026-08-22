/* tiger_host_files.c -- the file layer, and real memory mapping.
 *
 * Part of tiger_host.c, which includes it; see there for why this is one
 * translation unit. */

/* ---- the file layer ---------------------------------------------------- */
/*
 * Darwin's `struct stat` for i386 is 96 bytes with st_size at offset 48, which
 * is not assumed: ReadVoiceData passes a buffer at [ebp-0x78] and reads the
 * size from [ebp-0x48], and 0x78 - 0x48 is 0x30.
 *
 * **10.6 changed both the name and the shape.**  `st_ino` widened to 64 bits,
 * and rather than break every existing binary Apple gave the wide form its own
 * symbols: `stat$INODE64`, `fstat$INODE64`.  Leopard imports `_stat`; Lion's
 * SpeechDictionary imports `_stat$INODE64` and its MacinTalk imports both that
 * and `_fstat$INODE64`.  So the spelling tells you the layout, and one image
 * never wants both.
 *
 * A table that knows only the 10.5 spellings does not fail loudly.  The call
 * reaches the auto-stub, which returns **0 -- and 0 is success** -- over a
 * buffer nobody filled.  `SLMMapCache::Map` then reads a size of zero, maps
 * zero bytes, and hands `SLCartDict` a window into nothing; the crash lands
 * two frames later in a constructor that is not at fault.
 *
 * The 10.6 offsets are measured the same way the 10.4 ones were, and three
 * call sites in two binaries agree:
 *   - `SLMMapCache::Map` buffers at [ebp-0x78], takes the size from
 *     [ebp-0x3c] (= 60) and compares [ebp-0x70]/[ebp-0x6c] as one 64-bit
 *     inode (= offset 8);
 *   - Lion's MacinTalk buffers at [ebp-0x80] and takes the size from
 *     [ebp-0x44].  Also 60.
 */
typedef struct { unsigned bytes, dev, ino, mode, size; } darwin_stat_layout;

/* st_ino is 4 bytes wide in the first and 8 in the second; the fill zeroes the
 * whole struct first, so only the offset has to be said out loud. */
static const darwin_stat_layout DARWIN_STAT   = { 96,  0, 4, 8, 48 };
static const darwin_stat_layout DARWIN_STAT64 = { 108, 0, 8, 4, 60 };


/* st_dev and st_ino are not decoration: SpeechDictionary's SLMMapCache keys
 * its whole mapping cache on them.  SLMMapCache::Map(const char *) stats the
 * path and then walks its list comparing exactly the first eight bytes of the
 * stat buffer against each node -- nothing else, not the path.
 *
 * So a stat that leaves those bytes zero says "every file is the same file".
 * Under Leopard that is fatal and almost undetectable: the first dictionary
 * maps correctly, and the six after it are served the first one's bytes from
 * the cache without ever reaching open().  The engine then builds an SLCartDict
 * over a prefix dictionary, reads a length that is not a length, and walks
 * fifteen megabytes past the end.  The visible symptom -- one open() for seven
 * resources -- looks like five lookups failing when it is really five cache
 * hits succeeding.
 *
 * Tiger never noticed, because nothing in it asks twice for two different
 * files.  See the comment on file_ident() for why the answer is a table.
 */
#define DARWIN_S_IFREG 0x81a4                /* S_IFREG | 0644 */
#define DARWIN_S_IFDIR 0x41ed                /* S_IFDIR | 0755 */

/* Windows has a real file identity -- volume serial plus the 64-bit index from
 * GetFileInformationByHandle -- but it does not fit in the 32 bits Darwin has
 * for st_ino, and folding it down invites exactly the collision this code
 * exists to prevent.  So hand out small sequential ids instead and remember the
 * mapping: distinct files always differ, and the same file (by any path, or
 * through a hard link) always agrees, which is the contract the cache wants.
 */
#define MAX_IDENT 128
static struct { unsigned vol, hi, lo; } g_ident[MAX_IDENT];
static int g_nident;

static unsigned file_ident(HANDLE fh, unsigned *dev)
{
    BY_HANDLE_FILE_INFORMATION bi;
    int i;
    if (fh == INVALID_HANDLE_VALUE || !GetFileInformationByHandle(fh, &bi))
        return 0;
    for (i = 0; i < g_nident; i++)
        if (g_ident[i].vol == bi.dwVolumeSerialNumber &&
            g_ident[i].hi  == bi.nFileIndexHigh &&
            g_ident[i].lo  == bi.nFileIndexLow) break;
    if (i == g_nident) {
        if (g_nident >= MAX_IDENT) return 0;
        g_ident[i].vol = bi.dwVolumeSerialNumber;
        g_ident[i].hi  = bi.nFileIndexHigh;
        g_ident[i].lo  = bi.nFileIndexLow;
        g_nident++;
    }
    *dev = bi.dwVolumeSerialNumber ? bi.dwVolumeSerialNumber : 1;
    return (unsigned)i + 1;              /* never 0; 0 means "no identity" */
}

/* A filesystem that reports no file index still has to produce distinct keys,
 * so fold the path.  Case and separators are normalised because Windows would
 * hand back the same file under either. */
static unsigned path_ident(const char *path)
{
    unsigned h = 2166136261u;
    for (; path && *path; path++) {
        char c = *path;
        if (c == '\\') c = '/';
        if (c >= 'A' && c <= 'Z') c = (char)(c + 32);
        h = (h ^ (unsigned char)c) * 16777619u;
    }
    return h | 0x80000000u;              /* clear of the table's small ids */
}

static void darwin_stat_fill(const darwin_stat_layout *L, unsigned char *st,
                             long long size, unsigned dev, unsigned ino,
                             int isdir)
{
    memset(st, 0, L->bytes);
    *(unsigned *)(st + L->dev) = dev;
    *(unsigned *)(st + L->ino) = ino;
    *(unsigned short *)(st + L->mode) =
        (unsigned short)(isdir ? DARWIN_S_IFDIR : DARWIN_S_IFREG);
    *(long long *)(st + L->size) = size;
}

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

/* Leopard's engine imports GCC 4.0.1's C++ runtime -- std::string, the
 * _List_node_base helpers, __dynamic_cast and the RTTI behind it -- and those
 * have to be Leopard's own libstdc++ rather than a reimplementation: the engine
 * inlines code that walks basic_string's copy-on-write layout, so the bytes
 * have to agree exactly.
 *
 * Tiger's engine imports none of it, so this is entirely optional.  Found, it
 * is loaded and bound like any other image; absent, nothing changes.  Search
 * upwards from the dictionary, since the extracted tree keeps it at the root
 * and a real install keeps it in usr/lib.
 */
static int find_libstdcxx(const char *start, char *out, size_t n)
{
    /* **Newest first.**  Leopard shipped 6.0.4 and Lion 6.0.9, and the choice
     * is not cosmetic: Lion's SpeechDictionary inlines a `std::string` layout
     * that 6.0.4 does not have, so pairing them crashes in
     * `_Rep::_M_destroy` -- deep inside libstdc++, with the actual mismatch
     * named nowhere. A tree only ever holds the one its own system shipped, so
     * ordering by version picks the right one without having to ask.
     *
     * Lion's is in the installer's `BaseSystemBinaries.pkg` under `usr/lib`,
     * *not* in `BaseSystem.dmg` -- the copy at the root of that image is
     * x86_64 only, because Lion Recovery is, and it will not load here at all.
     */
    static const char *names[] = {
        "libstdc++.6.0.9.dylib",              /* Lion */
        "libstdc++.6.0.4.dylib",              /* Leopard */
        "libstdc++.6.dylib",                  /* usually a link to one of them */
        "usr/lib/libstdc++.6.0.9.dylib",
        "usr/lib/libstdc++.6.0.4.dylib",
        "usr/lib/libstdc++.6.dylib",
    };
    char dir[CFPATH];
    int level, i;
    if (!start) return 0;   /* `near` is still a keyword to MSVC */
    strncpy(dir, start, sizeof(dir) - 1);
    dir[sizeof(dir) - 1] = 0;
    for (level = 0; level < 6; level++) {
        char *a = strrchr(dir, '/');
        char *b = strrchr(dir, '\\');
        char *cut = (a > b) ? a : b;
        if (!cut) break;
        *cut = 0;
        for (i = 0; i < (int)(sizeof(names) / sizeof(names[0])); i++) {
            _snprintf(out, n, "%s/%s", dir, names[i]);
            out[n - 1] = 0;
            if (_access(out, 4) == 0) return 1;
        }
    }
    return 0;
}
static unsigned g_reads, g_read_bytes;
static int __cdecl sh_read(int fd, void *b, unsigned n)
{
    int got = _read(fd, b, n);
    g_reads++;
    if (got > 0) g_read_bytes += (unsigned)got;
    return got;
}
static int __cdecl sh_write(int fd, const void *b, unsigned n)
{ (void)fd; return (int)fwrite(b, 1, n, stderr); }

static int fstat_into(const darwin_stat_layout *L, int fd, unsigned char *st)
{
    struct _stat64 s;
    unsigned dev = 1, ino;
    if (!st) return -1;
    if (_fstat64(fd, &s) != 0) return -1;
    ino = file_ident((HANDLE)_get_osfhandle(fd), &dev);
    if (!ino) { dev = 1; ino = (unsigned)fd | 0x40000000u; }
    darwin_stat_fill(L, st, s.st_size, dev, ino, (s.st_mode & _S_IFDIR) != 0);
    return 0;
}

/* stat() by path, which is the one SLMMapCache actually calls.  Opening for
 * FILE_READ_ATTRIBUTES alone needs no read permission and does not disturb
 * anything; BACKUP_SEMANTICS is what lets the same call work on a directory. */
static int stat_into(const darwin_stat_layout *L, const char *path,
                     unsigned char *st)
{
    struct _stat64 s;
    HANDLE fh;
    unsigned dev = 1, ino;
    if (!path || !st) return -1;
    if (_stat64(path, &s) != 0) return -1;
    fh = CreateFileA(path, FILE_READ_ATTRIBUTES,
                     FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                     NULL, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, NULL);
    ino = file_ident(fh, &dev);
    if (fh != INVALID_HANDLE_VALUE) CloseHandle(fh);
    if (!ino) { dev = 1; ino = path_ident(path); }
    darwin_stat_fill(L, st, s.st_size, dev, ino, (s.st_mode & _S_IFDIR) != 0);
    return 0;
}

/* Four entry points over two bodies.  The `$INODE64` pair is 10.6 and later;
 * the bare pair is 10.5 and earlier.  Which one an image imports is decided by
 * the SDK it was built against, so a process only ever reaches one of each
 * pair -- but both have to exist, because the wrong one is not a link error
 * here, it is a stub that quietly reports success. */
static int __cdecl sh_fstat(int fd, unsigned char *st)
{ return fstat_into(&DARWIN_STAT, fd, st); }
static int __cdecl sh_stat(const char *path, unsigned char *st)
{ return stat_into(&DARWIN_STAT, path, st); }
static int __cdecl sh_fstat64(int fd, unsigned char *st)
{ return fstat_into(&DARWIN_STAT64, fd, st); }
static int __cdecl sh_stat64(const char *path, unsigned char *st)
{ return stat_into(&DARWIN_STAT64, path, st); }

/* ---- --stat-check ------------------------------------------------------ */
/*
 * Both layouts, filled for real files, decoded back at the offsets the engine
 * reads.  Needs no tree; see panthera/tests/test_stat_layout.py.  Written to
 * stdout for the reason `--dyld-check` is: in a check the report is the data.
 */
static void stat_check_one(const char *label, const darwin_stat_layout *L,
                           const char *a, const char *b, int *fails)
{
    unsigned char sa[160], sb[160];
    long long size;

    if (stat_into(L, a, sa) != 0 || stat_into(L, b, sb) != 0) {
        fprintf(stdout, "FAIL  %s: could not stat its own two files\n", label);
        (*fails)++;
        return;
    }
    size = *(long long *)(sa + L->size);
    fprintf(stdout, "[stat-check] %s field struct_size %u\n", label, L->bytes);
    fprintf(stdout, "[stat-check] %s field size_off %u\n", label, L->size);
    fprintf(stdout, "[stat-check] %s field ino_off %u\n", label, L->ino);
    /* The two properties the cache is keyed on: an identity at all, and a
     * different one per file.  Equal keys are silent -- the second lookup is
     * served the first file's bytes and never reaches open(). */
    fprintf(stdout, "[stat-check] %s field ino_nonzero %d\n", label,
            *(unsigned *)(sa + L->ino) != 0);
    fprintf(stdout, "[stat-check] %s field distinct %d\n", label,
            memcmp(sa, sb, 16) != 0);
    fprintf(stdout, "[stat-check] %s size %lld, mode %04x\n", label, size,
            *(unsigned short *)(sa + L->mode));
    if (size <= 0) {
        fprintf(stdout, "FAIL  %s: a file with contents measured %lld\n",
                label, size);
        (*fails)++;
    }
}

static int stat_check(void)
{
    /* Two files that certainly exist and certainly differ: this program, and
     * the directory holding it. */
    char me[MAX_PATH], dir[MAX_PATH];
    char *cut;
    int fails = 0;

    if (!GetModuleFileNameA(NULL, me, MAX_PATH)) {
        fprintf(stdout, "FAIL  cannot find my own path\n");
        return 1;
    }
    strncpy(dir, me, MAX_PATH - 1);
    dir[MAX_PATH - 1] = 0;
    cut = strrchr(dir, '\\');
    if (!cut) cut = strrchr(dir, '/');
    if (cut) *cut = 0;

    stat_check_one("stat",   &DARWIN_STAT,   me, dir, &fails);
    stat_check_one("stat64", &DARWIN_STAT64, me, dir, &fails);
    fprintf(stdout, "[stat-check] %d failure(s)\n", fails);
    return fails ? 1 : 0;
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
        printf("  [mmap] %u bytes of fd %d at offset %llu mapped -> %p\n",
               len, fd, (unsigned long long)off, (void *)(view + slack));
    return view + slack;

fallback:
    /* Never fatal: a mapping that cannot be made is still a file that can be
     * read, and for every voice but Alex the difference is only speed.
     *
     * It is not only speed if the offset is past 4 GB, though: the fallback
     * seeks with off_lo alone and would silently read the wrong part of the
     * file.  Say so rather than produce quietly wrong audio. */
    {
        void *p;
        if (off_hi)
            printf("  [mmap] REFUSING a fallback read at offset %llu -- past "
                   "4 GB, and the fallback cannot seek there\n",
                   (unsigned long long)off);
        p = off_hi ? (void *)-1 : mmap_fallback(len, fd, off_lo);
        if (g_verbose)
            printf("  [mmap] %u bytes of fd %d at offset %llu READ IN "
                   "(no mapping) -> %p\n", len, fd, (unsigned long long)off, p);
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
    if (g_verbose) printf("  [engine vsprintf] %s\n", buf);
    return n;
}
