package com.pantheraspeech.tts

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The pure half of the update check: which release, and is it newer. */
class UpdatesTest {

    /** The repository's own shape, with its real asset names -- including the
     * inversion that made the tag useless as a version: the 3.0.0 release
     * carries `panthera-android-3.0.2.apk`, an APK newer than its own tag,
     * while the 3.0.2 release carries the 3.0.0 add-on. A draft and a
     * pre-release are in the way. */
    private val releases = """
        [
          {"tag_name": "pantheraspeech/v3.2.0", "draft": true, "prerelease": false,
           "html_url": "https://example/draft",
           "assets": [{"name": "panthera-android-3.2.0.apk", "browser_download_url": "https://example/draft.apk"}]},
          {"tag_name": "pantheraspeech/v3.1.0-rc1", "draft": false, "prerelease": true,
           "html_url": "https://example/rc",
           "assets": [{"name": "panthera-android-3.1.0.apk", "browser_download_url": "https://example/rc.apk"}]},
          {"tag_name": "pantheraspeech/v3.0.2", "draft": false, "prerelease": false,
           "html_url": "https://example/3.0.2",
           "assets": [{"name": "panthera-android-3.0.2.apk", "browser_download_url": "https://example/3.0.2.apk"}]},
          {"tag_name": "pantheraspeech/v3.0.0", "draft": false, "prerelease": false,
           "html_url": "https://example/3.0.0",
           "assets": [{"name": "pantheraspeech-3.0.0.nvda-addon", "browser_download_url": "https://example/3.0.0.nvda-addon"},
                      {"name": "panthera-android-3.0.2.apk", "browser_download_url": "https://example/3.0.0.apk"}]},
          {"tag_name": "pantheraspeech/v2.0.4", "draft": false, "prerelease": false,
           "html_url": "https://example/2.0.4",
           "assets": [{"name": "pantheraspeech-2.0.4.nvda-addon", "browser_download_url": "https://example/2.0.4.nvda-addon"}]}
        ]
    """.trimIndent()

    @Test fun versionsAreReadOutOfNames() {
        assertEquals(listOf(3, 3, 1), Updates.parseVersion("panthera-android-3.3.1.apk"))
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

    @Test fun theVersionIsTheApkFileAndNotTheTag() {
        // The 3.0.0 release carries the 3.0.2 APK, and the APK is what counts:
        // somebody on 3.0.2 must not be offered it again because a tag says 3.0.0,
        // and somebody on 3.0.0 must be offered it even though no tag says 3.0.2.
        val newest = Updates.newestApk(releases)!!
        assertEquals("3.0.2", newest.number)
        assertFalse(Updates.isNewer(newest.version, "3.0.2"))
        assertTrue(Updates.isNewer(newest.version, "3.0.0"))
    }

    @Test fun aNewerApkFurtherDownTheListStillWins() {
        // A later release carrying only desktop files must not hide an APK
        // published before it.
        val json = """
            [
              {"tag_name": "pantheraspeech/v3.4.0", "draft": false, "prerelease": false,
               "html_url": "https://example/3.4.0",
               "assets": [{"name": "pantheraspeech-3.4.0.nvda-addon", "browser_download_url": "https://example/a"}]},
              {"tag_name": "pantheraspeech/v3.3.1", "draft": false, "prerelease": false,
               "html_url": "https://example/3.3.1",
               "assets": [{"name": "panthera-android-3.3.1.apk", "browser_download_url": "https://example/3.3.1.apk"}]}
            ]
        """.trimIndent()
        val newest = Updates.newestApk(json)!!
        assertEquals("3.3.1", newest.number)
        assertEquals("https://example/3.3.1", newest.page)
    }

    @Test fun anotherProjectsApkIsNotMistakenForOurs() {
        val json = """
            [{"tag_name": "v9.9.9", "draft": false, "prerelease": false,
              "html_url": "https://example/x",
              "assets": [{"name": "outspoken-9.9.9.apk", "browser_download_url": "https://example/theirs.apk"},
                         {"name": "panthera-android-3.3.1.apk", "browser_download_url": "https://example/ours.apk"}]}]
        """.trimIndent()
        val newest = Updates.newestApk(json)!!
        assertEquals("3.3.1", newest.number)
        assertEquals("https://example/ours.apk", newest.apk)
    }

    @Test fun anApkWithNoVersionInItsNameIsRefused() {
        assertNull(Updates.newestApk("""[{"tag_name": "v9.9", "draft": false, "prerelease": false,
            "assets": [{"name": "panthera-android.apk", "browser_download_url": "u"}]}]"""))
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
