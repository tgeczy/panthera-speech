package com.pantheraspeech.tts

import java.io.File
import java.io.IOException

/**
 * Moving engine data into device-protected storage, so the voices are there
 * before the phone has been unlocked.
 *
 * Android keeps an app's data in two halves. The credential-encrypted half --
 * the ordinary files folder, the `Android/data` folder a PC's file window
 * shows, the shared preferences -- does not exist until the person has
 * unlocked the phone once after a reboot. The device-protected half is there
 * from boot. A screen reader reads the lock screen through whichever engine
 * can run there, and until now that was never this one: its data lived in
 * the wrong half and none of its services said they could run before unlock.
 * That is Direct Boot (panthera-speech#18), and it needs no system partition
 * or root: Google's own engine is an ordinary updatable app that keeps its
 * voices in exactly this place.
 *
 * The folder a PC sees stays as the inbox. Whatever lands there, or in the
 * internal folder a debug push used, is moved in here the next time the app
 * runs unlocked: copied into `<gen>.moving`, checked file by file against
 * the source, swapped into place, and only then removed from where it was.
 * A copy, never a rename -- the two halves are different encryption
 * policies, and a rename across them fails -- so Alex's 670 MB is a visible
 * one-time wait, and the source is untouched until the copy is proven.
 *
 * Nothing here knows about Android. It works on files, so the desktop JVM
 * can test it.
 */
object ProtectedStorage {
    /** Distinct from the zip import's `.importing`, so an import and a move
     * of the same generation can never fight over one staging folder. */
    const val MOVING = ".moving"

    class Cancelled : IOException("cancelled")

    /** The size of every file under `dir`, in bytes. */
    fun size(dir: File): Long = dir.walkTopDown().filter { it.isFile }.sumOf { it.length() }

    /** Move `from/<gen>` to `into/<gen>`, replacing what is there.
     *
     * `progress(bytesDone, bytesTotal)` is called as the copy goes by and
     * `cancelled()` is polled between files. A failed or cancelled move
     * leaves both folders as they were. -> the bytes moved. */
    fun move(from: File, gen: String, into: File,
             progress: (Long, Long) -> Unit = { _, _ -> },
             cancelled: () -> Boolean = { false }): Long {
        val source = File(from, gen)
        val staging = File(into, gen + MOVING)
        val live = File(into, gen)
        if (!source.isDirectory) throw IOException("$source is not a folder")
        into.mkdirs()
        if (staging.exists()) staging.deleteRecursively()
        val total = size(source)
        var done = 0L
        val buffer = ByteArray(1 shl 16)
        try {
            for (file in source.walkTopDown()) {
                if (cancelled()) throw Cancelled()
                val target = File(staging, file.relativeTo(source).path)
                if (file.isDirectory) { target.mkdirs(); continue }
                target.parentFile?.mkdirs()
                file.inputStream().use { input ->
                    target.outputStream().use { output ->
                        while (true) {
                            val n = input.read(buffer)
                            if (n < 0) break
                            output.write(buffer, 0, n)
                            done += n
                            progress(done, total)
                        }
                    }
                }
            }
            verify(source, staging)
            if (live.exists()) live.deleteRecursively()
            if (!staging.renameTo(live)) throw IOException("could not move $gen into place")
        } catch (e: Throwable) {
            staging.deleteRecursively()
            throw e
        }
        // The copy is checked and in place. Only now does the original go.
        source.deleteRecursively()
        return total
    }

    /** Every file under `source` exists under `copy` at the same size. */
    fun verify(source: File, copy: File) {
        for (file in source.walkTopDown()) {
            if (!file.isFile) continue
            val twin = File(copy, file.relativeTo(source).path)
            if (!twin.isFile || twin.length() != file.length())
                throw IOException("${file.relativeTo(source).path} did not copy whole")
        }
    }
}
