package com.pantheraspeech.tts

import android.content.Context
import android.net.Uri
import android.os.ParcelFileDescriptor
import android.provider.OpenableColumns
import java.io.BufferedInputStream
import java.io.EOFException
import java.io.File
import java.io.FileInputStream
import java.io.FileOutputStream
import java.io.IOException
import java.io.InputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.nio.channels.FileChannel
import java.util.zip.Inflater
import java.util.zip.InflaterInputStream
import java.util.zip.ZipInputStream

/**
 * Engine data from a zip file.
 *
 * The desktop add-on extracts a generation's folder -- `Speech` and
 * `SpeechDictionary.framework` and the rest -- and a person zips that folder
 * to get it onto a phone in one piece. The zip may hold the folder itself
 * (`lion/Speech/...`), or its contents at the top (`Speech/...`), or all four
 * generations side by side; the folder's name is never trusted, because
 * "lion.zip" with Leopard inside is an easy thing to make. The engine says
 * what it is: `Info.plist` beside the MacinTalk binary names its version, and
 * where an extraction carries no plist (Lion's does not), the binary itself
 * does, in its build path. 3.3 is Tiger, 3.6 Leopard, 3.10 Snow Leopard and
 * 4.0 Lion; 4.1 is Mountain Lion's 64-bit engine, refused by name.
 *
 * Reading the zip is `java.util.zip`, which is the platform's own: nothing
 * here needs a library, and nothing needs a licence.
 *
 * Two passes. The check reads the central directory at the end of the file
 * when the file can be seeked -- a few kilobytes, so the question "what is
 * in here?" is answered before the confirm dialog, not after seven hundred
 * megabytes of Alex have been inflated. A source that cannot seek (a cloud
 * provider's pipe) is scanned front to back instead, which is slower and
 * gives the same answer. The import itself is one streamed pass, written
 * into `<gen>.importing` beside the real folder and swapped in only at the
 * end, so a failed or cancelled import leaves what was there alone.
 */
object ZipImport {
    const val MACINTALK = "Speech/Synthesizers/MacinTalk.SpeechSynthesizer/Contents/MacOS/MacinTalk"
    const val INFO_PLIST = "Speech/Synthesizers/MacinTalk.SpeechSynthesizer/Contents/Info.plist"
    const val VERSION_PLIST = "Speech/Synthesizers/MacinTalk.SpeechSynthesizer/Contents/version.plist"
    const val DICTIONARY = "SpeechDictionary.framework/Versions/A/SpeechDictionary"
    const val VOICES = "Speech/Voices/"
    private const val IMPORTING = ".importing"

    /** The bytes, twice over: a fresh stream from the start for the import,
     * and the same file as a channel when it can be seeked, for the check. */
    class Source(val name: String, val size: Long,
                 private val stream: () -> InputStream,
                 private val seekable: (() -> FileChannel?)? = null) {
        fun open(): InputStream = stream()
        fun channel(): FileChannel? = try { seekable?.invoke() } catch (e: Exception) { null }
    }

    /** A zip on disk, by path: the adb route, and the tests. */
    fun source(file: File) = Source(file.name, file.length(),
        { FileInputStream(file) }, { FileInputStream(file).channel })

    /** A zip the system picker handed over. Its display name and size come
     * from the provider; a provider that will not say a size says -1. */
    fun source(ctx: Context, uri: Uri): Source {
        val resolver = ctx.contentResolver
        var name = uri.lastPathSegment?.substringAfterLast('/') ?: "zip"
        var size = -1L
        try {
            resolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE),
                null, null, null)?.use { c ->
                if (c.moveToFirst()) {
                    c.getColumnIndex(OpenableColumns.DISPLAY_NAME).takeIf { it >= 0 }
                        ?.let { i -> if (!c.isNull(i)) name = c.getString(i) }
                    c.getColumnIndex(OpenableColumns.SIZE).takeIf { it >= 0 }
                        ?.let { i -> if (!c.isNull(i)) size = c.getLong(i) }
                }
            }
        } catch (e: Exception) { /* the name is a courtesy */ }
        if (size < 0) try {
            resolver.openFileDescriptor(uri, "r")?.use { size = it.statSize }
        } catch (e: Exception) { /* then the progress counts files instead */ }
        return Source(name, size,
            { resolver.openInputStream(uri) ?: throw IOException("Cannot open $name") },
            seekable@{
                val pfd = resolver.openFileDescriptor(uri, "r") ?: return@seekable null
                val stream = ParcelFileDescriptor.AutoCloseInputStream(pfd)
                // A pipe answers a size of zero, or throws; a file answers.
                val seekable = try { stream.channel.size() > 0 } catch (e: IOException) { false }
                if (seekable) stream.channel else { stream.close(); null }
            })
    }

    // ---- what is in the zip ----------------------------------------------

    class Entry(val name: String, val size: Long, val compressed: Long,
                val method: Int, val offset: Long)

    /** One engine folder inside the zip. */
    class Found(
        val prefix: String,       // what precedes Speech/: "lion/", or ""
        val version: String?,     // "3.6.59"; null when nothing named it
        val gen: String?,         // the generation that version is; null if none
        val voices: Int,
        val dictionary: Boolean,
        val bytes: Long,          // unpacked, everything under the prefix
        val files: Int,
    )

    /** The answer to "can this be imported, and as what?". Either `refusal`
     * says why not, in words meant for the person holding the phone, or
     * `found` lists what will be imported. */
    class Plan(val found: List<Found>, val refusal: String?) {
        val gens: List<String> get() = found.mapNotNull { it.gen }
        val bytes: Long get() = found.sumOf { it.bytes }
    }

    fun inspect(source: Source): Plan {
        val channel = source.channel()
        return if (channel != null) channel.use { inspectSeekable(it) }
        else inspectStreamed(source)
    }

    /** The version in a plist: `CFBundleVersion`, else the short version. */
    fun plistVersion(text: String): String? {
        for (key in listOf("CFBundleVersion", "CFBundleShortVersionString")) {
            val m = Regex("<key>$key</key>\\s*<string>([^<]+)</string>").find(text) ?: continue
            return m.groupValues[1].trim()
        }
        return null
    }

    /** The version in the binary's build path: Leopard's, Snow Leopard's and
     * Lion's engines carry `SpeechSynthesis-3.6.59` and the like. Tiger's
     * does not, and needs its plist. */
    fun binaryVersion(bytes: ByteArray): String? {
        val text = String(bytes, Charsets.ISO_8859_1)
        return Regex("SpeechSynthesis-(\\d+(?:\\.\\d+)+)").find(text)?.groupValues?.get(1)
    }

    /** Which generation a MacinTalk version belongs to, or null. */
    fun genFor(version: String): String? {
        val parts = version.split('.')
        val major = parts.getOrNull(0)?.toIntOrNull() ?: return null
        val minor = parts.getOrNull(1)?.toIntOrNull() ?: 0
        return when {
            major == 3 && minor <= 5 -> PantheraEngine.GEN_TIGER
            major == 3 && minor <= 9 -> PantheraEngine.GEN_LEOPARD
            major == 3 -> PantheraEngine.GEN_SNOW_LEOPARD
            major == 4 && minor == 0 -> PantheraEngine.GEN_LION
            else -> null
        }
    }

    /** Why a version is refused, in a sentence with the version in it. */
    fun refusalFor(version: String): String {
        val major = version.substringBefore('.').toIntOrNull()
        return if (major != null && major >= 4)
            "MacinTalk $version is Mountain Lion's or later, a 64-bit-only engine. " +
            "This app runs the 32-bit engines: Tiger 3.3, Leopard 3.6, Snow Leopard 3.10 and Lion 4.0."
        else
            "MacinTalk $version is not a version this app knows. It runs Tiger 3.3, " +
            "Leopard 3.6, Snow Leopard 3.10 and Lion 4.0."
    }

    /** Slashes one way, and nothing that leaves the folder. Null means the
     * entry is not a file worth looking at: a directory, Finder's resource
     * forks, a desktop services file. */
    fun cleanName(raw: String): String? {
        val name = raw.replace('\\', '/')
        if (name.endsWith("/")) return null
        val parts = name.split('/')
        if (parts.any { it == "__MACOSX" } || parts.last() == ".DS_Store") return null
        return name
    }

    fun unsafe(name: String): Boolean =
        name.startsWith("/") || name.split('/').any { it == ".." || it.isEmpty() }

    /** Decide from the entry list what the zip holds. `read` fetches one
     * entry's bytes, for the plist and, failing that, the binary. */
    fun plan(entries: List<Entry>, read: (Entry) -> ByteArray?): Plan {
        val byName = LinkedHashMap<String, Entry>()
        for (e in entries) {
            val name = cleanName(e.name) ?: continue
            if (unsafe(name)) return Plan(emptyList(),
                "The zip holds a path that leaves its own folder (${e.name}), so it is not one this app will unpack.")
            byName[name] = e
        }
        val prefixes = byName.keys.filter { it == MACINTALK || it.endsWith("/$MACINTALK") }
            .map { it.removeSuffix(MACINTALK) }
        if (prefixes.isEmpty()) {
            val flattened = byName.keys.any { it == "MacinTalk" || it.endsWith("/MacinTalk") }
            return Plan(emptyList(), if (flattened)
                "The zip holds MacinTalk without its bundle around it, so nothing in it says which " +
                "generation it is. Zip the folder the desktop add-on extracted: it holds Speech and " +
                "SpeechDictionary.framework."
            else
                "No engine in this zip. Zip the folder the desktop add-on extracted for a generation " +
                "-- it holds Speech and SpeechDictionary.framework -- with or without a folder around it.")
        }
        val found = ArrayList<Found>()
        for (prefix in prefixes) {
            var version: String? = null
            for (plist in listOf(INFO_PLIST, VERSION_PLIST)) {
                val e = byName[prefix + plist] ?: continue
                if (e.size > 1 shl 20) continue
                version = read(e)?.let { plistVersion(String(it, Charsets.UTF_8)) }
                if (version != null) break
            }
            if (version == null) byName[prefix + MACINTALK]?.let { e ->
                if (e.size <= 64L shl 20) version = read(e)?.let { binaryVersion(it) }
            }
            val under = byName.entries.filter { it.key.startsWith(prefix) }
            val voices = under.map { it.key.removePrefix(prefix) }
                .filter { it.startsWith(VOICES) }
                .mapNotNull { it.removePrefix(VOICES).substringBefore('/').takeIf { v -> v.endsWith(".SpeechVoice") } }
                .toSet().size
            found.add(Found(prefix, version, version?.let { genFor(it) }, voices,
                byName.containsKey(prefix + DICTIONARY),
                under.sumOf { it.value.size.coerceAtLeast(0) }, under.size))
        }
        for (f in found) {
            val where = if (f.prefix.isEmpty()) "at the top of the zip" else "under ${f.prefix}"
            if (f.version == null) return Plan(found,
                "The engine $where has neither an Info.plist nor a build path naming its version, " +
                "so this app cannot tell which generation it is.")
            if (f.gen == null) return Plan(found, "The engine $where: " + refusalFor(f.version))
            if (!f.dictionary) return Plan(found,
                "The engine $where has no SpeechDictionary.framework beside it, and cannot speak without one. " +
                "Zip the whole folder the desktop add-on extracted.")
            if (f.voices == 0) return Plan(found,
                "The engine $where has no voices: nothing under Speech/Voices. Zip the whole folder.")
        }
        val twice = found.groupBy { it.gen }.filter { it.value.size > 1 }.keys.firstOrNull()
        if (twice != null) return Plan(found,
            "The zip holds ${PantheraEngine.genLabel(twice)} twice, in different folders, " +
            "and this app cannot choose between them.")
        return Plan(found, null)
    }

    // ---- the central directory, for a file that can be seeked --------------

    private fun ByteBuffer.u16(at: Int) = getShort(at).toInt() and 0xffff
    private fun ByteBuffer.u32(at: Int) = getInt(at).toLong() and 0xffffffffL

    private fun FileChannel.readFully(position: Long, length: Int): ByteBuffer {
        val buffer = ByteBuffer.allocate(length).order(ByteOrder.LITTLE_ENDIAN)
        var at = position
        while (buffer.hasRemaining()) {
            val n = read(buffer, at)
            if (n < 0) throw EOFException("zip ends early")
            at += n
        }
        buffer.flip()
        return buffer
    }

    /** The entries, from the central directory at the end of the file.
     * Zip64 is honoured where a zipper used it; a comment up to the format's
     * limit is searched past. Throws when this is not a zip. */
    fun centralDirectory(channel: FileChannel): List<Entry> {
        val size = channel.size()
        val tailLength = minOf(size, 22L + 0xffff).toInt()
        val tail = channel.readFully(size - tailLength, tailLength)
        var eocd = -1
        var i = tailLength - 22
        while (i >= 0) { if (tail.u32(i) == 0x06054b50L) { eocd = i; break }; i-- }
        if (eocd < 0) throw IOException("not a zip file")
        var count = tail.u16(eocd + 10).toLong()
        var cdSize = tail.u32(eocd + 12)
        var cdOffset = tail.u32(eocd + 16)
        if (count == 0xffffL || cdSize == 0xffffffffL || cdOffset == 0xffffffffL) {
            // Zip64: the locator sits just before the record and points at the
            // 64-bit record, whose counts are the ones to believe.
            val locatorAt = size - tailLength + eocd - 20
            if (locatorAt >= 0) {
                val locator = channel.readFully(locatorAt, 20)
                if (locator.u32(0) == 0x07064b50L) {
                    val record = channel.readFully(locator.getLong(8), 56)
                    if (record.u32(0) == 0x06064b50L) {
                        count = record.getLong(32)
                        cdSize = record.getLong(40)
                        cdOffset = record.getLong(48)
                    }
                }
            }
        }
        if (cdSize > 256L shl 20 || cdOffset + cdSize > size) throw IOException("zip directory out of range")
        val cd = channel.readFully(cdOffset, cdSize.toInt())
        val entries = ArrayList<Entry>(count.coerceAtMost(1 shl 20).toInt())
        var at = 0
        while (at + 46 <= cd.limit() && entries.size < count) {
            if (cd.u32(at) != 0x02014b50L) throw IOException("zip directory damaged")
            val flags = cd.u16(at + 8)
            val method = cd.u16(at + 10)
            var compressed = cd.u32(at + 20)
            var uncompressed = cd.u32(at + 24)
            val nameLength = cd.u16(at + 28)
            val extraLength = cd.u16(at + 30)
            val commentLength = cd.u16(at + 32)
            var offset = cd.u32(at + 42)
            val nameBytes = ByteArray(nameLength).also { cd.position(at + 46); cd.get(it) }
            val name = String(nameBytes, if (flags and 0x800 != 0) Charsets.UTF_8 else Charsets.ISO_8859_1)
            // The zip64 extra field carries whichever of the three overflowed,
            // in this order, and only those.
            var extraAt = at + 46 + nameLength
            val extraEnd = extraAt + extraLength
            while (extraAt + 4 <= extraEnd) {
                val id = cd.u16(extraAt); val length = cd.u16(extraAt + 2)
                if (id == 1) {
                    var f = extraAt + 4
                    if (uncompressed == 0xffffffffL && f + 8 <= extraAt + 4 + length) { uncompressed = cd.getLong(f); f += 8 }
                    if (compressed == 0xffffffffL && f + 8 <= extraAt + 4 + length) { compressed = cd.getLong(f); f += 8 }
                    if (offset == 0xffffffffL && f + 8 <= extraAt + 4 + length) { offset = cd.getLong(f) }
                }
                extraAt += 4 + length
            }
            entries.add(Entry(name, uncompressed, compressed, method, offset))
            at += 46 + nameLength + extraLength + commentLength
        }
        return entries
    }

    /** One entry's bytes, read in place: stored or deflated, nothing else. */
    fun readEntry(channel: FileChannel, entry: Entry): ByteArray? {
        val header = channel.readFully(entry.offset, 30)
        if (header.u32(0) != 0x04034b50L) return null
        val dataAt = entry.offset + 30 + header.u16(26) + header.u16(28)
        val stream: InputStream = when (entry.method) {
            0 -> ChannelInputStream(channel, dataAt, entry.compressed, pad = false)
            8 -> InflaterInputStream(ChannelInputStream(channel, dataAt, entry.compressed, pad = true),
                Inflater(true), 1 shl 16)
            else -> return null
        }
        return stream.use { it.readBytes() }
    }

    /** `length` bytes of the file from `start` -- and, for deflated data, one
     * zero byte more: raw deflate through zlib wants a byte past the end of
     * its input to finish on, and the JDK's own ZipFile feeds it exactly this. */
    private class ChannelInputStream(private val channel: FileChannel, start: Long,
                                     private val length: Long, pad: Boolean) : InputStream() {
        private var at = start
        private var left = length
        private var padded = !pad
        override fun read(): Int {
            val one = ByteArray(1)
            return if (read(one, 0, 1) == 1) one[0].toInt() and 0xff else -1
        }
        override fun read(b: ByteArray, off: Int, len: Int): Int {
            if (len == 0) return 0
            if (left <= 0) {
                if (padded) return -1
                padded = true; b[off] = 0
                return 1
            }
            val n = channel.read(ByteBuffer.wrap(b, off, minOf(len.toLong(), left).toInt()), at)
            if (n < 0) return -1
            at += n; left -= n
            return n
        }
    }

    private fun inspectSeekable(channel: FileChannel): Plan = try {
        val entries = centralDirectory(channel)
        plan(entries) { readEntry(channel, it) }
    } catch (e: IOException) {
        Plan(emptyList(), "This is not a zip file this app can read (${e.message}).")
    }

    // ---- front to back, for a source that cannot be seeked -----------------

    private fun inspectStreamed(source: Source): Plan = try {
        val entries = ArrayList<Entry>()
        val kept = HashMap<String, ByteArray>()
        ZipInputStream(BufferedInputStream(source.open(), 1 shl 16)).use { zip ->
            while (true) {
                val e = zip.nextEntry ?: break
                val name = cleanName(e.name)
                val wanted = name != null && (name.endsWith("/$INFO_PLIST") || name == INFO_PLIST ||
                    name.endsWith("/$VERSION_PLIST") || name == VERSION_PLIST ||
                    name.endsWith("/$MACINTALK") || name == MACINTALK)
                var size = 0L
                if (wanted) {
                    val bytes = zip.readBytes()
                    size = bytes.size.toLong()
                    if (name!!.endsWith("Info.plist") || name.endsWith("version.plist")) {
                        if (bytes.size <= 1 shl 20) kept[name] = bytes
                    } else if (bytes.size <= 64 shl 20) kept[name] = bytes
                } else {
                    val buffer = ByteArray(1 shl 16)
                    while (true) { val n = zip.read(buffer); if (n < 0) break; size += n }
                }
                entries.add(Entry(e.name, size, e.compressedSize, e.method, -1))
                zip.closeEntry()
            }
        }
        if (entries.isEmpty()) throw IOException("no entries")
        plan(entries) { kept[cleanName(it.name)] }
    } catch (e: IOException) {
        // Android's own ZipInputStream refuses a `..` path before this code
        // sees it; the refusal reads the same either way.
        val path = Regex("Invalid zip entry path: (.*)").find(e.message ?: "")?.groupValues?.get(1)
        if (path != null) Plan(emptyList(),
            "The zip holds a path that leaves its own folder ($path), so it is not one this app will unpack.")
        else Plan(emptyList(), "This is not a zip file this app can read (${e.message}).")
    }

    // ---- the import itself --------------------------------------------------

    class Cancelled : IOException("cancelled")

    /** Unpack every engine in `plan` under `root`, one folder per generation,
     * replacing what is there only once the whole zip has been written.
     * `progress(done, total)` is called as bytes of the zip go by; `total` is
     * -1 when the source would not say its size. `cancelled()` is polled
     * between writes. Returns the generations now in place. */
    fun extract(source: Source, plan: Plan, root: File,
                progress: (Long, Long) -> Unit, cancelled: () -> Boolean): List<String> {
        check(plan.refusal == null && plan.found.isNotEmpty()) { "nothing to import" }
        // The longest prefix wins, so a zip with an engine at its top and
        // another under lion/ sends lion/'s files to lion.
        val targets = plan.found.sortedByDescending { it.prefix.length }
            .map { it to File(root, it.gen + IMPORTING) }
        root.mkdirs()
        for ((_, dir) in targets) if (dir.exists()) dir.deleteRecursively()
        val counting = CountingInputStream(source.open())
        try {
            ZipInputStream(BufferedInputStream(counting, 1 shl 16)).use { zip ->
                val buffer = ByteArray(1 shl 16)
                while (true) {
                    val e = zip.nextEntry ?: break
                    val name = cleanName(e.name)
                    val target = name?.let { n -> targets.firstOrNull { n.startsWith(it.first.prefix) } }
                    if (name == null || target == null || unsafe(name)) { zip.closeEntry(); continue }
                    val out = File(target.second, name.removePrefix(target.first.prefix))
                    out.parentFile?.mkdirs()
                    FileOutputStream(out).use { o ->
                        while (true) {
                            if (cancelled()) throw Cancelled()
                            val n = zip.read(buffer)
                            if (n < 0) break
                            o.write(buffer, 0, n)
                            progress(counting.count, source.size)
                        }
                    }
                    zip.closeEntry()
                }
            }
            // Everything is on disk. Now, and only now, the swap, in the
            // order the plan named them.
            val done = ArrayList<String>()
            for (found in plan.found) {
                val dir = targets.first { it.first === found }.second
                val gen = found.gen!!
                if (!File(dir, MACINTALK).isFile) throw IOException("the import of $gen is incomplete")
                val live = File(root, gen)
                if (live.exists()) live.deleteRecursively()
                if (!dir.renameTo(live)) throw IOException("could not move $gen into place")
                done.add(gen)
            }
            progress(source.size, source.size)
            return done
        } catch (e: Throwable) {
            for ((_, dir) in targets) dir.deleteRecursively()
            throw e
        } finally {
            try { counting.close() } catch (e: IOException) { /* already failing, or done */ }
        }
    }

    private class CountingInputStream(private val inner: InputStream) : InputStream() {
        @Volatile var count = 0L
        override fun read(): Int = inner.read().also { if (it >= 0) count++ }
        override fun read(b: ByteArray, off: Int, len: Int): Int =
            inner.read(b, off, len).also { if (it > 0) count += it }
        override fun close() = inner.close()
    }

    /** How a size reads to a person: "1.2 GB", "37 MB". */
    fun sizeText(bytes: Long): String = when {
        bytes >= 1L shl 30 -> String.format("%.1f GB", bytes / (1024.0 * 1024 * 1024))
        bytes >= 1L shl 20 -> "${bytes shr 20} MB"
        else -> "${(bytes shr 10).coerceAtLeast(1)} KB"
    }
}
