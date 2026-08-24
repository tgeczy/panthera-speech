/* tiger_host_dyldinfo.c -- the compressed dyld info: rebase and bind streams.
 *
 * Part of tiger_host.c, which includes it; see there for why this is one
 * translation unit.
 *
 * Snow Leopard is where Apple stopped emitting classic relocation tables.
 * From 10.6 on, MacinTalk carries LC_DYLD_INFO_ONLY and its `nextrel` and
 * `nlocrel` are **zero** -- so `apply_relocs` and `apply_ext_relocs` find
 * nothing to do.  They do not fail; they silently do nothing, and every
 * internal pointer in the image stays unslid.  What replaced those tables is
 * a pair of bytecode streams, and this interprets them.
 *
 * Four streams, two encodings.  `rebase` has its own small opcode set; `bind`,
 * `weak` and `lazy` share one.  They are walked by two functions and consumed
 * through a callback, so that dumping them for `--dyld-check` and applying
 * them to a mapped image are the same walk with a different consumer -- the
 * alternative being two readings of one bytecode that have to agree, which is
 * the shape of bug this codebase has paid for more than once.
 *
 * **Ownership.**  For an image with dyld info the streams own every pointer
 * slot: measured against the indirect symbol table, they name 424 of Snow
 * Leopard's 424 and 453 of Lion's 453, agreeing on the symbol at every one.
 * So `bind()` is reduced to the S_SYMBOL_STUBS case for these images -- which
 * is what stops a slot being written twice, or slid twice.  Nothing here runs
 * for Tiger or Leopard: `im->info` is NULL and the classic path is untouched.
 */

/* Rebase and bind share these three type codes. */
#define REBASE_TYPE_POINTER          1
#define REBASE_TYPE_TEXT_ABSOLUTE32  2
#define REBASE_TYPE_TEXT_PCREL32     3

#define REBASE_OPCODE_MASK                              0xF0
#define REBASE_IMMEDIATE_MASK                           0x0F
#define REBASE_OPCODE_DONE                              0x00
#define REBASE_OPCODE_SET_TYPE_IMM                      0x10
#define REBASE_OPCODE_SET_SEGMENT_AND_OFFSET_ULEB       0x20
#define REBASE_OPCODE_ADD_ADDR_ULEB                     0x30
#define REBASE_OPCODE_ADD_ADDR_IMM_SCALED               0x40
#define REBASE_OPCODE_DO_REBASE_IMM_TIMES               0x50
#define REBASE_OPCODE_DO_REBASE_ULEB_TIMES              0x60
#define REBASE_OPCODE_DO_REBASE_ADD_ADDR_ULEB           0x70
#define REBASE_OPCODE_DO_REBASE_ULEB_TIMES_SKIPPING_ULEB 0x80

#define BIND_OPCODE_MASK                                0xF0
#define BIND_IMMEDIATE_MASK                             0x0F
#define BIND_OPCODE_DONE                                0x00
#define BIND_OPCODE_SET_DYLIB_ORDINAL_IMM               0x10
#define BIND_OPCODE_SET_DYLIB_ORDINAL_ULEB              0x20
#define BIND_OPCODE_SET_DYLIB_SPECIAL_IMM               0x30
#define BIND_OPCODE_SET_SYMBOL_TRAILING_FLAGS_IMM       0x40
#define BIND_OPCODE_SET_TYPE_IMM                        0x50
#define BIND_OPCODE_SET_ADDEND_SLEB                     0x60
#define BIND_OPCODE_SET_SEGMENT_AND_OFFSET_ULEB         0x70
#define BIND_OPCODE_ADD_ADDR_ULEB                       0x80
#define BIND_OPCODE_DO_BIND                             0x90
#define BIND_OPCODE_DO_BIND_ADD_ADDR_ULEB               0xA0
#define BIND_OPCODE_DO_BIND_ADD_ADDR_IMM_SCALED         0xB0
#define BIND_OPCODE_DO_BIND_ULEB_TIMES_SKIPPING_ULEB    0xC0

#define BIND_SYMBOL_FLAGS_WEAK_IMPORT                   0x01

/* Which of the three bind streams a walk is reading.  They differ only in
 * what the consumer does with the result -- and in the lazy stream's use of
 * DONE, which is a separator rather than a terminator. */
#define BIND_KIND_NORMAL 0
#define BIND_KIND_WEAK   1
#define BIND_KIND_LAZY   2

static const char *const g_bind_kind[3] = { "bind", "weak", "lazy" };
static const char g_bind_tag[3] = { 'B', 'W', 'L' };

/* One fixup, before the slide is applied.  `sym` is NULL for a rebase. */
typedef struct {
    unsigned    addr;
    unsigned    type;
    const char *sym;
    int         addend;
    int         weak_import;
    int         kind;           /* BIND_KIND_*, or -1 for a rebase */
} fixup;

typedef void (*fixup_fn)(image *im, const fixup *f, void *ctx);

/* ---- reading the stream ------------------------------------------------ */

typedef struct {
    const unsigned char *p, *end;
    const char          *what;      /* for the error message */
    const char          *path;
} stream;

static unsigned char s_byte(stream *s)
{
    if (s->p >= s->end) die("%s: %s stream ran off the end", s->path, s->what);
    return *s->p++;
}

static unsigned s_uleb(stream *s)
{
    unsigned v = 0, shift = 0, b;
    do {
        b = s_byte(s);
        /* Past 32 bits the extra bits are the sign extension of a value that
         * was always meant to wrap; shifting them in would be undefined. */
        if (shift < 32) v |= (b & 0x7f) << shift;
        shift += 7;
    } while (b & 0x80);
    return v;
}

static int s_sleb(stream *s)
{
    int v = 0;
    unsigned shift = 0, b;
    do {
        b = s_byte(s);
        if (shift < 32) v |= (int)((b & 0x7f) << shift);
        shift += 7;
    } while (b & 0x80);
    if (shift < 32 && (b & 0x40)) v |= -(1 << shift);
    return v;
}

static const char *s_cstring(stream *s)
{
    const char *start = (const char *)s->p;
    while (s->p < s->end && *s->p) s->p++;
    if (s->p >= s->end) die("%s: unterminated symbol name", s->path);
    s->p++;
    return start;
}

/* A slot's address, from the (segment index, offset) pair the streams use.
 *
 * **The offset is meant to wrap.** `ADD_ADDR_ULEB` is how a stream steps
 * backwards: the linker emits a ULEB that overflows a pointer-sized unsigned
 * to reach the negative value it wants. Doing this in `unsigned` is what makes
 * that correct, and it is the one place C gets right for free -- the Python
 * oracle needed an explicit mask, and 75 of Lion's slots landed past 2**64
 * before it had one.
 */
static unsigned seg_addr(image *im, unsigned seg, unsigned off)
{
    if ((int)seg >= im->nsegs)
        die("%s: dyld info names segment %u of %d", im->path, seg, im->nsegs);
    return im->segaddr[seg] + off;
}

/* ---- the two walks ----------------------------------------------------- */

static void walk_rebase(image *im, fixup_fn cb, void *ctx)
{
    const dyld_info_command *in = im->info;
    stream s;
    fixup f;
    unsigned seg = 0, off = 0, count, skip, i;

    if (!in || !in->rebase_size) return;
    s.p = im->slice + in->rebase_off;
    s.end = s.p + in->rebase_size;
    s.what = "rebase";
    s.path = im->path;

    f.sym = NULL; f.addend = 0; f.weak_import = 0; f.kind = -1;
    f.type = REBASE_TYPE_POINTER;

    while (s.p < s.end) {
        unsigned char b = s_byte(&s);
        unsigned op = b & REBASE_OPCODE_MASK, imm = b & REBASE_IMMEDIATE_MASK;
        switch (op) {
        case REBASE_OPCODE_DONE:
            return;
        case REBASE_OPCODE_SET_TYPE_IMM:
            f.type = imm;
            break;
        case REBASE_OPCODE_SET_SEGMENT_AND_OFFSET_ULEB:
            seg = imm; off = s_uleb(&s);
            break;
        case REBASE_OPCODE_ADD_ADDR_ULEB:
            off += s_uleb(&s);
            break;
        case REBASE_OPCODE_ADD_ADDR_IMM_SCALED:
            off += imm * 4;
            break;
        case REBASE_OPCODE_DO_REBASE_IMM_TIMES:
            for (i = 0; i < imm; i++) {
                f.addr = seg_addr(im, seg, off); cb(im, &f, ctx); off += 4;
            }
            break;
        case REBASE_OPCODE_DO_REBASE_ULEB_TIMES:
            count = s_uleb(&s);
            for (i = 0; i < count; i++) {
                f.addr = seg_addr(im, seg, off); cb(im, &f, ctx); off += 4;
            }
            break;
        case REBASE_OPCODE_DO_REBASE_ADD_ADDR_ULEB:
            f.addr = seg_addr(im, seg, off); cb(im, &f, ctx);
            off += 4 + s_uleb(&s);
            break;
        case REBASE_OPCODE_DO_REBASE_ULEB_TIMES_SKIPPING_ULEB:
            count = s_uleb(&s); skip = s_uleb(&s);
            for (i = 0; i < count; i++) {
                f.addr = seg_addr(im, seg, off); cb(im, &f, ctx);
                off += 4 + skip;
            }
            break;
        default:
            die("%s: unknown rebase opcode %02x", im->path, op);
        }
    }
}

static void walk_bind(image *im, int kind, fixup_fn cb, void *ctx)
{
    const dyld_info_command *in = im->info;
    stream s;
    fixup f;
    unsigned seg = 0, off = 0, count, skip, i;
    unsigned start, size;

    if (!in) return;
    switch (kind) {
    case BIND_KIND_NORMAL: start = in->bind_off; size = in->bind_size; break;
    case BIND_KIND_WEAK:   start = in->weak_off; size = in->weak_size; break;
    default:               start = in->lazy_off; size = in->lazy_size; break;
    }
    if (!size) return;

    s.p = im->slice + start;
    s.end = s.p + size;
    s.what = g_bind_kind[kind];
    s.path = im->path;

    f.kind = kind; f.sym = NULL; f.addend = 0; f.weak_import = 0;
    f.type = REBASE_TYPE_POINTER;

    while (s.p < s.end) {
        unsigned char b = s_byte(&s);
        unsigned op = b & BIND_OPCODE_MASK, imm = b & BIND_IMMEDIATE_MASK;
        switch (op) {
        case BIND_OPCODE_DONE:
            /* **In the lazy stream DONE separates, it does not terminate.**
             * That stream is a sequence of little programs, one per lazy
             * pointer, each entered on its own by the stub helper. Treating
             * the first DONE as the end binds exactly one symbol and leaves
             * the rest pointing at helper code -- which, since nothing here
             * implements lazy resolution, means the first call through any of
             * them goes somewhere arbitrary. */
            if (kind != BIND_KIND_LAZY) return;
            f.sym = NULL; f.addend = 0; f.weak_import = 0;
            f.type = REBASE_TYPE_POINTER;
            seg = 0; off = 0;
            break;
        case BIND_OPCODE_SET_DYLIB_ORDINAL_IMM:
        case BIND_OPCODE_SET_DYLIB_SPECIAL_IMM:
            break;                      /* which library: see below */
        case BIND_OPCODE_SET_DYLIB_ORDINAL_ULEB:
            (void)s_uleb(&s);
            break;
        case BIND_OPCODE_SET_SYMBOL_TRAILING_FLAGS_IMM:
            f.weak_import = (imm & BIND_SYMBOL_FLAGS_WEAK_IMPORT) != 0;
            f.sym = s_cstring(&s);
            break;
        case BIND_OPCODE_SET_TYPE_IMM:
            f.type = imm;
            break;
        case BIND_OPCODE_SET_ADDEND_SLEB:
            f.addend = s_sleb(&s);
            break;
        case BIND_OPCODE_SET_SEGMENT_AND_OFFSET_ULEB:
            seg = imm; off = s_uleb(&s);
            break;
        case BIND_OPCODE_ADD_ADDR_ULEB:
            off += s_uleb(&s);
            break;
        case BIND_OPCODE_DO_BIND:
            f.addr = seg_addr(im, seg, off); cb(im, &f, ctx); off += 4;
            break;
        case BIND_OPCODE_DO_BIND_ADD_ADDR_ULEB:
            f.addr = seg_addr(im, seg, off); cb(im, &f, ctx);
            off += 4 + s_uleb(&s);
            break;
        case BIND_OPCODE_DO_BIND_ADD_ADDR_IMM_SCALED:
            f.addr = seg_addr(im, seg, off); cb(im, &f, ctx);
            off += 4 + imm * 4;
            break;
        case BIND_OPCODE_DO_BIND_ULEB_TIMES_SKIPPING_ULEB:
            count = s_uleb(&s); skip = s_uleb(&s);
            for (i = 0; i < count; i++) {
                f.addr = seg_addr(im, seg, off); cb(im, &f, ctx);
                off += 4 + skip;
            }
            break;
        default:
            die("%s: unknown %s opcode %02x", im->path, g_bind_kind[kind], op);
        }
    }
}

/*
 * The dylib ordinal is read and discarded on purpose.  It says which loaded
 * library a symbol should come from, and this host has no such list: the
 * resolver is `lookup_shim -> lookup_in -> lookup_loaded`, which searches the
 * shims, then the image itself, then its dependency.  Honouring the ordinal
 * would mean refusing a symbol we can satisfy because the ordinal named a
 * library we deliberately do not have.
 */

/* ---- consumers --------------------------------------------------------- */

typedef struct { unsigned applied, skipped; } counts;

static void do_rebase(image *im, const fixup *f, void *ctx)
{
    counts *c = (counts *)ctx;
    /* Both kinds that occur hold a 32-bit absolute address that was computed
     * for the image's preferred base: POINTER in a data slot, TEXT_ABSOLUTE32
     * embedded in instructions.  Both want the slide added and nothing else.
     * PCREL32 would not -- it is relative to the instruction, so it survives a
     * slide untouched -- and it does not occur in either engine; counted
     * rather than assumed away. */
    if (f->type == REBASE_TYPE_POINTER ||
        f->type == REBASE_TYPE_TEXT_ABSOLUTE32) {
        *(unsigned *)(f->addr + im->slide) += im->slide;
        c->applied++;
    } else {
        c->skipped++;
    }
}

typedef struct { counts c; image *dep; int thunked, fromdep, weak_missing; }
        bindctx;

static void do_bind(image *im, const fixup *f, void *ctx)
{
    bindctx *b = (bindctx *)ctx;
    void *target;

    if (f->type != REBASE_TYPE_POINTER) { b->c.skipped++; return; }

    target = lookup_shim(f->sym);
    /* Self first, and for the weak stream that is not an optimisation but the
     * whole answer: weak binding is C++ coalescing, so those symbols are
     * *defined here* -- 184 of Lion's 188 -- and `lookup_in` is what finds
     * them.  Treating them as imports would thunk ~180 real functions. */
    if (!target) target = lookup_in(im, f->sym);
    if (!target) {
        target = lookup_loaded(im, f->sym);
        if (target) {
            b->fromdep++;
            /* The engine-to-dictionary direction, and only it: the
             * dictionary defines these, so its own binds never come this
             * way.  See tiger_host_sllog.c and TIGER_SL_LOG. */
            target = sl_interpose(f->sym, target);
        }
    }
    if (!target) {
        /* A weak import is allowed to be absent; that is what the flag means,
         * and the engine tests the slot against zero before using it. */
        if (f->weak_import) {
            *(unsigned *)(f->addr + im->slide) = 0;
            b->weak_missing++;
            return;
        }
        if (f->sym[0] == '_' && f->sym[1] == '_' && f->sym[2] == 'Z')
            printf("    !! unresolved C++ symbol: %s\n", f->sym);
        target = make_thunk(f->sym);
        b->thunked++;
    }
    /* **Written, not added.**  A classic external relocation keeps its addend
     * in the slot, so `apply_ext_relocs` does `*at += target`.  A bind carries
     * its addend in the stream and the slot's file content is meaningless. */
    *(unsigned *)(f->addr + im->slide) = (unsigned)target + f->addend;
    b->c.applied++;
}

/* The one place in this host that writes to real stdout.
 *
 * `printf` is redirected to stderr throughout, because in serve mode stdout
 * carries raw PCM and a stray character corrupts an utterance. Under
 * `--dyld-check` nothing is served and this output *is* the data -- something
 * on the other end diffs it against the Python oracle -- so it belongs on
 * stdout, and the loader's own commentary stays on stderr where it cannot get
 * into the comparison. That separation is the useful half of the arrangement.
 */
static void print_fixup(image *im, const fixup *f, void *ctx)
{
    (void)im; (void)ctx;
    if (f->kind < 0)
        fprintf(stdout, "R %08x %u\n", f->addr, f->type);
    else
        fprintf(stdout, "%c %08x %u %s %d\n", g_bind_tag[f->kind], f->addr,
                f->type, f->sym ? f->sym : "(none)", f->addend);
}

/* ---- what the loader calls --------------------------------------------- */

static int has_dyld_info(const image *im)
{
    return im->info != NULL;
}

static void apply_rebases(image *im)
{
    counts c;
    c.applied = 0; c.skipped = 0;
    if (!has_dyld_info(im)) return;
    /* Unlike the classic path there is no "mapped at base, nothing to do"
     * shortcut worth taking: a zero slide still means every fixup is a
     * no-op add, and skipping the walk would hide a stream that cannot be
     * parsed until the day something does slide. */
    walk_rebase(im, do_rebase, &c);
    if (g_verbose)
        printf("  %u rebases applied, %u skipped (slide %08x)\n",
               c.applied, c.skipped, im->slide);
}

static void apply_binds(image *im, image *dep)
{
    bindctx b;
    int kind;
    memset(&b, 0, sizeof(b));
    b.dep = dep;
    if (!has_dyld_info(im)) return;
    /* Order matters only in that rebases must already have happened: a bind
     * writes an absolute address that must not then be slid. */
    for (kind = BIND_KIND_NORMAL; kind <= BIND_KIND_LAZY; kind++)
        walk_bind(im, kind, do_bind, &b);
    if (g_verbose)
        printf("  %u bound (%d from dependency), %d weak imports left null, "
               "%u skipped, %d stubbed out\n",
               b.c.applied, b.fromdep, b.weak_missing, b.c.skipped, b.thunked);
}

/* Resolve everything an image imports, by whichever route it uses.
 *
 * One function rather than the pair repeated at each call site, because the
 * pair became a triple the moment compressed info arrived and the third line
 * would have had to be remembered three times.  `bind` runs for both kinds --
 * it owns the jump-table stubs always, and the pointer sections only on a
 * classic image; see the guard in it.
 */
static void resolve(image *im, image *dep)
{
    bind(im, dep);
    if (im->info) apply_binds(im, dep);
    else          apply_ext_relocs(im, dep);
}

/* `--dyld-check <mach-o>`: print the streams and nothing else.
 *
 * Takes a bare path and needs no tree, like `--aac-check`, because its whole
 * job is to be compared against `tools/machodyld.py` on a binary the person
 * running it already has.  The addresses printed are the image's own, before
 * any slide, so the two sides are comparable without agreeing about where
 * Windows happened to put it.
 */
static int dyld_check(const char *path)
{
    image im;
    int kind;
    size_t len;

    memset(&im, 0, sizeof(im));
    im.path = path;
    im.file = read_file(path, &len);
    im.slice = find_i386(im.file, len);
    map_image(&im);
    if (!has_dyld_info(&im)) {
        printf("# no LC_DYLD_INFO: classic relocation tables\n");
        return 0;
    }
    walk_rebase(&im, print_fixup, NULL);
    for (kind = BIND_KIND_NORMAL; kind <= BIND_KIND_LAZY; kind++)
        walk_bind(&im, kind, print_fixup, NULL);
    return 0;
}
