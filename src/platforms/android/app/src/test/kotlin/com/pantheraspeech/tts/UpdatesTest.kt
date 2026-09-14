package com.pantheraspeech.tts

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The pure half of the update check: which release, and is it newer. */
class UpdatesTest {

    /** The repository's own shape: `/releases/latest` points at 3.0.0, which
     * carries the desktop files, and the Android-only 3.0.2 sits above it
     * without being "latest". A draft and a pre-release are in the way. */
    private val releases = """
        [
          {"tag_name": "pantheraspeech/v3.2.0", "draft": true, "prerelease": false,
           "html_url": "https://example/draft",
           "assets": [{"name": "pantheraspeech-3.2.0.apk", "browser_download_url": "https://example/draft.apk"}]},
          {"tag_name": "pantheraspeech/v3.1.0-rc1", "draft": false, "prerelease": true,
           "html_url": "https://example/rc",
           "assets": [{"name": "pantheraspeech-3.1.0.apk", "browser_download_url": "https://example/rc.apk"}]},
          {"tag_name": "pantheraspeech/v3.0.2", "draft": false, "prerelease": false,
           "html_url": "https://example/3.0.2",
           "assets": [{"name": "pantheraspeech-3.0.2.apk", "browser_download_url": "https://example/3.0.2.apk"}]},
          {"tag_name": "pantheraspeech/v3.0.0", "draft": false, "prerelease": false,
           "html_url": "https://example/3.0.0",
           "assets": [{"name": "pantheraspeech-3.0.0.nvda-addon", "browser_download_url": "https://example/3.0.0.nvda-addon"},
                      {"name": "pantheraspeech-3.0.0.apk", "browser_download_url": "https://example/3.0.0.apk"}]},
          {"tag_name": "pantheraspeech/v2.0.4", "draft": false, "prerelease": false,
           "html_url": "https://example/2.0.4",
           "assets": [{"name": "pantheraspeech-2.0.4.nvda-addon", "browser_download_url": "https://example/2.0.4.nvda-addon"}]}
        ]
    """.trimIndent()

    @Test fun versionsAreReadOutOfTags() {
        assertEquals(listOf(3, 0, 2), Updates.parseVersion("pantheraspeech/v3.0.2"))
        assertEquals(listOf(3, 1), Updates.parseVersion("3.1"))
        assertNull(Updates.parseVersion("no numbers here"))
        assertNull(Updates.parseVersion(null))
    }

    @Test fun newerMeansStrictlyHigherWithPadding() {
        assertTrue(Updates.isNewer("3.1", "3.0.9"))
        assertTrue(Updates.isNewer("pantheraspeech/v3.0.3", "3.0.2"))
        assertFalse(Updates.isNewer("3.0", "3.0.0"))
        assertFalse(Updates.isNewer("3.0.2", "3.0.2"))
        assertFalse(Updates.isNewer("2.9.9", "3.0.0"))
        assertFalse(Updates.isNewer("junk", "3.0.0"))
        assertFalse(Updates.isNewer("3.0.1", null))
    }

    @Test fun theNewestReleaseWithAnApkWinsNotTheOneMarkedLatest() {
        val newest = Updates.newestApk(releases)
        assertNotNull(newest)
        assertEquals("3.0.2", newest!!.number)
        assertEquals("https://example/3.0.2.apk", newest.apk)
        assertEquals("https://example/3.0.2", newest.page)
    }

    @Test fun draftsAndPreReleasesAreNeverOffered() {
        val newest = Updates.newestApk(releases)!!
        assertFalse(newest.apk.contains("draft"))
        assertFalse(newest.apk.contains("rc"))
    }

    @Test fun aListWithoutAnApkAnswersNothing() {
        assertNull(Updates.newestApk("""[{"tag_name": "v9.9", "assets": [{"name": "x.nvda-addon", "browser_download_url": "u"}]}]"""))
        assertNull(Updates.newestApk("[]"))
    }

    @Test fun checkSaysAvailableUpToDateOrWhyNot() {
        val available = Updates.check("3.0.1") { releases }
        assertTrue(available is Updates.Answer.Available)
        assertEquals("3.0.2", (available as Updates.Answer.Available).release.number)

        val current = Updates.check("3.0.2") { releases }
        assertTrue(current is Updates.Answer.UpToDate)

        val ahead = Updates.check("3.5.0") { releases }
        assertTrue(ahead is Updates.Answer.UpToDate)

        val offline = Updates.check("3.0.2") { throw java.io.IOException("Unable to resolve host") }
        assertTrue(offline is Updates.Answer.Failed)
        assertEquals("Unable to resolve host", (offline as Updates.Answer.Failed).reason)

        val garbage = Updates.check("3.0.2") { "<html>rate limited</html>" }
        assertTrue(garbage is Updates.Answer.Failed)
    }
}
