/* tiger_host_macho.c -- loading, relocating and binding a Mach-O.
 *
 * Part of tiger_host.c, which includes it; see there for why this is one
 * translation unit. */

/* ---- loading ----------------------------------------------------------- */

static unsigned char *read_file(const char *path, size_t *len)
{
    unsigned char *buf;
    long n;
    FILE *f = fopen(path, "rb");
    if (!f) die("cannot open %s", path);
    fseek(f, 0, SEEK_END); n = ftell(f); fseek(f, 0, SEEK_SET);
    buf = (unsigned char *)malloc(n);
    if (!buf || fread(buf, 1, n, f) != (size_t)n) die("cannot read %s", path);
    fclose(f);
    *len = n;
    return buf;
}

/* Both binaries are universal.  Find the i386 slice, or fail loudly -- a
 * PowerPC slice loaded by mistake would fail much later and far less clearly. */
static unsigned char *find_i386(unsigned char *file, size_t len)
{
    unsigned magic = *(unsigned *)file;
    unsigned n, i;
    if (magic == MH_MAGIC) return file;
    if (magic != FAT_CIGAM) die("not a Mach-O (magic %08x)", magic);
    n = bswap(*(unsigned *)(file + 4));
    for (i = 0; i < n; i++) {
        unsigned *a = (unsigned *)(file + 8 + i * 20);
        if (bswap(a[0]) == CPU_TYPE_X86) {
            unsigned off = bswap(a[2]);
            if (off >= len) die("fat slice offset out of range");
            return file + off;
        }
    }
    die("no i386 slice in this file");
    return NULL;
}

static void map_image(image *im)
{
    mach_header *mh = (mach_header *)im->slice;
    unsigned lo = 0xffffffffu, hi = 0, want, i;
    unsigned char *region;
    unsigned char *p = im->slice + sizeof(mach_header);

    if (mh->magic != MH_MAGIC) die("bad mach header in %s", im->path);

    for (i = 0; i < mh->ncmds; i++) {
        load_command *lc = (load_command *)p;
        if (lc->cmd == LC_SEGMENT) {
            segment_command *sc = (segment_command *)p;
            if (sc->vmsize) {
                if (sc->vmaddr < lo) lo = sc->vmaddr;
                if (sc->vmaddr + sc->vmsize > hi) hi = sc->vmaddr + sc->vmsize;
                /* The first writable segment is the relocation base for a
                 * split-segment image. */
                if ((sc->initprot & 2) && !im->data_vmaddr)
                    im->data_vmaddr = sc->vmaddr;
            }
        }
        p += lc->cmdsize;
    }
    im->lo = lo;
    im->hi = hi;

    /* Reserve the whole vm span, then commit segment by segment.
     *
     * Two things make this fussier than it looks.  VirtualAlloc rounds a
     * requested base down to the 64 KB allocation granularity, so asking for
     * SpeechDictionary's 0x96d0c000 yields 0x96d00000 -- reserve from the
     * rounded-down address and the library still lands exactly where it wants.
     * And MacinTalk's lowest vmaddr is 0, where `(void *)0` means "anywhere",
     * so it must slide; page zero is never mappable.
     *
     * Reserving the span costs address space, not memory, which matters
     * because SpeechDictionary is SPLIT_SEGS: its __DATA sits 256 MB above its
     * __TEXT, and only the segments themselves are ever committed.
     */
    want = lo & ~0xffffu;
    region = NULL;
    if (want)
        region = (unsigned char *)VirtualAlloc((void *)want, hi - want,
                                               MEM_RESERVE, PAGE_NOACCESS);
    if (want && region == (unsigned char *)want) {
        im->slide = 0;                       /* mapped at its own address */
    } else {
        /* `want == 0` lands here always, and must: page zero is never
         * mappable, so a bundle based at vmaddr 0 always slides. */
        if (region) VirtualFree(region, 0, MEM_RELEASE);
        region = (unsigned char *)VirtualAlloc(NULL, hi - want, MEM_RESERVE,
                                               PAGE_NOACCESS);
        if (!region) die("cannot reserve %u bytes for %s", hi - want, im->path);
        im->slide = (unsigned)region - want;
    }
    if (g_verbose)
        printf("  vm %08x..%08x -> %08x..%08x (slide %08x)\n",
               lo, hi, lo + im->slide, hi + im->slide, im->slide);

    /* Commit and copy each segment.  vmsize beyond filesize is __bss and
     * __common, which must read as zero; committed pages already are. */
    p = im->slice + sizeof(mach_header);
    for (i = 0; i < mh->ncmds; i++) {
        load_command *lc = (load_command *)p;
        if (lc->cmd == LC_SEGMENT) {
            segment_command *sc = (segment_command *)p;
            unsigned j;
            section *sec = (section *)(p + sizeof(segment_command));
            if (sc->vmsize) {
                void *at = (void *)(sc->vmaddr + im->slide);
                if (!VirtualAlloc(at, sc->vmsize, MEM_COMMIT,
                                  PAGE_EXECUTE_READWRITE))
                    die("cannot commit %s segment %.16s at %p",
                        im->path, sc->segname, at);
                if (sc->filesize)
                    memcpy(at, im->slice + sc->fileoff, sc->filesize);
            }
            /* Unfiltered, and in load-command order: this is the list the
             * dyld info streams index by. */
            if (im->nsegs < 16) im->segaddr[im->nsegs++] = sc->vmaddr;
            for (j = 0; j < sc->nsects; j++) {
                if (im->nsects < 64) im->sects[im->nsects++] = sec[j];
            }
        } else if (lc->cmd == LC_DYLD_INFO || lc->cmd == LC_DYLD_INFO_ONLY) {
            im->info = (const dyld_info_command *)p;
        } else if (lc->cmd == LC_SYMTAB) {
            symtab_command *st = (symtab_command *)p;
            im->syms  = (const nlist *)(im->slice + st->symoff);
            im->nsyms = st->nsyms;
            im->strs  = (const char *)(im->slice + st->stroff);
        } else if (lc->cmd == LC_DYSYMTAB) {
            im->dys = (const dysymtab_command *)p;
        }
        p += lc->cmdsize;
    }
    if (!im->syms || !im->dys) die("%s has no symbol table", im->path);
}

/* Relocation addresses are offsets from the first segment's vmaddr -- except
 * in a split-segment image, where they are offsets from the first *writable*
 * segment.  SpeechDictionary is MH_SPLIT_SEGS with __TEXT at 0x96d0c000 and
 * __DATA at 0xa6d0c000, and its two external relocations sit at 0x90 and
 * 0x960: against the __TEXT base those land inside the Mach header and the
 * load commands.  Everything here is mapped RWX, so those writes succeed
 * silently and corrupt the image.
 */
static unsigned reloc_base(image *im)
{
    return (((const mach_header *)im->slice)->flags & MH_SPLIT_SEGS)
           ? im->data_vmaddr : im->lo;
}

static void apply_relocs(image *im)
{
    const dysymtab_command *d = im->dys;
    const reloc *r = (const reloc *)(im->slice + d->locreloff);
    unsigned base = reloc_base(im);
    unsigned i, applied = 0, skipped = 0;

    if (!im->slide) {
        if (g_verbose)
            printf("  %u local relocations skipped (mapped at base)\n",
                   d->nlocrel);
        return;
    }
    for (i = 0; i < d->nlocrel; i++) {
        if ((unsigned)r[i].r_address & R_SCATTERED) {
            unsigned bits = (unsigned)r[i].r_address;
            unsigned type = (bits >> 24) & 0xf;
            unsigned addr = bits & 0x00ffffff;
            if (type == GENERIC_RELOC_PB_LA_PTR) {
                /* prebound lazy pointer: restore the unprebound value */
                *(unsigned *)(base + addr + im->slide) = r[i].r_info + im->slide;
                applied++;
            } else if (type == GENERIC_RELOC_VANILLA) {
                *(unsigned *)(base + addr + im->slide) += im->slide;
                applied++;
            } else skipped++;
        } else {
            unsigned type = (r[i].r_info >> 28) & 0xf;
            unsigned len  = (r[i].r_info >> 25) & 0x3;
            if (type == GENERIC_RELOC_VANILLA && len == 2) {
                *(unsigned *)(base + r[i].r_address + im->slide) += im->slide;
                applied++;
            } else skipped++;
        }
    }
    if (g_verbose)
        printf("  %u local relocations applied, %u skipped\n", applied, skipped);
}

static void *lookup_shim(const char *name)
{
    int i;
    const char *dollar;
    void *k;
    for (i = 0; g_shims[i].name; i++)
        if (!strcmp(g_shims[i].name, name)) return g_shims[i].fn;
    /* 10.7's speech property constants, which are a family rather than a list
     * -- see speech_const_lookup(). */
    k = speech_const_lookup(name);
    if (k) return k;
    /* Leopard's libSystem publishes conformance variants -- `_open$UNIX2003`,
     * `_pread$UNIX2003`, `_pthread_cond_wait$UNIX2003` and ten more.  They are
     * the same functions with standards-mandated behaviour on edge cases none
     * of this reaches, so the base name is the right answer.  Matching the
     * suffix generically beats adding thirteen more rows and then a fourteenth
     * when a different release names one differently. */
    dollar = strchr(name, '$');
    if (dollar) {
        char base[128];
        size_t n = (size_t)(dollar - name);
        if (n < sizeof(base)) {
            memcpy(base, name, n);
            base[n] = 0;
            for (i = 0; g_shims[i].name; i++)
                if (!strcmp(g_shims[i].name, base)) return g_shims[i].fn;
        }
    }
    return NULL;
}

/* A symbol defined and exported by an already-loaded image. */
/* `depth` bounds an alias chain; nothing real is more than one link long. */
static void *lookup_in_depth(image *dep, const char *name, int depth)
{
    unsigned k;
    if (!dep) return NULL;
    for (k = 0; k < dep->nsyms; k++) {
        const nlist *sy = &dep->syms[k];
        if (sy->n_type & N_STAB) continue;
        if ((sy->n_type & N_TYPE) == N_UNDF) continue;
        if (!(sy->n_type & N_EXT)) continue;
        if (strcmp(dep->strs + sy->n_strx, name)) continue;
        /* **N_INDR is a name, not an address.**  `n_value` indexes the string
         * table: the entry says "this symbol is whatever that name is".
         *
         * Lion's libstdc++ carries 150 of them -- one per C++ ABI symbol it
         * re-exports from libc++abi.dylib, since 10.7 is where the ABI moved
         * out of libstdc++ -- and every one names itself, which is how a
         * re-export is spelled.  Leopard's 6.0.4 has none.
         *
         * Skipping only N_UNDF walked straight past these and returned
         * `n_value + slide`: a text address computed from a string offset.
         * `___dynamic_cast` came out as libstdc++ + 0x24e6c and the engine's
         * first cast jumped four bytes into an unrelated function.  Nothing
         * reported it, because from here nothing had failed -- which is the
         * expensive kind of wrong, not the loud kind.
         *
         * A self-alias cannot be followed, so the honest answer is that this
         * image does not have the symbol: fall through, and let the caller
         * find it elsewhere or thunk it where it can be seen. */
        if ((sy->n_type & N_TYPE) == N_INDR) {
            const char *alias = dep->strs + sy->n_value;
            if (depth > 0 && strcmp(alias, name))
                return lookup_in_depth(dep, alias, depth - 1);
            continue;
        }
        return (void *)(sy->n_value + dep->slide);
    }
    return NULL;
}

static void *lookup_in(image *dep, const char *name)
{
    return lookup_in_depth(dep, name, 4);
}

/* The same, across every image loaded so far except the one being bound.
 *
 * One dependency was enough while MacinTalk only ever needed SpeechDictionary.
 * Leopard's pair also import GCC 4.0.1's C++ runtime -- std::string, the
 * _List_node_base helpers, __dynamic_cast and the RTTI that makes it answer
 * anything but null -- and those must come from Leopard's own
 * libstdc++.6.0.4.dylib rather than a reimplementation: the engine inlines code
 * that walks basic_string's copy-on-write layout, so the bytes have to agree
 * exactly.  Searching the whole set costs nothing and stops the order images
 * are loaded in from mattering.
 */
static void *lookup_loaded(const image *self, const char *name)
{
    int i;
    for (i = 0; i < g_nimages; i++) {
        void *p;
        if (g_images[i] == self) continue;
        p = lookup_in(g_images[i], name);
        if (p) return p;
    }
    return NULL;
}

/* External relocations, which the pointer tables do not cover.
 *
 * These are the references that carry an addend, and the addend is sitting in
 * the location waiting to be added to the resolved address.  The C++ vtable
 * references are exactly this shape: the Itanium ABI stores `&vtable + 8` in
 * an object, so skipping these leaves every vptr pointing eight bytes low --
 * at the vtable's offset-to-top word, which is zero.  The symptom is a call
 * through a null pointer from inside a virtual dispatch, a long way from the
 * cause.
 */
static void apply_ext_relocs(image *im, image *dep)
{
    const dysymtab_command *d = im->dys;
    const reloc *r = (const reloc *)(im->slice + d->extreloff);
    unsigned base = reloc_base(im);
    int prebound = (((const mach_header *)im->slice)->flags & MH_PREBOUND) != 0;
    unsigned i, done = 0, missed = 0;

    for (i = 0; i < d->nextrel; i++) {
        unsigned info = r[i].r_info;
        unsigned symnum = info & 0x00ffffff;
        unsigned pcrel  = (info >> 24) & 1;
        unsigned len    = (info >> 25) & 3;
        unsigned ext    = (info >> 27) & 1;
        unsigned type   = (info >> 28) & 0xf;
        unsigned *at;
        const char *nm;
        void *target;

        if (!ext || type != GENERIC_RELOC_VANILLA || len != 2 || pcrel) {
            missed++;
            continue;
        }
        if (symnum >= im->nsyms) { missed++; continue; }
        nm = im->strs + im->syms[symnum].n_strx;
        target = lookup_shim(nm);
        if (!target) target = lookup_in(im, nm);      /* self first */
        if (!target) target = lookup_loaded(im, nm);
        if (!target) { missed++; continue; }
        at = (unsigned *)(base + r[i].r_address + im->slide);
        /* Normally the stored value is the addend.  In a prebound image it is
         * `prebound target + addend`, and the prebound target is kept in the
         * undefined symbol's n_value -- so subtract it to recover the addend
         * rather than adding to a stale absolute address. */
        if (prebound) *at -= im->syms[symnum].n_value;
        *at += (unsigned)target;
        done++;
    }
    if (g_verbose)
        printf("  %u external relocations applied, %u skipped\n", done, missed);
}

static void bind(image *im, image *dep)
{
    const dysymtab_command *d = im->dys;
    const unsigned *ind = (const unsigned *)(im->slice + d->indirectsymoff);
    int i, bound = 0, thunked = 0, fromdep = 0, local = 0, stubs = 0;

    for (i = 0; i < im->nsects; i++) {
        const section *s = &im->sects[i];
        unsigned n, j, t = s->flags & 0xff, stride = 4;
        /* Two schemes, and this engine changed between releases.  Tiger's
         * MacinTalk is PIC: its stubs jump through __la_sym_ptr2, so binding
         * the pointer sections is enough.  Leopard's is not -- it carries an
         * __IMPORT,__jump_table of five-byte slots that arrive as 0xf4 (hlt)
         * padding and that dyld overwrites in place with `jmp rel32`.  Leave
         * them and the first call into the engine executes a privileged
         * instruction, which is exactly where Alex stopped. */
        if (t == S_SYMBOL_STUBS) {
            if (s->reserved2 != 5) continue;     /* not the rewritable kind */
            stride = 5;
        } else if (t != S_NON_LAZY_SYMBOL_PTR && t != S_LAZY_SYMBOL_PTR) {
            continue;
        } else if (im->info) {
            /* **The streams own the pointer sections for a dyld info image.**
             * Measured against this very table, they name 424 of Snow
             * Leopard's 424 slots and 453 of Lion's 453, agreeing on the
             * symbol at every one -- so there is nothing here left to do, and
             * doing it anyway would write each slot twice.  The
             * INDIRECT_SYMBOL_LOCAL case below is worse than redundant: the
             * rebase stream already slid those slots, and adding the slide a
             * second time puts every one of them out of the image.
             *
             * Stubs stay: they are code, no stream describes them, and Lion
             * carries the same rewritable five-byte __IMPORT jump table
             * Leopard does. */
            continue;
        }
        n = s->size / stride;
        for (j = 0; j < n; j++) {
            unsigned isym = ind[s->reserved1 + j];
            void **slot = (void **)(s->addr + im->slide + j * stride);
            const char *nm;
            void *fn;
            if (stride == 5) {
                /* A stub is code, not a pointer: the two "already resolved"
                 * cases have nothing sensible to write. */
                if (isym & (INDIRECT_SYMBOL_LOCAL | INDIRECT_SYMBOL_ABS))
                    continue;
                if (isym >= im->nsyms) continue;
                nm = im->strs + im->syms[isym].n_strx;
                fn = lookup_shim(nm);
                if (!fn) fn = lookup_in(im, nm);
                if (!fn) { fn = lookup_loaded(im, nm); if (fn) fromdep++; }
                if (fn) { bound++; stubs++; }
                else {
                    if (nm[0] == '_' && nm[1] == '_' && nm[2] == 'Z')
                        printf("    !! unresolved C++ symbol: %s\n", nm);
                    fn = make_thunk(nm);
                    thunked++;
                }
                {
                    unsigned char *at = (unsigned char *)slot;
                    at[0] = 0xe9;                               /* jmp rel32 */
                    *(int *)(at + 1) = (int)((unsigned char *)fn - (at + 5));
                }
                continue;
            }
            /* A slot flagged LOCAL is not bound to anything -- it already
             * holds an address inside this image and only needs the slide.
             * These deliberately carry NO local relocation, so skipping them
             * leaves an unslid pointer: the engine's lookups then quietly
             * return NULL and it faults a long way downstream.  ABS means
             * absolute; leave it alone. */
            if (isym & INDIRECT_SYMBOL_LOCAL) {
                *(unsigned *)slot += im->slide;
                local++;
                continue;
            }
            if (isym & INDIRECT_SYMBOL_ABS) continue;
            if (isym >= im->nsyms) continue;
            nm = im->strs + im->syms[isym].n_strx;
            fn = lookup_shim(nm);
            /* An image resolves against ITSELF first.  PIC code calls its own
             * functions through __picsymbolstub2 so they can be interposed, so
             * a large share of these slots are intra-image -- and binding
             * SpeechDictionary with no dependency to search turned every one
             * of its own C++ symbols, vtables included, into a thunk. */
            if (!fn) fn = lookup_in(im, nm);
            if (!fn) { fn = lookup_loaded(im, nm); if (fn) fromdep++; }
            if (fn) bound++;
            else {
                /* A thunked C++ symbol is never harmless: the engine will use
                 * it as a vtable, and `vtable + 8` lands in the middle of the
                 * thunk's own instructions. */
                if (nm[0] == '_' && nm[1] == '_' && nm[2] == 'Z')
                    printf("    !! unresolved C++ symbol: %s\n", nm);
                fn = make_thunk(nm);
                thunked++;
            }
            *slot = fn;
        }
    }
    if (g_verbose)
        printf("  bound %d (%d from dependency, %d jump-table stubs), "
               "%d stubbed out\n", bound, fromdep, stubs, thunked);
}

static void run_initializers(image *im)
{
    int i;
    for (i = 0; i < im->nsects; i++) {
        const section *s = &im->sects[i];
        if ((s->flags & 0xff) != S_MOD_INIT_FUNC) continue;
        {
            unsigned n = s->size / 4, j;
            void **fns = (void **)(s->addr + im->slide);
            for (j = 0; j < n; j++) {
                if (g_verbose) printf("  initializer %u -> %p\n", j, fns[j]);
                ((void (__cdecl *)(void))fns[j])();
            }
        }
    }
}

static void *find_export(image *im, const char *name)
{
    unsigned i;
    for (i = 0; i < im->nsyms; i++) {
        const nlist *s = &im->syms[i];
        if (s->n_type & N_STAB) continue;
        if ((s->n_type & N_TYPE) == N_UNDF) continue;
        if (!(s->n_type & N_EXT)) continue;
        if (!strcmp(im->strs + s->n_strx, name))
            return (void *)(s->n_value + im->slide);
    }
    return NULL;
}

static void load(image *im, const char *path)
{
    size_t len;
    memset(im, 0, sizeof(*im));
    im->path  = path;
    im->file  = read_file(path, &len);
    im->slice = find_i386(im->file, len);
    if (g_verbose) printf("%s\n", path);
    map_image(im);
    /* Exactly one of these does anything.  An image carries classic
     * relocation tables or a rebase stream, never both: Leopard has
     * nextrel/nlocrel and no LC_DYLD_INFO, Snow Leopard and Lion have the
     * command and zeroed tables.  `apply_relocs` on a compressed image is not
     * an error -- it is a silent no-op, which is exactly how "every internal
     * pointer in the image is unslid" would present. */
    if (im->info) apply_rebases(im);
    else          apply_relocs(im);
}
