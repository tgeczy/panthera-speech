package com.pantheraspeech.tts

import android.app.Instrumentation
import android.app.Activity
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.media.AudioAttributes
import android.util.Log
import java.io.File
import java.security.MessageDigest
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** Device integration test: requires engine data and a successful Check Engine.
 * Uses the platform TTS client, including its Binder and synthesis callbacks.
 * Run with adb shell am instrument -w
 * com.pantheraspeech.tts.test/com.pantheraspeech.tts.EngineSmokeTest.
 */
class EngineSmokeTest : Instrumentation() {
    private var nativeGen: String? = null
    private var nativeVoice: String? = null
    private var nativeText = "Hello there."
    private var nativePhrasing = "leopard"
    private var nativeWpm = 180
    private var audioOnly = false
    private var latency: String? = null
    private var zipImport: String? = null
    private var zipName = "tiger.zip"
    override fun onCreate(arguments: Bundle?) {
        zipImport = arguments?.getString("zipImport")
        zipName = arguments?.getString("zipName") ?: zipName
        nativeGen = arguments?.getString("nativeGeneration")
        nativeVoice = arguments?.getString("nativeVoice")
        nativeText = arguments?.getString("nativeText") ?: nativeText
        nativePhrasing = arguments?.getString("nativePhrasing") ?: nativePhrasing
        nativeWpm = arguments?.getString("nativeWpm")?.toInt() ?: nativeWpm
        audioOnly = arguments?.getString("audioOnly") == "true"
        latency = arguments?.getString("latency")
        super.onCreate(arguments); start()
    }

    private fun sliderValue(slider: android.widget.SeekBar): String =
        if (android.os.Build.VERSION.SDK_INT >= 30) slider.stateDescription.toString()
        else slider.contentDescription.toString().substringAfter(", ")

    /** The settings page's radio groups, by the name each was built with. */
    private fun radios(activity: Activity, name: String): android.widget.RadioGroup? =
        descendants(activity.window.decorView).filterIsInstance<android.widget.RadioGroup>()
            .firstOrNull { it.tag == name }
    private fun checkedIndex(group: android.widget.RadioGroup): Int =
        (0 until group.childCount).indexOfFirst { (group.getChildAt(it) as android.widget.RadioButton).isChecked }
    /** Click an option the way a finger would, so the change counts as the user's. */
    private fun pick(group: android.widget.RadioGroup, index: Int) {
        (group.getChildAt(index) as android.widget.RadioButton).performClick()
    }

    private fun checkNativeGeneration(gen: String) {
        val result = Bundle()
        try {
            val root = File(targetContext.filesDir, "panthera-data/$gen")
            check(PantheraNative.nativeSetPhrasing(nativePhrasing) == 0)
            check(PantheraNative.nativeOpen(File(root, "MacinTalk").path,
                File(root, "SpeechDictionary.framework/Versions/A/SpeechDictionary").path) == 0)
            PantheraNative.nativeSetVolume(-1, gen)
            val voice = PantheraEngine.scanVoices(targetContext, gen)
                .first { it.name == (nativeVoice ?: if (gen == "lion") "Alex" else "Fred") }
            var reference: ShortArray? = null
            repeat(5) { i ->
                val pcm = PantheraNative.nativeRender(voice.dir, voice.creator, voice.voiceId,
                    PantheraText.bytes(nativeText), nativeWpm) ?: error("No PCM")
                check(pcm.size > 12000) { "Short render: ${pcm.size}" }
                val bytes = ByteArray(pcm.size * 2)
                pcm.forEachIndexed { j, v -> bytes[j*2] = v.toByte(); bytes[j*2+1] = (v.toInt() shr 8).toByte() }
                File(targetContext.filesDir, "native-$gen.pcm").writeBytes(bytes)
                result.putString("render$i", "${pcm.size} frames " + MessageDigest.getInstance("SHA-256")
                    .digest(bytes).joinToString("") { "%02x".format(it) })
                if (reference != null) check(pcm.contentEquals(reference)) { "Render $i differs: ${pcm.size} frames" }
                reference = pcm
            }
            finish(Activity.RESULT_OK, result)
        } catch (e: Throwable) {
            result.putString("failure", Log.getStackTraceString(e))
            finish(Activity.RESULT_CANCELED, result)
        }
    }

    /** Exact variants measured over forty resident native Windows renders per
     * input. Lion Fred is not sample-identical on every repeated utterance. */
    private fun checkLionReference(text: String, wav: ByteArray, wpm: Int = 180) {
        val variants = when ("$wpm:$text") {
            "180:Hello there." -> setOf(
                "0e29a652fe28b36857c525714cc53407f8f9eb04b266d29309831ebaf03c3fa7",
                "3a6cc0ccf89db6587bba3ffdca012e82dfff75f378d4284e6bccd1b9770eeeab")
            "300:Hello there." -> setOf(
                "017fb05711553f96e6b64a1e38e42e9c775fb8afd1efae3c6e86d49821838f59",
                "e93df555e12053bc3ffba34606e3c23cd5aef3acf3f3998ba8a7299341317559")
            "180:D R X I V" -> setOf(
                "b71c9a56edc4a924f33fac5c558cf66674c5d3876a46be50b0cecd3c9b00989e",
                "a6c6ec2960d1437597078638b73282dfd177a47c198afb102dd7859ac0712bdd")
            "180:The file is 5KB and 20ish." -> setOf(
                "381e168e1be3300ae5dee393216f62366f0f521f1a48e64f5362b627ae11b054",
                "b5fa6a79845480404a95ff58af91bf4092c5487a58b93347c6a2a6dd39c81380")
            "180:The fox jumped over the dog. Later it ran again." -> setOf(
                "3b18c4bd07bf426b004e352fd890d8aa77885246cfdd280e8c1730298c16b6b6",
                "4a91d1f82f769b1b643026a1bcdcbb74d8530f385dcd2e6fc42dbabe5dfd6846",
                "ace87f22942ea7899420a10b2169089227c4f7960fbe099e6c277210e9121f45",
                "0d22ade479d6de8e24aab4f5b7aae92704522a7309e26bed5ef9a4ddd8453983")
            else -> error("Missing native Lion reference: $wpm:$text")
        }
        val hash = MessageDigest.getInstance("SHA-256").digest(wav.copyOfRange(44, wav.size))
            .joinToString("") { "%02x".format(it) }
        check(hash in variants) { "Lion differs from native at $wpm wpm: $text ($hash)" }
    }

    private fun descendants(v: android.view.View): List<android.view.View> =
        listOf(v) + if (v is android.view.ViewGroup)
            (0 until v.childCount).flatMap { descendants(v.getChildAt(it)) } else emptyList()

    /** Same amplitude-normalized breath signature as tests/leopard/test_breath.py:
     * at least 300 ms of quiet turbulent audio, rather than inserted silence. */
    private fun breathCount(wav: ByteArray): Int {
        val samples = IntArray((wav.size - 44) / 2) { i ->
            ((wav[44 + i*2].toInt() and 255) or (wav[45 + i*2].toInt() shl 8)).toShort().toInt()
        }
        val peak = samples.maxOf { kotlin.math.abs(it) }
        if (peak == 0) return 0
        if (kotlin.math.abs(peak - 14000) > 700)
            samples.indices.forEach { samples[it] = (samples[it] * (14000.0 / peak)).toInt() }
        var start = -1
        var found = 0
        for (i in 0 until samples.size - 220 step 220) {
            val quiet = (i until i + 220).all { kotlin.math.abs(samples[it]) < 900 }
            if (quiet) { if (start < 0) start = i }
            else if (start >= 0) {
                val count = i - start
                if (count >= 22050 * 300 / 1000) {
                    val rms = kotlin.math.sqrt((start until i).sumOf { samples[it].toDouble() * samples[it] } / count)
                    val crossings = (start + 1 until i).count { (samples[it-1] < 0) != (samples[it] < 0) }
                    if (crossings * 22050.0 / count >= 2200 && rms > 20) found++
                }
                start = -1
            }
        }
        return found
    }

    private fun checkSettingsPersistence(activity: Activity) {
        val prefs = PantheraEngine.prefs(targetContext)
        val voices = PantheraEngine.allVoices(targetContext)
        val choices = PantheraEngine.availableGens(targetContext).take(2).map { gen ->
            voices.first { it.gen == gen && it.name == "Fred" }
        }
        fun select(voice: PantheraEngine.VoiceInfo) {
            val chosen = CountDownLatch(1)
            val listener = android.content.SharedPreferences.OnSharedPreferenceChangeListener { _, _ ->
                if (PantheraEngine.activeGen(targetContext) == voice.gen &&
                    (PantheraEngine.defaultVoiceName(targetContext, voice.gen) ?: voice.name) == voice.name) chosen.countDown()
            }
            prefs.registerOnSharedPreferenceChangeListener(listener)
            runOnMainSync {
                // The voice list lives in a dialog now (twenty-three voices
                // inline was twenty-three swipes to get past), so choose the
                // way the dialog does and check the button reports it.
                PantheraEngine.chooseVoice(targetContext, voice)
                if (PantheraEngine.activeGen(targetContext) == voice.gen &&
                    (PantheraEngine.defaultVoiceName(targetContext, voice.gen) ?: voice.name) == voice.name) chosen.countDown()
            }
            try { check(chosen.await(5, TimeUnit.SECONDS)) {
                "Picker did not choose ${voice.id}; active=${PantheraEngine.activeGen(targetContext)}"
            } } finally { prefs.unregisterOnSharedPreferenceChangeListener(listener) }
            waitForIdleSync()
            check(PantheraEngine.activeGen(targetContext) == voice.gen)
        }
        for ((i, voice) in choices.withIndex()) {
            prefs.edit().putInt(PantheraEngine.settingKey("rate_wpm", voice.gen), 221 + i*100)
                .putInt(PantheraEngine.settingKey("volume", voice.gen), 37 + i*25)
                .putInt(PantheraEngine.settingKey(PantheraEngine.PREF_INFLECTION, voice.gen), 35 + i*30)
                .putString(PantheraEngine.settingKey("number_style", voice.gen), if (i == 0) "off" else "words")
                .putBoolean(PantheraEngine.settingKey(PantheraEngine.PREF_COMMANDS, voice.gen), i == 0)
                .putBoolean(PantheraEngine.settingKey(PantheraEngine.PREF_ABBREVIATIONS, voice.gen), i != 0)
                .putString(PantheraEngine.settingKey(PantheraEngine.PREF_PHRASING, voice.gen), if (i == 0) "fewer" else "most")
                .commit()
            select(voice)
        }
        for (i in choices.indices.reversed()) {
            select(choices[i])
            runOnMainSync {
                val views = descendants(activity.window.decorView)
                val sliders = views.filterIsInstance<android.widget.SeekBar>()
                val phrasing = views.filterIsInstance<android.widget.RadioGroup>().first { it.tag == "Engine phrase breaks" }
                check(phrasing.isEnabled == PantheraEngine.supportsPhrasing(choices[i].gen))
                check((0 until phrasing.childCount).all { phrasing.getChildAt(it).isEnabled == phrasing.isEnabled })
                check(checkedIndex(phrasing) == if (i == 0) 1 else 3)
                val commands = views.filterIsInstance<android.widget.CheckBox>().first { it.text == "Accept embedded speech commands" }
                val abbreviations = views.filterIsInstance<android.widget.CheckBox>().first { it.text == "Expand abbreviations" }
                check(commands.isChecked == (i == 0))
                check(abbreviations.isChecked == (i != 0))
                for (box in listOf(commands, abbreviations)) {
                    val before = box.isChecked
                    box.performClick()
                    val now = PantheraEngine.settings(targetContext)
                    check((if (box === commands) now.acceptCommands else now.expandAbbreviations) == !before)
                    box.performClick()
                    check(box.isChecked == before)
                    check(box.createAccessibilityNodeInfo().isCheckable)
                }
                check(sliders[0].progress == 221 + i*100 - 80 + 1)
                check(sliderValue(sliders[1]) == "${37 + i*25} percent")
                check(PantheraEngine.settings(targetContext).volume == 37 + i*25)
                check(sliders[2].progress == 35 + i*30)
                check(PantheraEngine.settings(targetContext).inflection == 35 + i*30)
                check(checkedIndex(views.filterIsInstance<android.widget.RadioGroup>()
                    .first { it.tag == "How to read numbers" }) == if (i == 0) 2 else 1)
                val voiceButton = views.filterIsInstance<android.widget.Button>()
                    .first { it.text.startsWith("Voice: ") }
                check(voiceButton.text.toString().contains(choices[i].name)) {
                    "Voice button says '${voiceButton.text}', expected ${choices[i].name}"
                }
                prefs.edit().putInt(PantheraEngine.settingKey("volume", choices[i].gen), 38 + i*25).commit()
                check(descendants(activity.window.decorView).any { it === voiceButton })
                check(sliderValue(sliders[1]) == "${38 + i*25} percent")
            }
            if (PantheraEngine.supportsPhrasing(choices[i].gen)) {
                fun pickPhrase(position: Int) {
                    runOnMainSync { pick(radios(activity, "Engine phrase breaks")!!, position) }
                    waitForIdleSync()
                    check(PantheraEngine.settings(targetContext).phrasing == PantheraEngine.PHRASING_VALUES[position])
                }
                pickPhrase(2)
                pickPhrase(3)
            }
        }
        runOnMainSync {
            val views = descendants(activity.window.decorView)
            views.filterIsInstance<android.widget.EditText>().single().setText("Saved sample text.")
            val box = views.filterIsInstance<android.widget.CheckBox>().first { it.text == "Use selected voice in all apps" }
            if (!box.isChecked) box.performClick()
        }
        val monitor = addMonitor(SettingsActivity::class.java.name, null, false)
        runOnMainSync { activity.recreate() }
        val recreated = waitForMonitorWithTimeout(monitor, 10000) ?: error("Settings recreation timed out")
        removeMonitor(monitor)
        waitForIdleSync()
        runOnMainSync {
            val views = descendants(recreated.window.decorView)
            check(views.filterIsInstance<android.widget.EditText>().single().text.toString() == "Saved sample text.")
            check(views.filterIsInstance<android.widget.CheckBox>().first { it.text == "Use selected voice in all apps" }.isChecked)
            check(views.filterIsInstance<android.widget.Button>().first { it.text == "Engine settings" }.isSelected)
            check(views.filterIsInstance<android.widget.SeekBar>()[0].progress == 221 - 80 + 1)
            check(views.filterIsInstance<android.widget.CheckBox>().first { it.text == "Accept embedded speech commands" }.isChecked)
            check(!views.filterIsInstance<android.widget.CheckBox>().first { it.text == "Expand abbreviations" }.isChecked)
            prefs.edit().putBoolean("override_voice", false).commit()
            check(!views.filterIsInstance<android.widget.CheckBox>().first { it.text == "Use selected voice in all apps" }.isChecked)
            recreated.finish()
        }
    }

    override fun onStart() {
        if (zipImport == "pick") { ZipImportCheck.pick(this, zipName); return }
        if (zipImport == "true") { ZipImportCheck.run(this); return }
        if (latency == "lifecycle") { PantheraWorkerCheck.run(this); return }
        if (latency == "reuse") { PantheraWorkerCheck.runReuse(this); return }
        latency?.let { PantheraLatencyCheck.run(this, it); return }
        nativeGen?.let { checkNativeGeneration(it); return }
        var tts: TextToSpeech? = null
        var resultCode = Activity.RESULT_CANCELED
        val results = Bundle()
        val prefs = PantheraEngine.prefs(targetContext)
        val originalPreferences = prefs.all.toMap()
        val generation = PantheraEngine.activeGen(targetContext)
        val volumeKey = PantheraEngine.settingKey("volume", generation)
        val rateKey = PantheraEngine.settingKey("rate_wpm", generation)
        val baselineVolume = if (PantheraEngine.activeGen(targetContext) == "tiger") 100 else 50
        prefs.edit().putBoolean("override_voice", false).putInt(volumeKey, baselineVolume)
            .putInt(rateKey, 0).putString("voice_filter", "all").commit()
        try {
            // Generated by the independent desktop Python abbreviation rules;
            // these are text fixtures only, with no engine files or recordings.
            val cases = org.json.JSONArray(context.assets.open("abbreviation-oracle.json").bufferedReader().use { it.readText() })
            for (i in 0 until cases.length()) {
                val c = cases.getJSONObject(i)
                check(SpeechTextOptions.abbreviations(c.getString("text"), c.getBoolean("expand")) == c.getString("expected")) {
                    "Abbreviation oracle case $i: ${c.getString("text")}"
                }
            }
            check(SpeechTextOptions.prepare("[[rate 200]]Dr. Kirk", false, true, "tiger") == "Doctor Kirk")
            check(SpeechTextOptions.prepare("[ [ rate 200 ] ]DR", true, false, "tiger") == "[[ rate 200 ]]D R")
            check(SpeechTextOptions.prepare("[[inpt PHON]]DR", true, false, "lion") == "D R")
            check(SpeechTextOptions.prepare("[[cmnt Dr. XIV]]Dr. Kirk", true, false, "leopard") == "[[cmnt Dr. XIV]]D R. Kirk")
            results.putString("textRules", "${cases.length()} desktop abbreviation cases passed")
            if (!audioOnly) {
                val keyguard = targetContext.getSystemService(android.app.KeyguardManager::class.java)
                check(!keyguard.isKeyguardLocked) { "Unlock the device before running the settings UI checks" }
                val activity = startActivitySync(android.content.Intent(targetContext,
                    SettingsActivity::class.java).addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK))
                runOnMainSync {
                    fun descendants(v: android.view.View): List<android.view.View> =
                        listOf(v) + if (v is android.view.ViewGroup)
                            (0 until v.childCount).flatMap { descendants(v.getChildAt(it)) } else emptyList()
                    val views = descendants(activity.window.decorView)
                    views.filterIsInstance<android.widget.Button>().first { it.text == "Engine settings" }.performClick()
                    val sliders = views.filterIsInstance<android.widget.SeekBar>()
                    val sample = views.filterIsInstance<android.widget.EditText>().single()
                    check(sample.contentDescription.isNullOrEmpty()) {
                        "The sample's associated label must not replace its editable text"
                    }
                    for (control in views.filter { it is android.widget.SeekBar || it is android.widget.RadioGroup || it is android.widget.EditText }) {
                        check(control.id != android.view.View.NO_ID)
                        check(views.filterIsInstance<android.widget.TextView>().any { it !== control && it.labelFor == control.id }) {
                            "Control has no associated label: ${control.contentDescription ?: control.tag}"
                        }
                    }
                    check(sliders.size == 3)
                    for (slider in sliders) {
                        check(!slider.contentDescription.isNullOrBlank())
                        slider.requestFocus()
                        fun key(code: Int) {
                            slider.dispatchKeyEvent(android.view.KeyEvent(android.view.KeyEvent.ACTION_DOWN, code))
                            slider.dispatchKeyEvent(android.view.KeyEvent(android.view.KeyEvent.ACTION_UP, code))
                        }
                        key(android.view.KeyEvent.KEYCODE_MOVE_END); check(slider.progress == slider.max)
                        key(android.view.KeyEvent.KEYCODE_MOVE_HOME); check(slider.progress == 0)
                        key(android.view.KeyEvent.KEYCODE_DPAD_RIGHT); check(slider.progress == 1)
                        key(android.view.KeyEvent.KEYCODE_DPAD_LEFT); check(slider.progress == 0)
                        if (android.os.Build.VERSION.SDK_INT >= 30) check(!slider.stateDescription.isNullOrBlank())
                    }
                    check(prefs.getInt(volumeKey, -2) == -1)
                    val volume = sliders[1]
                    check(sliderValue(volume) == "System default")
                    repeat(3) {
                        volume.dispatchKeyEvent(android.view.KeyEvent(android.view.KeyEvent.ACTION_DOWN,
                            android.view.KeyEvent.KEYCODE_DPAD_RIGHT))
                    }
                    check(prefs.getInt(volumeKey, -2) == 10)
                    check(prefs.getInt(rateKey, -1) == 0)
                }
                checkSettingsPersistence(activity)
            }
            for (gen in PantheraEngine.availableGens(targetContext)) {
                prefs.edit().putInt(PantheraEngine.settingKey("rate_wpm", gen), 0)
                    .putInt(PantheraEngine.settingKey(PantheraEngine.PREF_INFLECTION, gen), 50)
                    .putBoolean(PantheraEngine.settingKey(PantheraEngine.PREF_COMMANDS, gen), false)
                    .putBoolean(PantheraEngine.settingKey(PantheraEngine.PREF_ABBREVIATIONS, gen), true)
                    .putString(PantheraEngine.settingKey(PantheraEngine.PREF_PHRASING, gen), "leopard")
                    .putString(PantheraEngine.settingKey("number_style", gen), "fix").commit()
            }
            prefs.edit().putString("engine_generation", generation).commit()
            prefs.edit().putInt(volumeKey, baselineVolume)
                .putInt(rateKey, 0).commit()
            results.putString("sliders", if (audioOnly) "Skipped: audio-only run" else "PASS: arrows, Home/End, labels, per-generation settings, recreation, sample text, live refresh")
            val ready = CountDownLatch(1)
            var init = TextToSpeech.ERROR
            runOnMainSync {
                tts = TextToSpeech(targetContext, { status ->
                    init = status; ready.countDown()
                }, targetContext.packageName)
            }
            check(ready.await(30, TimeUnit.SECONDS)) { "TTS initialization timed out" }
            check(init == TextToSpeech.SUCCESS) { "TTS initialization failed: $init" }
            val client = tts!!
            check(client.isLanguageAvailable(Locale.US) == TextToSpeech.LANG_COUNTRY_AVAILABLE)
            check(client.setLanguage(Locale.US) >= 0)
            // The public client survives generation changes; each worker owns one engine.
            val activeGen = PantheraEngine.activeGen(targetContext)
            val genVoices = client.voices.filter {
                it.name.startsWith("panthera-$activeGen-")
            }
            check(genVoices.isNotEmpty()) {
                "no $activeGen voices in the platform list: " +
                    client.voices.joinToString { it.name }
            }
            val fred = genVoices.firstOrNull { it.name.endsWith("-fred") }
                ?: error("Fred missing from $activeGen: " +
                         genVoices.joinToString { it.name })
            Log.i("PantheraTest", "generation under test: $activeGen " +
                "(${genVoices.size} of ${client.voices.size} voices listed)")
            check(client.setVoice(fred) == TextToSpeech.SUCCESS)
            // Pin the rate: otherwise the system TTS preference changes the oracle.
            // 215% maps to 387 wpm and renders just 4032 frames on desktop too.
            check(client.setSpeechRate(1.0f) == TextToSpeech.SUCCESS)
            results.putInt("voices", client.voices.size)
            Log.i("PantheraTest", "initialized: ${client.voices.size} voices, Fred selected")

            fun utterance(id: String, action: () -> Int) {
                val done = CountDownLatch(1)
                var failure: String? = null
                client.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                    override fun onStart(utteranceId: String) { Log.i("PantheraTest", "start $utteranceId") }
                    override fun onDone(utteranceId: String) { if (utteranceId == id) done.countDown() }
                    override fun onError(utteranceId: String) {
                        if (utteranceId == id) { failure = "synthesis failed"; done.countDown() }
                    }
                    override fun onError(utteranceId: String, code: Int) {
                        if (utteranceId == id) { failure = "synthesis failed: $code"; done.countDown() }
                    }
                })
                check(action() == TextToSpeech.SUCCESS) { "request rejected: $id" }
                check(done.await(35, TimeUnit.SECONDS)) { "completion timed out: $id" }
                check(failure == null) { "$id: $failure" }
                Log.i("PantheraTest", "done $id")
            }

            // Canonical user-supplied Tiger/Leopard data, "Hello there.",
            // 180 wpm, mono PCM16 at 22050 Hz. These are desktop references,
            // not hashes learned from the implementation under test.
            fun checkReference(voiceName: String, wav: ByteArray) {
                val expectedFrames = when (voiceName) {
                    "panthera-tiger-fred" -> 15792
                    "panthera-tiger-vicki" -> 15713
                    "panthera-leopard-fred", "panthera-snowleopard-fred", "panthera-lion-fred" -> 17360
                    "panthera-leopard-vicki" -> 17887
                    "panthera-leopard-alex" -> 17851
                    "panthera-lion-alex" -> 19387
                    "panthera-lion-vicki" -> 19517
                    "panthera-snowleopard-vicki" -> 19610
                    "panthera-snowleopard-alex" -> 19450
                    else -> return
                }
                check(wav.size == 44 + expectedFrames * 2) {
                    "$voiceName: expected $expectedFrames frames at 180 wpm, got ${(wav.size - 44) / 2}"
                }
                val expectedHash = when (voiceName) {
                    "panthera-tiger-fred" -> "cef98214a9eb7c7052053619f027c73badfbf251c016313be31d6091278d8b91"
                    "panthera-lion-fred" -> "0e29a652fe28b36857c525714cc53407f8f9eb04b266d29309831ebaf03c3fa7"
                    "panthera-leopard-fred", "panthera-snowleopard-fred" -> "ec4be821f742fcd8facd6d215ce4443d01c55ac0dc983d482574c31cfb63c761"
                    else -> return // AAC backends can differ by a PCM rounding unit.
                }
                val digest = MessageDigest.getInstance("SHA-256")
                digest.update(wav, 44, wav.size - 44)
                val actual = digest.digest().joinToString("") { "%02x".format(it.toInt() and 255) }
                if (voiceName == "panthera-lion-fred") checkLionReference("Hello there.", wav)
                else check(actual == expectedHash) { "$voiceName differs from desktop PCM: $actual" }
            }

            var firstWav: ByteArray? = null
            for (i in 1..2) {
                val file = File(targetContext.filesDir, "framework-fred-$i.wav")
                utterance("file-$i") {
                    client.synthesizeToFile("Hello there.", Bundle(), file, "file-$i")
                }
                val bytes = file.readBytes()
                check(bytes.size > 2048) { "empty or very short WAV: ${bytes.size}" }
                // At the explicitly selected normal rate (180 wpm), this
                // sentence must exceed 0.45 s. A bound without a pinned rate
                // mistakes the user's fast speech preference for a port bug.
                check(bytes.size > 20000) {
                    "far too short for \"Hello there.\": ${bytes.size} bytes " +
                    "at the explicitly selected 180 wpm"
                }
                check(String(bytes, 0, 4) == "RIFF")
                checkReference(fred.name, bytes)
                var peak = 0
                for (offset in 44 until bytes.size - 1 step 2) {
                    val sample = ((bytes[offset].toInt() and 255) or
                        (bytes[offset + 1].toInt() shl 8)).toShort().toInt()
                    peak = maxOf(peak, kotlin.math.abs(sample))
                }
                check(peak > 100) { "silent WAV: peak=$peak" }
                if (firstWav == null) firstWav = bytes
                else check(firstWav.contentEquals(bytes)) { "Repeated synthesis changed PCM" }
                results.putInt("wav${i}Bytes", bytes.size)
                results.putInt("wav${i}Peak", peak)
                Log.i("PantheraTest", "file $i: ${bytes.size} bytes, peak=$peak")
            }
            // Emoji and encoding, checked against each other rather than by ear.
            //
            // TalkBack hands over emoji code points, and MacRoman -- a 1984
            // Western European encoding -- has none of them, so without the
            // describing pass they become the space that anything unspellable
            // becomes and the user hears nothing where the emoji was. The oracle
            // is exact: if the pass works, an emoji renders to the same bytes as
            // the words it stands for.
            fun pcmOf(name: String, text: String): ByteArray {
                val file = File(targetContext.filesDir, "$name.wav")
                utterance(name) { client.synthesizeToFile(text, Bundle(), file, name) }
                return file.readBytes()
            }
            fun energy(wav: ByteArray): Double {
                var sum = 0.0
                for (o in 44 until wav.size - 1 step 2) {
                    val v = ((wav[o].toInt() and 255) or (wav[o+1].toInt() shl 8)).toShort().toInt()
                    sum += v.toDouble() * v
                }
                return sum
            }
            prefs.edit().putInt(volumeKey, baselineVolume / 2).commit()
            val quiet = pcmOf("volume-half", "Hello there.")
            prefs.edit().putInt(volumeKey, 0).commit()
            val mute = pcmOf("volume-mute", "Hello there.")
            prefs.edit().putInt(volumeKey, baselineVolume).commit()
            val restored = pcmOf("volume-restored", "Hello there.")
            check(restored.contentEquals(firstWav!!)) { "Volume did not recover after mute" }
            check(quiet.size == restored.size && mute.size == restored.size)
            check(energy(mute) == 0.0) { "Mute produced audible samples" }
            val ratio = energy(quiet) / energy(restored)
            check(ratio in 0.15..0.35) { "Half-volume energy ratio: $ratio" }
            prefs.edit().putInt(volumeKey, PantheraEngine.defaultVolume(generation)).commit()
            val normalVolume = pcmOf("volume-normal", "Hello there.")
            prefs.edit().putInt(volumeKey, PantheraEngine.VOLUME_SYSTEM_DEFAULT).commit()
            check(pcmOf("volume-system-default", "Hello there.").contentEquals(normalVolume)) {
                "System default changed the engine's normal PCM"
            }
            prefs.edit().putInt(volumeKey, baselineVolume).commit()
            val info = PantheraEngine.voiceById(targetContext, fred.name)!!
            val preview = PantheraEngine.render(targetContext, info, "Hello there.", 180)!!
            check(preview.size * 2 == restored.size - 44)
            check(preview.indices.all { i ->
                val o = 44 + i*2
                preview[i] == ((restored[o].toInt() and 255) or (restored[o+1].toInt() shl 8)).toShort()
            }) { "Preview and service disagree on volume" }
            results.putString("volume", "PASS: half, mute, restore, preview/service identity")
            val emoji = pcmOf("emoji", "A 👋 here.")
            val spelled = pcmOf("spelled", "A waving hand here.")
            check(emoji.contentEquals(spelled)) {
                "emoji did not speak as its name: ${emoji.size} vs ${spelled.size} bytes"
            }
            results.putInt("emojiBytes", emoji.size)
            Log.i("PantheraTest", "emoji speaks as its name: ${emoji.size} bytes")

            // The same trick for the MacRoman fold. A typographic apostrophe is
            // an apostrophe; sent as itself, the engine's front end reads 0xD5
            // as a quotation mark and breaks the phrase there.
            val curly = pcmOf("curly", "Canopy’s investments.")
            val straight = pcmOf("straight", "Canopy's investments.")
            check(curly.contentEquals(straight)) {
                "curly apostrophe did not fold: ${curly.size} vs ${straight.size} bytes"
            }
            Log.i("PantheraTest", "curly apostrophe folds to straight")

            // Numbers, checked the same way and for the same reason.
            //
            // From seven digits up the engine spells a number out one digit at
            // a time, and the repair it suggests is already in its own
            // behaviour: it reads "1,234,567" correctly. So the rule groups
            // the digits -- and the oracle is exact, because if the rule works
            // the two spellings must render to the same bytes.
            //
            // Android had no number handling at all until the rules moved into
            // the host, so this is the check that the move reached the phone.
            val plain = pcmOf("longnum", "1234567")
            val grouped = pcmOf("groupednum", "1,234,567")
            check(plain.contentEquals(grouped)) {
                "1234567 did not group: ${plain.size} vs ${grouped.size} bytes"
            }
            Log.i("PantheraTest", "seven digits group: ${plain.size} bytes")

            // The AAC voices, whichever of them this device has.
            //
            // Vicki and Alex share the `meow` engine, whose sample bank is AAC
            // rather than PCM, so these are the only voices that exercise the
            // decoder the device supplies -- everything else here is Fred, who
            // never touches one. A silent WAV is the failure that matters: a
            // decoder returning nothing looks exactly like success from here.
            for (want in listOf("vicki", "alex")) {
                val voice = genVoices.firstOrNull { it.name.endsWith("-$want") }
                if (voice == null) {
                    Log.i("PantheraTest", "no $want on this device")
                    continue
                }
                check(client.setVoice(voice) == TextToSpeech.SUCCESS)
                val file = File(targetContext.filesDir, "$want.wav")
                utterance(want) {
                    client.synthesizeToFile("Hello there.", Bundle(), file, want)
                }
                val bytes = file.readBytes()
                var peak = 0
                for (o in 44 until bytes.size - 1 step 2) {
                    val sample = ((bytes[o].toInt() and 255) or (bytes[o + 1].toInt() shl 8)).toShort()
                    peak = maxOf(peak, kotlin.math.abs(sample.toInt()))
                }
                check(bytes.size > 2048) { "$want produced almost nothing: ${bytes.size} bytes" }
                check(peak > 100) { "$want decoded to silence: peak=$peak (AAC decoder?)" }
                checkReference(voice.name, bytes)
                results.putInt("${want}Bytes", bytes.size)
                results.putInt("${want}Peak", peak)
                Log.i("PantheraTest", "$want through AAC: ${bytes.size} bytes, peak=$peak")
                check(client.setVoice(fred) == TextToSpeech.SUCCESS)
            }

            // A session, not an utterance.
            //
            // The guest arena used to be a bump allocator whose free was a
            // no-op, on the reasoning that one utterance never approaches its
            // 64 MB. One does not; a session of them does, and Alex allocates
            // enough per utterance to get there in about thirty seconds of real
            // reading -- malloc then answers null, the engine does not ask, and
            // memcpy writes to address zero. Nothing that renders a single
            // utterance can see that, which is why it reached a wrist before it
            // reached a test.
            run {
                val soakVoice = genVoices.firstOrNull { it.name.endsWith("-alex") }
                    ?: genVoices.firstOrNull { it.name.endsWith("-vicki") }
                    ?: fred
                check(client.setVoice(soakVoice) == TextToSpeech.SUCCESS)
                val file = File(targetContext.filesDir, "soak.wav")
                for (i in 1..24) {
                    utterance("soak-$i") {
                        client.synthesizeToFile(
                            "Licensed under the Apache License, Version $i.0, " +
                            "you may not use this file except in compliance.",
                            Bundle(), file, "soak-$i")
                    }
                }
                val bytes = file.readBytes()
                check(bytes.size > 2048) { "soak produced almost nothing" }
                results.putString("soak", "24 utterances on ${soakVoice.name}")
                Log.i("PantheraTest", "soak: 24 utterances on ${soakVoice.name} survived")
                check(client.setVoice(fred) == TextToSpeech.SUCCESS)
            }

            // Keep the same TextToSpeech client and public service alive across engines.
            for (gen in PantheraEngine.availableGens(targetContext)) {
                val voice = client.voices.firstOrNull { it.name == "panthera-$gen-fred" } ?: continue
                prefs.edit().putInt(PantheraEngine.settingKey("volume", gen), if (gen == "tiger") 100 else 50)
                    .putInt(PantheraEngine.settingKey("rate_wpm", gen), 0).commit()
                check(client.setVoice(voice) == TextToSpeech.SUCCESS)
                val wav = pcmOf("switch-$gen", "Hello there.")
                checkReference(voice.name, wav)
                val genRate = PantheraEngine.settingKey("rate_wpm", gen)
                val genVolume = PantheraEngine.settingKey("volume", gen)
                val genCommands = PantheraEngine.settingKey(PantheraEngine.PREF_COMMANDS, gen)
                val genAbbreviations = PantheraEngine.settingKey(PantheraEngine.PREF_ABBREVIATIONS, gen)
                checkReference(voice.name, pcmOf("$gen-commands-off", "[[rate 90]][[volm 0]]Hello there."))
                prefs.edit().putBoolean(genCommands, true).commit()
                val slowCommand = pcmOf("$gen-command-rate", "[[rate 90]]Hello there.")
                check(slowCommand.size > wav.size * 1.3) { "$gen embedded rate was ignored" }
                val paused = pcmOf("$gen-command-pause", "[[slnc 500]]Hello there.")
                check(paused.size > wav.size + 18000) { "$gen embedded pause was ignored" }
                check(energy(pcmOf("$gen-command-mute", "[[volm 0]]Hello there.")) == 0.0)
                prefs.edit().putBoolean(genCommands, false).commit()
                checkReference(voice.name, pcmOf("$gen-command-recovery", "Hello there."))
                // Lexical and dictionary halves, with off/on/off transitions on
                // the already-open worker. A setting change must affect audio.
                val expanded = pcmOf("$gen-abbrev-on", "The file is 5KB and 20ish.")
                val lexicalExpanded = pcmOf("$gen-lexical-on", "DR XIV")
                prefs.edit().putBoolean(genAbbreviations, false).commit()
                val unexpanded = pcmOf("$gen-abbrev-off", "The file is 5KB and 20ish.")
                // Native oracles show these dictionary rules in Leopard and
                // Snow Leopard. Tiger/Lion use different quantity handling.
                if (gen == "leopard" || gen == "snowleopard") check(!expanded.contentEquals(unexpanded)) {
                    "$gen dictionary abbreviation toggle had no effect"
                }
                val lexicalOff = pcmOf("$gen-lexical", "DR XIV")
                check(!lexicalOff.contentEquals(lexicalExpanded))
                val letters = pcmOf("$gen-letters", "D R X I V")
                if (gen == "lion") {
                    for (audio in listOf(lexicalOff, letters)) checkLionReference("D R X I V", audio)
                } else check(lexicalOff.contentEquals(letters))
                prefs.edit().putBoolean(genAbbreviations, true).commit()
                val restoredAbbreviations = pcmOf("$gen-abbrev-restored", "The file is 5KB and 20ish.")
                if (gen == "lion") {
                    for (audio in listOf(expanded, unexpanded, restoredAbbreviations))
                        checkLionReference("The file is 5KB and 20ish.", audio)
                } else check(restoredAbbreviations.contentEquals(expanded))
                if (PantheraEngine.supportsPhrasing(gen)) {
                    val phraseKey = PantheraEngine.settingKey(PantheraEngine.PREF_PHRASING, gen)
                    val phraseText = "The fox jumped over the dog. Later it ran again."
                    val originalPhrase = pcmOf("$gen-phrase-default", phraseText)
                    if (gen == "leopard") check(pcmOf("$gen-debug-default", "Restart with debug logging enabled.").size == 44 + 2 * 56896)
                    // Independently rendered with native Windows, all at 180
                    // wpm. Endpoints alone miss a real change: on Snow/Lion
                    // this sentence differs only at the middle position.
                    val frames = when (gen) {
                        "leopard" -> listOf(77168, 79632, 79632, 80304, 77168)
                        "snowleopard" -> listOf(78624, 78624, 80752, 78624, 78624)
                        else -> listOf(78470, 78470, 79702, 78470, 78470)
                    }
                    for ((i, style) in PantheraEngine.PHRASING_VALUES.withIndex()) {
                        prefs.edit().putString(phraseKey, style).commit()
                        val phrase = pcmOf("$gen-phrase-$style", phraseText)
                        check(phrase.size == 44 + 2 * frames[i]) {
                            "$gen $style phrase breaks differ from native: ${(phrase.size - 44) / 2} frames"
                        }
                        if (gen == "leopard" && style == "fewest")
                            check(pcmOf("$gen-debug-fewest", "Restart with debug logging enabled.").size == 44 + 2 * 50960)
                    }
                    prefs.edit().putString(phraseKey, "leopard").commit()
                    val restoredPhrase = pcmOf("$gen-phrase-restore", phraseText)
                    if (gen == "lion") {
                        for (audio in listOf(originalPhrase, restoredPhrase)) checkLionReference(phraseText, audio)
                    } else check(restoredPhrase.contentEquals(originalPhrase))
                }
                prefs.edit().putInt(genRate, 300).commit()
                val fast = pcmOf("$gen-rate-300", "Hello there.")
                check(fast.size > 2048 && fast.size < wav.size * 0.85) { "$gen ignored changed speech rate" }
                check(client.setSpeechRate(0.75f) == TextToSpeech.SUCCESS)
                val lockedRate = pcmOf("$gen-rate-locked", "Hello there.")
                if (gen == "lion") {
                    for (audio in listOf(fast, lockedRate)) checkLionReference("Hello there.", audio, 300)
                } else check(lockedRate.contentEquals(fast))
                check(client.setSpeechRate(1.0f) == TextToSpeech.SUCCESS)
                prefs.edit().putInt(genRate, 0).putInt(genVolume, 0).commit()
                check(energy(pcmOf("$gen-mute", "Hello there.")) == 0.0)
                prefs.edit().putInt(genVolume, if (gen == "tiger") 100 else 50).commit()
                checkReference(voice.name, pcmOf("$gen-settings-restored", "Hello there."))
                // Match the public setting against the measured engine command,
                // then prove that returning to default restores the voice itself.
                // AAC Alex gives a stable Lion oracle; Fred covers Tiger.
                val inflectionVoice = client.voices.firstOrNull { it.name == "panthera-$gen-alex" } ?: voice
                check(client.setVoice(inflectionVoice) == TextToSpeech.SUCCESS)
                val inflectionKey = PantheraEngine.settingKey(PantheraEngine.PREF_INFLECTION, gen)
                val inflectionDefault = pcmOf("$gen-inflection-default", "Hello there.")
                prefs.edit().putInt(inflectionKey, 20).commit()
                val adjustedInflection = pcmOf("$gen-inflection-20", "Hello there.")
                prefs.edit().putInt(inflectionKey, 50).commit()
                check(pcmOf("$gen-inflection-restored", "Hello there.").contentEquals(inflectionDefault)) {
                    "$gen inflection default changed the voice"
                }
                prefs.edit().putBoolean(genCommands, true).commit()
                check(pcmOf("$gen-inflection-command", "[[pmod 40]]Hello there.").contentEquals(adjustedInflection)) {
                    "$gen inflection disagrees with the engine command"
                }
                // Mark the channel adjusted, then return to default again so an
                // accepted embedded command cannot contaminate later oracles.
                prefs.edit().putInt(inflectionKey, 20).putBoolean(genCommands, false).commit()
                pcmOf("$gen-inflection-reset-arm", "Hello there.")
                prefs.edit().putInt(inflectionKey, 50).commit()
                check(pcmOf("$gen-inflection-reset", "Hello there.").contentEquals(inflectionDefault))
                check(client.setVoice(voice) == TextToSpeech.SUCCESS)
                // AAC plus repeated worker retirement, on the same bound client.
                val aac = client.voices.firstOrNull { it.name == "panthera-$gen-alex" }
                    ?: client.voices.firstOrNull { it.name == "panthera-$gen-vicki" }
                if (aac != null) {
                    check(client.setVoice(aac) == TextToSpeech.SUCCESS)
                    val reference = pcmOf("switch-$gen-aac", "Hello there.")
                    checkReference(aac.name, reference)
                    if (aac.name.endsWith("-alex")) {
                        // The existing NVDA breath regression paragraph. Keep
                        // it whole; its single breath lies inside the request.
                        val paragraph = "The US Chamber of Commerce had also warned Tuesday that higher tariffs " +
                            "would damage both economies, drive up costs for families, further " +
                            "disrupt critical supply chains, and risk the 13 million American jobs " +
                            "that depend on trade. Negotiators met again on Wednesday morning, but neither side would say " +
                            "whether a deal was close, and the deadline is now only days away."
                        check(PantheraText.pieces(paragraph, PantheraEngine.settings(targetContext, gen), gen).size == 1)
                        val breathing = pcmOf("$gen-alex-breath", paragraph)
                        // Native references disable the optional SQLite table,
                        // matching Android's app linker namespace.
                        val frames = when (gen) { "leopard" -> 508455; "snowleopard" -> 520641; else -> 527288 }
                        // Within a few frames, not exact.  When its worker falls behind
                        // the pacer at a phrase gap the engine restarts its player, and a
                        // restart made with audio already in hand leaves one zero sample
                        // of padding in the stream (traced on the S22 under Box64's
                        // interpreter, 2026-09-07).  The host now keeps every slice across
                        // those restarts -- it used to write the second restart's audio
                        // over the first's, losing about 228 frames of speech at a click --
                        // so what remains is a sample or two of silence, the engine's own
                        // and below anything audible.  A paragraph short by a slice, or
                        // missing its breath, still fails here.
                        val got = (breathing.size - 44) / 2
                        check(Math.abs(got - frames) <= 4) { "$gen paragraph differs from native: $got frames, expected $frames" }
                        check(breathCount(breathing) == 1) { "$gen lost Alex's paragraph breath" }
                    }
                    for (i in 1..24) {
                        check(pcmOf("$gen-aac-$i", "Hello there.").contentEquals(reference)) {
                            "$gen AAC render $i changed during the session"
                        }
                    }
                    // Do not wait for the paragraph to finish before recovering.
                    client.speak("This sentence will be interrupted. ".repeat(20),
                        TextToSpeech.QUEUE_FLUSH, Bundle(), "$gen-interrupt")
                    Thread.sleep(200)
                    val stopAt = android.os.SystemClock.elapsedRealtime()
                    client.stop()
                    check(pcmOf("$gen-after-stop", "Hello there.").contentEquals(reference))
                    val elapsed = android.os.SystemClock.elapsedRealtime() - stopAt
                    check(elapsed < 3000) { "$gen cancellation recovery took ${elapsed}ms" }
                    Log.i("PantheraTest", "$gen AAC: 24 exact repeats and cancellation recovery ${elapsed}ms")
                }
                check(client.setVoice(fred) == TextToSpeech.SUCCESS)
                check(pcmOf("return-$gen", "Hello there.").contentEquals(firstWav!!))
            }
            // The saved-voice override is optional and changes the very next request.
            val other = client.voices.firstOrNull { it.name.endsWith("-fred") && it.name != fred.name }
            if (other != null) {
                PantheraEngine.chooseVoice(targetContext, PantheraEngine.voiceById(targetContext, fred.name)!!)
                prefs.edit().putBoolean("override_voice", true).commit()
                check(client.setVoice(other) == TextToSpeech.SUCCESS)
                check(pcmOf("override-selected", "Hello there.").contentEquals(firstWav!!))
                prefs.edit().putBoolean("override_voice", false).commit()
                checkReference(other.name, pcmOf("override-disabled", "Hello there."))
                check(client.setVoice(fred) == TextToSpeech.SUCCESS)
            }
            results.putString("switching", "PASS: every installed generation and back, live rate/volume and voice override")

            client.setAudioAttributes(AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_ASSISTANCE_ACCESSIBILITY)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
            utterance("play") {
                client.speak("Hello there. This is Fred testing Android speech.",
                    TextToSpeech.QUEUE_FLUSH, Bundle(), "play")
            }
            // Interrupt a long utterance, then require the next one to complete.
            client.speak("This sentence will be interrupted. ".repeat(20),
                TextToSpeech.QUEUE_FLUSH, Bundle(), "interrupt")
            Thread.sleep(200)
            val stopAt = android.os.SystemClock.elapsedRealtime()
            client.stop()
            utterance("after-stop") {
                client.speak("Speech after stopping.", TextToSpeech.QUEUE_FLUSH, Bundle(), "after-stop")
            }
            val stopMs = android.os.SystemClock.elapsedRealtime() - stopAt
            Log.i("PantheraTest", "stop through next playback completion: ${stopMs}ms")
            results.putLong("stopThroughPlaybackMs", stopMs)
            results.putString("stream", "PASS: sliders, volume/mute/restore, preview/service identity, reference WAVs, playback, stop/restart")
            resultCode = Activity.RESULT_OK
        } catch (e: Throwable) {
            Log.e("PantheraTest", "FAILED", e)
            results.putString("stream", "FAIL: $e")

        } finally {
            tts?.shutdown()
            val restore = prefs.edit().clear()
            for ((key, value) in originalPreferences) when (value) {
                is String -> restore.putString(key, value)
                is Int -> restore.putInt(key, value)
                is Boolean -> restore.putBoolean(key, value)
                is Float -> restore.putFloat(key, value)
                is Long -> restore.putLong(key, value)
            }
            restore.commit()
        }
        finish(resultCode, results)
    }
}
