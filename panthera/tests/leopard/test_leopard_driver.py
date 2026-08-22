# -*- coding: utf-8 -*-
"""What a user would notice, and what previously reached one.

Every test below corresponds to a rule at the top of `leopardspeech.py`, and
every rule was paid for in the sibling ROM add-on.  Porting the rules without
porting their tests would have been the same mistake in a new repository.
"""
import time

import numpy as np
import pytest

import pantheradriver


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
    measurement -- and waited out to the end, not merely to its first bytes.

    The feeder hands the device audio at roughly real time now, rather than
    dumping the whole utterance at once, so a warm-up that is only waited for
    until it starts is still being fed while the next measurement runs, and
    its bytes land in that measurement instead.
    """
    import synthDriverHandler
    before = synthDriverHandler.synthDoneSpeaking.count
    driver.speak(["warm"])
    end = time.perf_counter() + 20.0
    while time.perf_counter() < end:
        if synthDriverHandler.synthDoneSpeaking.count > before:
            break
        time.sleep(0.005)
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


def test_only_playable_engines_are_offered(engine_tree):
    """Whatever reaches the list must be something the host can render."""
    import pantheraleopard as tree
    _mt, _sd, voicesdir = tree.engine_paths(engine_tree)
    offered = tree.read_voices(voicesdir, playable_only=True)
    assert offered, "nothing offered at all"
    assert all(e in tree.PLAYABLE_ENGINES for _b, _d, e in offered)


def test_vicki_is_withheld_without_an_aac_decoder(engine_tree, monkeypatch):
    """A Windows N install with no decoder must lose the voice, not the sound.

    Vicki is the one voice whose audio comes from outside the engine.  If the
    decoder is missing she renders silence, and a silent voice in this list
    mutes the screen reader for someone who then cannot hear their way back
    out -- so the list has to drop her instead.
    """
    import pantheraleopard as tree
    _mt, _sd, voicesdir = tree.engine_paths(engine_tree)
    with_decoder = tree.read_voices(voicesdir, playable_only=True)
    assert any(e == "meow" for _b, _d, e in with_decoder), \
        "Vicki should be offered on a machine that can decode AAC"

    monkeypatch.setattr(tree, "aac_available", lambda: False)
    without = tree.read_voices(voicesdir, playable_only=True)
    assert not any(e == "meow" for _b, _d, e in without)
    # Leopard has two of them -- Alex as well as Vicki -- where Tiger has only
    # Vicki, so counting one lost voice was Tiger's arithmetic, not a rule.
    lost = sum(1 for _b, _d, e in with_decoder if e == "meow")
    assert lost >= 1
    assert len(without) == len(with_decoder) - lost
    # And the rest of the list is untouched: one voice lost, not a whole engine.
    assert {b for b, _d, _e in without} == \
        {b for b, _d, e in with_decoder if e != "meow"}


def test_voices_are_read_from_the_install(engine_tree):
    """The list must come from the user's files, not a table that can drift."""
    import leopardspeech
    _mt, _sd, voicesdir = leopardspeech.engine_paths(engine_tree)
    voices = leopardspeech.read_voices(voicesdir)
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
    import leopardspeech
    # `pantheraleopard`, not `tree`: every add-on shares one sys.modules, and
    # rename is exactly what stops this one loading Tiger's copy.  The test
    # kept the old name and had been failing to import ever since.
    import pantheraleopard as tree
    monkeypatch.delenv("LEOPARD_TREE", raising=False)
    monkeypatch.setattr(tree, "config_base", lambda: str(tmp_path))
    assert tree.find_tree() is None
    assert not tree.usable()

    ok, lines = tree.explain()
    assert not ok
    assert any("no tree found" in ln for ln in lines), lines

    # Offered, so that selecting it is a way to find out why.
    assert leopardspeech.SynthDriver.check()

    # And refuses to load, so NVDA falls back and speech carries on.
    told = []
    monkeypatch.setattr(pantheradriver, "_explainLater",
                        lambda folder, *rest: told.append(folder))
    with pytest.raises(Exception):
        leopardspeech.SynthDriver()
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
    import leopardspeech
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
        # Every sample, not every fourth: subsampling a correlation aliases.
        bc, bl = 0.0, 0
        for lag in range(22050 // 500, 22050 // 60):
            c = sum(seg[i] * seg[i + lag] for i in range(0, len(seg) - lag))
            if c > bc:
                bc, bl = c, lag
        return 22050.0 / bl if bl else 0.0

    _warm(driver)
    text = "Hello there, this is a test of the pitch."

    def sweep(voice):
        got = {}
        for p in (0, 50, 100):
            driver._set_pitch(p)
            pcm = driver._render(text, 180, voice, driver._pitchOffset())
            assert pcm, "pitch %d produced nothing for %s" % (p, voice)
            got[p] = f0(pcm)
        driver._set_pitch(50)
        return got

    # Whatever the voice, the slider has to move the voice and move it the
    # right way.  This is the part that being inert would break.
    default = sweep(driver._get_voice())
    assert default[0] < default[50] < default[100], \
        "pitch is not monotonic on %s: %r" % (driver._get_voice(), default)

    # The octave-either-way claim holds for a *formant* voice, and only there.
    # Measured through the host, Fred is 71.4 / 143.2 / 279.1 Hz across the
    # slider -- an octave each way to within a percent -- while Alex, who is
    # concatenative and pitch-shifts recorded speech instead of retuning an
    # oscillator, gives 129.7 / 198.6 / 501.1.  This driver defaults to Alex,
    # so the test inherited from the Tiger add-on was quietly measuring him
    # and calling a correct driver broken.
    fred = [b for b, _d, e in driver._voices if b == "Fred"]
    if fred:
        got = sweep(fred[0])
        assert 1.6 < got[50] / got[0] < 2.4, "low end is not an octave: %r" % got
        assert 1.6 < got[100] / got[50] < 2.4, "high end is not an octave: %r" % got


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


def _speakAndWait(driver, seq, timeout=25.0):
    """Speak one sequence and wait for it to finish, -> (feeds, bytes)."""
    import synthDriverHandler
    before = synthDriverHandler.synthDoneSpeaking.count
    fed0, bytes0 = driver._player.fed, driver._player.bytes
    driver.speak(seq)
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        if synthDriverHandler.synthDoneSpeaking.count > before:
            break
        time.sleep(0.005)
    else:
        raise AssertionError("the sequence never finished speaking")
    return driver._player.fed - fed0, driver._player.bytes - bytes0


class _renderCounter(object):
    """Count the times the engine was asked to speak.

    How many utterances a sequence became used to be inferred from
    `player.feed()` calls, one per utterance.  The audio is streamed now, so a
    single utterance is fed in many chunks and that proxy measures nothing.

    What those tests always meant is how many times the engine was handed
    text, which is worth counting directly: the property under test is that
    adjacent fragments become *one* request, and the pause testers reported
    was one request per fragment.
    """

    def __init__(self, driver):
        self.driver = driver
        self.texts = []

    def __enter__(self):
        original = self.driver._render
        self._original = original

        def spy(text, wpm, voice, pitch=0, sink=None):
            self.texts.append(text)
            return original(text, wpm, voice, pitch, sink=sink)

        self.driver._render = spy
        return self

    def __exit__(self, *exc):
        self.driver._render = self._original
        return False


def test_adjacent_text_is_one_utterance_not_several(driver):
    """A line with a link in it is one sentence, and must sound like one.

    NVDA puts an IndexCommand only where a callback sits or an utterance ends
    -- speech/manager.py -- so the pieces of a web page line arrive as plain
    adjacent strings.  Rendering each on its own gave every fragment the
    falling intonation of a finished sentence, which two testers reported as
    the speech pausing before every link.
    """
    _warm(driver)
    with _renderCounter(driver) as rc:
        _speakAndWait(driver, ["Read more about it ", "link", "Home"])
    assert len(rc.texts) == 1, (
        "each fragment was still rendered on its own: %r" % (rc.texts,))


def test_an_index_does_not_split_the_sentence_but_is_still_reported(driver):
    """An index marks a position; it does not end an utterance.

    NVDA puts one at the *start* of every line during say-all -- the
    lineReached callback -- and sayAll speaks through speakWithoutPauses,
    which buffered those lines together precisely because none of them
    contained a natural pause.  Splitting there handed the engine a fragment
    ending in nothing and it read that as the end of a sentence: with word
    wrap on, a full stop between "narrowing" and "budgets".

    So the audio stays whole, and the index is still reported.
    """
    import speech.commands
    import synthDriverHandler
    _warm(driver)
    before = synthDriverHandler.synthIndexReached.count
    with _renderCounter(driver) as rc:
        _speakAndWait(driver, ["first part ",
                               speech.commands.IndexCommand(7),
                               "second part"])
    assert len(rc.texts) == 1, "the index split the sentence: %r" % (rc.texts,)
    assert synthDriverHandler.synthIndexReached.count > before, "index lost"


def test_every_index_is_reported_even_when_lines_are_joined(driver):
    """A wrapped paragraph carries one index per line, and none may vanish."""
    import speech.commands
    import synthDriverHandler
    _warm(driver)
    before = synthDriverHandler.synthIndexReached.count
    _speakAndWait(driver, [speech.commands.IndexCommand(1), "one line ",
                           speech.commands.IndexCommand(2), "and another ",
                           speech.commands.IndexCommand(3), "and a third."])
    got = synthDriverHandler.synthIndexReached.count - before
    assert got == 3, "expected three indexes, got %d" % got


def test_a_break_command_becomes_real_silence(driver):
    """NVDA asking for a pause in so many words was dropped until now."""
    import speech.commands
    import leopardspeech
    _warm(driver)
    _f1, plain = _speakAndWait(driver, ["one", "two"])
    _f2, withGap = _speakAndWait(driver, ["one",
                                          speech.commands.BreakCommand(300),
                                          "two"])
    want = len(pantheradriver._silence(300))
    assert withGap >= plain + want * 0.8, (
        "a 300 ms break added %d bytes, expected about %d" % (withGap - plain, want))


def test_the_pause_setting_lengthens_the_gaps(driver):
    """The knob two testers asked for, in both directions."""
    import leopardspeech
    _warm(driver)
    driver._set_pauseMode("short")
    _f, short = _speakAndWait(driver, ["alpha", "beta"])
    driver._set_pauseMode("long")
    _f, long_ = _speakAndWait(driver, ["alpha", "beta"])
    driver._set_pauseMode("short")
    assert long_ > short, "'long' produced no more audio than 'short'"
    assert leopardspeech.SynthDriver.PAUSE_MS["short"] == 0


def test_fragments_are_joined_without_gluing_words_together(driver):
    """"link" then "Home" must not reach the engine as "linkHome"."""
    import leopardspeech
    join = pantheradriver._joinFragments
    assert join(["link", "Home"]) == "link Home"
    assert join(["Read more about it ", "here"]) == "Read more about it here"
    assert join([" on our site.", " Next"]) == " on our site. Next"
    assert join(["only"]) == "only"


def test_typographic_characters_reach_the_engine_as_macroman():
    """The engine's text is a single-byte Mac encoding, not UTF-8."""
    import leopardspeech
    enc = pantheradriver._encode
    assert enc(u"—") == b"\xd1"
    assert enc(u"–") == b"\xd0"
    assert enc(u"“") == b"\xd2"
    assert enc(u"”") == b"\xd3"
    assert enc(u"…") == b"\xc9"
    assert enc(u"café") == b"caf\x8e"
    assert enc(u"Is it?") == b"Is it?"
    assert b"?" not in enc(u"你好")
    assert enc(u"你好") == b"  "


def test_the_driver_actually_uses_that_encoder():
    """An encoder that is never called is exactly as broken as no encoder."""
    import io
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))),
                        "addon", "synthDrivers", "_panthera",
                        "pantheradriver.py")
    src = io.open(path, encoding="utf-8").read()
    assert "t = _encode(text)" in src, "the request no longer encodes the text"
    assert 't = text.encode("utf-8")' not in src, "still sending UTF-8"


def test_capital_pitch_change_reaches_the_engine(driver, monkeypatch):
    """NVDA's "capital pitch change percentage" is a PitchCommand.

    Tomi set it to 30 and heard no change on capitals at all, because the
    driver kept only IndexCommands and dropped every other command in the
    sequence -- so the setting was inert whatever it was set to.
    """
    import speech.commands
    seen = []
    original = driver._render

    def spy(text, wpm, voice, pitch=0, sink=None):
        seen.append(pitch)
        return original(text, wpm, voice, pitch, sink=sink)

    _warm(driver)
    monkeypatch.setattr(driver, "_render", spy)
    _speakAndWait(driver, ["a",
                           speech.commands.PitchCommand(offset=30), "B",
                           speech.commands.PitchCommand(), "c"])
    assert len(seen) == 3, "expected three renders, got %r" % (seen,)
    assert seen[1] > seen[0], "the capital was not raised in pitch: %r" % (seen,)
    assert seen[2] == seen[0], "pitch did not come back down: %r" % (seen,)


def test_volume_actually_changes_the_level(driver):
    """A gap people named: there was no volume control at all.

    It is the engine's own [[volm]] command rather than gain applied to the
    PCM: measured on both engines it is exactly linear, and the synthesizer
    does the arithmetic before it quantises to 16 bits.
    """
    import struct as _s

    def rms(pcm):
        v = _s.unpack("<%dh" % (len(pcm) // 2), pcm)
        return (sum(float(x) * x for x in v) / max(1, len(v))) ** 0.5

    _warm(driver)
    text = "Hello there, testing the volume."
    voice = driver._get_voice()
    driver._set_volume(100)
    full = rms(driver._render(text, 180, voice))
    driver._set_volume(50)
    half = rms(driver._render(text, 180, voice))
    driver._set_volume(100)
    assert full > 0
    assert 0.35 < half / full < 0.65,         "half volume gave %.2f of full, not about a half" % (half / full)


def test_full_volume_changes_nothing_about_the_request(driver):
    """The default must not perturb what has been byte-identical for months."""
    import leopardspeech
    driver._set_volume(100)
    text = "Hello there."
    a = driver._render(text, 180, driver._get_voice())
    b = driver._render(text, 180, driver._get_voice())
    assert a == b and a, "renders are not reproducible at full volume"


def test_volume_defaults_to_full_not_nvdas_fifty():
    """Adding a volume control must not turn everybody's volume down.

    NumericDriverSetting takes defaultVal=50, that becomes the config spec's
    default, and NVDA writes it over whatever __init__ set.  So the first
    build with a volume control made people quieter on upgrade -- reported
    within the hour: "alex got quieter, not by a whole lot, but it was
    definitely noticeable".
    """
    import leopardspeech
    from synthDriverHandler import SynthDriver
    setting = pantheradriver._fullVolumeByDefault(SynthDriver.VolumeSetting())
    # 90, not 100: that is where each voice reaches its own measured maximum,
    # and the last tenth of the slider is deliberately past it for anyone who
    # wants loudness more than they mind clipping. Still emphatically not 50.
    assert setting.defaultVal == pantheradriver.VOLUME_CLEAN == 90

    # And it has to be the setting the driver actually offers, not one made
    # up by the test.
    import io
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))),
                        "addon", "synthDrivers", "_panthera",
                        "pantheradriver.py")
    src = io.open(path, encoding="utf-8").read()
    assert "_fullVolumeByDefault(SynthDriver.VolumeSetting())" in src
    assert "\n        SynthDriver.VolumeSetting()," not in src


def test_a_typographic_apostrophe_is_an_apostrophe():
    """MacRoman has it, and that turned out to be the problem.

    0xD5 is the right single QUOTATION mark, and the engine's front end treats
    it as one -- it breaks the phrase there.  "Canopy's investments" was spoken
    as "Canopy", 250 ms of silence, "s investments", and a sentence carrying
    two of them ran 1.57 s longer than the same sentence with straight
    apostrophes.  Reported as the speech "breaking up at and data", which is
    where it landed after the engine lost its place.
    """
    import leopardspeech
    enc = pantheradriver._encode
    assert enc(u"Canopy’s") == b"Canopy's"
    assert enc(u"‘quoted’") == b"'quoted'"
    # Curly *double* quotes stay: those really are quotation marks, and the
    # engine is right to treat them as such.
    assert enc(u"“quoted”") == b"\xd2quoted\xd3"


def test_a_string_that_crashes_the_engine_does_not_silence_the_synthesizer(driver):
    """A tester found one: "U" followed by about fifty p's.

    It faults inside Apple's own code -- MacinTalk + 0x3aed1, reached from
    MTMBSegmentProducer::LoadUnit, which is the concatenative unit loader --
    so it is Alex's path, Fred survives it, and there is nothing to fix on
    this side of the engine.

    What must never follow is silence.  The host dies, the driver notices the
    broken exchange, drops the process and starts a fresh one, and the next
    utterance speaks.  Losing one utterance to Apple's bug is a nuisance;
    losing the screen reader is the failure this driver exists to avoid.
    """
    _warm(driver)
    driver._render("U" + "p" * 60, 180, driver._get_voice())
    spoke = driver._render("testing", 180, driver._get_voice())
    assert spoke, "the synthesizer never spoke again after the engine crashed"


def test_rate_boost_actually_speaks_faster(driver):
    """A user asked how to get past 100%, and nothing was stopping us but a
    constant.

    Measured, the engine honours whatever rate it is given and stays stable
    far past anything useful: Alex delivers 853 wpm when asked for 800 and
    1598 when asked for 1500.  So boost widens the slider rather than doing
    anything clever with the audio.

    It is a switch and not a wider slider because widening the slider would
    have made everybody's existing setting faster without asking -- the same
    mistake as a volume control that arrives at half.
    """
    import leopardspeech
    _warm(driver)
    driver._set_rate(100)
    driver._set_rateBoost(False)
    plain = driver._wpm()
    driver._set_rateBoost(True)
    boosted = driver._wpm()
    driver._set_rateBoost(False)
    assert plain == pantheradriver.RATE_MAX
    assert boosted == pantheradriver.RATE_MAX_BOOST
    assert boosted > plain

    # And it has to reach the engine, not just the arithmetic.
    voice = driver._get_voice()
    slow = driver._render("the quick brown fox jumps over the lazy dog", plain, voice)
    fast = driver._render("the quick brown fox jumps over the lazy dog", boosted, voice)
    assert slow and fast
    assert len(fast) < len(slow) * 0.75, "boosted speech was not much faster"


def test_rate_boost_is_off_by_default(driver):
    """Upgrading must not change how fast anybody's screen reader talks."""
    assert driver._get_rateBoost() is False


def test_inflection_flattens_and_exaggerates_the_voice(driver):
    """Asked for by a tester, and it is the engine's own 'pmod'.

    It reaches the two engine families differently, which is why this checks
    the mean and not the wander.  On the concatenative voices it opens the
    voice right up -- Vicki's spread runs 7.1 to 31.2 across the range, Alex's
    8.6 to 22.5 -- while formant Fred barely changes spread (24.2 to 28.8) and
    climbs in pitch instead, 101.4 Hz to 125.9.  Mean F0 rises on every voice.
    """
    import struct as _s
    import leopardspeech

    def meanF0(pcm):
        v = np.frombuffer(pcm, dtype="<i2").astype(float) / 32768.0
        sr, N, H = 22050, 1024, 512
        lo, hi = sr // 300, sr // 60
        out = []
        for i in range(0, len(v) - N, H):
            f = v[i:i + N]
            if np.abs(f).max() < 0.02:
                continue
            g = f - f.mean()
            ac = np.correlate(g, g, "full")[N - 1:]
            if ac[0] <= 0:
                continue
            lag = lo + int(np.argmax(ac[lo:hi]))
            if ac[lag] / ac[0] > 0.3:
                out.append(sr / lag)
        return float(np.mean(out)) if out else 0.0

    _warm(driver)
    text = "This is a test of the inflection parameter today."
    voice = driver._get_voice()
    driver._set_inflection(0)
    flat = meanF0(driver._render(text, 180, voice))
    driver._set_inflection(100)
    lively = meanF0(driver._render(text, 180, voice))
    driver._set_inflection(50)
    assert lively > flat * 1.05, ("inflection did not move the voice: "
                                  "%.1f Hz flat, %.1f Hz lively" % (flat, lively))


def test_the_default_utterance_carries_no_embedded_commands(driver):
    """Defaults must leave the engine exactly as it comes.

    Volume at full and inflection at the halfway point add nothing to the
    text, which is what keeps the default render byte-for-byte what it has
    always been -- and what the Tiger regression baseline depends on.
    """
    import leopardspeech
    driver._set_volume(100)
    driver._set_inflection(50)
    a = driver._render("hello there", 180, driver._get_voice())
    b = driver._render("hello there", 180, driver._get_voice())
    assert a and a == b


def _waitForFeeds(driver, want, timeout=20.0):
    """Wait until the player has been fed `want` times, -> the count."""
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        if driver._player.fed >= want:
            break
        time.sleep(0.002)
    return driver._player.fed


def test_a_long_utterance_sounds_before_it_has_finished_rendering(driver):
    """The whole reason for streaming, and Alex is why it matters most.

    He renders more audio per character than anything else here, so a
    paragraph of Alex was the longest wait of all -- and none of it was the
    engine, which runs at about ninety times real time.  The audio now
    arrives in chunks and each is fed as it comes.

    Waiting for the utterance to *finish* would mean waiting out its
    playback, so this only waits for the second chunk: one feed could be a
    whole utterance handed over at once, two cannot.
    """
    _warm(driver)
    driver.speak(["The quick brown fox jumps over the lazy dog. " * 6])
    fed = _waitForFeeds(driver, 2)
    driver.cancel()
    assert fed >= 2, "the utterance arrived in one piece; nothing streamed"


def test_cancel_during_a_streamed_utterance_leaves_the_engine_usable(driver):
    """Streaming put a cancel inside a response for the first time.

    One utterance is now many chunks arriving over the pipe, so `cancel()`
    can land while the driver is still reading them.  Walking away from the
    rest would leave those chunks in the pipe and the *next* response would
    be read starting from them -- and the answer to a desynchronised pipe is
    killing the engine, which for Alex means reloading a 701 MB sample bank.
    So the response is read to its end and only the feeding stops.
    """
    _warm(driver)
    driver.speak(["The quick brown fox jumps over the lazy dog. " * 6])
    _waitForFeeds(driver, 1)
    driver.cancel()
    # Let the worker finish reading that response, and let the interrupted
    # utterance report its "done", so what follows measures the new utterance
    # rather than the end of the old one.
    time.sleep(0.5)
    _feeds, spoken = _speakAndWait(driver, ["still here"])
    assert spoken > 0, "the driver went silent after cancelling a stream"


def test_an_engine_that_cannot_stream_still_speaks(driver, monkeypatch):
    """New driver, old engine: the one combination that did go silent.

    This is not hypothetical here.  The first run of these tests after the
    driver learnt to stream produced no audio at all, because the bundled
    `panthera_host.exe` was one build out of date: it refuses 'TGR4' and
    exits, the driver sees a closed pipe and respawns it, and asks again --
    for every utterance, for ever.  An add-on update whose executable failed
    to copy does exactly that on a real machine.

    Simulated by asking for a magic no host will ever know.  The driver must
    notice, stop asking, say so where somebody will see it, and speak.
    """
    import leopardspeech
    _warm(driver)
    monkeypatch.setattr(pantheradriver, "REQ_MAGIC_STREAM", 0x54475239)
    assert driver._streaming, "streaming should start on"

    _feeds, spoken = _speakAndWait(driver, ["can you still hear me"])
    assert spoken > 0, "a host that cannot stream left the driver silent"
    assert not driver._streaming, "it kept asking for a request it was refused"

    _feeds, again = _speakAndWait(driver, ["and again"])
    assert again > 0, "it spoke once and then went silent"


def test_debug_logging_reports_how_long_an_utterance_took(driver, monkeypatch):
    """"It lags on long text" is the report this add-on gets most.

    Until now a debug log said what was spoken but never how long any of it
    took, so the one question people actually ask could not be answered from
    the log they sent.  Both numbers go in, because they are different
    faults: a first sound that arrives late, and an utterance that takes a
    long time in total -- and with Alex the second is expected, since he
    renders far more audio per character than anything else here.
    """
    from logHandler import log as fakelog
    _warm(driver)
    monkeypatch.setattr(type(fakelog), "isEnabledFor", lambda self, lvl: True)
    del fakelog.messages[:]
    _speakAndWait(driver, ["The quick brown fox jumps over the lazy dog. " * 3])

    lines = [m for lvl, m in fakelog.messages
             if lvl == "debug" and "first sound after" in m]
    assert lines, "debug logging still says nothing about timing: %r" % (
        [m for _l, m in fakelog.messages][-5:],)
    line = lines[-1]
    assert "chars" in line and "chunk(s)" in line and "s of audio" in line, line
    first = float(line.split("first sound after ")[1].split(" ms")[0])
    total = float(line.split("all of it by ")[1].split(" ms")[0])
    assert 0.0 <= first <= total, "nonsensical timings: %s" % line


def test_interrupting_does_not_delay_the_next_utterance(driver):
    """The lag people actually describe, and it survived streaming.

    Cancelling stops the sound at once, but the host went on synthesising the
    rest of an utterance nobody would hear, and the worker could not begin the
    next one until that response ended.  Measured on a real session before
    this was fixed: 38% of utterances waited more than 200 ms to *start*
    rendering, the worst 931 ms -- while the first sound, once started,
    arrived in 20.

    Alex is where this bites hardest, because he renders more audio per
    character than anything else here, so an abandoned sentence of his is the
    longest pointless wait available.
    """
    _warm(driver)
    # Long enough that abandoning it matters: the whole point is the
    # render still running after nobody wants it, so the text has to
    # take appreciably longer to synthesise than the threshold below.
    fed0 = driver._player.fed
    driver.speak(["The quick brown fox jumps over the lazy dog. " * 40])
    # Beyond the warm-up's own feeds, or this waits for nothing and cancels
    # before the long utterance has even been picked up -- which is exactly
    # how the first version of this test passed without the fix in place.
    assert _waitForFeeds(driver, fed0 + 1) > fed0, "the long utterance never started"

    before = driver._player.bytes
    driver.cancel()
    started = time.perf_counter()
    driver.speak(["next"])
    end = started + 5.0
    while time.perf_counter() < end:
        if driver._player.bytes > before:
            break
        time.sleep(0.002)
    waited = (time.perf_counter() - started) * 1000.0
    assert driver._player.bytes > before, "the next utterance never arrived"
    assert waited < 400.0, (
        "interrupting still costs %.0f ms before the next utterance is heard"
        % waited)


def test_audio_from_a_cancelled_utterance_is_dropped_by_the_feeder(driver):
    """The post above bleeding into the post below.

    `cancel()` bumps the epoch and then drains the audio queue; the worker
    checks the epoch and then puts.  A chunk landing between those two steps
    survived the drain and played against whatever the user asked for next --
    heard as a sentence from one message repeating over the one below it.
    With a whole utterance per put that was one narrow window an utterance,
    and streaming made it twenty to seventy.

    Hammering interrupts does not reproduce it -- a dozen rounds hit the
    window no more reliably than a user does, and a test that passes either
    way is worse than none.  So the invariant is tested where the fix lives.
    """
    _warm(driver)
    driver.cancel()
    stale = driver._epoch - 1
    time.sleep(0.05)

    before = driver._player.bytes
    driver._audioQueue.put(("audio", bytes(4000), stale))
    time.sleep(0.2)
    assert driver._player.bytes == before, (
        "audio from a cancelled utterance was played anyway")

    # And the guard must not eat what is still wanted, or it would be silence.
    driver._audioQueue.put(("audio", bytes(4000), driver._epoch))
    end = time.perf_counter() + 2.0
    while time.perf_counter() < end and driver._player.bytes == before:
        time.sleep(0.005)
    assert driver._player.bytes > before, "current audio was dropped too"


def test_volume_comes_back_after_being_taken_to_zero(driver):
    """A user found this, and it is the worst failure this driver has.

    `[[volm]]` sets state on the speech channel and it outlives the utterance
    that set it, so sending nothing at 100 did not mean "full volume", it
    meant "whatever was set last".  Home to 0 and End back to 100 left the
    synthesizer permanently silent; only 99 brought it back, because 99 is
    the one value that still sends a command.
    """
    import struct as _s

    def level(pcm):
        if not pcm:
            return 0.0
        v = _s.unpack("<%dh" % (len(pcm) // 2), pcm)
        return (sum(x * x for x in v) / float(len(v))) ** 0.5

    voice = driver._get_voice()
    driver._set_volume(100)
    full = level(driver._render("testing one two three", 180, voice))
    driver._set_volume(0)
    driver._render("testing one two three", 180, voice)
    driver._set_volume(100)
    back = level(driver._render("testing one two three", 180, voice))
    assert back > full * 0.5, (
        "volume did not come back: %.1f against %.1f at full" % (back, full))


def test_inflection_comes_back_to_the_middle_too(driver, monkeypatch):
    """The same trap, one setting over: 'pmod' is channel state as well."""
    import leopardspeech
    voice = driver._get_voice()
    driver._set_inflection(0)
    driver._render("testing one two three", 180, voice)

    seen = []
    original = pantheradriver._encode
    monkeypatch.setattr(pantheradriver, "_encode",
                        lambda t: (seen.append(t), original(t))[1])
    driver._set_inflection(50)
    driver._render("testing one two three", 180, voice)
    assert seen and "[[pmod 100]]" in seen[0], (
        "returning to the middle sent nothing, so the engine stays flat: %r"
        % (seen,))

    del seen[:]
    driver._render("testing one two three", 180, voice)
    assert seen and "[[pmod" not in seen[0], (
        "the command is still being sent after the setting came back: %r"
        % (seen,))


def test_natural_phrasing_removes_a_break_the_engine_invented(driver):
    """The complaint, in one assertion.

    "Restart with debug logging enabled" came out with 191 ms of silence after
    "debug" and 94 ms after "logging" -- the word fenced on both sides, which
    listeners described as hearing it in quotes.  Whisper transcribed the same
    render as *"restart with debug, logging and enabled."*, commas and all.

    None of that is the phrasing dictionary: the same text matches zero rows in
    Leopard's table, in Mountain Lion's, and in a merged one, and all three
    render byte-identically.  It is `Boundaries.SilThreshold`, which the engine
    asks for and was never told.
    """
    text = "restart with debug logging enabled"
    voice = driver._get_voice()
    _warm(driver)

    driver._set_phrasing("leopard")   # Leopard's own model
    plain = driver._render(text, driver._wpm(), voice)
    driver._set_phrasing("fewest")    # answered, fewest breaks
    natural = driver._render(text, driver._wpm(), voice)
    driver._set_phrasing("fewest")    # answered, fewest breaks

    assert plain and natural
    assert natural != plain, "the setting never reached the engine"
    # The break is silence, so removing it can only make the utterance shorter.
    assert len(natural) < len(plain), "natural phrasing did not shorten the gap"

    def gaps(pcm, floor=300, least=772):
        import struct
        v = struct.unpack("<%dh" % (len(pcm) // 2), pcm)
        runs, start, first, last = [], None, None, None
        for i, s in enumerate(v):
            if abs(s) >= floor:
                if first is None:
                    first = i
                last = i
                if start is not None and i - start >= least:
                    runs.append((start, i))
                start = None
            elif start is None:
                start = i
        return [(a, b) for a, b in runs
                if first is not None and a > first and b < last]

    assert len(gaps(natural)) < len(gaps(plain)), \
        "the same number of interior silences survived"


def test_the_settings_default_to_the_better_behaviour(driver):
    """Both defaults are deliberate, and both are a change.

    Every release before this one phrased the way the engine's own defaults do
    and had every abbreviation rule refused, so upgrading changes how the
    synthesizer sounds.  That is the point -- but it is the kind of thing to
    state in a test, so nobody flips it back by accident.
    """
    assert driver._get_phrasing() == "fewest"
    assert driver._get_expandAbbreviations() is True


def test_turning_a_setting_off_restarts_the_engine(driver):
    """The host reads both settings once, when it starts.

    So a change that does not restart the process is a change the user cannot
    hear until they switch synthesizers -- which is exactly how the first
    attempt at this failed, with an environment variable that never reached
    NVDA's child.
    """
    _warm(driver)
    before = driver._host()
    driver._set_expandAbbreviations(False)
    after = driver._host()
    # Assert *before* putting the setting back: restoring it restarts the host
    # again, which would kill the very process being examined.  The first
    # version of this test did that and failed on its own tidying up.
    assert after is not before, "the engine kept running with the old setting"
    assert after.poll() is None, "the replacement engine is not alive"
    assert before.poll() is not None, "the old engine was left running"
    # And it still speaks afterwards.
    assert driver._render("one two three", driver._wpm(), driver._get_voice())
    driver._set_expandAbbreviations(True)


def test_a_very_long_utterance_can_still_be_interrupted(driver):
    """The same lag as the test above, at the length that made it look fatal.

    A paragraph costs the better part of a second.  A whole post costs seconds,
    and at that length the driver does not read as slow -- it reads as dead.
    From a real session, arrowing a timeline: the render of a 6429-character
    post began at 41.449, twelve keypresses over the next five and a half
    seconds produced no speech at all, and the log line saying the render had
    finished -- `all of it by 7455 ms` -- is immediately followed by the next
    utterance.  The user reported it as "speech stops until I alt tab", and
    alt-tabbing had nothing to do with it: the wait simply ended.

    The threshold is the same as the shorter test's, because the whole point
    of retiring the host is that the cost of an interruption no longer has
    anything to do with how long the abandoned utterance was.
    """
    _warm(driver)
    fed0 = driver._player.fed
    driver.speak(["The quick brown fox jumps over the lazy dog. " * 150])
    assert _waitForFeeds(driver, fed0 + 1) > fed0, "the long post never started"

    before = driver._player.bytes
    driver.cancel()
    started = time.perf_counter()
    driver.speak(["next"])
    end = started + 20.0
    while time.perf_counter() < end:
        if driver._player.bytes > before:
            break
        time.sleep(0.002)
    waited = (time.perf_counter() - started) * 1000.0
    assert driver._player.bytes > before, "the next utterance never arrived"
    assert waited < 400.0, (
        "interrupting a long post still costs %.0f ms before the next "
        "utterance is heard" % waited)


def test_cancel_does_not_block_while_an_utterance_is_rendering(driver):
    """Rule 5, against the thing that now happens inside `cancel()`.

    Retiring the host is process work, and `cancel()` runs on NVDA's main
    thread -- the one that turns keystrokes into speech.  Doing it there would
    trade a wait the user hears for a stall they type into, which is the worse
    of the two.  So it is handed to a thread, and this measures that it was.
    """
    _warm(driver)
    fed0 = driver._player.fed
    driver.speak(["The quick brown fox jumps over the lazy dog. " * 150])
    assert _waitForFeeds(driver, fed0 + 1) > fed0, "the long post never started"

    started = time.perf_counter()
    driver.cancel()
    took = (time.perf_counter() - started) * 1000.0
    assert took < 50.0, (
        "cancel() took %.0f ms on the thread that must never wait" % took)


def test_interrupting_over_and_over_leaves_one_engine_running(driver,
                                                              monkeypatch):
    """Arrowing a timeline, and what it leaves behind.

    An interruption now costs a process, so the question the old driver never
    had to answer is whether sixty of them in a row leak sixty of them.  They
    are started by a thread and killed by a thread, and a burst of cancels
    arrives faster than either finishes.
    """
    import leopardspeech
    started = []
    realPopen = leopardspeech.subprocess.Popen

    def record(*a, **k):
        proc = realPopen(*a, **k)
        started.append(proc)
        return proc

    monkeypatch.setattr(leopardspeech.subprocess, "Popen", record)

    _warm(driver)
    for _ in range(60):
        driver.cancel()
        driver.speak(["The quick brown fox jumps over the lazy dog. " * 60])
        time.sleep(0.06)

    # Let the last retirement finish before counting: the kill and the
    # replacement both happen off this thread.
    end = time.perf_counter() + 10.0
    while time.perf_counter() < end and driver._retiring:
        time.sleep(0.01)
    time.sleep(0.5)

    alive = [p for p in started if p.poll() is None]
    assert len(alive) == 1, (
        "%d engines left running out of %d started"
        % (len(alive), len(started)))

    # And, above everything else, it still speaks -- rule 3.
    before = driver._player.bytes
    driver.cancel()
    driver.speak(["still here"])
    assert _settle(driver._player, before + 1, timeout=10.0) > before, (
        "the synthesizer went silent after being interrupted repeatedly")


# -- per-voice volume -------------------------------------------------------
#
# Alex, the default voice, is the quietest speaking voice in the set: RMS 2473
# at `volm 1.0` against Bruce's 5899, nearly 8 dB down. That is why Leopard has
# always sounded quieter than the Tiger and outSPOKEN add-ons at the same
# setting, and why the slider now scales to each voice's own measured maximum.


def _rms(pcm):
    import struct as _s
    if not pcm:
        return 0.0
    v = _s.unpack("<%dh" % (len(pcm) // 2), pcm)
    return (sum(float(x) * x for x in v) / max(1, len(v))) ** 0.5


def _peak_and_clipped(pcm):
    import struct as _s
    if not pcm:
        return 0, 0
    v = _s.unpack("<%dh" % (len(pcm) // 2), pcm)
    return (max(max(v), -min(v)),
            sum(1 for x in v if x >= 32766 or x <= -32767))


def test_the_volume_table_covers_every_voice_we_offer(driver):
    """An unmeasured voice silently gets 1.0, which is quiet but never wrong.

    Worth failing on anyway: a voice added later should be measured rather
    than left behind at the old level while everything around it got louder.
    """
    import leopardspeech
    missing = [entry[0] for entry in driver._voices
               if entry[0] not in leopardspeech.VOLUME_NORM]
    assert not missing, (
        "not in VOLUME_NORM, so they stay at the old level: %s -- run "
        "tools/volume_table.py" % ", ".join(missing))


def test_ninety_never_clips_more_than_the_voice_already_did(driver):
    """**The promise the default makes**, and it is not "nothing clips".

    It cannot be, because **Victoria already clips at its own natural level**
    -- 11 samples on a vowel-heavy probe at `volm 1.0`, which is exactly what
    every previous release sent at volume 100. It scales perfectly well and
    only becomes clean at 0.75, so removing those eleven samples would mean
    turning Victoria down 2.5 dB. Eleven samples in two hundred thousand
    against a voice audibly quieter than everything around it is a bad trade,
    so the distortion stays and this test says so out loud rather than
    pretending otherwise.

    What must hold is that the table never makes a voice *worse* than it was.

    A handful of voices rather than all twenty-four, because each one is two
    real renders; these are the extremes -- loudest, quietest, and the ones
    already at the ceiling.
    """
    _warm(driver)
    driver._acceptCommands = True   # or COMMAND_RE strips the baseline prefix
    text = ("The US Chamber of Commerce warned Tuesday. Ah, oh, ooh, aye. "
            "WARNING! ERROR! Take a big pack of tickets, Bobby.")
    for voice in ("Alex", "Bruce", "Victoria", "Whisper", "Fred"):
        if voice not in driver._get_availableVoices():
            continue
        driver._set_voice(voice)
        was = _peak_and_clipped(
            driver._render("[[volm 1.000]]" + text, driver._wpm(), voice))[1]             if driver._acceptCommands else None
        driver._set_volume(90)
        peak, clipped = _peak_and_clipped(
            driver._render(text, driver._wpm(), voice))
        assert peak, "%s rendered nothing" % voice
        if was is None:
            assert not clipped, (
                "%s clips %d samples at the default volume -- its VOLUME_NORM "
                "is too high" % (voice, clipped))
        else:
            assert clipped <= was, (
                "%s clips %d samples at the default volume against %d at its "
                "own natural level -- VOLUME_NORM made it worse"
                % (voice, clipped, was))


def test_alex_is_no_longer_the_quiet_one(driver):
    """The whole point of the table, stated as the thing a listener notices.

    Alex used to sit nearly 8 dB below Bruce at the same setting. It is not
    required to match -- Bruce cannot be turned up at all, so the gap closes
    from Alex's side only -- but it must close.
    """
    _warm(driver)
    text = "The US Chamber of Commerce warned Tuesday that tariffs would rise."
    driver._set_volume(90)
    levels = {}
    for voice in ("Alex", "Bruce"):
        if voice not in driver._get_availableVoices():
            import pytest
            pytest.skip("need both Alex and Bruce")
        driver._set_voice(voice)
        levels[voice] = _rms(driver._render(text, driver._wpm(), voice))
    import math
    gap = 20 * math.log10(levels["Bruce"] / levels["Alex"])
    assert gap < 4.0, (
        "Alex is still %.1f dB below Bruce; it used to be 7.6 and the table "
        "is meant to close that" % gap)


def test_the_last_tenth_of_the_slider_really_is_louder(driver):
    """90 to 100 has to do something, or the range is a lie.

    It cannot do much on a voice already at its ceiling -- the engine clamps
    volm at 2.0 -- so this asks a voice with room, and only that it rises.
    """
    _warm(driver)
    text = "Testing the top of the volume range."
    voice = "Bruce" if "Bruce" in driver._get_availableVoices() else None
    if voice is None:
        import pytest
        pytest.skip("need Bruce, which has headroom above its clean maximum")
    driver._set_voice(voice)
    driver._set_volume(90)
    clean = _rms(driver._render(text, driver._wpm(), voice))
    driver._set_volume(100)
    hot = _rms(driver._render(text, driver._wpm(), voice))
    driver._set_volume(90)
    assert hot > clean * 1.05, (
        "100 gave %.0f against 90's %.0f -- the last tenth does nothing"
        % (hot, clean))
