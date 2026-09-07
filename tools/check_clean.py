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
    # **Only where it looks like a path.** The bare name is not a leak: this
    # project quotes Tomi constantly in its comments -- `# Tomi: "it does do
    # it"` -- and matching those made the check report twenty hits on every
    # single run, which is exactly how a guard stops being read. What actually
    # leaks is `C:\Users\Tomi\...`, and that is one separator away from a name
    # in prose. The two patterns around this one catch the rest.
    (re.compile(rb"(?i)[\\/]tomi(?![A-Za-z0-9_])"), "a user name in a path"),
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


def _walk(roots):
    files = []
    for r in roots:
        for dirpath, dirs, names in os.walk(r):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            files += [os.path.join(dirpath, n) for n in names]
    return files


def _scan(files, label):
    """-> how many hits, printed under `label`."""
    bad = 0
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
            if not bad:
                print("%s:" % label)
            print(fmt % (rel, where, what, text))
            bad += 1
    return bad


def main():
    # **Two questions, and only one of them should stop a release.**
    #
    # What ships is the add-on and the loader that goes into it: a developer's
    # path in there reaches every user, and that is what this tool was written
    # for. Tests and dev tools are a different question -- they are public in
    # this repository but they are not in anybody's download -- and treating
    # them the same made the check report twenty hits and exit 1 on every run,
    # which is how a guard stops being read.
    #
    # So both are reported and only the first decides the exit code.
    #
    # **Every add-on is discovered rather than listed.** This repository gains
    # one whenever a new tree is decoded, and a hardcoded list would quietly
    # stop checking the newest -- which is the one most likely to have a
    # developer's path in it.
    shipped = [os.path.join(ROOT, "src")]
    dev = [os.path.join(ROOT, "tools")]
    for name in sorted(os.listdir(ROOT)):
        addon = os.path.join(ROOT, name, "addon")
        if os.path.isdir(addon):
            shipped.append(addon)
            dev.append(os.path.join(ROOT, name, "tests"))
            dev.append(os.path.join(ROOT, name, "tools"))

    loose = [os.path.join(ROOT, f) for f in os.listdir(ROOT)
             if f.endswith(TEXT_EXT)]

    bad = _scan(_walk(shipped), "in what ships")
    other = _scan(_walk(dev) + loose, "in tests and tools, which do not ship")

    if bad:
        print("\n%d machine-specific reference(s) in shippable files." % bad)
        return 1
    if other:
        print("\nNothing shippable mentions a particular machine."
              " %d reference(s) in files that are not shipped." % other)
        return 0
    print("clean: nothing mentions a particular machine.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
