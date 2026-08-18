# -*- coding: utf-8 -*-
"""What a user would notice, and what previously reached one.

Every test below corresponds to a rule at the top of `tigerspeech.py`, and
every rule was paid for in the sibling ROM add-on.  Porting the rules without
porting their tests would have been the same mistake in a new repository.
"""
import time

import pytest


def _settle(player, want_bytes, timeout=5.0):
    """Wait until the player has at least `want_bytes`, then return the total."""
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        if player.bytes >= want_bytes:
            return player.bytes
        time.sleep(0.005)
    return player.bytes


def _warm(driver):
    """One utterance first, so process startup is not charged to a latency
    measurement."""
    before = driver._player.bytes
    driver.speak(["warm"])
    _settle(driver._player, before + 1, timeout=20.0)
    time.sleep(0.05)


# -- things that need no engine ------------------------------------------

def test_every_offered_voice_actually_speaks(driver):
    """The one that matters most, and the one a user reported.

    Vicki's sample bank is AAC, decoded through a `SoundConverter` the host
    does not implement, so she renders the right number of frames of silence.
    Offering her mutes the screen reader -- and the user then cannot hear the
    voice list well enough to choose their way back out.

    So: whatever is in the list must make a sound.  No exceptions, because the
    person who finds the exception cannot see the dialog.
    """
    _warm(driver)
    silent = []
    for bundle, display, _engine in driver._voices:
        before = driver._player.bytes
        pcm = driver._render("testing", 180, bundle)
        if not pcm or max(abs(x) for x in
                          __import__("struct").unpack(
                              "<%dh" % (len(pcm) // 2), pcm)) == 0:
            silent.append(display)
    assert not silent, "offered but silent: %s" % ", ".join(silent)


def test_a_long_utterance_does_not_poison_the_engine(driver):
    """A user read a long post with Good News and lost speech until restart.

    The singing voices render several times more audio per character than the
    rest, so an ordinary social-media post reached the slice-spin guard -- and
    tripping that stopped completing slices, which is the engine's clock.  The
    channel then blocked mid-utterance: every later SESpeakBuffer returned
    -231 and the host died.  Silence that survives the next keystroke is the
    worst failure this driver has, because the user cannot hear their way out
    of it.

    Long text through a long-winded voice, then check the engine still works.
    """
    _warm(driver)
    long_text = ("Amir favourited your post. Wellp folks, the add-on is ready, "
                 "and just as with the other one it will find a home on "
                 "GitHub, running natively inside NVDA as its own code. "
                 "Archivists are advised to create a copy of the data file, "
                 "as this content will not be hosted for ever. ") * 2
    musical = [b for b, _d, _e in driver._voices
               if b in ("GoodNews", "Hysterical", "Bells", "Cellos", "Organ")]
    assert musical, "expected the singing voices to be present"

    for bundle in musical[:2]:
        pcm = driver._render(long_text, 304, bundle)
        assert pcm, "%s produced nothing for a long utterance" % bundle
        # And, crucially, the engine must still be usable afterwards.
        after = driver._render("Mentions. 1 of 1724", 304, bundle)
        assert after, "%s stopped speaking after a long utterance" % bundle
        assert driver._render("testing", 180, "Fred"), \
            "Fred stopped speaking after a long %s utterance" % bundle


def test_only_playable_engines_are_offered(tiger_tree):
    """Whatever reaches the list must be something the host can render."""
    import tree
    _mt, _sd, voicesdir = tree.engine_paths(tiger_tree)
    offered = tree.read_voices(voicesdir, playable_only=True)
    assert offered, "nothing offered at all"
    assert all(e in tree.PLAYABLE_ENGINES for _b, _d, e in offered)


def test_vicki_is_withheld_without_an_aac_decoder(tiger_tree, monkeypatch):
    """A Windows N install with no decoder must lose the voice, not the sound.

    Vicki is the one voice whose audio comes from outside the engine.  If the
    decoder is missing she renders silence, and a silent voice in this list
    mutes the screen reader for someone who then cannot hear their way back
    out -- so the list has to drop her instead.
    """
    import tree
    _mt, _sd, voicesdir = tree.engine_paths(tiger_tree)
    with_decoder = tree.read_voices(voicesdir, playable_only=True)
    assert any(e == "meow" for _b, _d, e in with_decoder), \
        "Vicki should be offered on a machine that can decode AAC"

    monkeypatch.setattr(tree, "aac_available", lambda: False)
    without = tree.read_voices(voicesdir, playable_only=True)
    assert not any(e == "meow" for _b, _d, e in without)
    assert len(without) == len(with_decoder) - 1
    # And the rest of the list is untouched: one voice lost, not a whole engine.
    assert {b for b, _d, _e in without} == \
        {b for b, _d, e in with_decoder if e != "meow"}


def test_voices_are_read_from_the_install(tiger_tree):
    """The list must come from the user's files, not a table that can drift."""
    import tigerspeech
    _mt, _sd, voicesdir = tigerspeech.engine_paths(tiger_tree)
    voices = tigerspeech.read_voices(voicesdir)
    assert voices, "no voices found"
    names = {display for _b, display, _e in voices}
    # Display names differ from bundle names; reading the bundle name instead
    # would silently give "Organ" and "BadNews".
    assert "Pipe Organ" in names or "Organ" not in {b for b, _d, _e in voices}
    engines = {e for _b, _d, e in voices}
    assert engines <= {"mtk3", "gala", "meow"}, engines


def test_missing_tree_refuses_to_load_rather_than_going_silent(monkeypatch,
                                                               tmp_path):
    """Selectable, and then it refuses -- which is not the same as silent.

    The synthesizer is always offered now, so that choosing it produces an
    explanation rather than an absence nobody could account for. What must
    never happen is the other failure: loading successfully and then saying
    nothing. So `__init__` has to raise, and it has to tell the user first.

    Patch `tree`, not the driver: the lookup lives there, so the global plugin
    that offers to open the folder gets exactly the same answer.
    """
    import tigerspeech
    import tree
    monkeypatch.delenv("TIGER_TREE", raising=False)
    monkeypatch.setattr(tree, "config_base", lambda: str(tmp_path))
    assert tree.find_tree() is None
    assert not tree.usable()

    ok, lines = tree.explain()
    assert not ok
    assert any("no tree found" in ln for ln in lines), lines

    # Offered, so that selecting it is a way to find out why.
    assert tigerspeech.SynthDriver.check()

    # And refuses to load, so NVDA falls back and speech carries on.
    told = []
    monkeypatch.setattr(tigerspeech, "_explainLater", told.append)
    with pytest.raises(Exception):
        tigerspeech.SynthDriver()
    assert told, "it refused to load and told the user nothing"


# -- the rules ------------------------------------------------------------

def test_queued_speech_is_not_dropped(driver):
    """Rule 2: one feed per utterance, and none of them vanish."""
    _warm(driver)
    fed = driver._player.fed
    for phrase in ("one", "two", "three"):
        driver.speak([phrase])
    # The fake player's idle() blocks for as long as the audio would sound, so
    # three utterances take real seconds to drain however fast rendering is.
    end = time.perf_counter() + 20.0
    while time.perf_counter() < end and driver._player.fed < fed + 3:
        time.sleep(0.01)
    assert driver._player.fed >= fed + 3, \
        "only %d of 3 utterances reached the player" % (driver._player.fed - fed)


def test_cancel_always_stops_the_player(driver):
    """Rule 4.  Interrupting is the whole job of cancel().

    Gating this on a flag that tracked the *worker* being busy once left
    interruption silently broken, because the worker goes idle while sound is
    still playing.
    """
    _warm(driver)
    driver.speak(["a sentence long enough that it is certainly still playing"])
    _settle(driver._player, driver._player.bytes + 1, timeout=20.0)
    before = driver._player.stops
    driver.cancel()
    assert driver._player.stops == before + 1, "cancel did not stop the player"


def test_cancel_leaves_the_driver_usable(driver):
    """Cancelling with nothing queued, repeatedly, must not wedge anything."""
    _warm(driver)
    for _ in range(20):
        driver.cancel()
    before = driver._player.bytes
    driver.speak(["still here"])
    assert _settle(driver._player, before + 1, timeout=20.0) > before


def test_never_goes_permanently_silent(driver):
    """Rule 3, and the worst failure this driver's ancestor ever had.

    A generation counter stamped items when queued and compared them when
    rendered, so a cancel in that window made an item stale.  In real use it
    reached a state where every item was stale and never recovered: 615
    utterances spoken, then 194 discarded unheard, silence until NVDA was
    restarted.

    Thirty cancel-then-speak cycles -- a few seconds of ordinary typing.
    Audio must still be arriving at the end, not just at the start.
    """
    _warm(driver)
    first_half = last_half = 0
    for i, c in enumerate("abcdefghijklmnopqrstuvwxyzabcd"):
        driver.cancel()
        before = driver._player.bytes
        driver.speak([c])
        got = _settle(driver._player, before + 1, timeout=5.0) - before
        if i < 15:
            first_half += got
        else:
            last_half += got
        time.sleep(0.02)
    assert last_half > 0, "went silent partway through and stayed silent"
    assert last_half > first_half * 0.5, \
        "audio dwindled: %d bytes early, %d late" % (first_half, last_half)


def test_typing_like_nvda_does(driver):
    """Rule 1, measured the way a user feels it.

    NVDA cancels, speaks one character, and does not send the next until
    synthDoneSpeaking arrives.  Nothing else here models that pacing, which is
    why latency bugs reached a user before they reached a test.
    """
    import synthDriverHandler
    done = synthDriverHandler.synthDoneSpeaking
    _warm(driver)
    latencies, spoke = [], 0
    for c in "abcdefghijklmnopqrst":
        done.arm()
        driver.cancel()
        before = driver._player.bytes
        t0 = time.perf_counter()
        driver.speak([c])
        got = _settle(driver._player, before + 1, timeout=5.0)
        if got > before:
            spoke += 1
            latencies.append(time.perf_counter() - t0)
        done.wait(timeout=5.0)          # NVDA waits here before the next key
    assert spoke == 20, "only %d of 20 keystrokes produced audio" % spoke
    latencies.sort()
    median = latencies[len(latencies) // 2]
    assert median < 0.15, "median keystroke latency %.0f ms" % (median * 1000)
    assert latencies[-1] < 0.60, "worst keystroke latency %.0f ms" % (
        latencies[-1] * 1000)


def test_index_commands_are_reported(driver):
    """NVDA tracks where it is in an utterance by these."""
    import speech.commands
    import synthDriverHandler
    _warm(driver)
    before = synthDriverHandler.synthIndexReached.count
    driver.speak([speech.commands.IndexCommand(1), "hello",
                  speech.commands.IndexCommand(2)])
    end = time.perf_counter() + 20.0
    while time.perf_counter() < end:
        if synthDriverHandler.synthIndexReached.count >= before + 2:
            break
        time.sleep(0.01)
    assert synthDriverHandler.synthIndexReached.count >= before + 2


def test_a_voice_change_needs_no_utterance_of_its_own(driver):
    """Rule 5: settings are recorded and reconciled, not queued as events.

    NVDA cancels between changing a setting and speaking the confirmation of
    it, so a queued voice change would be eaten and the confirmation spoken in
    the old voice.
    """
    import tigerspeech
    voices = [b for b, _d, _e in driver._voices]
    other = next(v for v in voices if v != driver._get_voice())
    _warm(driver)
    driver._set_voice(other)
    driver.cancel()                      # exactly what NVDA does next
    assert driver._get_voice() == other, "the voice change was discarded"
    before = driver._player.bytes
    driver.speak(["confirmation"])
    assert _settle(driver._player, before + 1, timeout=20.0) > before


def test_terminate_is_clean(driver):
    """Terminate must stop the host process, not leak one per session."""
    _warm(driver)
    proc = driver._proc
    assert proc is not None and proc.poll() is None
    driver.terminate()
    end = time.perf_counter() + 5.0
    while time.perf_counter() < end and proc.poll() is None:
        time.sleep(0.02)
    assert proc.poll() is not None, "the engine process outlived terminate()"


def test_high_rates_do_not_lose_a_chunk(driver):
    """A user reported speech vanishing above about NVDA rate 80.

    MacinTalk 3 divides by (index2 - index1) when interpolating segment
    durations, and above roughly 320 wpm those indices can collapse.  PowerPC
    does not trap on integer divide by zero, so the bug was harmless on the
    Macs Apple shipped it for; on x86 it killed the host, the driver restarted
    it, and exactly one chunk of the utterance disappeared without a sound.

    The host now survives it, so every rate on the slider must render.
    """
    long_text = ("Type the name of a program, folder, document, or Internet "
                 "resource, and Windows will open it for you.")
    _warm(driver)
    for rate in (0, 25, 50, 75, 80, 90, 100):
        driver._set_rate(rate)
        pcm = driver._render(long_text, driver._wpm(), driver._get_voice())
        assert pcm, "rate %d (%d wpm) produced nothing" % (rate, driver._wpm())
        n = len(pcm) // 2
        assert n > 1000, "rate %d (%d wpm) produced only %d frames" % (
            rate, driver._wpm(), n)


def test_pitch_changes_the_fundamental(driver):
    """Pitch must actually move the voice, not just move a slider.

    Measured by autocorrelation, the same way the sibling add-on settled its
    own pitch question -- where 'pbas' reached the engine and did nothing
    useful.  A slider that is inert is worse than no slider, because the user
    cannot tell it is inert.
    """
    import struct as _s

    def f0(pcm):
        n = len(pcm) // 2
        v = _s.unpack("<%dh" % n, pcm)
        w = int(22050 * 0.2)
        best, bi = -1, 0
        for i in range(0, max(1, n - w), w // 2):
            e = sum(abs(x) for x in v[i:i + w])
            if e > best:
                best, bi = e, i
        seg = [float(x) for x in v[bi:bi + w]]
        m = sum(seg) / len(seg)
        seg = [x - m for x in seg]
        bc, bl = 0.0, 0
        for lag in range(22050 // 500, 22050 // 60):
            c = sum(seg[i] * seg[i + lag] for i in range(0, len(seg) - lag, 4))
            if c > bc:
                bc, bl = c, lag
        return 22050.0 / bl if bl else 0.0

    _warm(driver)
    text = "Hello there, this is a test of the pitch."
    got = {}
    for p in (0, 50, 100):
        driver._set_pitch(p)
        pcm = driver._render(text, 180, driver._get_voice(),
                             driver._pitchOffset())
        assert pcm, "pitch %d produced nothing" % p
        got[p] = f0(pcm)
    driver._set_pitch(50)
    assert got[0] < got[50] < got[100], \
        "pitch is not monotonic: %r" % got
    # The ends are an octave either way, so each should be near a factor of two.
    assert 1.6 < got[50] / got[0] < 2.4, "low end is not about an octave: %r" % got
    assert 1.6 < got[100] / got[50] < 2.4, "high end is not about an octave: %r" % got


def test_embedded_commands_are_off_by_default(driver):
    """Tiger's front end parses "[[rate 100]]" and friends -- measured.

    That is a real feature and a real hazard: a web page or a file name
    containing "[[" could change how the screen reader sounds, and the change
    would outlive the utterance.  So it is a setting, off by default, and the
    text is neutralised when it is off.
    """
    _warm(driver)
    plain = driver._render("Hello there", 180, driver._get_voice())
    assert plain

    driver._set_acceptCommands(False)
    guarded = driver._render("[[rate 100]] Hello there", 180,
                             driver._get_voice())
    assert guarded, "guarded text produced nothing"
    # With commands off the rate command must not take effect, so the audio
    # should be close to the plain length rather than much longer.
    assert len(guarded) < len(plain) * 1.6, \
        "an embedded command changed the speech while the setting was off"

    driver._set_acceptCommands(True)
    honoured = driver._render("[[rate 100]] Hello there", 180,
                              driver._get_voice())
    assert honoured and len(honoured) > len(plain) * 1.3, \
        "the setting is on but the command did nothing"
    driver._set_acceptCommands(False)


def test_a_command_does_not_outlive_its_utterance(driver):
    """Rate and pitch are re-applied every utterance for exactly this reason."""
    _warm(driver)
    driver._set_acceptCommands(True)
    before = driver._render("Hello there", 180, driver._get_voice())
    driver._render("[[rate 100]] slow down", 180, driver._get_voice())
    after = driver._render("Hello there", 180, driver._get_voice())
    driver._set_acceptCommands(False)
    assert before and after
    assert abs(len(after) - len(before)) < len(before) * 0.1, \
        "an embedded command leaked into the next utterance"
