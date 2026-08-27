# -*- coding: utf-8 -*-
"""Pull the speech engine out of your own Mac OS X 10.7 install image.

Nothing of Apple's ships with this project, so this is how you get an engine:
from a Lion installer you own.  Same posture as the sibling extractors --
ship the extractor, never the bits.

    py -3 tools/extract_lion.py "Lion.iso"
    py -3 tools/extract_lion.py Lion.iso --out "%APPDATA%\\nvda\\macintalk\\lion"
    py -3 tools/extract_lion.py Lion.iso --no-voices   (engine and Fred only)

Lion is i386-capable and the binaries on the disc are fat Mach-O files with an
i386 slice.  That is the slice this project runs; the voices and the
dictionary tables are architecture-neutral data and are the same bytes either
way.

## Lion is not shaped like Leopard

Leopard's DVD has a live filesystem: the engine, the dictionary and Fred can
be copied straight out of it.  **Lion's installer has nothing live.**  Its
HFS+ volume holds an installer app, a `BaseSystem.dmg`, and a `Packages`
folder, and the engine is split across the two in a way that is worth stating
exactly, because guessing it wrong is easy and each wrong guess looks
plausible:

    BaseSystemBinaries.pkg      MacinTalk, SpeechDictionary.framework,
                                SPSupport.framework, libstdc++.6.0.9,
                                libc++abi -- the *binaries*, fat, with i386
    BaseSystem.dmg              the dictionary's Resources -- TuplesEng,
                                PrefixDictionaryEng, CartLite, CartNames,
                                Homophones, PhonemeSymbols -- and Fred
    Essentials.pkg              every classic voice: Agnes, Albert, Bruce,
                                Victoria, and the novelty ones
    AdditionalSpeechVoices.pkg  Alex and Vicki, and nothing else

Three traps in that table, all of them measured rather than reasoned about:

* **`AdditionalSpeechVoices.pkg` misleads exactly as Leopard's did.**  Taking
  only the package that says "speech voices" gets Alex and loses everybody
  else.
* **`BaseSystemResources.pkg` has no `Payload` member at all.**  It is a
  manifest for what the installer restores out of the disk image, and its
  bill of materials cheerfully lists TuplesEng -- which reads exactly like
  "the file is in here", and is not.
* **The binaries inside `BaseSystem.dmg` are thin x86_64**, because Lion
  Recovery is 64-bit.  Its MacinTalk is 1023792 bytes, which is precisely the
  x86_64 slice of the 1989040-byte fat binary in the package: the right file,
  the wrong half.  So the image is read for data and never for code.

## What that costs

Reaching into `BaseSystem.dmg` needs two things Leopard never did, and both
are here:

* an HFS+ **extents overflow** reader, because the image is the one file on
  the volume with more than eight extents, and the rest live in a second
  B-tree;
* a **UDIF** decoder -- a `koly` trailer, an XML plist of `blkx` block
  tables, and zlib-compressed chunks -- with an Apple partition map inside
  the result, so the HFS+ reader runs twice, once nested in the other.

Nothing is unpacked to disk on the way.  The image is read through a seekable
view that decompresses the chunk under the read head, so a 1.4 GB image costs
the few megabytes the catalogue walk actually touches.

## Package format

A .pkg here is a **xar** archive whose `Payload` member is a gzip stream, and
inside that is **cpio**.  Not tar; `tarfile` rejects it.

## Why this repeats code from extract_leopard.py

Each extractor is a **single file a user downloads and runs**.  Making them
import a shared module would mean telling someone to fetch two files and keep
them together, and a missing import is an error message about Python at the
moment they are trying to get their screen reader talking.  The HFS+, xar and
cpio readers here are the same code, proved against the same discs.
"""
import argparse
import base64
import bz2
import gzip
import io
import os
import plistlib
import re
import struct
import sys
import zlib

#: HFS+ reserves catalogue node id 2 for the root folder.
ROOT_ID = 2

S_IFMT, S_IFREG, S_IFDIR, S_IFLNK = 0o170000, 0o100000, 0o040000, 0o120000

#: Windows will not name a file containing any of these.
ILLEGAL = set('<>:"|?*')

#: Where things land.  The driver looks for `Speech/Voices`,
#: `Speech/Synthesizers` and `SpeechDictionary.framework/Versions/A`, so these
#: names are not free choices -- see pantheraleopard.engine_paths().
BINARIES_PKG = "Packages/BaseSystemBinaries.pkg"
VOICE_PKGS = ["Packages/Essentials.pkg",
              "Packages/AdditionalSpeechVoices.pkg"]

#: A rule ending in "/" matches a whole subtree; one that does not is a single
#: file matched exactly.
ENGINE_RULES = [
    ("./System/Library/Speech/Synthesizers/MacinTalk.SpeechSynthesizer/",
     "Speech/Synthesizers/MacinTalk.SpeechSynthesizer/"),
    ("./System/Library/PrivateFrameworks/SpeechDictionary.framework/",
     "SpeechDictionary.framework/"),
    ("./System/Library/PrivateFrameworks/SPSupport.framework/",
     "SPSupport.framework/"),
    ("./usr/lib/libstdc++.6.0.9.dylib", "libstdc++.6.0.9.dylib"),
    ("./usr/lib/libc++abi.dylib", "libc++abi.dylib"),
]

VOICE_RULES = [("./System/Library/Speech/Voices/", "Speech/Voices/")]

#: Taken out of BaseSystem.dmg, where they are the only copy.
DMG_TREES = [
    ("System/Library/PrivateFrameworks/SpeechDictionary.framework/Versions/A"
     "/Resources", "SpeechDictionary.framework/Versions/A/Resources"),
    ("System/Library/Speech/Voices", "Speech/Voices"),
]

#: 10.7 introduced the multilingual "Compact" voices, driven by
#: `MultiLingual.SpeechSynthesizer`, which this project does not load.  They
#: are skipped by default: they carry no `VoiceDescription`, so the driver
#: cannot even name them, and a list of voices that cannot speak is worse than
#: a shorter list.
COMPACT = re.compile(r"[^/]*Compact\.SpeechVoice(/|$)")

#: From 10.7 the C++ runtime is two libraries: the ABI moved out of libstdc++
#: into libc++abi.  Without either one the engine will not load at all.
RUNTIME = ["libstdc++.6.0.9.dylib", "libc++abi.dylib"]

#: What the dictionary cannot work without, and what nothing but the disk
#: image has.  Checked by name at the end, because an engine that loads and
#: then cannot look a word up is a much worse failure than one that says so.
DICT_RESOURCES = ["TuplesEng", "PrefixDictionaryEng", "CartLiteEng",
                  "CartNamesEng", "HomophonesEng", "PhonemeSymbolsEng"]


# ---- Apple Partition Map ------------------------------------------------

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
ADC = 0x80000004
ZLIB, BZ2, LZFSE = 0x80000005, 0x80000006, 0x80000007
COMMENT, TERMINATOR = 0x7FFFFFFE, 0xFFFFFFFF
SECTOR = 512



def _adc(src, want):
    """Apple Data Compression, as used by older disk images.

    Not needed by any 10.4-10.7 installer Apple shipped -- those are zlib or
    bzip2 -- but a `.dmg` somebody made themselves years ago on a Mac may
    well be ADC, and until now the reader stopped at "unknown blkx chunk
    type" for one, which tells the person nothing they can act on.

    Three token shapes, distinguished by the top bits: a literal run, a long
    match with a two-byte distance, and a short match with a ten-bit one.
    The copy is byte at a time on purpose -- a run may overlap itself, which
    is how a repeated pattern is stored.
    """
    out = bytearray()
    i = 0
    while i < len(src) and len(out) < want:
        b = src[i]
        i += 1
        if b & 0x80:                                  # literal
            n = (b & 0x7F) + 1
            out += src[i:i + n]
            i += n
        elif b & 0x40:                                # long match
            n = (b & 0x3F) + 4
            if i + 1 >= len(src) + 1:
                break
            dist = ((src[i] << 8) | src[i + 1]) + 1
            i += 2
            for _ in range(n):
                out.append(out[-dist])
        else:                                         # short match
            n = ((b & 0x3F) >> 2) + 3
            if i >= len(src):
                break
            dist = (((b & 0x03) << 8) | src[i]) + 1
            i += 1
            for _ in range(n):
                out.append(out[-dist])
    return bytes(out)


class UDIFReader(io.RawIOBase):
    """A seekable, read-only view of a compressed disk image.

    The last 512 bytes are a `koly` trailer naming an XML plist; the plist's
    `resource-fork` -> `blkx` is one base64 block table per partition; a block
    table is a run of 40-byte chunks saying where each span of output sectors
    comes from -- stored raw, deflated, bzip2'd, or not stored because it is
    all zero.
    """

    def __init__(self, stream, size, what="this image"):
        self.f = stream
        self.pos = 0
        self.what = what
        self.f.seek(size - 512)
        koly = self.f.read(512)
        if koly[:4] != b"koly":
            raise SystemExit("%s has no koly trailer" % what)
        # **The data fork does not have to start at byte zero.**  Apple's own
        # download of Lion is an installer package with the disk image
        # appended: the file begins `xar!` and the real UDIF data starts
        # 12,994,304 bytes in.  Every source offset below is relative to
        # that, not to the file.  Ignoring it puts each read inside the
        # package, where zlib says "incorrect header check".  Retail DVDs
        # start at zero and are unaffected.  Reported by Gavin.
        self.fork = struct.unpack(">Q", koly[24:32])[0]
        xmloff, xmllen = struct.unpack(">QQ", koly[216:232])
        self.f.seek(xmloff)
        plist = plistlib.loads(self.f.read(xmllen))
        self.chunks = []
        for entry in plist["resource-fork"]["blkx"]:
            self.chunks += self._table(entry["Data"], self.fork)
        self.chunks.sort(key=lambda c: c[1])
        self.size = max((c[1] + c[2]) for c in self.chunks) \
            if self.chunks else 0
        self._cache_i, self._cache = -1, b""

    @staticmethod
    def _table(blob, fork=0):
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
                        soff + fork, slen))
        return out


def open_image(path, what=None):
    """-> (source, base) for whatever shape of image somebody hands us.

    A retail DVD is a raw image with an Apple partition map; the same DVD
    imaged by its owner is usually a compressed UDIF; and Apple's own
    download of Lion is an installer package with a compressed UDIF stuck on
    the end of it.  All three are legitimate and only the first used to work.
    """
    what = what or os.path.basename(path)
    size = os.path.getsize(path)
    f = open(path, "rb")
    try:
        def read_at(off, n):
            f.seek(off)
            return f.read(n)
        try:
            base = find_hfs(read_at, size, what)
        except BaseException:
            pass
        else:
            f.close()
            return path, base
        if size < 512:
            raise SystemExit("%s is too small to be a disc image" % what)
        f.seek(size - 512)
        if f.read(4) != b"koly":
            raise SystemExit(
                "%s is not a disc image this can read: it has no Apple "
                "partition map, no HFS+ volume and no UDIF trailer." % what)
        udif = UDIFReader(f, size, what)
        return udif, find_hfs(
            lambda o, n: (udif.seek(o), udif.read(n))[1], udif.size, what)
    except BaseException:
        f.close()
        raise

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
        elif kind == ADC:
            self.f.seek(src)
            data = _adc(self.f.read(srclen), length)
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


def route(name, rules):
    """-> the output-relative path for a cpio member, or None to skip it."""
    for src, dst in rules:
        if src.endswith("/"):
            if name.startswith(src):
                return dst + name[len(src):]
        elif name == src:
            return dst
    return None


def want(rel, counts, compact):
    """-> True if this output path should be written."""
    if not compact and COMPACT.search(rel):
        counts["compact"].add(COMPACT.search(rel).group(0).rstrip("/"))
        return False
    if ILLEGAL & set(rel):
        counts["skipped"].append(rel)
        return False
    return True


def extract_package(volume, entry, outdir, rules, counts, label, compact):
    """Stream one package once, writing everything the rules name."""
    offset, length = payload_offset(volume, entry)
    if offset is None:
        print("  (%s carries no payload; it is a manifest)" % label)
        return
    print("  reading %s (%.0f MB, streamed -- this is the slow part)"
          % (label, length / 1e6))
    reader = io.BufferedReader(ForkReader(volume, entry, skip=offset), 1 << 20)
    with gzip.GzipFile(fileobj=reader) as gz:
        for name, mode, size, read in cpio_entries(gz):
            rel = route(name, rules)
            if rel is None or not want(rel, counts, compact):
                continue
            dest = safe_join(outdir, rel)
            if dest is None:
                continue
            kind = mode & S_IFMT
            if kind == S_IFDIR:
                os.makedirs(dest, exist_ok=True)
                continue
            if kind != S_IFREG:
                # Symlinks are a path in the data fork.  Windows has no use
                # for them; the runtime alias is written by name instead.
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as out:
                if size:
                    out.write(read())
            counts["files"] += 1
            counts["bytes"] += size
            if size > 50 * 1024 * 1024:
                print("    %s (%.0f MB)" % (rel.split("/")[-1], size / 1e6))


def copy_tree(volume, entry, outdir, rel, counts, compact):
    """Copy one folder out of a volume, recursively."""
    for child in volume.children(entry):
        name, isdir, _id, size, ext, mode = child
        sub = rel + "/" + name
        if not want(sub, counts, compact):
            continue
        dest = safe_join(outdir, sub)
        if dest is None:
            continue
        if isdir:
            os.makedirs(dest, exist_ok=True)
            copy_tree(volume, child, outdir, sub, counts, compact)
            continue
        if (mode & S_IFMT) == S_IFLNK or ext is None:
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as out:
            out.write(volume.read(child))
        counts["files"] += 1
        counts["bytes"] += size


def open_basesystem(volume):
    """-> a Volume over the HFS+ inside BaseSystem.dmg."""
    dmg = volume.entry("BaseSystem.dmg")
    if dmg is None:
        raise SystemExit(
            "BaseSystem.dmg is not on this image.\nIt is the only place the "
            "dictionary's tables and Fred exist, so an installer\nwithout it "
            "cannot be used.")
    if dmg[4] is None:
        raise SystemExit(
            "BaseSystem.dmg is split across more extents than even the "
            "overflow tree lists.\nThe image may be damaged.")
    udif = UDIFReader(ExtentStream(volume, dmg), dmg[3])
    inner = find_hfs(lambda o, n: (udif.seek(o), udif.read(n))[1],
                     udif.size, "BaseSystem.dmg")
    return Volume(udif, inner)


def default_out():
    """The shared folder every Macintosh engine writes into."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return os.path.join(appdata, "nvda", "macintalk", "lion")
    return os.path.join(os.getcwd(), "macintalk", "lion")


def main():
    ap = argparse.ArgumentParser(
        description="Extract Lion's speech engine from your own install image.")
    ap.add_argument("image", help="the Mac OS X 10.7 installer image (.iso)")
    ap.add_argument("--out", default=default_out(),
                    help="where to write it "
                         "(default: %%APPDATA%%\\nvda\\macintalk\\lion)")
    ap.add_argument("--no-voices", action="store_true",
                    help="engine, dictionary and Fred only -- skips the two "
                         "big packages, and so skips Alex")
    ap.add_argument("--compact", action="store_true",
                    help="also write the multilingual *Compact voices, which "
                         "need a synthesizer this project does not load")
    args = ap.parse_args()

    if not os.path.isfile(args.image):
        raise SystemExit("no such file: %s" % args.image)

    source, base = open_image(args.image)
    print("HFS+ partition at offset %d%s"
          % (base, "" if source is args.image else " (inside a disk image)"))
    volume = Volume(source, base)

    outdir = os.path.normpath(os.path.abspath(args.out))
    os.makedirs(outdir, exist_ok=True)
    counts = {"files": 0, "bytes": 0, "skipped": [], "compact": set()}

    binaries = volume.entry(BINARIES_PKG)
    if binaries is None:
        raise SystemExit(
            "%s is not on this image.\nIs it really a Mac OS X 10.7 "
            "installer?  Lion keeps its engine in packages, so an image\n"
            "without this one has nothing to take." % BINARIES_PKG)
    extract_package(volume, binaries, outdir, ENGINE_RULES, counts,
                    "BaseSystemBinaries.pkg", args.compact)

    # The versioned runtime is what the loader prefers, but a Mac reaches it
    # through a symlink and this writes no symlinks -- so write the bytes
    # under both names and let the loader find whichever it looks for first.
    real = os.path.join(outdir, "libstdc++.6.0.9.dylib")
    if os.path.isfile(real):
        with open(real, "rb") as f:
            data = f.read()
        with open(os.path.join(outdir, "libstdc++.6.dylib"), "wb") as out:
            out.write(data)
        counts["files"] += 1
        counts["bytes"] += len(data)

    missing = [n for n in RUNTIME
               if not os.path.isfile(os.path.join(outdir, n))]
    if missing:
        raise SystemExit(
            "did not find %s in the image.\n10.7 needs both: the C++ ABI "
            "moved out of libstdc++ into libc++abi, and\nwithout either one "
            "the engine will not load at all." % " and ".join(missing))

    # The dictionary's tables and Fred live only in the disk image.
    print("  reading BaseSystem.dmg (compressed; decoded as it is read)")
    inner = open_basesystem(volume)
    for src, rel in DMG_TREES:
        entry = inner.entry(src)
        if entry is None:
            raise SystemExit("%s is not inside BaseSystem.dmg" % src)
        os.makedirs(os.path.join(outdir, rel.replace("/", os.sep)),
                    exist_ok=True)
        copy_tree(inner, entry, outdir, rel, counts, args.compact)

    if not args.no_voices:
        for path in VOICE_PKGS:
            pkg = volume.entry(path)
            label = path.rsplit("/", 1)[-1]
            if pkg is None:
                print("  (no %s on this image)" % label)
                continue
            extract_package(volume, pkg, outdir, VOICE_RULES, counts, label,
                            args.compact)

    res = os.path.join(outdir, "SpeechDictionary.framework", "Versions", "A",
                       "Resources")
    absent = [n for n in DICT_RESOURCES
              if not os.path.isfile(os.path.join(res, n))]
    if absent:
        raise SystemExit(
            "the dictionary is missing %s.\nThe engine would load and then "
            "be unable to look a word up, which is a much\nworse failure than "
            "this one." % ", ".join(absent))

    voices = os.path.join(outdir, "Speech", "Voices")
    found = sorted(n for n in os.listdir(voices)
                   if n.endswith(".SpeechVoice")) if os.path.isdir(voices) \
        else []
    print("\nwrote %d files, %.0f MB, to %s"
          % (counts["files"], counts["bytes"] / 1e6, outdir))
    print("%d voice%s: %s" % (len(found), "" if len(found) == 1 else "s",
                              ", ".join(n[:-12] for n in found) or "none"))
    if counts["compact"]:
        print("skipped %d multilingual Compact voice(s); --compact takes them "
              "too" % len(counts["compact"]))
    if counts["skipped"]:
        print("skipped %d file(s) Windows cannot name" % len(counts["skipped"]))
    print("\nNothing of Apple's is redistributed by this project.  What is now "
          "in that folder\ncame from your own disc and stays on your own "
          "machine.")


if __name__ == "__main__":
    main()
