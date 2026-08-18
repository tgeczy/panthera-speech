/* tiger_host_cf.c -- a very small CoreFoundation.
 *
 * Part of tiger_host.c, which includes it; see there for why this is one
 * translation unit. */

/* ---- a very small CoreFoundation -------------------------------------- */
/*
 * Only as much as the voice loader touches, which turns out to be a short
 * chain.  `SpeechChannelManager::ReadVoiceData` does exactly this:
 *
 *     CFBundleCopyResourceURL(bundle, CFSTR("VoiceDescription"), 0, 0)
 *     CFURLCopyFileSystemPath(url, kCFURLPOSIXPathStyle)
 *     CFStringGetCStringPtr(path, 0)       <- returning non-NULL here skips
 *     open(path, O_RDONLY)                    the GetLength/GetCString path
 *     fstat, mmap
 *
 * and repeats it for CFSTR("PCMWave").
 *
 * A CFSTR constant in this binary is {isa, flags, cstr, length} -- verified
 * against the two the engine uses: "VoiceDescription" at 0x58600 carries
 * cstr=0x3d2c0 len=16.  `isa` is filled by an external relocation we never
 * apply, and nothing here reads it, so strings we create use the same shape
 * and both kinds flow through one accessor.
 *
 * The bundle is ours to define: it arrives as argument 3 of SEUseVoice and the
 * engine only ever hands it back to CFBundleCopyResourceURL.  So it is simply
 * the directory of a .SpeechVoice.
 */
#define CF_MAGIC 0x54494743u            /* 'TIGC' */
#define CFPATH   512

typedef struct { void *isa; unsigned flags; const char *cstr; unsigned len; }
        cfstring;
/* The cfstring MUST come first: the engine holds these as opaque CFStringRefs
 * and our accessors cast straight to cfstring.  Burying it behind a header
 * made every lookup read `flags` as the char pointer and return NULL, which
 * looked exactly like "the resource is missing". */
typedef struct { cfstring str; unsigned magic; long rc; char buf[CFPATH]; }
        cfobj;

/* Bundles handed back by CFBundleGetBundleWithIdentifier are *not* owned by
 * the caller -- "Get" rules -- but the engine still retains and releases them,
 * and a shim that frees on the last release destroys the one object every
 * later resource lookup needs.  That produced a genuinely intermittent
 * SLCartDict(NULL): freed memory reads back plausibly often enough that the
 * failure came and went between runs.  Pinned objects start with a refcount
 * that cannot reach zero. */
#define CF_PINNED 0x40000000

static void *g_cfstring_class;           /* ___CFConstantStringClassReference */

static cfobj *cf_make(const char *path, long rc)
{
    cfobj *o = (cfobj *)calloc(1, sizeof(*o));
    if (!o) return NULL;
    o->magic = CF_MAGIC;
    o->rc    = rc;
    strncpy(o->buf, path, CFPATH - 1);
    o->str.isa  = &g_cfstring_class;
    o->str.cstr = o->buf;
    o->str.len  = (unsigned)strlen(o->buf);
    return o;
}
static cfobj *cf_new(const char *path)    { return cf_make(path, 1); }
static cfobj *cf_pinned(const char *path) { return cf_make(path, CF_PINNED); }

static int cf_ours(const void *o)
{
    return o && ((const cfstring *)o)->isa == &g_cfstring_class &&
           ((const cfobj *)o)->magic == CF_MAGIC;
}

static const char *cf_cstr(const void *s)
{
    return s ? ((const cfstring *)s)->cstr : NULL;
}

static const char * __cdecl sh_CFStringGetCStringPtr(const void *s, unsigned e)
{
    const char *p = cf_cstr(s);
    (void)e;
    if (!p) printf("  [cf] GetCStringPtr(%p) -> NULL\n", s);
    return p;
}
static int __cdecl sh_CFStringGetLength(const void *s)
{ return s ? (int)((const cfstring *)s)->len : 0; }
static int __cdecl sh_CFStringGetMaximumSizeForEncoding(int len, unsigned e)
{ (void)e; return len * 4 + 1; }
static int __cdecl sh_CFStringGetCString(const void *s, char *buf, int sz,
                                         unsigned e)
{
    const char *p = cf_cstr(s);
    (void)e;
    if (!p || !buf || (int)strlen(p) + 1 > sz) return 0;
    strcpy(buf, p);
    return 1;
}
/* Leopard's SpeechDictionary asks for paths this way rather than with
 * CFStringGetCString.  Same answer here: the strings we hand out are already
 * filesystem paths in the host's own encoding. */
static int __cdecl sh_CFStringGetMaximumSizeOfFileSystemRepresentation(
    const void *s)
{
    const char *p = cf_cstr(s);
    return (int)(p ? strlen(p) + 1 : CFPATH);
}
static int __cdecl sh_CFStringGetFileSystemRepresentation(const void *s,
                                                          char *buf, int sz)
{
    const char *p = cf_cstr(s);
    if (!p || !buf || (int)strlen(p) + 1 > sz) return 0;
    strcpy(buf, p);
    return 1;
}
static void * __cdecl sh_CFRetain(void *o)
{
    if (cf_ours(o)) InterlockedIncrement(&((cfobj *)o)->rc);
    return o;
}
/* Only free objects we made.  The engine also passes CFSTR constants through
 * here, and those live in __const.  `isa` discriminates: ours points at our
 * class variable, theirs is left at zero by the external relocation we never
 * apply. */
/* Freeing on the last release is correct and breaks the engine.
 *
 * `CFStringGetCStringPtr` returns a pointer *into* the string object, and
 * SpeechDictionary releases the string before calling open() on that pointer.
 * On a real Mac the bug survives because the memory has not been reused yet;
 * here it produced an intermittent `open("g")` -- a path one character long,
 * salvaged from a recycled block -- roughly one run in ten.
 *
 * So retire objects into a bounded graveyard instead: the last CF_GRAVE of
 * them stay readable, and anything older is genuinely freed.  Bounded memory,
 * and dangling reads keep working exactly as far as they did on the original.
 */
#define CF_GRAVE 64
static void  *g_grave[CF_GRAVE];
static long   g_grave_i;

static void   __cdecl sh_CFRelease(void *o)
{
    if (cf_ours(o) && InterlockedDecrement(&((cfobj *)o)->rc) <= 0) {
        long i = InterlockedIncrement(&g_grave_i) & (CF_GRAVE - 1);
        void *old = InterlockedExchangePointer(&g_grave[i], o);
        free(old);
    }
}

/* The bundle for a voice: its .SpeechVoice directory.  Resources live at
 * Contents/Resources, which is what a real CFBundle would look up too. */
/* Resources sit at Contents/Resources in an application-style bundle and at
 * Resources in a framework version directory.  SpeechDictionary is the second
 * kind, so trying only the first silently loses StdDictionary -- and a missing
 * dictionary produces no phonemes, which looks like a synthesiser that runs
 * and emits nothing. */
static const char *k_res_layouts[] = { "Contents/Resources", "Resources", "" };

static void * __cdecl sh_CFBundleCopyResourceURL(const void *bundle,
                                                 const void *name,
                                                 const void *type,
                                                 const void *subdir)
{
    char path[CFPATH];
    const char *dir = cf_cstr(bundle), *nm = cf_cstr(name);
    int i;
    (void)type; (void)subdir;
    if (!dir || !nm) return NULL;
    for (i = 0; i < 3; i++) {
        if (*k_res_layouts[i])
            _snprintf(path, sizeof(path), "%s/%s/%s", dir, k_res_layouts[i], nm);
        else
            _snprintf(path, sizeof(path), "%s/%s", dir, nm);
        path[sizeof(path) - 1] = 0;
        if (_access(path, 4) == 0) {
            if (g_verbose) printf("  [cf] resource -> %s\n", path);
            return cf_new(path);
        }
    }
    if (g_verbose) printf("  [cf] no resource '%s' under %s\n", nm, dir);
    return NULL;
}

/* SpeechDictionary asks for its own bundle by identifier in order to find
 * StdDictionary, CartLite, CartNames and PhonemeSymbols.  We know where it is
 * -- it is the framework we loaded -- so answer with its directory. */
static cfobj *g_dict_bundle;
static void * __cdecl sh_CFBundleGetBundleWithIdentifier(const void *id)
{
    const char *s = cf_cstr(id);
    if (g_verbose) printf("  [cf] GetBundleWithIdentifier '%s' -> %s\n", s ? s : "(null)",
           g_dict_bundle ? g_dict_bundle->buf : "(none)");
    return g_dict_bundle;
}
static void * __cdecl sh_CFURLCopyFileSystemPath(void *url, int style)
{ (void)style; return url ? cf_new(cf_cstr(url)) : NULL; }
/* SpeechDictionary reaches for CFURLCopyPath rather than the filesystem-path
 * variant MacinTalk uses.  Same thing here -- our URLs only ever hold a path. */
static void * __cdecl sh_CFURLCopyPath(void *url)
{ return url ? cf_new(cf_cstr(url)) : NULL; }
