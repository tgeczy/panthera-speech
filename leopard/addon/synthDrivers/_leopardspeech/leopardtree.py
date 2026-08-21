# -*- coding: utf-8 -*-
"""Find the engine the user supplied.

This add-on ships no part of Apple's speech software.  The user extracts it
from their own Mac OS X 10.5 install image -- `tools/extract_leopard.py` in the
repository does that -- and drops it somewhere we look.

**The engine lives in NVDA's configuration folder, not the add-on folder.**
Updating an add-on deletes and recreates its directory, so a few hundred
megabytes of extracted engine kept inside it would be destroyed on every
upgrade.

**Every engine in the family shares one folder**, `macintalk`, with a
subfolder per generation:

    macintalk/
        leopard/        <- here
            Speech/Voices/<name>.SpeechVoice/
            SpeechDictionary.framework/Versions/A/
            libstdc++.6.0.4.dylib
        tiger/
        outspoken/

That is a change from `leopardspeech-data` sitting loose in the configuration
directory beside `tigerspeech-data` and `outspoken-roms`.  Three folders for
one lineage was asked about the day the repositories merged, and the answer
was the same one: they belong together.  `migrate` moves an older release's
folder across, once.

The extracted folder may also be dropped in whole, one level down, because
that is what people actually do.  And because a Leopard tree is 717 MB and
often kept on another drive, a text file named for the folder works as a
pointer.

Kept free of NVDA imports on purpose: the synthesizer and the global plugin
both need it, and so does anything run from a command line.
"""
import os

CONFIG_DIRNAME = os.path.join("macintalk", "leopard")

#: Where earlier releases kept it.  Searched after the new location and
#: moved out of on first use.  **`leopard-data` is in here because
#: `extract_leopard.py` defaulted to writing it there while this module
#: only ever looked in `leopardspeech-data`** -- anyone who took the
#: extractor at its word had a tree the add-on could not find.
LEGACY_DIRNAMES = ("leopardspeech-data", "leopard-data")

_HERE = os.path.dirname(os.path.abspath(__file__))
HOST_EXE = os.path.join(_HERE, "leopard_host.exe")


def config_base():
    """NVDA's user configuration directory, or a stand-in outside NVDA.

    `globalVars.appArgs.configPath` is the only correct source. NVDA's own
    `NVDAState.WritePaths.configDir` is a property wrapping exactly this value,
    so it already accounts for a portable copy and for a config directory given
    on the command line with `-c`. Expanding `%APPDATA%` ourselves would be
    right on one machine and wrong on every portable one.

    The fallback exists for running outside NVDA -- the tests, and anything
    driven from a command line -- and for nothing else.
    """
    try:
        import globalVars
        path = globalVars.appArgs.configPath
        if path:
            return str(path)
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), ".nvda")


def config_dir():
    """`<nvda user config>/macintalk/leopard`."""
    return os.path.join(config_base(), CONFIG_DIRNAME)


def legacy_dirs():
    return [os.path.join(config_base(), n) for n in LEGACY_DIRNAMES]


def migrate():
    """Move an earlier release's folder under `macintalk`, once. -> path|None

    **A rename, never a copy.** Old and new both sit inside NVDA's
    configuration directory, so this is one volume and one metadata
    operation: this tree is 717 MB, almost all of it Alex, and it moves in
    milliseconds. A copy would have to be resumable, verified and undoable,
    and would make the whole idea a bad one.

    Three things keep it safe:

    * **It is lazy.** Called from `find_tree`, not at import, so nothing
      happens while NVDA is starting and speech is waiting.
    * **Failure changes nothing.** `os.rename` either moves the directory or
      raises; there is no half-moved state. Windows refuses it outright if
      the engine has a file open, so the answer to a locked tree is to go on
      using it where it is -- which is why the old locations stay in
      `find_tree`\'s candidates for good rather than for one release.
    * **It leaves a breadcrumb**, and the breadcrumb is load-bearing. The
      pointer file it writes is the one an *older* version of this add-on
      already reads, so a user who rolls back, or who runs two add-ons of
      different vintages, still finds the tree.
    """
    new = config_dir()
    if os.path.isdir(new):
        return None
    for old in legacy_dirs():
        if not os.path.isdir(old):
            continue
        try:
            os.makedirs(os.path.dirname(new), exist_ok=True)
            os.rename(old, new)
        except OSError:
            return None                  # in use, or not ours to move
        pointer = os.path.join(config_base(), LEGACY_DIRNAMES[0] + ".txt")
        try:
            if not os.path.exists(pointer):
                with open(pointer, "w", encoding="utf-8") as f:
                    f.write(new)
        except OSError:
            pass
        return new
    return None


def is_tree(path):
    return bool(path) and os.path.isdir(os.path.join(path, "Speech", "Voices"))


def find_tree():
    """-> the directory holding Speech/ and SpeechDictionary.framework, or None.

    Deliberately quiet about failure.  The synthesizer reports itself as
    unavailable rather than appearing and then being silent.
    """
    migrate()
    home = config_dir()
    cands = []

    env = os.environ.get("LEOPARD_TREE")
    if env:
        cands.append(env)

    # The shared folder first, then wherever earlier releases left it -- which
    # is where it stays if the rename could not happen, so this is a permanent
    # fallback rather than one release\'s courtesy.
    for root in [home] + legacy_dirs():
        cands.append(root)
        try:
            cands += [os.path.join(root, d) for d in sorted(os.listdir(root))
                      if os.path.isdir(os.path.join(root, d))]
        except OSError:
            pass

    for pointer in ([os.path.join(config_base(), n + ".txt")
                     for n in LEGACY_DIRNAMES] +
                    [os.path.join(config_base(), "leopard-tree.txt")]):
        if os.path.isfile(pointer):
            try:
                with open(pointer, encoding="utf-8") as f:
                    cands.append(f.read().strip())
            except OSError:
                pass

    for c in cands:
        if is_tree(c):
            return c
    return None


#: Leopard's engine links against Apple's own C++ runtime and will not load
#: without it: std::string, the list-node helpers, and __dynamic_cast with the
#: RTTI that makes it answer anything but null. It cannot be reimplemented --
#: GCC 4.0.1's copy-on-write basic_string layout has to match exactly, and the
#: engine has inlined code that walks it -- so the real dylib has to be present.
#: Tiger needed none of this; Leopard falls over at the first speech channel.
LIBSTDCXX = ("libstdc++.6.0.4.dylib", "libstdc++.6.dylib")


def find_libstdcxx(tree):
    """-> the path to Leopard's C++ runtime under `tree`, or None.

    The extractor puts it at the root; a hand-assembled tree may keep the
    install layout, so usr/lib is checked too.
    """
    for sub in ("", os.path.join("usr", "lib")):
        for name in LIBSTDCXX:
            p = os.path.join(tree, sub, name) if sub else os.path.join(tree, name)
            if os.path.isfile(p):
                return p
    return None


def engine_paths(tree):
    """-> (MacinTalk, SpeechDictionary, voices directory)."""
    return (os.path.join(tree, "Speech", "Synthesizers",
                         "MacinTalk.SpeechSynthesizer", "Contents", "MacOS",
                         "MacinTalk"),
            os.path.join(tree, "SpeechDictionary.framework", "Versions", "A",
                         "SpeechDictionary"),
            os.path.join(tree, "Speech", "Voices"))


#: Engines the host can actually render with -- all three, since 0.5.
#:
#: `meow` -- Vicki, and only Vicki -- keeps its sample bank as **AAC**, which
#: is why Apple gave her an engine to herself and why she was silent here until
#: the host learned to answer `SoundConverterFillBuffer` through the AAC
#: decoder Windows already ships.
#:
#: A voice that is selectable and then silent is worse than one that is absent,
#: and far worse here than for a whole synthesizer: choosing it mutes the
#: screen reader, and the user cannot hear the voice list well enough to choose
#: their way back out.  That is not a cosmetic failure for the person this is
#: built for -- so if the decoder is ever missing, the fallback has to be an
#: absent voice rather than a mute one.
PLAYABLE_ENGINES = ("mtk3", "gala", "meow")


#: The AAC decoder Vicki needs, by class id.  Checking the registration is
#: cheaper and quieter than starting the host to ask, and it is the same test
#: the host's own `CoCreateInstance` will make a moment later.
_AAC_CLSID = r"CLSID\{32D186A7-218F-4C75-8876-DD77273A8999}"


def aac_available():
    """-> True when Windows has an AAC decoder for Vicki's sample bank.

    Windows N and KN editions ship without one until the Media Feature Pack is
    installed.  Absent from *both* registry views is a clear answer; anything
    else -- no registry at all, an unexpected error -- is not, and says yes,
    because losing a voice to a failed check is the smaller mistake and the
    driver still has to survive a host that renders silence.
    """
    try:
        import winreg
    except ImportError:
        return True
    views = (getattr(winreg, "KEY_WOW64_32KEY", 0),
             getattr(winreg, "KEY_WOW64_64KEY", 0))
    missing = 0
    for view in views:
        try:
            key = winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, _AAC_CLSID, 0,
                                 winreg.KEY_READ | view)
            key.Close()
            return True
        except FileNotFoundError:
            missing += 1
        except OSError:
            return True
    return missing < len(views)


def read_voices(voicesdir, playable_only=False):
    """-> [(bundleName, displayName, engine), ...] read from the user's install.

    Built from the files rather than a table, so it cannot disagree with what
    is actually there.  The creator OSType is at +4 and the name is a `Str63`
    at **+16** -- a `version` long sits between the VoiceSpec and the name, and
    missing it yields empty strings.
    """
    out = []
    playable = PLAYABLE_ENGINES
    if playable_only and not aac_available():
        playable = tuple(e for e in playable if e != "meow")
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
        if playable_only and engine not in playable:
            continue
        nlen = head[16]
        name = head[17:17 + nlen].decode("mac-roman", "replace")
        out.append((entry[:-len(".SpeechVoice")], name or entry, engine))
    # Concatenative voices first: this list is a menu a blind user arrows
    # through, and the novelty voices are not what anyone is looking for.
    order = {"meow": 0, "gala": 1, "mtk3": 2}
    out.sort(key=lambda v: (order.get(v[2], 3), v[1]))
    return out


def explain():
    """-> (usable, [lines]) -- the same decision as usable(), said out loud.

    usable() answers a bare False and says nothing about which of its four
    conditions failed, which is right for a synthesizer list and useless for
    everything else.  Two users reported a `leopardspeech-data` folder with the
    three expected directories inside it and no synthesizer in the list, and
    nothing -- not the add-on, not the log -- could say whether the host was
    missing, the tree was one level off, or the voices were unreadable.

    Kept next to the decision it describes so the two cannot drift apart.
    """
    lines = []
    ok = True

    lines.append("host: %s %s"
                 % (HOST_EXE, "found" if os.path.isfile(HOST_EXE)
                    else "MISSING"))
    if not os.path.isfile(HOST_EXE):
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
                 "MISSING -- Leopard's engine cannot load without it; take "
                 "usr/lib/libstdc++.6.0.4.dylib from the same install disc"))
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
        lines.append("no AAC decoder registered, so Vicki is withheld")
    return ok, lines


def usable():
    """-> True when there is an engine we could actually speak with."""
    if not os.path.isfile(HOST_EXE):
        return False
    tree = find_tree()
    if not tree:
        return False
    mt, sd, voices = engine_paths(tree)
    if not find_libstdcxx(tree):
        return False
    return (os.path.isfile(mt) and os.path.isfile(sd)
            and bool(read_voices(voices, playable_only=True)))
