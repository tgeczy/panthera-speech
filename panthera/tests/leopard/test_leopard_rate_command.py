# -*- coding: utf-8 -*-
"""An add-on asking for a different speed has to get one.

Reported by **Amir**, who wrote *Typing & Spelling Rate*:

    "Panthera speech doesn't work with my add-on... So I can't get Panthera
    speech (Alex and other Lion voices) to type or spell with different rates.
    Even setting these parameters to 100 percent in my add-on doesn't affect
    Alex and other Lion voices."

His add-on speaks `[RateCommand(offset=N), *seq, RateCommand()]`, which is the
documented way to ask, and every Panthera voice ignored it at every setting.

**The trap worth naming: `supportedCommands` is advisory, not a filter.** NVDA
does not remove a command a driver has left out of that set -- it arrives at
`speak()` all the same, and the loop there dropped it by falling off the end.
So the fault was two faults wearing one coat: callers that *do* consult the set
(MathCAT is one) declined to send the command at all, and callers that do not
consult it sent one and were ignored in silence.

NVDA never emits a `RateCommand` itself -- only SSML and add-ons do -- which is
exactly why nothing in this suite had ever noticed. That is the argument for
testing the whole documented interface rather than the parts NVDA happens to
exercise.
"""
import speech.commands as commands


def _speak(driver, seq):
    """-> the (kind, value) items the worker would see for `seq`."""
    captured = []
    real = driver._queue.put
    driver._queue.put = captured.append
    try:
        driver.speak(seq)
    finally:
        driver._queue.put = real
    return captured[0] if captured else []


def test_the_command_is_declared(driver):
    """Not decoration: MathCAT and others read this set and stay quiet."""
    assert commands.RateCommand in driver.supportedCommands


def test_a_rate_command_survives_speak(driver):
    """It used to fall off the end of the loop and vanish."""
    items = _speak(driver, [commands.RateCommand(offset=30), "hello",
                            commands.RateCommand()])
    assert items == [("rate", 30), ("text", "hello"), ("rate", 0)]


def test_the_offset_moves_the_words_per_minute(driver):
    """On NVDA's own 0-100 scale, like the pitch offset beside it."""
    driver.rate = 50
    plain = driver._wpm()
    assert driver._wpm(30) > plain
    assert driver._wpm(-30) < plain


def test_it_clamps_rather_than_overflowing(driver):
    """Amir's add-on offers up to 100, and 50 + 100 is not a rate.

    Clamping is what the pitch offset already does, and it means an add-on
    asking for more than the slider has gets the top of the slider instead of
    an exception on NVDA's main thread.
    """
    driver.rate = 50
    assert driver._wpm(100) == driver._wpm(50)      # both land on 100
    assert driver._wpm(-100) == driver._wpm(-50)    # and both on 0


def test_a_rate_change_is_not_applied_backwards(driver):
    """The text before the command was asked for at the old rate.

    The flush has to happen *before* the new rate is adopted, or the first
    words of an utterance come out at the speed the end of it asked for.
    """
    items = _speak(driver, ["before", commands.RateCommand(offset=40),
                            "after"])
    assert items == [("text", "before"), ("rate", 40), ("text", "after")]


def test_a_rate_change_stops_a_join(driver):
    """Joining across one would speak the next utterance at this one's speed.

    The join exists to give Alex whole sentences to breathe in, and it holds a
    fragment back waiting for more -- so it is exactly the mechanism that would
    carry a rate change forward into text that never asked for it.
    """
    seq = [("index", 1), ("rate", 40), ("text", "one letter")]
    assert driver._join(seq, driver._epoch) == seq


# -- and the audio actually changes ---------------------------------------

def _spoken_bytes(driver, seq, timeout=15.0):
    """-> how much audio `seq` produced, through the whole driver.

    Waits on `synthDoneSpeaking` rather than on the byte count going still,
    which is what the first version of this did -- and a sequence with a rate
    change in it flushes more than once, so the count *does* go still in the
    middle and the measurement stopped after the first piece. It read as the
    reset having failed.
    """
    import synthDriverHandler
    done = synthDriverHandler.synthDoneSpeaking
    done.arm()
    p = driver._player
    before = p.bytes
    driver.speak(seq)
    assert done.wait(timeout), "the driver never reported it had finished"
    return p.bytes - before


TEXT = "The quick brown fox jumps over the lazy dog and keeps on running."


def test_a_boosted_rate_really_renders_less_audio(driver):
    """The end of it: same words, faster, so fewer samples.

    Everything above this checks the plumbing; this one checks that the
    plumbing reaches the engine. A generous margin -- 10% -- because the point
    is that the rate moved at all, not by how much.
    """
    driver._set_voice("Fred")
    driver.rate = 40
    plain = _spoken_bytes(driver, [TEXT])
    boosted = _spoken_bytes(driver, [commands.RateCommand(offset=40), TEXT,
                                     commands.RateCommand()])
    assert plain > 0 and boosted > 0, "nothing was spoken at all"
    assert boosted < plain * 0.9, (
        "the same text took %d bytes at the user's rate and %d with a "
        "RateCommand asking for +40 -- the command is not reaching the engine"
        % (plain, boosted))


def test_and_the_reset_puts_it_back(driver):
    """`RateCommand()` means "the user's setting again", not "keep going"."""
    driver._set_voice("Fred")
    driver.rate = 40
    plain = _spoken_bytes(driver, [TEXT])
    after = _spoken_bytes(driver, [commands.RateCommand(offset=40), "quickly",
                                   commands.RateCommand(), TEXT])
    # `after` carries one extra word, so it can only be longer than a clean
    # render if the reset worked; if it did not, all of TEXT was rushed too.
    assert after > plain * 0.9, (
        "after a reset the text still rendered short (%d against %d), so the "
        "rate never went back to the user's setting" % (after, plain))


# -- and the one beside it ------------------------------------------------

def test_the_volume_command_is_declared_too(driver):
    """Not the reported bug -- the drift that produced it.

    The sibling ROM driver has accepted all five of these since its own
    sequence work, and this one had three. Nothing NVDA does emits a
    `VolumeCommand` either, so it would have gone the same way: unreported
    until somebody's add-on quietly did nothing.
    """
    assert commands.VolumeCommand in driver.supportedCommands


def test_a_volume_command_survives_speak(driver):
    items = _speak(driver, [commands.VolumeCommand(offset=-20), "quietly",
                            commands.VolumeCommand()])
    assert items == [("volume", -20), ("text", "quietly"), ("volume", 0)]


def test_the_volume_offset_reaches_the_engine_command(driver):
    """It becomes `[[volm]]`, which is the engine's own, not gain afterwards.

    Checked on the text handed to the host rather than on the audio, because
    the level is per-voice: what proves the offset arrived is that the number
    in the command moved, and by the right sign.
    """
    driver._set_voice("Fred")
    sent = []
    real = driver._render
    driver._render = lambda text, *a, **k: sent.append((text, k.get("volume", 0))) or b""
    try:
        driver._flush(["hello"], driver._wpm(), "Fred", 0, driver._epoch,
                      None, -30)
    finally:
        driver._render = real
    assert sent and sent[0][1] == -30, \
        "the volume offset never reached _render: %r" % (sent,)
