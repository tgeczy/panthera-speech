/* tiger_host_sllog.c -- log the engine's calls into SpeechDictionary.
 *
 * `TIGER_SL_LOG=1` interposes a handful of the dictionary's exports at bind
 * time, so every token and homograph the engine actually consumes is printed
 * with its class and counts.  Built for panthera-speech#6: the lexer provably
 * parses a tune and the engine provably receives homographs with the right
 * phonemes and no tune, and the layer that rebuilds them in between is
 * invisible to the stub logger because every function on its path is real.
 * This makes the boundary itself speak.
 *
 * Interposition happens only on the from-dependency binding path, which is
 * exactly the engine-to-dictionary direction: the dictionary defines these
 * symbols, so its own binds never resolve through a dependency, and nothing
 * here can loop.  printf goes to stderr throughout the host, so none of this
 * can touch the PCM on stdout.
 */
static int g_sl_log = -1;

static int sl_log_on(void)
{
    if (g_sl_log < 0) {
        const char *e = getenv("TIGER_SL_LOG");
        g_sl_log = (e && *e && *e != '0');
    }
    return g_sl_log;
}

static void *g_real_tok_class;
static void *g_real_tok_counth;
static void *g_real_tok_geth;
static void *g_real_tok_gettext;
static void *g_real_hom_copytune;
static void *g_real_dict_lookup;

static int __cdecl sl_TokenGetClass(void *tok)
{
    int r = ((int (__cdecl *)(void *))g_real_tok_class)(tok);
    printf("  [sl] SLTokenGetClass(%p) = %d\n", tok, r);
    return r;
}

static int __cdecl sl_TokenCountHomographs(void *tok)
{
    int r = ((int (__cdecl *)(void *))g_real_tok_counth)(tok);
    printf("  [sl] SLTokenCountHomographs(%p) = %d\n", tok, r);
    return r;
}

static void * __cdecl sl_TokenGetHomograph(void *tok, int i)
{
    void *r = ((void *(__cdecl *)(void *, int))g_real_tok_geth)(tok, i);
    printf("  [sl] SLTokenGetHomograph(%p, %d) = %p\n", tok, i, r);
    if (r) {
        /* Raw, uninterpreted: the question under investigation is which of
         * these words is the tune vector and whether it survives to
         * CopyTune, so nothing here is allowed to assume the layout. */
        const unsigned *h = (const unsigned *)r;
        printf("  [sl]   token type=%u  homograph words: "
               "%08x %08x %08x %08x  %08x %08x %08x %08x  %08x %08x %08x %08x\n",
               *(const unsigned char *)tok,
               h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7],
               h[8], h[9], h[10], h[11]);
    }
    return r;
}

static void * __cdecl sl_TokenGetText(void *tok)
{
    void *r = ((void *(__cdecl *)(void *))g_real_tok_gettext)(tok);
    printf("  [sl] SLTokenGetText(%p) = %p\n", tok, r);
    return r;
}

static void * __cdecl sl_HomographCopyTune(void *h)
{
    void *r;
    if (h) {
        const unsigned *w = (const unsigned *)h;
        printf("  [sl] CopyTune sees +0x24=%08x +0x28=%08x\n", w[9], w[10]);
    }
    r = ((void *(__cdecl *)(void *))g_real_hom_copytune)(h);
    printf("  [sl] SLHomographCopyTune(%p) = %p\n", h, r);
    return r;
}

/* SLDictLookup::Lookup(SLDictionary*, const char*, size_t, SLToken*) const --
 * the engine filling a token with pronunciations from the dictionary
 * database.  If a tune token's text ever shows up here, the engine treated
 * it as an ordinary word and the rebuild is found. */
static int __cdecl sl_DictLookup(void *self, void *dict, const char *text,
                                 unsigned len, void *tok)
{
    int r = ((int (__cdecl *)(void *, void *, const char *, unsigned,
                              void *))g_real_dict_lookup)(
                self, dict, text, len, tok);
    printf("  [sl] SLDictLookup::Lookup(\"%.*s\" len=%u, tok=%p) = %d\n",
           len > 60 ? 60 : (int)len, text ? text : "(null)", len, tok, r);
    return r;
}

static const struct { const char *sym; void **real; void *wrap; } SL_HOOKS[] = {
    { "_SLTokenGetClass",        &g_real_tok_class,    (void *)sl_TokenGetClass },
    { "_SLTokenCountHomographs", &g_real_tok_counth,   (void *)sl_TokenCountHomographs },
    { "_SLTokenGetHomograph",    &g_real_tok_geth,     (void *)sl_TokenGetHomograph },
    { "_SLTokenGetText",         &g_real_tok_gettext,  (void *)sl_TokenGetText },
    { "_SLHomographCopyTune",    &g_real_hom_copytune, (void *)sl_HomographCopyTune },
    { "__ZNK12SLDictLookup6LookupEP12SLDictionaryPKcmP7SLToken",
                                 &g_real_dict_lookup,  (void *)sl_DictLookup },
};

/* -> `target`, or a logging wrapper for it when TIGER_SL_LOG asks. */
static void *sl_interpose(const char *sym, void *target)
{
    size_t i;
    if (!sl_log_on()) return target;
    for (i = 0; i < sizeof(SL_HOOKS) / sizeof(SL_HOOKS[0]); i++)
        if (!strcmp(sym, SL_HOOKS[i].sym)) {
            *SL_HOOKS[i].real = target;
            printf("  [sl] interposed %s\n", sym);
            return SL_HOOKS[i].wrap;
        }
    return target;
}
