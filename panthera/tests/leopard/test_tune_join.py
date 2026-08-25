# -*- coding: utf-8 -*-
"""A song is one utterance, however many chunks say-all hands it over in.

The engine gives the last note of every utterance a sentence-final pitch
fall, and 1.1.1's input-mode carry made each say-all chunk its own
utterance -- so every verse ended on a droop that is in no score
(panthera-speech#11).  A tune's prosodic "." and "!" phonemes count as
sentence ends, which is exactly why the prose joiner's two-sentence rule
kept cutting: mid-song the joiner now holds until the song ends, the mode
closes, or TUNE_JOIN_MAX_CHARS.

Whole-buffer rendering is the only reference ever validated -- the
reporter's passage in one utterance matches their real-Mac golden
note for note -- so joining reproduces the one behaviour known correct.
"""
import time

import pantheradriver

#: A verse: prosodic sentence-enders and all, exactly the shape that used
#: to stop the prose joiner after two "sentences".
VERSE = ("m{D 200; P 165:0 165:100} .{D 140; P 165:0 165:100} "
         "m{D 200; P 175:0 175:100} . "
         "m{D 200; P 185:0 185:100} ")


def _renders(driver, items, seconds=25.0):
    """-> the texts `_render` was asked for after feeding `items`."""
    texts = []
    realRender = driver._render

    def render(text, wpm, voice, *a, **k):
        texts.append(text)
        return realRender(text, wpm, voice, *a, **k)

    driver._render = render
    try:
        for item in items:
            driver._queue.put(item)
        end = time.time() + seconds
        last, still = 0, 0.0
        while time.time() < end:
            time.sleep(0.05)
            if len(texts) != last:
                last, still = len(texts), 0.0
            else:
                still += 0.05
                if last and still >= 1.5:
                    break
        return list(texts)
    finally:
        driver._render = realRender


def _song(chunks):
    """Chunks the way say-all hands a tune file over: the switch only in
    the first, an index at the head of every line."""
    items = []
    for i, chunk in enumerate(chunks):
        text = ("[[inpt TUNE]] " + chunk) if i == 0 else chunk
        items.append([("index", i + 1), ("text", text)])
    return items


def test_a_chunked_song_is_one_utterance(driver, monkeypatch):
    monkeypatch.setattr(driver, "INPUT_MODES_WORK", True)
    driver._acceptCommands = True
    driver.speak(["warming up the engine"])
    time.sleep(1.0)
    texts = _renders(driver, _song([VERSE, VERSE, VERSE]))
    driver._inputMode = None
    joined = [t for t in texts if "inpt TUNE" in t]
    assert len(joined) == 1, (
        "three verses rendered as %d utterances -- every verse-final note "
        "takes a pitch fall again" % len(joined))
    assert joined[0].count("m{D 200") == 9, (
        "the one utterance does not carry all three verses: %r" % joined[0])


def test_the_join_ignores_the_breathe_setting(driver, monkeypatch):
    """Breathing is comfort; one-utterance songs are correctness."""
    monkeypatch.setattr(driver, "INPUT_MODES_WORK", True)
    driver._acceptCommands = True
    monkeypatch.setattr(driver, "_joinSentences", False)
    driver.speak(["warming up the engine"])
    time.sleep(1.0)
    texts = _renders(driver, _song([VERSE, VERSE]))
    driver._inputMode = None
    joined = [t for t in texts if "inpt TUNE" in t]
    assert len(joined) == 1, (
        "with the breathe setting off the song split into %d utterances"
        % len(joined))


def test_closing_the_mode_hands_back_to_prose(driver, monkeypatch):
    """`[[inpt TEXT]]` ends the song; what follows obeys the prose rules."""
    monkeypatch.setattr(driver, "INPUT_MODES_WORK", True)
    driver._acceptCommands = True
    driver.speak(["warming up the engine"])
    time.sleep(1.0)
    closing = VERSE + "[[inpt TEXT]] And that was the song."
    texts = _renders(driver, _song([VERSE, closing]))
    assert driver._inputMode is None, (
        "the closed mode is still carried: %r" % driver._inputMode)
    joined = [t for t in texts if "inpt TUNE" in t]
    assert len(joined) == 1 and "that was the song" in joined[0], (
        "the closing chunk did not join the song it ends: %r" % texts)


def test_cancel_mid_song_still_stops(driver, monkeypatch):
    """A whole song as one buffer is the new longest render; cancel must
    still cut it and the next utterance must still speak."""
    monkeypatch.setattr(driver, "INPUT_MODES_WORK", True)
    driver._acceptCommands = True
    driver.speak(["warming up the engine"])
    time.sleep(1.0)
    long_verse = VERSE * 20
    for item in _song([long_verse, long_verse]):
        driver._queue.put(item)
    time.sleep(0.2)
    driver.cancel()
    assert driver._inputMode is None, "cancel did not drop the carried mode"
    fed = driver._player.fed
    driver.speak(["still here"])
    end = time.time() + 20.0
    while time.time() < end and driver._player.fed == fed:
        time.sleep(0.1)
    assert driver._player.fed > fed, (
        "nothing spoke after a mid-song cancel -- the worker is wedged")
