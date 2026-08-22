# -*- coding: utf-8 -*-
"""Read a Mach-O's compressed dyld info: the rebase and bind opcode streams.

Snow Leopard is where Apple stopped emitting classic relocation tables.  From
10.6 on, `MacinTalk` carries `LC_DYLD_INFO_ONLY` and its `nextrel`/`nlocrel`
are **zero** -- so the loader's relocation path finds nothing to do and every
internal pointer stays unslid.  What replaced those tables is a pair of
bytecode streams, and this reads them.

Two jobs:

* **A tool.** `py -3 tools/machodyld.py <binary>` prints what an image asks
  for, which is the only way to see it without a Mac.
* **An oracle.** `tests/` compares the host's own interpreter against this,
  and against the indirect symbol table, which is a second and independent
  encoding of some of the same facts.

Deliberately standalone: no imports from the rest of the repository, so that
a disagreement between this and the C is evidence rather than two views of one
mistake.  See `tiger_host_dyldinfo.c` for the side that has to be right.
"""
import struct
import sys

FAT_MAGIC = 0xCAFEBABE
MH_MAGIC = 0xFEEDFACE
CPU_TYPE_X86 = 7

LC_SEGMENT = 0x01
LC_SYMTAB = 0x02
LC_DYSYMTAB = 0x0B
LC_DYLD_INFO = 0x22
LC_DYLD_INFO_ONLY = 0x80000022

#: i386.  Every offset and stride in these streams is in pointers.
PTR = 4

#: Offsets accumulate in a pointer-sized unsigned, and **they are meant to
#: wrap**.  `ADD_ADDR_ULEB` is how a stream steps *backwards*: the linker emits
#: a ULEB that overflows to the negative value it wants, because dyld adds it
#: to a `uintptr_t`.  C gets this right by doing nothing.  Python's integers
#: are unbounded, so without this mask an offset that should have gone down by
#: eight instead lands somewhere past 2**64, and every record after it in that
#: segment is silently misplaced.
MASK32 = 0xFFFFFFFF


def u32(v):
    return v & MASK32


REBASE_TYPES = {1: "POINTER", 2: "TEXT_ABSOLUTE32", 3: "TEXT_PCREL32"}
BIND_TYPES = REBASE_TYPES

REBASE_OPCODES = {
    0x00: "DONE", 0x10: "SET_TYPE_IMM", 0x20: "SET_SEGMENT_AND_OFFSET_ULEB",
    0x30: "ADD_ADDR_ULEB", 0x40: "ADD_ADDR_IMM_SCALED",
    0x50: "DO_REBASE_IMM_TIMES", 0x60: "DO_REBASE_ULEB_TIMES",
    0x70: "DO_REBASE_ADD_ADDR_ULEB",
    0x80: "DO_REBASE_ULEB_TIMES_SKIPPING_ULEB",
}
BIND_OPCODES = {
    0x00: "DONE", 0x10: "SET_DYLIB_ORDINAL_IMM", 0x20: "SET_DYLIB_ORDINAL_ULEB",
    0x30: "SET_DYLIB_SPECIAL_IMM", 0x40: "SET_SYMBOL_TRAILING_FLAGS_IMM",
    0x50: "SET_TYPE_IMM", 0x60: "SET_ADDEND_SLEB",
    0x70: "SET_SEGMENT_AND_OFFSET_ULEB", 0x80: "ADD_ADDR_ULEB",
    0x90: "DO_BIND", 0xA0: "DO_BIND_ADD_ADDR_ULEB",
    0xB0: "DO_BIND_ADD_ADDR_IMM_SCALED",
    0xC0: "DO_BIND_ULEB_TIMES_SKIPPING_ULEB",
}

BIND_SYMBOL_FLAGS_WEAK_IMPORT = 0x1


class Reader(object):
    """A cursor over one opcode stream."""

    def __init__(self, data, start, size):
        self.d, self.p, self.end = data, start, start + size

    def byte(self):
        b = self.d[self.p]
        self.p += 1
        return b if isinstance(b, int) else ord(b)

    def uleb(self):
        v, shift = 0, 0
        while True:
            b = self.byte()
            v |= (b & 0x7F) << shift
            shift += 7
            if not (b & 0x80):
                return v

    def sleb(self):
        v, shift = 0, 0
        while True:
            b = self.byte()
            v |= (b & 0x7F) << shift
            shift += 7
            if not (b & 0x80):
                if b & 0x40:
                    v -= (1 << shift)
                return v

    def cstring(self):
        e = self.d.index(b"\0", self.p)
        s = self.d[self.p:e].decode("utf-8", "replace")
        self.p = e + 1
        return s

    def more(self):
        return self.p < self.end


class Image(object):
    """Just enough Mach-O to reach the streams and name an address."""

    def __init__(self, path):
        self.path = path
        self.data = open(path, "rb").read()
        self.off = self._i386()
        self.segments = []            # (name, vmaddr, vmsize, initprot)
        self.info = None              # dict of stream offsets/sizes
        self.symtab = None
        self.dysymtab = None
        self.sections = []            # (segname, sectname, addr, size, flags,
                                      #  reserved1, reserved2)
        self._commands()

    def _i386(self):
        d = self.data
        if struct.unpack(">I", d[:4])[0] != FAT_MAGIC:
            if struct.unpack("<I", d[:4])[0] != MH_MAGIC:
                raise SystemExit("%s: not a 32-bit Mach-O" % self.path)
            return 0
        n, = struct.unpack(">I", d[4:8])
        for i in range(n):
            f = struct.unpack(">5I", d[8 + i * 20:28 + i * 20])
            if f[0] == CPU_TYPE_X86:
                return f[2]
        raise SystemExit("%s: no i386 slice" % self.path)

    def _commands(self):
        d, off = self.data, self.off
        ncmds, = struct.unpack("<I", d[off + 16:off + 20])
        p = off + 28
        for _ in range(ncmds):
            cmd, size = struct.unpack("<II", d[p:p + 8])
            if cmd == LC_SEGMENT:
                name = d[p + 8:p + 24].rstrip(b"\0").decode()
                vmaddr, vmsize, _fo, _fs, maxprot, initprot, nsects, _fl = \
                    struct.unpack("<8I", d[p + 24:p + 56])
                self.segments.append((name, vmaddr, vmsize, initprot))
                sp = p + 56
                for _s in range(nsects):
                    sect = d[sp:sp + 16].rstrip(b"\0").decode("utf-8", "replace")
                    seg = d[sp + 16:sp + 32].rstrip(b"\0").decode("utf-8",
                                                                 "replace")
                    addr, sz = struct.unpack("<II", d[sp + 32:sp + 40])
                    flags, r1, r2 = struct.unpack("<III", d[sp + 56:sp + 68])
                    self.sections.append((seg, sect, addr, sz, flags, r1, r2))
                    sp += 68
            elif cmd in (LC_DYLD_INFO, LC_DYLD_INFO_ONLY):
                f = struct.unpack("<10I", d[p + 8:p + 48])
                self.info = dict(zip(
                    ("rebase_off", "rebase_size", "bind_off", "bind_size",
                     "weak_off", "weak_size", "lazy_off", "lazy_size",
                     "export_off", "export_size"), f))
                self.info["only"] = cmd == LC_DYLD_INFO_ONLY
            elif cmd == LC_SYMTAB:
                self.symtab = struct.unpack("<4I", d[p + 8:p + 24])
            elif cmd == LC_DYSYMTAB:
                self.dysymtab = struct.unpack("<18I", d[p + 8:p + 80])
            p += size

    # -- naming an address ------------------------------------------------

    def seg_addr(self, index, offset):
        """-> vmaddr for a (segment index, offset) pair, as the streams use."""
        if index >= len(self.segments):
            raise ValueError("segment index %d of %d" % (index,
                                                         len(self.segments)))
        return u32(self.segments[index][1] + offset)

    def writable(self, vmaddr):
        """-> True if this address is inside a writable segment.

        The one invariant worth asserting about a rebase: it has to land
        somewhere the loader may write.  A stream that points into __TEXT is
        either misparsed or would corrupt the image.
        """
        VM_PROT_WRITE = 0x2
        for _n, va, sz, prot in self.segments:
            if va <= vmaddr < va + sz:
                return bool(prot & VM_PROT_WRITE)
        return False

    def undefined_symbols(self):
        """-> set of names `LC_SYMTAB` marks undefined and external.

        The bind streams may only name these; anything else means the parse
        has drifted.
        """
        symoff, nsyms, stroff, _ = self.symtab
        d, off = self.data, self.off
        out = set()
        for i in range(nsyms):
            q = off + symoff + i * 12
            strx, typ = struct.unpack("<IB", d[q:q + 5])
            if typ & 0xE0:                      # N_STAB
                continue
            if (typ & 0x0E) == 0x00 and (typ & 0x01):
                e = d.index(b"\0", off + stroff + strx)
                out.add(d[off + stroff + strx:e].decode("utf-8", "replace"))
        return out

    def _string(self, strx):
        """-> the string table entry at `strx`."""
        base = self.off + self.symtab[2] + strx
        return self.data[base:self.data.index(b"\0", base)].decode(
            "utf-8", "replace")

    def indirect_symbols(self):
        """-> {name: the name it aliases} for every N_INDR entry.

        An indirect symbol is a **name, not an address**: `n_value` indexes
        the string table rather than holding a value. Lion's libstdc++ carries
        150 of them, one per C++ ABI symbol it re-exports from
        `libc++abi.dylib`, and every one names itself.

        Read as a definition, such an entry yields `n_value + slide` -- a text
        address computed from a string offset. That is how `___dynamic_cast`
        came to point four bytes into an unrelated function, with nothing
        reporting a failure because, as far as the loader knew, nothing had
        failed.
        """
        symoff, nsyms = self.symtab[0], self.symtab[1]
        d, off = self.data, self.off
        out = {}
        for i in range(nsyms):
            q = off + symoff + i * 12
            strx, typ = struct.unpack("<IB", d[q:q + 5])
            if typ & 0xE0:                      # N_STAB
                continue
            if (typ & 0x0E) != 0x0A:            # N_INDR
                continue
            out[self._string(strx)] = self._string(
                struct.unpack("<I", d[q + 8:q + 12])[0])
        return out

    def exported_symbols(self):
        """-> set of names this image defines, as `find_export` sees them.

        The weak streams name symbols the image *defines* rather than imports
        -- C++ coalescing -- so this is what a resolver would actually find
        for them.

        **N_INDR is not a definition either**, and skipping only N_UNDF does
        not say so; see `indirect_symbols`.
        """
        symoff, nsyms, stroff, _ = self.symtab
        d, off = self.data, self.off
        out = set()
        for i in range(nsyms):
            q = off + symoff + i * 12
            strx, typ = struct.unpack("<IB", d[q:q + 5])
            if typ & 0xE0:                      # N_STAB
                continue
            if (typ & 0x0E) in (0x00, 0x0A) or not (typ & 0x01):
                continue
            e = d.index(b"\0", off + stroff + strx)
            out.add(d[off + stroff + strx:e].decode("utf-8", "replace"))
        return out

    def indirect_pointer_slots(self):
        """-> {vmaddr: symbol name} for every indirect pointer slot.

        The indirect symbol table is a **second, independent** encoding of
        part of what the bind streams say, and it is parsed by code that has
        been right since Tiger. Agreement between it and the streams is real
        evidence; agreement between two readings of the streams is not.
        """
        S_NON_LAZY, S_LAZY = 0x6, 0x7
        INDIRECT_LOCAL, INDIRECT_ABS = 0x80000000, 0x40000000
        d, off = self.data, self.off
        symoff, nsyms, stroff, _ = self.symtab
        indirectsymoff, nindirect = self.dysymtab[12], self.dysymtab[13]
        out = {}
        for seg, sect, addr, size, flags, r1, _r2 in self.sections:
            if (flags & 0xFF) not in (S_NON_LAZY, S_LAZY):
                continue
            for j in range(size // PTR):
                k = r1 + j
                if k >= nindirect:
                    continue
                isym, = struct.unpack(
                    "<I", d[off + indirectsymoff + k * 4:
                            off + indirectsymoff + k * 4 + 4])
                if isym & (INDIRECT_LOCAL | INDIRECT_ABS) or isym >= nsyms:
                    continue
                q = off + symoff + isym * 12
                strx, = struct.unpack("<I", d[q:q + 4])
                e = d.index(b"\0", off + stroff + strx)
                out[addr + j * PTR] = d[off + stroff + strx:e].decode(
                    "utf-8", "replace")
        return out


# -- the two walkers ------------------------------------------------------

def walk_rebase(im):
    """-> [(vmaddr, type)], plus the set of opcode names seen.

    One address per rebase, expanded from the run-length forms, because that
    is what the host has to end up writing and what a comparison can check.
    """
    info = im.info
    if not info or not info["rebase_size"]:
        return [], set()
    r = Reader(im.data, im.off + info["rebase_off"], info["rebase_size"])
    out, seen = [], set()
    typ, seg, off = 1, 0, 0
    while r.more():
        b = r.byte()
        op, imm = b & 0xF0, b & 0x0F
        seen.add(REBASE_OPCODES.get(op, "?%02x" % op))
        if op == 0x00:                                   # DONE
            break
        elif op == 0x10:                                 # SET_TYPE_IMM
            typ = imm
        elif op == 0x20:                                 # SET_SEG_AND_OFF
            seg, off = imm, u32(r.uleb())
        elif op == 0x30:                                 # ADD_ADDR_ULEB
            off = u32(off + r.uleb())
        elif op == 0x40:                                 # ADD_ADDR_IMM_SCALED
            off = u32(off + imm * PTR)
        elif op == 0x50:                                 # DO_REBASE_IMM_TIMES
            for _ in range(imm):
                out.append((im.seg_addr(seg, off), typ))
                off = u32(off + PTR)
        elif op == 0x60:                                 # DO_REBASE_ULEB_TIMES
            for _ in range(r.uleb()):
                out.append((im.seg_addr(seg, off), typ))
                off = u32(off + PTR)
        elif op == 0x70:                                 # DO_REBASE_ADD_ADDR
            out.append((im.seg_addr(seg, off), typ))
            off = u32(off + PTR + r.uleb())
        elif op == 0x80:                                 # ..TIMES_SKIPPING
            count, skip = r.uleb(), r.uleb()
            for _ in range(count):
                out.append((im.seg_addr(seg, off), typ))
                off = u32(off + PTR + skip)
        else:
            raise SystemExit("unknown rebase opcode %02x at %d"
                             % (op, r.p - 1))
    return out, seen


def walk_bind(im, which):
    """-> [record], opcode names seen.  `which` is 'bind', 'weak' or 'lazy'.

    One opcode set for all three streams -- they differ only in what the
    loader does with the result, and in the lazy stream's use of DONE.

    **DONE is a separator in the lazy stream, not a terminator.** It is a
    sequence of little programs, one per lazy pointer, each entered on its own
    by the stub helper. Walking it as a single program and stopping at the
    first DONE binds exactly one symbol and silently leaves the rest.
    """
    info = im.info
    if not info or not info[which + "_size"]:
        return [], set()
    r = Reader(im.data, im.off + info[which + "_off"], info[which + "_size"])
    out, seen = [], set()
    lazy = which == "lazy"

    def fresh():
        return {"type": 1, "seg": 0, "off": 0, "sym": None, "addend": 0,
                "ordinal": 0, "weak_import": False}

    st = fresh()
    while r.more():
        b = r.byte()
        op, imm = b & 0xF0, b & 0x0F
        seen.add(BIND_OPCODES.get(op, "?%02x" % op))
        if op == 0x00:                                   # DONE
            if lazy:
                st = fresh()          # separator: next mini-program
                continue
            break
        elif op == 0x10:
            st["ordinal"] = imm
        elif op == 0x20:
            st["ordinal"] = r.uleb()
        elif op == 0x30:                                 # SPECIAL_IMM
            st["ordinal"] = (imm | 0xF0) - 0x100 if imm else 0
        elif op == 0x40:                                 # SET_SYMBOL...FLAGS
            st["sym"] = r.cstring()
            st["weak_import"] = bool(imm & BIND_SYMBOL_FLAGS_WEAK_IMPORT)
        elif op == 0x50:
            st["type"] = imm
        elif op == 0x60:
            st["addend"] = r.sleb()
        elif op == 0x70:
            st["seg"], st["off"] = imm, u32(r.uleb())
        elif op == 0x80:
            st["off"] = u32(st["off"] + r.uleb())
        elif op == 0x90:                                 # DO_BIND
            out.append(_rec(im, st))
            st["off"] = u32(st["off"] + PTR)
        elif op == 0xA0:                                 # DO_BIND_ADD_ADDR_ULEB
            out.append(_rec(im, st))
            st["off"] = u32(st["off"] + PTR + r.uleb())
        elif op == 0xB0:                                 # ..IMM_SCALED
            out.append(_rec(im, st))
            st["off"] = u32(st["off"] + PTR + imm * PTR)
        elif op == 0xC0:                                 # ..TIMES_SKIPPING
            count, skip = r.uleb(), r.uleb()
            for _ in range(count):
                out.append(_rec(im, st))
                st["off"] = u32(st["off"] + PTR + skip)
        else:
            raise SystemExit("unknown bind opcode %02x at %d" % (op, r.p - 1))
    return out, seen


def _rec(im, st):
    return (im.seg_addr(st["seg"], st["off"]), st["type"], st["sym"],
            st["addend"], st["weak_import"])


# -- reporting -------------------------------------------------------------

def dump(path, verbose=False):
    im = Image(path)
    print("# %s" % path)
    if not im.info:
        print("no LC_DYLD_INFO: this image uses classic relocation tables")
        return im, None
    print("# segments")
    for i, (n, va, sz, prot) in enumerate(im.segments):
        print("seg %d %-12s %08x..%08x %s" % (i, n, va, va + sz,
                                              "rw" if prot & 2 else "r-"))
    rebases, rops = walk_rebase(im)
    binds, bops = walk_bind(im, "bind")
    weaks, wops = walk_bind(im, "weak")
    lazies, lops = walk_bind(im, "lazy")

    print("# counts")
    print("rebase %d" % len(rebases))
    print("bind %d" % len(binds))
    print("weak %d" % len(weaks))
    print("lazy %d" % len(lazies))
    print("# types")
    for label, recs, idx in (("rebase", rebases, 1), ("bind", binds, 1),
                             ("weak", weaks, 1), ("lazy", lazies, 1)):
        kinds = {}
        for rec in recs:
            kinds[REBASE_TYPES.get(rec[idx], rec[idx])] = \
                kinds.get(REBASE_TYPES.get(rec[idx], rec[idx]), 0) + 1
        if kinds:
            print("%s: %s" % (label, ", ".join(
                "%s=%d" % kv for kv in sorted(kinds.items()))))
    print("# opcodes")
    print("rebase: %s" % " ".join(sorted(rops)))
    print("bind: %s" % " ".join(sorted(bops)))
    print("weak: %s" % " ".join(sorted(wops)))
    print("lazy: %s" % " ".join(sorted(lops)))

    if verbose:
        print("# rebase records")
        for a, t in rebases:
            print("R %08x %s" % (a, REBASE_TYPES.get(t, t)))
        for label, recs in (("B", binds), ("W", weaks), ("L", lazies)):
            print("# %s records" % label)
            for a, t, sym, add, wk in recs:
                print("%s %08x %s %s %+d%s" % (label, a,
                                               REBASE_TYPES.get(t, t), sym,
                                               add, " weak" if wk else ""))
    return im, (rebases, binds, weaks, lazies)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    verbose = "-v" in sys.argv[1:]
    if not args:
        raise SystemExit(__doc__.strip().splitlines()[0] +
                         "\n\nusage: machodyld.py [-v] <mach-o> ...")
    for a in args:
        dump(a, verbose)
        print()
