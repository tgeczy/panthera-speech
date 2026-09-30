package com.pantheraspeech.tts

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File

/** Which folders a removal has to delete.
 *
 * The dangerous half of removing a generation is not the delete, it is the
 * arithmetic before it: data lives in up to three roots, `migrate` copies
 * anything outside protected storage back in on the next unlock, and a removal
 * that missed a copy would have the generation reappear by itself. That is pure
 * file work, so it is tested here rather than on a phone.
 */
class GenerationRemovalTest {
    @get:Rule val temp = TemporaryFolder()

    private fun folder(root: File, name: String): File =
        File(root, name).also {
            File(it, "Speech/Voices").mkdirs()
            File(it, "Speech/Voices/Fred.SpeechVoice").mkdirs()
            File(it, "marker").writeText(name)
        }

    /** Every copy, in every root -- not only the one the engine would read. */
    @Test fun findsEveryCopyAcrossEveryRoot() {
        val protected = temp.newFolder("protected")
        val inbox = temp.newFolder("inbox")
        val legacy = temp.newFolder("legacy")
        folder(protected, "lion")
        folder(inbox, "lion")
        folder(legacy, "lion")
        folder(protected, "tiger")          // a different generation stays out of it

        val found = PantheraEngine.generationFolders(listOf(protected, inbox, legacy), "lion")
        assertEquals(setOf(File(protected, "lion"), File(inbox, "lion"), File(legacy, "lion")),
                     found.toSet())
    }

    /** A cancelled import or an interrupted move leaves a staging folder beside
     * the real one. Both belong to the generation and both go with it. */
    @Test fun includesStagingFolders() {
        val root = temp.newFolder("protected")
        folder(root, "lion")
        folder(root, "lion" + ProtectedStorage.MOVING)
        folder(root, "lion" + ZipImport.IMPORTING)

        val found = PantheraEngine.generationFolders(listOf(root), "lion")
        assertEquals(3, found.size)
        assertTrue(found.contains(File(root, "lion" + ProtectedStorage.MOVING)))
        assertTrue(found.contains(File(root, "lion" + ZipImport.IMPORTING)))
    }

    /** A generation whose name merely starts the same is a different
     * generation. "leopard" must never be caught by removing "leo", and
     * "snowleopard" must never be caught by removing "leopard". */
    @Test fun doesNotCatchGenerationsWithSimilarNames() {
        val root = temp.newFolder("protected")
        folder(root, "leopard")
        folder(root, "snowleopard")

        assertEquals(listOf(File(root, "leopard")),
                     PantheraEngine.generationFolders(listOf(root), "leopard"))
        assertEquals(listOf(File(root, "snowleopard")),
                     PantheraEngine.generationFolders(listOf(root), "snowleopard"))
    }

    /** Before unlock there is only one root, and the same root can be listed
     * twice; neither may produce a duplicate delete. */
    @Test fun oneEntryPerFolderHoweverManyTimesARootIsListed() {
        val root = temp.newFolder("protected")
        folder(root, "lion")
        val found = PantheraEngine.generationFolders(listOf(root, root, File(root.path)), "lion")
        assertEquals(1, found.size)
    }

    /** Nothing installed is not an error, and neither is a name nobody has. */
    @Test fun answersEmptyWhenThereIsNothingToRemove() {
        val root = temp.newFolder("protected")
        assertTrue(PantheraEngine.generationFolders(listOf(root), "lion").isEmpty())
        folder(root, "lion")
        assertTrue(PantheraEngine.generationFolders(listOf(root), "tiger").isEmpty())
    }

    /** A file where a generation folder would be is not a generation folder.
     * Deleting it would be deleting something this feature does not understand. */
    @Test fun ignoresAFileWithAGenerationName() {
        val root = temp.newFolder("protected")
        File(root, "lion").writeText("not a folder")
        assertTrue(PantheraEngine.generationFolders(listOf(root), "lion").isEmpty())
    }

    /** What the confirmation dialog promises to free is every copy's size
     * added up, which is what ProtectedStorage.size measures. */
    @Test fun sizeCoversEveryCopy() {
        val protected = temp.newFolder("protected")
        val inbox = temp.newFolder("inbox")
        for (root in listOf(protected, inbox)) {
            val gen = folder(root, "lion")
            File(gen, "Speech/Voices/Fred.SpeechVoice/bank").writeBytes(ByteArray(50_000))
        }
        val folders = PantheraEngine.generationFolders(listOf(protected, inbox), "lion")
        val total = folders.sumOf { ProtectedStorage.size(it) }
        assertEquals(2, folders.size)
        assertTrue("two 50 KB copies should be over 100 KB, was $total", total > 100_000)
    }

    /** The delete itself, on the folders the arithmetic chose: everything the
     * generation had goes, and the generation beside it is untouched. */
    @Test fun deletingTheFoundFoldersLeavesOthersAlone() {
        val root = temp.newFolder("protected")
        folder(root, "lion")
        folder(root, "lion" + ProtectedStorage.MOVING)
        val tiger = folder(root, "tiger")
        for (f in PantheraEngine.generationFolders(listOf(root), "lion")) f.deleteRecursively()
        assertFalse(File(root, "lion").exists())
        assertFalse(File(root, "lion" + ProtectedStorage.MOVING).exists())
        assertTrue(tiger.isDirectory)
        assertTrue(File(tiger, "marker").isFile)
    }
}
