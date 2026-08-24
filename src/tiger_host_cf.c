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
/* Lion's SpeechDictionary reads its tables through CFURL rather than by
 * opening a path, so it needs a CFData to read them into.  `bytes` is heap,
 * not `buf`: these are whole dictionary files. */
#define CF_DATA    3

/* `big` holds a string too long for `buf`, and it exists because of a bug that
 * reached users.
 *
 * `CFPATH` is 512 because these objects began life carrying **file paths**,
 * and `cf_make` truncated to 511 characters with `strncpy`.  Then Lion started
 * speaking through them: 10.7 takes its text as a `CFStringRef` rather than as
 * a buffer and a length, so every utterance passes through here -- and
 * anything past 511 characters was silently cut, mid-word, in every Lion
 * voice.
 *
 * Reported by Amir, who found the boundary from outside and supplied the exact
 * paragraph: *"if a paragraph contains more than 500 characters, Alex skips or
 * omits the rest... the following paragraph is cut on the word date"*.
 * Measured afterwards: "dates" begins at character 496, and the audio stops
 * growing at exactly 511.
 *
 * Leopard never had it -- `SESpeakBuffer` takes the pointer and the length and
 * never comes near this.  Nor was it a `meow` fault, though it looked like
 * one: Fred, Kathy and Bruce are cut identically, and that is what said the
 * cause was above the voice rather than inside it. */
/* `macroman` says the bytes are an **utterance**, and it exists because the
 * two kinds of string in here are in two different encodings.
 *
 * Text arrives already encoded by the driver with Python's `mac_roman` codec.
 * Everything else -- file paths, table names, preference keys -- is bytes in
 * whatever Windows handed us, which above 0x7F is Latin-1 and includes the
 * accented characters in somebody's user folder name.  Widening both the same
 * way has to be wrong for one of them, so the object remembers which it is
 * rather than a shim guessing from the bytes.
 *
 * Before this, both widened by zero-extension.  Paths were right; **every
 * accented character in a Lion utterance became a C1 control and vanished** --
 * `á` is MacRoman 0x87, which zero-extends to U+0087.  Tiger, Leopard and
 * Snow Leopard never had it: they hand the engine the raw bytes through
 * `SESpeakBuffer` and its own front end decodes MacRoman itself. */
typedef struct { cfstring str; unsigned magic; long rc; char buf[CFPATH];
                 int kind; double num; int macroman;
                 unsigned char *bytes; unsigned nbytes; char *big; }
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
    size_t n;
    cfobj *o = (cfobj *)calloc(1, sizeof(*o));
    if (!o) return NULL;
    o->magic = CF_MAGIC;
    o->rc    = rc;
    if (!path) path = "";
    n = strlen(path);
    /* Short strings -- every path, key and table name -- still live in `buf`,
     * so the common case allocates nothing extra.  Only an utterance is ever
     * likely to need the heap, and a fixed buffer of any size would only move
     * the cliff: NVDA hands over whatever the user asked it to read, and a
     * clipboard can be tens of kilobytes. */
    if (n < CFPATH) {
        memcpy(o->buf, path, n + 1);
        o->str.cstr = o->buf;
    } else {
        o->big = (char *)malloc(n + 1);
        if (!o->big) { free(o); return NULL; }
        memcpy(o->big, path, n + 1);
        o->str.cstr = o->big;
    }
    o->str.isa  = &g_cfstring_class;
    o->str.len  = (unsigned)n;
    return o;
}
static cfobj *cf_new(const char *path)    { return cf_make(path, 1); }
static cfobj *cf_pinned(const char *path) { return cf_make(path, CF_PINNED); }

/* An utterance, rather than a path.  The only caller is `speak_text`.
 *
 * A separate constructor rather than a flag argument on `cf_new`, because
 * every one of the twenty-odd other call sites would then have to answer a
 * question none of them has any business being asked. */
static cfobj *cf_text(const char *s)
{
    cfobj *o = cf_make(s, 1);
    if (o) o->macroman = 1;
    return o;
}

static int cf_ours(const void *o)
{
    return o && ((const cfstring *)o)->isa == &g_cfstring_class &&
           ((const cfobj *)o)->magic == CF_MAGIC;
}

static const char *cf_cstr(const void *s)
{
    return s ? ((const cfstring *)s)->cstr : NULL;
}

/* MacRoman 0x80-0xFF as Unicode, for the one caller that needs it.
 *
 * **Generated from Python's own `mac_roman` codec**, not typed out: the driver
 * encodes with that exact codec, so anything hand-entered here would be a
 * second opinion about the same table, and the entries that differ would be
 * the accented ones nobody types in a test.
 *
 * Below 0x80 MacRoman is ASCII, so this starts at 0x80 and everything under it
 * still widens by zero-extension -- which is why no ASCII render can move.
 */
static const unsigned short CF_MACROMAN[128] = {
    0x00C4, 0x00C5, 0x00C7, 0x00C9, 0x00D1, 0x00D6, 0x00DC, 0x00E1,
    0x00E0, 0x00E2, 0x00E4, 0x00E3, 0x00E5, 0x00E7, 0x00E9, 0x00E8,
    0x00EA, 0x00EB, 0x00ED, 0x00EC, 0x00EE, 0x00EF, 0x00F1, 0x00F3,
    0x00F2, 0x00F4, 0x00F6, 0x00F5, 0x00FA, 0x00F9, 0x00FB, 0x00FC,
    0x2020, 0x00B0, 0x00A2, 0x00A3, 0x00A7, 0x2022, 0x00B6, 0x00DF,
    0x00AE, 0x00A9, 0x2122, 0x00B4, 0x00A8, 0x2260, 0x00C6, 0x00D8,
    0x221E, 0x00B1, 0x2264, 0x2265, 0x00A5, 0x00B5, 0x2202, 0x2211,
    0x220F, 0x03C0, 0x222B, 0x00AA, 0x00BA, 0x03A9, 0x00E6, 0x00F8,
    0x00BF, 0x00A1, 0x00AC, 0x221A, 0x0192, 0x2248, 0x2206, 0x00AB,
    0x00BB, 0x2026, 0x00A0, 0x00C0, 0x00C3, 0x00D5, 0x0152, 0x0153,
    0x2013, 0x2014, 0x201C, 0x201D, 0x2018, 0x2019, 0x00F7, 0x25CA,
    0x00FF, 0x0178, 0x2044, 0x20AC, 0x2039, 0x203A, 0xFB01, 0xFB02,
    0x2021, 0x00B7, 0x201A, 0x201E, 0x2030, 0x00C2, 0x00CA, 0x00C1,
    0x00CB, 0x00C8, 0x00CD, 0x00CE, 0x00CF, 0x00CC, 0x00D3, 0x00D4,
    0xF8FF, 0x00D2, 0x00DA, 0x00DB, 0x00D9, 0x0131, 0x02C6, 0x02DC,
    0x00AF, 0x02D8, 0x02D9, 0x02DA, 0x00B8, 0x02DD, 0x02DB, 0x02C7
};

/* -> the UniChar for one byte of `s`.
 *
 * The choice is the object's, not this function's: **text is MacRoman and
 * everything else is not.**  See `cf_text` for why that distinction has to be
 * carried rather than guessed. */
static unsigned short cf_uni(const cfobj *o, unsigned char c)
{
    if (c < 0x80 || !o || !o->macroman) return (unsigned short)c;
    return CF_MACROMAN[c - 0x80];
}

/* TEMPORARY: which accessors does the engine read an utterance through, and
 * does it see all of it?  Removed once the tune question is answered. */
static int g_cflog;
#define CFLOG(who, s) do { if (g_cflog) { const char *_p = cf_cstr(s); \
    printf("  [cflog] %-22s len=%d \"%.90s\"\n", who, \
           s ? (int)((const cfstring *)(s))->len : -1, _p ? _p : "(null)"); } \
    } while (0)

static const char * __cdecl sh_CFStringGetCStringPtr(const void *s, unsigned e)
{
    const char *p = cf_cstr(s);
    (void)e;
    CFLOG("GetCStringPtr", s);
    if (!p) printf("  [cf] GetCStringPtr(%p) -> NULL\n", s);
    return p;
}
static int __cdecl sh_CFStringGetLength(const void *s)
{ CFLOG("GetLength", s); return s ? (int)((const cfstring *)s)->len : 0; }
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
/* The lexer reads the text a character at a time, in UTF-16.
 *
 * `SLLexerBuffer::operator[]` indexes a buffer this fills, so a stub left the
 * dictionary tokenising uninitialised memory -- and it got as far as
 * `SLPostLexerImpl::HasApostrophe` before dying on a read of address 4, which
 * names neither the string nor the shim that never filled it.
 *
 * `CFRange` is two words passed by value, so it arrives as two arguments.
 *
 * **This is the only place a Lion utterance is ever read**, which was measured
 * rather than assumed: logging every string accessor through one accented
 * render shows this one, called once, and none of the others.  So it is also
 * the only place the MacRoman question had to be answered -- see `cf_uni`, and
 * the comment on `cfobj` for what answering it wrong cost.
 *
 * Length needs no adjustment for that: MacRoman is single-byte and every byte
 * is exactly one UniChar, so the byte count `GetLength` returns is the
 * character count too.
 */
static void __cdecl sh_CFStringGetCharacters(const void *s, int loc, int len,
                                             unsigned short *buf)
{
    const char *p = cf_cstr(s);
    int i, n;
    if (g_cflog) printf("  [cflog] GetCharacters loc=%d len=%d of %d\n",
                        loc, len, p ? (int)strlen(p) : -1);
    if (!buf || len <= 0) return;
    if (!p) { memset(buf, 0, (size_t)len * 2); return; }
    n = (int)strlen(p);
    for (i = 0; i < len; i++) {
        int k = loc + i;
        buf[i] = (k >= 0 && k < n)
            ? cf_uni((const cfobj *)s, (unsigned char)p[k]) : 0;
    }
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
        /* `old` has already been evicted from the ring, so its string is as
         * safe to release as the object itself -- and it has to be, because a
         * paragraph's worth of text per utterance is not a leak anybody would
         * notice until they read a book. */
        if (old) free(((cfobj *)old)->big);
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

/* Lion's SpeechDictionary reads its tables through the URL, not the path.
 *
 * Leopard's opens `HomophonesEng` and the rest with `open`/`fstat`/`mmap`, all
 * of which are shimmed. Lion's asks CoreFoundation to hand it the bytes, and
 * the stub answered `false` -- so a member that should have held the table
 * stayed NULL and the post-lexer read address 4 on the first word it wanted
 * from it, several calls later and in a function whose name says apostrophes.
 *
 * The last argument is an out-parameter for an error code, and the caller is
 * entitled to look at it whether or not the read worked.
 */
static cfobj *cf_data(const char *path)
{
    cfobj *o;
    FILE *f = fopen(path, "rb");
    long n;
    if (!f) return NULL;
    fseek(f, 0, SEEK_END); n = ftell(f); fseek(f, 0, SEEK_SET);
    o = cf_new(path);
    if (!o) { fclose(f); return NULL; }
    o->kind = CF_DATA;
    o->bytes = (unsigned char *)malloc(n > 0 ? (size_t)n : 1);
    if (!o->bytes) { fclose(f); return NULL; }
    o->nbytes = (unsigned)fread(o->bytes, 1, (size_t)n, f);
    fclose(f);
    return o;
}

static int __cdecl sh_CFURLCreateDataAndPropertiesFromResource(
        void *alloc, const void *url, void **outData, void **outProps,
        const void *desired, int *errorCode)
{
    const char *path = cf_cstr(url);
    cfobj *d;
    (void)alloc; (void)desired;
    if (outProps) *outProps = NULL;
    if (!path || !outData) {
        if (errorCode) *errorCode = -10;            /* unknown scheme */
        return 0;
    }
    d = cf_data(path);
    if (!d) {
        if (g_verbose) printf("  [cf] cannot read %s\n", path);
        if (errorCode) *errorCode = -15;            /* resource not found */
        return 0;
    }
    if (g_verbose)
        printf("  [cf] read %u bytes of %s\n", d->nbytes, path);
    *outData = d;
    if (errorCode) *errorCode = 0;
    return 1;
}

static const void * __cdecl sh_CFDataGetBytePtr(const void *o)
{
    const cfobj *d = (const cfobj *)o;
    return (d && d->magic == CF_MAGIC && d->kind == CF_DATA) ? d->bytes : NULL;
}

static int __cdecl sh_CFDataGetLength(const void *o)
{
    const cfobj *d = (const cfobj *)o;
    return (d && d->magic == CF_MAGIC && d->kind == CF_DATA)
           ? (int)d->nbytes : 0;
}

/* The token layer hands text back the other way round.
 *
 * `SLTokenGetText` does not return a string it was given -- it *builds* one,
 * out of a UTF-16 range it holds as a pair of pointers, and caches it at
 * `tok+0x24`.  `SLHomographGetPhonemes` is the same shape over CFData.  Left
 * stubbed, the cache is filled with NULL and the front end reads straight
 * through it: the crash lands in `MTFEBuilder::PeekToken`, which has nothing
 * to do with either function.
 *
 * **Believe the count, not a terminator.**  The engine passes
 * `(end - begin) / 2 - 1`: the range holds a terminator the count excludes, so
 * a shim that scanned for a NUL instead would be right almost always and one
 * character wrong exactly when it mattered.
 *
 * `NoCopy` names the caller's promise to keep the buffer alive, not ours to
 * borrow it.  These are single tokens, so copying costs nothing and removes a
 * lifetime this host would otherwise have to reason about.
 *
 * Narrowing is the inverse of `CFStringGetCharacters`, which widens byte to
 * UniChar; see `engine-text-encoding` for why that pairing is Latin-1 here and
 * why the engine's own front end reads MacRoman. */
static void * __cdecl sh_CFStringCreateWithCharactersNoCopy(
        void *alloc, const unsigned short *chars, int n, void *dealloc)
{
    char buf[CFPATH];
    int i;
    (void)alloc; (void)dealloc;
    if (!chars || n < 0) return NULL;
    if (n > CFPATH - 1) n = CFPATH - 1;
    for (i = 0; i < n; i++)
        buf[i] = chars[i] < 0x100 ? (char)chars[i] : '?';
    buf[n] = '\0';
    return cf_new(buf);
}

static unsigned short __cdecl sh_CFStringGetCharacterAtIndex(const void *s,
                                                             int i)
{
    const char *p = cf_cstr(s);
    if (!p || i < 0 || i >= (int)strlen(p)) return 0;
    /* Nothing measured reads an utterance this way -- only `GetCharacters`
     * does -- but the two must not be able to disagree about one string. */
    return cf_uni((const cfobj *)s, (unsigned char)p[i]);
}

static void * __cdecl sh_CFDataCreateWithBytesNoCopy(void *alloc,
        const unsigned char *b, int n, void *dealloc)
{
    cfobj *o;
    (void)alloc; (void)dealloc;
    if (!b || n < 0) return NULL;
    o = cf_new("");
    if (!o) return NULL;
    o->kind  = CF_DATA;
    o->bytes = (unsigned char *)malloc(n ? (size_t)n : 1);
    if (!o->bytes) { free(o); return NULL; }
    memcpy(o->bytes, b, (size_t)n);
    o->nbytes = (unsigned)n;
    return o;
}

/* Lion's lexer takes a locale.
 *
 * `SLLexer::Create(SLTextSource*, SLDictLookup*, SLPronouncer*,
 * const __CFLocale*, unsigned)` is where the text pipeline starts, and with
 * `CFLocaleCreate` stubbed it starts with NULL. What came back was not a null
 * dereference but **heap corruption inside ntdll** -- so the report named a
 * Windows allocator function and no part of the actual mistake.
 *
 * One locale, pinned, because these follow Get rules in places and a refcount
 * that reaches zero would free the object every later lookup needs -- the same
 * trap the bundle objects above are pinned for.
 */
static cfobj *g_locale;

static void * __cdecl sh_CFLocaleCreate(void *alloc, const void *ident)
{
    const char *name = cf_cstr(ident);
    (void)alloc;
    if (!g_locale) g_locale = cf_pinned(name && *name ? name : "en_US");
    return g_locale;
}

/* Every key answered with the language code.
 *
 * The keys are CFString constants the engine imports as *data*, so an
 * unresolved one is a thunk address rather than a string -- reading it to
 * decide what was asked would be reading code as text. Answering "en" to
 * everything is a smaller lie than NULL, and the engine's next move is to
 * compare it against its own language list, which is exactly what it should
 * find. A voice that is not English will need this to say so.
 */
static void * __cdecl sh_CFLocaleGetValue(void *locale, void *key)
{
    static cfobj *lang;
    (void)locale; (void)key;
    if (!lang) lang = cf_pinned("en");
    return lang;
}

/* -1, 0, 1 -- kCFCompareLessThan, EqualTo, GreaterThan.  Both arguments may
 * be an engine CFSTR constant or one of ours; `cf_cstr` reads either. */
static int __cdecl sh_CFStringCompare(const void *a, const void *b,
                                      unsigned opts)
{
    const char *x = cf_cstr(a), *y = cf_cstr(b);
    int r;
    if (!x || !y) return x == y ? 0 : (x ? 1 : -1);
    r = (opts & 1) ? _stricmp(x, y) : strcmp(x, y);  /* 1 = caseInsensitive */
    return r < 0 ? -1 : (r > 0 ? 1 : 0);
}

/* Lion's `SESpeakCFString` copies the string it is handed before doing
 * anything with it, and a stub returning NULL made that read as **`OSErr
 * -108`, memFullErr** -- an out-of-memory report from a host with gigabytes
 * spare, which is a long way from "one shim is missing".
 *
 * Copied rather than retained: these objects are refcounted by hand and the
 * engine releases what it copies, so handing back the same pointer would put
 * the caller's release on an object the caller does not own. */
/* **The encoding is part of what is being copied.**
 *
 * `SESpeakCFString` copies its argument before doing anything with it, so this
 * is on the utterance path, and a copy that forgot the tag would leave the
 * engine reading the one string that matters as though it were a file path --
 * which is the bug this whole tag exists to fix, reintroduced one function
 * later. */
static void * __cdecl sh_CFStringCreateCopy(void *alloc, const void *s)
{
    cfobj *o;
    (void)alloc;
    if (!s) return NULL;
    o = cf_new(cf_cstr(s));
    if (o && cf_ours(s)) o->macroman = ((const cfobj *)s)->macroman;
    return o;
}

/* Lion's dictionary does not name its tables.  It builds the names:
 *
 *     CFStringCreateWithFormat(0, 0, CFSTR("%@Eng"), CFSTR("PrefixDictionary"))
 *     CFBundleCopyResourceURL(bundle, thatName, NULL, NULL)
 *
 * three times in `SLDictLookup::Create` -- PrefixDictionary, CartLite,
 * CartNames -- and once in `CreatePhonemeSymbols`.  `%@Eng` is the **only**
 * format string in the whole binary, so this one shim decides whether the
 * dictionary exists at all.
 *
 * Stubbed, it returned NULL, so the bundle was asked for a resource called
 * nothing and answered NULL to all three.  `Create` then took its own error
 * path and returned NULL -- which surfaced two stages later as
 * `SLPostLexerImpl` holding a null `SLDictLookup`: a fault in a constructor
 * that is not the one at fault, naming neither the format nor the shim.
 * `TuplesEng` is why it was findable at all -- it is a *literal*, so it got
 * its URL while the three formatted names did not, and two tables out of six
 * is the shape of a formatter returning nothing.
 *
 * **The arguments are Apple's own CFSTR constants**, not objects this host
 * made, so they go through `cf_cstr`'s shape cast and never through
 * `cf_ours`.  A formatter that reads only its own objects passes every test
 * written with its own objects and fails every real call site.
 *
 * `%@` and `%%` are all either binary asks for.  Anything else is refused out
 * loud rather than half-rendered: a partial string here would be a quieter
 * version of the bug it replaces. */
static void * __cdecl sh_CFStringCreateWithFormat(void *alloc, void *opts,
                                                  const void *format, ...)
{
    const char *f = cf_cstr(format);
    char out[CFPATH];
    unsigned n = 0;
    va_list ap;

    (void)alloc; (void)opts;
    if (!f) return NULL;
    va_start(ap, format);
    while (*f && n + 1 < CFPATH) {
        if (*f != '%') { out[n++] = *f++; continue; }
        f++;
        if (*f == '%') { out[n++] = *f++; continue; }
        if (*f == '@') {
            const char *s = cf_cstr(va_arg(ap, const void *));
            f++;
            if (!s) s = "(null)";
            while (*s && n + 1 < CFPATH) out[n++] = *s++;
            continue;
        }
        printf("  [cf] CFStringCreateWithFormat: unsupported conversion "
               "%%%c in \"%s\"\n", *f ? *f : '?', cf_cstr(format));
        va_end(ap);
        return NULL;
    }
    va_end(ap);
    out[n] = '\0';
    return cf_new(out);
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

/* ---- 10.7's property keys ---------------------------------------------- */
/*
 * Lion moved rate, pitch, volume and inflection **out** of `SESetSpeechInfo`.
 * Its `SetSpeechInfo` compares exactly nine selectors -- xtnc, xtnd, latd,
 * late, dopt, popt, picn, pico, prld -- all internal, and answers OSErr -231
 * to `rate` and `pbas`.  They live in
 * `SESetSpeechProperty(chan, CFStringRef key, CFTypeRef value)` now.
 *
 * `SpeechChannelManager::SetSpeechProperty` dispatches by `CFStringCompare`
 * against imported `kSpeech*` constants -- and being **imports, this host
 * defines them**, so both sides of every comparison are objects we made and a
 * match is guaranteed by construction.
 *
 * The strings are still Apple's documented values rather than anything
 * convenient. The comparison the engine performs is against the constant it
 * was handed, so the text does not strictly matter -- but if any path ever
 * compares against an internal literal instead, only the real one matches,
 * and a wrong guess would present as a property that silently does nothing.
 *
 * The value is a **CFNumber read as Float32 and multiplied by 65536.0** --
 * verified from the constant in `__const`, not assumed.  That is a `Fixed`,
 * which is exactly what `soRate` and `soPitchBase` always were, so nothing
 * measured in the pitch or inflection work changes; only the delivery.
 *
 * These are *data* symbols: the engine loads the pointer slot and then
 * dereferences it, so the shim table must hand out **the address of a
 * variable holding the object**, not the object.  A thunk here would be a
 * code address masquerading as a CFStringRef, which is the `__stack_chk_guard`
 * mistake in a new hat.
 */
/* **All of them, not only the interesting ones.** `SetSpeechProperty` walks
 * its keys in order, comparing the caller's string against each in turn, so
 * every constant it might reach on the way to `rate` has to be a real object.
 * Leaving the rest unshimmed thunked them to code addresses, and the first
 * comparison read a `cstr` field out of x86 instructions -- a fault inside
 * `CFStringCompare` on the very call that was meant to prove rate works.
 *
 * The strings are Apple's documented values, and unlike almost everything
 * else in this host they are **not verified against the binary** -- they
 * cannot be, being imports with no data behind them. That is acceptable here
 * for a specific reason: the engine only ever compares a key it was handed
 * against a constant this host defined, so both sides are ours and the text
 * is a label rather than a protocol. If a path ever compares against an
 * internal literal instead, a wrong value here would present as a property
 * that silently does nothing, which is why they are the real ones anyway. */
typedef struct { const char *sym, *text; void *obj; } speech_const;

static speech_const g_speech_consts[] = {
    { "_kSpeechRateProperty",             "rate" },
    { "_kSpeechPitchBaseProperty",        "pitchBase" },
    { "_kSpeechPitchModProperty",         "pitchMod" },
    { "_kSpeechVolumeProperty",           "volume" },
    { "_kSpeechResetProperty",            "reset" },
    { "_kSpeechStatusProperty",           "status" },
    { "_kSpeechErrorsProperty",           "errors" },
    { "_kSpeechInputModeProperty",        "inputMode" },
    { "_kSpeechCharacterModeProperty",    "characterMode" },
    { "_kSpeechNumberModeProperty",       "numberMode" },
    { "_kSpeechCommandDelimiterProperty", "commandDelimiter" },
    { "_kSpeechRecentSyncProperty",       "recentSync" },
    { "_kSpeechPhonemeSymbolsProperty",   "phonemeSymbols" },
    { "_kSpeechPhonemeOptionsProperty",   "phonemeOptions" },
    { "_kSpeechOutputToFileURLProperty",  "outputToFileURL" },
    { "_kSpeechOfflineModeProperty",      "offlineMode" },
    { "_kSpeechAudioGraphProperty",       "audioGraph" },
    { "_kSpeechAudioUnitProperty",        "audioUnit" },
    { "_kSpeechRefConProperty",           "refCon" },
    { "_kSpeechCommandPrefix",            "commandPrefix" },
    { "_kSpeechCommandSuffix",            "commandSuffix" },
    /* Callbacks. Nothing installs one yet, but they are compared against on
     * the way to the properties that matter, so they must exist. When index
     * marks arrive for the driver, `kSpeechSyncCallBack` and
     * `kSpeechWordCFCallBack` are the two to reach for. */
    { "_kSpeechTextDoneCallBack",         "textDoneCallBack" },
    { "_kSpeechSpeechDoneCallBack",       "speechDoneCallBack" },
    { "_kSpeechSyncCallBack",             "syncCallBack" },
    { "_kSpeechErrorCFCallBack",          "errorCallBack" },
    { "_kSpeechPhonemeCallBack",          "phonemeCallBack" },
    { "_kSpeechWordCFCallBack",           "wordCallBack" },
    /* Values rather than keys. */
    { "_kSpeechModeText",                 "TEXT" },
    { "_kSpeechModePhoneme",              "PHON" },
    { "_kSpeechModeTune",                 "TUNE" },
    { "_kSpeechModeNormal",               "NORM" },
    { "_kSpeechModeLiteral",              "LTRL" },
    { "_kSpeechNoSpeechInterrupt",        "NoSpeechInterrupt" },
    { "_kSpeechPreflightThenPause",       "PreflightThenPause" },
    { "_kSpeechErrorCount",               "count" },
    { "_kSpeechErrorNewest",              "newest" },
    { "_kSpeechErrorNewestCharacterOffset", "newestCharacterOffset" },
    { "_kSpeechErrorOldest",              "oldest" },
    { "_kSpeechErrorOldestCharacterOffset", "oldestCharacterOffset" },
    { "_kSpeechErrorCallbackSpokenString", "spokenString" },
    { "_kSpeechErrorCallbackCharacterOffset", "characterOffset" },
    { "_kSpeechStatusOutputBusy",         "outputBusy" },
    { "_kSpeechStatusOutputPaused",       "outputPaused" },
    { "_kSpeechStatusNumberOfCharactersLeft", "numberOfCharactersLeft" },
    { "_kSpeechStatusPhonemeCode",        "phonemeCode" },
    /* Found by the diagnostic below rather than by reading a header: the
     * phoneme-symbols dictionary is built at startup, before anything asks
     * for a property, so these were reached on the very first run. */
    { "_kSpeechPhonemeInfoOpcode",        "opcode" },
    { "_kSpeechPhonemeInfoSymbol",        "symbol" },
    { "_kSpeechPhonemeInfoExample",       "example" },
    { "_kSpeechPhonemeInfoHiliteStart",   "hiliteStart" },
    { "_kSpeechPhonemeInfoHiliteEnd",     "hiliteEnd" },
};
#define SPEECH_CONST_N \
        (int)(sizeof(g_speech_consts) / sizeof(g_speech_consts[0]))

/* The named ones, by index into the table above. */
#define SPK_RATE       0
#define SPK_PITCHBASE  1
#define SPK_PITCHMOD   2
#define SPK_VOLUME     3
#define SPK_RESET      4
#define SPK_STATUS     5

static void *g_speech_key[6];

/* Pinned, like the bundle: the engine retains and releases these as though it
 * owned them, and a real CoreFoundation constant cannot be freed. */
static void speech_keys_init(void)
{
    int i;
    for (i = 0; i < SPEECH_CONST_N; i++)
        if (!g_speech_consts[i].obj)
            g_speech_consts[i].obj = cf_pinned(g_speech_consts[i].text);
    for (i = 0; i < 6; i++)
        g_speech_key[i] = g_speech_consts[i].obj;
}

/* -> the address of the slot holding the constant, which is what an imported
 * *data* symbol resolves to: the engine loads the pointer slot and then
 * dereferences it. Handing back the object itself would be one indirection
 * short, and a thunk would be a code address wearing a CFStringRef's clothes
 * -- the `__stack_chk_guard` mistake in a new hat.
 *
 * Consulted by `lookup_shim` rather than written out as forty-five rows in
 * the shim table: they are one family, resolved one way, and a row each would
 * be forty-five chances to mistype a name that fails silently. */
static void *speech_const_lookup(const char *name)
{
    int i;
    if (strncmp(name, "_kSpeech", 8)) return NULL;
    speech_keys_init();
    for (i = 0; i < SPEECH_CONST_N; i++)
        if (!strcmp(g_speech_consts[i].sym, name))
            return &g_speech_consts[i].obj;
    /* Named the family but not a member: say so. Being thunked from here is
     * how the first attempt at this crashed. */
    printf("  [cf] no constant for %s -- it will be thunked\n", name);
    return NULL;
}

/* A CFNumber the engine can read back through `CFNumberGetValue`. */
static cfobj *cf_number(double v)
{
    cfobj *o = cf_new("");
    if (!o) return NULL;
    o->kind = CF_NUMBER;
    o->num  = v;
    return o;
}

/* The engine's own direction: `CopySpeechProperty` builds a CFNumber to hand
 * a value *back*.  Stubbed, it returned NULL, and the caller could not read
 * the voice's base pitch -- so the pitch offset was applied to nothing and
 * every render came out byte-identical whatever the slider said. Rate worked
 * throughout, which is what made it look like pitch was simply unsupported.
 *
 * The types are CFNumber.h's, and the same list `CFNumberGetValue` answers --
 * this is its inverse and the two must agree. */
static void * __cdecl sh_CFNumberCreate(void *alloc, int type,
                                        const void *value)
{
    double v;
    (void)alloc;
    if (!value) return NULL;
    switch (type) {
    case 1:  case 7:  v = *(const char *)value;      break;
    case 2:  case 8:  v = *(const short *)value;     break;
    case 3:  case 9:  case 14: case 15:
                      v = *(const int *)value;       break;
    case 10:          v = (double)*(const long *)value;    break;
    case 4:  case 11: v = (double)*(const __int64 *)value; break;
    case 5:  case 12: case 16:
                      v = *(const float *)value;     break;
    case 6:  case 13: v = *(const double *)value;    break;
    default:
        fprintf(stderr, "tiger_host: CFNumberCreate asked for type %d, which "
                        "this does not know\n", type);
        return NULL;
    }
    return cf_number(v);
}

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

/* ---- --cf-check ------------------------------------------------------- */
/*
 * The formatter, on the four names Lion's dictionary cannot open without,
 * needing no tree -- like `--regex-check`.  See
 * panthera/tests/test_cf_format.py.
 *
 * Written to **stdout**, for the reason `--dyld-check` is: `printf` here is
 * redirected to stderr because serve mode puts PCM on stdout, and in a check
 * the report *is* the data.  The shim's own diagnostic stays on stderr, where
 * it belongs -- a shim writing to stdout mid-render would corrupt the audio.
 */
static int cf_check(void)
{
    /* A CFSTR constant as Apple's binaries carry it: the bare 16-byte record,
     * with an isa that is not ours.  Every argument at every real call site
     * looks like this and none of them look like a `cfobj`, so a check built
     * only from `cf_new` would prove nothing about the calls being fixed. */
    static void *not_our_class;
    static const struct { const char *name, *want; } tables[] = {
        { "PrefixDictionary", "PrefixDictionaryEng" },
        { "CartLite",         "CartLiteEng"         },
        { "CartNames",        "CartNamesEng"        },
        { "PhonemeSymbols",   "PhonemeSymbolsEng"   },
    };
    cfstring fmt = { 0 }, arg = { 0 };
    int i, fails = 0;
    void *r;

    fmt.isa = &not_our_class;
    fmt.cstr = "%@Eng";
    fmt.len = 5;
    arg.isa = &not_our_class;

    for (i = 0; i < (int)(sizeof(tables) / sizeof(tables[0])); i++) {
        arg.cstr = tables[i].name;
        arg.len  = (unsigned)strlen(arg.cstr);
        r = sh_CFStringCreateWithFormat(NULL, NULL, &fmt, &arg);
        fprintf(stdout, "[cf-check] constant \"%%@Eng\" + \"%s\" -> \"%s\"\n",
                tables[i].name, r ? cf_cstr(r) : "(null)");
        if (!r || strcmp(cf_cstr(r), tables[i].want)) {
            fprintf(stdout, "FAIL  wanted \"%s\"\n", tables[i].want);
            fails++;
        }
        if (r) free(r);
    }

    /* An object this host made, formatted through the same path: the two
     * shapes have to be one code path, or the next kind of argument is a new
     * bug. */
    {
        cfobj *o = cf_new("Homophones");
        r = sh_CFStringCreateWithFormat(NULL, NULL, &fmt, o);
        fprintf(stdout, "[cf-check] cfobj \"%%@Eng\" + \"Homophones\" -> "
                        "\"%s\"\n", r ? cf_cstr(r) : "(null)");
        if (!r || strcmp(cf_cstr(r), "HomophonesEng")) {
            fprintf(stdout, "FAIL  wanted \"HomophonesEng\"\n");
            fails++;
        }
        if (r) free(r);
        free(o);
    }

    /* Literal percent, and a conversion nothing in either binary uses.  The
     * refusal is the point: it has to name itself rather than hand back a
     * plausible half-rendered string. */
    {
        cfstring pct = { 0 };
        pct.isa = &not_our_class;
        pct.cstr = "100%% sure";
        pct.len = 10;
        r = sh_CFStringCreateWithFormat(NULL, NULL, &pct);
        fprintf(stdout, "[cf-check] \"100%%%% sure\" -> \"%s\"\n",
                r ? cf_cstr(r) : "(null)");
        if (!r || strcmp(cf_cstr(r), "100% sure")) {
            fprintf(stdout, "FAIL  wanted \"100%% sure\"\n");
            fails++;
        }
        if (r) free(r);

        pct.cstr = "%d items";
        pct.len = 8;
        r = sh_CFStringCreateWithFormat(NULL, NULL, &pct, 3);
        fprintf(stdout, "[cf-check] \"%%d items\" -> %s (unsupported "
                        "conversion refused)\n", r ? cf_cstr(r) : "(null)");
        if (r) {
            fprintf(stdout, "FAIL  an unsupported conversion was guessed at\n");
            fails++;
            free(r);
        }
    }

    /* The token layer, which builds strings rather than being handed them.
     * The length is the engine's own `(end - begin) / 2 - 1`, so the buffer
     * deliberately carries a terminator the count excludes: a shim that
     * scanned for it instead would pass the first case and fail the second. */
    {
        static const unsigned short WIDE[] = {
            'H','o','m','o','p','h','o','n','e','s', 0
        };
        r = sh_CFStringCreateWithCharactersNoCopy(NULL, WIDE, 10, NULL);
        fprintf(stdout, "[cf-check] chars \"%s\"\n", r ? cf_cstr(r) : "(null)");
        if (!r || strcmp(cf_cstr(r), "Homophones")) {
            fprintf(stdout, "FAIL  wanted \"Homophones\"\n");
            fails++;
        }
        if (r) {
            if (sh_CFStringGetCharacterAtIndex(r, 1) != 'o') {
                fprintf(stdout, "FAIL  character 1 was not 'o'\n");
                fails++;
            }
            fprintf(stdout, "[cf-check] index oob %u\n",
                    sh_CFStringGetCharacterAtIndex(r, 99));
            if (sh_CFStringGetCharacterAtIndex(r, 99) != 0) {
                fprintf(stdout, "FAIL  an index past the end read memory\n");
                fails++;
            }
            free(r);
        }

        r = sh_CFStringCreateWithCharactersNoCopy(NULL, WIDE, 4, NULL);
        fprintf(stdout, "[cf-check] count \"%s\"\n", r ? cf_cstr(r) : "(null)");
        if (!r || strcmp(cf_cstr(r), "Homo")) {
            fprintf(stdout, "FAIL  the count was not believed over the "
                            "terminator\n");
            fails++;
        }
        if (r) free(r);
    }

    /* An utterance is MacRoman and a path is not, and the only difference
     * between the two objects is which constructor made them.
     *
     * The three characters checked are the ones the bug was reported on:
     * Hungarian `a` and `o` acute and `u` diaeresis.  Each is a byte that
     * zero-extends into the C1 control block, which is why they vanished
     * rather than coming out as some other letter -- a wrong letter would
     * have been noticed years earlier. */
    {
        static const char ACC[] = { (char)0x87, (char)0x97, (char)0x9F, 'a',
                                    '\0' };
        static const unsigned short WANT[] = { 0x00E1, 0x00F3, 0x00FC, 'a' };
        unsigned short got[4];
        cfobj *t = cf_text(ACC), *p = cf_new(ACC), *c;
        int i;
        sh_CFStringGetCharacters(t, 0, 4, got);
        fprintf(stdout, "[cf-check] text U+%04X U+%04X U+%04X U+%04X\n",
                got[0], got[1], got[2], got[3]);
        for (i = 0; i < 4; i++)
            if (got[i] != WANT[i]) {
                fprintf(stdout, "FAIL  MacRoman %02X widened to U+%04X, "
                                "wanted U+%04X\n",
                        (unsigned char)ACC[i], got[i], WANT[i]);
                fails++;
            }
        sh_CFStringGetCharacters(p, 0, 4, got);
        if (got[0] != 0x0087) {
            fprintf(stdout, "FAIL  a path was decoded as MacRoman; the user "
                            "folder of anyone with an accent just moved\n");
            fails++;
        }
        /* SESpeakCFString copies before reading, so the copy has to remember
         * what it is a copy of. */
        c = (cfobj *)sh_CFStringCreateCopy(NULL, t);
        sh_CFStringGetCharacters(c, 0, 1, got);
        if (got[0] != 0x00E1) {
            fprintf(stdout, "FAIL  copying an utterance lost its encoding\n");
            fails++;
        }
        if (sh_CFStringGetCharacterAtIndex(t, 0) != 0x00E1) {
            fprintf(stdout, "FAIL  the two character accessors disagree\n");
            fails++;
        }
        free(t); free(p); free(c);
    }

    {
        static const unsigned char RAW[] = { 1, 2, 3, 0, 5 };
        cfobj *d = (cfobj *)sh_CFDataCreateWithBytesNoCopy(NULL, RAW, 5, NULL);
        const unsigned char *back = d ? sh_CFDataGetBytePtr(d) : NULL;
        int len = d ? sh_CFDataGetLength(d) : -1;
        int ok = back && len == 5 && !memcmp(back, RAW, 5);
        fprintf(stdout, "[cf-check] data %d bytes %s\n", len,
                ok ? "ok" : "WRONG");
        if (!ok) {
            fprintf(stdout, "FAIL  a CFData did not read back as written\n");
            fails++;
        }
        if (d) { free(d->bytes); free(d); }
    }

    fprintf(stdout, "[cf-check] %d failure(s)\n", fails);
    return fails ? 1 : 0;
}
