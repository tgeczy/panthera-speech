package com.pantheraspeech.tts

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.SharedPreferences
import android.os.Build
import android.os.StatFs
import android.os.UserManager
import android.util.Log
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
    /** Generations the person has switched off.  Their data may well be
     * present; they are simply not wanted, and a generation that is not
     * wanted is not offered anywhere: not in the voice list a screen reader
     * sees, not in the settings picker, not as a fallback engine. */
    const val PREF_DISABLED_GENS = "disabled_generations"

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
    // Data lives in device-protected storage: the half of the app's files
    // that exists before the phone has been unlocked, which is what lets a
    // screen reader read the lock screen after a reboot with these voices --
    // see ProtectedStorage for the whole of it.  Two other places are looked
    // in, and only once unlocked, because before that they do not exist: the
    // external files dir a PC's file window shows, which is the inbox a
    // person copies into, and the internal files dir a debug push writes to.
    // Whatever is found in either is moved into protected storage by
    // `migrate`, so a lookup that finds a generation there is finding it on
    // its way in.

    /** Whether the person has unlocked the phone since it booted.  Before
     * that, only protected storage may be touched: reading the other half
     * throws, and an engine that throws on the lock screen is worse than
     * one that is quiet there. */
    @Volatile private var knownUnlocked = false

    fun unlocked(ctx: Context): Boolean {
        // Asked on every lookup, and a lookup happens several times per
        // utterance.  Once true it stays true for the life of the process --
        // no process survives the reboot that would lock the user again --
        // so the system is asked only until it says yes.
        if (knownUnlocked) return true
        val now = try { ctx.getSystemService(UserManager::class.java)?.isUserUnlocked ?: true }
                  catch (e: Exception) { true }
        if (now) knownUnlocked = true
        return now
    }

    private fun protectedContext(ctx: Context): Context =
        if (ctx.isDeviceProtectedStorage) ctx else ctx.createDeviceProtectedStorageContext()

    /** Where the engine data lives, and where a zip import lands. */
    fun dataRoot(ctx: Context): File = File(protectedContext(ctx).filesDir, DATA_DIR)

    /** The folder a PC's file window shows -- the inbox -- or null before
     * unlock.  Not created here: this is on the path of every utterance,
     * and a mkdirs on shared storage is a round trip through FUSE each
     * time.  Setup creates it, which is where somebody about to copy into
     * it is looking. */
    fun inboxRoot(ctx: Context): File? =
        if (!unlocked(ctx)) null
        else try { ctx.getExternalFilesDir(null)?.let { File(it, DATA_DIR) } }
             catch (e: Exception) { null }

    /** The internal folder the data used to be read from, and a debug push
     * still writes to.  Null before unlock. */
    private fun legacyRoot(ctx: Context): File? =
        if (!unlocked(ctx)) null else File(ctx.filesDir, DATA_DIR)

    private fun candidateRoots(ctx: Context): List<File> =
        listOfNotNull(dataRoot(ctx), inboxRoot(ctx), legacyRoot(ctx))

    /** A generation just imported is wanted, whatever its switch said
     * before; then the catalogue is rebuilt and the gate re-run, so the new
     * voices are offered without a trip to Check Engine. */
    fun imported(ctx: Context, gens: Collection<String>) {
        val disabled = disabledGens(ctx).toMutableSet()
        if (disabled.removeAll(gens.toSet()))
            prefs(ctx).edit().putStringSet(PREF_DISABLED_GENS, disabled).apply()
        checkEngine(ctx)
    }

    // Two layouts are accepted inside a generation folder.  The Mac's own is
    // what the desktop add-on extracts and what a person copies over as it
    // is: Speech/Synthesizers/MacinTalk.SpeechSynthesizer/Contents/MacOS/
    // MacinTalk, Speech/Voices, SpeechDictionary.framework.  The flattened
    // one, MacinTalk and Voices at the top, is what the development push
    // script makes.  The dictionary framework sits at the same place in both,
    // and so does the engine's C++ runtime, which the host finds by searching
    // upward from the dictionary -- so nothing below this needs to know.
    private fun mtIn(root: File, gen: String): File {
        val flat = File(root, "$gen/MacinTalk")
        return if (flat.isFile) flat
        else File(root, "$gen/Speech/Synthesizers/MacinTalk.SpeechSynthesizer/Contents/MacOS/MacinTalk")
    }
    private fun sdIn(root: File, gen: String) =
        File(root, "$gen/SpeechDictionary.framework/Versions/A/SpeechDictionary")
    private fun voicesIn(root: File, gen: String): File {
        val flat = File(root, "$gen/Voices")
        return if (flat.isDirectory) flat else File(root, "$gen/Speech/Voices")
    }

    /** The generation the engine is (or would be) loaded from: the user's
     * choice if its data is actually there, otherwise the first generation that
     * is. A stored choice whose data has since been removed must not strand the
     * app with no engine, so presence wins over preference. */
    fun activeGen(ctx: Context): String {
        val chosen = prefs(ctx).getString(PREF_GEN, null)
        if (chosen != null && chosen in SUPPORTED_GENERATIONS && genOffered(ctx, chosen) &&
            genRoot(ctx, chosen) != null) return chosen
        return SUPPORTED_GENERATIONS.firstOrNull { genOffered(ctx, it) && genRoot(ctx, it) != null }
            ?: GEN_TIGER
    }

    /** The generations the person has switched off in Engine settings. */
    fun disabledGens(ctx: Context): Set<String> =
        prefs(ctx).getStringSet(PREF_DISABLED_GENS, null) ?: emptySet()

    fun genOffered(ctx: Context, gen: String): Boolean = gen !in disabledGens(ctx)

    /** Switch a generation on or off.  Off means its voices vanish from every
     * list at once, its data untouched; on brings them back.  The last
     * generation standing cannot be switched off, because an engine with no
     * voices is a screen reader with no speech. */
    fun setGenOffered(ctx: Context, gen: String, offered: Boolean): Boolean {
        val disabled = disabledGens(ctx).toMutableSet()
        if (offered) disabled.remove(gen) else {
            val remaining = SUPPORTED_GENERATIONS.filter { genPresent(ctx, it) && it != gen && it !in disabled }
            if (remaining.isEmpty()) return false
            disabled.add(gen)
        }
        prefs(ctx).edit().putStringSet(PREF_DISABLED_GENS, disabled).apply()
        return true
    }

    /** The generations whose data is present, which run here, AND which the
     * person has not switched off. This is what the settings picker and the
     * voice list are built from. */
    fun availableGens(ctx: Context): List<String> =
        SUPPORTED_GENERATIONS.filter { genOffered(ctx, it) && genPresent(ctx, it) }

    /** Present and runnable, whether offered or not: what the switches list. */
    fun installedGens(ctx: Context): List<String> =
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

    @Volatile private var prefsMoved = false

    /** The settings, in device-protected storage so the service can read
     * them before unlock.  The first call in a process after unlock carries
     * them over from where they used to live -- a no-op once nothing is
     * left there -- so nobody's voice or rate is lost to the move.  The
     * other half cannot even be asked before unlock: it throws. */
    fun prefs(ctx: Context): SharedPreferences {
        val protected = protectedContext(ctx)
        if (!prefsMoved && !ctx.isDeviceProtectedStorage && unlocked(ctx)) {
            try { protected.moveSharedPreferencesFrom(ctx, PREFS) }
            catch (e: Exception) { Log.w("PantheraEngine", "settings could not be moved", e) }
            prefsMoved = true
        }
        return protected.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    }

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

    // ---- moving data into protected storage --------------------------------
    //
    // A generation found in the inbox or the old internal folder is copied
    // into protected storage and then removed from where it was.  The engine
    // keeps working throughout: a worker that already has the old copy mapped
    // keeps it until it is next replaced, and the lookup finds whichever copy
    // exists.  One move at a time, and never on the main thread.

    private val migrateLock = Any()
    @Volatile private var unlockWatcher: BroadcastReceiver? = null

    /** What the last move had to say, in words for the Setup page; null when
     * there has never been anything to move. */
    @Volatile var migrationNote: String? = null
        private set

    /** Whether a move is running right now. */
    @Volatile var migrating: Boolean = false
        private set

    /** (folder, generation) pairs sitting outside protected storage.  Empty
     * before unlock, because those folders cannot be looked at.
     *
     * The internal folder first and the inbox last, because a later move
     * replaces an earlier one: the inbox used to be looked in first, so a
     * generation in both places spoke from the inbox, and it still does. */
    fun pendingMoves(ctx: Context): List<Pair<File, String>> =
        listOfNotNull(legacyRoot(ctx), inboxRoot(ctx)).flatMap { root ->
            GENERATIONS.filter { mtIn(root, it).isFile }.map { root to it }
        }

    /** Move everything found outside protected storage into it.
     * `progress(generation, bytesDone, bytesTotal)` follows the copy.
     * -> the generations moved. */
    fun migrate(ctx: Context, progress: ((String, Long, Long) -> Unit)? = null): List<String> =
        synchronized(migrateLock) {
            val moved = ArrayList<String>()
            migrating = true
            try {
                for ((root, gen) in pendingMoves(ctx)) {
                    val into = dataRoot(ctx)
                    into.mkdirs()
                    val bytes = ProtectedStorage.size(File(root, gen))
                    val free = try { StatFs(into.absolutePath).availableBytes } catch (e: Exception) { -1L }
                    if (free in 0 until bytes + (64L shl 20)) {
                        migrationNote = "${genLabel(gen)} could not be moved into protected storage: " +
                            "it needs ${ZipImport.sizeText(bytes)} and this device has " +
                            "${ZipImport.sizeText(free)} free. It will speak, but not before the " +
                            "phone is unlocked."
                        Log.w("PantheraEngine", migrationNote!!)
                        continue
                    }
                    try {
                        ProtectedStorage.move(root, gen, into, { d, t -> progress?.invoke(gen, d, t) })
                        moved.add(gen)
                        Log.i("PantheraEngine", "moved $gen (${ZipImport.sizeText(bytes)}) from $root " +
                            "into protected storage")
                    } catch (e: Exception) {
                        migrationNote = "${genLabel(gen)} could not be moved into protected storage: ${e.message}"
                        Log.w("PantheraEngine", migrationNote!!, e)
                    }
                }
                if (moved.isNotEmpty()) {
                    refreshVoiceCatalogue()
                    migrationNote = "Moved ${moved.joinToString(" and ") { genLabel(it) }} into " +
                        "protected storage, so the voices can speak before the phone is unlocked."
                }
            } finally {
                migrating = false
            }
            moved
        }

    /** Move data in as soon as the phone is unlocked: now, or when the
     * unlock arrives.  The service calls this when it starts, which on a
     * phone that has just booted is before unlock. */
    fun migrateWhenUnlocked(ctx: Context) {
        val app = ctx.applicationContext
        fun now() = Thread({
            try { migrate(app) } catch (e: Throwable) { Log.w("PantheraEngine", "the move failed", e) }
        }, "panthera-move").apply { priority = Thread.MIN_PRIORITY }.start()
        if (unlocked(app)) { now(); return }
        synchronized(migrateLock) {
            if (unlockWatcher != null) return
            val watcher = object : BroadcastReceiver() {
                override fun onReceive(c: Context, intent: Intent) {
                    try { app.unregisterReceiver(this) } catch (e: Exception) { /* already gone */ }
                    synchronized(migrateLock) { if (unlockWatcher === this) unlockWatcher = null }
                    now()
                }
            }
            unlockWatcher = watcher
            val filter = IntentFilter(Intent.ACTION_USER_UNLOCKED)
            if (Build.VERSION.SDK_INT >= 33) app.registerReceiver(watcher, filter, Context.RECEIVER_NOT_EXPORTED)
            else app.registerReceiver(watcher, filter)
        }
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
    private val cancellations = java.util.concurrent.Executors.newSingleThreadExecutor { work ->
        Thread(work, "panthera-cancel")
    }
    // Warm short labels on the Nothing Phone finished in 11-60 ms with Alex.
    // A stop just before completion should not pay another process/voice load.
    // This is a maximum opportunity to finish, never an unconditional sleep.
    private const val COMPLETION_GRACE_MS = 60L
    // The measured gain is for short labels (digits, Search, Messages).
    // Longer requests use explicit native cleanup instead of waiting to render.
    private const val COMPLETION_GRACE_MAX_BYTES = 32
    private const val NATIVE_CLEANUP_MS = 120
    @Volatile private var worker: IPantheraWorker? = null
    @Volatile private var request: PantheraRequest<IPantheraWorker>? = null
    @Volatile private var openedGen: String? = null
    @Volatile private var rate = 22050
    fun loadedGen(): String? = openedGen
    fun isOpen(): Boolean = worker != null

    fun <T> withSynthesis(block: () -> T): T = synchronized(lock) {
        val owned = PantheraRequest<IPantheraWorker>(
            retire = PantheraWorkers::retire,
            canReuse = { api, allowGrace ->
                val at = android.os.SystemClock.elapsedRealtime()
                var complete = api.renderComplete()
                while (!complete && allowGrace && android.os.SystemClock.elapsedRealtime() - at < COMPLETION_GRACE_MS) {
                    Thread.sleep(2)
                    complete = api.renderComplete()
                }
                val nativeCleanup = !complete && !allowGrace
                if (nativeCleanup) complete = api.stopAndFinish(NATIVE_CLEANUP_MS)
                android.util.Log.i("PantheraEngine", "Cancelled renderer settled in " +
                    "${android.os.SystemClock.elapsedRealtime() - at} ms, grace=$allowGrace native=$nativeCleanup reused=$complete")
                complete
            },
            dispatchCancel = { cancellations.execute(it) })
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
                .also { if (it == 0) owned.started(text.size <= COMPLETION_GRACE_MAX_BYTES) }
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
