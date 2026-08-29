# -*- coding: utf-8 -*-
"""The numbers this driver was tuned to, and the one function that uses them.

Split out of `pantheradriver.py` unchanged.  They are here rather than inline
because every one of them is a measurement -- the comment above each says what
was measured, on what, and what went wrong when it was set differently -- and a
table of measurements reads better as a table than as a preamble to two
thousand lines of driver.

Nothing here imports anything, and nothing here has state.  `pantheradriver`
re-exports every name, so `pantheradriver.OUT_RATE` still resolves for the
drivers and the tests that reach for it.
"""

#: Sample format the engine renders in.  Read from the StreamFormat it sets,
#: not assumed: 22050 Hz, mono, and the host converts its 32-bit float to 16.
OUT_RATE = 22050

#: How far ahead of the speakers the feeder may run, in seconds.
#:
#: Small enough that an interrupt cannot leave much behind, large
#: enough that playback never catches up with the renderer.
#:
#: **It also has to stay under the output device's own buffer**, which is
#: what makes `feed()` block, and blocking there is what a cancel collides
#: with -- see FEED_SLICE.  Measured from two uninterrupted feeds in a user's
#: log: 939 ms of audio blocked 614 ms, and 457 ms blocked 203 ms, so the
#: device holds 250 to 330 ms.  This plus one slice has to be less than that.
FEED_LEAD = 0.15

#: The largest piece of audio handed to the player in one call, in seconds.
#:
#: **The host answers a streamed request with the whole utterance in one
#: chunk.**  Every log line says "in 1 chunk(s)", for 40 characters and for
#: 740 alike, because the engine calls back once per `SESpeakBuffer`.  So the
#: pacing above never engaged -- one `feed()` carried seventeen seconds of
#: audio -- and that one call blocked, holding `_playerLock`, for as long as
#: it took the device to drain: measured 643, 343, 294, 361 and 556 ms.
#:
#: In every one of those five, the call returned within 36 ms of the keypress
#: that cancelled it.  That is not a coincidence and it is not the device
#: being slow: `cancel()` was landing *inside* the feed, every single time.
#: It waits 20 ms for the lock the feeder is holding, gives up, and calls
#: `stop()` anyway -- which is exactly the unsynchronised stop-during-feed
#: this driver already knew stalls the next start, measured at 1839 ms.
#:
#: Slicing the audio here is what puts the pacing back in charge.  A slice
#: fits the device buffer, so `feed()` returns at once, so the lock is free
#: when `cancel()` asks for it, so the stop lands between feeds where it is
#: safe.  It also bounds what an interrupt leaves in the device to one
#: FEED_LEAD rather than a whole utterance.
FEED_SLICE = 0.08

#: NVDA's 0-100 rate onto words per minute.  180 is the engine's own default and
#: lands mid-slider, so the control behaves the way people expect.
RATE_MIN, RATE_MAX = 80, 400

#: The top of the slider with rate boost on.
#:
#: 400 was never the engine's limit, it was ours.  Measured, the engine
#: honours whatever it is asked for and stays stable well past anything
#: useful: Alex delivers 853 wpm when asked for 800, and 1598 when asked
#: for 1500, without a stumble.  A user asked how to get past 100% and the
#: honest answer was that nothing was stopping us but a constant.
#:
#: It is a separate switch rather than a wider slider because widening the
#: slider would silently make everyone's existing setting faster -- the
#: same mistake as a volume control that defaults to half.
RATE_MAX_BOOST = 1200

#: NVDA's 0-100 pitch onto an offset from the voice's own pitch, in tenths of
#: a semitone.  50 is the voice as Apple recorded it; the ends are an octave
#: either way, which is as far as any of these stay recognisable.
#:
#: An offset rather than an absolute value because every voice has its own
#: natural pitch -- Fred sits near 127 Hz, Bruce near 135 -- so an absolute
#: scale would make the middle of the slider mean something different for each.
#: The host asks the engine for the voice's own 'pbas' and adds this to it.
PITCH_SEMITONES = 12

#: NVDA's 0-100 inflection onto the engine's 'pmod', which is a percentage:
#: 0 is a monotone, 100 is roughly the voice as recorded, 200 is twice its
#: usual movement.  Measured on Alex over one sentence, mean F0 and how far
#: it wanders:
#:
#:     pmod   0    100.0 Hz, spread  8.6   (flat)
#:     pmod 100    111.3 Hz, spread 13.7
#:     pmod 200    121.5 Hz, spread 22.5   (very expressive)
#:
#: Nothing is sent at the halfway point, because no command at all is not
#: quite the same as pmod 100 -- untouched measures 117.0 Hz and 16.8 --
#: and the default has to be the engine exactly as it comes.
INFLECTION_MAX_PMOD = 200

#: Where the volume slider's 100 sits. Everything below it is clean; the last
#: tenth is deliberately allowed past what a voice can render without clipping,
#: for anyone who would rather have the loudness and accept the distortion.
#:
#: 90 rather than 100 so that headroom exists at all, and rather than 50
#: because NVDA's own default of 50 is genuinely too quiet -- Eloquence ships
#: 92 for the same reason.
VOLUME_CLEAN = 90

#: **The engine clamps `[[volm]]` at 2.0.** Asked for 3 or 4 it renders exactly
#: what 2 renders, so this is a real ceiling and not a guess.
VOLUME_MAX_VOLM = 2.0

#: No voice is given more than this, whatever its headroom measures.
#:
#: Right at the engine's own 2.0 clamp the arithmetic stops holding: Whisper
#: measures peak 32654 at 1.90 and then clips at 2.00, which is not the 3%
#: rise that gain would give. Something saturates there. Rather than model it,
#: stay below it -- the difference between 1.80 and 2.00 is 0.9 dB and nobody
#: can hear it, while the clipping it avoids is audible.
VOLUME_NORM_CEILING = 1.80

#: How far each voice may be turned up, so that one slider position means
#: roughly one loudness whichever voice is speaking.
#:
#: **Alex, the default voice, is the quietest speaking voice in the set.** At
#: `volm 1.0` -- which is what the driver used to send at 100 -- it measures
#: RMS 2473 against Bruce's 5899, nearly 8 dB down. That is why Leopard has
#: always sounded quieter than the Tiger and outSPOKEN add-ons, and it is what
#: this table exists to correct.
#:
#: Built by `tools/volume_table.py`, worst case across three probe texts,
#: because peak is set by whatever transient the text happens to contain and a
#: factor measured on prose will clip on a list of numbers. Each entry is
#:
#:     max(1.0, min(safe, loudest_voice_level / this_voice_level))
#:
#: so quiet voices come up to meet the loudest one and **nothing is ever
#: turned down**. Equalising properly -- bringing the loud voices down as well
#: -- was measured and rejected: the limit is `Whisper`, which is supposed to
#: be quiet, and matching it would have cost Bruce 8.9 dB and Alex 1.2.
#:
#: Voices that cannot reach the target keep their character: Whisper is still
#: the quietest thing here, it is simply 6 dB less faint than it was.
#:
#: Many of the formant voices share a ceiling of exactly 17200, which is a
#: limiter inside the engine rather than a coincidence, and gives them all the
#: same factor.
#:
#: **Every entry is held about 1 dB below the measured ceiling.** A first
#: version used the maximum exactly and Whisper clipped thirteen samples the
#: moment a test used a sentence the table had not been fitted to. A ceiling
#: fitted to five texts is not a ceiling for every text, and an inaudible
#: decibel is a cheap price for that.
VOLUME_NORM_LEOPARD = {
    "Agnes": 1.00,
    "Albert": 1.70,
    "Alex": 1.80,
    "BadNews": 1.80,
    "Bahh": 1.70,
    "Bells": 1.70,
    "Boing": 1.70,
    "Bruce": 1.00,
    "Bubbles": 1.70,
    "Cellos": 1.70,
    "Deranged": 1.70,
    "Fred": 1.80,
    "GoodNews": 1.80,
    "Hysterical": 1.70,
    "Junior": 1.80,
    "Kathy": 1.73,
    "Organ": 1.70,
    "Princess": 1.70,
    "Ralph": 1.70,
    "Trinoids": 1.70,
    "Vicki": 1.20,
    "Victoria": 1.00,
    "Whisper": 1.80,
    "Zarvox": 1.70,
}

#: What an unmeasured voice gets. 1.0 is the level it has always had, which is
#: the only safe answer: a voice nobody has measured might already be at the
#: ceiling, and guessing high turns it into distortion.
VOLUME_NORM_DEFAULT = 1.0


def volume_volm(volume, voice, norms):
    """-> the `[[volm]]` value for this slider position and this voice.

    `norms` is the generation's own table.  Passed in rather than read from a
    module global because the voice *names* are shared between Leopard and
    Lion while the recordings behind them are not, and a table picked by name
    alone would be measured on one bank and applied to the other.

    `VOLUME_CLEAN` is where the voice reaches its measured maximum, so 0..90
    is the clean range and 90..100 asks for more than the voice can render
    without clipping. That is the trade the slider's last tenth exists to
    offer, and it is why the default sits at 90 rather than at the top.
    """
    #: `None` rather than a table is the class default, and it has to mean
    #: "no normalisation" rather than an exception.  A generation whose table
    #: has not been measured yet is exactly the state the tool that measures
    #: it runs in -- so without this, building a table needs a table.
    norm = (norms or {}).get(voice, VOLUME_NORM_DEFAULT)
    return min(VOLUME_MAX_VOLM,
               norm * max(0, volume) / float(VOLUME_CLEAN))
