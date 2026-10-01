package com.pantheraspeech.tts

import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL

/**
 * Is there a newer APK than the one running?
 *
 * Asked for in panthera-speech#19. The answer stops at pointing, on purpose.
 * An app cannot install an APK unless the person has switched on "install
 * unknown apps" for it, one app at a time, and asking for that is the sort
 * of thing Google treats third-party apps badly for. So the check finds the
 * release and hands its APK to the browser: the browser downloads it,
 * opening the download installs it, and Android asks once to let the
 * *browser* install apps. Slower than an updater, and it asks for nothing.
 *
 * **Only ever when somebody presses the button.** The desktop add-on's rule,
 * kept for the same reason: an engine that quietly contacts a server on its
 * own tells that server when its owner picked the phone up.
 *
 * `/releases/latest` is not consulted, and **the version does not come from the
 * tag**. A release carries assets for things that do not move together -- the
 * APK, the add-on, the SAPI installer, the Linux tarballs -- so a release whose
 * tag is newer than its APK would announce an update that is not there, offer
 * the file already installed, and go on offering it, because what is installed
 * never catches up with a tag. Each thing is versioned by its own asset's
 * filename instead: `panthera-android-3.3.1.apk` says 3.3.1 wherever it hangs.
 * This walks the recent releases for the newest such APK. The version
 * comparison is pure and tested; the fetch is the smallest piece that reaches
 * the network.
 */
object Updates {
    const val RELEASES_API =
        "https://api.github.com/repos/tgeczy/panthera-speech/releases?per_page=20"
    const val RELEASES_PAGE = "https://github.com/tgeczy/panthera-speech/releases"

    /** What this project's APK asset is called. Pinned to the shape rather than
     * to `.apk` alone, so a second APK ever attached to a release -- a sibling
     * project's, a one-off build -- is not mistaken for this one. */
    val APK_ASSET = Regex("""^panthera-android-\d[\d.]*\.apk$""", RegexOption.IGNORE_CASE)

    class Release(val version: String, val page: String, val apk: String) {
        /** "3.3.1" out of "panthera-android-3.3.1.apk". */
        val number: String get() = parseVersion(version)?.joinToString(".") ?: version
    }

    sealed class Answer {
        class UpToDate(val installed: String) : Answer()
        class Available(val release: Release, val installed: String) : Answer()
        class Failed(val reason: String) : Answer()
    }

    private val VERSION = Regex("""(\d+(?:\.\d+)*)""")

    /** -> the numbers in a version, or null if there are none. */
    fun parseVersion(text: String?): List<Int>? {
        val found = text?.let { VERSION.find(it) } ?: return null
        return try { found.groupValues[1].split('.').map { it.toInt() } }
            catch (e: NumberFormatException) { null }
    }

    /** True when `latest` is strictly higher than `installed`. Padded, so
     * 3.1 beats 3.0.9 and 3.0 ties 3.0.0. Either side unreadable is false:
     * a check that cannot tell should say nothing rather than announce an
     * update that may not exist. */
    fun isNewer(latest: String?, installed: String?): Boolean {
        val a = parseVersion(latest) ?: return false
        val b = parseVersion(installed) ?: return false
        val width = maxOf(a.size, b.size)
        val pa = a + List(width - a.size) { 0 }
        val pb = b + List(width - b.size) { 0 }
        for (i in 0 until width) if (pa[i] != pb[i]) return pa[i] > pb[i]
        return false
    }

    /** The newest APK across the published releases, by the version in its own
     * filename -- not by the tag of the release carrying it.
     *
     * Releases arrive newest first, but the newest **APK** decides: a later
     * release that carries only desktop files must not hide an APK published
     * before it, and a release carrying an older APK for people who want one
     * must not claim to be an upgrade of it. Drafts and pre-releases are passed
     * over, so a draft under test never advertises itself.
     *
     * -> null when no release carries an APK this recognises. */
    fun newestApk(json: String): Release? {
        val releases = org.json.JSONArray(json)
        var best: Release? = null
        for (i in 0 until releases.length()) {
            val r = releases.getJSONObject(i)
            if (r.optBoolean("draft", false) || r.optBoolean("prerelease", false)) continue
            val assets = r.optJSONArray("assets") ?: continue
            val page = if (r.has("html_url")) r.getString("html_url") else RELEASES_PAGE
            for (j in 0 until assets.length()) {
                val a = assets.getJSONObject(j)
                val name = a.optString("name", "")
                if (!APK_ASSET.matches(name) || !a.has("browser_download_url")) continue
                if (parseVersion(name) == null) continue
                // Newest release first, so a strict comparison keeps the page
                // link on the first release carrying a given APK.
                if (best == null || isNewer(name, best.version))
                    best = Release(name, page, a.getString("browser_download_url"))
                break
            }
        }
        return best
    }

    /** The one piece that reaches the network. */
    fun fetch(url: String, timeoutMs: Int = 10000): String {
        val c = URL(url).openConnection() as HttpURLConnection
        try {
            c.connectTimeout = timeoutMs
            c.readTimeout = timeoutMs
            c.setRequestProperty("User-Agent", "panthera-speech-android")
            c.setRequestProperty("Accept", "application/vnd.github+json")
            if (c.responseCode !in 200..299) throw IOException("HTTP ${c.responseCode}")
            return c.inputStream.bufferedReader().use { it.readText() }
        } finally {
            c.disconnect()
        }
    }

    /** `opener` exists for the tests: anything that takes a URL and returns
     * the body. The default reaches the network. */
    fun check(installed: String, opener: (String) -> String = { fetch(it) }): Answer {
        val json = try { opener(RELEASES_API) } catch (e: Exception) {
            return Answer.Failed(e.message?.takeIf { it.isNotBlank() } ?: e.javaClass.simpleName)
        }
        val newest = try { newestApk(json) } catch (e: Exception) {
            return Answer.Failed("the release list could not be read")
        } ?: return Answer.Failed("no release carries an APK")
        return if (isNewer(newest.version, installed)) Answer.Available(newest, installed)
               else Answer.UpToDate(installed)
    }
}
