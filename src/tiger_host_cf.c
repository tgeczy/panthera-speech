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
/* `kind` and `num` carry the tuning parameters further down: the engine wants
 * a CFNumber or a CFBoolean back from a preference lookup, not a string, and
 * it type-checks before reading.  A string object leaves both at zero, so
 * every path that existed before sees exactly what it saw before. */
#define CF_STRING  0
#define CF_NUMBER  1
#define CF_BOOLEAN 2

typedef struct { cfstring str; unsigned magic; long rc; char buf[CFPATH];
                 int kind; double num; }
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
/* Alex asks for these two and Vicki never does -- they are the only shims that
 * separate a voice that renders perfectly from one that does not, which is why
 * they are written out rather than thunked.
 *
 * GetParam looks a tuning parameter up in an override dictionary before
 * falling back to CFPreferences. Handing back NULL for the *key itself* is not
 * the same as saying "that key is absent": the engine never gets to ask the
 * question, and what it does with the answer it never received is its own
 * business. Building a real string costs four lines. */
static void * __cdecl sh_CFStringCreateWithCStringNoCopy(void *alloc,
                                                         const char *cstr,
                                                         unsigned enc,
                                                         void *dealloc)
{
    (void)alloc; (void)enc; (void)dealloc;
    return cstr ? (void *)cf_new(cstr) : NULL;
}

/* An empty override dictionary is a truthful answer -- there is no override --
 * and it is what lets GetParam fall through to its own default. */
static void * __cdecl sh_CFDictionaryGetValue(void *dict, void *key)
{
    (void)dict; (void)key;
    return NULL;
}

static void * __cdecl sh_CFURLCopyFileSystemPath(void *url, int style)
{ (void)style; return url ? cf_new(cf_cstr(url)) : NULL; }
/* SpeechDictionary reaches for CFURLCopyPath rather than the filesystem-path
 * variant MacinTalk uses.  Same thing here -- our URLs only ever hold a path. */
static void * __cdecl sh_CFURLCopyPath(void *url)
{ return url ? cf_new(cf_cstr(url)) : NULL; }

/* ---- the engine's own tuning parameters ------------------------------- */
/*
 * Leopard's MacinTalk looks up 283 named settings while it speaks -- see
 * docs/engine-tunables.md for the list and how to regenerate it -- and until
 * now every one of them was answered with nothing, so the engine used its
 * compiled-in defaults.  Two of them decide when a phrase gets a break:
 *
 *     Boundaries.PhrThreshold      how strong a candidate has to be
 *     Boundaries.SilThreshold      the same before a silence is inserted
 *     Boundaries.Debug             makes the engine report what it decided
 *
 * and six more, BreathIntake.*, decide where it breathes.  Listeners describe
 * the defaults as putting words "into quotes" -- a break arriving mid-clause
 * where an author wrote none.
 *
 * The engine reaches them by asking an override dictionary first and
 * CFPreferences second.  Both are ours, and CFPreferences is the documented
 * fallback rather than a side door, so that is where the answer goes.
 *
 * **Empty unless asked.**  With no TIGER_PARAMS set the lookup returns NULL
 * exactly as the generic thunk did, so nothing changes for anyone who has not
 * opted in.  That is also what keeps Tiger safe by construction: MacinTalk 3.3
 * has no __cfstring section at all and never asks a single one of these.
 *
 *     set TIGER_PARAMS=Boundaries.Debug=1;Boundaries.SilThreshold=0.9
 *
 * or, when there is no useful environment to set one in, a `params.txt` beside
 * this executable, one `Name = Value` per line and `#` to the end of a line
 * for a comment.  **The file is the one that works from NVDA**: the driver
 * starts this host as a child, so it inherits the environment NVDA itself was
 * started with, and a variable set afterwards -- with `setx`, or in the System
 * panel -- does not reach it until the whole session is restarted.  That cost
 * a round of "it sounds exactly the same" before the log showed the host had
 * never been told anything.  It is also the shape a checkbox wants: a driver
 * setting writes the file, rather than trying to reach into a child's
 * environment.
 *
 * Values are read as numbers, or as booleans when written true/false/yes/no/
 * on/off.  The engine type-checks with CFGetTypeID before reading, so what
 * comes back has to be a real CFNumber or CFBoolean.
 */
#define CF_TYPEID_NUMBER      22        /* the real CoreFoundation values, so */
#define CF_TYPEID_BOOLEAN     21        /* a stray comparison cannot collide  */
#define CF_TYPEID_DICTIONARY  18
#define CF_MAX_PARAMS         64

typedef struct { char key[64]; cfobj *val; } cfparam;
static cfparam g_params[CF_MAX_PARAMS];
static int     g_nparams;

static cfobj *cf_value(int kind, double v)
{
    /* Pinned: these are returned over and over, once per utterance, and a
     * "Copy" function hands ownership to the engine, which releases it.  A
     * fresh object per lookup would be correct and would churn; one pinned
     * object per key is bounded and outlives every release. */
    cfobj *o = cf_make("", CF_PINNED);
    if (!o) return NULL;
    o->kind = kind;
    o->num  = v;
    return o;
}

/* TIGER_PARAMS=Name=Value;Name=Value -- ';' or ',' between, spaces ignored. */
/* -> 1 if `params.txt` beside this executable was read into buf. */
static int cf_params_file(char *buf, size_t n, char *shown, size_t shownlen)
{
    char path[CFPATH];
    char *cut;
    FILE *f;
    size_t got;

    if (!GetModuleFileNameA(NULL, path, (DWORD)sizeof(path) - 1)) return 0;
    path[sizeof(path) - 1] = 0;
    cut = strrchr(path, '\\');
    if (!cut) cut = strrchr(path, '/');
    if (!cut) return 0;
    strncpy(cut + 1, "params.txt", sizeof(path) - (size_t)(cut + 1 - path) - 1);
    path[sizeof(path) - 1] = 0;

    f = fopen(path, "rb");
    if (!f) return 0;
    got = fread(buf, 1, n - 1, f);
    fclose(f);
    buf[got] = 0;
    /* A comment runs to the end of its line, and a newline separates entries
     * exactly as ';' does. */
    for (; *buf ? 1 : 0; buf++) {
        if (*buf == '#') { while (*buf && *buf != '\n') *buf++ = ' '; if (!*buf) break; }
        if (*buf == '\r' || *buf == '\n') *buf = ';';
    }
    _snprintf(shown, shownlen, "%s", path);
    shown[shownlen - 1] = 0;
    return 1;
}

static void cf_params_init(void)
{
    const char *env = getenv("TIGER_PARAMS");
    char buf[2048];
    char from[CFPATH];
    char *p;

    if (env && *env) {
        strncpy(buf, env, sizeof(buf) - 1);
        buf[sizeof(buf) - 1] = 0;
        strcpy(from, "TIGER_PARAMS");
    } else if (!cf_params_file(buf, sizeof(buf), from, sizeof(from))) {
        return;
    }
    fprintf(stderr, "tiger_host: reading engine parameters from %s\n", from);

    for (p = buf; *p; ) {
        char *name = p, *eq, *end;
        int kind = CF_NUMBER;
        double v = 0.0;

        while (*p && *p != ';' && *p != ',') p++;
        if (*p) *p++ = 0;
        while (*name == ' ' || *name == '\t') name++;
        if (!*name) continue;

        eq = strchr(name, '=');
        if (!eq) {
            fprintf(stderr, "tiger_host: TIGER_PARAMS entry '%s' has no "
                            "value, ignored\n", name);
            continue;
        }
        *eq = 0;
        end = eq + 1;
        while (*end == ' ' || *end == '\t') end++;
        /* `Name = Value` from a file leaves spaces the environment form never
         * had.  An untrimmed name never matches the key the engine asks for,
         * and an untrimmed value is not the word "true". */
        {
            char *z = eq;
            while (z > name && (z[-1] == ' ' || z[-1] == '\t')) *--z = 0;
            z = end + strlen(end);
            while (z > end && (z[-1] == ' ' || z[-1] == '\t')) *--z = 0;
        }

        if (!_stricmp(end, "true") || !_stricmp(end, "yes") ||
            !_stricmp(end, "on"))   { kind = CF_BOOLEAN; v = 1.0; }
        else if (!_stricmp(end, "false") || !_stricmp(end, "no") ||
                 !_stricmp(end, "off")) { kind = CF_BOOLEAN; v = 0.0; }
        else {
            char *stop = NULL;
            v = strtod(end, &stop);
            if (!stop || stop == end) {
                fprintf(stderr, "tiger_host: TIGER_PARAMS value '%s' for %s "
                                "is not a number, ignored\n", end, name);
                continue;
            }
        }
        if (g_nparams >= CF_MAX_PARAMS) {
            fprintf(stderr, "tiger_host: more than %d parameters, '%s' "
                            "ignored\n", CF_MAX_PARAMS, name);
            continue;
        }
        strncpy(g_params[g_nparams].key, name,
                sizeof(g_params[0].key) - 1);
        g_params[g_nparams].val = cf_value(kind, v);
        if (!g_params[g_nparams].val) continue;
        /* Say it out loud: a setting that silently failed to apply is the
         * shape of half the bugs this project has had. */
        if (kind == CF_BOOLEAN)
            fprintf(stderr, "tiger_host: parameter %s = %s\n",
                    g_params[g_nparams].key, v != 0.0 ? "true" : "false");
        else
            fprintf(stderr, "tiger_host: parameter %s = %g\n",
                    g_params[g_nparams].key, v);
        g_nparams++;
    }
}

static void * __cdecl sh_CFPreferencesCopyAppValue(const void *key,
                                                   const void *appid)
{
    const char *k = cf_cstr(key);
    int i;
    (void)appid;
    /* Which of the 283 are *actually* consulted is not the same question as
     * which exist, and the difference matters: a setting the engine never asks
     * for cannot be changed by answering it.
     *
     * TIGER_PREF_LOG rather than g_verbose, because serve mode turns g_verbose
     * off the moment it starts answering requests -- and every one of these
     * lookups happens during an utterance, so gating on it prints nothing at
     * all and reads as "the engine never asks".  Logged before the null check
     * too: behind it, a key we failed to read is indistinguishable from a key
     * that was never asked for. */
    if (g_pref_log)
        fprintf(stderr, "  [pref] asked for %s\n",
                k ? k : "(unreadable key)");
    if (!k) return NULL;
    for (i = 0; i < g_nparams; i++)
        if (!strcmp(g_params[i].key, k)) {
            if (g_verbose)
                printf("  [cf] parameter %s -> %g\n", k, g_params[i].val->num);
            return g_params[i].val;
        }
    return NULL;                         /* what it has always answered */
}

static unsigned long __cdecl sh_CFGetTypeID(const void *o)
{
    if (!cf_ours(o)) return 0;
    switch (((const cfobj *)o)->kind) {
    case CF_NUMBER:  return CF_TYPEID_NUMBER;
    case CF_BOOLEAN: return CF_TYPEID_BOOLEAN;
    default:         return 0;
    }
}
static unsigned long __cdecl sh_CFNumberGetTypeID(void)
{ return CF_TYPEID_NUMBER; }
static unsigned long __cdecl sh_CFBooleanGetTypeID(void)
{ return CF_TYPEID_BOOLEAN; }
static unsigned long __cdecl sh_CFDictionaryGetTypeID(void)
{ return CF_TYPEID_DICTIONARY; }

static int __cdecl sh_CFBooleanGetValue(const void *o)
{ return cf_ours(o) && ((const cfobj *)o)->num != 0.0; }

/* CFNumberType, from CFNumber.h.  The engine asks for whichever C type the
 * variable it is filling happens to be, so all of them are answered. */
static int __cdecl sh_CFNumberGetValue(const void *o, int type, void *out)
{
    double v;
    if (!cf_ours(o) || !out) return 0;
    v = ((const cfobj *)o)->num;
    switch (type) {
    case 1:  case 7:  *(char *)out           = (char)v;      break;
    case 2:  case 8:  *(short *)out          = (short)v;     break;
    case 3:  case 9:  case 14: case 15:
                      *(int *)out            = (int)v;       break;
    case 10:          *(long *)out           = (long)v;      break;
    case 4:  case 11: *(__int64 *)out        = (__int64)v;   break;
    case 5:  case 12: case 16:
                      *(float *)out          = (float)v;     break;
    case 6:  case 13: *(double *)out         = v;            break;
    default:
        fprintf(stderr, "tiger_host: CFNumberGetValue asked for type %d, "
                        "which this does not know; leaving it alone\n", type);
        return 0;
    }
    return 1;
}
