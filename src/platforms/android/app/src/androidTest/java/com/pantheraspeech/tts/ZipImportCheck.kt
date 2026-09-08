package com.pantheraspeech.tts

import android.app.Activity
import android.app.Instrumentation
import android.os.Bundle
import android.util.Log
import java.io.File
import java.util.zip.CRC32
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/**
 * The zip importer against zips built here, on the device, from nothing of
 * Apple's: a few kilobytes standing in for an engine, laid out every way a
 * person is likely to zip one. Run with
 *
 *     am instrument -w -e zipImport true \
 *         com.pantheraspeech.tts.test/com.pantheraspeech.tts.EngineSmokeTest
 *
 * Every zip is read both ways -- through the central directory, as a file
 * the picker hands over is, and front to back, as a cloud provider's pipe
 * is -- and the two must agree.
 */
object ZipImportCheck {
    private const val MT = ZipImport.MACINTALK
    private const val PLIST = ZipImport.INFO_PLIST
    private const val SD = ZipImport.DICTIONARY

    private fun plist(version: String) =
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<plist version=\"1.0\">\n<dict>\n" +
        "\t<key>CFBundleName</key>\n\t<string>MacinTalk</string>\n" +
        "\t<key>CFBundleShortVersionString</key>\n\t<string>$version</string>\n" +
        "\t<key>CFBundleVersion</key>\n\t<string>$version</string>\n</dict>\n</plist>\n"

    /** Stands in for the binary: noise, and for the engines that carry one,
     * the build path that names the version. Tiger's carries none. */
    private fun binary(version: String?): ByteArray {
        val noise = ByteArray(6000) { ((it * 7919) and 0xff).toByte() }
        val path = version?.let {
            "/SourceCache/SpeechSynthesis_MacInTalk/SpeechSynthesis-$it/Synthesizers/MacinTalk/x.cpp"
        } ?: return noise
        return noise + path.toByteArray() + ByteArray(500) { 3 }
    }

    private fun engine(prefix: String, version: String, withPlist: Boolean,
                       voices: List<String>, dictionary: Boolean = true): Map<String, ByteArray> {
        val m = LinkedHashMap<String, ByteArray>()
        if (withPlist) m[prefix + PLIST] = plist(version).toByteArray()
        m[prefix + MT] = binary(if (version.startsWith("3.3")) null else version)
        if (dictionary) m[prefix + SD] = ByteArray(700) { 1 }
        for (v in voices) {
            m["${prefix}Speech/Voices/$v.SpeechVoice/Contents/Info.plist"] = "<plist/>".toByteArray()
            m["${prefix}Speech/Voices/$v.SpeechVoice/Contents/Resources/$v"] = ByteArray(3000) { it.toByte() }
        }
        return m
    }

    private fun zip(file: File, entries: Map<String, ByteArray>, stored: Boolean = false,
                    dirs: List<String> = emptyList()) {
        ZipOutputStream(file.outputStream()).use { z ->
            for (d in dirs) { z.putNextEntry(ZipEntry(d)); z.closeEntry() }
            for ((name, bytes) in entries) {
                val e = ZipEntry(name)
                if (stored) {
                    e.method = ZipEntry.STORED
                    e.size = bytes.size.toLong(); e.compressedSize = bytes.size.toLong()
                    e.crc = CRC32().also { it.update(bytes) }.value
                }
                z.putNextEntry(e); z.write(bytes); z.closeEntry()
            }
        }
    }

    private fun bothWays(file: File): List<ZipImport.Plan> = listOf(
        ZipImport.inspect(ZipImport.source(file)),
        ZipImport.inspect(ZipImport.Source(file.name, file.length(), { file.inputStream() })))

    private fun describe(p: ZipImport.Plan) =
        p.refusal ?: p.found.joinToString(";") { "${it.prefix}|${it.version}|${it.gen}|${it.voices}|${it.dictionary}" }

    /** The whole route as a person takes it, on a phone: the button, the
     * system picker, a zip named `name` chosen from it (put it in Downloads
     * first), the confirm dialog read and answered, and the status line
     * waited for. The screen must be unlocked. Run with
     *
     *     am instrument -w -e zipImport pick -e zipName tiger.zip \
     *         com.pantheraspeech.tts.test/com.pantheraspeech.tts.EngineSmokeTest
     */
    fun pick(inst: Instrumentation, name: String) {
        val results = Bundle()
        try {
            val ui = inst.uiAutomation
            val activity = inst.startActivitySync(android.content.Intent(inst.targetContext,
                SettingsActivity::class.java).addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK))
            fun descendants(v: android.view.View): List<android.view.View> =
                listOf(v) + if (v is android.view.ViewGroup)
                    (0 until v.childCount).flatMap { descendants(v.getChildAt(it)) } else emptyList()
            inst.runOnMainSync {
                descendants(activity.window.decorView).filterIsInstance<android.widget.Button>()
                    .first { it.text == "Extract engine from zip file" }.performClick()
            }
            fun node(match: (android.view.accessibility.AccessibilityNodeInfo) -> Boolean):
                    android.view.accessibility.AccessibilityNodeInfo? {
                fun walk(n: android.view.accessibility.AccessibilityNodeInfo?): android.view.accessibility.AccessibilityNodeInfo? {
                    n ?: return null
                    if (match(n)) return n
                    for (i in 0 until n.childCount) walk(n.getChild(i))?.let { return it }
                    return null
                }
                return walk(ui.rootInActiveWindow)
            }
            fun text(n: android.view.accessibility.AccessibilityNodeInfo) =
                n.text?.toString() ?: n.contentDescription?.toString() ?: ""
            fun click(n: android.view.accessibility.AccessibilityNodeInfo) {
                var at: android.view.accessibility.AccessibilityNodeInfo? = n
                while (at != null && !at.isClickable) at = at.parent
                check(at != null) { "nothing clickable around '${text(n)}'" }
                at.performAction(android.view.accessibility.AccessibilityNodeInfo.ACTION_CLICK)
            }
            fun await(what: String, seconds: Int, match: (android.view.accessibility.AccessibilityNodeInfo) -> Boolean):
                    android.view.accessibility.AccessibilityNodeInfo {
                val deadline = System.currentTimeMillis() + seconds * 1000L
                while (System.currentTimeMillis() < deadline) {
                    node(match)?.let { return it }
                    Thread.sleep(500)
                }
                error("never saw $what")
            }
            // The picker. Its Recent view lists a freshly pushed zip; if not,
            // the Downloads root is one tap away behind the roots button.
            val picker = try { await("the zip in the picker", 15) { text(it) == name } }
                catch (e: IllegalStateException) {
                    node { text(it).equals("Show roots", true) }?.let { click(it); Thread.sleep(1500) }
                    click(await("Downloads in the picker", 10) { text(it) == "Downloads" })
                    Thread.sleep(1500)
                    await("the zip under Downloads", 15) { text(it) == name }
                }
            click(picker)
            val offer = await("the confirm dialog", 60) { text(it).startsWith("Will import into") }
            results.putString("offer", text(offer))
            click(await("OK", 10) { text(it) == "OK" })
            val outcome = await("the outcome", 300) {
                val t = text(it)
                t.startsWith("Imported ") || t.startsWith("Import failed") || t.startsWith("Import cancelled")
            }
            results.putString("outcome", text(outcome))
            check(text(outcome).startsWith("Imported ")) { text(outcome) }
            inst.finish(Activity.RESULT_OK, results)
        } catch (e: Throwable) {
            Log.e("PantheraTest", "zip pick failed", e)
            results.putString("failure", e.toString())
            inst.finish(Activity.RESULT_CANCELED, results)
        }
    }

    fun run(inst: Instrumentation) {
        val results = Bundle()
        val dir = File(inst.targetContext.cacheDir, "zipcheck").apply { deleteRecursively(); mkdirs() }
        var cases = 0
        fun expect(name: String, entries: Map<String, ByteArray>, stored: Boolean = false,
                   dirs: List<String> = emptyList(), test: (ZipImport.Plan) -> Unit): File {
            val file = File(dir, name)
            zip(file, entries, stored, dirs)
            val plans = bothWays(file)
            check(describe(plans[0]) == describe(plans[1])) {
                "$name: seekable and streamed disagree: ${describe(plans[0])} vs ${describe(plans[1])}"
            }
            for (p in plans) try { test(p) } catch (e: IllegalStateException) {
                throw IllegalStateException("$name: ${e.message} -- ${describe(p)}")
            }
            cases++
            return file
        }
        try {
            // A folder around it, no plist (Lion's extraction has none), and
            // the noise the Finder adds. The binary names the version.
            val lionNoise = mapOf(
                "__MACOSX/lion/._Speech" to ByteArray(10),
                "lion/.DS_Store" to ByteArray(10),
                "lion/Speech/.DS_Store" to ByteArray(10))
            expect("lion.zip", engine("lion/", "4.0.74", false, listOf("Alex", "Fred")) + lionNoise,
                   dirs = listOf("lion/", "lion/Speech/", "__MACOSX/")) { p ->
                check(p.refusal == null) { p.refusal!! }
                val f = p.found.single()
                check(f.prefix == "lion/" && f.version == "4.0.74" && f.gen == "lion" && f.voices == 2 && f.dictionary)
            }
            // The same, stored rather than deflated.
            expect("lion-stored.zip", engine("lion/", "4.0.74", false, listOf("Alex")), stored = true) { p ->
                check(p.refusal == null && p.found.single().gen == "lion" && p.found.single().version == "4.0.74")
            }
            // No folder around it: the contents at the top. Plist says 3.6.59.
            expect("leopard-top.zip", engine("", "3.6.59", true, listOf("Alex", "Bruce", "Vicki"))) { p ->
                check(p.refusal == null)
                val f = p.found.single()
                check(f.prefix == "" && f.version == "3.6.59" && f.gen == "leopard" && f.voices == 3)
            }
            // Tiger: plist only, nothing in the binary; and Windows-style
            // separators, which some zippers write.
            expect("tiger.zip", engine("tiger/", "3.3", true, listOf("Fred", "Kathy"))
                    .mapKeys { it.key.replace('/', '\\') }) { p ->
                check(p.refusal == null)
                check(p.found.single().gen == "tiger" && p.found.single().version == "3.3" && p.found.single().voices == 2)
            }
            // The folder the desktop keeps them all in, two levels deep.
            val all = engine("panthera-data/tiger/", "3.3", true, listOf("Fred")) +
                engine("panthera-data/leopard/", "3.6.59", true, listOf("Alex", "Fred")) +
                engine("panthera-data/snowleopard/", "3.10.35", true, listOf("Vicki")) +
                engine("panthera-data/lion/", "4.0.74", false, listOf("Alex", "Fred", "Bruce"))
            val allZip = expect("all.zip", all) { p ->
                check(p.refusal == null) { p.refusal!! }
                check(p.gens == listOf("tiger", "leopard", "snowleopard", "lion")) { p.gens.toString() }
                check(p.found.map { it.voices } == listOf(1, 2, 1, 3))
            }
            // A folder named for one generation holding another: the name
            // is not believed.
            expect("lion-but-leopard.zip", engine("lion/", "3.6.59", true, listOf("Alex"))) { p ->
                check(p.refusal == null && p.found.single().gen == "leopard")
            }
            // Refusals, each in words with the reason in them.
            expect("ml.zip", engine("ml/", "4.1.12", true, listOf("Alex"))) { p ->
                check(p.refusal?.contains("Mountain Lion") == true && p.refusal!!.contains("4.1.12"))
            }
            expect("future.zip", engine("x/", "3.12.1", true, listOf("Alex"))) { p ->
                // 3.12 has never existed; a version past Snow Leopard's line
                // still lands in its generation rather than being refused,
                // and the confirm dialog names the version so a person sees.
                check(p.refusal == null && p.found.single().gen == "snowleopard")
            }
            expect("unknown.zip", engine("x/", "2.1", true, listOf("Alex"))) { p ->
                check(p.refusal?.contains("2.1") == true)
            }
            expect("flat.zip", mapOf(
                "MacinTalk" to binary("3.6.59"),
                "Voices/Fred.SpeechVoice/Contents/Info.plist" to ByteArray(5),
                SD to ByteArray(5))) { p ->
                check(p.refusal?.contains("bundle") == true) { p.refusal ?: "accepted" }
            }
            expect("nothing.zip", mapOf("readme.txt" to "hi".toByteArray())) { p ->
                check(p.refusal?.startsWith("No engine") == true) { p.refusal ?: "accepted" }
            }
            expect("twice.zip", engine("a/", "3.6.59", true, listOf("Alex")) +
                    engine("b/", "3.6.59", true, listOf("Bruce"))) { p ->
                check(p.refusal?.contains("twice") == true) { p.refusal ?: "accepted" }
            }
            expect("nodict.zip", engine("lion/", "4.0.74", false, listOf("Alex"), dictionary = false)) { p ->
                check(p.refusal?.contains("SpeechDictionary") == true) { p.refusal ?: "accepted" }
            }
            expect("novoices.zip", engine("lion/", "4.0.74", false, emptyList())) { p ->
                check(p.refusal?.contains("no voices") == true) { p.refusal ?: "accepted" }
            }
            expect("noversion.zip", engine("tiger/", "3.3", false, listOf("Fred"))) { p ->
                check(p.refusal?.contains("cannot tell") == true) { p.refusal ?: "accepted" }
            }
            expect("evil.zip", engine("lion/", "4.0.74", false, listOf("Alex")) +
                    mapOf("lion/../../escape" to ByteArray(3))) { p ->
                check(p.refusal?.contains("leaves") == true) { p.refusal ?: "accepted" }
            }
            File(dir, "notzip.zip").writeBytes(ByteArray(5000) { (it * 31).toByte() })
            for (p in bothWays(File(dir, "notzip.zip")))
                check(p.refusal?.contains("not a zip") == true) { p.refusal ?: "accepted" }
            cases++

            // The import itself: into a root that already holds a stale Lion,
            // which must survive a cancelled import and be replaced by a
            // finished one, with nothing left behind either way.
            val root = File(dir, "root")
            val stale = File(root, "lion/stale.txt").apply { parentFile!!.mkdirs(); writeText("old") }
            val plan = ZipImport.inspect(ZipImport.source(allZip))
            var progressCalls = 0
            try {
                ZipImport.extract(ZipImport.source(allZip), plan, root, { _, _ -> progressCalls++ }) { true }
                error("a cancelled import finished")
            } catch (e: ZipImport.Cancelled) { /* as it should */ }
            check(stale.isFile && root.listFiles()!!.none { it.name.endsWith(".importing") }) { "cancel left a mess" }

            var last = 0L to 0L
            val gens = ZipImport.extract(ZipImport.source(allZip), plan, root, { d, t -> last = d to t }) { false }
            check(gens == listOf("tiger", "leopard", "snowleopard", "lion")) { gens.toString() }
            check(last.first == allZip.length() && last.second == allZip.length()) { "progress ended at $last" }
            check(!stale.exists()) { "the stale Lion survived" }
            check(File(root, "lion/$MT").length() == binary("4.0.74").size.toLong())
            check(File(root, "tiger/Speech/Voices/Fred.SpeechVoice/Contents/Resources/Fred").length() == 3000L)
            check(File(root, "leopard/$PLIST").readText().contains("3.6.59"))
            check(root.listFiles()!!.map { it.name }.sorted() == listOf("leopard", "lion", "snowleopard", "tiger"))
            cases++

            // And a zip whose top is one engine with another beneath it: the
            // longer prefix owns its files.
            val nested = engine("", "3.6.59", true, listOf("Alex")) + engine("lion/", "4.0.74", false, listOf("Fred"))
            val nestedZip = File(dir, "nested.zip").also { zip(it, nested) }
            val nestedPlan = ZipImport.inspect(ZipImport.source(nestedZip))
            check(nestedPlan.refusal == null && nestedPlan.gens.toSet() == setOf("leopard", "lion")) { describe(nestedPlan) }
            val root2 = File(dir, "root2")
            ZipImport.extract(ZipImport.source(nestedZip), nestedPlan, root2, { _, _ -> }) { false }
            check(File(root2, "lion/$MT").isFile && File(root2, "leopard/$MT").isFile)
            check(!File(root2, "leopard/lion").exists()) { "lion's files leaked into leopard" }
            cases++

            results.putString("zipImport", "$cases cases passed")
            inst.finish(Activity.RESULT_OK, results)
        } catch (e: Throwable) {
            Log.e("PantheraTest", "zip import check failed", e)
            results.putString("failure", e.toString())
            inst.finish(Activity.RESULT_CANCELED, results)
        } finally {
            dir.deleteRecursively()
        }
    }
}
