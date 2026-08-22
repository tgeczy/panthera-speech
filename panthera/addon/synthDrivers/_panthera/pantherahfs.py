# -*- coding: utf-8 -*-
"""Enough of HFS+, UDIF, xar and cpio to read a Mac OS X install image.

**Lifted verbatim from `lion/tools/extract_lion.py`**, which is the copy that
was verified 98 files byte-identical against a hand-made tree.  It is here
because the add-on needs the same reader and cannot import a script that ships
separately: the three extractors are deliberately standalone single files, so
that someone who wants one downloads one file and not four.

That makes this the fourth copy of the reader, which is the trade this project
usually refuses -- see the note at the top of `pantheradriver.py`.  It is
accepted here for one reason: the standalone extractors have a hard
requirement to be single files, and the add-on has a hard requirement not to
shell out to anything.  `tests/test_disc_reader.py` reads the same image
through both copies and compares, so a divergence fails rather than lurks.

The one thing this does not do is reach the network or write anything.  It
opens an image the user chose, reads bytes out of it, and hands them back.
"""
import io
import os
import struct
import zlib
import bz2
import base64
import plistlib
import re
ROOT_ID = 2
#: Just the file-type bits of a POSIX mode, and the three
#: kinds that appear in these images.
S_IFMT = 0o170000
S_IFDIR = 0o040000
S_IFREG = 0o100000
S_IFLNK = 0o120000
ILLEGAL = set('<>:"|?*')
def find_hfs(read_at, size, what):
    """-> offset of the Apple_HFS partition within a container.

    `read_at(offset, n)` reads from the container; used for the image file and
    again for the decompressed disk image inside it.
    """
    block0 = read_at(0, 16)
    if len(block0) < 8:
        raise SystemExit("%s is too small to be a disc image" % what)
    if block0[:2] != b"ER":
        if read_at(1024, 2) in (b"H+", b"HX"):
            return 0
        raise SystemExit(
            "%s has no Apple partition map and is not an HFS volume." % what)
    stride = struct.unpack(">H", block0[2:4])[0] or 512
    entry = read_at(stride, 512)
    if entry[:2] != b"PM":
        raise SystemExit("%s: the partition map is not where block zero says"
                         % what)
    count = struct.unpack(">I", entry[4:8])[0]
    for i in range(count):
        e = read_at(stride * (1 + i), 512)
        if e[:2] != b"PM":
            break
        start, _size = struct.unpack(">II", e[8:16])
        kind = e[48:80].split(b"\0")[0].decode("ascii", "replace")
        if kind == "Apple_HFS":
            return start * stride
    raise SystemExit("no Apple_HFS partition in %s" % what)


# ---- HFS+ ---------------------------------------------------------------
class Volume(object):
    """Just enough HFS+ to find files and read them.

    `source` is a path or an already-open seekable stream -- the disk image
    inside the image is the second case.
    """

    def __init__(self, source, base):
        self.f = open(source, "rb") if isinstance(source, str) else source
        self.base = base
        self.f.seek(base + 1024)
        vh = self.f.read(512)
        if vh[:2] not in (b"H+", b"HX"):
            raise SystemExit("no HFS+ volume header at offset %d" % base)
        self.bs = struct.unpack(">I", vh[40:44])[0]
        self.kids = {}
        self.short = {}
        self._read_tree(vh, 272, self._catalog_record)
        if self.short:
            self._resolve_short(vh)

    # -- B-trees ----------------------------------------------------------
    def _fork(self, vh, off):
        logical = struct.unpack(">Q", vh[off:off + 8])[0]
        ext = [struct.unpack(">II", vh[off + 16 + 8 * i:off + 24 + 8 * i])
               for i in range(8)]
        return logical, [e for e in ext if e[1]]

    def _read_tree(self, vh, fork_off, handler):
        """Walk every leaf of one B-tree through the `fLink` chain.

        Rather than implement HFS+'s case-insensitive Unicode key ordering --
        genuinely fiddly and easy to get subtly wrong -- this reads every leaf
        and builds the answer in memory.
        """
        logical, extents = self._fork(vh, fork_off)
        buf = bytearray()
        for start, count in extents:
            self.f.seek(self.base + start * self.bs)
            buf += self.f.read(count * self.bs)
        tree = bytes(buf[:logical])
        if len(tree) < 14:
            return
        if len(tree) < logical:
            raise SystemExit("a B-tree is truncated -- image incomplete?")
        node_size = struct.unpack(">H", tree[32:34])[0]
        node = struct.unpack(">I", tree[24:28])[0]
        seen = set()
        while node and node not in seen:
            seen.add(node)
            nd = tree[node * node_size:(node + 1) * node_size]
            if len(nd) < 14:
                break
            flink, _b, kind, _h, nrecs = struct.unpack(">IIbBH", nd[:12])
            if kind != -1:                               # not a leaf
                break
            for i in range(nrecs):
                a = struct.unpack(">H", nd[node_size - 2 * (i + 1):
                                           node_size - 2 * i])[0]
                b = struct.unpack(">H", nd[node_size - 2 * (i + 2):
                                           node_size - 2 * (i + 1)])[0]
                handler(nd[a:b])
            node = flink

    def _catalog_record(self, rec):
        if len(rec) < 8:
            return
        klen = struct.unpack(">H", rec[:2])[0]
        parent = struct.unpack(">I", rec[2:6])[0]
        nlen = struct.unpack(">H", rec[6:8])[0]
        name = rec[8:8 + 2 * nlen].decode("utf-16-be", "replace")
        data = rec[2 + klen + (klen % 2):]
        if len(data) < 12:
            return
        kind = struct.unpack(">h", data[:2])[0]
        node_id = struct.unpack(">I", data[8:12])[0]
        if kind == 1:                                    # folder
            self.kids.setdefault(parent, []).append(
                [name, True, node_id, 0, [], 0])
        elif kind == 2:                                  # file
            fork = data[88:168]
            size = struct.unpack(">Q", fork[:8])[0]
            blocks = struct.unpack(">I", fork[12:16])[0]
            ext = [struct.unpack(">II", fork[16 + 8 * j:24 + 8 * j])
                   for j in range(8)]
            ext = [e for e in ext if e[1]]
            mode = struct.unpack(">H", data[42:44])[0]
            entry = [name, False, node_id, size, ext, mode]
            self.kids.setdefault(parent, []).append(entry)
            if sum(c for _s, c in ext) < blocks:
                # More than eight extents: the rest are in the extents
                # overflow tree, read next.  On a Lion installer exactly one
                # file is like this, and it is BaseSystem.dmg.
                self.short[node_id] = (entry, blocks)

    def _overflow_record(self, rec):
        """One HFSPlusExtentKey and its eight extents."""
        if len(rec) < 12 + 64:
            return
        klen = struct.unpack(">H", rec[:2])[0]
        fork_type = rec[2]
        file_id, start_block = struct.unpack(">II", rec[4:12])
        if fork_type != 0:                               # data fork only
            return
        data = rec[2 + klen:]
        ext = [struct.unpack(">II", data[8 * j:8 * j + 8]) for j in range(8)]
        self._extra.setdefault(file_id, []).append(
            (start_block, [e for e in ext if e[1]]))

    def _resolve_short(self, vh):
        self._extra = {}
        self._read_tree(vh, 192, self._overflow_record)
        for cnid, (entry, blocks) in self.short.items():
            # Each record says which block offset it continues from, so
            # ordering by that key puts the pieces back in file order.
            for _start, ext in sorted(self._extra.get(cnid, [])):
                entry[4] = entry[4] + ext
            if sum(c for _s, c in entry[4]) < blocks:
                entry[4] = None                          # still short: refuse

    # -- lookup -----------------------------------------------------------
    def entry(self, path):
        node, found = ROOT_ID, None
        for part in [p for p in path.split("/") if p]:
            for e in self.kids.get(node, []):
                if e[0] == part:
                    found, node = e, e[2]
                    break
            else:
                return None
        return found

    def children(self, entry):
        return sorted(self.kids.get(entry[2], []))

    def read(self, entry):
        if entry[4] is None:
            raise SystemExit("%s is too fragmented for this reader" % entry[0])
        out = bytearray()
        for start, count in entry[4]:
            self.f.seek(self.base + start * self.bs)
            out += self.f.read(count * self.bs)
        return bytes(out[:entry[3]])
class ForkReader(io.RawIOBase):
    """A sequential file-like view of one HFS+ file, read in extent order."""

    CHUNK = 32                                   # allocation blocks per read

    def __init__(self, volume, entry, skip=0):
        if entry[4] is None:
            raise SystemExit("%s is too fragmented for this reader" % entry[0])
        self.v = volume
        self.ext = list(entry[4])
        self.buf = b""
        while skip > 0:
            got = self.read(min(skip, 1 << 20))
            if not got:
                break
            skip -= len(got)

    def readable(self):
        return True

    def read(self, n=-1):
        out = b""
        while (n < 0 or n > 0) and (self.buf or self.ext):
            if not self.buf:
                start, count = self.ext[0]
                take = min(count, self.CHUNK)
                self.v.f.seek(self.v.base + start * self.v.bs)
                self.buf = self.v.f.read(take * self.v.bs)
                if take >= count:
                    self.ext.pop(0)
                else:
                    self.ext[0] = (start + take, count - take)
            piece = self.buf if n < 0 else self.buf[:n]
            self.buf = self.buf[len(piece):]
            out += piece
            if n > 0:
                n -= len(piece)
        return out

    def readinto(self, b):
        data = self.read(len(b))
        b[:len(data)] = data
        return len(data)
class ExtentStream(io.RawIOBase):
    """A **seekable** view of one HFS+ file, for reading a disk image inside
    a disk image.  `ForkReader` only goes forwards; UDIF needs to start at the
    trailer and jump about."""

    def __init__(self, volume, entry):
        if entry[4] is None:
            raise SystemExit("%s is too fragmented for this reader" % entry[0])
        self.f, self.size, self.pos = volume.f, entry[3], 0
        self.map, off = [], 0
        for start, count in entry[4]:
            self.map.append((off, count * volume.bs,
                             volume.base + start * volume.bs))
            off += count * volume.bs

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=io.SEEK_SET):
        self.pos = (off if whence == io.SEEK_SET else
                    self.pos + off if whence == io.SEEK_CUR else
                    self.size + off)
        return self.pos

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        out = bytearray()
        while n > 0 and self.pos < self.size:
            for off, length, phys in self.map:
                if off <= self.pos < off + length:
                    take = min(n, off + length - self.pos,
                               self.size - self.pos)
                    self.f.seek(phys + (self.pos - off))
                    out += self.f.read(take)
                    self.pos += take
                    n -= take
                    break
            else:
                break
        return bytes(out)

    def readinto(self, b):
        data = self.read(len(b))
        b[:len(data)] = data
        return len(data)


# ---- UDIF ---------------------------------------------------------------

ZERO, RAW, IGNORE = 0x00000000, 0x00000001, 0x00000002
ZLIB, BZ2, LZFSE = 0x80000005, 0x80000006, 0x80000007
COMMENT, TERMINATOR = 0x7FFFFFFE, 0xFFFFFFFF
SECTOR = 512
class UDIFReader(io.RawIOBase):
    """A seekable, read-only view of a compressed disk image.

    The last 512 bytes are a `koly` trailer naming an XML plist; the plist's
    `resource-fork` -> `blkx` is one base64 block table per partition; a block
    table is a run of 40-byte chunks saying where each span of output sectors
    comes from -- stored raw, deflated, bzip2'd, or not stored because it is
    all zero.
    """

    def __init__(self, stream, size):
        self.f = stream
        self.pos = 0
        self.f.seek(size - 512)
        koly = self.f.read(512)
        if koly[:4] != b"koly":
            raise SystemExit("BaseSystem.dmg has no koly trailer")
        xmloff, xmllen = struct.unpack(">QQ", koly[216:232])
        self.f.seek(xmloff)
        plist = plistlib.loads(self.f.read(xmllen))
        self.chunks = []
        for entry in plist["resource-fork"]["blkx"]:
            self.chunks += self._table(entry["Data"])
        self.chunks.sort(key=lambda c: c[1])
        self.size = max((c[1] + c[2]) for c in self.chunks) \
            if self.chunks else 0
        self._cache_i, self._cache = -1, b""

    @staticmethod
    def _table(blob):
        if blob[:4] != b"mish":
            return []
        start = struct.unpack(">Q", blob[8:16])[0]
        nchunks = struct.unpack(">I", blob[200:204])[0]
        out = []
        for i in range(nchunks):
            rec = blob[204 + 40 * i:244 + 40 * i]
            if len(rec) < 40:
                break
            kind = struct.unpack(">I", rec[0:4])[0]
            osec, ocount, soff, slen = struct.unpack(">QQQQ", rec[8:40])
            if kind in (COMMENT, TERMINATOR):
                continue
            out.append((kind, (start + osec) * SECTOR, ocount * SECTOR,
                        soff, slen))
        return out

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, off, whence=io.SEEK_SET):
        self.pos = (off if whence == io.SEEK_SET else
                    self.pos + off if whence == io.SEEK_CUR else
                    self.size + off)
        return self.pos

    def _find(self, off):
        lo, hi = 0, len(self.chunks) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            _k, out, length, _s, _l = self.chunks[mid]
            if off < out:
                hi = mid - 1
            elif off >= out + length:
                lo = mid + 1
            else:
                return mid
        return -1

    def _bytes(self, i):
        if i == self._cache_i:
            return self._cache
        kind, _out, length, src, srclen = self.chunks[i]
        if kind in (ZERO, IGNORE):
            data = b"\0" * length
        elif kind == RAW:
            self.f.seek(src)
            data = self.f.read(srclen)
        elif kind == ZLIB:
            self.f.seek(src)
            data = zlib.decompress(self.f.read(srclen))
        elif kind == BZ2:
            self.f.seek(src)
            data = bz2.decompress(self.f.read(srclen))
        elif kind == LZFSE:
            raise SystemExit(
                "this image uses LZFSE compression, which needs a decoder "
                "Python does not ship.  10.7 does not use it, so this is "
                "probably a later system than Lion.")
        else:
            raise SystemExit("unknown blkx chunk type 0x%08x" % kind)
        if len(data) < length:
            data += b"\0" * (length - len(data))
        self._cache_i, self._cache = i, data
        return data

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        out = bytearray()
        while n > 0 and self.pos < self.size:
            i = self._find(self.pos)
            if i < 0:                            # a hole between chunks
                out += b"\0" * n
                self.pos += n
                break
            _k, start, length, _s, _l = self.chunks[i]
            data = self._bytes(i)
            at = self.pos - start
            take = min(n, length - at)
            out += data[at:at + take]
            self.pos += take
            n -= take
        return bytes(out)

    def readinto(self, b):
        data = self.read(len(b))
        b[:len(data)] = data
        return len(data)


# ---- xar ----------------------------------------------------------------
def payload_offset(volume, entry):
    """-> (offset, length) of the package's Payload within the .pkg file, or
    (None, None) if it has none.

    `BaseSystemResources.pkg` has none: it is a manifest for what the
    installer restores out of the disk image, not content.
    """
    head = ForkReader(volume, entry).read(1 << 16)
    if head[:4] != b"xar!":
        raise SystemExit("%s is not a flat package (no xar header)" % entry[0])
    hsize, _ver, toc_c, _toc_u, _ck = struct.unpack(">HHQQI", head[4:28])
    toc = zlib.decompress(head[hsize:hsize + toc_c]).decode("utf-8", "replace")
    heap = hsize + toc_c
    for block in re.findall(r"<file\b.*?</file>", toc, re.S):
        if "<name>Payload</name>" not in block:
            continue
        off = re.search(r"<offset>(\d+)</offset>", block)
        length = re.search(r"<length>(\d+)</length>", block)
        if off and length:
            return heap + int(off.group(1)), int(length.group(1))
    return None, None


# ---- cpio ---------------------------------------------------------------

TRAILER = "TRAILER!!!"
def _exact(stream, n):
    buf = b""
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            raise EOFError("the archive ended early")
        buf += chunk
    return buf
def cpio_entries(stream):
    """Yield (name, mode, size, read) for each member, in order.

    One forward pass over a gzip stream, so `read` must be called before
    moving on -- there is no seeking back.
    """
    while True:
        magic = _exact(stream, 6)
        if magic == b"070707":                              # odc
            head = _exact(stream, 70)
            mode = int(head[12:18], 8)
            namesize = int(head[53:59], 8)
            size = int(head[59:70], 8)
            name = _exact(stream, namesize)[:-1].decode("utf-8", "replace")
            pad = 0
        elif magic in (b"070701", b"070702"):               # newc
            head = _exact(stream, 104)
            mode = int(head[8:16], 16)
            size = int(head[48:56], 16)
            namesize = int(head[88:96], 16)
            name = _exact(stream, namesize)[:-1].decode("utf-8", "replace")
            skip = (4 - ((110 + namesize) % 4)) % 4
            if skip:
                _exact(stream, skip)
            pad = (4 - (size % 4)) % 4
        else:
            raise SystemExit("not a cpio archive: magic %r" % magic)

        if name == TRAILER:
            return

        taken = [False]

        def read(n=size, _s=stream, _t=taken):
            _t[0] = True
            return _exact(_s, n) if n else b""

        yield name, mode, size, read

        if not taken[0] and size:
            _exact(stream, size)
        if pad:
            _exact(stream, pad)


# ---- writing ------------------------------------------------------------
def safe_join(root, rel):
    """Never let an archive or a catalogue decide where on disk to write."""
    dest = os.path.normpath(os.path.join(root, rel))
    if not (dest == root or dest.startswith(root + os.sep)):
        return None
    return dest
