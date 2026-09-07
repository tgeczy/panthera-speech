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
    const val PREF_VOLUME = "volume"
    fun volume(ctx: Context): Int = prefs(ctx).getInt(PREF_VOLUME,
        if (activeGen(ctx) == GEN_TIGER) 100 else 90).coerceIn(0, 100)

    const val PREF_RATE = "rate_wpm"                 // 0 = engine default

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

    /**
     * The generations that actually run here, as opposed to the ones whose data
     * this app can find.
     *
     * **Presence is not support, and offering an engine that faults is worse
     * than not offering it.** Snow Leopard and Lion are both measured failures
     * under emulation, on a Galaxy S22, on *both* ABIs -- so this is not the
     * 64-bit gap it was assumed to be:
     *
     *     lion         guest fault, UC_ERR_FETCH_UNMAPPED, in the static
     *                  initializer __GLOBAL__I__ZN12_GLOBAL__N_114freelist_mutexE
     *                  -- libstdc++.6.0.9 calling through a pointer nothing bound
     *     snowleopard  guest fault, UC_ERR_READ_UNMAPPED, earlier still
     *
     * Both die before a voice is ever asked for, so nothing about them can be
     * salvaged by hiding voices. They stay in [GENERATIONS] because the push
     * harness, the data layout and this diagnosis all need names for them, and
     * because the desktop host runs all four -- it is only the emulated path
     * that stops here.
     */
    val SUPPORTED_GENERATIONS = listOf(GEN_TIGER, GEN_LEOPARD)

    /** Why a present generation is not on offer, for the settings page to say
     * out loud rather than silently omitting it. */
    fun unsupportedReason(gen: String): String? =
        if (gen in SUPPORTED_GENERATIONS) null
        else "${genLabel(gen)} is installed but does not run on Android yet — " +
             "its C++ runtime faults before the first voice loads. It works on " +
             "desktop Panthera."

    /** Which generation the engine loads from. One per process, because
     * panthera_init maps its images into a reserved guest block once and there
     * is no unmap: changing it takes effect when the speech service next
     * starts. See [restartNeeded]. */
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

    private fun scanVoicesIn(root: File, gen: String): List<VoiceInfo> {
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
     * the same model, and [restartNeeded] is the same respawn.
     *
     * Sorted by voice name so the list reads alphabetically the way a person
     * expects, with each name's generations together. */
    fun allVoices(ctx: Context): List<VoiceInfo> =
        availableGens(ctx).flatMap { scanVoices(ctx, it) }
            .sortedWith(compareBy({ it.name.lowercase() }, { it.gen }))

    /** The voices of the generation currently loaded (or about to be), which is
     * the subset that can be spoken without restarting first. */
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
        val ok = genPresent(ctx, activeGen(ctx))
        prefs(ctx).edit().putBoolean(PREF_VERIFIED, ok).apply()
        return ok
    }

    // ---- native lifecycle --------------------------------------------------

    private val lock = Any()
    private var opened = false
    /** The generation [open] actually loaded, which after a preference change
     * is not necessarily [activeGen]. */
    private var openedGen: String? = null

    /**
     * True when the chosen generation is not the one this process loaded.
     *
     * `panthera_init` maps its images into a reserved guest block once and
     * offers no unmap, so a second generation cannot be brought up beside the
     * first: the process has to start again. That is not a workaround, it is
     * what the SAPI host already does -- it remembers the tree it was spawned
     * with and respawns on a difference, because "a generation is a different
     * engine".
     *
     * The saving grace is that this is almost never true. The engine opens
     * lazily, on the first utterance, so a user who picks a voice before
     * speaking simply gets the right engine. It only becomes true if they
     * speak, switch, and speak again.
     */
    fun restartNeeded(ctx: Context): Boolean = synchronized(lock) {
        opened && openedGen != null && openedGen != activeGen(ctx)
    }

    /** Which generation is loaded right now, or null before the first open. */
    fun loadedGen(): String? = synchronized(lock) { openedGen }

    /**
     * End this process so the next one loads the generation now chosen.
     *
     * Deliberately abrupt. There is nothing to tidy: the engine's state is the
     * mapped images and the guest arena, both of which die with the process,
     * and any half-finished utterance has already been refused. Android
     * restarts a bound speech service on demand -- the same path it uses after
     * a crash, and one this project has watched work in logcat.
     *
     * Called only after the choice has been written to preferences, so the
     * replacement process comes up with the generation the user asked for.
     */
    fun exitForGenerationChange() {
        android.util.Log.i("PantheraEngine", "exiting so the next process loads a new generation")
        Thread {
            // A beat, so the refusal reaches the client before the binder dies
            // and it sees a service death instead of an error it can report.
            try { Thread.sleep(150) } catch (e: InterruptedException) { }
            android.os.Process.killProcess(android.os.Process.myPid())
        }.start()
    }

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
            if (opened) openedGen = gen
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
                applyNumberStyle(ctx)
                PantheraNative.nativeSetVolume(volume(ctx), activeGen(ctx))
                PantheraNative.nativeRender(voice.dir, voice.creator, voice.voiceId,
                    PantheraText.forEngine(text), wpm)
            } catch (e: Throwable) { null }
        }
    }

    /** Begin an utterance for the streaming path. Serialised (does the one-time
     * open + selects the voice); the pull that follows is lock-free. Returns 0
     * or an error. */
    /** Push the number preference down before an utterance uses it. */
    private fun applyNumberStyle(ctx: Context) {
        PantheraNative.nativeSetNumberStyle(
            prefs(ctx).getString(PREF_NUMBER_STYLE, NUMBER_STYLE_DEFAULT)
                ?: NUMBER_STYLE_DEFAULT)
    }

    fun speakStart(ctx: Context, voice: VoiceInfo, text: ByteArray, wpm: Int): Int {
        synchronized(lock) {
            if (!open(ctx)) return -1
            return try {
                applyNumberStyle(ctx)
                PantheraNative.nativeSetVolume(volume(ctx), activeGen(ctx))
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
