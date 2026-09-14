package com.pantheraspeech.tts

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.io.IOException

/** Moving a generation folder between the two halves of the app's storage,
 * on plain files: copied whole, checked, swapped in, and only then removed
 * from where it was. */
class ProtectedStorageTest {
    @get:Rule val temp = TemporaryFolder()

    /** A generation folder in the Mac's own layout, with a few files of
     * different sizes so a truncated copy would be caught. */
    private fun fakeGeneration(root: File, gen: String): Map<String, ByteArray> {
        val files = mapOf(
            "Speech/Synthesizers/MacinTalk.SpeechSynthesizer/Contents/MacOS/MacinTalk" to ByteArray(70000) { it.toByte() },
            "Speech/Synthesizers/MacinTalk.SpeechSynthesizer/Contents/Info.plist" to "<plist/>".toByteArray(),
            "Speech/Voices/Fred.SpeechVoice/Contents/Info.plist" to "<plist>Fred</plist>".toByteArray(),
            "Speech/Voices/Fred.SpeechVoice/Contents/Resources/Fred" to ByteArray(123456) { (it * 7).toByte() },
            "SpeechDictionary.framework/Versions/A/SpeechDictionary" to ByteArray(5000) { 1 },
            "SpeechDictionary.framework/Versions/A/Resources/empty" to ByteArray(0),
        )
        for ((name, bytes) in files) {
            val f = File(File(root, gen), name)
            f.parentFile.mkdirs()
            f.writeBytes(bytes)
        }
        return files
    }

    @Test fun aGenerationIsCopiedWholeAndTheSourceIsRemovedOnlyAfterwards() {
        val inbox = temp.newFolder("inbox")
        val protected = File(temp.newFolder("protected"), "panthera-data")
        val files = fakeGeneration(inbox, "leopard")
        val seen = ArrayList<Pair<Long, Long>>()
        val moved = ProtectedStorage.move(inbox, "leopard", protected, { d, t -> seen.add(d to t) })

        assertEquals(files.values.sumOf { it.size.toLong() }, moved)
        for ((name, bytes) in files)
            assertArrayEquals(name, bytes, File(File(protected, "leopard"), name).readBytes())
        assertFalse("the source stays until the copy is proven, then goes", File(inbox, "leopard").exists())
        assertFalse(File(protected, "leopard" + ProtectedStorage.MOVING).exists())
        assertTrue(seen.isNotEmpty())
        assertEquals(moved, seen.last().first)
        assertTrue(seen.all { it.second == moved })
    }

    @Test fun whatWasThereBeforeIsReplaced() {
        val inbox = temp.newFolder("inbox")
        val protected = File(temp.newFolder("protected"), "panthera-data")
        val stale = File(protected, "lion/Speech/Voices/Old.SpeechVoice/Contents/Info.plist")
        stale.parentFile.mkdirs(); stale.writeText("old")
        fakeGeneration(inbox, "lion")
        ProtectedStorage.move(inbox, "lion", protected)
        assertFalse("a stale voice from an earlier copy must not linger", stale.exists())
        assertTrue(File(protected, "lion/Speech/Voices/Fred.SpeechVoice/Contents/Info.plist").isFile)
    }

    @Test fun aCancelledMoveLeavesBothFoldersAsTheyWere() {
        val inbox = temp.newFolder("inbox")
        val protected = File(temp.newFolder("protected"), "panthera-data")
        val files = fakeGeneration(inbox, "tiger")
        var polls = 0
        try {
            ProtectedStorage.move(inbox, "tiger", protected, cancelled = { ++polls > 3 })
            throw AssertionError("the move should have been cancelled")
        } catch (e: ProtectedStorage.Cancelled) { /* expected */ }
        for ((name, bytes) in files)
            assertArrayEquals(name, bytes, File(File(inbox, "tiger"), name).readBytes())
        assertFalse(File(protected, "tiger").exists())
        assertFalse(File(protected, "tiger" + ProtectedStorage.MOVING).exists())
    }

    @Test fun aCopyThatDoesNotMatchIsRefused() {
        val a = temp.newFolder("a")
        val b = temp.newFolder("b")
        File(a, "x").writeBytes(ByteArray(10))
        File(b, "x").writeBytes(ByteArray(9))
        try {
            ProtectedStorage.verify(a, b)
            throw AssertionError("a short copy passed")
        } catch (e: IOException) {
            assertTrue(e.message!!.contains("x"))
        }
    }

    @Test fun aMissingSourceIsAnErrorNotAnEmptyMove() {
        val inbox = temp.newFolder("inbox")
        val protected = File(temp.newFolder("protected"), "panthera-data")
        try {
            ProtectedStorage.move(inbox, "lion", protected)
            throw AssertionError("moved nothing and said so")
        } catch (e: IOException) { /* expected */ }
    }
}
