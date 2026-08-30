# -*- coding: utf-8 -*-
"""Which speech-data folder the SAPI tool will offer to move, and which it won't.

The SAPI data moves to `%ProgramData%`; NVDA's data does not.  That reads as
an inconsistency and is not one: a portable NVDA copy carries its own
configuration folder, so data kept inside it travels and data outside it is
silently lost -- while SAPI has no portable copy to protect and every account
on the machine needs to read one copy.  On the NVDA side the driver only
*adds* `%ProgramData%` to the places it looks; see `test_sapi_roots.py`.

So exactly one arrangement may be moved: the standalone per-user default,
`%APPDATA%\\macintalk-data`.  Getting that wrong is expensive in a direction
tests usually cannot see -- moving NVDA's own `macintalk` folder out of its
configuration directory is precisely what breaks speech on the Windows
sign-in screen, where NVDA reads a copy of that directory and nothing else,
and it would break it *silently*, months after the move, on a machine nobody
is looking at.

`settings.ps1 -ShowMigrationPlan` prints the classification and does nothing
else, so the decision can be held still without an elevation prompt, a
registry to write, or 1.6 GB to move.  `-DataRoot` pins the resolved root,
which is what keeps this test independent of whatever the machine running it
happens to have in HKCU.
"""
import os
import subprocess
import sys

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SETTINGS = os.path.join(os.path.dirname(os.path.dirname(_HERE)), "sapi",
                         "settings.ps1")

pytestmark = pytest.mark.skipif(sys.platform != "win32",
                                reason="the SAPI tool is Windows PowerShell")


def _plan(root, appdata=None, programdata=None):
    """-> the plan `settings.ps1` prints for `root`, as a dict.

    The environment is built explicitly rather than inherited so that a
    variable can be *absent*, which is one of the cases -- a machine with no
    `%ProgramData%` has nowhere machine-wide to move anything to.
    """
    env = dict(os.environ)

    def drop(*names):
        # Windows upper-cases the keys of `os.environ`, so removing
        # "ProgramData" by the name PowerShell spells it removes nothing --
        # which is how this helper first reported that a machine with no
        # `%ProgramData%` offers to migrate.  It does not; the test was
        # deleting a key that was not there.
        wanted = {n.casefold() for n in names}
        for key in [k for k in env if k.casefold() in wanted]:
            del env[key]

    for name, value in (("APPDATA", appdata), ("ProgramData", programdata)):
        if value is None:
            drop(name, "ALLUSERSPROFILE") if name == "ProgramData" else drop(name)
        else:
            drop(name)
            env[name] = value
    out = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-STA",
         "-File", _SETTINGS, "-ShowMigrationPlan", "-DataRoot", root],
        capture_output=True, text=True, env=env, timeout=120)
    assert out.returncode == 0, out.stderr
    plan = {}
    for line in out.stdout.splitlines():
        if ": " in line:
            key, _, value = line.partition(": ")
            plan[key.strip()] = value.strip()
    assert "plan" in plan, out.stdout
    return plan


def _tree(base, *voices):
    """A folder shaped enough like speech data for `Get-Voices` to count it."""
    for voice in voices or ("Alex",):
        os.makedirs(os.path.join(base, "Lion", "Speech", "Voices",
                                 voice + ".SpeechVoice"))
    return base


def test_the_per_user_default_with_data_in_it_moves(tmp_path):
    """The one case there is, and the only one that costs an elevation prompt."""
    appdata = str(tmp_path / "roaming")
    root = _tree(os.path.join(appdata, "macintalk-data"))
    plan = _plan(root, appdata=appdata, programdata=str(tmp_path / "common"))
    assert plan["plan"] == "migrate"
    assert plan["to"] == os.path.join(str(tmp_path / "common"), "macintalk-data")


def test_nvdas_own_folder_is_never_moved(tmp_path):
    """Moving this is what breaks the sign-in screen.  It is not offered.

    NVDA copies its whole configuration directory to `systemConfig` for the
    secure desktop, `macintalk/` included; data outside that directory is
    reachable from the sign-in screen by nothing.
    """
    appdata = str(tmp_path / "roaming")
    root = _tree(os.path.join(appdata, "nvda", "macintalk"))
    plan = _plan(root, appdata=appdata, programdata=str(tmp_path / "common"))
    assert plan["plan"] == "nvda"


def test_a_folder_somebody_chose_is_left_where_they_put_it(tmp_path):
    root = _tree(str(tmp_path / "my-voices"))
    plan = _plan(root, appdata=str(tmp_path / "roaming"),
                 programdata=str(tmp_path / "common"))
    assert plan["plan"] == "chosen"


def test_data_already_in_the_machine_wide_folder_is_done(tmp_path):
    common = str(tmp_path / "common")
    root = _tree(os.path.join(common, "macintalk-data"))
    plan = _plan(root, appdata=str(tmp_path / "roaming"), programdata=common)
    assert plan["plan"] == "done"


def test_an_empty_per_user_folder_has_nothing_to_move(tmp_path):
    """Fresh installs resolve here before extracting anything.

    Offering to move an empty folder would be an elevation prompt bought with
    nothing, on the first launch, before the person has done a thing.
    """
    appdata = str(tmp_path / "roaming")
    root = os.path.join(appdata, "macintalk-data")
    os.makedirs(root)
    plan = _plan(root, appdata=appdata, programdata=str(tmp_path / "common"))
    assert plan["plan"] == "none"


def test_no_machine_wide_folder_means_no_offer(tmp_path):
    """A machine with no `%ProgramData%` has nowhere to move anything to."""
    appdata = str(tmp_path / "roaming")
    root = _tree(os.path.join(appdata, "macintalk-data"))
    plan = _plan(root, appdata=appdata, programdata=None)
    assert plan["plan"] == "none"


# ---------------------------------------------------------------------------
# Where the tool *resolves* its data root, with nothing pinned.
#
# The tokens are the reason this matters more here than in the add-on.  The
# add-on searches on every start; a SAPI token carries a DataPath written once,
# at registration.  Move the folder and all 96 still name the old one -- SAPI
# lists the voices, hands them text, and the engine renders nothing.  Measured
# on Tomi's Rog from the sign-in screen: 24 utterances, each returning its
# bookmark in 21-23 ms flat regardless of length, where working voices on the
# same screen took 216 to 2164 ms.  A constant is not slow rendering, it is no
# rendering, and no log line said so.
#
# So the tool has to be able to *find* the moved folder before it can offer to
# re-register against it.
# ---------------------------------------------------------------------------

def _resolved(appdata, programdata, tmp_path):
    """-> the root `settings.ps1` resolves with nothing pinned."""
    plan = _plan_unpinned(appdata, programdata)
    return plan["from"]


def _requireNoChosenFolder():
    """Skip when this machine has an HKCU DataPath that resolves.

    These four cannot pass `-DataRoot`, because the resolution *is* what they
    test -- so a remembered folder on the machine running them would win and
    the assertion would be about that instead.  Skipping says so out loud
    rather than failing mysteriously on somebody else's desk.
    """
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Panthera SAPI") as key:
            value, _kind = winreg.QueryValueEx(key, "DataPath")
    except OSError:
        return
    if value and os.path.isdir(value):
        pytest.skip("this machine has a chosen data folder: %s" % value)


def _plan_unpinned(appdata, programdata):
    _requireNoChosenFolder()
    env = dict(os.environ)
    for key in [k for k in env
                if k.casefold() in ("appdata", "programdata", "allusersprofile")]:
        del env[key]
    env["APPDATA"] = appdata
    env["ProgramData"] = programdata
    out = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-STA",
         "-File", _SETTINGS, "-ShowMigrationPlan"],
        capture_output=True, text=True, env=env, timeout=120)
    assert out.returncode == 0, out.stderr
    plan = {}
    for line in out.stdout.splitlines():
        if ": " in line:
            key, _, value = line.partition(": ")
            plan[key.strip()] = value.strip()
    return plan


def test_nvdas_folder_moved_to_programdata_is_found(tmp_path):
    """`%ProgramData%\macintalk` -- NVDA's folder name, machine-wide.

    Not `macintalk-data`, which is the SAPI installer's name and the only one
    this tool knew.  One word apart, and the voices went silent.
    """
    appdata = str(tmp_path / "roaming")
    os.makedirs(appdata)
    common = str(tmp_path / "common")
    _tree(os.path.join(common, "macintalk"))
    assert _resolved(appdata, common, tmp_path) == os.path.join(
        common, "macintalk")


def test_this_users_nvda_folder_still_wins_over_the_machines(tmp_path):
    """A per-user NVDA folder is this person's own and outranks the shared one."""
    appdata = str(tmp_path / "roaming")
    _tree(os.path.join(appdata, "nvda", "macintalk"))
    common = str(tmp_path / "common")
    _tree(os.path.join(common, "macintalk"))
    assert _resolved(appdata, common, tmp_path) == os.path.join(
        appdata, "nvda", "macintalk")


def test_the_sapi_folder_is_still_resolved_when_it_is_the_only_one(tmp_path):
    """The name that already worked must keep working."""
    appdata = str(tmp_path / "roaming")
    os.makedirs(appdata)
    common = str(tmp_path / "common")
    _tree(os.path.join(common, "macintalk-data"))
    assert _resolved(appdata, common, tmp_path) == os.path.join(
        common, "macintalk-data")
