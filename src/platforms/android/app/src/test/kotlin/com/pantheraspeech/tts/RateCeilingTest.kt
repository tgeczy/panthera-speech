package com.pantheraspeech.tts

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** What rate the engine is actually asked for.
 *
 * 500 was never the engine's limit, it was this app's: the host honours whatever
 * it is asked for, and measured on the desktop through the same host, Alex
 * delivers a sentence 2.4 times faster at 1200 wpm than at 500. Android was the
 * one front end that could not ask -- the NVDA driver has had the switch and the
 * same 1200 ceiling for a while, and the SAPI voices reach 1200 too -- so
 * somebody already at the top of the slider asking for more got exactly what
 * they had. Credit to Jade, who reported hearing precisely that.
 *
 * `Settings.wpm` is pure, so the arithmetic is pinned here rather than on a
 * phone.
 */
class RateCeilingTest {

    private fun settings(rate: Int, boost: Boolean) = PantheraEngine.Settings(
        volume = PantheraEngine.VOLUME_SYSTEM_DEFAULT, rate = rate,
        numbers = PantheraEngine.NUMBER_STYLE_DEFAULT, rateBoost = boost)

    /** The switch moves the top and nothing else. */
    @Test fun theSwitchRaisesOnlyTheCeiling() {
        assertEquals(500, settings(0, false).rateCeiling)
        assertEquals(1200, settings(0, true).rateCeiling)
        // The same 1200 the NVDA driver's RATE_MAX_BOOST reaches, so one setting
        // means one speed whichever front end is speaking.
        assertEquals(1200, PantheraEngine.RATE_CEILING_BOOST)
    }

    /** A rate set here: the bug Jade hit, and its fix. */
    @Test fun aChosenRateAboveFiveHundredNeedsTheSwitch() {
        assertEquals(500, settings(1200, false).wpm(100))
        assertEquals(1200, settings(1200, true).wpm(100))
        assertEquals(900, settings(900, true).wpm(100))
    }

    /** **The other way in, which is the likelier one.** Following the system's
     * own text-to-speech rate is where a screen reader's speed lives, and it
     * needs no visit to these settings at all. TalkBack at 400% asks for 720,
     * which was quietly cut to 500. */
    @Test fun followingTheSystemRateIsLiftedToo() {
        assertEquals(500, settings(0, false).wpm(400))
        assertEquals(720, settings(0, true).wpm(400))
        // And the engine's own default stays the default at 100%.
        assertEquals(180, settings(0, false).wpm(100))
        assertEquals(180, settings(0, true).wpm(100))
    }

    /** The floor never moves. A boost that also made slow slower would be a
     * different setting wearing this one's name. */
    @Test fun theFloorIsUntouched() {
        for (boost in listOf(false, true)) {
            assertEquals(80, settings(10, boost).wpm(100))
            assertEquals(80, settings(0, boost).wpm(1))
            assertEquals(PantheraEngine.RATE_FLOOR, settings(1, boost).wpm(100))
        }
    }

    /** Nothing changes for somebody who never turns it on -- which, the day it
     * ships, is everybody. Every rate reachable before is reachable now and
     * answers the same. */
    @Test fun theSwitchOffBehavesExactlyAsBefore() {
        for (rate in listOf(0, 80, 120, 180, 300, 499, 500)) {
            val before = if (rate > 0) rate.coerceIn(80, 500) else 180
            assertEquals("rate $rate", before, settings(rate, false).wpm(100))
        }
        for (requested in listOf(0, 50, 100, 200, 277, 400)) {
            val expected = (180 * (if (requested <= 0) 100 else requested) / 100).coerceIn(80, 500)
            assertEquals("system rate $requested", expected, settings(0, false).wpm(requested))
        }
    }

    /** With it on, the whole span to the new top is reachable and monotonic --
     * no step where asking for more gives less. */
    @Test fun theBoostedSpanIsReachableAndMonotonic() {
        var last = 0
        for (rate in 80..1200 step 20) {
            val got = settings(rate, true).wpm(100)
            assertEquals("rate $rate", rate, got)
            assertTrue("$got after $last", got > last)
            last = got
        }
        assertEquals(1200, settings(5000, true).wpm(100))
    }
}
