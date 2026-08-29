# -*- coding: utf-8 -*-
"""Find the engine the user supplied.

This add-on ships no part of Apple's speech software.  The user extracts it
from their own Mac OS X 10.6 install DVD -- the speech data manager under
NVDA's Tools menu does that, and needs nothing else installed -- and it lands
somewhere we look.

**The engine lives in NVDA's configuration folder, not the add-on folder.**
Updating an add-on deletes and recreates its directory, so a few hundred
megabytes of extracted engine kept inside it would be destroyed on every
upgrade.

**Every engine in the family shares one folder**, `macintalk`, with a
subfolder per generation:

    macintalk/
        snowleopard/    <- here
            Speech/Synthesizers/MacinTalk.SpeechSynthesizer/
            Speech/Voices/<name>.SpeechVoice/
            SpeechDictionary.framework/Versions/A/
            libstdc++.6.0.9.dylib
        lion/
        leopard/
        tiger/
        outspoken/

## What 10.6 is

**MacinTalk 3.10**, by Apple's own reckoning: the engine carries
`SpeechSynthesis-3.10.35` in its debug paths where Leopard's carries
`3.6.59` and Lion's `4.0.74`.  So the numbering says what the measurements
say -- a late 3.x, nearer Leopard than Lion, and not the rewrite 4.0 was.

It is a hybrid, and that is the whole reason it needed no new binding work:

    ==================  ================  ==================  ===============
                        Leopard 10.5      Snow Leopard 10.6   Lion 10.7
    ==================  ================  ==================  ===============
    binding             relocations       compressed          compressed
    speech API          SESpeakBuffer     SESpeakBuffer       SESpeakCFString
    worker              MPCreateTask      GCD                 GCD
    clock               UpTime            gettimeofday        gettimeofday
    maths               vecLib, time      vecLib, time        Accelerate, FFT
    ends an utterance   AUGraphStop       AUGraphStop         a deferred stop
    ==================  ================  ==================  ===============

Every one of those columns already existed here: the compressed dyld info
interpreter written for Lion, Leopard's speech path and vDSP shims, and Lion's
GCD and scaled `gettimeofday`.  Its worker is `_MTBEWorkerExecuteTask`,
singular, where Lion's is the plural -- a direct ancestor.

The last row is the one that decides how it feels to use.  10.7 never stops
its audio graph, so a Lion utterance had nothing to end on but a silence
timeout; 10.6 calls `AUGraphStop` once per utterance exactly as 10.5 does, and
measured here a short sentence costs a single ten-millisecond tick after its
audio is complete.  There is no fixed tail to remove because there is none to
begin with.

Kept free of NVDA imports on purpose: the synthesizer, the global plugin and
the speech data manager all need it, and so does anything run from a command
line.
"""
import os

from . import dllhost

from . import pantheratrees
from .pantheratrees import (PLAYABLE_ENGINES, aac_available,  # noqa: F401
                            config_base, is_tree)

CONFIG_DIRNAME = os.path.join("macintalk", "snowleopard")

_HERE = os.path.dirname(os.path.abspath(__file__))
HOST_EXE = os.path.join(_HERE, "panthera_host.exe")
#: The same program, as a library, for the screens NVDA will not copy an
#: executable to.  See `HostMixin._useLibrary`: which of the two is used
#: is decided by whether the executable is there, and nothing else.
HOST_DLL = os.path.join(_HERE, "panthera_host.dll")


def config_dir():
    """`<nvda user config>/macintalk/snowleopard`."""
    return os.path.join(config_base(), CONFIG_DIRNAME)


#: Text files naming a tree kept somewhere else.  Both spellings, because the
#: other generations answer to two and a user who has done this once for
#: Leopard will reach for the same shape of name.
POINTERS = ("snowleopardspeech-data.txt", "snowleopard-tree.txt")


def migrate():
    """-> None, always.  Snow Leopard has nowhere to have come from.

    **Present on purpose rather than missing.**  The global plugin migrates
    every generation in its table without asking whether that generation has
    anything to migrate, so a tree module without this raises `AttributeError`
    inside a start-up timer thread -- somewhere nobody is looking, in a
    release nobody would test the absence of.

    Tiger's and Leopard's answer with a path when they move an older release's
    folder under `macintalk`.  There has never been a Snow Leopard add-on, so
    there is no older folder and never will be.  Lion's says the same thing
    for the same reason.
    """
    return None


def find_tree():
    """-> the directory holding Speech/ and SpeechDictionary.framework, or None.

    Deliberately quiet about failure.  `explain()` is where the reasons live.
    """
    home = config_dir()
    cands = [home]

    env = os.environ.get("SNOWLEOPARD_TREE")
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
    cands += pantheratrees.sapi_roots("snowleopard")

    for c in cands:
        if is_tree(c):
            return c
    return None


#: 10.6's engine wants Apple's own C++ runtime, and the version number is a
#: trap: **it is 6.0.9, the same name Lion's is, and it is not the same
#: library.**  Snow Leopard's is 2,439,888 bytes and implements the C++ ABI
#: itself, the way Leopard's 6.0.4 does.  Lion's is 1,595,728 bytes and
#: re-exports the ABI out of `libc++abi.dylib`, which is why Lion needs a
#: second file and this does not.
#:
#: They are never confused in practice because each generation's folder holds
#: its own and the host searches from the engine outwards -- but a tree
#: assembled by hand from two discs would load one and then behave
#: inexplicably, so it is worth saying out loud before somebody tidies the two
#: identical-looking files into one.
LIBSTDCXX = ("libstdc++.6.0.9.dylib", "libstdc++.6.dylib")


def find_libstdcxx(tree):
    """-> the path to Snow Leopard's C++ runtime under `tree`, or None."""
    return pantheratrees.find_runtime(tree, LIBSTDCXX)


def engine_paths(tree):
    """-> (MacinTalk, SpeechDictionary, voices directory).

    The same three places every generation from 10.4 to 10.7 keeps them.
    `SPSupport.framework`, which the extractor also takes, is not among them:
    the host never loads it, and requiring it would refuse a tree that speaks
    perfectly well.
    """
    return (os.path.join(tree, "Speech", "Synthesizers",
                         "MacinTalk.SpeechSynthesizer", "Contents", "MacOS",
                         "MacinTalk"),
            os.path.join(tree, "SpeechDictionary.framework", "Versions", "A",
                         "SpeechDictionary"),
            os.path.join(tree, "Speech", "Voices"))


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

    **It matters here in the way it matters for Lion**: 10.6 hides itself when
    it has no tree rather than appearing and refusing to load, and a
    synthesizer that is simply not in the list can say nothing at all.  This
    text, through the Tools menu report, is the only route to an answer.

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
                 "MISSING -- Snow Leopard's engine cannot load without it; "
                 "take usr/lib/libstdc++.6.0.9.dylib from the same install "
                 "disc, and note that Lion's file of that name is a different "
                 "library and will not do"))
    if not cxx:
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

    Like Lion's and unlike Tiger's and Leopard's, this answer decides whether
    the synthesizer is *listed*: see `snowleopardspeech.SynthDriver.check`.
    """
    if not dllhost.haveHost(HOST_EXE, HOST_DLL):
        return False
    tree = find_tree()
    if not tree:
        return False
    mt, sd, voices = engine_paths(tree)
    if not find_libstdcxx(tree):
        return False
    return (os.path.isfile(mt) and os.path.isfile(sd)
            and bool(read_voices(voices, playable_only=True)))
