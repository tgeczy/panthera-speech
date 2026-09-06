// Process-wide singleton owning the one emulated engine. The native handle is
// process-global (host_open maps the images once per process), so every render
// is serialised on [lock]; stop is deliberately NOT under the lock, so it can
// interrupt a render that is holding it.
//
// Voice data lives under the app's own external files dir -- no storage
// permission, works on every Android version and on Play. The user extracts an
// engine generation's files into panthera-data/<generation>/, mirroring the
// layout the NVDA add-on uses; the "Check Engine" step verifies it is there
// before the engine reports any voices.
package com.pantheraspeech.tts

import android.content.Context
import android.content.SharedPreferences
import java.io.File

object PantheraEngine {
    const val PREFS = "pantheraspeech"
    const val PREF_VERIFIED = "engine_verified"      // set by "Check Engine"
    const val PREF_DEFAULT_VOICE = "default_voice"   // voice name, e.g. "Fred"
    const val PREF_RATE = "rate_wpm"                 // 0 = engine default

    const val DATA_DIR = "panthera-data"

    // One folder per Mac OS X speech generation. All four ship the same engine
    // body under different builds, so the host loads any of them; what differs
    // is which voices come with it and what they need underneath. Tiger is the
    // one proven on the watch. The rest are listed because the data layout, the
    // gate and the push harness are all per-generation already, and because
    // "Alex and the other engines" is the point of the port -- but Alex and
    // Vicki both live on the `meow` engine, whose banks are AAC, and the AAC
    // decoder here is still the Windows one. Their generations therefore offer
    // their formant voices and no more until that is ported.
    const val GEN_TIGER = "tiger"
    const val GEN_LEOPARD = "leopard"
    const val GEN_SNOW_LEOPARD = "snowleopard"
    const val GEN_LION = "lion"
    val GENERATIONS = listOf(GEN_TIGER, GEN_LEOPARD, GEN_SNOW_LEOPARD, GEN_LION)

    /** Which generation the engine loads from. Chosen once per process, because
     * host_open maps its images once per process: changing it takes effect on
     * the next start, which is why this is a stored preference and not a live
     * switch. */
    const val PREF_GEN = "engine_generation"

    data class VoiceInfo(
        val name: String,      // "Fred"
        val id: String,        // Android voice id, "panthera-tiger-fred"
        val dir: String,       // absolute path to the .SpeechVoice bundle
        val creator: Int,      // VoiceSpec OSType
        val voiceId: Int,      // VoiceSpec id
    )

    // ---- data layout -------------------------------------------------------
    //
    // Data may live in either the app's external files dir (user-visible, the
    // place to extract into) or its internal files dir (always readable by the
    // app -- the reliable fallback, and where a future in-app import lands).
    // Each generation is looked for in both; whichever holds it wins.

    private fun candidateRoots(ctx: Context): List<File> = listOfNotNull(
        ctx.getExternalFilesDir(null)?.let { File(it, DATA_DIR) },
        File(ctx.filesDir, DATA_DIR),
    )

    /** The root shown to the user as the place to extract data into. */
    fun dataRoot(ctx: Context): File =
        ctx.getExternalFilesDir(null)?.let { File(it, DATA_DIR) }
            ?: File(ctx.filesDir, DATA_DIR)

    private fun mtIn(root: File, gen: String) = File(root, "$gen/MacinTalk")
    private fun sdIn(root: File, gen: String) =
        File(root, "$gen/SpeechDictionary.framework/Versions/A/SpeechDictionary")
    private fun voicesIn(root: File, gen: String) = File(root, "$gen/Voices")

    /** The generation the engine is (or would be) loaded from: the user's
     * choice if its data is actually there, otherwise the first generation that
     * is. A stored choice whose data has since been removed must not strand the
     * app with no engine, so presence wins over preference. */
    private fun activeGen(ctx: Context): String {
        val chosen = prefs(ctx).getString(PREF_GEN, null)
        if (chosen != null && genRoot(ctx, chosen) != null) return chosen
        return GENERATIONS.firstOrNull { genRoot(ctx, it) != null } ?: GEN_TIGER
    }

    /** The generations whose data is present, for the settings picker. */
    fun availableGens(ctx: Context): List<String> =
        GENERATIONS.filter { genPresent(ctx, it) }

    // ---- scanning ----------------------------------------------------------

    /** The candidate root that actually holds this generation's engine, or null. */
    private fun genRoot(ctx: Context, gen: String): File? =
        candidateRoots(ctx).firstOrNull { mtIn(it, gen).isFile && sdIn(it, gen).isFile }

    /** Whether a generation's engine files are all present (in either root). */
    fun genPresent(ctx: Context, gen: String): Boolean =
        genRoot(ctx, gen)?.let { scanVoicesIn(it, gen).isNotEmpty() } == true

    /** The voices found in a generation, each read from its own bundle. Safe to
     * call before the engine is opened (nativeVoiceSpec is a plain file read). */
    fun scanVoices(ctx: Context, gen: String): List<VoiceInfo> =
        genRoot(ctx, gen)?.let { scanVoicesIn(it, gen) } ?: emptyList()

    private fun scanVoicesIn(root: File, gen: String): List<VoiceInfo> {
        val files = voicesIn(root, gen).listFiles() ?: return emptyList()
        val out = ArrayList<VoiceInfo>()
        for (f in files.sortedBy { it.name.lowercase() }) {
            if (!f.isDirectory || !f.name.endsWith(".SpeechVoice")) continue
            val spec = try { PantheraNative.nativeVoiceSpec(f.absolutePath) } catch (e: Throwable) { null }
                ?: continue
            val name = f.name.removeSuffix(".SpeechVoice")
            out.add(VoiceInfo(
                name = name,
                id = "panthera-$gen-${name.lowercase()}",
                dir = f.absolutePath,
                creator = spec[0],
                voiceId = spec[1],
            ))
        }
        return out
    }

    /** The engine's voice list: the active generation's voices, and only those.
     *
     * Not every present generation's. host_open maps one generation's images
     * per process, so a voice from another one would be handed to an engine
     * that has never heard of it -- listing them all would offer the user
     * twenty-four voices of which most answer an OSErr. Which generation is
     * active is the user's choice; see activeGen. */
    fun allVoices(ctx: Context): List<VoiceInfo> = scanVoices(ctx, activeGen(ctx))

    fun voiceById(ctx: Context, id: String?): VoiceInfo? =
        allVoices(ctx).firstOrNull { it.id == id }

    // ---- the gate ----------------------------------------------------------

    fun prefs(ctx: Context): SharedPreferences =
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** The engine is usable only after the user has run "Check Engine" AND the
     * data is actually there.  This is the gate the service honours: no verify,
     * no voices. */
    fun verified(ctx: Context): Boolean =
        prefs(ctx).getBoolean(PREF_VERIFIED, false) && genPresent(ctx, activeGen(ctx))

    /** Run the check: does the data exist? Records the result as the gate. */
    fun checkEngine(ctx: Context): Boolean {
        val ok = genPresent(ctx, activeGen(ctx))
        prefs(ctx).edit().putBoolean(PREF_VERIFIED, ok).apply()
        return ok
    }

    // ---- native lifecycle --------------------------------------------------

    private val lock = Any()
    private var opened = false

    // Hold ownership for the entire stream; the preview uses this same lock.
    // stop() stays outside it so cancellation can interrupt the owner.
    fun <T> withSynthesis(block: () -> T): T = synchronized(lock) {
        try { block() } finally { if (opened) PantheraNative.nativeFinish() }
    }

    private fun open(ctx: Context): Boolean {
        synchronized(lock) {
            if (opened) return true
            val gen = activeGen(ctx)
            val root = genRoot(ctx, gen) ?: return false
            val rc = try {
                PantheraNative.nativeOpen(
                    mtIn(root, gen).absolutePath, sdIn(root, gen).absolutePath)
            } catch (e: Throwable) { return false }
            opened = (rc == 0)
            return opened
        }
    }

    fun isOpen(): Boolean = synchronized(lock) { opened }

    /** Load the engine ahead of the first utterance (the images take a few
     * seconds to map), so a screen reader's first request is not gated on it. */
    fun warmUp(ctx: Context) { synchronized(lock) { open(ctx) } }

    /** Render one utterance to PCM, or null on failure. Serialised.
     *
     * Takes the text as the app has it and puts it through the same emoji and
     * MacRoman pass the speech path uses, so a preview here sounds like the
     * real thing rather than like a second implementation of it. */
    fun render(ctx: Context, voice: VoiceInfo, text: String, wpm: Int): ShortArray? {
        synchronized(lock) {
            if (!open(ctx)) return null
            return try {
                PantheraNative.nativeRender(voice.dir, voice.creator, voice.voiceId,
                    PantheraText.forEngine(text), wpm)
            } catch (e: Throwable) { null }
        }
    }

    /** Begin an utterance for the streaming path. Serialised (does the one-time
     * open + selects the voice); the pull that follows is lock-free. Returns 0
     * or an error. */
    fun speakStart(ctx: Context, voice: VoiceInfo, text: ByteArray, wpm: Int): Int {
        synchronized(lock) {
            if (!open(ctx)) return -1
            return try {
                PantheraNative.nativeSpeakStart(voice.dir, voice.creator, voice.voiceId, text, wpm)
            } catch (e: Throwable) { -1 }
        }
    }

    /** Drain the utterance: fills [out], returns sample count (0 = finished).
     * Not under [lock] -- it only reads the worker's output buffer, and a
     * concurrent stop must be able to interrupt it. */
    fun pull(out: ShortArray): Int =
        try { PantheraNative.nativePull(out) } catch (e: Throwable) { -1 }

    /** Interrupt the utterance in progress. NOT under [lock]. */
    fun stop() {
        try { PantheraNative.nativeStop() } catch (e: Throwable) { /* nothing to stop */ }
    }

    fun sampleRate(): Int =
        try { PantheraNative.nativeSampleRate() } catch (e: Throwable) { 22050 }
}
