# -*- coding: utf-8 -*-
"""Recognise a Mac OS X install image, and take the speech engine out of it.

This is the add-on's half of the extractors that live in `*/tools/`.  It reads
a disc image the user chose, works out which release it is, and writes the
engine into the right folder under `macintalk`.

**Nothing of Apple's ships with this project and nothing is downloaded.**  The
bytes come off an image the user already has, and stay on their machine.  That
is the same rule the extractors have always followed; what is new is that it no
longer costs the user a Python install, a command line, or 7-Zip.

## Why there is no 7-Zip here any more

`extract_tiger.py` still shells out to `7z.exe`, on the strength of a comment
saying "Python cannot read an ISO or a DMG".  That stopped being true when
Leopard's extractor learned to read an HFS+ partition inside a hybrid image,
and doubly so when Lion's learned UDIF.  Measured against Tomi's own discs, the
one reader in `pantherahfs` opens **all** of them -- both Tiger builds,
Leopard, Snow Leopard, both Lion images, Mountain Lion and Sonoma.

So there is no bundled binary here, no LGPL question and no antivirus surface.

## What the version number does and does not tell you

It names the generation, and that is all.  It does **not** predict whether the
engine on the disc will run: the MacinTalk that works is byte-identical on a
disc reporting 10.4.1, and the one that crashes on hosts is on a disc
reporting 10.4.5.  `KNOWN_BAD_ENGINES` is checked against the engine itself,
off the image, before anything is written -- three seconds instead of an hour
followed by silence.
"""
import gzip
import hashlib
import io
import os
import re

#: **This module has two homes and the import has to survive both.**  Inside
#: NVDA it is `synthDrivers._panthera.pantheradiscs` and the relative import
#: is the right one.  `sapi/build.ps1` also stages it and `pantherahfs.py`
#: flat beside `extract.py`, where the SAPI installer's bundled Python
#: imports them as top-level modules with no package around them -- and the
#: embeddable build's `._pth` locks `sys.path` to exactly that folder.  The
#: relative form raises `ImportError` there, which is what makes the fallback
#: a fallback rather than a style choice: extraction for every JAWS user goes
#: through this line.  `test_sapi_flat_staging.py` keeps it honest.
try:
    from . import pantherahfs as hfs
except ImportError:                     # staged flat for the SAPI installer
    import pantherahfs as hfs

#: Where Apple states the version, on every image from 10.4 to 14.
VERSION_PLIST = "System/Library/CoreServices/SystemVersion.plist"
_VERSION_RE = re.compile(rb"ProductVersion</key>\s*<string>([^<]+)")

#: The engine, wherever it is live on the disc.
LIVE_ENGINE = ("System/Library/Speech/Synthesizers/"
               "MacinTalk.SpeechSynthesizer/Contents/MacOS/MacinTalk")

#: Builds that load and then fail, by sha256 of the MacinTalk binary.
#:
#: **This is the check the version number cannot make.**  See
#: `tiger-build-generations`: 10.4.5's engine crashes at channel open, jumping
#: to an unmapped address, and it took a user's bug report to find out.
KNOWN_BAD_ENGINES = {
    "b63b1ec9222d8eaab627911c830676891a90f0fccc722d9e4700c97a4ff121e5":
        "This disc carries the Tiger build of MacinTalk that does not run "
        "here: it loads, opens a speech channel and then crashes. Its twin "
        "on a disc reporting 10.4.1 is fine, so the version number is no "
        "guide -- look for another Tiger image. Nothing has been written.",
}

#: Trees that sit uncompressed on the disc, and where they go in our folder.
CLASSIC_LIVE = (
    ("System/Library/Speech/Synthesizers", "Speech/Synthesizers"),
    ("System/Library/Speech/Voices", "Speech/Voices"),
    ("System/Library/PrivateFrameworks/SpeechDictionary.framework",
     "SpeechDictionary.framework"),
    ("System/Library/PrivateFrameworks/SPSupport.framework",
     "SPSupport.framework"),
)

#: Where the voices hide, on every release that keeps the engine live.
#:
#: **The split is not what the names suggest.**  AdditionalSpeechVoices holds
#: only the two big concatenative voices; the twenty-odd classic MacinTalk 3
#: ones are in Essentials.  Taking only the first, which its name invites,
#: gets Alex and loses Agnes, Bruce, Victoria and all the singing voices.
CLASSIC_VOICE_PKGS = ("System/Installation/Packages/AdditionalSpeechVoices.pkg",
                      "System/Installation/Packages/Essentials.pkg")
VOICE_PREFIX = "./System/Library/Speech/Voices/"

#: 10.7 keeps nothing live: the engine is in a package and the dictionary's
#: tables are in a disk image inside the image.
LION_PKG = "Packages/BaseSystemBinaries.pkg"
LION_VOICE_PKGS = ("Packages/AdditionalSpeechVoices.pkg",
                   "Packages/Essentials.pkg")
LION_ENGINE_RULES = (
    ("./System/Library/Speech/Synthesizers/MacinTalk.SpeechSynthesizer/",
     "Speech/Synthesizers/MacinTalk.SpeechSynthesizer/"),
    ("./System/Library/PrivateFrameworks/SpeechDictionary.framework/",
     "SpeechDictionary.framework/"),
    ("./System/Library/PrivateFrameworks/SPSupport.framework/",
     "SPSupport.framework/"),
    ("./usr/lib/libstdc++.6.0.9.dylib", "libstdc++.6.0.9.dylib"),
    ("./usr/lib/libc++abi.dylib", "libc++abi.dylib"),
)
LION_DMG_TREES = (
    ("System/Library/PrivateFrameworks/SpeechDictionary.framework/Versions/A"
     "/Resources", "SpeechDictionary.framework/Versions/A/Resources"),
    ("System/Library/Speech/Voices", "Speech/Voices"),
)

#: Nuance Vocalizer, driven by a synthesizer this project does not load.  Left
#: out on a decision about what this project is rather than what it could read.
COMPACT = re.compile(r"[^/]*Compact\.SpeechVoice(/|$)")


class Generation(object):
    """One release, and everything that differs about reading its disc."""

    def __init__(self, key, label, prefixes, layout, runtime=(),
                 driver=None, why_not=None):
        self.key = key
        self.label = label
        self.prefixes = prefixes
        self.layout = layout
        #: (path on the image, [names to write]) -- Apple's C++ runtimes.
        self.runtime = runtime
        #: The synthesizer that reads this folder, or None if none does yet.
        self.driver = driver
        #: Set when the release is recognised but cannot be used.
        self.why_not = why_not

    @property
    def dirname(self):
        return os.path.join("macintalk", self.key)


GENERATIONS = (
    Generation("tiger", "Mac OS X 10.4 Tiger", ("10.4",), "classic",
               driver="tigerspeech"),
    Generation("leopard", "Mac OS X 10.5 Leopard", ("10.5",), "classic",
               runtime=(("usr/lib/libstdc++.6.0.4.dylib",
                         ("libstdc++.6.0.4.dylib", "libstdc++.6.dylib")),),
               driver="leopardspeech"),
    Generation("snowleopard", "Mac OS X 10.6 Snow Leopard", ("10.6",),
               "classic",
               runtime=(("usr/lib/libstdc++.6.0.9.dylib",
                         ("libstdc++.6.0.9.dylib", "libstdc++.6.dylib")),),
               driver="snowleopardspeech"),
    Generation("lion", "Mac OS X 10.7 Lion", ("10.7",), "lion",
               driver="lionspeech"),
)

#: Recognised, and deliberately refused, with the reason.
#:
#: 10.8 is where the i386 slice stops: its MacinTalk is a thin x86_64 binary,
#: and the host is 32-bit because Apple's engine is.  That is not a gap anyone
#: can close by trying harder -- there is no 32-bit code on the disc to run.
TOO_NEW = ("10.8", "10.9", "10.10", "10.11", "10.12", "10.13", "10.14",
           "10.15", "11.", "12.", "13.", "14.", "15.", "16.", "26.")


class Disc(object):
    """What an image turned out to be."""

    def __init__(self, path):
        self.path = path
        self.version = None
        self.generation = None
        self.usable = False
        self.problem = None
        self.warnings = []
        self.engine_sha = None
        self.engine_size = None
        self._base = None

    @property
    def label(self):
        if self.generation:
            return self.generation.label
        if self.version:
            return "Mac OS X %s" % self.version
        return os.path.basename(self.path)

    def volume(self):
        # Reopened rather than remembered: a compressed image is read through
        # a decompressing stream, and extraction runs on a different thread
        # from the dialog that identified it.  One stream shared by two
        # threads is a bug waiting for a big enough image.
        source, base = hfs.open_image(self.path)
        return hfs.Volume(source, base)


def _read_version(volume):
    entry = volume.entry(VERSION_PLIST)
    if entry is None:
        return None
    head = hfs.ExtentStream(volume, entry).read(8192)
    found = _VERSION_RE.search(head)
    return found.group(1).decode("ascii", "replace") if found else None


def _generation_for(version):
    for gen in GENERATIONS:
        for prefix in gen.prefixes:
            if version == prefix or version.startswith(prefix + "."):
                return gen
    return None


def _digest(volume, entry):
    """-> (sha256, size) of a file on the image, without writing it out."""
    h = hashlib.sha256()
    stream = hfs.ExtentStream(volume, entry)
    size = 0
    while True:
        block = stream.read(1 << 20)
        if not block:
            break
        h.update(block)
        size += len(block)
    return h.hexdigest(), size


def identify(path):
    """-> a `Disc` describing the image at `path`.  Never raises for a file
    that simply is not one of ours; `problem` says why instead."""
    disc = Disc(path)
    if not os.path.isfile(path):
        disc.problem = "There is no file at that path."
        return disc
    try:
        source, disc._base = hfs.open_image(path)
        volume = hfs.Volume(source, disc._base)
    except BaseException:
        # **Say what was tried, not what the file is.**
        #
        # This used to answer "it has no Mac partition map and no HFS+ volume
        # inside it" -- a confident claim about somebody's file, when all the
        # code knew was that it had not found one.  A reader that could not
        # read compressed disk images said it in that voice to a man holding
        # a perfectly good copy of Lion, and he concluded his own machine was
        # broken.  A tool should not be able to make someone doubt their disc
        # on the strength of a gap in itself.
        disc.problem = ("This file could not be opened as a Mac OS X install "
                        "image: it is not a raw disc image, and it has no "
                        "disk image inside it that this can read. If it is a "
                        "Mac OS X installer, please report it -- it may be a "
                        "kind of image this does not handle yet.")
        return disc

    disc.version = _read_version(volume)
    if not disc.version:
        disc.problem = ("This is a Mac disc, but it does not say which "
                        "version of Mac OS X it is, so it is probably not an "
                        "installer.")
        return disc

    if any(disc.version.startswith(v) for v in TOO_NEW):
        disc.problem = (
            "Mac OS X %s is too new. Apple dropped the 32-bit build of "
            "MacinTalk after 10.7, so the engine on this disc is 64-bit only "
            "and there is no 32-bit code on it to run. 10.7 Lion is the last "
            "release this can use." % disc.version)
        return disc

    disc.generation = _generation_for(disc.version)
    if disc.generation is None:
        disc.problem = ("Mac OS X %s is older than this project reaches. "
                        "10.4 Tiger is the earliest." % disc.version)
        return disc

    if disc.generation.why_not:
        disc.problem = disc.generation.why_not
        return disc

    engine = volume.entry(LIVE_ENGINE)
    if engine is not None:
        disc.engine_sha, disc.engine_size = _digest(volume, engine)
        bad = KNOWN_BAD_ENGINES.get(disc.engine_sha)
        if bad:
            disc.problem = bad
            return disc

    disc.usable = True
    return disc


# ---- writing it out ------------------------------------------------------

def _noop(_percent, _message):
    pass


class _Progress(object):
    """Turns bytes consumed into a percentage, once, in one place."""

    def __init__(self, total, report):
        self.total = max(1, total)
        self.done = 0
        self.report = report or _noop
        self.message = ""

    def say(self, message):
        self.message = message
        self.report(int(100.0 * self.done / self.total), message)

    def add(self, n):
        self.done += n
        self.report(min(99, int(100.0 * self.done / self.total)), self.message)


def _write_file(volume, entry, dest, prog):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    stream = hfs.ExtentStream(volume, entry)
    with open(dest, "wb") as out:
        while True:
            block = stream.read(1 << 20)
            if not block:
                break
            out.write(block)
            prog.add(len(block))


def _copy_tree(volume, entry, outdir, rel, prog, counts):
    """Copy a directory off the image, recursively.

    A catalogue entry is `(name, isdir, id, size, extents, mode)`.  Asking
    whether it has children is not the same question as whether it is a
    directory -- an empty folder has neither -- so this reads `isdir`, and
    skips symlinks, which on Windows are a path in a file nothing will follow.
    """
    for child in volume.children(entry):
        name, isdir, _id, size, ext, mode = child
        if hfs.ILLEGAL & set(name):
            counts["skipped"].append(name)
            continue
        here = "%s/%s" % (rel, name) if rel else name
        # Vocalizer, on the disk-image path too.  BaseSystem.dmg carries four
        # of them for the recovery system, and without this they arrive in the
        # voice list looking like voices the driver simply fails to speak.
        if COMPACT.search(here):
            continue
        dest = hfs.safe_join(outdir, here.replace("/", os.sep))
        if dest is None:
            counts["skipped"].append(here)
            continue
        if isdir:
            os.makedirs(dest, exist_ok=True)
            _copy_tree(volume, child, outdir, here, prog, counts)
            continue
        if (mode & hfs.S_IFMT) == hfs.S_IFLNK or ext is None:
            continue
        _write_file(volume, child, dest, prog)
        counts["files"] += 1
        counts["bytes"] += size

class _Counting(io.RawIOBase):
    """Counts what is read through it, so the bar can follow a package.

    The obvious thing -- asking the gzip stream how far into the file it is --
    does not work: `GzipFile.myfileobj` exists only when gzip opened the file
    itself, and here it is handed a stream.  It returned zero every time, so
    the bar sat at nought and then jumped to a hundred.  Count the bytes going
    past instead, which is the one number that is always true.
    """

    def __init__(self, inner, prog):
        self.inner = inner
        self.prog = prog

    def readable(self):
        return True

    def read(self, n=-1):
        block = self.inner.read(n)
        if block:
            self.prog.add(len(block))
        return block

    def readinto(self, buf):
        block = self.read(len(buf))
        buf[:len(block)] = block
        return len(block)


def _members(volume, entry, kind, prog):
    """-> a stream of cpio members for a package, whichever shape it is.

    Flat packages (10.5 and later) are a xar whose `Payload` is gzip; bundle
    packages (10.4) are a directory holding `Contents/Archive.pax.gz`, which
    despite the name is cpio too.
    """
    if kind == "bundle":
        inner = volume.entry_in(entry, "Contents/Archive.pax.gz") \
            if hasattr(volume, "entry_in") else None
        if inner is None:
            for child in volume.children(entry):
                if child[0] == "Contents":
                    for grand in volume.children(child):
                        if grand[0] == "Archive.pax.gz":
                            inner = grand
                    break
        if inner is None:
            return None, 0
        raw = _Counting(hfs.ExtentStream(volume, inner), prog)
        return gzip.GzipFile(fileobj=raw), inner[3]
    offset, length = hfs.payload_offset(volume, entry)
    if offset is None:
        return None, 0
    raw = _Counting(hfs.ForkReader(volume, entry, skip=offset), prog)
    return gzip.GzipFile(fileobj=raw), length


def _package_kind(volume, entry):
    return "bundle" if volume.children(entry) else "flat"


def _take_voices(volume, entry, outdir, prog, counts, label):
    """Stream a package once, keeping only what is under the voices prefix.

    The whole package is read whether or not much of it is wanted, which is
    why progress counts what has gone past rather than what has been written.
    """
    stream, _size = _members(volume, entry, _package_kind(volume, entry), prog)
    if stream is None:
        return
    prog.say("Reading %s" % label)
    for name, mode, size, read in hfs.cpio_entries(stream):
        if not name.startswith(VOICE_PREFIX):
            continue
        rel = name[len(VOICE_PREFIX):]
        if not rel or COMPACT.match(rel) or (hfs.ILLEGAL & set(rel)):
            continue
        dest = hfs.safe_join(outdir, os.path.join("Speech", "Voices",
                                                  *rel.split("/")))
        if dest is None:
            counts["skipped"].append(rel)
            continue
        kind = mode & hfs.S_IFMT
        if kind == hfs.S_IFDIR:
            os.makedirs(dest, exist_ok=True)
            continue
        if kind != hfs.S_IFREG:
            continue                       # a symlink; Windows has no use
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as out:
            if size:
                out.write(read())
        counts["files"] += 1
        counts["bytes"] += size

def _route(name, rules):
    for src, rel in rules:
        if src.endswith("/"):
            if name.startswith(src):
                return rel + name[len(src):]
        elif name == src:
            return rel
    return None


def _take_by_rules(volume, entry, outdir, rules, prog, counts, label):
    """Stream one package once, writing everything the rules name."""
    stream, _size = _members(volume, entry, _package_kind(volume, entry), prog)
    if stream is None:
        return
    prog.say("Reading %s" % label)
    for name, mode, size, read in hfs.cpio_entries(stream):
        rel = _route(name, rules)
        if rel is None or COMPACT.search(rel) or (hfs.ILLEGAL & set(rel)):
            continue
        dest = hfs.safe_join(outdir, rel.replace("/", os.sep))
        if dest is None:
            counts["skipped"].append(rel)
            continue
        kind = mode & hfs.S_IFMT
        if kind == hfs.S_IFDIR:
            os.makedirs(dest, exist_ok=True)
            continue
        if kind != hfs.S_IFREG:
            continue
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as out:
            if size:
                out.write(read())
        counts["files"] += 1
        counts["bytes"] += size

def _estimate(volume, generation):
    """Roughly how many bytes will be read.  Only the bar depends on it."""
    total = 0
    if generation.layout == "classic":
        for src, _rel in CLASSIC_LIVE:
            entry = volume.entry(src)
            if entry is not None:
                total += _tree_bytes(volume, entry)
        for path in CLASSIC_VOICE_PKGS:
            entry = volume.entry(path)
            if entry is not None:
                total += entry[3] or _tree_bytes(volume, entry)
    else:
        for path in (LION_PKG,) + LION_VOICE_PKGS:
            entry = volume.entry(path)
            if entry is not None:
                total += entry[3]
    return total


def _tree_bytes(volume, entry):
    total = 0
    for child in volume.children(entry):
        total += (_tree_bytes(volume, child) if child[1] else (child[3] or 0))
    return total


def extract(disc, outdir, progress=None, voices=True):
    """Write the engine from `disc` into `outdir`.  -> counts.

    `progress(percent, message)` is called as it goes, from whatever thread
    this runs on.  It must not touch the user interface directly.
    """
    if not disc.usable:
        raise ValueError(disc.problem or "this image cannot be used")
    volume = disc.volume()
    generation = disc.generation
    outdir = os.path.normpath(os.path.abspath(outdir))
    os.makedirs(outdir, exist_ok=True)
    counts = {"files": 0, "bytes": 0, "skipped": []}
    prog = _Progress(_estimate(volume, generation), progress)

    if generation.layout == "classic":
        for src, rel in CLASSIC_LIVE:
            entry = volume.entry(src)
            if entry is None:
                continue
            prog.say("Copying %s" % rel)
            os.makedirs(os.path.join(outdir, rel.replace("/", os.sep)),
                        exist_ok=True)
            _copy_tree(volume, entry, outdir, rel, prog, counts)
        for src, names in generation.runtime:
            entry = volume.entry(src)
            if entry is None:
                raise ValueError(
                    "%s is not on this image, and the engine cannot load "
                    "without it." % src)
            prog.say("Copying %s" % src.rsplit("/", 1)[-1])
            data = volume.read(entry)
            for name in names:
                # Both names: the real file is versioned and the other is a
                # symlink on a Mac.  Writing both means it does not matter
                # which one the loader looks for first.
                with open(os.path.join(outdir, name), "wb") as out:
                    out.write(data)
                counts["files"] += 1
                counts["bytes"] += len(data)
        if voices:
            for path in CLASSIC_VOICE_PKGS:
                entry = volume.entry(path)
                if entry is None:
                    continue
                _take_voices(volume, entry, outdir, prog, counts,
                             path.rsplit("/", 1)[-1])
    else:
        entry = volume.entry(LION_PKG)
        if entry is None:
            raise ValueError(
                "%s is not on this image. Lion keeps its engine in packages, "
                "so an installer without this one has nothing to take."
                % LION_PKG)
        _take_by_rules(volume, entry, outdir, LION_ENGINE_RULES, prog, counts,
                       "BaseSystemBinaries.pkg")
        # The dictionary's tables and Fred exist only inside BaseSystem.dmg.
        dmg = volume.entry("BaseSystem.dmg")
        if dmg is not None and dmg[4] is not None:
            prog.say("Reading BaseSystem.dmg")
            udif = hfs.UDIFReader(hfs.ExtentStream(volume, dmg), dmg[3])
            inner = hfs.find_hfs(
                lambda o, n: (udif.seek(o), udif.read(n))[1], udif.size,
                "BaseSystem.dmg")
            base = hfs.Volume(udif, inner)
            for src, rel in LION_DMG_TREES:
                sub = base.entry(src)
                if sub is None:
                    continue
                os.makedirs(os.path.join(outdir, rel.replace("/", os.sep)),
                            exist_ok=True)
                _copy_tree(base, sub, outdir, rel, prog, counts)
        if voices:
            for path in LION_VOICE_PKGS:
                pkg = volume.entry(path)
                if pkg is None:
                    continue
                _take_voices(volume, pkg, outdir, prog, counts,
                             path.rsplit("/", 1)[-1])

    prog.report(100, "Finished")
    return counts


def installed_voices(outdir):
    """-> the voices in a folder that a driver would actually offer.

    **Counting `*.SpeechVoice` folders is the wrong count.**  A tree extracted
    before the Vocalizer filter existed holds all 28 Compact bundles as well,
    so a Lion folder reads as 52 while the synthesizer offers 24 -- and a
    number in a dialog that disagrees with the voice list is worse than no
    number.  The drivers route a voice by the creator in its
    `VoiceDescription`, and the Compact bundles have no such file at all, so
    asking the same question here gives the same answer.
    """
    voices = os.path.join(outdir, "Speech", "Voices")
    try:
        entries = os.listdir(voices)
    except OSError:
        return []
    found = []
    for name in entries:
        if not name.endswith(".SpeechVoice"):
            continue
        if os.path.isfile(os.path.join(voices, name, "Contents", "Resources",
                                       "VoiceDescription")):
            found.append(name[:-len(".SpeechVoice")])
    return sorted(found)
