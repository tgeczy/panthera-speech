/* tiger_host_regex.c -- the POSIX regex Leopard's SpeechDictionary uses.
 *
 * Part of tiger_host.c, which includes it; see there for why this is one
 * translation unit.
 *
 * Tiger's dictionary matches with the flat SLPD and Cart tables and imports no
 * regex at all.  Leopard's imports regcomp, regexec and regfree, compiles one
 * pattern when the channel opens, and runs it over words afterwards:
 *
 *     ^[[:digit:]]{7,}$          cflags 0x5 = REG_EXTENDED | REG_NOSUB
 *
 * "Seven or more digits and nothing else" -- a telephone number, which is read
 * out digit by digit instead of as a quantity.
 *
 * Stubbed, this was far worse than absent, because **POSIX regexec returns 0
 * for a match**.  A stub returning 0 therefore said "yes, a phone number" for
 * every word it was ever handed, so every number in every utterance was spelt
 * out: Leopard said "one, two, three" where Tiger said "one hundred twenty
 * three", and it had done so for as long as this has run.
 *
 * What is implemented below is not a regex engine.  It is a reader for the
 * shape of pattern this framework actually contains -- an optionally anchored
 * single bracket expression with a repetition count -- and it **refuses
 * anything else**, because a matcher that quietly mishandles a construct it
 * does not know would put us straight back where we started.  A refused
 * pattern says so once and then never matches, which is the same answer as
 * having no regex at all and is a defined one.
 */
#define REG_NOMATCH 1

/* The subset: [^]? '[' '[:' class ':]' ']' repeat '$'?  where repeat is one of
 * {n,} {n,m} {n} + * ? or absent. */
typedef struct {
    const void *preg;                   /* the caller's regex_t, as a key */
    int   used;
    int   anchor_start, anchor_end;
    int   cls;                          /* 'd', 'a', 'w', 's', 'p', 'x' */
    unsigned lo, hi;                    /* repetition, hi 0 meaning no limit */
} re_pat;

static re_pat g_re[8];

static int re_class_of(const char *name, unsigned n)
{
    if (n == 5 && !strncmp(name, "digit", 5)) return 'd';
    if (n == 5 && !strncmp(name, "alpha", 5)) return 'a';
    if (n == 5 && !strncmp(name, "alnum", 5)) return 'w';
    if (n == 5 && !strncmp(name, "space", 5)) return 's';
    if (n == 5 && !strncmp(name, "punct", 5)) return 'p';
    if (n == 6 && !strncmp(name, "xdigit", 6)) return 'x';
    return 0;
}

static int re_in_class(int cls, unsigned char c)
{
    switch (cls) {
    case 'd': return c >= '0' && c <= '9';
    case 'a': return isalpha(c) != 0;
    case 'w': return isalnum(c) != 0;
    case 's': return isspace(c) != 0;
    case 'p': return ispunct(c) != 0;
    case 'x': return isxdigit(c) != 0;
    }
    return 0;
}

/* -> 1 if the whole pattern was understood and `out` describes it. */
static int re_parse(const char *p, re_pat *out)
{
    const char *e;
    unsigned n;
    if (!p) return 0;
    memset(out, 0, sizeof(*out));
    out->lo = out->hi = 1;
    if (*p == '^') { out->anchor_start = 1; p++; }
    if (strncmp(p, "[[:", 3) != 0) return 0;
    p += 3;
    e = strstr(p, ":]]");
    if (!e) return 0;
    out->cls = re_class_of(p, (unsigned)(e - p));
    if (!out->cls) return 0;
    p = e + 3;
    if (*p == '{') {
        p++;
        if (*p < '0' || *p > '9') return 0;
        for (n = 0; *p >= '0' && *p <= '9'; p++) n = n * 10 + (unsigned)(*p - '0');
        out->lo = n;
        if (*p == ',') {
            p++;
            if (*p == '}') out->hi = 0;                 /* {n,} -- no limit */
            else {
                for (n = 0; *p >= '0' && *p <= '9'; p++)
                    n = n * 10 + (unsigned)(*p - '0');
                out->hi = n;
            }
        } else out->hi = out->lo;
        if (*p != '}') return 0;
        p++;
    } else if (*p == '+') { out->lo = 1; out->hi = 0; p++; }
    else if (*p == '*')   { out->lo = 0; out->hi = 0; p++; }
    else if (*p == '?')   { out->lo = 0; out->hi = 1; p++; }
    if (*p == '$') { out->anchor_end = 1; p++; }
    return *p == 0;
}

static re_pat *re_find(const void *preg)
{
    int i;
    for (i = 0; i < 8; i++)
        if (g_re[i].used && g_re[i].preg == preg) return &g_re[i];
    return NULL;
}

/* Nothing is written through `preg`.  A Darwin i386 regex_t is four words --
 * re_magic, re_nsub, re_endp, re_g -- and clearing a generous 64 bytes over it
 * walked off the end of the caller's stack slot and took the process down on
 * the first utterance.  The pattern is kept here and keyed by that pointer
 * instead, so the caller's memory is never touched. */
static int __cdecl sh_regcomp(void *preg, const char *pattern, int cflags)
{
    re_pat parsed;
    int i, ok = re_parse(pattern, &parsed);
    if (!ok)
        fprintf(stderr, "tiger_host: SpeechDictionary compiled a regular "
                        "expression this does not implement, so it will never "
                        "match: %s\n", pattern ? pattern : "(null)");
    for (i = 0; i < 8; i++)
        if (!g_re[i].used || g_re[i].preg == preg) break;
    if (i < 8) {
        if (ok) { g_re[i] = parsed; }
        else    { memset(&g_re[i], 0, sizeof(g_re[i])); }
        g_re[i].preg = preg;
        g_re[i].used = ok ? 1 : 0;      /* unparsed patterns stay unmatched */
    }
    if (g_verbose)
        printf("  [re] %s (cflags 0x%x): %s\n", ok ? "compiled" : "REFUSED",
               cflags, pattern ? pattern : "(null)");
    return 0;                            /* the compile itself always succeeds */
}

/* 0 for a match, REG_NOMATCH otherwise -- POSIX's way round, which is the
 * whole reason the stub was harmful. */
static int __cdecl sh_regexec(const void *preg, const char *string,
                              unsigned nmatch, void *pmatch, int eflags)
{
    const re_pat *r = re_find(preg);
    const unsigned char *s = (const unsigned char *)string;
    unsigned start;
    (void)eflags; (void)nmatch; (void)pmatch;   /* every use here is REG_NOSUB */
    if (!r || !s) return REG_NOMATCH;
    /* Try each starting position, which for the anchored form is only the
     * first.  A run is a match when it is long enough, short enough, and --
     * if the pattern ends in '$' -- reaches the end of the string. */
    for (start = 0; ; start++) {
        unsigned n = 0;
        while (s[start + n] && re_in_class(r->cls, s[start + n])) n++;
        if (n >= r->lo && (!r->hi || n <= r->hi) &&
            (!r->anchor_end || !s[start + n]))
            return 0;
        if (r->anchor_start || !s[start]) break;
    }
    return REG_NOMATCH;
}

static void __cdecl sh_regfree(void *preg)
{
    re_pat *r = re_find(preg);
    if (r) r->used = 0;
}
