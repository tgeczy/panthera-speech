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


def common_base():
    """-> the machine-wide place for speech data, or None.

    `%ProgramData%`, named rather than hard-coded because a Windows install is
    not obliged to put it on C:.
    """
    return os.environ.get("ProgramData") or os.environ.get("ALLUSERSPROFILE")


def common_dir(config_dirname):
    """-> the machine-wide twin of `config_dir()`, or None.

    `%ProgramData%\\macintalk\\<generation>` -- the *same* folder name NVDA's
    configuration directory uses, which is the whole point.  The SAPI world's
    `macintalk-data` was already reachable through `sapi_roots`, and that near
    miss is what made this worth writing: Tomi moved his `macintalk` folder to
    `%ProgramData%`, restarted, and got "5 Macintosh speech engines are
    missing" -- because the only machine-wide root anybody looked in was
    spelled `macintalk-data`.  One folder name apart, and nothing said so.

    **A tree here is readable from the sign-in screen without being copied
    there.**  Data inside NVDA's configuration directory reaches the secure
    desktop only because NVDA copies that whole directory into `systemConfig`
    -- 1.6 GB of voice banks included, on every save.  An absolute path under
    `%ProgramData%` is read by SYSTEM directly.  The trade is the portable
    copy, which carries the configuration folder and nothing outside it.

    **This folder must not be writable by ordinary accounts.**  What
    `%ProgramData%` grants by inheritance is `BUILTIN\\Users:(CI)(WD,AD,...)`,
    and the host *maps and executes* the Mach-O in a tree, as SYSTEM, on the
    sign-in screen.  `sapi/settings.ps1` locks the ACL when it migrates; a
    folder made by hand has not been locked, and should be.
    """
    base = common_base()
    return os.path.join(base, config_dirname) if base else None


def tree_candidates(root):
    """-> `root` and its immediate subdirectories, for `is_tree` to sort out.

    One level down as well, because dropping the extracted folder in whole is
    what people actually do.  Absent or unreadable is an empty list rather
    than an error: this is a search, and a place that is not there simply is
    not one of the answers.
    """
    if not root:
        return []
    out = [root]
    try:
        out += [os.path.join(root, d) for d in sorted(os.listdir(root))
                if os.path.isdir(os.path.join(root, d))]
    except OSError:
        pass
    return out


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

    **A machine-wide answer is what makes a secure screen work.**  On the
    sign-in desktop NVDA runs as SYSTEM: `HKEY_CURRENT_USER` is SYSTEM's,
    `%APPDATA%` is SYSTEM's, and a tree the signed-in person extracted under
    their own profile is reachable through neither.  The SAPI side's voice
    tokens are registered in `HKLM` already, so they are visible there -- it
    is the *data* behind them that has not been.  A root under `%ProgramData%`
    is readable by every account on the machine, which is the whole point of
    putting one there.

    The per-user answers stay, and keep working, for everybody who already has
    a tree in one of them.
    """
    roots = []
    try:
        import winreg
    except ImportError:
        winreg = None

    def _fromRegistry(hiveName):
        """One hive's remembered folder, from both registry views.

        The hive is fetched by name rather than referenced directly so that a
        `winreg` without it -- a stand-in, or a future Python -- is simply a
        hive with nothing in it rather than an `AttributeError` that takes the
        whole lookup down.

        **Both views, because `HKLM\\Software` is redirected under WOW64 and
        `HKCU\\Software` is not.**  NVDA is 32-bit and would otherwise see
        `Wow6432Node` alone, so a machine-wide folder set by a 64-bit tool --
        or by somebody in `regedit` -- would be invisible here while being
        perfectly present.  The SAPI settings tool writes through both views
        for the same reason; this is the reading half of that agreement, and
        the same two-view dance `aac_available` already does below.
        """
        hive = getattr(winreg, hiveName, None)
        if hive is None:
            return
        access = getattr(winreg, "KEY_READ", 0)
        for view in (getattr(winreg, "KEY_WOW64_32KEY", 0),
                     getattr(winreg, "KEY_WOW64_64KEY", 0)):
            try:
                with winreg.OpenKey(hive, r"Software\Panthera SAPI", 0,
                                    access | view) as key:
                    value, kind = winreg.QueryValueEx(key, "DataPath")
            except OSError:
                continue
            if kind != winreg.REG_SZ or not value:
                continue
            candidate = os.path.join(value, generation)
            # The two views usually answer the same thing, and a folder
            # offered twice would have every caller looking in it twice.
            if candidate not in roots:
                roots.append(candidate)

    # The folder this user chose, then the one set for the machine.  Both are
    # explicit choices and outrank any default; the machine-wide one is the
    # only one of the two a secure screen can read.
    if winreg is not None:
        _fromRegistry("HKEY_CURRENT_USER")
        _fromRegistry("HKEY_LOCAL_MACHINE")

    #: `%ProgramData%`, the standalone default that survives the account
    #: changing underneath it.  Named rather than hard-coded because a Windows
    #: install is not obliged to put it on C:.
    common = os.environ.get("ProgramData") or os.environ.get("ALLUSERSPROFILE")
    if common:
        roots.append(os.path.join(common, "macintalk-data", generation))

    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(os.path.join(appdata, "macintalk-data", generation))
    return roots


def unreadable(paths):
    """-> (path, why) for the first folder that is there and will not open.

    A folder that exists and refuses to open is, everywhere else in this
    add-on, indistinguishable from a folder that was never there: `is_tree`
    asks `os.path.isdir` about `Speech\\Voices` inside it, and "access denied"
    and "not there" both answer False.  The generation then hides itself, the
    placeholder synthesizer stands in, and the person is told no speech data
    is installed -- a confident, wrong and unactionable answer for somebody
    whose data is sitting on the disk where they put it.

    Getting told the truth instead is what lets them act: the folder can be
    moved somewhere every account can read.  That is the one case Tomi
    described where a person would otherwise be left with no speech and no
    explanation, and it is reachable today by pointing the SAPI tool's data
    location at a folder under another profile.

    Absence stays silent, because absence is the ordinary case and this is
    only interesting when it is *not* what happened.
    """
    for path in paths:
        if not path:
            continue
        try:
            os.listdir(path)
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError as e:
            return path, (getattr(e, "strerror", None) or str(e))
    return None, None


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
