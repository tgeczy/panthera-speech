# -*- coding: utf-8 -*-
"""Silence must not cost you the next thing you ask for.

**This is the bug that shipped in 0.95.0**, and three people described it from
three directions before anything in this repository could see it:

    Jerry: "if I don't do anything on the computer for maybe a minute or two,
    then I have to do something like check the time which won't talk, so that
    if I do something that I want to do can talk."

    Alex Chapman: "I noticed with LionSpeech it has a habbit of deciding to not
    read whatever you just focused more often than LeopardSpeech."

Five seconds after it stops speaking, 10.7's engine arms a GCD one-shot on
`_MTBEAudioUnitDeferredStopAudioGraph`, which calls `AUGraphStop` and moves
`MTBEAudioUnitSoundOutput` into its stopped state.  On a Mac that hands the
audio device back.  Here the next `SESpeakBuffer` simply never returns -- not
a short render, not an error, no audio and no answer at all -- so the driver
waits out its timeout and NVDA says nothing.

Leopard is immune and always was: 10.5's MacinTalk imports no libdispatch
whatsoever, so there is no deferred anything.  That is the whole of the
difference the reports were pointing at, and it is why the same complaint from
the same people about the same voices never came up for `leopardspeech`.

It is not voice-specific either, which the reports did make it look like:
measured with the fix backed out, Fred, Victoria, Vicki and Alex all wedge.
`tiger_host_gcd.c` refuses to arm that one timer.

The delay is the engine's own 5000 ms, logged by `TIGER_GCD_LOG=1`.  Waiting
six and a half is enough to be past it and short enough to stay in a suite.
"""
import time

#: Longer than the engine's own five seconds, with enough margin that a loaded
#: machine cannot make the test pass by being slow to get here.
IDLE = 6.5

TEXT = "Check the time."


def test_speech_survives_a_silence(driver):
    """Speak, say nothing for longer than the engine's patience, speak again."""
    voice = driver._voices[0][0]
    wpm = driver._wpm()
    first = driver._render(TEXT, wpm, voice)
    assert first, "the first render produced nothing, so the test proves nothing"

    time.sleep(IDLE)

    started = time.time()
    second = driver._render(TEXT, wpm, voice)
    elapsed = time.time() - started

    assert second, (
        "nothing came back after %.1f s of silence -- the engine's deferred "
        "audio-graph stop has fired and the channel is wedged" % IDLE)
    assert second == first, (
        "the same text rendered differently either side of a silence: "
        "%d frames then %d" % (len(first) // 2, len(second) // 2))
    #: A wedged channel used to be indistinguishable from a slow one until the
    #: driver gave up, so the timing is worth asserting on its own.
    assert elapsed < 5.0, (
        "the render after a silence took %.1f s; it takes about a third of a "
        "second warm" % elapsed)
