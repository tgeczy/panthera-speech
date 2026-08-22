# -*- coding: utf-8 -*-
"""The Lion driver, from the outside.

Deliberately short.  Everything the driver *does* is `pantheradriver`, which
Leopard's hundred and forty tests already exercise line for line; repeating
them here would test the same code twice and drift the second copy.  What is
left is what is genuinely Lion's: which voices it offers, when it appears at
all, and that the engine behind it really speaks.
"""
import os

import pytest

import pantheradriver


def test_the_driver_is_named_what_nvda_stores_settings_under():
    """A rename resets everybody's voice, rate and pitch.  Pin it."""
    import lionspeech
    assert lionspeech.SynthDriver.name == "lionspeech"


def test_lion_is_its_own_folder_and_not_leopards():
    """The failure this guards against shipped once, in the other direction.

    Leopard read `tigerspeech-data`, ran `tiger_host.exe` and offered Tiger's
    voices under Leopard's name.  Nothing failed; a user noticed the wrong
    voices.  Two generations that speak the same calls make it easy to repeat.
    """
    import pantheralion
    import pantheraleopard
    assert pantheralion.CONFIG_DIRNAME != pantheraleopard.CONFIG_DIRNAME
    assert pantheralion.CONFIG_DIRNAME.endswith("lion")


def test_the_abi_library_is_required_as_well_as_the_runtime(tmp_path,
                                                            monkeypatch):
    """10.7 split the C++ ABI out of libstdc++, and half of it will not do.

    Without `libc++abi.dylib` the engine still *loads*: `__dynamic_cast` and
    the `__cxa_*` family resolve to nothing and every RTTI object gets a null
    vptr, so it misbehaves later and somewhere else.  Refusing up front is the
    only version of that failure anyone can act on.
    """
    import pantheralion
    tree = tmp_path / "lion"
    (tree / "Speech" / "Voices").mkdir(parents=True)
    monkeypatch.setattr(pantheralion, "config_base", lambda: str(tmp_path))
    monkeypatch.setenv("LION_TREE", str(tree))
    assert pantheralion.find_tree() == str(tree)

    (tree / "libstdc++.6.0.9.dylib").write_bytes(b"not really")
    assert pantheralion.find_libstdcxx(str(tree))
    assert pantheralion.find_libcxxabi(str(tree)) is None
    assert not pantheralion.usable()

    ok, lines = pantheralion.explain()
    assert not ok
    assert any("libc++abi" in ln and "MISSING" in ln for ln in lines), lines


def test_a_generation_with_no_data_is_not_in_the_list(tmp_path, monkeypatch):
    """Tomi's call, and the opposite of what Tiger and Leopard do.

    Four generations is two too many to arrow past when only two of them can
    speak.  It is safe here only because the Tools menu report still names
    every generation, hidden ones included, so there is somewhere to find out
    why -- which is exactly what hiding used to cost.
    """
    import lionspeech
    import pantheralion
    monkeypatch.delenv("LION_TREE", raising=False)
    monkeypatch.setattr(pantheralion, "config_base", lambda: str(tmp_path))
    assert pantheralion.find_tree() is None
    assert not lionspeech.SynthDriver.check()


# -- the engine ----------------------------------------------------------

def test_the_vocalizer_voices_are_not_offered(engine_tree):
    """The twenty-eight `*Compact` voices are Nuance Vocalizer, and out.

    Out on a decision about what this project is, not on capability.  Nothing
    filters them by name: they carry no `VoiceDescription`, so `read_voices`
    can find neither a creator nor a name for them and passes them over.

    This test exists because that is a coincidence of Apple's packaging rather
    than an intention in our code, and someone reading `read_voices` could
    reasonably "fix" it to fall back to the folder name.
    """
    import lionspeech
    _, _, voicesdir = lionspeech.engine_paths(engine_tree)
    on_disk = [d[:-len(".SpeechVoice")] for d in os.listdir(voicesdir)
               if d.endswith(".SpeechVoice")]
    compact = [n for n in on_disk if n.endswith("Compact")]
    assert compact, ("no Compact voices in this tree, so this proves nothing "
                     "-- extract without --compact and try again")

    offered = {v[0] for v in lionspeech.read_voices(voicesdir)}
    assert not (offered & set(compact)), sorted(offered & set(compact))


def test_the_voice_list_is_the_twenty_four_that_speak(engine_tree):
    """2 concatenative, 3 MacinTalk Pro and 19 MacinTalk 3."""
    import lionspeech
    _, _, voicesdir = lionspeech.engine_paths(engine_tree)
    voices = lionspeech.read_voices(voicesdir)
    engines = {}
    for _bundle, _name, engine in voices:
        engines[engine] = engines.get(engine, 0) + 1
    assert engines == {"meow": 2, "gala": 3, "mtk3": 19}, engines
    # Alex first: the list is a menu read one item at a time, and he is what
    # the generation is for.
    assert voices[0][0] == "Alex", voices[0]


def test_alex_speaks(driver):
    """The whole point.  Ten bugs between binding the engine and this line."""
    voices = {v[0] for v in driver._voices}
    assert "Alex" in voices, sorted(voices)
    driver._set_voice("Alex")
    pcm = driver._render("Hello there, this is Alex on Lion.", 180, "Alex")
    assert pcm, "Alex rendered nothing at all"
    # Two thirds of him used to be literal zero, and it still transcribed
    # well enough to fool a listener.  Count the silence, not the words.
    zeros = sum(1 for i in range(0, len(pcm) - 1, 2)
                if pcm[i] == 0 and pcm[i + 1] == 0)
    frames = len(pcm) // 2
    assert frames > 22050, "less than a second of audio for that sentence"
    assert zeros < frames * 0.25, (
        "%.1f%% of the render is silence -- the converter is short-changing "
        "the request again" % (100.0 * zeros / frames))


def test_fred_speaks_too(driver):
    """A formant voice, so the concatenative path is not the only one tested."""
    pcm = driver._render("Hello there!", 180, "Fred")
    assert pcm and len(pcm) > 4410, len(pcm) if pcm else 0
