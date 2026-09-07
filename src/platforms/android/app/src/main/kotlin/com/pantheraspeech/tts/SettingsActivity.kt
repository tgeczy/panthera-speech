package com.pantheraspeech.tts

import android.app.Activity
import android.content.Intent
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.os.Bundle
import android.provider.Settings
import android.text.InputType
import android.util.Log
import java.io.File
import java.io.FileOutputStream
import android.text.method.ScrollingMovementMethod
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.SeekBar
import android.widget.Spinner
import android.widget.TextView
import android.widget.Toast

class SettingsActivity : Activity() {

    private lateinit var status: TextView
    private lateinit var voicesView: TextView
    private lateinit var testButton: Button
    private lateinit var sampleText: EditText
    private lateinit var setupTab: Button
    private lateinit var engineTab: Button
    private var voiceHolder: LinearLayout? = null
    private var pageHolders: List<View> = emptyList()
    private var pad = 0
    private var rateSlider: ValueSlider? = null
    private var volumeSlider: ValueSlider? = null
    private var inflectionSlider: ValueSlider? = null
    private var volumeLevels = PantheraEngine.volumeLevels(PantheraEngine.VOLUME_SYSTEM_DEFAULT)
    private var numberSpinner: Spinner? = null
    private var overrideVoice: android.widget.CheckBox? = null
    private var commandCheck: android.widget.CheckBox? = null
    private var abbreviationCheck: android.widget.CheckBox? = null
    private var phrasingSpinner: Spinner? = null
    private var phrasingHelp: TextView? = null
    private var voiceLabel: TextView? = null
    private var voiceSignature = ""
    private var voiceSpinner: Spinner? = null
    private var listedVoices: List<PantheraEngine.VoiceInfo> = emptyList()
    private var currentPage = 0
    private val preferenceListener = android.content.SharedPreferences.OnSharedPreferenceChangeListener { _, key ->
        if (key != "sample_text" && key != "settings_page")
            refreshSettings(key == PantheraEngine.PREF_GEN || key?.startsWith("default_voice") == true)
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        pad = (16 * resources.displayMetrics.density).toInt()

        val setupPage = column().also { buildSetup(it) }
        val enginePage = column().also { buildEngine(it) }

        // The tab strip is two ordinary buttons rather than a TabHost.
        //
        // A TabHost is more idiomatic, but this app also runs on a round watch
        // screen where a tab bar is nearly untappable -- and TalkBack reads a
        // selected/unselected button pair perfectly well, which is the audience
        // that matters most here.
        setupTab = Button(this).apply { text = "Setup"; setOnClickListener { show(0) } }
        engineTab = Button(this).apply {
            text = "Engine settings"; setOnClickListener { show(1) }
        }
        val wide = resources.configuration.screenWidthDp >= 340
        val tabs = LinearLayout(this).apply {
            orientation = if (wide) LinearLayout.HORIZONTAL else LinearLayout.VERTICAL
            addView(setupTab, tabParams(wide))
            addView(engineTab, tabParams(wide))
        }

        val pages = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(ScrollView(this@SettingsActivity).apply { addView(setupPage) })
            addView(ScrollView(this@SettingsActivity).apply { addView(enginePage) })
        }
        pageHolders = listOf(pages.getChildAt(0), pages.getChildAt(1))

        setContentView(LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(tabs)
            addView(pages)
        })
        show(savedInstanceState?.getInt("page") ?: PantheraEngine.prefs(this).getInt("settings_page", 0))
        PantheraEngine.prefs(this).registerOnSharedPreferenceChangeListener(preferenceListener)
        refresh()

        // Test hook: `am start ... --ez autospeak true` checks the engine and
        // speaks a sample, so a render can be triggered without navigating to
        // the button. Harmless in normal use (the extra is never set).
        if (intent?.getBooleanExtra("autospeak", false) == true) {
            Thread {
                PantheraEngine.checkEngine(this)
                runOnUiThread { refresh(); testSpeak() }
            }.start()
        }
    }

    override fun onResume() { super.onResume(); refresh(); refreshSettings(true) }
    override fun onDestroy() {
        PantheraEngine.prefs(this).unregisterOnSharedPreferenceChangeListener(preferenceListener)
        super.onDestroy()
    }
    override fun onSaveInstanceState(out: Bundle) {
        out.putInt("page", currentPage)
        super.onSaveInstanceState(out)
    }
    private fun refreshSettings(voiceSelection: Boolean = false) {
        overrideVoice?.isChecked = PantheraEngine.prefs(this).getBoolean("override_voice", false)
        val settings = PantheraEngine.settings(this)
        commandCheck?.isChecked = settings.acceptCommands
        abbreviationCheck?.isChecked = settings.expandAbbreviations
        val phrasesSupported = PantheraEngine.supportsPhrasing(PantheraEngine.activeGen(this))
        phrasingSpinner?.isEnabled = phrasesSupported
        phrasingSpinner?.setSelection(PantheraEngine.PHRASING_VALUES.indexOf(settings.phrasing), false)
        phrasingHelp?.text = if (phrasesSupported) "How often the engine inserts pauses within a sentence. Applies from the next request."
            else "Engine phrase breaks are available with Leopard and later. Tiger does not support this setting."
        rateSlider?.progress = wpmToProgress(settings.rate)
        volumeLevels = PantheraEngine.volumeLevels(settings.volume)
        volumeSlider?.max = volumeLevels.lastIndex
        volumeSlider?.progress = volumeLevels.indexOf(settings.volume)
        volumeSlider?.refreshValue()
        inflectionSlider?.progress = settings.inflection
        numberSpinner?.setSelection(listOf("fix", "words", "off").indexOf(settings.numbers), false)
        if (voiceSelection && listedVoices.isNotEmpty())
            voiceSpinner?.setSelection(preferredVoiceIndex(listedVoices), false)

    }

    private fun show(page: Int) {
        currentPage = page.coerceIn(0, 1)
        PantheraEngine.prefs(this).edit().putInt("settings_page", currentPage).apply()
        pageHolders.forEachIndexed { i, v ->
            v.visibility = if (i == page) View.VISIBLE else View.GONE
        }
        setupTab.isSelected = page == 0
        engineTab.isSelected = page == 1
        // Say which page this is, and say it out loud. A sighted user sees the
        // content swap; a screen-reader user whose focus is still on the tab
        // button hears nothing at all unless it is announced.
        setupTab.contentDescription =
            "Setup, tab 1 of 2" + if (page == 0) ", selected" else ""
        engineTab.contentDescription =
            "Engine settings, tab 2 of 2" + if (page == 1) ", selected" else ""
        val name = if (page == 0) "Setup" else "Engine settings"
        try { window.decorView.announceForAccessibility("$name page") }
        catch (e: Throwable) { /* announcing is a courtesy, never a failure */ }
    }

    // ---- page 1: setup -----------------------------------------------------

    private fun buildSetup(root: LinearLayout) {
        root.addView(TextView(this).apply {
            text = "Panthera Speech"; textSize = 26f; gravity = Gravity.CENTER
        })
        root.addView(body("Macintosh speech voices, running under emulation."))

        // The positioning, said once and plainly.
        //
        // Extraction lives on the desktop, where the extractor and the disk
        // images are. Without saying so, the first thing anybody asks is how to
        // point this at a DMG on their phone -- which it will never do, and
        // which is a fair thing to expect if nobody says otherwise.
        root.addView(body(
            "\nThis app is the companion to Panthera on your desktop. Extract " +
            "the engine data there, from your own copy of Mac OS X, then copy " +
            "the finished folder here. Nothing of Apple's ships with this app, " +
            "and extraction does not happen on a phone or a watch."))

        root.addView(heading("1.  Put engine data here"))
        root.addView(TextView(this).apply {
            textSize = 13f
            setTextIsSelectable(true)
            text = PantheraEngine.dataRoot(this@SettingsActivity).absolutePath
        })
        root.addView(body(
            "Inside it, one folder per engine generation — a “tiger” folder " +
            "holding MacinTalk, the SpeechDictionary framework, and a Voices " +
            "folder of .SpeechVoice bundles."))

        root.addView(heading("2.  Check the engine"))
        root.addView(Button(this).apply {
            text = "Check Engine"
            setOnClickListener { runCheck() }
        })
        status = body("Not checked yet.")
        status.movementMethod = ScrollingMovementMethod()
        root.addView(status)

        root.addView(heading("Voices found"))
        voicesView = body("—")
        root.addView(voicesView)

        root.addView(heading("3.  Try it"))
        // An edit field rather than a fixed sentence: the point of a preview is
        // to hear the voice say the kind of thing you are about to make it say
        // for hours, and that sentence is never the one that shipped.
        val sampleLabel = body("Text to speak").also { root.addView(it) }
        sampleText = EditText(this).apply {
            id = View.generateViewId()
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
            setText(PantheraEngine.prefs(this@SettingsActivity).getString("sample_text",
                "Hello there. This is Panthera Speech."))
            addTextChangedListener(object : android.text.TextWatcher {
                override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
                override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {
                    PantheraEngine.prefs(this@SettingsActivity).edit().putString("sample_text", s.toString()).apply()
                }
                override fun afterTextChanged(s: android.text.Editable?) {}
            })
            textSize = 15f
        }
        sampleLabel.labelFor = sampleText.id
        root.addView(sampleText)
        testButton = Button(this).apply {
            text = "Speak"
            isEnabled = false
            setOnClickListener { testSpeak() }
        }
        root.addView(testButton)

        root.addView(body(
            "\nThen pick Panthera Speech as your engine in the system's " +
            "Text-to-speech settings:"))
        root.addView(Button(this).apply {
            text = "Open Text-to-speech settings"
            setOnClickListener { openTtsSettings() }
        })
        root.addView(Button(this).apply {
            text = "Licenses and source"
            setOnClickListener {
                val notice = assets.open("DISTRIBUTION.txt").bufferedReader().use { it.readText() }
                android.app.AlertDialog.Builder(this@SettingsActivity)
                    .setTitle("Licenses and source").setMessage(notice)
                    .setPositiveButton("Close", null)
                    .setNeutralButton("GPLv2 license") { _, _ ->
                        val license = assets.open("GPL-2.0.txt").bufferedReader().use { it.readText() }
                        android.app.AlertDialog.Builder(this@SettingsActivity)
                            .setTitle("GNU General Public License version 2")
                            .setMessage(license).setPositiveButton("Close", null).show()
                    }.show()
            }
        })
    }

    // ---- page 2: engine settings -------------------------------------------

    private fun buildEngine(root: LinearLayout) {
        val p = PantheraEngine.prefs(this)

        voiceLabel = heading("Voice").also { root.addView(it) }
        root.addView(body("Choosing a voice also chooses its engine generation."))
        voiceHolder = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        root.addView(voiceHolder!!)
        rebuildVoices()
        root.addView(android.widget.CheckBox(this).apply {
            text = "Use selected voice in all apps"
            isChecked = p.getBoolean("override_voice", false)
            overrideVoice = this
            setOnCheckedChangeListener { _, checked ->
                if (checked != p.getBoolean("override_voice", false))
                    p.edit().putBoolean("override_voice", checked).apply()
            }
        })

        // Data that is present and cannot run. Saying so is the point: someone
        // who has copied Lion's folder across and then sees nothing would
        // reasonably conclude the copy failed.
        for (gen in PantheraEngine.presentButUnsupportedGens(this)) {
            PantheraEngine.unsupportedReason(gen)?.let { root.addView(body(it)) }
        }

        root.addView(heading("Rate"))
        root.addView(body(
            "By default this engine follows the rate in the system's own " +
            "text-to-speech screen. Setting a rate here overrides it — worth " +
            "doing when an app asks for a speed you did not choose."))
        val savedRate = PantheraEngine.settings(this).rate
        rateSlider = addSlider(root, "Speech rate", RATE_MAX - RATE_MIN + 1,
            wpmToProgress(savedRate), { rateText(progressToWpm(it)) }) {
            p.edit().putInt(PantheraEngine.settingKey(PantheraEngine.PREF_RATE, PantheraEngine.activeGen(this)), progressToWpm(it)).apply()
        }

        root.addView(heading("Volume"))
        root.addView(body("System default uses the engine's normal volume while Android " +
            "controls app and device volume. Choose 0 to mute, or adjust in 5 percent steps. " +
            "Leopard and later balance voice loudness; above 90 may distort."))
        val savedVolume = PantheraEngine.volume(this)
        volumeLevels = PantheraEngine.volumeLevels(savedVolume)
        volumeSlider = addSlider(root, "Engine volume", volumeLevels.lastIndex,
            volumeLevels.indexOf(savedVolume), { volumeText(volumeLevels[it]) }) {
            p.edit().putInt(PantheraEngine.settingKey(PantheraEngine.PREF_VOLUME,
                PantheraEngine.activeGen(this)), volumeLevels[it]).apply()
        }

        root.addView(heading("Inflection"))
        root.addView(body("Pitch variation within speech. 50 uses the voice's own default; " +
            "some voices respond less than others. Changes apply to the next request."))
        inflectionSlider = addSlider(root, "Inflection", 100, PantheraEngine.settings(this).inflection,
            { if (it == 50) "50 percent, engine default" else "$it percent" }) {
            p.edit().putInt(PantheraEngine.settingKey(PantheraEngine.PREF_INFLECTION,
                PantheraEngine.activeGen(this)), it).apply()
        }

        val phrasingLabel = heading("Engine phrase breaks").also { root.addView(it) }
        phrasingHelp = body("").also { root.addView(it) }
        phrasingSpinner = spinner(listOf("Fewest pauses", "Fewer pauses", "More pauses", "Most pauses", "Engine default"),
            PantheraEngine.PHRASING_VALUES.indexOf(PantheraEngine.settings(this).phrasing)) { i ->
            val gen = PantheraEngine.activeGen(this)
            val value = PantheraEngine.PHRASING_VALUES[i]
            if (PantheraEngine.supportsPhrasing(gen) && PantheraEngine.settings(this).phrasing != value)
                p.edit().putString(PantheraEngine.settingKey(PantheraEngine.PREF_PHRASING, gen), value).apply()
        }.also {
            it.contentDescription = "Engine phrase breaks"
            phrasingLabel.labelFor = it.id
            root.addView(it)
        }

        val numberLabel = heading("Numbers").also { root.addView(it) }
        root.addView(body(
            "From seven digits up, the engine reads a number one digit at a " +
            "time, and it drops the leading zero from a version like 0.7.3. " +
            "“Fix what is wrong” repairs those and leaves the rest to the " +
            "engine; “Read out in full” says every number in words."))
        val numberValues = listOf("fix", "words", "off")
        val numberNames = listOf(
            "Fix what is wrong (recommended)",
            "Read out in full",
            "Leave numbers alone")
        val current = PantheraEngine.settings(this).numbers
        numberSpinner = spinner(numberNames, numberValues.indexOf(current).coerceAtLeast(0)) { i ->
            if (PantheraEngine.settings(this).numbers != numberValues[i])
                p.edit().putString(PantheraEngine.settingKey(PantheraEngine.PREF_NUMBER_STYLE,
                PantheraEngine.activeGen(this)), numberValues[i]).apply()
        }.also {
            it.contentDescription = "How to read numbers"
            numberLabel.labelFor = it.id
            root.addView(it)
        }

        root.addView(heading("Text handling"))
        abbreviationCheck = preferenceCheck(root, "Expand abbreviations",
            PantheraEngine.PREF_ABBREVIATIONS, true)
        root.addView(body("Read abbreviations such as Dr. and XIV as words. Turn off " +
            "to spell their letters. Applies to the selected engine generation."))
        commandCheck = preferenceCheck(root, "Accept embedded speech commands",
            PantheraEngine.PREF_COMMANDS, false)
        root.addView(body("Allow commands in text to change speech, such as [[rate 200]] " +
            "or [[slnc 500]]. Otherwise command blocks are removed. " +
            "Lion ignores input-mode commands."))

    }

    private fun preferredVoiceIndex(voices: List<PantheraEngine.VoiceInfo>): Int {
        val gen = PantheraEngine.activeGen(this)
        val saved = PantheraEngine.defaultVoiceName(this, gen)
        return voices.indexOfFirst { it.gen == gen && it.name.equals(saved, true) }.takeIf { it >= 0 }
            ?: voices.indexOfFirst { it.gen == gen && it.name.equals("Fred", true) }.takeIf { it >= 0 }
            ?: voices.indexOfFirst { it.gen == gen }.coerceAtLeast(0)
    }

    private fun rebuildVoices() {
        val holder = voiceHolder ?: return
        holder.removeAllViews()
        val voices = PantheraEngine.allVoices(this)
        listedVoices = voices
        if (voices.isEmpty()) {
            holder.addView(body("No voices in the engine data folder."))
            return
        }
        // Only one generation is labelled when there is only one to tell apart:
        // "Fred (Tiger)" twenty-three times is noise to read and worse to hear.
        val single = PantheraEngine.availableGens(this).size < 2
        val names = voices.map { if (single) it.name else it.label }
        val at = preferredVoiceIndex(voices)
        voiceSpinner = spinner(names, at) { i ->
            val v = voices[i]
            if (v.gen != PantheraEngine.activeGen(this) ||
                v.name != PantheraEngine.defaultVoiceName(this, v.gen)) PantheraEngine.chooseVoice(this, v)
        }.also {
            it.contentDescription = "Voice"
            voiceLabel?.labelFor = it.id
            holder.addView(it)
        }
        holder.addView(body(
            "Used when an app does not name a voice itself. The sample on the " +
            "Setup page speaks with this one."))
    }

    // ---- small builders ----------------------------------------------------

    private fun column() = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(pad, pad, pad, pad)
    }
    private fun heading(t: String) = TextView(this).apply {
        text = t; textSize = 20f; setPadding(0, pad, 0, pad / 2)
        if (android.os.Build.VERSION.SDK_INT >= 28) isAccessibilityHeading = true
    }
    private fun body(t: String) = TextView(this).apply { text = t; textSize = 15f }
    private fun tabParams(wide: Boolean) =
        if (wide) LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        else LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)

    private fun spinner(items: List<String>, selected: Int, onPick: (Int) -> Unit) =
        Spinner(this).apply {
            id = View.generateViewId()
            adapter = ArrayAdapter(this@SettingsActivity,
                android.R.layout.simple_spinner_dropdown_item, items)
            setSelection(selected, false)
            onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
                private var previous = selected
                override fun onItemSelected(a: AdapterView<*>?, v: View?, pos: Int, id: Long) {
                    // setSelection fires this once as the view attaches, and a
                    // preference should change because somebody chose it, not
                    // because a screen was built.
                    if (pos == previous) return
                    previous = pos
                    onPick(pos)
                }
                override fun onNothingSelected(a: AdapterView<*>?) {}
            }
        }

    // View-based equivalent of TG Speechbox's AccessibleSlider: named value,
    // one-step arrows, Home/End, and the stock accessibility range actions.
    private class ValueSlider(context: android.content.Context, private val label: TextView,
                              private val name: String, private val describe: (Int) -> String) : SeekBar(context) {
        fun refreshValue() {
            label.text = describe(progress)
            if (android.os.Build.VERSION.SDK_INT >= 30) {
                contentDescription = name
                stateDescription = describe(progress)
            } else contentDescription = "$name, ${describe(progress)}"
        }
    }

    private fun volumeText(value: Int) = if (value < 0) "System default" else "$value percent"

    private fun addSlider(root: LinearLayout, name: String, limit: Int, value: Int,
            describe: (Int) -> String, save: (Int) -> Unit): ValueSlider {
        val label = body(describe(value)).apply { importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO }
        root.addView(label)
        val slider = ValueSlider(this, label, name, describe).apply {
            id = View.generateViewId()
            max = limit
            progress = value
            keyProgressIncrement = 1
            refreshValue()
            setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
                override fun onProgressChanged(s: SeekBar, v: Int, user: Boolean) {
                    refreshValue()
                    if (user) save(v)
                }
                override fun onStartTrackingTouch(s: SeekBar) {}
                override fun onStopTrackingTouch(s: SeekBar) {}
            })
            setOnKeyListener { _, key, event ->
                val target = when (key) {
                    android.view.KeyEvent.KEYCODE_DPAD_LEFT -> (progress - 1).coerceAtLeast(0)
                    android.view.KeyEvent.KEYCODE_DPAD_RIGHT -> (progress + 1).coerceAtMost(max)
                    android.view.KeyEvent.KEYCODE_MOVE_HOME -> 0
                    android.view.KeyEvent.KEYCODE_MOVE_END -> max
                    else -> return@setOnKeyListener false
                }
                if (event.action == android.view.KeyEvent.ACTION_DOWN) {
                    progress = target
                    save(target)
                }
                true
            }
        }
        label.labelFor = slider.id
        root.addView(slider)
        return slider
    }

    private fun preferenceCheck(root: LinearLayout, title: String, key: String,
                                fallback: Boolean) = android.widget.CheckBox(this).apply {
        id = View.generateViewId()
        text = title
        val prefs = PantheraEngine.prefs(this@SettingsActivity)
        fun currentKey() = PantheraEngine.settingKey(key, PantheraEngine.activeGen(this@SettingsActivity))
        fun value() = prefs.getBoolean(currentKey(), prefs.getBoolean(key, fallback))
        isChecked = value()
        // The stock CheckBox owns its label, focus and checked state. Avoid a
        // separate clickable parent or a second accessible label for the row.
        setOnCheckedChangeListener { _, checked ->
            if (checked != value()) prefs.edit().putBoolean(currentKey(), checked).apply()
        }
        root.addView(this)
    }

    private fun rateText(wpm: Int) =
        if (wpm <= 0) "Follow the system's rate" else "$wpm words per minute"
    private fun wpmToProgress(wpm: Int) =
        if (wpm <= 0) 0 else (wpm - RATE_MIN + 1).coerceIn(1, RATE_MAX - RATE_MIN + 1)
    private fun progressToWpm(p: Int) =
        if (p == 0) 0 else (p + RATE_MIN - 1).coerceIn(RATE_MIN, RATE_MAX)

    // ---- behaviour ---------------------------------------------------------

    private fun refresh() {
        val signature = PantheraEngine.allVoices(this).joinToString { it.id }
        if (voiceSignature != signature) { voiceSignature = signature; rebuildVoices() }
        val verified = PantheraEngine.verified(this)
        testButton.isEnabled = verified
        val voices = PantheraEngine.allVoices(this)
        voicesView.text = if (voices.isEmpty()) "—"
            else voices.joinToString("\n") { "•  ${it.label}" }
        if (verified) status.text = "Engine verified: ${voices.size} voice(s) ready."
    }

    private fun runCheck() {
        status.text = "Checking…"
        Thread {
            val ok = PantheraEngine.checkEngine(this)
            runOnUiThread {
                status.text = if (ok)
                    "Engine found and verified. You can now select Panthera " +
                    "Speech in Text-to-speech settings."
                else
                    "No engine data found.\nExpected an engine folder (e.g. " +
                    "“tiger”) with MacinTalk, SpeechDictionary and " +
                    "Voices under:\n${PantheraEngine.dataRoot(this).absolutePath}"
                refresh()
            }
        }.start()
    }

    private fun testSpeak() {
        val gen = PantheraEngine.activeGen(this)
        val voices = PantheraEngine.scanVoices(this, gen)
        val saved = PantheraEngine.defaultVoiceName(this, gen)
        // The voice the settings chose, so the preview previews the settings
        // rather than a hard-coded Fred.
        val voice = voices.firstOrNull { it.name.equals(saved, true) }
            ?: voices.firstOrNull { it.name.equals("Fred", true) }
            ?: voices.firstOrNull()
        if (voice == null) { toast("No voice to speak."); return }
        val text = sampleText.text.toString().ifBlank { "Hello there." }
        val systemRate = Settings.Secure.getInt(contentResolver, "tts_default_rate", 100)
        val wpm = PantheraEngine.settings(this, gen).wpm(systemRate)
        testButton.isEnabled = false
        status.text = "Rendering ${voice.label}…"
        Thread {
            val pcm = PantheraEngine.render(this, voice, text, wpm)
            val n = pcm?.size ?: 0
            val peak = if (n > 0) pcm!!.maxOf { kotlin.math.abs(it.toInt()) } else 0
            Log.i("Panthera", "render ${voice.name}: $n samples, peak $peak")
            if (n > 0) try {
                writeWav(File(filesDir, "last-render.wav"), pcm!!, PantheraEngine.sampleRate())
            } catch (e: Exception) { Log.w("Panthera", "wav dump failed", e) }
            runOnUiThread {
                testButton.isEnabled = true
                status.text = when {
                    n == 0 -> "The sample could not be spoken."
                    peak == 0 -> "The sample is silent. Check the volume setting."
                    else -> "Playing the sample with ${voice.label}."
                }
            }
            if (n > 0 && peak > 0) playPcm(pcm!!, PantheraEngine.sampleRate())
        }.start()
    }

    /** Jump to the system's Text-to-speech settings; fall back if unavailable. */
    private fun openTtsSettings() {
        val tries = listOf(
            Intent("com.android.settings.TTS_SETTINGS"),
            Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS),
            Intent(Settings.ACTION_SETTINGS),
        )
        for (i in tries) {
            try { i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK); startActivity(i); return }
            catch (e: Exception) { /* try the next */ }
        }
        toast("Couldn't open settings on this device.")
    }

    /** A 16-bit mono WAV, for pulling a rendered utterance off the device. */
    private fun writeWav(file: File, pcm: ShortArray, rate: Int) {
        val dataBytes = pcm.size * 2
        FileOutputStream(file).use { o ->
            fun i32(v: Int) = o.write(byteArrayOf(
                v.toByte(), (v shr 8).toByte(), (v shr 16).toByte(), (v shr 24).toByte()))
            fun i16(v: Int) = o.write(byteArrayOf(v.toByte(), (v shr 8).toByte()))
            o.write("RIFF".toByteArray()); i32(36 + dataBytes); o.write("WAVE".toByteArray())
            o.write("fmt ".toByteArray()); i32(16); i16(1); i16(1)
            i32(rate); i32(rate * 2); i16(2); i16(16)
            o.write("data".toByteArray()); i32(dataBytes)
            val b = ByteArray(dataBytes); var j = 0
            for (s in pcm) { b[j++] = s.toByte(); b[j++] = (s.toInt() shr 8).toByte() }
            o.write(b)
        }
    }

    private fun playPcm(pcm: ShortArray, rate: Int) {
        // MODE_STREAM, not MODE_STATIC: the streaming path (write after play,
        // blocking) is what the watch actually plays -- MODE_STATIC created a
        // track that routed to the speaker but never made a sound.  Accessibility
        // usage so it goes to the built-in speaker (media does not, on a watch).
        val minBuf = AudioTrack.getMinBufferSize(
            rate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val track = AudioTrack.Builder()
            .setAudioAttributes(
                AudioAttributes.Builder()
                    .setUsage(AudioAttributes.USAGE_ASSISTANCE_ACCESSIBILITY)
                    .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
            .setAudioFormat(
                AudioFormat.Builder()
                    .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                    .setSampleRate(rate)
                    .setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
            .setBufferSizeInBytes(maxOf(minBuf, rate))   // ~0.5 s of headroom
            .setTransferMode(AudioTrack.MODE_STREAM)
            .build()
        Log.i("Panthera", "AudioTrack state=${track.state} min=$minBuf")
        track.play()
        var off = 0
        while (off < pcm.size) {
            val w = track.write(pcm, off, pcm.size - off)   // blocks, pacing playback
            if (w <= 0) { Log.e("Panthera", "AudioTrack.write -> $w"); break }
            off += w
        }
        Log.i("Panthera", "wrote $off/${pcm.size} samples, playState=${track.playState}")
        // A successful write only queues audio. Wait for the playback head,
        // with a deadline, before releasing the remaining buffered speech.
        val deadline = android.os.SystemClock.elapsedRealtime() + off * 1000L / rate + 2000
        while (track.playbackHeadPosition.toLong() < off &&
            android.os.SystemClock.elapsedRealtime() < deadline) {
            try { Thread.sleep(20) } catch (e: InterruptedException) { break }
        }
        Log.i("Panthera", "playback head=${track.playbackHeadPosition}/$off routed=${track.routedDevice?.type}")
        try { track.stop() } finally { track.release() }
    }

    private fun toast(t: String) = Toast.makeText(this, t, Toast.LENGTH_SHORT).show()

    private companion object {
        // The engine's own range; PantheraEngine clamps to it as well.
        const val RATE_MIN = 80
        const val RATE_MAX = 500
    }
}
