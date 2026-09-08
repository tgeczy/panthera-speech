package com.pantheraspeech.tts

import android.content.Context
import android.content.SharedPreferences
import java.io.File

object PantheraEngine {
    const val PREFS = "pantheraspeech"
    const val PREF_VERIFIED = "engine_verified"      // set by "Check Engine"
    const val PREF_DEFAULT_VOICE = "default_voice"   // voice name, e.g. "Fred"
    const val PREF_VOLUME = "volume"
    const val PREF_RATE = "rate_wpm"                 // 0 = follow the requesting app

    /**
     * How numbers are read: "off", "fix" (the default) or "words".
     *
     * "fix" changes only what the engine gets wrong -- seven digits and up,
     * which it otherwise spells one at a time, and versions and decimals
     * whose leading zero it drops. "words" reads every number out in full.
     * The rules themselves are in the host, shared with every other platform.
     */
    const val PREF_NUMBER_STYLE = "number_style"
    const val NUMBER_STYLE_DEFAULT = "fix"

    const val DATA_DIR = "panthera-data"

    // One folder per Mac OS X speech generation. All four share one engine body
    // under different builds, so the loader takes any of them; the data layout,
    // the gate and the push harness have always been per-generation.
    const val GEN_TIGER = "tiger"
    const val GEN_LEOPARD = "leopard"
    const val GEN_SNOW_LEOPARD = "snowleopard"
    const val GEN_LION = "lion"
    val GENERATIONS = listOf(GEN_TIGER, GEN_LEOPARD, GEN_SNOW_LEOPARD, GEN_LION)

    /** How a generation is named to a person: "Leopard", "Snow Leopard". */
    fun genLabel(gen: String): String = when (gen) {
        GEN_TIGER -> "Tiger"
        GEN_LEOPARD -> "Leopard"
        GEN_SNOW_LEOPARD -> "Snow Leopard"
        GEN_LION -> "Lion"
        else -> gen
    }

    // Offered only after the native and framework device suites pass.
    val SUPPORTED_GENERATIONS = GENERATIONS
    fun unsupportedReason(gen: String): String? =
        if (gen in SUPPORTED_GENERATIONS) null else "${genLabel(gen)} is not supported yet."

    const val PREF_GEN = "engine_generation"

    data class VoiceInfo(
        val name: String,      // "Fred"
        val id: String,        // Android voice id, "panthera-tiger-fred"
        val dir: String,       // absolute path to the .SpeechVoice bundle
        val creator: Int,      // VoiceSpec OSType
        val voiceId: Int,      // VoiceSpec id
        val gen: String,       // which generation it belongs to
    ) {
        /** How the voice is named in a list a person reads.
         *
         * Generation-qualified, because a bare "Alex" is four different voices
         * -- Tiger has none, Leopard's is MacinTalk 3.6 and Lion's is 4.0 --
         * and a list with four of them is a list with none. The SAPI side
         * settled this first and its tokens read the same way, "Alex (Leopard)";
         * matching it means the two platforms describe one voice identically. */
        val label: String get() = "$name (${genLabel(gen)})"
    }

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
    fun activeGen(ctx: Context): String {
        val chosen = prefs(ctx).getString(PREF_GEN, null)
        if (chosen != null && chosen in SUPPORTED_GENERATIONS &&
            genRoot(ctx, chosen) != null) return chosen
        return SUPPORTED_GENERATIONS.firstOrNull { genRoot(ctx, it) != null } ?: GEN_TIGER
    }

    /** The generations whose data is present AND which run here. This is what
     * the settings picker and the voice list are built from. */
    fun availableGens(ctx: Context): List<String> =
        SUPPORTED_GENERATIONS.filter { genPresent(ctx, it) }

    /** Present, but known not to run -- so the settings page can say why
     * instead of leaving the user to wonder where their data went. */
    fun presentButUnsupportedGens(ctx: Context): List<String> =
        GENERATIONS.filter { it !in SUPPORTED_GENERATIONS && genPresent(ctx, it) }

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

    private data class VoiceCatalogue(val modified: Long, val voices: List<VoiceInfo>)
    private val catalogueLock = Any()
    private val catalogues = mutableMapOf<String, VoiceCatalogue>()

    /** Bundle identity changes on import, not on every spoken digit. Directory
     * changes invalidate automatically; Check Engine also refreshes descriptions
     * edited inside an existing bundle without changing the parent directory. */
    fun refreshVoiceCatalogue() = synchronized(catalogueLock) { catalogues.clear() }

    private fun scanVoicesIn(root: File, gen: String): List<VoiceInfo> = synchronized(catalogueLock) {
        val directory = voicesIn(root, gen)
        val modified = directory.lastModified()
        catalogues[directory.absolutePath]?.let {
            if (it.modified == modified) return@synchronized it.voices
        }
        val voices = readVoiceDescriptions(root, gen)
        catalogues[directory.absolutePath] = VoiceCatalogue(modified, voices)
        voices
    }

    private fun readVoiceDescriptions(root: File, gen: String): List<VoiceInfo> {
        val files = voicesIn(root, gen).listFiles() ?: return emptyList()
        val out = ArrayList<VoiceInfo>()
        for (f in files.sortedBy { it.name.lowercase() }) {
            if (!f.isDirectory || !f.name.endsWith(".SpeechVoice")) continue
            // A bundle that is present and still not offered is worth a line:
            // silently dropping one is indistinguishable from never having
            // copied it, and that cost an hour once.
            val spec = try { PantheraNative.nativeVoiceSpec(f.absolutePath) }
                catch (e: Throwable) {
                    android.util.Log.w("PantheraEngine", "voiceSpec threw for ${f.name}: $e"); null
                }
            if (spec == null) {
                android.util.Log.w("PantheraEngine", "no usable VoiceDescription in ${f.name}")
                continue
            }
            val name = f.name.removeSuffix(".SpeechVoice")
            out.add(VoiceInfo(
                name = name,
                id = "panthera-$gen-${name.lowercase()}",
                dir = f.absolutePath,
                creator = spec[0],
                voiceId = spec[1],
                gen = gen,
            ))
        }
        return out
    }

    /** Every voice of every generation that is present and runs here.
     *
     * **Picking a voice is how you pick an engine.** This used to list only the
     * active generation's voices, which made the engine a separate setting the
     * user had to find first, and made a saved voice name dangle the moment
     * they changed it -- "Alex" means nothing in Tiger. SAPI settled the
     * question first: it registers one token per voice per generation, named
     * "Alex (Leopard)", and respawns its host when the tree changes. This is
     * the same model, each private worker owns one generation.
     *
     * Sorted by voice name so the list reads alphabetically the way a person
     * expects, with each name's generations together. */
    fun allVoices(ctx: Context): List<VoiceInfo> =
        availableGens(ctx).flatMap { scanVoices(ctx, it) }
            .sortedWith(compareBy({ it.name.lowercase() }, { it.gen }))

    /** Voices of the generation selected in settings. */
    fun activeVoices(ctx: Context): List<VoiceInfo> = scanVoices(ctx, activeGen(ctx))

    fun voiceById(ctx: Context, id: String?): VoiceInfo? =
        allVoices(ctx).firstOrNull { it.id == id }

    // ---- per-generation settings -------------------------------------------

    /** The chosen voice is stored per generation.
     *
     * One global name could not survive a switch: "Alex" is a Leopard voice and
     * Tiger has no such bundle, so going Leopard -> Tiger -> Leopard used to
     * lose the choice, and going the other way left a name the new generation
     * could not resolve. Keyed by generation, each engine remembers its own
     * voice and a switch is reversible. */
    fun voicePrefKey(gen: String) = "${PREF_DEFAULT_VOICE}_$gen"

    fun defaultVoiceName(ctx: Context, gen: String): String? {
        val p = prefs(ctx)
        p.getString(voicePrefKey(gen), null)?.let { return it }
        // Carry the old single-valued preference into whichever generation was
        // active when it was written, once, so nobody's setting disappears in
        // an upgrade. Then it is per-generation like everything else.
        val legacy = p.getString(PREF_DEFAULT_VOICE, null) ?: return null
        if (gen != activeGen(ctx)) return null
        p.edit().putString(voicePrefKey(gen), legacy).remove(PREF_DEFAULT_VOICE).apply()
        return legacy
    }

    /** Record a voice choice, and with it the generation that voice belongs to.
     * These are one action: choosing "Alex (Leopard)" *is* choosing Leopard. */
    fun chooseVoice(ctx: Context, voice: VoiceInfo) {
        defaultVoiceName(ctx, activeGen(ctx)) // migrate the old choice before switching
        prefs(ctx).edit()
            .putString(voicePrefKey(voice.gen), voice.name)
            .putString(PREF_GEN, voice.gen)
            .apply()
    }

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
        refreshVoiceCatalogue()
        val ok = genPresent(ctx, activeGen(ctx))
        prefs(ctx).edit().putBoolean(PREF_VERIFIED, ok).apply()
        return ok
    }

    // Settings are read together once per utterance, after resolving its voice.
    // Legacy global values remain the fallback until a generation is customized.
    fun settingKey(key: String, gen: String) = "${key}_$gen"
    const val PREF_COMMANDS = "accept_commands"
    const val PREF_ABBREVIATIONS = "expand_abbreviations"
    const val PREF_INFLECTION = "inflection"
    const val PREF_PHRASING = "phrasing"
    val PHRASING_VALUES = listOf("fewest", "fewer", "more", "most", "leopard")
    const val VOLUME_SYSTEM_DEFAULT = -1
    fun defaultVolume(generation: String) = if (generation == GEN_TIGER) 100 else 90
    fun volumeLevels(saved: Int): List<Int> =
        (listOf(VOLUME_SYSTEM_DEFAULT) + (0..100 step 5) + listOf(saved.coerceIn(-1, 100))).distinct().sorted()
    fun supportsPhrasing(generation: String) = generation != GEN_TIGER
    data class Settings(val volume: Int, val rate: Int, val numbers: String,
                        val acceptCommands: Boolean = false, val expandAbbreviations: Boolean = true,
                        val phrasing: String = "fewest", val inflection: Int = 50) {
        fun engineVolume(generation: String) = if (volume < 0) defaultVolume(generation) else volume
        fun wpm(requestRate: Int): Int = if (rate > 0) rate.coerceIn(80, 500)
            else (180 * (if (requestRate <= 0) 100 else requestRate) / 100).coerceIn(80, 500)
    }
    fun settings(ctx: Context, gen: String = activeGen(ctx)): Settings {
        val values = prefs(ctx).all
        fun value(key: String): Any? = values[settingKey(key, gen)] ?: values[key]
        return Settings(
            ((value(PREF_VOLUME) as? Int) ?: VOLUME_SYSTEM_DEFAULT).coerceIn(-1, 100),
            (value(PREF_RATE) as? Int) ?: 0,
            (value(PREF_NUMBER_STYLE) as? String)?.takeIf { it in listOf("fix", "words", "off") }
                ?: NUMBER_STYLE_DEFAULT,
            (value(PREF_COMMANDS) as? Boolean) ?: false,
            (value(PREF_ABBREVIATIONS) as? Boolean) ?: true,
            (value(PREF_PHRASING) as? String)?.takeIf { it in PHRASING_VALUES } ?: "fewest",
            ((value(PREF_INFLECTION) as? Int) ?: 50).coerceIn(0, 100))
    }
    fun volume(ctx: Context): Int = settings(ctx).volume

    private val lock = Any()
    @Volatile private var worker: IPantheraWorker? = null
    @Volatile private var request: PantheraRequest<IPantheraWorker>? = null
    @Volatile private var openedGen: String? = null
    @Volatile private var rate = 22050
    fun loadedGen(): String? = openedGen
    fun isOpen(): Boolean = worker != null

    fun <T> withSynthesis(block: () -> T): T = synchronized(lock) {
        val owned = PantheraRequest<IPantheraWorker>(PantheraWorkers::retire) { api ->
            // Playback can remain queued after synthesis has already finished.
            // Preserve that warm worker; only unfinished synthesis needs death.
            val complete = try { api.renderComplete() } catch (_: Exception) { false }
            if (complete) android.util.Log.i("PantheraEngine", "Keeping completed engine worker after playback stop")
            complete
        }
        check(request == null) { "Nested synthesis request" }
        request = owned
        try { block() } finally {
            try { owned.current()?.finish() } catch (e: Exception) {
                android.util.Log.w("PantheraEngine", "Engine finish failed", e)
            } finally { owned.complete(); request = null }
        }
    }

    private fun open(ctx: Context, gen: String, phrasing: String = settings(ctx, gen).phrasing,
                     inflection: Int = settings(ctx, gen).inflection): IPantheraWorker {
        val root = genRoot(ctx, gen) ?: error("Engine data missing for $gen")
        val requested = if (supportsPhrasing(gen)) phrasing else "leopard"
        var next = PantheraWorkers.get(ctx, gen)
        var hz = next.open(mtIn(root, gen).absolutePath, sdIn(root, gen).absolutePath, requested, inflection)
        if (hz == PantheraWorkerService.RECONFIGURE) {
            worker = null
            PantheraWorkers.restart(gen)
            next = PantheraWorkers.get(ctx, gen)
            hz = next.open(mtIn(root, gen).absolutePath, sdIn(root, gen).absolutePath, requested, inflection)
        }
        check(hz > 0) { "Engine could not open: $gen" }
        worker = next
        openedGen = gen
        rate = hz
        return next
    }
    fun warmUp(ctx: Context) { synchronized(lock) { open(ctx, activeGen(ctx)) } }

    /** Preview uses the same streaming API and settings as platform speech. */
    fun render(ctx: Context, voice: VoiceInfo, text: String, wpm: Int): ShortArray? = withSynthesis {
        try {
            val snapshot = settings(ctx, voice.gen)
            val chunks = ArrayList<ShortArray>()
            var total = 0
            val buffer = ShortArray(4096)
            for (piece in PantheraText.pieces(text, snapshot, voice.gen)) {
                check(speakStart(ctx, voice, PantheraText.bytes(piece), wpm, snapshot) == 0)
                while (true) {
                    val count = pull(buffer)
                    check(count >= 0)
                    if (count == 0) break
                    chunks.add(buffer.copyOf(count)); total += count
                }
            }
            ShortArray(total).also { result ->
                var offset = 0
                for (chunk in chunks) { chunk.copyInto(result, offset); offset += chunk.size }
            }
        } catch (e: Exception) {
            android.util.Log.e("PantheraEngine", "Preview failed", e); null
        }
    }

    fun speakStart(ctx: Context, voice: VoiceInfo, text: ByteArray, wpm: Int,
                   snapshot: Settings = settings(ctx, voice.gen)): Int = synchronized(lock) {
        try {
            val owned = checkNotNull(request) { "Speech requires withSynthesis" }
            val next = open(ctx, voice.gen, snapshot.phrasing, snapshot.inflection)
            // A stop during open stays attached to this request. It cannot be
            // lost in the interval before nativeSpeakStart marks itself active.
            if (!owned.attach(next)) return@synchronized -1
            next.start(voice.dir, voice.creator, voice.voiceId, text, wpm,
                snapshot.engineVolume(voice.gen), voice.gen, snapshot.numbers, snapshot.expandAbbreviations, snapshot.inflection)
                .also { if (it == 0) owned.started() }
        } catch (e: Exception) {
            android.util.Log.e("PantheraEngine", "Speech failed", e); -1
        }
    }
    fun pull(out: ShortArray): Int { return try {
        val bytes = request?.current()?.pull(out.size) ?: return -1
        for (i in 0 until bytes.size / 2)
            out[i] = ((bytes[i*2].toInt() and 255) or (bytes[i*2+1].toInt() shl 8)).toShort()
        bytes.size / 2
    } catch (e: Exception) { -1 } }
    fun stop() { request?.cancel() }
    fun sampleRate(): Int = rate
}
