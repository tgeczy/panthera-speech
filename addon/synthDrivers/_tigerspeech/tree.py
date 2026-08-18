# -*- coding: utf-8 -*-
"""Find the engine the user supplied.

This add-on ships no part of Apple's speech software.  The user extracts it
from their own Mac OS X 10.4 install image -- `tools/extract_tiger.py` in the
repository does that -- and drops it somewhere we look.

**The engine lives in NVDA's configuration folder, not the add-on folder.**
Updating an add-on deletes and recreates its directory, so a few hundred
megabytes of extracted engine kept inside it would be destroyed on every
upgrade.

The folder is `tigerspeech-data` rather than anything shorter: it sits
directly in NVDA's configuration directory alongside every other add-on's
data, so a generic name would be a collision waiting to happen.

    tigerspeech-data/
        Speech/Synthesizers/MacinTalk.SpeechSynthesizer/
        Speech/Voices/<name>.SpeechVoice/
        SpeechDictionary.framework/Versions/A/

The extracted folder may also be dropped in whole, one level down, because
that is what people actually do.  And because a Tiger tree is large and often
kept on another drive, a text file of the same name works as a pointer.

Kept free of NVDA imports on purpose: the synthesizer and the global plugin
both need it, and so does anything run from a command line.
"""
import os

CONFIG_DIRNAME = "tigerspeech-data"

_HERE = os.path.dirname(os.path.abspath(__file__))
HOST_EXE = os.path.join(_HERE, "tiger_host.exe")


def config_base():
    """NVDA's user configuration directory, or a sensible stand-in."""
    try:
        import globalVars
        return globalVars.appArgs.configPath
    except Exception:
        return os.path.join(os.path.expanduser("~"), ".nvda")


def config_dir():
    """`<nvda user config>/tigerspeech-data`."""
    return os.path.join(config_base(), CONFIG_DIRNAME)


def is_tree(path):
    return bool(path) and os.path.isdir(os.path.join(path, "Speech", "Voices"))


def find_tree():
    """-> the directory holding Speech/ and SpeechDictionary.framework, or None.

    Deliberately quiet about failure.  The synthesizer reports itself as
    unavailable rather than appearing and then being silent.
    """
    home = config_dir()
    cands = []

    env = os.environ.get("TIGER_TREE")
    if env:
        cands.append(env)

    cands.append(home)
    try:
        cands += [os.path.join(home, d) for d in sorted(os.listdir(home))
                  if os.path.isdir(os.path.join(home, d))]
    except OSError:
        pass

    for pointer in (home + ".txt",
                    os.path.join(config_base(), "tiger-tree.txt")):
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


def usable():
    """-> True when there is an engine we could actually speak with."""
    if not os.path.isfile(HOST_EXE):
        return False
    tree = find_tree()
    if not tree:
        return False
    mt, sd, voices = engine_paths(tree)
    return (os.path.isfile(mt) and os.path.isfile(sd)
            and bool(read_voices(voices, playable_only=True)))
