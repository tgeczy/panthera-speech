# -*- coding: utf-8 -*-
"""Find the engine the user supplied.

This add-on ships no part of Apple's speech software.  The user extracts it
from their own Mac OS X 10.7 install image -- `lion/tools/extract_lion.py` in
the repository does that -- and drops it somewhere we look.

**The engine lives in NVDA's configuration folder, not the add-on folder.**
Updating an add-on deletes and recreates its directory, so a few hundred
megabytes of extracted engine kept inside it would be destroyed on every
upgrade.

**Every engine in the family shares one folder**, `macintalk`, with a
subfolder per generation:

    macintalk/
        lion/           <- here
            Speech/Synthesizers/MacinTalk.SpeechSynthesizer/
            Speech/Voices/<name>.SpeechVoice/
            SpeechDictionary.framework/Versions/A/
            libstdc++.6.0.9.dylib
            libc++abi.dylib
        leopard/
        tiger/
        outspoken/

There is no list of legacy folders, because there has never been a Lion
add-on to have left one -- `migrate()` is here and does nothing, for a reason
worth reading before deleting it.  Everything else about finding the tree is
deliberately the same as the other generations: the folder itself, one level
inside it, or a text file pointing elsewhere, since a Lion tree runs to
hundreds of megabytes and is often kept on another drive.

Kept free of NVDA imports on purpose: the synthesizer and the global plugin
both need it, and so does anything run from a command line.
"""
import os

from . import dllhost

from . import pantheratrees
from .pantheratrees import (aac_available, config_base,  # noqa: F401
                            is_tree)

CONFIG_DIRNAME = os.path.join("macintalk", "lion")

_HERE = os.path.dirname(os.path.abspath(__file__))
HOST_EXE = os.path.join(_HERE, "panthera_host.exe")
#: The same program, as a library, for the screens NVDA will not copy an
#: executable to.  See `HostMixin._useLibrary`: which of the two is used
#: is decided by whether the executable is there, and nothing else.
HOST_DLL = os.path.join(_HERE, "panthera_host.dll")


def config_dir():
    """`<nvda user config>/macintalk/lion`."""
    return os.path.join(config_base(), CONFIG_DIRNAME)


#: Text files naming a tree kept somewhere else.  Both spellings, because the
#: other generations answer to two and a user who has done this once for
#: Leopard will reach for the same name.
POINTERS = ("lionspeech-data.txt", "lion-tree.txt")


def migrate():
    """-> None, always.  Lion has nowhere to have come from.

    **Present on purpose rather than missing.**  The global plugin migrates
    every generation in its table without asking whether that generation has
    anything to migrate, so a tree module without this raises `AttributeError`
    inside a start-up timer thread -- somewhere nobody is looking, in a
    release nobody would test the absence of.

    The other two answer with a path when they move an older release's folder
    under `macintalk`.  There has never been a Lion add-on, so there is no
    older folder and never will be.
    """
    return None


def find_tree():
    """-> the directory holding Speech/ and SpeechDictionary.framework, or None.

    Deliberately quiet about failure.  `explain()` is where the reasons live.
    """
    home = config_dir()
    cands = [home]

    env = os.environ.get("LION_TREE")
    if env:
        cands.insert(0, env)

    # One level down as well, because dropping the extracted folder in whole
    # is what people actually do.
    try:
        cands += [os.path.join(home, d) for d in sorted(os.listdir(home))
                  if os.path.isdir(os.path.join(home, d))]
    except OSError:
        pass

    for name in POINTERS:
        pointer = os.path.join(config_base(), name)
        if os.path.isfile(pointer):
            try:
                with open(pointer, encoding="utf-8") as f:
                    cands.append(f.read().strip())
            except OSError:
                pass

    #: And the SAPI driver's world, so data extracted there is found here --
    #: the same courtesy that driver already pays this folder, both ways now.
    cands += pantheratrees.sapi_roots("lion")

    for c in cands:
        if is_tree(c):
            return c
    return None


#: 10.7's engine wants a newer C++ runtime than Leopard's: 6.0.9 rather than
#: 6.0.4.  The host walks up from the engine path looking for these, so they
#: belong at the root of the tree or under `usr/lib`.
LIBSTDCXX = ("libstdc++.6.0.9.dylib", "libstdc++.6.dylib")

#: **And a second library, which is the whole reason 10.7 was hard.**  From
#: 10.7 the C++ ABI lives in `libc++abi.dylib` and libstdc++ merely re-exports
#: it, through a hundred and fifty indirect symbols.  Without it `__dynamic_cast`
#: and the `__cxa_*` family resolve to nothing, every RTTI object has a null
#: vptr, and the guards around function-local statics are missing -- so the
#: engine does not fail to load, it loads and then behaves inexplicably.
#: Leopard needs none of it; 6.0.4 implements the ABI itself.
LIBCXXABI = ("libc++abi.dylib",)


def find_libstdcxx(tree):
    """-> the path to Lion's C++ runtime under `tree`, or None.

    **6.0.9 is a version number two generations share and do not agree on.**
    Snow Leopard's file of this name is a different, larger library that
    implements the C++ ABI itself; Lion's re-exports it from `libc++abi`.
    Each lives in its own generation's folder and the host searches outwards
    from the engine, so they never meet -- unless somebody assembles a tree by
    hand from two discs.
    """
    return pantheratrees.find_runtime(tree, LIBSTDCXX)


def find_libcxxabi(tree):
    """-> the path to Lion's C++ ABI library under `tree`, or None."""
    return pantheratrees.find_runtime(tree, LIBCXXABI)


def engine_paths(tree):
    """-> (MacinTalk, SpeechDictionary, voices directory).

    The same three places Leopard keeps them.  `SPSupport.framework`, which
    the extractor also takes, is not among them: the host never loads it, and
    requiring it would refuse a tree that speaks perfectly well.
    """
    return (os.path.join(tree, "Speech", "Synthesizers",
                         "MacinTalk.SpeechSynthesizer", "Contents", "MacOS",
                         "MacinTalk"),
            os.path.join(tree, "SpeechDictionary.framework", "Versions", "A",
                         "SpeechDictionary"),
            os.path.join(tree, "Speech", "Voices"))


#: Engines the host can render with.  All three of Lion's, which is all of
#: them: 2 `meow`, 3 `gala` and 19 `mtk3` make the twenty-four voices.
#:
#: **The twenty-eight `*Compact` voices are not a fourth engine we are
#: missing.**  They are Nuance Vocalizer, driven by
#: `MultiLingual.SpeechSynthesizer`, and they are left out on purpose -- a
#: product with living commercial lineage and a current vendor, which is a
#: decision about what this project will be rather than about what it could
#: load.  Nothing here has to filter them by name: they carry no
#: `VoiceDescription` at all, so `read_voices` cannot even find a creator for
#: them and passes them over.  That is worth knowing before anyone "fixes"
#: `read_voices` to name a voice from its folder.
PLAYABLE_ENGINES = ("mtk3", "gala", "meow")


def read_voices(voicesdir, playable_only=False):
    """-> [(bundleName, displayName, engine), ...] -- see `pantheratrees`.

    A wrapper rather than a re-export, because `explain()` and the tests both
    replace `aac_available` *on this module*.  A shared reader consulting its
    own copy would ignore them and answer for a machine nobody is testing.
    """
    return pantheratrees.read_voices(voicesdir, playable_only, aac_available,
                                     PLAYABLE_ENGINES)


def explain():
    """-> (usable, [lines]) -- the same decision as usable(), said out loud.

    usable() answers a bare False and says nothing about which of its
    conditions failed, which is right for a synthesizer list and useless for
    everything else.

    **It matters more here than on the other generations**, because Lion hides
    itself when it has no tree rather than appearing and refusing to load.  A
    synthesizer that is simply not in the list can say nothing at all, so this
    text -- through the Tools menu report -- is the only route to an answer.

    Kept next to the decision it describes so the two cannot drift apart.
    """
    lines = []
    ok = True

    # Either kind of host will do.  On a secure screen NVDA has not copied
    # the executable -- it drops every `.exe` -- and the library beside it is
    # what speaks there, so a report that only looked for the executable would
    # call a working install broken on the one screen hardest to check.
    lines.append("host: %s %s"
                 % (HOST_EXE, "found" if os.path.isfile(HOST_EXE)
                    else ("MISSING, but %s is there and will be used"
                          % os.path.basename(HOST_DLL))
                    if os.path.isfile(HOST_DLL) else "MISSING"))
    if not dllhost.haveHost(HOST_EXE, HOST_DLL):
        ok = False

    home = config_dir()
    lines.append("data folder: %s %s"
                 % (home, "exists" if os.path.isdir(home) else "MISSING"))
    try:
        lines.append("  contains: %s"
                     % (", ".join(sorted(os.listdir(home))) or "(empty)"))
    except OSError as e:
        lines.append("  cannot list: %s" % e)

    found_tree = find_tree()
    if not found_tree:
        lines.append("no tree found: needs a folder with Speech\\Voices in it,"
                     " either the data folder itself or one level inside it")
        return False, lines
    lines.append("tree: %s" % found_tree)

    mt, sd, voices = engine_paths(found_tree)
    cxx = find_libstdcxx(found_tree)
    lines.append("libstdc++: %s" % (cxx or
                 "MISSING -- Lion's engine cannot load without it; take "
                 "usr/lib/libstdc++.6.0.9.dylib from the same install image"))
    if not cxx:
        ok = False
    abi = find_libcxxabi(found_tree)
    lines.append("libc++abi: %s" % (abi or
                 "MISSING -- 10.7 keeps the C++ ABI here rather than in "
                 "libstdc++; take usr/lib/libc++abi.dylib from the same "
                 "install image"))
    if not abi:
        ok = False
    for what, path in (("MacinTalk", mt), ("SpeechDictionary", sd)):
        good = os.path.isfile(path)
        lines.append("%s: %s %s" % (what, path, "found" if good else "MISSING"))
        if not good:
            ok = False

    playable = read_voices(voices, playable_only=True)
    present = read_voices(voices)
    lines.append("voices: %d playable of %d present in %s"
                 % (len(playable), len(present), voices))
    if present and not playable:
        lines.append("  present, but no engine we can render: %s"
                     % ", ".join(sorted({v[2] for v in present})))
    if not playable:
        ok = False
    if not aac_available():
        lines.append("no AAC decoder registered, so Alex and Vicki are "
                     "withheld")
    return ok, lines


def usable():
    """-> True when there is an engine we could actually speak with.

    Unlike Tiger's and Leopard's, this answer decides whether the synthesizer
    is *listed*: see `lionspeech.SynthDriver.check`.
    """
    if not dllhost.haveHost(HOST_EXE, HOST_DLL):
        return False
    tree = find_tree()
    if not tree:
        return False
    mt, sd, voices = engine_paths(tree)
    if not find_libstdcxx(tree) or not find_libcxxabi(tree):
        return False
    return (os.path.isfile(mt) and os.path.isfile(sd)
            and bool(read_voices(voices, playable_only=True)))
