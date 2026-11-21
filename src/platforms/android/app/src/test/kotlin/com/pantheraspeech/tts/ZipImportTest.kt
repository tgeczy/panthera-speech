package com.pantheraspeech.tts

import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

class ZipImportTest {
    @get:Rule val temp = TemporaryFolder()

    private fun engine(prefix: String): Map<String, ByteArray> = linkedMapOf(
        prefix + ZipImport.MACINTALK to "test SpeechSynthesis-4.0.74 engine".toByteArray(),
        prefix + ZipImport.DICTIONARY to "dictionary".toByteArray(),
        prefix + "Speech/Voices/Alex.SpeechVoice/Contents/Resources/Alex" to
            byteArrayOf(0, 1, 2, 3, 127),
    )

    private fun archive(entries: Map<String, ByteArray>): File = temp.newFile().also { file ->
        // No explicit directory entries: common ZIP writers omit them.
        ZipOutputStream(file.outputStream()).use { zip ->
            for ((name, bytes) in entries) {
                zip.putNextEntry(ZipEntry(name)); zip.write(bytes); zip.closeEntry()
            }
        }
    }

    private fun sources(file: File) = listOf(ZipImport.source(file),
        ZipImport.Source(file.name, file.length(), { file.inputStream() }))

    @Test fun requestedLayoutsImportIdenticallyFromFilesAndStreams() {
        for (prefix in listOf("lion/", "LiOn/", "panthera/LION/",
                "panthera-data/Lion/", "PANTHERA-DATA/lIoN/")) {
            val entries = engine(prefix)
            val file = archive(entries + ("unrelated.txt" to byteArrayOf(9)))
            for (source in sources(file)) {
                val plan = ZipImport.inspect(source)
                assertNull("$prefix: ${plan.refusal}", plan.refusal)
                assertEquals(listOf("lion"), plan.gens)
                val root = temp.newFolder()
                assertEquals(listOf("lion"), ZipImport.extract(source, plan, root, { _, _ -> }, { false }))
                for ((name, bytes) in entries)
                    assertArrayEquals(bytes, File(root, "lion/" + name.removePrefix(prefix)).readBytes())
                assertEquals(listOf("lion"), root.list()!!.toList())
                assertFalse(File(root, "lion/unrelated.txt").exists())
            }
        }
    }

    /** A zip whose names an older tool wrote in a DOS code page, without the
     * zip's UTF-8 flag (panthera-speech#42; ssi263-speech#54, the same class).
     * The check read such a name as Latin-1 and passed the zip, but the
     * extraction read it as UTF-8 and threw -- after OK, possibly hundreds of
     * MB in -- and the streamed check refused the whole zip.  One stray
     * "LÉEME.TXT" ahead of the engine is enough. */
    @Test fun aDosCodePageNameNeitherRefusesNorBreaksTheImport() {
        val entries = linkedMapOf("lion/LÉEME.TXT" to "léame".toByteArray(), "für.txt" to byteArrayOf(1)) +
            engine("lion/")
        val file = temp.newFile().also { f ->
            ZipOutputStream(f.outputStream(), java.nio.charset.Charset.forName("IBM437")).use { zip ->
                for ((name, bytes) in entries) { zip.putNextEntry(ZipEntry(name)); zip.write(bytes); zip.closeEntry() }
            }
        }
        for (source in sources(file)) {
            val plan = ZipImport.inspect(source)
            assertNull(plan.refusal)
            assertEquals(listOf("lion"), plan.gens)
            val root = temp.newFolder()
            assertEquals(listOf("lion"), ZipImport.extract(source, plan, root, { _, _ -> }, { false }))
            for ((name, bytes) in engine("lion/"))
                assertArrayEquals(bytes, File(root, "lion/" + name.removePrefix("lion/")).readBytes())
        }
    }

    /** An engine entry far larger than any real one -- a gigabyte of zeros,
     * about a megabyte in the zip -- is refused, not read whole
     * (panthera-speech#55; ssi263-speech#50, the same class).  The streamed
     * check read every engine entry with readBytes and only then compared it
     * with its cap: an OutOfMemoryError on the check thread, uncaught, took
     * the app down, the TTS service with it.  From a stream, where the size
     * is only known by reading. */
    @Test fun aHugeEngineEntryIsRefusedWithoutReadingItWhole() {
        val file = temp.newFile().also { f ->
            ZipOutputStream(f.outputStream()).use { zip ->
                zip.putNextEntry(ZipEntry("lion/" + ZipImport.MACINTALK))
                val zeros = ByteArray(1 shl 20)
                repeat(1024) { zip.write(zeros) }
                zip.closeEntry()
                zip.putNextEntry(ZipEntry("lion/" + ZipImport.DICTIONARY)); zip.write(byteArrayOf(1)); zip.closeEntry()
            }
        }
        val streamed = ZipImport.Source(file.name, file.length(), { file.inputStream() })
        val plan = ZipImport.inspect(streamed)
        assertNotNull(plan.refusal)
        assertTrue(plan.refusal!!, plan.refusal!!.contains("cannot tell which generation"))
    }

    @Test fun aSingleEngineAtTheRootStillImports() {
        val file = archive(engine(""))
        for (source in sources(file)) {
            val plan = ZipImport.inspect(source)
            assertNull(plan.refusal)
            val root = temp.newFolder()
            ZipImport.extract(source, plan, root, { _, _ -> }, { false })
            assertArrayEquals(engine("").getValue(ZipImport.MACINTALK),
                File(root, "lion/" + ZipImport.MACINTALK).readBytes())
        }
    }

    @Test fun unrecognizedAndMissingGenerationFoldersAreRefusedBeforeEngineReads() {
        for (prefix in listOf("panthera/", "panthera-data/", "panthera/other/",
                "panthera/backup/lion/", "backup/lion/", "other/")) {
            val entries = engine(prefix).map { (name, bytes) ->
                ZipImport.Entry(name, bytes.size.toLong(), 0, 0, 0)
            }
            val plan = ZipImport.plan(entries) { error("invalid layout read engine data") }
            assertTrue("$prefix: ${plan.refusal}", plan.refusal!!.contains("generation folder"))
        }
    }

    @Test fun allGenerationNamesAllowMixedCaseAndStillUseEngineVersions() {
        for ((folder, version, gen) in listOf(
                Triple("TiGeR", "3.3", "tiger"),
                Triple("LEOPARD", "3.6.59", "leopard"),
                Triple("SnowLeopard", "3.10.35", "snowleopard"),
                Triple("lIoN", "4.0.74", "lion"))) {
            val prefix = "panthera-data/$folder/"
            val entries = engine(prefix) + (prefix + ZipImport.INFO_PLIST to
                "<key>CFBundleVersion</key><string>$version</string>".toByteArray())
            for (source in sources(archive(entries))) {
                val plan = ZipImport.inspect(source)
                assertNull(plan.refusal)
                assertEquals(listOf(gen), plan.gens)
            }
        }
    }

    @Test fun outspokenArchivesGetAnExplanationAndAreNeverExtracted() {
        for (prefix in listOf("outspoken/", "outspoken-data/", "OuTsPoKeN-DaTa/")) {
            // Even plausible Panthera data inside the wrong application's wrapper
            // must be refused, as must an archive containing just that directory.
            for (entries in listOf(engine(prefix + "lion/"), mapOf(prefix to byteArrayOf()))) {
                val file = archive(entries)
                for (source in sources(file)) {
                    val plan = ZipImport.inspect(source)
                    assertTrue(plan.refusal!!.contains("Outspoken TTS for Android"))
                    val root = temp.newFolder()
                    try {
                        ZipImport.extract(source, plan, root, { _, _ -> }, { false })
                        fail("Outspoken archive was extracted")
                    } catch (_: IllegalStateException) { }
                    assertTrue(root.list()!!.isEmpty())
                }
            }
        }
    }

    @Test fun missingGenerationOrIncompleteEngineCannotReplaceExistingData() {
        for (entries in listOf(
                mapOf("panthera/readme.txt" to byteArrayOf(1)),
                mapOf("panthera-data/LION/readme.txt" to byteArrayOf(1)),
                mapOf("lion/readme.txt" to byteArrayOf(1)))) {
            val file = archive(entries)
            for (source in sources(file)) {
                val root = temp.newFolder()
                val existing = File(root, "lion/keep").apply { parentFile!!.mkdirs(); writeText("existing") }
                val plan = ZipImport.inspect(source)
                assertNotNull(plan.refusal)
                try {
                    ZipImport.extract(source, plan, root, { _, _ -> }, { false })
                    fail("refused plan was extracted")
                } catch (_: IllegalStateException) { }
                assertEquals("existing", existing.readText())
                assertEquals(listOf("lion"), root.list()!!.toList())
            }
        }
    }
}
