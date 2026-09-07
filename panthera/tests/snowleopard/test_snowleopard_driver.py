# -*- coding: utf-8 -*-
"""The Snow Leopard driver, from the outside.

Deliberately short, for the reason Lion's is: everything the driver *does* is
`pantheradriver`, which Leopard's hundred and forty tests exercise line for
line, and repeating them here would test the same code twice and drift the
second copy.  What is left is what is genuinely 10.6's -- which voices it
offers, when it appears at all, which C++ runtime it needs, and that the
engine behind it really speaks.
"""
import struct
import time

import pytest


def test_the_driver_is_named_what_nvda_stores_settings_under():
    """A rename resets everybody's voice, rate and pitch.  Pin it."""
    import snowleopardspeech
    assert snowleopardspeech.SynthDriver.name == "snowleopardspeech"


def test_snow_leopard_is_its_own_folder_and_not_a_neighbours():
    """The failure this guards against shipped once, in the other direction.

    Leopard read `tigerspeech-data`, ran `tiger_host.exe` and offered Tiger's
    voices under Leopard's name.  Nothing failed; a user noticed the wrong
    voices.  10.6 makes it easier still: it speaks Leopard's calls with Lion's
    threading, so a tree of either neighbour would get some way in before
    anything went visibly wrong.
    """
    import pantheraleopard
    import pantheralion
    import pantherasnowleopard
    here = pantherasnowleopard.CONFIG_DIRNAME
    assert here.endswith("snowleopard")
    assert here not in (pantheraleopard.CONFIG_DIRNAME,
                        pantheralion.CONFIG_DIRNAME)


def test_the_runtime_is_required_and_the_abi_library_is_not(tmp_path,
                                                            monkeypatch):
    """**The inverse of Lion's test, and the reason both exist.**

    Lion needs `libc++abi.dylib` beside libstdc++ and refuses without it, so
    a tree missing it is caught up front rather than misbehaving later.  Snow
    Leopard's 6.0.9 implements the ABI itself -- the way Leopard's 6.0.4 does
    -- and there is no such file on the disc.  Requiring one here would refuse
    every correctly extracted 10.6 tree there is.

    The two libraries share a version number and a file name and are not the
    same library, which is why this is worth a test rather than a comment.
    """
    import pantherasnowleopard as sl
    tree = tmp_path / "snowleopard"
    (tree / "Speech" / "Voices").mkdir(parents=True)
    monkeypatch.setattr(sl, "config_base", lambda: str(tmp_path))
    monkeypatch.setenv("SNOWLEOPARD_TREE", str(tree))
    assert sl.find_tree() == str(tree)

    assert sl.find_libstdcxx(str(tree)) is None
    ok, lines = sl.explain()
    assert not ok
    assert any("libstdc++" in ln and "MISSING" in ln for ln in lines), lines
    # And it says which disc to take it from, because the wrong one has the
    # same name and loads.
    assert any("Lion's file of that name" in ln for ln in lines), lines

    (tree / "libstdc++.6.0.9.dylib").write_bytes(b"not really")
    assert sl.find_libstdcxx(str(tree))
    assert not hasattr(sl, "find_libcxxabi"), (
        "10.6 has no libc++abi and asking for one would refuse every real "
        "tree")


def test_a_generation_with_no_data_is_not_in_the_list(tmp_path, monkeypatch):
    """Four generations is two too many to arrow past when two are mute.

    Safe only because the Tools menu report still names every generation,
    hidden ones included, so there is somewhere to find out why -- which is
    exactly what hiding used to cost.
    """
    import pantherasnowleopard as sl
    import snowleopardspeech
    monkeypatch.delenv("SNOWLEOPARD_TREE", raising=False)
    monkeypatch.setattr(sl, "config_base", lambda: str(tmp_path))
    assert sl.find_tree() is None
    assert not snowleopardspeech.SynthDriver.check()


# -- the engine ----------------------------------------------------------

def test_the_voice_list_is_the_twenty_four_that_speak(engine_tree):
    """2 concatenative, 3 MacinTalk Pro and 19 MacinTalk 3 -- as 10.5 and 10.7."""
    import snowleopardspeech
    _, _, voicesdir = snowleopardspeech.engine_paths(engine_tree)
    voices = snowleopardspeech.read_voices(voicesdir)
    engines = {}
    for _bundle, _name, engine in voices:
        engines[engine] = engines.get(engine, 0) + 1
    assert engines == {"meow": 2, "gala": 3, "mtk3": 19}, engines
    # Alex first: the list is a menu read one item at a time, and he is what
    # the generation is for.
    assert voices[0][0] == "Alex", voices[0]


def test_alex_speaks(driver):
    """The whole point.  Two bugs between the engine loading and this line."""
    voices = {v[0] for v in driver._voices}
    assert "Alex" in voices, sorted(voices)
    driver._set_voice("Alex")
    pcm = driver._render("Hello there, this is Alex on Snow Leopard.", 180,
                         "Alex")
    assert pcm, "Alex rendered nothing at all"
    # Two thirds of Lion's Alex was once literal zero, and it still
    # transcribed well enough to fool a listener.  Count the silence, not the
    # words.
    zeros = sum(1 for i in range(0, len(pcm) - 1, 2)
                if pcm[i] == 0 and pcm[i + 1] == 0)
    frames = len(pcm) // 2
    assert frames > 22050, "less than a second of audio for that sentence"
    assert zeros < frames * 0.25, (
        "%.1f%% of the render is silence" % (100.0 * zeros / frames))


def test_fred_speaks_too(driver):
    """A formant voice, so the concatenative path is not the only one tested."""
    pcm = driver._render("Hello there!", 180, "Fred")
    assert pcm and len(pcm) > 4410, len(pcm) if pcm else 0


def test_every_voice_speaks_in_one_session(driver):
    """**The test the dispatch-source bug would have failed.**

    10.6 creates a GCD source per unit of work and cancels it again -- about
    ninety-five for one long utterance -- where 10.7 keeps a couple for the
    whole session.  A source table that only ever counted up therefore lasted
    exactly one utterance, and everything after it was a bare handle no timer
    could attach to: one frame of audio, then -231 for ever.

    It presented as a voice-switching fault and is not one; the same voice
    eight times over fails just as well.  What it needs is *one host*, several
    long utterances -- which is what a person reading a timeline does, and
    what a fresh host per voice would never have caught.
    """
    text = ("Hello there. This is a test of the emergency broadcast system, "
            "and it carries on for long enough to need more than one worker.")
    short = []
    for bundle, _name, _engine in driver._voices:
        driver._set_voice(bundle)
        pcm = driver._render(text, driver._wpm(), bundle)
        if not pcm or len(pcm) // 2 < 4410:
            short.append("%s (%d frames)" % (bundle,
                                             len(pcm) // 2 if pcm else 0))
    assert not short, "rendered almost nothing: %s" % ", ".join(short)


def test_an_utterance_ends_when_the_engine_stops_rather_than_on_a_timeout(
        driver):
    """**10.6 stops its audio graph, so there is no fixed tail to pay.**

    10.7 never calls `AUGraphStop`, so a Lion utterance had nothing to end on
    but a silence window, at a flat 300 ms every time until 0.98.0 found the
    signal it does give.  10.6 ends the way 10.5 does, and this pins that:
    a word costs a tick, not a third of a second.

    Bounded generously -- 150 ms against a measured 10 to 40 -- because this
    is a wall-clock assertion on a machine that may be doing other things.
    What it is really watching for is a regression to the 300 ms floor.
    """
    driver._set_voice("Fred")
    driver._render("a", driver._wpm(), "Fred")          # warm the host
    best = min(_time_render(driver, "a") for _ in range(3))
    assert best < 150.0, (
        "a one-letter utterance took %.0f ms, which is the fixed-wait floor "
        "coming back" % best)


def _time_render(driver, text):
    started = time.time()
    driver._render(text, driver._wpm(), driver._voiceId)
    return (time.time() - started) * 1000.0


# -- the volume table ----------------------------------------------------

def _peak_and_clipped(pcm):
    v = struct.unpack("<%dh" % (len(pcm) // 2), pcm)
    return (max(max(v), -min(v)),
            sum(1 for x in v if x >= 32766 or x <= -32767))


def test_the_volume_table_covers_every_voice_we_offer(driver):
    """An unmeasured voice silently gets 1.0, which is quiet but never wrong.

    Worth failing on anyway: a voice added later should be measured rather
    than left behind at the old level while everything around it got louder.
    """
    import snowleopardspeech
    missing = [entry[0] for entry in driver._voices
               if entry[0] not in snowleopardspeech.VOLUME_NORM]
    assert not missing, (
        "not in VOLUME_NORM, so they stay at the old level: %s -- run "
        "tools/volume_table.py snowleopard" % ", ".join(missing))


def test_the_levels_are_snow_leopards_and_not_a_neighbours():
    """**The generation that proves the tables must not be copied.**

    Twenty-three of these names are in Leopard's table and twenty-four in
    Lion's, so either would have looked reasonable.  Alex settles it: 1.46
    here, 1.80 on Leopard, 1.19 on Lion.  Snow Leopard's bank is the shrunken
    400 MB recording, so Leopard's factor asks for gain it does not have, and
    Lion's leaves nearly 2 dB of it unused.
    """
    import lionspeech
    import pantheradriver
    import snowleopardspeech
    here = snowleopardspeech.VOLUME_NORM
    assert here != pantheradriver.VOLUME_NORM_LEOPARD, "Leopard's, copied"
    assert here != lionspeech.VOLUME_NORM, "Lion's, copied"
    assert 1.3 < here["Alex"] < 1.6, (
        "Alex is at %.2f, which is a neighbour's figure rather than 10.6's"
        % here["Alex"])


def test_the_default_volume_never_clips_more_than_the_voice_already_did(
        driver):
    """What the table promises: never make a voice worse than it was.

    Not "nothing clips" -- a voice can already clip at its own natural level,
    and turning it down to fix that would cost more than the distortion does.
    A handful of voices rather than all twenty-four, because each is two real
    renders; these are the extremes.
    """
    driver._acceptCommands = True    # or the baseline prefix is stripped
    text = ("The US Chamber of Commerce warned Tuesday. Ah, oh, ooh, aye. "
            "WARNING! ERROR! Take a big pack of tickets, Bobby.")
    available = {v[0] for v in driver._voices}
    for voice in ("Alex", "Bruce", "Victoria", "Whisper", "Fred"):
        if voice not in available:
            continue
        driver._set_voice(voice)
        was = _peak_and_clipped(
            driver._render("[[volm 1.000]]" + text, driver._wpm(), voice))[1]
        driver._set_volume(90)
        peak, clipped = _peak_and_clipped(
            driver._render(text, driver._wpm(), voice))
        assert peak, "%s rendered nothing" % voice
        assert clipped <= was, (
            "%s clips %d samples at the default volume against %d at its own "
            "natural level -- VOLUME_NORM made it worse"
            % (voice, clipped, was))


def test_rate_reaches_the_engine(driver):
    """10.6 sets rate through `SESetSpeechInfo`, as 10.5 does and 10.7 does not.

    Worth a test rather than none: the host picks the property call by which
    entry point the engine exports, and 10.6 is exactly the generation that
    would silently take Lion's branch if that choice were ever made by
    version number instead.  It would still speak; it would just ignore the
    rate slider, which is how the same fault reached users once already.

    One driver and three rates, not three drivers: the comparison is between
    the three lengths, and a per-rate test could only assert that *something*
    came out.
    """
    driver._set_voice("Fred")
    text = "One two three four five six seven eight nine ten."
    lengths = [len(driver._render(text, wpm, "Fred") or b"")
               for wpm in (90, 180, 400)]
    assert all(lengths), "nothing rendered at one of the rates: %s" % lengths
    slow, mid, fast = lengths
    assert slow > mid > fast, lengths
