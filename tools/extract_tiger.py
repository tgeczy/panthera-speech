# -*- coding: utf-8 -*-
"""Pull the speech engine out of your own Mac OS X 10.4 install image.

Nothing of Apple's ships with this project, so this is how you get an engine:
from a Tiger install disc you own.  It is the same posture as the sibling ROM
add-on -- ship the extractor, never the bits.

    py -3 tools/extract_tiger.py "Mac OS X 10.4 Tiger.iso"
    py -3 tools/extract_tiger.py tiger.dmg --out "%APPDATA%\\nvda\\tigerspeech-data"

You need an **Intel** Tiger image.  The PowerPC discs contain the same voices,
which are architecture-neutral data, but their engine is PowerPC code that this
host cannot run.

Everything lives in one package on the disc:

    <image>
     +- System/Installation/Packages/Essentials.pkg
         +- Contents/Archive.pax.gz          (~307 MB)
             +- Archive.pax                  (~790 MB)
                 +- ./System/Library/Speech/                        engine, voices
                 +- ./System/Library/PrivateFrameworks/
                        SpeechDictionary.framework/                 the 2.1 MB dictionary

7-Zip does the first step, because Python cannot read an ISO or a DMG.  The
rest is done here, streaming, so the 790 MB is never written out and only the
few files we want land on disk.

Despite the name, `Archive.pax` is **cpio**, not tar -- it begins `070707`,
the old portable ASCII format.  `tarfile` rejects it outright, and Python has
no cpio module, so there is a small reader below.  It is about forty lines and
saves depending on anything further.
"""
import argparse
import gzip
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile

#: What we take, and where it goes.  The engine and the dictionary sit in
#: different places on a real system; the add-on wants them side by side.
#:
#: Deliberately narrow.  `System/Library/Speech` also holds `Recognizers`,
#: which is speech *recognition* -- not wanted, several megabytes, and it
#: contains a file called "What Day Is It?" that Windows cannot name.
WANTED = [
    ("System/Library/Speech/Synthesizers/", "Speech/Synthesizers/"),
    ("System/Library/Speech/Voices/", "Speech/Voices/"),
    ("System/Library/PrivateFrameworks/SpeechDictionary.framework/",
     "SpeechDictionary.framework/"),
    ("System/Library/PrivateFrameworks/SPSupport.framework/",
     "SPSupport.framework/"),
]

#: Windows will not name a file containing any of these.  Nothing we need has
#: one, but an archive from another platform is entitled to, and failing the
#: whole extraction over an unwanted file would be silly.
ILLEGAL = set('<>:"|?*') | {chr(c) for c in range(32)}

SEVENZIP_GUESSES = [
    r"C:\Program Files\7-Zip\7z.exe",
    r"C:\Program Files (x86)\7-Zip\7z.exe",
]


def find_7zip():
    exe = shutil.which("7z") or shutil.which("7za")
    if exe:
        return exe
    for guess in SEVENZIP_GUESSES:
        if os.path.isfile(guess):
            return guess
    raise SystemExit(
        "7-Zip is required to read an ISO or DMG, and was not found.\n"
        "Install it from https://www.7-zip.org/ and try again, or put 7z.exe\n"
        "on your PATH.")


#: Things that are a filesystem rather than a file.  A retail DMG -- and a
#: hybrid installer ISO -- carries a small ISO9660 partition holding the BIOS
#: boot chain and a couple of text files, with the actual install disc in a
#: second, HFS+ partition.  7-Zip shows only the outer layer, so anything that
#: looks like a partition has to be opened in turn.
PARTITION_EXT = (".hfs", ".hfsx", ".img", ".iso", ".dmg", ".part", ".apfs")


def list_entries(sevenzip, image):
    """-> [(path, size), ...] for one layer of an image."""
    out = subprocess.run([sevenzip, "l", "-slt", image],
                         capture_output=True, text=True, errors="replace")
    entries, path, size = [], None, 0
    for line in out.stdout.splitlines():
        if line.startswith("Path = "):
            path = line[7:].strip()
            size = 0
        elif line.startswith("Size = ") and path:
            try:
                size = int(line[7:].strip())
            except ValueError:
                size = 0
        elif not line.strip() and path:
            entries.append((path, size))
            path = None
    if path:
        entries.append((path, size))
    return entries


def pick_paxes(entries):
    """-> the installer packages worth opening, in order.

    **The speech files are split across two of them.**  `Essentials.pkg` holds
    the engine, the dictionary and twenty-two voices; `AdditionalSpeechVoices
    .pkg` holds Vicki, whose AAC sample bank is 29 MB and evidently did not fit
    the budget for the main package.  Taking only the first yields 22 voices
    instead of 23 -- which looks exactly like a complete extraction.

    Matched on the package name to keep this cheap: opening all thirty-odd
    archives to find out would mean streaming several gigabytes.
    """
    out = []
    for p, _s in entries:
        if not p.lower().endswith("archive.pax.gz"):
            continue
        parts = [x.lower() for x in p.replace("\\", "/").split("/")]
        pkg = next((x for x in parts if x.endswith(".pkg")), "")
        # `Essentials.pkg` exactly: a substring test also catches
        # AdditionalEssentials.pkg, which holds nothing we want.
        if pkg == "essentials.pkg":
            out.insert(0, p)
        elif "speech" in pkg or "voice" in pkg:
            out.append(p)
    return out


def pick_partitions(entries, image):
    """-> nested filesystems, largest first.

    Largest first because the install content is the big one; the boot
    partition is a rounding error beside it.
    """
    me = os.path.basename(image).lower()
    parts = [(p, s) for p, s in entries
             if p.lower().endswith(PARTITION_EXT) and p.lower() != me]
    parts.sort(key=lambda ps: -ps[1])
    return [p for p, _s in parts]


def extract_one(sevenzip, image, inner, workdir):
    """Pull a single member out of an image.  -> its path on disk."""
    r = subprocess.run([sevenzip, "e", image, "-o" + workdir, "-y", inner],
                       capture_output=True, text=True, errors="replace")
    got = os.path.join(workdir, os.path.basename(inner))
    if r.returncode != 0 or not os.path.isfile(got):
        sys.stderr.write(r.stdout[-2000:] + r.stderr[-2000:])
        return None
    return got


def fetch_paxes(sevenzip, image, workdir, depth=0, indent="  "):
    """Find and extract every speech package, descending into partitions.

    -> [(label, path on disk), ...], empty if this image holds none.
    """
    entries = list_entries(sevenzip, image)
    inners = pick_paxes(entries)
    if inners:
        got = []
        for i, inner in enumerate(inners):
            label = next((x for x in inner.replace("\\", "/").split("/")
                          if x.lower().endswith(".pkg")), inner)
            print("%sfound %s" % (indent, label))
            # 7-Zip writes by basename, and every one of these is called
            # Archive.pax.gz, so give each its own directory.
            sub = os.path.join(workdir, "pkg%d" % i)
            os.makedirs(sub, exist_ok=True)
            path = extract_one(sevenzip, image, inner, sub)
            if path:
                got.append((label, path))
        return got

    if depth >= 2:
        return []
    for part in pick_partitions(entries, image):
        print("%sno installer packages here; opening partition %s"
              % (indent, part))
        sub = extract_one(sevenzip, image, part, workdir)
        if not sub:
            continue
        got = fetch_paxes(sevenzip, sub, workdir, depth + 1, indent + "  ")
        if got:
            return got
        # A partition that did not contain them can be large; do not keep it.
        try:
            os.remove(sub)
        except OSError:
            pass
    return []


def _target(name):
    """-> where a pax member should land, or None if we do not want it."""
    clean = name.lstrip("./")
    for prefix, dest in WANTED:
        if clean.startswith(prefix):
            return dest + clean[len(prefix):]
    return None


#: cpio, the two formats Apple's installers use.
#: "odc" is fixed-width octal ASCII with no padding; "newc" is hex and pads
#: names and data to four bytes.
S_IFMT, S_IFDIR, S_IFREG = 0o170000, 0o040000, 0o100000
TRAILER = "TRAILER!!!"


def _read_exactly(stream, n):
    buf = b""
    while len(buf) < n:
        chunk = stream.read(n - len(buf))
        if not chunk:
            raise EOFError("archive ended early")
        buf += chunk
    return buf


def cpio_entries(stream):
    """Yield (name, mode, size, read) for each member, in order.

    `read` must be called before advancing to the next entry -- this is a
    single forward pass over a gzip stream, so there is no seeking back.
    """
    while True:
        magic = _read_exactly(stream, 6)
        if magic == b"070707":                       # odc
            head = _read_exactly(stream, 70)
            fields = [int(head[a:b], 8) for a, b in
                      ((12, 18), (53, 59), (59, 70))]   # mode, namesize, size
            mode, namesize, size = fields
            name = _read_exactly(stream, namesize)[:-1].decode("utf-8",
                                                               "replace")
            pad = 0
        elif magic in (b"070701", b"070702"):        # newc
            head = _read_exactly(stream, 104)
            mode = int(head[8:16], 16)
            size = int(head[48:56], 16)
            namesize = int(head[88:96], 16)
            name = _read_exactly(stream, namesize)[:-1].decode("utf-8",
                                                               "replace")
            skip = (4 - ((110 + namesize) % 4)) % 4
            if skip:
                _read_exactly(stream, skip)
            pad = (4 - (size % 4)) % 4
        else:
            raise SystemExit("not a cpio archive: magic %r" % magic)

        if name == TRAILER:
            return

        consumed = [False]

        def read(n=size, _s=stream, _c=consumed):
            _c[0] = True
            return _read_exactly(_s, n) if n else b""

        yield name, mode, size, read

        if not consumed[0] and size:
            _read_exactly(stream, size)
        if pad:
            _read_exactly(stream, pad)


def unpack(paxgz, outdir):
    """Stream the archive and write only the speech files.  -> count written."""
    written, skipped = 0, []
    root = os.path.normpath(outdir)
    print("reading the archive (about 790 MB, streamed) ...")
    with gzip.open(paxgz, "rb") as gz:
        for name, mode, size, read in cpio_entries(gz):
            rel = _target(name)
            if rel is None:
                continue
            if ILLEGAL & set(rel):
                skipped.append(rel)
                continue
            # Never let an archive decide where on the disk to write.
            dest = os.path.normpath(os.path.join(outdir, rel))
            if not dest.startswith(root + os.sep):
                continue
            kind = mode & S_IFMT
            if kind == S_IFDIR:
                os.makedirs(dest, exist_ok=True)
                continue
            if kind != S_IFREG:
                continue      # symlinks: Windows cannot, and we do not need
                              # them -- Versions/A is addressed explicitly
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(read())
            written += 1
            if written % 25 == 0:
                print("  %d files ..." % written)
    # Say what was dropped rather than letting it look like a clean run.
    for rel in skipped:
        print("  skipped (illegal name on Windows): %s" % rel)
    return written


def read_voices(voicesdir):
    """-> [(bundle, displayName, engine), ...].

    Deliberately a copy of the driver's reader rather than an import: this
    script has to run outside NVDA, and importing the driver drags in `nvwave`.
    The offsets are the ones that matter -- creator OSType at +4, and the name
    is a Str63 at **+16**, because a `version` long sits between the VoiceSpec
    and the name.
    """
    out = []
    try:
        entries = sorted(os.listdir(voicesdir))
    except OSError:
        return out
    for entry in entries:
        if not entry.endswith(".SpeechVoice"):
            continue
        desc = os.path.join(voicesdir, entry, "Contents", "Resources",
                            "VoiceDescription")
        try:
            with open(desc, "rb") as f:
                head = f.read(80)
        except OSError:
            continue
        if len(head) < 80:
            continue
        engine = head[4:8].decode("latin-1")
        nlen = head[16]
        name = head[17:17 + nlen].decode("mac-roman", "replace")
        out.append((entry[:-len(".SpeechVoice")], name or entry, engine))
    return out


def verify(outdir):
    """Check what came out, the same way the driver will."""
    mt = os.path.join(outdir, "Speech", "Synthesizers",
                      "MacinTalk.SpeechSynthesizer", "Contents", "MacOS",
                      "MacinTalk")
    sd = os.path.join(outdir, "SpeechDictionary.framework", "Versions", "A",
                      "SpeechDictionary")
    voicesdir = os.path.join(outdir, "Speech", "Voices")
    ok = True
    for label, path in (("MacinTalk", mt), ("SpeechDictionary", sd)):
        good = os.path.isfile(path)
        print("  %-18s %s" % (label, "ok" if good else "MISSING"))
        ok = ok and good

    if ok:
        # An Intel image is required: the PowerPC engine cannot run here, and
        # saying so now is far kinder than a loader error later.
        with open(mt, "rb") as f:
            head = f.read(8)
        if head[:4] == b"\xca\xfe\xba\xbe":
            kind = "universal (contains Intel)"
        elif head[:4] == b"\xce\xfa\xed\xfe":
            kind = "Intel"
        else:
            kind = "PowerPC only -- this host cannot run it"
            ok = False
        print("  %-18s %s" % ("engine build", kind))

    voices = read_voices(voicesdir)
    print("  %-18s %d" % ("voices", len(voices)))
    for engine, label in (("mtk3", "MacinTalk 3"), ("gala", "MacinTalk Pro"),
                          ("meow", "Vicki's engine")):
        names = [v[1] for v in voices if v[2] == engine]
        if names:
            print("      %-6s %-15s %s" % (engine, label, ", ".join(names)))
    return ok and bool(voices)


def default_out():
    appdata = os.environ.get("APPDATA")
    if appdata:
        return os.path.join(appdata, "nvda", "tigerspeech-data")
    return os.path.abspath("tigerspeech-data")


def main():
    ap = argparse.ArgumentParser(
        description="Extract Tiger's speech engine from your own install image.")
    ap.add_argument("image", help="a Mac OS X 10.4 installer .iso or .dmg")
    ap.add_argument("--out", default=None,
                    help="where to put it (default: the NVDA config folder)")
    args = ap.parse_args()

    if not os.path.isfile(args.image):
        raise SystemExit("no such image: %s" % args.image)
    outdir = args.out or default_out()

    sevenzip = find_7zip()
    print("7-Zip     : %s" % sevenzip)
    print("image     : %s" % args.image)
    print("output    : %s" % outdir)

    workdir = tempfile.mkdtemp(prefix="tiger-extract-")
    try:
        print("\nlooking for the installer packages:")
        paxes = fetch_paxes(sevenzip, args.image, workdir)
        if not paxes:
            raise SystemExit(
                "\nNo installer packages found in %s, in any partition.\n"
                "Is this a Mac OS X 10.4 *installer* image?  A disk image of\n"
                "an already-installed system has a different layout: point\n"
                "--out at a copy of its System/Library folder instead."
                % args.image)
        os.makedirs(outdir, exist_ok=True)
        written = 0
        for label, paxgz in paxes:
            print("\nunpacking %s:" % label)
            written += unpack(paxgz, outdir)
        print("\nwrote %d files" % written)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print("\nchecking:")
    if not verify(outdir):
        raise SystemExit("\nExtraction did not produce a usable engine.")
    print("\nDone. Restart NVDA and choose Tiger-speech (MacinTalk 3.3).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
