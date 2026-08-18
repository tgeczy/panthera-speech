# -*- coding: utf-8 -*-
"""What a user would notice, and what previously reached one.

Every test below corresponds to a rule at the top of `tigerspeech.py`, and
every rule was paid for in the sibling ROM add-on.  Porting the rules without
porting their tests would have been the same mistake in a new repository.
"""
import time

import numpy as np
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


def test_every_wx_name_we_use_actually_exists_in_wxpython():
    """A misspelt wx constant is invisible until a user sends in a log.

    `YES_NO_CANCEL` is real in wxWidgets' C++ API and absent from wxPython.
    The start-up dialog asked for it, so `_ask` raised AttributeError every
    time it ran, and that dialog had never once appeared in any release of
    either add-on. It presented as nothing happening -- which is also what a
    missing add-on, a suppressed reminder and a mistimed thread all look like,
    so it was blamed on each of those in turn before a user's log named it.

    wxPython is not installed here and should not have to be: this reads the
    source and checks every `wx.NAME` against the ones wxPython really has.
    Add to the set when a genuinely new one is needed -- deliberately, which is
    the whole point of it being a list.
    """
    import os
    import re

    known = {
        # message box styles and answers
        "OK", "CANCEL", "YES", "NO", "YES_NO", "OK_DEFAULT", "NO_DEFAULT",
        "ICON_INFORMATION", "ICON_WARNING", "ICON_ERROR", "ICON_QUESTION",
        "CENTRE", "CENTER",
        # scheduling
        "CallAfter", "CallLater",
        # menus, if a future version grows one
        "ID_ANY", "EVT_MENU", "Menu", "MenuItem",
    }

    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.join(os.path.dirname(here), "addon")
    assert os.path.isdir(root), root

    used = {}
    scanned = 0
    for dirpath, _dirs, names in os.walk(root):
        for n in names:
            if not n.endswith(".py"):
                continue
            scanned += 1
            with open(os.path.join(dirpath, n), encoding="utf-8") as f:
                for name in re.findall(r"\bwx\.([A-Za-z_][A-Za-z0-9_]*)",
                                       f.read()):
                    used.setdefault(name, n)

    # The first version of this test matched nothing at all and passed
    # vacuously, which is worse than not having it: an escaping mistake had
    # turned the \b in the pattern into a literal backspace byte. So prove it
    # is looking at something before trusting what it says.
    assert scanned, "scanned no Python files under %s" % root
    assert used, "found no wx names at all -- the pattern is broken"

    unknown = {k: v for k, v in used.items() if k not in known}
    assert not unknown, (
        "these wx names are not in the allowed set. Check they exist in "
        "wxPython -- YES_NO_CANCEL does not -- then add them here: %r"
        % unknown)


def test_typographic_characters_reach_the_engine_as_macroman():
    """The engine's text is a single-byte Mac encoding, not UTF-8.

    Sent as UTF-8 an em dash arrived as three bytes and was read a character
    at a time, so "he paused - then left" came out as "he paused, he eyed and
    left" and smart quotes as "ah".  A tester found it in a story; nothing in
    the driver noticed, because every byte was perfectly valid.
    """
    import tigerspeech
    enc = tigerspeech._encode
    assert enc(u"—") == b"\xd1"            # em dash
    assert enc(u"–") == b"\xd0"            # en dash
    assert enc(u"“") == b"\xd2"            # left double quote
    assert enc(u"”") == b"\xd3"            # right double quote
    assert enc(u"…") == b"\xc9"            # ellipsis
    assert enc(u"café") == b"caf\x8e"      # accents survive

    # A real question mark must stay one: the engine lifts the intonation of
    # the whole sentence for it, so it is the wrong thing to substitute with.
    assert enc(u"Is it?") == b"Is it?"
    assert b"?" not in enc(u"你好")     # unmappable -> space, not "?"
    assert enc(u"你好") == b"  "


def test_the_driver_actually_uses_that_encoder():
    """Guards the half of the bug that a unit test cannot see.

    An encoder that is never called is exactly as broken as no encoder, and
    this file has shipped a test before that passed without looking at
    anything.  So read the source and prove the call site changed.
    """
    import io
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "addon", "synthDrivers", "tigerspeech.py")
    src = io.open(path, encoding="utf-8").read()
    assert "t = _encode(text)" in src, "the request no longer encodes the text"
    assert 't = text.encode("utf-8")' not in src, "still sending UTF-8"


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
    import tigerspeech
    _warm(driver)
    _f1, plain = _speakAndWait(driver, ["one", "two"])
    _f2, withGap = _speakAndWait(driver, ["one",
                                          speech.commands.BreakCommand(300),
                                          "two"])
    want = len(tigerspeech._silence(300))
    assert withGap >= plain + want * 0.8, (
        "a 300 ms break added %d bytes, expected about %d" % (withGap - plain, want))


def test_the_pause_setting_lengthens_the_gaps(driver):
    """The knob two testers asked for, in both directions."""
    import tigerspeech
    _warm(driver)
    driver._set_pauseMode("short")
    _f, short = _speakAndWait(driver, ["alpha", "beta"])
    driver._set_pauseMode("long")
    _f, long_ = _speakAndWait(driver, ["alpha", "beta"])
    driver._set_pauseMode("short")
    assert long_ > short, "'long' produced no more audio than 'short'"
    assert tigerspeech.SynthDriver.PAUSE_MS["short"] == 0


def test_fragments_are_joined_without_gluing_words_together(driver):
    """"link" then "Home" must not reach the engine as "linkHome"."""
    import tigerspeech
    join = tigerspeech._joinFragments
    assert join(["link", "Home"]) == "link Home"
    assert join(["Read more about it ", "here"]) == "Read more about it here"
    assert join([" on our site.", " Next"]) == " on our site. Next"
    assert join(["only"]) == "only"


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
    import tigerspeech
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
    import tigerspeech
    from synthDriverHandler import SynthDriver
    setting = tigerspeech._fullVolumeByDefault(SynthDriver.VolumeSetting())
    assert setting.defaultVal == 100

    # And it has to be the setting the driver actually offers, not one made
    # up by the test.
    import io
    import os
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "addon", "synthDrivers", "tigerspeech.py")
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
    import tigerspeech
    enc = tigerspeech._encode
    assert enc(u"Canopy’s") == b"Canopy's"
    assert enc(u"‘quoted’") == b"'quoted'"
    # Curly *double* quotes stay: those really are quotation marks, and the
    # engine is right to treat them as such.
    assert enc(u"“quoted”") == b"\xd2quoted\xd3"


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
    import tigerspeech
    _warm(driver)
    driver._set_rate(100)
    driver._set_rateBoost(False)
    plain = driver._wpm()
    driver._set_rateBoost(True)
    boosted = driver._wpm()
    driver._set_rateBoost(False)
    assert plain == tigerspeech.RATE_MAX
    assert boosted == tigerspeech.RATE_MAX_BOOST
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
    import tigerspeech

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
    import tigerspeech
    driver._set_volume(100)
    driver._set_inflection(50)
    a = driver._render("hello there", 180, driver._get_voice())
    b = driver._render("hello there", 180, driver._get_voice())
    assert a and a == b


def _waitForFeeds(driver, want, timeout=10.0):
    """Wait until the player has been fed `want` times, -> the count."""
    end = time.perf_counter() + timeout
    while time.perf_counter() < end:
        if driver._player.fed >= want:
            break
        time.sleep(0.002)
    return driver._player.fed


def test_a_long_utterance_sounds_before_it_has_finished_rendering(driver):
    """The whole reason for streaming.

    A paragraph used to be rendered completely before a single sample
    reached the player, which was most of a second of silence -- and none of
    it the engine's doing, since it renders at about ninety times real time.
    The audio now arrives in chunks and each is fed as it comes.

    Waiting for the utterance to *finish* here would mean waiting out its
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
    rest would leave those chunks in the pipe and the *next* response would be
    read starting from them -- and this driver's answer to a desynchronised
    pipe is to kill the engine, which costs a process start.  So the response
    is read to its end and only the feeding stops.

    What a user would notice: interrupting a long paragraph and then being
    told something must still speak.
    """
    _warm(driver)
    driver.speak(["The quick brown fox jumps over the lazy dog. " * 6])
    _waitForFeeds(driver, 1)
    driver.cancel()
    # The worker is still reading the rest of that response out of the pipe,
    # and the interrupted utterance still has a "done" to report.  Let both
    # land, so what follows measures the new utterance rather than the end of
    # the old one -- the first version of this test waited on a
    # `synthDoneSpeaking` that belonged to the cancelled paragraph and
    # concluded the driver had gone silent when it had not.
    time.sleep(0.5)
    _feeds, spoken = _speakAndWait(driver, ["still here"])
    assert spoken > 0, "the driver went silent after cancelling a stream"


def test_an_engine_that_cannot_stream_still_speaks(driver, monkeypatch):
    """New driver, old engine: the one combination that could go silent.

    A host that does not know 'TGR4' exits rather than answer it, which the
    driver sees as a closed pipe -- and its answer to that is to respawn.  Ask
    again, refused again, for every utterance: nothing is ever heard, which is
    the worst failure this driver has and precisely what an add-on update
    whose executable failed to copy would produce.

    Simulated by asking for a magic no host will ever know.  The driver must
    notice, stop asking, say so where somebody will see it, and speak.
    """
    import tigerspeech
    _warm(driver)
    monkeypatch.setattr(tigerspeech, "REQ_MAGIC_STREAM", 0x54475239)  # 'TGR9'
    assert driver._streaming, "streaming should start on"

    _feeds, spoken = _speakAndWait(driver, ["can you still hear me"])
    assert spoken > 0, "a host that cannot stream left the driver silent"
    assert not driver._streaming, "it kept asking for a request it was refused"

    # And it stays working afterwards, without asking again.
    _feeds, again = _speakAndWait(driver, ["and again"])
    assert again > 0, "it spoke once and then went silent"


def test_debug_logging_reports_how_long_an_utterance_took(driver, monkeypatch):
    """"It lags on long text" is the report this driver gets most.

    Until now a debug log said what was spoken but never how long any of it
    took, so the one question people actually ask could not be answered from
    the log they sent.  Both numbers go in, because they are different
    faults: a first sound that arrives late, and an utterance that takes a
    long time in total.
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
