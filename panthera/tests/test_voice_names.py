# -*- coding: utf-8 -*-
"""A voice is named after its folder, and the folder is the user's to name.

**Tomi's ask for 1.0**, and the shape of it is worth writing down because the
obvious version of it is wrong.

Until now the list showed the name inside each bundle's `VoiceDescription`.
That is fine while everybody has exactly the voices Apple shipped -- and stops
being fine the moment somebody has two banks of one voice, which this project
allows without advertising.  A second Alex dropped in beside Apple's arrives
carrying the same descriptor name, and the list offers "Alex" twice with
nothing to choose between them.  Named after the folder, one of them can be
`alex-compact` and the question answers itself.

It also makes the list agree with two places that were already using folder
names and quietly disagreeing with it: the `voice=` line NVDA writes into its
config, and the speech data manager's count of what is installed.

**What must not change is the descriptor check.**  The tempting simplification
-- name it from the folder, so stop opening the file -- would list all
twenty-eight of Nuance's `*Compact` bundles as voices the driver then fails to
speak.  They have no `VoiceDescription` at all, and that absence is both the
filter that keeps them out and the only thing that routes a real voice to an
engine.  The old comment in `read_voices` warned against "fixing" it to name
from the folder; the fix was to do that *and* keep reading the file.

These run against fabricated bundles rather than a real install, so they say
the same thing on a machine with no Mac OS X data at all.
"""
import os

import pytest

from synthDrivers._panthera import pantheratrees

#: A `VoiceDescription` is a fixed 80-byte header: the creator OSType at +4,
#: and a `Str63` -- one length byte then the characters -- at +16.  A `version`
#: long sits between the VoiceSpec and the name, which is what makes it +16
#: rather than +12, and reading it as +12 yields empty strings.
def _descriptor(creator, name):
    head = bytearray(80)
    head[4:8] = creator.encode("latin-1")
    head[16] = len(name)
    head[17:17 + len(name)] = name.encode("mac-roman")
    return bytes(head)


def _voice(voicesdir, bundle, creator="mtk3", name=None):
    folder = os.path.join(voicesdir, bundle + ".SpeechVoice",
                          "Contents", "Resources")
    os.makedirs(folder)
    with open(os.path.join(folder, "VoiceDescription"), "wb") as f:
        f.write(_descriptor(creator, bundle if name is None else name))


def _nameless(voicesdir, bundle):
    """A Vocalizer bundle: everything but the descriptor."""
    os.makedirs(os.path.join(voicesdir, bundle + ".SpeechVoice",
                             "Contents", "Resources"))


@pytest.fixture
def voicesdir(tmp_path):
    d = tmp_path / "Voices"
    d.mkdir()
    return str(d)


def test_the_name_shown_is_the_folders_not_the_descriptors(voicesdir):
    """Three of Apple's own differ, and they are the whole visible change.

    `BadNews`, `GoodNews` and `Organ` read "Bad News", "Good News" and "Pipe
    Organ" inside.  Everything else on a stock install already agreed.
    """
    _voice(voicesdir, "BadNews", name="Bad News")
    _voice(voicesdir, "Organ", name="Pipe Organ")
    got = {v[0]: v[1] for v in pantheratrees.read_voices(voicesdir)}
    assert got == {"BadNews": "BadNews", "Organ": "Organ"}


def test_the_name_and_the_stored_id_are_the_same_string(voicesdir):
    """Which is the point: NVDA writes the id, and now the user can read it.

    The id was always the folder name, so nobody's stored voice moves.
    """
    _voice(voicesdir, "Alex", creator="meow", name="Alex Premium")
    (bundle, shown, _engine), = pantheratrees.read_voices(voicesdir)
    assert bundle == shown == "Alex"


def test_two_banks_of_one_voice_are_told_apart_by_their_folders(voicesdir):
    """The reason for the change, in one test.

    Both descriptors say "Alex".  Under the old rule the list offered "Alex"
    twice; the user could pick either and had no way to know which was
    speaking.
    """
    _voice(voicesdir, "Alex", creator="meow", name="Alex")
    _voice(voicesdir, "alex-compact", creator="meow", name="Alex")
    shown = [v[1] for v in pantheratrees.read_voices(voicesdir)]
    assert sorted(shown) == ["Alex", "alex-compact"]
    assert len(set(shown)) == 2, shown


def test_a_bundle_with_no_descriptor_is_still_left_out(voicesdir):
    """**The check the folder-naming change must not take with it.**

    Every Nuance `*Compact` voice lands here, and so would a half-copied
    bundle.  Naming from the folder without opening the file would list all
    twenty-eight of them as voices that then refuse to speak.
    """
    _voice(voicesdir, "Fred")
    _nameless(voicesdir, "AlexCompact")
    _nameless(voicesdir, "SamanthaCompact")
    assert [v[0] for v in pantheratrees.read_voices(voicesdir)] == ["Fred"]


def test_a_half_written_descriptor_is_left_out_too(voicesdir):
    """Eighty bytes or it is not a descriptor.

    A bundle still being copied has a short one, and reading a creator out of
    it would route the voice to whichever engine four arbitrary bytes named.
    """
    _voice(voicesdir, "Fred")
    folder = os.path.join(voicesdir, "Half.SpeechVoice", "Contents",
                          "Resources")
    os.makedirs(folder)
    with open(os.path.join(folder, "VoiceDescription"), "wb") as f:
        f.write(_descriptor("mtk3", "Half")[:40])
    assert [v[0] for v in pantheratrees.read_voices(voicesdir)] == ["Fred"]


def test_concatenative_voices_still_come_first(voicesdir):
    """Kept from before the change, and deliberately not "sorted by folder".

    The list is a menu a blind user arrows through one item at a time, and the
    novelty voices are not what anyone came for.  Folder order is what decides
    it *within* a group.
    """
    _voice(voicesdir, "Zarvox")
    _voice(voicesdir, "Alex", creator="meow")
    _voice(voicesdir, "Bruce", creator="gala")
    _voice(voicesdir, "Fred")
    assert [v[0] for v in pantheratrees.read_voices(voicesdir)] == [
        "Alex", "Bruce", "Fred", "Zarvox"]


def test_a_lower_case_folder_sorts_where_a_reader_would_look_for_it(voicesdir):
    """Case-insensitively, because the names are now the user's to choose.

    Apple's are all capitalised, so this never came up before: under a
    case-sensitive sort every lower-case folder lands after `Zarvox`, which is
    nowhere anyone would look for it.
    """
    for bundle in ("Zarvox", "banana", "Apple"):
        _voice(voicesdir, bundle)
    assert [v[0] for v in pantheratrees.read_voices(voicesdir)] == [
        "Apple", "banana", "Zarvox"]


def test_every_generation_names_voices_the_same_way(voicesdir):
    """One reader, four callers -- and the wrappers must not diverge.

    Each tree module keeps a two-line `read_voices` so that `explain()` and
    the tests can replace `aac_available` on it.  Two lines is little enough
    to get wrong quietly, so this checks all four give the same answer.
    """
    from synthDrivers._panthera import pantheraleopard
    from synthDrivers._panthera import pantheralion
    from synthDrivers._panthera import pantherasnowleopard
    from synthDrivers._panthera import pantheratiger
    _voice(voicesdir, "GoodNews", name="Good News")
    _voice(voicesdir, "Alex", creator="meow", name="Alex")
    expected = [("Alex", "Alex", "meow"), ("GoodNews", "GoodNews", "mtk3")]
    for tree in (pantheratiger, pantheraleopard, pantherasnowleopard,
                 pantheralion):
        assert tree.read_voices(voicesdir) == expected, tree.__name__
