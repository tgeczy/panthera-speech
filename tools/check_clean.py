# -*- coding: utf-8 -*-
"""Fail if anything shippable mentions a particular machine.

Paths from a developer's disk are the easiest thing to leak into a release:
they hide in usage strings compiled into a binary, in default arguments, in
test fixtures.  This walks the add-on and the sources looking for absolute
paths and personal names, and is meant to be run before publishing.

    py -3 tools/check_clean.py
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: An absolute Windows path, a home directory, or a name.  `%APPDATA%` and
#: other environment-relative references are fine -- they are not anybody's
#: machine in particular.
PATTERNS = [
    # The drive letter must not follow an identifier character, or every
    # `printf("stack:\n")` in the sources looks like a path.
    (re.compile(rb"(?<![A-Za-z0-9_])[A-Za-z]:[\\/][A-Za-z0-9_.\-]"),
     "an absolute path"),
    (re.compile(rb"(?i)tomi"), "a user name"),
    (re.compile(rb"(?i)/Users/|\\Users\\"), "a home directory"),
]

TEXT_EXT = (".py", ".c", ".h", ".sh", ".ini", ".md", ".cmd", ".txt")

#: Standard install locations are not a particular machine, and the build has
#: to name them to find a compiler at all.
ALLOWED = (b"C:/Program Files", rb"C:\Program Files", b"%APPDATA%")


def allowed(line):
    return any(a in line for a in ALLOWED)


def scan_text(path):
    hits = []
    with open(path, "rb") as f:
        for n, line in enumerate(f, 1):
            for pat, what in PATTERNS:
                if pat.search(line) and not allowed(line):
                    hits.append((n, what, line.decode("utf-8", "replace").strip()))
    return hits


def scan_binary(path):
    """Printable runs inside a binary, which is where usage strings hide."""
    hits = []
    with open(path, "rb") as f:
        data = f.read()
    for m in re.finditer(rb"[ -~]{6,}", data):
        s = m.group()
        for pat, what in PATTERNS:
            if pat.search(s) and not allowed(s):
                hits.append((m.start(), what,
                             s.decode("utf-8", "replace")[:120]))
    return hits


def main():
    bad = 0
    # Everything that ships, plus the sources that build it.
    #
    # **Every add-on, discovered rather than listed.** This repository holds
    # one per engine generation and gains one whenever a new tree is decoded;
    # a hardcoded list would quietly stop checking the newest add-on, which is
    # exactly the one most likely to have a developer's path in it.
    roots = [os.path.join(ROOT, "src"), os.path.join(ROOT, "tools")]
    for name in sorted(os.listdir(ROOT)):
        addon = os.path.join(ROOT, name, "addon")
        if os.path.isdir(addon):
            roots.append(addon)
            roots.append(os.path.join(ROOT, name, "tests"))
            roots.append(os.path.join(ROOT, name, "tools"))
    files = [os.path.join(ROOT, f) for f in os.listdir(ROOT)
             if f.endswith(TEXT_EXT)]
    for r in roots:
        for dirpath, dirs, names in os.walk(r):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            files += [os.path.join(dirpath, n) for n in names]

    for path in sorted(set(files)):
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
        if os.path.basename(path) == "check_clean.py":
            continue                      # the patterns themselves live here
        if path.lower().endswith((".exe", ".dll")):
            hits = scan_binary(path)
            fmt = "  %s: at 0x%x, %s: %s"
        elif path.lower().endswith(TEXT_EXT):
            hits = scan_text(path)
            fmt = "  %s: line %d, %s: %s"
        else:
            continue
        for where, what, text in hits:
            print(fmt % (rel, where, what, text))
            bad += 1

    if bad:
        print("\n%d machine-specific reference(s) found." % bad)
        return 1
    print("clean: nothing shippable mentions a particular machine.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
