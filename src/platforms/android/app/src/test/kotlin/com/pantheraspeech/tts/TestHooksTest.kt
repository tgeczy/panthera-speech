package com.pantheraspeech.tts

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** What the exported settings screen takes from an intent (TestHooks.kt,
 * panthera-speech#40): a test hook only in a debug build and only on a fresh
 * start; the adb import only on a fresh start, asking first in a release. */
class TestHooksTest {
    @Test fun aReleaseBuildTakesNoTestHook() {
        assertFalse("a release build ran a test hook another app could send", TestHooks.on(debuggable = false, fresh = true))
        assertFalse(TestHooks.on(debuggable = false, fresh = false))
    }

    @Test fun aDebugBuildTakesItsHooksOnAFreshStartOnly() {
        assertTrue(TestHooks.on(debuggable = true, fresh = true))
        assertFalse("a test hook ran again when the screen was rebuilt", TestHooks.on(debuggable = true, fresh = false))
    }

    @Test fun theAdbImportRunsOnceAndAsksFirstInARelease() {
        assertTrue(TestHooks.route(fresh = true))
        assertFalse("an import ran again when the screen was rebuilt", TestHooks.route(fresh = false))
        assertTrue("a release build imported a zip without asking", TestHooks.confirm(debuggable = false))
        assertFalse(TestHooks.confirm(debuggable = true))
    }

    @Test fun onlyAFirstStartIsFresh() {
        assertTrue(TestHooks.fresh(rebuilt = false, fromHistory = false))
        assertFalse("a rebuilt screen counted as a fresh start", TestHooks.fresh(rebuilt = true, fromHistory = false))
        assertFalse("a screen reopened from Recents counted as a fresh start", TestHooks.fresh(rebuilt = false, fromHistory = true))
    }
}
