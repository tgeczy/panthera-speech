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

    // v1 supports the Tiger generation (Fred and the other MacinTalk voices).
    // Each generation is a folder; more get added here as they come online.
    const val GEN_TIGER = "tiger"
    val GENERATIONS = listOf(GEN_TIGER)

    data class VoiceInfo(
        val name: String,      // "Fred"
        val id: String,        // Android voice id, "panthera-tiger-fred"
        val dir: String,       // absolute path to the .SpeechVoice bundle
        val creator: Int,      // VoiceSpec OSType
        val voiceId: Int,      // VoiceSpec id
    )

    // ---- data layout -------------------------------------------------------

    fun dataRoot(ctx: Context): File = File(ctx.getExternalFilesDir(null), DATA_DIR)
    fun genDir(ctx: Context, gen: String): File = File(dataRoot(ctx), gen)
    private fun macinTalk(ctx: Context, gen: String) = File(genDir(ctx, gen), "MacinTalk")
    private fun speechDict(ctx: Context, gen: String) =
        File(genDir(ctx, gen), "SpeechDictionary.framework/Versions/A/SpeechDictionary")
    private fun voicesDir(ctx: Context, gen: String) = File(genDir(ctx, gen), "Voices")

    /** The one generation the engine is (or would be) loaded from. v1: Tiger. */
    private fun activeGen(): String = GEN_TIGER

    // ---- scanning ----------------------------------------------------------

    /** Whether a generation's engine files are all present. */
    fun genPresent(ctx: Context, gen: String): Boolean =
        macinTalk(ctx, gen).isFile && speechDict(ctx, gen).isFile &&
            scanVoices(ctx, gen).isNotEmpty()

    /** The voices found in a generation, each read from its own bundle. Safe to
     * call before the engine is opened (nativeVoiceSpec is a plain file read). */
    fun scanVoices(ctx: Context, gen: String): List<VoiceInfo> {
        val files = voicesDir(ctx, gen).listFiles() ?: return emptyList()
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

    /** Every voice across every present generation -- the engine's voice list. */
    fun allVoices(ctx: Context): List<VoiceInfo> =
        GENERATIONS.flatMap { if (genPresent(ctx, it)) scanVoices(ctx, it) else emptyList() }

    fun voiceById(ctx: Context, id: String?): VoiceInfo? =
        allVoices(ctx).firstOrNull { it.id == id }

    // ---- the gate ----------------------------------------------------------

    fun prefs(ctx: Context): SharedPreferences =
        ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** The engine is usable only after the user has run "Check Engine" AND the
     * data is actually there.  This is the gate the service honours: no verify,
     * no voices. */
    fun verified(ctx: Context): Boolean =
        prefs(ctx).getBoolean(PREF_VERIFIED, false) && genPresent(ctx, activeGen())

    /** Run the check: does the data exist? Records the result as the gate. */
    fun checkEngine(ctx: Context): Boolean {
        val ok = genPresent(ctx, activeGen())
        prefs(ctx).edit().putBoolean(PREF_VERIFIED, ok).apply()
        return ok
    }

    // ---- native lifecycle --------------------------------------------------

    private val lock = Any()
    private var opened = false

    private fun open(ctx: Context): Boolean {
        synchronized(lock) {
            if (opened) return true
            val gen = activeGen()
            if (!genPresent(ctx, gen)) return false
            val rc = try {
                PantheraNative.nativeOpen(
                    macinTalk(ctx, gen).absolutePath, speechDict(ctx, gen).absolutePath)
            } catch (e: Throwable) { return false }
            opened = (rc == 0)
            return opened
        }
    }

    fun isOpen(): Boolean = synchronized(lock) { opened }

    /** Render one utterance to PCM, or null on failure. Serialised. */
    fun render(ctx: Context, voice: VoiceInfo, text: String, wpm: Int): ShortArray? {
        synchronized(lock) {
            if (!open(ctx)) return null
            return try {
                PantheraNative.nativeRender(voice.dir, voice.creator, voice.voiceId, text, wpm)
            } catch (e: Throwable) { null }
        }
    }

    /** Interrupt the render in progress. NOT under [lock] -- render holds it. */
    fun stop() {
        try { PantheraNative.nativeStop() } catch (e: Throwable) { /* nothing to stop */ }
    }

    fun sampleRate(): Int =
        try { PantheraNative.nativeSampleRate() } catch (e: Throwable) { 22050 }
}
