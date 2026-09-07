/* tiger_host_regex.c -- the POSIX regex Leopard's SpeechDictionary uses.
 *
 * Part of tiger_host.c, which includes it; see there for why this is one
 * translation unit.
 *
 * Tiger's dictionary matches with the flat SLPD and Cart tables and imports no
 * regex at all.  Leopard's imports regcomp, regexec and regfree and compiles a
 * pattern for every rule that needs one, then runs them over words:
 *
 *     ^[[:digit:]]{7,}$                 a telephone number, read out digit by
 *                                       digit rather than as a quantity
 *     ^(K|M|G|T|P)B$                    kilobyte, megabyte, ...
 *     ^[IVXLCDM]{2,}$                   a roman numeral
 *     ^([[:upper:]](\.)?)+('?S)?$       an initialism, with or without stops
 *     ^((JAN(UARY)?)|(FEB(RUARY)?)|...  a month, spelt out or abbreviated
 *
 * Stubbed, this was far worse than absent, because **POSIX regexec returns 0
 * for a match**.  A stub returning 0 therefore said "yes, a phone number" for
 * every word it was ever handed, so every number in every utterance was spelt
 * out: Leopard said "one, two, three" where Tiger said "one hundred twenty
 * three", and it had done so for as long as this has run.
 *
 * The first fix read one shape of pattern -- an anchored bracket expression
 * with a repetition count -- and refused everything else, on the grounds that
 * a matcher which quietly mishandles a construct it does not know would put us
 * straight back where we started.  That was the right instinct and the wrong
 * subset: of the patterns above it accepted exactly one, so a user's log filled
 * with a hundred refusals per startup and every rule behind them was dead.  KB
 * stayed "kay bee", "III" stayed "eye eye eye", "Sept." stayed a word.
 *
 * So this is the engine, rather than a reader for one shape.  It is a
 * backtracking matcher over POSIX Extended Regular Expressions -- alternation,
 * groups, the bracket expression including named classes and ranges, the three
 * repetition operators and the bounded {n,m} form, both anchors, '.', and the
 * backslash escape -- which is the whole of ERE except back references, which
 * ERE does not have, and subexpression reporting, which every call here asks
 * not to have (REG_NOSUB).  Anything it still cannot parse is refused exactly
 * as before: said once, and never matched, which is a defined answer.
 *
 * REG_ICASE and REG_NEWLINE are honoured.  Basic REs -- regcomp without
 * REG_EXTENDED -- are refused rather than silently read as Extended, because
 * the two disagree about what '(' and '{' and '+' mean and guessing wrong is
 * the failure this file exists to avoid.  Nothing in the framework compiles
 * one.
 *
 * Matching is bounded.  A backtracking engine can be made to take exponential
 * time by a pattern with nested repetition, and this one runs inside a speech
 * request, so the step count is capped and a pattern that reaches the cap
 * reports no match instead of stopping the voice.  A word is a dozen or so
 * bytes and the real patterns settle in well under a thousand steps.
 */
#define REG_NOMATCH 1

/* Darwin's cflags, from /usr/include/regex.h -- octal, as they are there. */
#define TRE_EXTENDED 0001
#define TRE_ICASE    0002
#define TRE_NEWLINE  0004
#define TRE_NOSUB    0010

#define RE_INF       0xffffffffu    /* the 'hi' of an unbounded repetition */
#define RE_MAX_STEPS 200000         /* the backtracking budget for one match */
#define RE_MAX_PROGS 256            /* live compiled patterns; see re_slot_for */

enum {
    RN_EMPTY, RN_CHAR, RN_ANY, RN_SET, RN_BOL, RN_EOL, RN_CAT, RN_ALT, RN_REP
};

typedef struct {
    unsigned char kind;
    unsigned char ch;               /* RN_CHAR */
    int a, b;                       /* RN_CAT/RN_ALT children, RN_REP body in a */
    int set;                        /* RN_SET: index into prog->sets */
    unsigned lo, hi;                /* RN_REP */
} re_node;

typedef struct {
    re_node *node;
    int nnode, capnode;
    unsigned char (*sets)[32];      /* one 256-bit membership map per set */
    int nset, capset;
    int root;
    int icase, newline;
} re_prog;

/* ---------------------------------------------------------------- building */

static int re_node_new(re_prog *pr, int kind)
{
    if (pr->nnode == pr->capnode) {
        int cap = pr->capnode ? pr->capnode * 2 : 16;
        re_node *grown = (re_node *)realloc(pr->node, (size_t)cap * sizeof(*grown));
        if (!grown) return -1;
        pr->node = grown;
        pr->capnode = cap;
    }
    memset(&pr->node[pr->nnode], 0, sizeof(pr->node[0]));
    pr->node[pr->nnode].kind = (unsigned char)kind;
    pr->node[pr->nnode].a = pr->node[pr->nnode].b = -1;
    pr->node[pr->nnode].set = -1;
    return pr->nnode++;
}

static int re_set_new(re_prog *pr)
{
    if (pr->nset == pr->capset) {
        int cap = pr->capset ? pr->capset * 2 : 4;
        unsigned char (*grown)[32] =
            (unsigned char (*)[32])realloc(pr->sets, (size_t)cap * 32);
        if (!grown) return -1;
        pr->sets = grown;
        pr->capset = cap;
    }
    memset(pr->sets[pr->nset], 0, 32);
    return pr->nset++;
}

static void re_set_add(unsigned char *set, unsigned c)
{
    set[(c & 0xff) >> 3] |= (unsigned char)(1u << (c & 7));
}

static int re_set_has(const unsigned char *set, unsigned c)
{
    return (set[(c & 0xff) >> 3] >> (c & 7)) & 1;
}

static void re_prog_free(re_prog *pr)
{
    if (!pr) return;
    free(pr->node);
    free(pr->sets);
    free(pr);
}

/* ----------------------------------------------------------------- parsing */

typedef struct {
    const char *p;
    re_prog *pr;
    int bad;
} re_parse_state;

static int re_parse_alt(re_parse_state *ps);

/* The eleven classes POSIX names, plus the two ranges every locale agrees on.
 * "C" is the locale here: the framework hands us ASCII words. */
static int re_class_add(unsigned char *set, const char *name, unsigned n)
{
    unsigned c;
    int which = 0;
    if      (n == 5 && !strncmp(name, "alpha",  5)) which = 1;
    else if (n == 5 && !strncmp(name, "digit",  5)) which = 2;
    else if (n == 5 && !strncmp(name, "alnum",  5)) which = 3;
    else if (n == 5 && !strncmp(name, "upper",  5)) which = 4;
    else if (n == 5 && !strncmp(name, "lower",  5)) which = 5;
    else if (n == 5 && !strncmp(name, "space",  5)) which = 6;
    else if (n == 5 && !strncmp(name, "punct",  5)) which = 7;
    else if (n == 5 && !strncmp(name, "print",  5)) which = 8;
    else if (n == 5 && !strncmp(name, "graph",  5)) which = 9;
    else if (n == 5 && !strncmp(name, "cntrl",  5)) which = 10;
    else if (n == 5 && !strncmp(name, "blank",  5)) which = 11;
    else if (n == 6 && !strncmp(name, "xdigit", 6)) which = 12;
    else return 0;
    for (c = 0; c < 256; c++) {
        int in = 0;
        switch (which) {
        case 1:  in = isalpha((int)c)  != 0; break;
        case 2:  in = c >= '0' && c <= '9';  break;
        case 3:  in = isalnum((int)c)  != 0; break;
        case 4:  in = isupper((int)c)  != 0; break;
        case 5:  in = islower((int)c)  != 0; break;
        case 6:  in = isspace((int)c)  != 0; break;
        case 7:  in = ispunct((int)c)  != 0; break;
        case 8:  in = isprint((int)c)  != 0; break;
        case 9:  in = isgraph((int)c)  != 0; break;
        case 10: in = iscntrl((int)c)  != 0; break;
        case 11: in = c == ' ' || c == '\t';  break;
        case 12: in = isxdigit((int)c) != 0; break;
        }
        if (in) re_set_add(set, c);
    }
    return 1;
}

/* '[' has been consumed.  -> a set index, or -1 with ps->bad set. */
static int re_parse_bracket(re_parse_state *ps)
{
    unsigned char work[32];
    int negate = 0, first = 1, idx;
    unsigned c;

    memset(work, 0, sizeof(work));
    if (*ps->p == '^') { negate = 1; ps->p++; }

    for (;;) {
        if (!*ps->p) { ps->bad = 1; return -1; }
        /* A ']' is a literal only as the very first member. */
        if (*ps->p == ']' && !first) break;
        first = 0;

        if (ps->p[0] == '[' && (ps->p[1] == ':' || ps->p[1] == '.' || ps->p[1] == '=')) {
            char kind = ps->p[1];
            const char close[4] = { kind, ']', 0, 0 };
            const char *e = strstr(ps->p + 2, close);
            if (!e) { ps->bad = 1; return -1; }
            if (kind == ':') {
                if (!re_class_add(work, ps->p + 2, (unsigned)(e - (ps->p + 2)))) {
                    ps->bad = 1;                     /* a class we do not know */
                    return -1;
                }
            } else {
                /* [.x.] and [=x=]: in the C locale a collating element and an
                 * equivalence class are each just the character itself. */
                if (e != ps->p + 3) { ps->bad = 1; return -1; }
                re_set_add(work, (unsigned char)ps->p[2]);
            }
            ps->p = e + 2;
            continue;
        }

        c = (unsigned char)*ps->p++;
        /* A range, unless the '-' is last and so a literal. */
        if (*ps->p == '-' && ps->p[1] && ps->p[1] != ']') {
            unsigned hi = (unsigned char)ps->p[1];
            if (hi < c) { ps->bad = 1; return -1; }
            for (; c <= hi; c++) re_set_add(work, c);
            ps->p += 2;
            continue;
        }
        re_set_add(work, c);
    }
    ps->p++;                                          /* the closing ']' */

    if (ps->pr->icase) {
        for (c = 'a'; c <= 'z'; c++) {
            if (re_set_has(work, c)) re_set_add(work, c - 'a' + 'A');
            if (re_set_has(work, c - 'a' + 'A')) re_set_add(work, c);
        }
    }
    if (negate) {
        int i;
        for (i = 0; i < 32; i++) work[i] = (unsigned char)~work[i];
        /* With REG_NEWLINE a negated bracket does not match a newline. */
        if (ps->pr->newline) work['\n' >> 3] &= (unsigned char)~(1u << ('\n' & 7));
    }

    if ((idx = re_set_new(ps->pr)) < 0) { ps->bad = 1; return -1; }
    memcpy(ps->pr->sets[idx], work, 32);
    return idx;
}

static int re_lit(re_parse_state *ps, unsigned char c)
{
    int n = re_node_new(ps->pr, RN_CHAR);
    if (n < 0) { ps->bad = 1; return -1; }
    ps->pr->node[n].ch = c;
    return n;
}

static int re_parse_atom(re_parse_state *ps)
{
    int n;
    switch (*ps->p) {
    case '(': {
        ps->p++;
        n = re_parse_alt(ps);
        if (ps->bad) return -1;
        if (*ps->p != ')') { ps->bad = 1; return -1; }
        ps->p++;
        return n;
    }
    case '[':
        ps->p++;
        n = re_parse_bracket(ps);
        if (n < 0) return -1;
        {
            int node = re_node_new(ps->pr, RN_SET);
            if (node < 0) { ps->bad = 1; return -1; }
            ps->pr->node[node].set = n;
            return node;
        }
    case '.':
        ps->p++;
        n = re_node_new(ps->pr, RN_ANY);
        if (n < 0) ps->bad = 1;
        return n;
    case '^':
        ps->p++;
        n = re_node_new(ps->pr, RN_BOL);
        if (n < 0) ps->bad = 1;
        return n;
    case '$':
        ps->p++;
        n = re_node_new(ps->pr, RN_EOL);
        if (n < 0) ps->bad = 1;
        return n;
    case '\\':
        ps->p++;
        if (!*ps->p) { ps->bad = 1; return -1; }
        return re_lit(ps, (unsigned char)*ps->p++);
    case ')':
    case '\0':
        ps->bad = 1;
        return -1;
    case '*':
    case '+':
    case '?':
        /* A repetition with nothing to repeat.  POSIX leaves this undefined
         * and this refuses it, rather than pick a meaning. */
        ps->bad = 1;
        return -1;
    default:
        return re_lit(ps, (unsigned char)*ps->p++);
    }
}

/* "{2,}", "{3}", "{1,3}" -- and not "{", which is then an ordinary character.
 * -> 1 if `s` opens a well formed interval, with the bounds in *lo and *hi and
 * *len the length including both braces. */
static int re_interval(const char *s, unsigned *lo, unsigned *hi, int *len)
{
    const char *p = s + 1;
    unsigned n;
    if (*s != '{' || *p < '0' || *p > '9') return 0;
    for (n = 0; *p >= '0' && *p <= '9'; p++) n = n * 10 + (unsigned)(*p - '0');
    *lo = n;
    if (*p == ',') {
        p++;
        if (*p == '}') *hi = RE_INF;
        else {
            if (*p < '0' || *p > '9') return 0;
            for (n = 0; *p >= '0' && *p <= '9'; p++) n = n * 10 + (unsigned)(*p - '0');
            *hi = n;
        }
    } else *hi = *lo;
    if (*p != '}') return 0;
    if (*hi != RE_INF && *hi < *lo) return 0;
    *len = (int)(p - s) + 1;
    return 1;
}

static int re_parse_rep(re_parse_state *ps)
{
    int a = re_parse_atom(ps), rep;
    unsigned lo, hi;
    int len;
    if (ps->bad) return -1;
    for (;;) {
        if (*ps->p == '*')      { lo = 0; hi = RE_INF; ps->p++; }
        else if (*ps->p == '+') { lo = 1; hi = RE_INF; ps->p++; }
        else if (*ps->p == '?') { lo = 0; hi = 1;      ps->p++; }
        else if (re_interval(ps->p, &lo, &hi, &len)) { ps->p += len; }
        else break;
        if ((rep = re_node_new(ps->pr, RN_REP)) < 0) { ps->bad = 1; return -1; }
        ps->pr->node[rep].a  = a;
        ps->pr->node[rep].lo = lo;
        ps->pr->node[rep].hi = hi;
        a = rep;
    }
    return a;
}

static int re_parse_cat(re_parse_state *ps)
{
    int a = -1;
    while (*ps->p && *ps->p != '|' && *ps->p != ')') {
        int r = re_parse_rep(ps), cat;
        if (ps->bad) return -1;
        if (a < 0) { a = r; continue; }
        if ((cat = re_node_new(ps->pr, RN_CAT)) < 0) { ps->bad = 1; return -1; }
        ps->pr->node[cat].a = a;
        ps->pr->node[cat].b = r;
        a = cat;
    }
    if (a < 0) {
        if ((a = re_node_new(ps->pr, RN_EMPTY)) < 0) ps->bad = 1;
    }
    return a;
}

static int re_parse_alt(re_parse_state *ps)
{
    int a = re_parse_cat(ps);
    if (ps->bad) return -1;
    while (*ps->p == '|') {
        int b, alt;
        ps->p++;
        b = re_parse_cat(ps);
        if (ps->bad) return -1;
        if ((alt = re_node_new(ps->pr, RN_ALT)) < 0) { ps->bad = 1; return -1; }
        ps->pr->node[alt].a = a;
        ps->pr->node[alt].b = b;
        a = alt;
    }
    return a;
}

static re_prog *re_compile(const char *pattern, int cflags)
{
    re_parse_state ps;
    re_prog *pr;
    if (!pattern) return NULL;
    if (!(cflags & TRE_EXTENDED)) return NULL;     /* see the header comment */
    if (!(pr = (re_prog *)calloc(1, sizeof(*pr)))) return NULL;
    pr->icase   = (cflags & TRE_ICASE)   != 0;
    pr->newline = (cflags & TRE_NEWLINE) != 0;
    ps.p = pattern;
    ps.pr = pr;
    ps.bad = 0;
    pr->root = re_parse_alt(&ps);
    if (ps.bad || *ps.p) { re_prog_free(pr); return NULL; }
    return pr;
}

/* ---------------------------------------------------------------- matching */

/* What is left to do after the node in hand.  A frame is either "run this
 * node" or "you have just finished iteration `count` of this repetition",
 * which is what lets a repetition backtrack into its own body. */
typedef struct re_cont {
    int node;                       /* RN_* node, when rep < 0 */
    int rep;                        /* the RN_REP node, when >= 0 */
    unsigned count;
    const unsigned char *mark;      /* where that iteration began */
    const struct re_cont *next;
} re_cont;

typedef struct {
    const re_prog *pr;
    const unsigned char *base;
    unsigned long steps;
    int spent;                      /* the step budget ran out */
} re_ctx;

static int re_run(re_ctx *c, int node, const unsigned char *s, const re_cont *k);

static int re_cont_run(re_ctx *c, const re_cont *k, const unsigned char *s);

static int re_eq(const re_ctx *c, unsigned char a, unsigned char b)
{
    if (a == b) return 1;
    return c->pr->icase && tolower((int)a) == tolower((int)b);
}

static int re_rep(re_ctx *c, int rep, unsigned count, const unsigned char *mark,
                  const unsigned char *s, const re_cont *k)
{
    const re_node *n = &c->pr->node[rep];
    re_cont frame;

    if (c->spent) return 0;
    /* An iteration that consumed nothing cannot be improved on by doing it
     * again, and doing it again is how a body that matches the empty string
     * spins forever.  Any shortfall against `lo` can be padded with more of
     * those same empty iterations, so the answer either way is to go on. */
    if (count > 0 && s == mark) return re_cont_run(c, k, s);

    if (n->hi == RE_INF || count < n->hi) {
        frame.node  = -1;
        frame.rep   = rep;
        frame.count = count + 1;
        frame.mark  = s;
        frame.next  = k;
        if (re_run(c, n->a, s, &frame)) return 1;
        if (c->spent) return 0;
    }
    if (count >= n->lo) return re_cont_run(c, k, s);
    return 0;
}

static int re_cont_run(re_ctx *c, const re_cont *k, const unsigned char *s)
{
    if (c->spent) return 0;
    if (!k) return 1;                               /* nothing left: a match */
    if (k->rep >= 0) return re_rep(c, k->rep, k->count, k->mark, s, k->next);
    return re_run(c, k->node, s, k->next);
}

static int re_run(re_ctx *c, int node, const unsigned char *s, const re_cont *k)
{
    const re_node *n;
    re_cont frame;

    if (c->spent) return 0;
    if (++c->steps > RE_MAX_STEPS) { c->spent = 1; return 0; }
    n = &c->pr->node[node];

    switch (n->kind) {
    case RN_EMPTY:
        return re_cont_run(c, k, s);
    case RN_CHAR:
        return (*s && re_eq(c, *s, n->ch)) ? re_cont_run(c, k, s + 1) : 0;
    case RN_ANY:
        if (!*s) return 0;
        if (c->pr->newline && *s == '\n') return 0;
        return re_cont_run(c, k, s + 1);
    case RN_SET:
        return (*s && re_set_has(c->pr->sets[n->set], *s))
             ? re_cont_run(c, k, s + 1) : 0;
    case RN_BOL:
        if (s == c->base || (c->pr->newline && s[-1] == '\n'))
            return re_cont_run(c, k, s);
        return 0;
    case RN_EOL:
        if (!*s || (c->pr->newline && *s == '\n'))
            return re_cont_run(c, k, s);
        return 0;
    case RN_CAT:
        frame.node = n->b;
        frame.rep  = -1;
        frame.count = 0;
        frame.mark = NULL;
        frame.next = k;
        return re_run(c, n->a, s, &frame);
    case RN_ALT:
        if (re_run(c, n->a, s, k)) return 1;
        return re_run(c, n->b, s, k);
    case RN_REP:
        return re_rep(c, node, 0, NULL, s, k);
    }
    return 0;
}

static int re_search(const re_prog *pr, const char *string)
{
    const unsigned char *base = (const unsigned char *)string;
    const unsigned char *s;
    re_ctx c;
    c.pr = pr;
    c.base = base;
    c.steps = 0;
    c.spent = 0;
    /* REG_NOSUB: whether a match exists anywhere is the whole question, so the
     * first start position that works is the answer.  A '^' inside the pattern
     * fails every later start on its own. */
    for (s = base; ; s++) {
        if (re_run(&c, pr->root, s, NULL)) return 1;
        if (c.spent || !*s) return 0;
    }
}

/* --------------------------------------------------------------- the shims */

/* Nothing is written through `preg`.  A Darwin i386 regex_t is four words --
 * re_magic, re_nsub, re_endp, re_g -- and clearing a generous 64 bytes over it
 * walked off the end of the caller's stack slot and took the process down on
 * the first utterance.  The compiled pattern is kept here and keyed by that
 * pointer instead, so the caller's memory is never touched. */
typedef struct {
    const void *preg;               /* NULL once freed: the slot is spare */
    re_prog *prog;                  /* NULL for a pattern we would not read */
} re_slot;

static re_slot *g_re;
static int g_re_n, g_re_cap;

/* The table is shared, and the framework does not keep one `regex_t` per
 * pattern: every rule it compiles arrives at the *same* address --
 *
 *     [re] compile preg=0451F940 ^[[:digit:]]+ISH$
 *     [re] compile preg=0451F940 ^((...(,[[:digit:]]{3})*)|...)[[:upper:]]+$
 *
 * -- so a table keyed by that pointer holds one entry, and each compile frees
 * the program the previous one left there.  With the engine's own worker
 * threads running, a compile can therefore free a program while another thread
 * is matching against it: a use-after-free that reads as a rule which works
 * sometimes and not others, on no pattern the user can see.
 *
 * Reproduced without touching a setting: say "1,234MB" and then "the file is
 * 5KB" in the same host, and the second comes back 30800 frames or 27440
 * depending on the run.
 *
 * One lock over every entry point fixes it.  Compiling happens a couple of
 * times per utterance and matching a few dozen, so the contention is nothing
 * beside a speech request. */
static CRITICAL_SECTION g_re_lock;
static int g_re_lock_ready;

static void re_lock(void)
{
    if (!g_re_lock_ready) {          /* main() is single-threaded here */
        InitializeCriticalSection(&g_re_lock);
        g_re_lock_ready = 1;
    }
    EnterCriticalSection(&g_re_lock);
}

static void re_unlock(void)
{
    LeaveCriticalSection(&g_re_lock);
}

static re_slot *re_find(const void *preg)
{
    int i;
    if (!preg) return NULL;
    for (i = 0; i < g_re_n; i++)
        if (g_re[i].preg == preg) return &g_re[i];
    return NULL;
}

/* -> the slot for `preg`: its own if it has one, otherwise one freed earlier,
 * otherwise a new one.  The table only grows to RE_MAX_PROGS; past that the
 * oldest is taken, on the grounds that a caller which holds that many patterns
 * at once is not going to be helped by us running out of memory instead. */
static re_slot *re_slot_for(const void *preg)
{
    re_slot *slot = re_find(preg);
    int i;
    if (slot) {
        re_prog_free(slot->prog);
        slot->prog = NULL;
        return slot;
    }
    for (i = 0; i < g_re_n; i++)
        if (!g_re[i].preg) { g_re[i].preg = preg; return &g_re[i]; }
    if (g_re_n == g_re_cap && g_re_cap < RE_MAX_PROGS) {
        int cap = g_re_cap ? g_re_cap * 2 : 16;
        re_slot *grown;
        if (cap > RE_MAX_PROGS) cap = RE_MAX_PROGS;
        grown = (re_slot *)realloc(g_re, (size_t)cap * sizeof(*grown));
        if (grown) { g_re = grown; g_re_cap = cap; }
    }
    if (g_re_n < g_re_cap) {
        g_re[g_re_n].preg = preg;
        g_re[g_re_n].prog = NULL;
        return &g_re[g_re_n++];
    }
    if (g_re_n == 0) return NULL;
    re_prog_free(g_re[0].prog);
    memmove(&g_re[0], &g_re[1], (size_t)(g_re_n - 1) * sizeof(g_re[0]));
    g_re[g_re_n - 1].preg = preg;
    g_re[g_re_n - 1].prog = NULL;
    return &g_re[g_re_n - 1];
}

/* "Expand abbreviations", from the driver.
 *
 * The dictionary's rules *are* patterns, so declining to compile one turns
 * that rule off -- and a refused pattern is a state this file already had,
 * defined and tested: it never matches, and the word is spoken as written.
 * The switch therefore needs no new machinery, only a list of which rules it
 * covers.
 *
 * These are the shapes that rewrite a written form into different words: KB
 * into kilobytes, SEPT into September.  The phone-number rules are
 * deliberately *not* here -- reading digits as digits is not an abbreviation,
 * and that is the one rule that already worked. */
/* Taken from what the dictionary actually compiles, logged from a running
 * channel -- not from a description of it.  The rule that turns "5KB" into
 * kilobytes is the quantity-then-capitals pattern, *not* the bare unit one,
 * which is the kind of thing only the log tells you.
 *
 * **A seventh entry used to sit here, "IVXLCDM", commented "roman numerals",
 * and it never matched a thing.**  Every pattern the dictionary compiles is
 * in this list, logged on text stuffed with roman numerals, and none of them
 * is about roman numerals -- II is read as "two" whatever this switch says,
 * because it is a lexical entry inside MacinTalk rather than a rule out here.
 * That is the same place DR lives, and it is repaired the same way, in the
 * text: see `pantheraabbrev.py`.
 *
 * The mark was worse than useless, because it read as coverage.  Reported by
 * Tomi, who turned the setting off and still heard "World War two". */
static const char *k_abbrev_marks[] = {
    "(,[[:digit:]]{3})*",       /* a quantity followed by a unit: 5KB, 1,234MB */
    "(K|M|G|T|P)B",             /* a bare unit                                 */
    "JAN",                      /* the month alternation                       */
    "ISH",                      /* 20ish                                       */
    "&",                        /* AT&T                                        */
};

static int re_is_abbrev(const char *pattern)
{
    size_t i;
    if (!pattern) return 0;
    for (i = 0; i < sizeof(k_abbrev_marks) / sizeof(*k_abbrev_marks); i++)
        if (strstr(pattern, k_abbrev_marks[i])) return 1;
    return 0;
}

static int __cdecl sh_regcomp(void *preg, const char *pattern, int cflags)
{
    re_prog *prog;
    re_slot *slot;

    re_lock();
    if (g_no_abbrev && re_is_abbrev(pattern)) {
        if (g_verbose)
            printf("  [re] abbreviations off, so not compiled: %s\n", pattern);
        if ((slot = re_slot_for(preg))) slot->prog = NULL;
        re_unlock();
        return 0;
    }
    prog = re_compile(pattern, cflags);
    if (!prog)
        fprintf(stderr, "tiger_host: SpeechDictionary compiled a regular "
                        "expression this does not implement, so it will never "
                        "match: %s\n", pattern ? pattern : "(null)");
    if (g_pref_log)
        fprintf(stderr, "  [re] compile preg=%p %s -> %s\n", preg,
                pattern ? pattern : "(null)", prog ? "ok" : "REFUSED");
    if ((slot = re_slot_for(preg))) slot->prog = prog;
    else re_prog_free(prog);
    if (g_verbose)
        printf("  [re] %s (cflags 0x%x): %s\n", prog ? "compiled" : "REFUSED",
               cflags, pattern ? pattern : "(null)");
    re_unlock();
    return 0;                            /* the compile itself always succeeds */
}

/* 0 for a match, REG_NOMATCH otherwise -- POSIX's way round, which is the
 * whole reason the stub was harmful. */
static int __cdecl sh_regexec(const void *preg, const char *string,
                              unsigned nmatch, void *pmatch, int eflags)
{
    const re_slot *slot;
    int result;
    char bounded[512];
    const char *subject = string;

    /* **REG_STARTEND, and the reason a rule fired only sometimes.**
     *
     * The framework does not hand over a C string.  It passes a pointer into
     * its own word buffer with `eflags = REG_STARTEND` and the bounds in
     * pmatch[0], and that buffer is *not* terminated at the end of the word.
     * Reading to the first NUL therefore matched the word plus whatever
     * happened to follow it in memory:
     *
     *     [re] exec 5KBE                 -> MATCH   (the byte after was 'E')
     *     [re] exec 5KBE<binary>         -> no      (the next run, it was not)
     *
     * Every pattern here is anchored with '$', so trailing rubbish decides the
     * answer.  That is the whole of "sometimes KB expands and sometimes it
     * does not": same text, same settings, same host, different memory.
     *
     * regoff_t is 64-bit on Darwin, so pmatch[0] is two int64s -- confirmed
     * against the engine rather than assumed: "5KB" arrives as [0, 3],
     * "1,234MB" as [0, 7], "20ISH" as [0, 5]. */
    if ((eflags & 4) && pmatch && string) {
        const __int64 *off = (const __int64 *)pmatch;
        __int64 so = off[0], eo = off[1];
        if (so >= 0 && eo >= so && eo - so < (__int64)sizeof(bounded)) {
            memcpy(bounded, string + so, (size_t)(eo - so));
            bounded[eo - so] = 0;
            subject = bounded;
        }
    }
    /* Held across the match, not merely across the lookup: the program can be
     * freed by a compile on another thread, and a half-freed program is what
     * made this rule fire on some utterances and not others. */
    re_lock();
    slot = re_find(preg);
    if (!slot || !slot->prog || !subject) {
        if (g_pref_log)
            fprintf(stderr, "  [re] exec preg=%p on %.20s -> %s\n", preg,
                    string ? string : "",
                    slot ? "slot has no prog" : "NO SLOT");
        re_unlock();
        return REG_NOMATCH;
    }
    result = re_search(slot->prog, subject) ? 0 : REG_NOMATCH;
    if (g_pref_log) {
        const int *w = (const int *)pmatch;
        fprintf(stderr, "  [re] exec eflags=%d nmatch=%u pmatch32=[%d %d %d %d]"
                        " -> %s : %.24s\n", eflags, nmatch,
                pmatch ? w[0] : -1, pmatch ? w[1] : -1,
                pmatch ? w[2] : -1, pmatch ? w[3] : -1,
                result == 0 ? "MATCH" : "no", subject);
    }
    re_unlock();
    return result;
}

static void __cdecl sh_regfree(void *preg)
{
    re_slot *slot;
    re_lock();
    slot = re_find(preg);
    if (slot) {
        re_prog_free(slot->prog);
        slot->prog = NULL;
        slot->preg = NULL;
    }
    re_unlock();
}

/* --------------------------------------------------------------- self test */

/* `tiger_host --regex-check` -- the same shape as --aac-check, and for the
 * same reason: a matcher is easy to get subtly wrong and impossible to see
 * being wrong from the outside, where its only trace is a word pronounced
 * oddly.  Every pattern below with a name is one this framework really
 * compiles, taken from a log; the expectations were computed independently.
 */
static const char RE_MONTH[] =
    "^((JAN(UARY)?)|(FEB(RUARY)?)|(MAR(CH)?)|(APR(IL)?)|(MAY)|(JUN(E)?)"
    "|(JUL(Y)?)|(AUG(UST)?)|(SEP(TEMBER)?)|(OCT(OBER)?)|(NOV(EMBER)?)"
    "|(DEC(EMBER)?))$";
static const char RE_INITIALISM[] =
    "^([[:upper:]](\\.)?)+('(([[:upper:]](\\.)?)+)?)?$";
static const char RE_HYPHEN_INITIALISM[] =
    "^([[:upper:]](\\.)?)+('(([[:upper:]](\\.)?)+)?)?"
    "(-([[:upper:]](\\.)?)+('(([[:upper:]](\\.)?)+)?)?)+('?S)?$";
static const char RE_QUANTITY[] =
    "^(([[:digit:]]{1,3}(,[[:digit:]]{3})*)|([[:digit:]]+(\\.[[:digit:]]+)?)"
    "|(([[:digit:]]+)?\\.[[:digit:]]+))[[:upper:]]+$";

static int re_check(void)
{
    static const struct { const char *pat, *subj; int icase, want; } cases[] = {
        { "^[[:digit:]]{7,}$"     , "1234567"       , 0, 1 },
        { "^[[:digit:]]{7,}$"     , "123456"        , 0, 0 },
        { "^[[:digit:]]{7,}$"     , "12345678"      , 0, 1 },
        { "^[[:digit:]]{7,}$"     , "123456a"       , 0, 0 },
        { "^[[:digit:]]{7,}$"     , ""              , 0, 0 },
        { "^[[:digit:]]{7,}$"     , "a1234567"      , 0, 0 },
        { RE_MONTH                , "JAN"           , 0, 1 },
        { RE_MONTH                , "JANUARY"       , 0, 1 },
        { RE_MONTH                , "MAY"           , 0, 1 },
        { RE_MONTH                , "MAYBE"         , 0, 0 },
        { RE_MONTH                , "SEPTEMBER"     , 0, 1 },
        { RE_MONTH                , "SEP"           , 0, 1 },
        { RE_MONTH                , "DEC"           , 0, 1 },
        { RE_MONTH                , "DECEMBERS"     , 0, 0 },
        { RE_MONTH                , "FEBRUARY"      , 0, 1 },
        { RE_MONTH                , "JUL"           , 0, 1 },
        { RE_MONTH                , "JULY"          , 0, 1 },
        { RE_MONTH                , "MARCH"         , 0, 1 },
        { RE_MONTH                , "MAR"           , 0, 1 },
        { RE_MONTH                , "OCT"           , 0, 1 },
        { RE_MONTH                , "APRIL"         , 0, 1 },
        { RE_MONTH                , "JUNE"          , 0, 1 },
        { RE_MONTH                , "AUG"           , 0, 1 },
        { RE_MONTH                , "NOV"           , 0, 1 },
        { RE_MONTH                , "XAN"           , 0, 0 },
        { RE_HYPHEN_INITIALISM    , "U.S.-BASED"    , 0, 1 },
        { RE_HYPHEN_INITIALISM    , "U.S."          , 0, 0 },
        { RE_HYPHEN_INITIALISM    , "AT&T"          , 0, 0 },
        { RE_HYPHEN_INITIALISM    , "A-B"           , 0, 1 },
        { RE_HYPHEN_INITIALISM    , "A.B.-C.D."     , 0, 1 },
        { RE_HYPHEN_INITIALISM    , "US-A'S"        , 0, 1 },
        { RE_HYPHEN_INITIALISM    , "ABC"           , 0, 0 },
        { "^[[:digit:]]+ISH$"     , "20ISH"         , 0, 1 },
        { "^[[:digit:]]+ISH$"     , "20ish"         , 0, 0 },
        { "^[[:digit:]]+ISH$"     , "ISH"           , 0, 0 },
        { "^[[:digit:]]+ISH$"     , "1ISH"          , 0, 1 },
        { RE_QUANTITY             , "1,234MB"       , 0, 1 },
        { RE_QUANTITY             , "5MB"           , 0, 1 },
        { RE_QUANTITY             , "12.5GB"        , 0, 1 },
        { RE_QUANTITY             , ".5KB"          , 0, 1 },
        { RE_QUANTITY             , "1234KB"        , 0, 1 },
        { RE_QUANTITY             , "5M"            , 0, 1 },
        { RE_QUANTITY             , "MB"            , 0, 0 },
        { "^[IVXLCDM]{2,}$"       , "III"           , 0, 1 },
        { "^[IVXLCDM]{2,}$"       , "IV"            , 0, 1 },
        { "^[IVXLCDM]{2,}$"       , "I"             , 0, 0 },
        { "^[IVXLCDM]{2,}$"       , "XIV"           , 0, 1 },
        { "^[IVXLCDM]{2,}$"       , "IIIA"          , 0, 0 },
        { "^[IVXLCDM]{2,}$"       , "MCMLXXXIV"     , 0, 1 },
        { "^[[:upper:]]+&[[:upper:]]+$", "AT&T"          , 0, 1 },
        { "^[[:upper:]]+&[[:upper:]]+$", "A&B"           , 0, 1 },
        { "^[[:upper:]]+&[[:upper:]]+$", "AT&"           , 0, 0 },
        { "^[[:upper:]]+&[[:upper:]]+$", "&B"            , 0, 0 },
        { "^[[:upper:]]+&[[:upper:]]+$", "AB&CD"         , 0, 1 },
        { "^(K|M|G|T|P)B$"        , "KB"            , 0, 1 },
        { "^(K|M|G|T|P)B$"        , "MB"            , 0, 1 },
        { "^(K|M|G|T|P)B$"        , "GB"            , 0, 1 },
        { "^(K|M|G|T|P)B$"        , "TB"            , 0, 1 },
        { "^(K|M|G|T|P)B$"        , "PB"            , 0, 1 },
        { "^(K|M|G|T|P)B$"        , "XB"            , 0, 0 },
        { "^(K|M|G|T|P)B$"        , "K"             , 0, 0 },
        { "^(K|M|G|T|P)B$"        , "KBB"           , 0, 0 },
        { "^[[:digit:]]{3}\\.[[:digit:]]{3}\\.[[:digit:]]{4}$", "555.123.4567"  , 0, 1 },
        { "^[[:digit:]]{3}\\.[[:digit:]]{3}\\.[[:digit:]]{4}$", "5551234567"    , 0, 0 },
        { "^[[:digit:]]{3}\\.[[:digit:]]{3}\\.[[:digit:]]{4}$", "555.123.456"   , 0, 0 },
        { RE_INITIALISM           , "U.S."          , 0, 1 },
        { RE_INITIALISM           , "USA"           , 0, 1 },
        { RE_INITIALISM           , "U.S.A."        , 0, 1 },
        { RE_INITIALISM           , "U.S.A"         , 0, 1 },
        { RE_INITIALISM           , "us"            , 0, 0 },
        { "abc"                   , "xxabcxx"       , 0, 1 },
        { "abc"                   , "abd"           , 0, 0 },
        { "^abc$"                 , "abc"           , 0, 1 },
        { "^abc$"                 , "abcd"          , 0, 0 },
        { "a.c"                   , "abc"           , 0, 1 },
        { "a.c"                   , "ac"            , 0, 0 },
        { "a.*c"                  , "abbbc"         , 0, 1 },
        { "a.*c"                  , "ac"            , 0, 1 },
        { "a+"                    , "b"             , 0, 0 },
        { "a+"                    , "baaa"          , 0, 1 },
        { "colou?r"               , "color"         , 0, 1 },
        { "colou?r"               , "colour"        , 0, 1 },
        { "colou?r"               , "colouur"       , 0, 0 },
        { "^(a|b)+$"              , "abab"          , 0, 1 },
        { "^(a|b)+$"              , "abc"           , 0, 0 },
        { "^(ab|a)(c|bc)$"        , "abc"           , 0, 1 },
        { "^a{3}$"                , "aaa"           , 0, 1 },
        { "^a{3}$"                , "aa"            , 0, 0 },
        { "^a{2,3}$"              , "aaaa"          , 0, 0 },
        { "^a{2,}$"               , "aaaaa"         , 0, 1 },
        { "^[a-c]+$"              , "abcabc"        , 0, 1 },
        { "^[^a-c]+$"             , "xyz"           , 0, 1 },
        { "^[^a-c]+$"             , "xayz"          , 0, 0 },
        { "^[]a]+$"               , "]a]"           , 0, 1 },
        { "^[a-]+$"               , "a-a"           , 0, 1 },
        { "^[.]$"                 , "."             , 0, 1 },
        { "^[.]$"                 , "x"             , 0, 0 },
        { "^\\.$"                 , "."             , 0, 1 },
        { "^\\.$"                 , "x"             , 0, 0 },
        { "^a\\+b$"               , "a+b"           , 0, 1 },
        { "^(a*)*$"               , "aaa"           , 0, 1 },
        { "^(a*)*b$"              , "aaab"          , 0, 1 },
        { "^(|a)b$"               , "b"             , 0, 1 },
        { "^$"                    , ""              , 0, 1 },
        { "^$"                    , "a"             , 0, 0 },
        { "x{"                    , "x{"            , 0, 1 },
        { "^[[:alpha:][:digit:]]+$", "ab12"          , 0, 1 },
        { "^[[:space:]]+$"        , " \t"           , 0, 1 },
        { "^[[:punct:]]+$"        , "!?."           , 0, 1 },
        { "^[[:xdigit:]]+$"       , "dEadBeef99"    , 0, 1 },
        { "^[[:lower:]]+$"        , "abc"           , 0, 1 },
        { "^[[:lower:]]+$"        , "aBc"           , 0, 0 },
        { "^abc$"                 , "ABC"           , 1, 1 },
        { "^[a-c]+$"              , "ABC"           , 1, 1 },
        { "^[^a-c]+$"             , "ABC"           , 1, 0 },
    };
    /* Patterns that are not ERE at all, and must be refused rather than
     * guessed at.  A refusal is what puts a line in the user's log, so it has
     * to stay rare and stay honest. */
    static const char *bad[] = {
        "(",  ")",  "a)",  "[a",  "[[:nosuch:]]",  "*a",  "a|*",  "[z-a]",
    };
    int i, fails = 0;
    for (i = 0; i < (int)(sizeof(cases) / sizeof(cases[0])); i++) {
        re_prog *pr = re_compile(cases[i].pat,
                                 TRE_EXTENDED | TRE_NOSUB |
                                 (cases[i].icase ? TRE_ICASE : 0));
        int got;
        if (!pr) {
            printf("FAIL  refused: %s\n", cases[i].pat);
            fails++;
            continue;
        }
        got = re_search(pr, cases[i].subj);
        if (got != cases[i].want) {
            printf("FAIL  %s  on \"%s\"%s: wanted %d, got %d\n",
                   cases[i].pat, cases[i].subj,
                   cases[i].icase ? " (icase)" : "", cases[i].want, got);
            fails++;
        }
        re_prog_free(pr);
    }
    for (i = 0; i < (int)(sizeof(bad) / sizeof(bad[0])); i++) {
        re_prog *pr = re_compile(bad[i], TRE_EXTENDED | TRE_NOSUB);
        if (pr) {
            printf("FAIL  accepted a pattern it should refuse: %s\n", bad[i]);
            re_prog_free(pr);
            fails++;
        }
    }
    /* A basic RE is refused: '(' and '{' and '+' mean other things there. */
    {
        re_prog *pr = re_compile("^a$", TRE_NOSUB);
        if (pr) { printf("FAIL  accepted a basic RE\n"); re_prog_free(pr); fails++; }
    }
    /* Nested unbounded repetition over a subject that cannot match is the
     * classic way to make a backtracking engine take forever.  This must come
     * back, and come back saying no. */
    {
        re_prog *pr = re_compile("^(a*)*b$", TRE_EXTENDED | TRE_NOSUB);
        if (!pr) { printf("FAIL  refused the backtracking probe\n"); fails++; }
        else {
            if (re_search(pr, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaX")) {
                printf("FAIL  the backtracking probe matched\n");
                fails++;
            }
            re_prog_free(pr);
        }
    }
    printf("regex: %d cases, %d failures\n",
           (int)(sizeof(cases) / sizeof(cases[0])), fails);
    return fails ? 1 : 0;
}
