# -*- coding: utf-8 -*-
r"""Pull the speech engine out of your own Mac OS X 10.6 install disc.

Nothing of Apple's ships with this project, so this is how you get an engine:
from a Snow Leopard installer you own.  Same posture as the sibling
extractors -- ship the extractor, never the bits.

    py -3 snowleopard/tools/extract_snowleopard.py "Mac OS X 10.6.iso"
    py -3 snowleopard/tools/extract_snowleopard.py 10.6.iso --out D:\sl
    py -3 snowleopard/tools/extract_snowleopard.py 10.6.iso --no-voices

**Most people should not need this.**  NVDA's Tools menu has "Mac OS X speech
data...", which does the same job from a dialog, with a progress bar, without
Python and without a copy of this repository.  This is here for people who
would rather drive it from a command line, and because a tool you can read is
a better answer to "what exactly does it take off my disc?" than a promise.

## It is a hundred lines because the work moved

Tiger's extractor is 478 lines, Leopard's 540 and Lion's 870, and each one
carries its own copy of a disc reader.  They were written that way on purpose:
they are single files you download and run, so they cannot import anything.

That stopped being the shape worth having when the reader moved into the
add-on for the Tools menu to use.  `pantherahfs` opens every Mac OS X
installer from 10.4 to 10.7 -- and Mountain Lion and Sonoma, which it opens in
order to refuse them -- and `pantheradiscs` knows what to take out of each.
So this asks them, rather than repeating them, and what it costs is a clone of
the repository instead of one downloaded file.

## What comes off a 10.6 disc

Snow Leopard's DVD has a live filesystem, the way Tiger's and Leopard's do and
Lion's does not: the engine, the dictionary and the frameworks are copied
straight out of it, and only the voices are inside packages.

    System/Library/Speech/Synthesizers/      MacinTalk itself
    System/Library/Speech/Voices/            what few voices are live
    .../PrivateFrameworks/SpeechDictionary.framework
    .../PrivateFrameworks/SPSupport.framework
    usr/lib/libstdc++.6.0.9.dylib            Apple's C++ runtime
    System/Installation/Packages/Essentials.pkg
                                             every classic voice
    System/Installation/Packages/AdditionalSpeechVoices.pkg
                                             Alex and Vicki, and nothing else

Two things about that list are worth stating rather than discovering:

* **`AdditionalSpeechVoices.pkg` misleads exactly as it does on 10.5 and
  10.7.**  Taking only the package whose name says "speech voices" gets Alex
  and loses Agnes, Bruce, Victoria and every novelty voice.  Both packages are
  read.
* **`libstdc++.6.0.9.dylib` is not Lion's file of that name.**  Snow Leopard's
  is 2,439,888 bytes and implements the C++ ABI itself; Lion's is 1,595,728
  and re-exports it out of `libc++abi.dylib`.  Take this one from the 10.6
  disc.  Nothing checks, because nothing can tell them apart by name, and the
  wrong one loads and then behaves inexplicably.

The binaries on the disc are fat Mach-O files with an i386 slice, which is the
slice this project runs.  The voices and the dictionary tables are
architecture-neutral data and are the same bytes on any Mac.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ADDON = os.path.join(os.path.dirname(os.path.dirname(_HERE)),
                      "panthera", "addon", "synthDrivers", "_panthera")
if not os.path.isdir(_ADDON):
    raise SystemExit(
        "this tool reads the disc with the add-on's own reader, and cannot "
        "find it at:\n  %s\nRun it from a clone of the repository, or use "
        "NVDA's Tools menu instead." % _ADDON)
sys.path.insert(0, _ADDON)

import pantheradiscs                                          # noqa: E402
import pantherasnowleopard                                    # noqa: E402

GENERATION = "snowleopard"


def default_out():
    """The shared folder every Macintosh engine writes into.

    **Not `pantherasnowleopard.config_dir()`, and that is the whole comment.**
    `config_base()` reads `globalVars.appArgs.configPath`, which is the only
    correct source *inside NVDA* -- it accounts for a portable copy and for
    `-c` on the command line.  Outside NVDA there is no `globalVars`, so it
    falls back to `~/.nvda`, which is a real directory that nothing reads.

    A command-line tool is outside NVDA by definition, so asking the module
    that knows best gets the one answer that is always wrong here.  It wrote
    446 MB into `C:\\Users\\<you>\\.nvda` once before this existed.  The
    sibling extractors compute it from `%APPDATA%` for the same reason.
    """
    appdata = os.environ.get("APPDATA")
    if appdata:
        return os.path.join(appdata, "nvda", "macintalk", GENERATION)
    return os.path.join(os.getcwd(), "macintalk", GENERATION)


def _bar(percent, message, _state={"last": None}):
    """One line, rewritten -- and only when it has something new to say."""
    line = "%3d%%  %s" % (percent, message)
    if line == _state["last"]:
        return
    _state["last"] = line
    sys.stderr.write("\r%-70s" % line)
    sys.stderr.flush()


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Extract Mac OS X 10.6 speech data from an install image.")
    ap.add_argument("image", help="a Snow Leopard install .iso, .dmg or .cdr")
    ap.add_argument("--out", default=default_out(),
                    help="where to write it (default: "
                         "%%APPDATA%%\\nvda\\macintalk\\snowleopard)")
    ap.add_argument("--no-voices", action="store_true",
                    help="engine and dictionary only, no voice banks")
    ap.add_argument("--quiet", action="store_true", help="no progress")
    args = ap.parse_args(argv)

    out = args.out

    disc = pantheradiscs.identify(args.image)
    if not disc.usable:
        raise SystemExit(disc.problem or "this image cannot be used")
    if disc.generation.key != GENERATION:
        # Recognised, and the wrong disc.  Saying which one it is beats
        # "unusable", because the answer is usually "you picked the wrong ISO".
        raise SystemExit(
            "this is %s, not Snow Leopard. Nothing has been written.\n"
            "The add-on's Tools menu reads every generation from one dialog; "
            "on the command line, use that generation's own extractor."
            % disc.label)

    print("%s -> %s" % (disc.label, out))
    counts = pantheradiscs.extract(disc, out, voices=not args.no_voices,
                                   progress=None if args.quiet else _bar)
    if not args.quiet:
        sys.stderr.write("\r%-70s\n" % "")

    voices = pantheradiscs.installed_voices(out)
    print("%d files, %d MB" % (counts["files"], int(counts["bytes"] / 1e6)))
    print("%d voices: %s" % (len(voices), ", ".join(voices)))
    if counts["skipped"]:
        print("%d file(s) skipped because Windows cannot name them; none of "
              "them are needed." % len(counts["skipped"]))
    # Checked against the folder that was actually written, not against the
    # one the driver would look in.  Those are the same folder only when
    # `--out` was left alone, and a tool that reports on somewhere else is
    # worse than one that reports nothing.
    missing = [what for what, path in
               zip(("MacinTalk", "SpeechDictionary"),
                   pantherasnowleopard.engine_paths(out))
               if not os.path.isfile(path)]
    if not pantherasnowleopard.find_libstdcxx(out):
        missing.append("libstdc++.6.0.9.dylib")
    if not voices:
        missing.append("any voice")
    if missing:
        print("\nthis tree will not run yet -- missing: %s"
              % ", ".join(missing))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
