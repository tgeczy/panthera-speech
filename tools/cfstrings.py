"""List every CFString literal in a Mach-O.

The engine asks CFPreferencesCopyAppValue for settings we answer with NULL,
and the keys are CFString constants, so they are all in __cfstring: a table of
16-byte records whose third word points at the C string.

    py -3 cfstrings.py <binary> [regex]
"""
import re, struct, sys

path = sys.argv[1]
pat = re.compile(sys.argv[2], re.I) if len(sys.argv) > 2 else None

d = open(path, "rb").read()
if struct.unpack(">I", d[:4])[0] == 0xcafebabe:
    n = struct.unpack(">I", d[4:8])[0]
    for i in range(n):
        cpu, sub, off, size, al = struct.unpack(">iiIII", d[8 + i * 20:28 + i * 20])
        if cpu == 7:
            d = d[off:off + size]
            break

ncmds = struct.unpack("<I", d[16:20])[0]
o, sects = 28, []
for _ in range(ncmds):
    cmd, sz = struct.unpack("<II", d[o:o + 8])
    if cmd == 1:
        nsects = struct.unpack("<I", d[o + 48:o + 52])[0]
        so = o + 56
        for _k in range(nsects):
            name = d[so:so + 16].rstrip(b"\0").decode()
            addr, size, foff = struct.unpack("<III", d[so + 32:so + 44])
            sects.append((name, addr, size, foff))
            so += 68
    o += sz

by_name = {n: (a, s, f) for n, a, s, f in sects}


def read_at(addr, limit=400):
    """-> the C string at a virtual address, wherever it lives."""
    for name, a, s, f in sects:
        if a <= addr < a + s:
            start = f + (addr - a)
            end = d.index(b"\0", start)
            return d[start:min(end, start + limit)].decode("utf-8", "replace")
    return None


if "__cfstring" not in by_name:
    raise SystemExit("no __cfstring section")

addr, size, foff = by_name["__cfstring"]
out = []
for i in range(size // 16):
    rec = d[foff + i * 16:foff + i * 16 + 16]
    _isa, _flags, cstr, length = struct.unpack("<IIII", rec)
    if not cstr:
        continue
    s = read_at(cstr)
    if s is None:
        continue
    if pat and not pat.search(s):
        continue
    out.append((addr + i * 16, s))

for a, s in out:
    print("0x%08x  %s" % (a, s))
print("(%d CFString constants)" % len(out), file=sys.stderr)
