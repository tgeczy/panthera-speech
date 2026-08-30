# -*- coding: utf-8 -*-
"""The parts of finding an engine that do not vary by generation.

Four of these functions were carried in every tree module, and it was the
same code four times over rather than four versions of it -- identical down
to the comments, which is how you can tell nobody had *decided* to keep four
copies.  They were compared before being moved, and the bodies were the same
in Tiger's, Leopard's and Lion's; Snow Leopard's would have been the fourth.

What stayed behind is what genuinely differs: where each generation looks,
what it calls its pointer files, which C++ runtime it needs, and what its
`explain()` has to say.  Those are the parts worth reading four times.

**The tree modules keep a two-line `read_voices` of their own** rather than
re-exporting this one.  `explain()` and the tests both reach for
`tree.aac_available` and `tree.PLAYABLE_ENGINES`, and a shared reader that
read *this* module's copies would quietly ignore a caller who had replaced
them -- which is precisely how a test passes while measuring the wrong thing.
The wrapper passes them in, so there is one answer and the caller chooses it.

Kept free of NVDA imports, like the modules it serves: the synthesizer, the
global plugin, the speech data manager and anything run from a command line
all need these, and only one of those four is inside NVDA.
"""
import os


def _config_from_own_path():
    """-> the configuration directory this file is installed under, or None.

    An add-on always lives at `<config>/addons/<name>/synthDrivers/_panthera/`,
    so four levels up from here *is* the configuration directory in use --
    whichever one that is, a portable copy and NVDA's `systemConfig` included.
    `addonHandler` fixes that shape, so this is derived rather than guessed.

    None unless the shape matches, so that running from the source tree or
    from a command line falls through to the ordinary answer.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    parts = here.split(os.sep)
    #        <config> / addons / <name> / synthDrivers / _panthera
    if len(parts) < 5 or parts[-4].lower() != "addons":
        return None
    root = os.sep.join(parts[:-4])
    return root if os.path.isdir(root) else None


def config_base():
    """NVDA's user configuration directory, or a stand-in outside NVDA.

    `globalVars.appArgs.configPath` is normally the only correct source. NVDA's
    own `NVDAState.WritePaths.configDir` is a property wrapping exactly this
    value, so it already accounts for a portable copy and for a configuration
    directory given on the command line with `-c`. Expanding `%APPDATA%`
    ourselves would be right on one machine and wrong on every portable one.

    **Except inside NVDA's own 32-bit synth-driver host, where it is a lie.**
    `_bridge/runtimes/synthDriverHost/globalVars.py` is a stub, and it says

        appArgs.configPath = "."

    -- the host process's working directory, under a comment reading "very
    basic values to allow things to run". That host is how this add-on speaks
    on a secure screen, so there every generation looked for its engine in
    `./macintalk/<generation>`, found nothing, and reported that it had no
    speech data. Which is exactly what the first real sign-in screen did: all
    four synthesizers listed, and not one of them would load.

    So an answer that is not an absolute path is not believed, and the
    directory this add-on is installed in answers instead. `os.path.isabs(".")`
    is False, which is the whole of the test.

    The last fallback is for running outside NVDA -- the tests, and anything
    driven from a command line -- and for nothing else.
    """
    try:
        import globalVars
        path = globalVars.appArgs.configPath
        if path and os.path.isabs(str(path)):
            return str(path)
        own = _config_from_own_path()
        if own:
            return own
        if path:
            return str(path)
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), ".nvda")


def is_tree(path):
    return bool(path) and os.path.isdir(os.path.join(path, "Speech", "Voices"))


def sapi_roots(generation):
    """-> where the SAPI driver would keep `generation`'s tree, as candidates.

    The SAPI settings tool resolves its data root as: the folder the person
    chose (remembered in HKCU), then NVDA's shared macintalk folder, then a
    standalone default -- and it reads NVDA's folder precisely so that an
    NVDA-first user never needs the data twice.  This is the same courtesy
    pointed the other way: someone who installed the SAPI driver first and
    extracted into its world has a tree the add-on should simply find.

    The chosen folder is looked up live rather than cached, because the
    settings tool can change it while NVDA is running.  Case of the
    generation folder does not matter on Windows, so the SAPI side's
    title-case folders match these lowercase names as they are.
    """
    roots = []
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Panthera SAPI") as key:
            value, kind = winreg.QueryValueEx(key, "DataPath")
            if kind == winreg.REG_SZ and value:
                roots.append(os.path.join(value, generation))
    except OSError:
        pass
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(os.path.join(appdata, "macintalk-data", generation))
    return roots


def find_runtime(tree, names):
    """-> the first of `names` present at the root of `tree` or in usr/lib."""
    for sub in ("", os.path.join("usr", "lib")):
        for name in names:
            p = (os.path.join(tree, sub, name) if sub
                 else os.path.join(tree, name))
            if os.path.isfile(p):
                return p
    return None


#: Engines the host can render with.  The same three in every generation from
#: 10.4 to 10.7: `mtk3` is MacinTalk 3 and its novelty voices, `gala` is
#: MacinTalk Pro, and `meow` is the concatenative pair.  A generation that
#: ever differs overrides this in its own module rather than editing it here.
PLAYABLE_ENGINES = ("mtk3", "gala", "meow")

#: The AAC decoder the concatenative banks need, by class id.  Checking the
#: registration is cheaper and quieter than starting the host to ask, and it
#: is the same test the host's own `CoCreateInstance` will make a moment later.
_AAC_CLSID = r"CLSID\{32D186A7-218F-4C75-8876-DD77273A8999}"


def aac_available():
    """-> True when Windows has an AAC decoder for the `meow` sample banks.

    Windows N and KN editions ship without one until the Media Feature Pack is
    installed.  Absent from *both* registry views is a clear answer; anything
    else -- no registry at all, an unexpected error -- is not, and says yes,
    because losing a voice to a failed check is the smaller mistake and the
    driver still has to survive a host that renders silence.

    What it costs varies by generation.  On Tiger it is Vicki alone; from
    Leopard on it is Alex as well, which is the voice these add-ons exist for.
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


#: Concatenative voices first: the voice list is a menu a blind user arrows
#: through one item at a time, and the novelty voices are not what anyone came
#: for.  Within a group, folder order.
_ENGINE_ORDER = {"meow": 0, "gala": 1, "mtk3": 2}

_SUFFIX = ".SpeechVoice"


def read_voices(voicesdir, playable_only=False, aac=None, playable=None):
    """-> [(bundleName, displayName, engine), ...] read from the user's install.

    Built from the files rather than a table, so it cannot disagree with what
    is actually there.

    **A voice is named after its folder, not after the name inside it.**  The
    two agree for all but three of Apple's own -- `BadNews`, `GoodNews` and
    `Organ`, whose descriptors read "Bad News", "Good News" and "Pipe Organ" --
    so on a stock install this is nearly invisible.  It stops being invisible
    the moment somebody has two banks of the same voice, which is the whole
    reason for it: a second Alex dropped in beside Apple's arrives with the
    same descriptor name and the list would offer "Alex" twice, with nothing
    to tell them apart.  Named `alex-compact`, it says which one it is.

    That also makes the name the user's to choose, because the folder is
    theirs to rename, and it makes this list agree with the two places that
    were already using folder names: the config file NVDA stores the choice in,
    and the speech data manager's count of what is installed.

    **`VoiceDescription` still has to be there, and it still has to be 80
    bytes.**  It is not being read for the name any more, but it is what
    routes a voice to an engine, and its absence is the filter that keeps
    Nuance's Vocalizer bundles out -- they carry no descriptor at all.
    Naming from the folder without this check would list all twenty-eight of
    them as voices the driver then fails to speak.  The creator OSType is at
    +4; the descriptor is read in full rather than in part so that a
    half-copied bundle is skipped rather than misread.
    """
    out = []
    playable = playable or PLAYABLE_ENGINES
    if playable_only and not (aac or aac_available)():
        playable = tuple(e for e in playable if e != "meow")
    try:
        entries = sorted(os.listdir(voicesdir))
    except OSError:
        return out
    for entry in entries:
        if not entry.endswith(_SUFFIX):
            continue
        desc = os.path.join(voicesdir, entry, "Contents", "Resources",
                            "VoiceDescription")
        try:
            with open(desc, "rb") as f:
                head = f.read(80)
        except OSError:
            # Every Vocalizer voice lands here, and so would a half-copied
            # bundle.  Both are right to skip: without this file there is no
            # creator to route the voice to an engine.
            continue
        if len(head) < 80:
            continue
        engine = head[4:8].decode("latin-1")
        if playable_only and engine not in playable:
            continue
        bundle = entry[:-len(_SUFFIX)]
        out.append((bundle, bundle, engine))
    out.sort(key=lambda v: (_ENGINE_ORDER.get(v[2], 3), v[1].lower()))
    return out
