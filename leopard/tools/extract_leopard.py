# -*- coding: utf-8 -*-
"""Pull the speech engine out of your own Mac OS X 10.5 install image.

Nothing of Apple's ships with this project, so this is how you get an engine:
from a Leopard install DVD you own.  Same posture as the sibling add-ons --
ship the extractor, never the bits.

    py -3 tools/extract_leopard.py "Mac OS X Leopard Install DVD.iso"
    py -3 tools/extract_leopard.py leopard.iso --out "%APPDATA%\\nvda\\leopard-data"
    py -3 tools/extract_leopard.py leopard.iso --no-voices    (engine + Fred only)

Leopard is an Intel-capable release, and the engine on the disc is a fat
Mach-O containing an i386 slice.  That is the slice this project runs; the
voices are architecture-neutral data.

## Why this is not four lines of 7-Zip

The DVD is a hybrid image.  A plain listing shows only the Boot Camp
documentation, because that is the ISO9660 filesystem; everything else lives in
an HFS+ partition described by an Apple Partition Map:

    Apple      Apple_partition_map     offset          2048
    Macintosh  Apple_Driver_ATAPI      offset         32768
    Mac_OS_X   Apple_HFS               offset     421294080   <- here
                                       size      7634907136

7-Zip can list that map, and it can read an HFS volume that is a file on its
own -- but it cannot be pointed at a partition *inside* another file, and it
will not read HFS from a pipe.  The alternative was to copy 7.6 GB out to a
temporary file first.  So this reads the HFS+ catalogue directly instead, at
the offset the partition map gives, and touches only the bytes it needs.

The catalogue is a B-tree.  Rather than implement HFS+'s case-insensitive
Unicode key ordering -- which is genuinely fiddly and easy to get subtly wrong
-- this walks every leaf node through the `fLink` chain and builds the tree in
memory.  There are about 49,000 records and it takes under a second.

## Where the pieces are

The engine, the dictionary and **Fred** are live on the disc, so the smallest
useful extraction needs no package handling at all:

    System/Library/Speech/Synthesizers/MacinTalk.SpeechSynthesizer/
    System/Library/Speech/Voices/Fred.SpeechVoice/
    System/Library/PrivateFrameworks/SpeechDictionary.framework/
    System/Library/PrivateFrameworks/SPSupport.framework/
    usr/lib/libstdc++.6.0.4.dylib

Every other voice comes out of a package, and they are split across two of
them in a way the names actively mislead about:

    AdditionalSpeechVoices.pkg   Alex and Vicki only -- but 707 MB of them
    Essentials.pkg               the twenty-two classic MacinTalk 3 voices

Take only the first, which its name invites, and you get Alex and lose Agnes,
Bruce, Victoria and all the singing voices.

Leopard uses *flat* packages, unlike Tiger's bundles: a .pkg is a **xar**
archive whose `Payload` member is a gzip stream, and inside that is **cpio**,
in the old `070707` portable ASCII format.  Not tar; `tarfile` rejects it.
"""
import argparse
import gzip
import io
import os
import re
import struct
import sys
import zlib

#: HFS+ reserves catalogue node id 2 for the root folder.
ROOT_ID = 2

S_IFMT, S_IFREG, S_IFDIR, S_IFLNK = 0o170000, 0o100000, 0o040000, 0o120000

#: Taken from the live filesystem, and where each lands in the output.
LIVE = [
    ("System/Library/Speech/Synthesizers", "Speech/Synthesizers"),
    ("System/Library/Speech/Voices", "Speech/Voices"),
    ("System/Library/PrivateFrameworks/SpeechDictionary.framework",
     "SpeechDictionary.framework"),
    ("System/Library/PrivateFrameworks/SPSupport.framework",
     "SPSupport.framework"),
]

#: Leopard's engine links against these and the loader maps Apple's own copy
#: as a third image -- GCC 4.0.1's basic_string layout has to match exactly,
#: so it cannot be reimplemented.
LIBSTDCXX = "usr/lib/libstdc++.6.0.4.dylib"

#: The voices are split across two packages, and the split is not obvious:
#: AdditionalSpeechVoices.pkg holds only the two big concatenative ones,
#: Alex and Vicki, while the twenty-two classic MacinTalk 3 voices are in
#: Essentials.pkg.  Extracting only the first, which its name invites, gets
#: Alex and leaves out Agnes, Bruce, Victoria and the singing voices.
#:
#: Found by reading each package's bill of materials rather than by
#: streaming all of them: a Bom is a small bzip2 member in the same xar, and
#: the voice names are plain strings inside it.
VOICE_PKGS = ["System/Installation/Packages/AdditionalSpeechVoices.pkg",
              "System/Installation/Packages/Essentials.pkg"]

#: Inside the package payload, this prefix is stripped and the rest kept.
VOICE_PREFIX = "./System/Library/Speech/Voices/"

#: Windows will not name a file containing any of these.  Nothing wanted has
#: one, but an archive from another platform is entitled to.
ILLEGAL = set('<>:"|?*')


# ---- Apple Partition Map ------------------------------------------------

def hfs_partition(path):
    """-> (offset, size) of the Apple_HFS partition, or (0, size) if the file
    is already a bare HFS volume."""
    with open(path, "rb") as f:
        block0 = f.read(16)
        if len(block0) < 8:
            raise SystemExit("%s is too small to be a disc image" % path)
        if block0[:2] != b"ER":
            # Possibly a raw HFS+ volume: its header sits 1024 bytes in.
            f.seek(1024)
            if f.read(2) in (b"H+", b"HX"):
                return 0, os.path.getsize(path)
            raise SystemExit(
                "%s has no Apple partition map and is not an HFS volume.\n"
                "This wants the Leopard *install DVD* image." % path)
        stride = struct.unpack(">H", block0[2:4])[0] or 512
        f.seek(stride)
        entry = f.read(512)
        if entry[:2] != b"PM":
            raise SystemExit("the partition map is not where block zero says")
        count = struct.unpack(">I", entry[4:8])[0]
        for i in range(count):
            f.seek(stride * (1 + i))
            e = f.read(512)
            if e[:2] != b"PM":
                break
            start, size = struct.unpack(">II", e[8:16])
            kind = e[48:80].split(b"\0")[0].decode("ascii", "replace")
            if kind == "Apple_HFS":
                return start * stride, size * stride
    raise SystemExit("no Apple_HFS partition in %s" % path)


# ---- HFS+ ---------------------------------------------------------------

class Volume(object):
    """Just enough HFS+ to find files and read them."""

    def __init__(self, path, base):
        self.f = open(path, "rb")
        self.base = base
        self.f.seek(base + 1024)
        vh = self.f.read(512)
        if vh[:2] not in (b"H+", b"HX"):
            raise SystemExit("no HFS+ volume header at offset %d" % base)
        self.bs = struct.unpack(">I", vh[40:44])[0]
        self.kids = {}
        self._read_catalog(vh)

    def _fork(self, vh, off):
        logical = struct.unpack(">Q", vh[off:off + 8])[0]
        ext = [struct.unpack(">II", vh[off + 16 + 8 * i:off + 24 + 8 * i])
               for i in range(8)]
        return logical, [e for e in ext if e[1]]

    def _read_catalog(self, vh):
        logical, extents = self._fork(vh, 272)
        buf = bytearray()
        for start, count in extents:
            self.f.seek(self.base + start * self.bs)
            buf += self.f.read(count * self.bs)
        cat = bytes(buf[:logical])
        if len(cat) < logical:
            raise SystemExit("the catalogue is truncated -- image incomplete?")
        node_size = struct.unpack(">H", cat[32:34])[0]
        node = struct.unpack(">I", cat[24:28])[0]        # first leaf
        while node:
            nd = cat[node * node_size:(node + 1) * node_size]
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
                self._record(nd[a:b])
            node = flink

    def _record(self, rec):
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
                (name, True, node_id, 0, [], 0))
        elif kind == 2:                                  # file
            fork = data[88:168]
            size = struct.unpack(">Q", fork[:8])[0]
            blocks = struct.unpack(">I", fork[12:16])[0]
            ext = [struct.unpack(">II", fork[16 + 8 * j:24 + 8 * j])
                   for j in range(8)]
            ext = [e for e in ext if e[1]]
            # HFSPlusBSDInfo starts at 32; fileMode is ten bytes into it,
            # after ownerID, groupID, adminFlags and ownerFlags.
            mode = struct.unpack(">H", data[42:44])[0]
            if sum(c for _s, c in ext) < blocks:
                # More than eight extents; the rest live in the extents
                # overflow tree, which nothing here needs to read.  Say so
                # rather than write a file that is quietly short.
                ext = None
            self.kids.setdefault(parent, []).append(
                (name, False, node_id, size, ext, mode))

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
            raise SystemExit("%s is split across more than eight extents, "
                             "which this reader does not follow" % entry[0])
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


# ---- xar ----------------------------------------------------------------

def payload_offset(volume, entry):
    """-> byte offset of the package's Payload within the .pkg file.

    A flat package is a xar archive: a 28-byte header, a zlib table of
    contents, then the heap.  The Payload is stored uncompressed at the xar
    layer -- it is already a gzip stream -- so its offset is all that is
    wanted.
    """
    reader = ForkReader(volume, entry)
    head = reader.read(1 << 16)
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
    raise SystemExit("no Payload in %s" % entry[0])


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


def copy_tree(volume, entry, outdir, rel, counts):
    """Copy one live folder out of the volume, recursively."""
    for child in volume.children(entry):
        name, isdir, _id, size, ext, mode = child
        if ILLEGAL & set(name):
            counts["skipped"].append(name)
            continue
        dest = safe_join(outdir, os.path.join(rel, name))
        if dest is None:
            continue
        if isdir:
            os.makedirs(dest, exist_ok=True)
            copy_tree(volume, child, outdir, os.path.join(rel, name), counts)
            continue
        if (mode & S_IFMT) == S_IFLNK:
            # Symlinks are a path in the data fork.  Windows has no use for
            # them and nothing here is addressed through one.
            continue
        if ext is None:
            counts["skipped"].append(name)
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as out:
            out.write(volume.read(child))
        counts["files"] += 1
        counts["bytes"] += size


def extract_voices(volume, entry, outdir, counts, label):
    """Stream one package and write only the voices out of it."""
    offset, length = payload_offset(volume, entry)
    print("  reading %s (%.0f MB, streamed -- this is the slow part)"
          % (label, length / 1e6))
    reader = io.BufferedReader(ForkReader(volume, entry, skip=offset), 1 << 20)
    with gzip.GzipFile(fileobj=reader) as gz:
        for name, mode, size, read in cpio_entries(gz):
            if not name.startswith(VOICE_PREFIX):
                continue
            rel = name[len(VOICE_PREFIX):]
            if not rel or ILLEGAL & set(rel):
                continue
            dest = safe_join(outdir, os.path.join("Speech", "Voices", rel))
            if dest is None:
                continue
            kind = mode & S_IFMT
            if kind == S_IFDIR:
                os.makedirs(dest, exist_ok=True)
                continue
            if kind != S_IFREG:
                continue
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            remaining = size
            with open(dest, "wb") as out:
                if size:
                    out.write(read())
            counts["files"] += 1
            counts["bytes"] += size
            if size > 50 * 1024 * 1024:
                print("    %s (%.0f MB)" % (rel.split("/")[0], size / 1e6))


def default_out():
    appdata = os.environ.get("APPDATA")
    if appdata:
        return os.path.join(appdata, "nvda", "leopard-data")
    return os.path.join(os.getcwd(), "leopard-data")


def main():
    ap = argparse.ArgumentParser(
        description="Extract Leopard's speech engine from your own install DVD.")
    ap.add_argument("image", help="the Leopard install DVD image (.iso)")
    ap.add_argument("--out", default=default_out(),
                    help="where to write it (default: %%APPDATA%%\\nvda\\leopard-data)")
    ap.add_argument("--no-voices", action="store_true",
                    help="engine, dictionary and Fred only -- skips the 707 MB "
                         "package, and so skips Alex")
    args = ap.parse_args()

    if not os.path.isfile(args.image):
        raise SystemExit("no such file: %s" % args.image)

    base, size = hfs_partition(args.image)
    print("HFS+ partition at offset %d (%.1f GB)" % (base, size / 1e9))
    volume = Volume(args.image, base)

    outdir = os.path.normpath(os.path.abspath(args.out))
    os.makedirs(outdir, exist_ok=True)
    counts = {"files": 0, "bytes": 0, "skipped": []}

    for src, rel in LIVE:
        entry = volume.entry(src)
        if entry is None:
            raise SystemExit(
                "%s is not on this image.\nIs it really a Mac OS X 10.5 "
                "install DVD?" % src)
        print("  %s" % src)
        os.makedirs(os.path.join(outdir, rel), exist_ok=True)
        copy_tree(volume, entry, outdir, rel, counts)

    lib = volume.entry(LIBSTDCXX)
    if lib is None:
        raise SystemExit("%s is not on this image" % LIBSTDCXX)
    data = volume.read(lib)
    for name in ("libstdc++.6.0.4.dylib", "libstdc++.6.dylib"):
        # Both names, because the real one is versioned and the other is a
        # symlink on a Mac.  The loader accepts either; writing both means it
        # does not matter which it looks for first.
        with open(os.path.join(outdir, name), "wb") as out:
            out.write(data)
        counts["files"] += 1
        counts["bytes"] += len(data)
    print("  %s" % LIBSTDCXX)

    if not args.no_voices:
        for path in VOICE_PKGS:
            pkg = volume.entry(path)
            label = path.rsplit("/", 1)[-1]
            if pkg is None:
                print("  (no %s on this image)" % label)
                continue
            extract_voices(volume, pkg, outdir, counts, label)

    voices = os.path.join(outdir, "Speech", "Voices")
    found = sorted(n for n in os.listdir(voices)
                   if n.endswith(".SpeechVoice")) if os.path.isdir(voices) else []
    print("\nwrote %d files, %.0f MB, to %s" %
          (counts["files"], counts["bytes"] / 1e6, outdir))
    print("%d voice%s: %s" % (len(found), "" if len(found) == 1 else "s",
                              ", ".join(n[:-12] for n in found) or "none"))
    if counts["skipped"]:
        print("skipped %d file(s) Windows cannot name" % len(counts["skipped"]))
    print("\nNothing of Apple's is redistributed by this project.  What is now "
          "in that folder\ncame from your own disc and stays on your own "
          "machine.")


if __name__ == "__main__":
    main()
