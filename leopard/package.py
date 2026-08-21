# -*- coding: utf-8 -*-
"""Build the .nvda-addon.

Deliberately refuses to package anything that looks like Apple's engine or
voice data. Nothing here is ours to distribute: the user supplies Tiger, and
the engine is read from wherever they extracted it and never ships here.
"""
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
ADDON = os.path.join(ROOT, "addon")

#: Every name here is a real file in a Tiger install, added as the extraction
#: turned each one up. `.i386`/`.ppc` are the slices a fat Mach-O is split into
#: on the way to a native port -- exactly the bytes it would be most tempting,
#: and least defensible, to ship.
FORBIDDEN_EXT = (".aiff", ".wav", ".pcm", ".qcow2", ".dylib", ".bin", ".img",
                 ".dmg", ".i386", ".ppc", ".so")
FORBIDDEN_NAME = ("macintalk", "speechdictionary", "stddictionary",
                  "voicedescription", "spsupport", "cartnames", "cartlite",
                  "pcmwave", "phonemesymbols", "symboldictionary",
                  "speechenginedescription")


def version():
    for line in open(os.path.join(ADDON, "manifest.ini"), encoding="utf-8"):
        if line.strip().startswith("version"):
            return line.split("=", 1)[1].strip()
    raise SystemExit("no version in manifest.ini")


def main():
    out = os.path.join(ROOT, "leopardspeech-%s.nvda-addon" % version())
    files, refused = [], []
    for dirpath, dirs, names in os.walk(ADDON):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for n in names:
            rel = os.path.relpath(os.path.join(dirpath, n), ADDON)
            rel = rel.replace(os.sep, "/")
            low = n.lower()
            if low.endswith(FORBIDDEN_EXT) or any(
                    low.startswith(b) for b in FORBIDDEN_NAME):
                refused.append(rel)
            else:
                files.append((os.path.join(dirpath, n), rel))

    if refused:
        print("REFUSING to package -- these are not ours to distribute:")
        for r in refused:
            print("   " + r)
        return 1

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for full, rel in sorted(files, key=lambda x: x[1]):
            z.write(full, rel)
            print("   + " + rel)
    print("\nwrote %s (%d bytes)" % (os.path.basename(out),
                                     os.path.getsize(out)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
