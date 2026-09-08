package com.pantheraspeech.tts

import android.app.Activity
import android.content.Intent
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioTrack
import android.os.Build
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
import android.view.WindowInsets
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.ScrollView
import android.widget.SeekBar
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
    private var numberChoice: Choice? = null
    private var overrideVoice: android.widget.CheckBox? = null
    private var commandCheck: android.widget.CheckBox? = null
    private var abbreviationCheck: android.widget.CheckBox? = null
    private var phrasingChoice: Choice? = null
    private var phrasingHelp: TextView? = null
    private var voiceLabel: TextView? = null
    private var voiceSignature = ""
    private var voiceButton: Button? = null
    private var filterButton: Button? = null
    private var voiceFilter = FILTER_ALL
    private var listedVoices: List<PantheraEngine.VoiceInfo> = emptyList()
    private var currentPage = 0
    private val preferenceListener = android.content.SharedPreferences.OnSharedPreferenceChangeListener { _, key ->
        if (key != "sample_text" && key != "settings_page" && key != PREF_VOICE_FILTER)
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

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            addView(tabs)
            addView(pages)
        }

        // Android 15 draws every activity edge to edge whether it asked to or
        // not.  Without this the tab strip is laid out at y=0 -- underneath
        // the status bar, invisible and untappable -- and the last button on
        // the setup page disappears under the navigation bar.
        //
        // The instrumentation did not catch it and could not: it finds a view
        // by its text and clicks it through the accessibility API, which works
        // perfectly on a view behind the status bar.  The tabs were present,
        // correct and reachable by every measure a test has.  Found by eye.
        root.setOnApplyWindowInsetsListener { view, insets ->
            val bars = systemBarInsets(insets)
            view.setPadding(bars[0], bars[1], bars[2], bars[3])
            insets
        }

        setContentView(root)
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
        overrideVoice?.isChecked = PantheraEngine.prefs(this).getBoolean("override_voice", true)
        val settings = PantheraEngine.settings(this)
        commandCheck?.isChecked = settings.acceptCommands
        abbreviationCheck?.isChecked = settings.expandAbbreviations
        val phrasesSupported = PantheraEngine.supportsPhrasing(PantheraEngine.activeGen(this))
        phrasingChoice?.enableAll(phrasesSupported)
        phrasingChoice?.select(PantheraEngine.PHRASING_VALUES.indexOf(settings.phrasing))
        phrasingHelp?.text = if (phrasesSupported) "How often the engine inserts pauses within a sentence. Applies from the next request."
            else "Engine phrase breaks are available with Leopard and later. Tiger does not support this setting."
        rateSlider?.progress = wpmToProgress(settings.rate)
        volumeLevels = PantheraEngine.volumeLevels(settings.volume)
        volumeSlider?.max = volumeLevels.lastIndex
        volumeSlider?.progress = volumeLevels.indexOf(settings.volume)
        volumeSlider?.refreshValue()
        inflectionSlider?.progress = settings.inflection
        numberChoice?.select(listOf("fix", "words", "off").indexOf(settings.numbers))
        if (voiceSelection) refreshVoiceButton()

    }

    /** Left, top, right and bottom taken by the status and navigation bars.
     *
     * `WindowInsets.Type` arrived in API 30 and this app supports 26, so the
     * older accessors stay for the watch and anything else on an early
     * release.  Both report the same rectangle.
     */
    @Suppress("DEPRECATION")
    private fun systemBarInsets(insets: WindowInsets): IntArray =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val bars = insets.getInsets(WindowInsets.Type.systemBars())
            intArrayOf(bars.left, bars.top, bars.right, bars.bottom)
        } else {
            intArrayOf(insets.systemWindowInsetLeft, insets.systemWindowInsetTop,
                       insets.systemWindowInsetRight, insets.systemWindowInsetBottom)
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
        //
        // The selected state is the button's own (isSelected, above) and
        // TalkBack already says it. Putting the word in the description as
        // well made it say "selected" twice, which sounds like two different
        // things being true of one control.
        setupTab.contentDescription = "Setup, tab 1 of 2"
        engineTab.contentDescription = "Engine settings, tab 2 of 2"
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
            "Inside it, one folder per engine generation, named tiger, leopard, " +
            "snowleopard or lion. Copy the folder the desktop add-on extracted " +
            "exactly as it is: Speech, SpeechDictionary.framework and the rest. " +
            "On a PC, plug the phone in and use the file window; the folder " +
            "above is under Android, then data, then this app."))

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
                    .setNeutralButton("Full licenses") { _, _ ->
                        val files = assets.list("").orEmpty()
                            .filter { it.endsWith(".txt") && it != "DISTRIBUTION.txt" }.sorted()
                        android.app.AlertDialog.Builder(this@SettingsActivity)
                            .setTitle("Full licenses")
                            .setItems(files.map { it.removeSuffix(".txt").replace('-', ' ') }.toTypedArray()) { _, which ->
                                val license = assets.open(files[which]).bufferedReader().use { it.readText() }
                                android.app.AlertDialog.Builder(this@SettingsActivity)
                                    .setTitle(files[which].removeSuffix(".txt").replace('-', ' '))
                                    .setMessage(license).setPositiveButton("Close", null).show()
                            }.setNegativeButton("Close", null).show()
                    }.show()
            }
        })
    }

    // ---- page 2: engine settings -------------------------------------------

    private fun buildEngine(root: LinearLayout) {
        val p = PantheraEngine.prefs(this)

        voiceLabel = heading("Voice").also { root.addView(it) }
        root.addView(body("Choosing a voice also chooses its engine generation."))
        voiceFilter = p.getString(PREF_VOICE_FILTER, null) ?: PantheraEngine.activeGen(this)
        voiceHolder = LinearLayout(this).apply { orientation = LinearLayout.VERTICAL }
        root.addView(voiceHolder!!)
        rebuildVoices()
        // On by default. A screen reader never names a voice of its own: it
        // asks this engine for a default once, when it connects, and sends
        // that name with every request from then on. With this off, choosing
        // a voice here changed nothing until the engine was restarted -- the
        // setting looked broken, and to the person using it, it was.
        root.addView(android.widget.CheckBox(this).apply {
            text = "Use selected voice in all apps"
            isChecked = p.getBoolean("override_voice", true)
            overrideVoice = this
            setOnCheckedChangeListener { _, checked ->
                if (checked != p.getBoolean("override_voice", true))
                    p.edit().putBoolean("override_voice", checked).apply()
            }
        })
        root.addView(body(
            "Every app hears the voice chosen above, straight away. Turn this " +
            "off only for an app that picks a voice of its own."))

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
        phrasingChoice = choice("Engine phrase breaks",
            listOf("Fewest pauses", "Fewer pauses", "More pauses", "Most pauses", "Engine default"),
            PantheraEngine.PHRASING_VALUES.indexOf(PantheraEngine.settings(this).phrasing)) { i ->
            val gen = PantheraEngine.activeGen(this)
            val value = PantheraEngine.PHRASING_VALUES[i]
            if (PantheraEngine.supportsPhrasing(gen) && PantheraEngine.settings(this).phrasing != value)
                p.edit().putString(PantheraEngine.settingKey(PantheraEngine.PREF_PHRASING, gen), value).apply()
        }.also {
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
        numberChoice = choice("How to read numbers", numberNames,
            numberValues.indexOf(current).coerceAtLeast(0)) { i ->
            if (PantheraEngine.settings(this).numbers != numberValues[i])
                p.edit().putString(PantheraEngine.settingKey(PantheraEngine.PREF_NUMBER_STYLE,
                PantheraEngine.activeGen(this)), numberValues[i]).apply()
        }.also {
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

    /** The listed voice that is in use, or -1 when none of them is.
     *
     * -1 matters: with the list filtered to a generation other than the one
     * speaking, nothing is the voice in use, and a radio button that says
     * "checked" of a voice nobody is hearing is a lie the ear cannot catch. */
    private fun preferredVoiceIndex(voices: List<PantheraEngine.VoiceInfo>): Int {
        val gen = PantheraEngine.activeGen(this)
        val saved = PantheraEngine.defaultVoiceName(this, gen)
        return voices.indexOfFirst { it.gen == gen && it.name.equals(saved, true) }.takeIf { it >= 0 }
            ?: voices.indexOfFirst { it.gen == gen && it.name.equals("Fred", true) }.takeIf { it >= 0 }
            ?: voices.indexOfFirst { it.gen == gen }
    }

    /** The button says which voice is in use, so it has to be re-read whenever
     * the choice changes -- from the dialog, or from another screen. */
    private fun refreshVoiceButton() {
        val button = voiceButton ?: return
        val voices = listedVoices
        val labelled = voiceFilter == FILTER_ALL && PantheraEngine.availableGens(this).size > 1
        val at = preferredVoiceIndex(voices)
        val name = voices.getOrNull(at)?.let { if (labelled) it.label else it.name }
        button.text = "Voice: " + (name ?: "none")
    }

    private fun rebuildVoices() {
        val holder = voiceHolder ?: return
        holder.removeAllViews()
        val all = PantheraEngine.allVoices(this)
        if (all.isEmpty()) {
            listedVoices = all
            filterButton = null
            voiceButton = null
            holder.addView(body("No voices in the engine data folder."))
            return
        }
        val gens = PantheraEngine.availableGens(this)
        if (voiceFilter != FILTER_ALL && voiceFilter !in gens) voiceFilter = FILTER_ALL

        // Which generation's voices are listed. One button that opens a list,
        // not a column of radios: the engine is a filter on the choice below
        // it, and giving it the same shape as the choice itself made two
        // lists of radio buttons stacked on one screen, which is exactly as
        // confusing as it sounds. The button says what is set, so it needs no
        // separate label. With one generation present there is nothing to
        // filter and it is not shown.
        if (gens.size > 1) {
            val options = listOf(FILTER_ALL) + gens
            val names = listOf("All engines") + gens.map { PantheraEngine.genLabel(it) }
            filterButton = Button(this).apply {
                id = View.generateViewId()
                text = "Engine: ${names[options.indexOf(voiceFilter).coerceAtLeast(0)]}"
                setOnClickListener {
                    val at = options.indexOf(voiceFilter).coerceAtLeast(0)
                    android.app.AlertDialog.Builder(this@SettingsActivity)
                        .setTitle("Engine")
                        .setSingleChoiceItems(names.toTypedArray(), at) { dialog, which ->
                            dialog.dismiss()
                            if (options[which] != voiceFilter) {
                                voiceFilter = options[which]
                                PantheraEngine.prefs(this@SettingsActivity).edit()
                                    .putString(PREF_VOICE_FILTER, voiceFilter).apply()
                                rebuildVoices()
                            }
                        }
                        .setNegativeButton("Cancel", null)
                        .show()
                }
                holder.addView(this)
            }
        } else filterButton = null

        val voices = if (voiceFilter == FILTER_ALL) all else all.filter { it.gen == voiceFilter }
        listedVoices = voices
        // Generations are named only when more than one is in the list:
        // "Fred (Tiger)" twenty-three times is noise to read and worse to hear.
        val labelled = voiceFilter == FILTER_ALL && gens.size > 1
        val names = voices.map { if (labelled) it.label else it.name }

        // A button that opens the list, not the list itself. Tiger alone has
        // twenty-three voices, and inline they were twenty-three stops to
        // swipe past to reach the rate slider below -- with no way out but
        // through. In a dialog the whole choice is one stop, Back leaves it,
        // and the list scrolls on its own.
        voiceButton = Button(this).apply {
            id = View.generateViewId()
            val at = preferredVoiceIndex(voices)
            text = "Voice: " + (names.getOrNull(at) ?: "none")
            setOnClickListener {
                android.app.AlertDialog.Builder(this@SettingsActivity)
                    .setTitle("Voice")
                    .setSingleChoiceItems(names.toTypedArray(), preferredVoiceIndex(voices)) { dialog, which ->
                        dialog.dismiss()
                        val v = voices[which]
                        if (v.gen != PantheraEngine.activeGen(this@SettingsActivity) ||
                            v.name != PantheraEngine.defaultVoiceName(this@SettingsActivity, v.gen))
                            PantheraEngine.chooseVoice(this@SettingsActivity, v)
                        refreshVoiceButton()
                    }
                    .setNegativeButton("Cancel", null)
                    .show()
            }
            voiceLabel?.labelFor = id
            holder.addView(this)
        }
        holder.addView(body("The sample on the Setup page speaks with this one."))
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

    /** A set of radio buttons: one focus stop per option, and each option
     * says its own name and whether it is the one in use -- "Fred, radio
     * button, checked".
     *
     * These were Spinners. A Spinner's collapsed item is a CheckedTextView,
     * so exploring one by touch read a drop-down role and then "not checked"
     * for the very option that was chosen -- two roles for one control, and
     * the one state that matters said backwards. The stock RadioButton owns
     * its label, its focus and its checked state, and says each once. */
    private class Choice(context: android.content.Context) : RadioGroup(context) {
        private var quiet = false
        var onPick: ((Int) -> Unit)? = null

        /** The chosen option's index, or -1 when none is. */
        fun index(): Int = (0 until childCount).indexOfFirst { (getChildAt(it) as RadioButton).isChecked }

        /** Show an option as chosen without treating that as the user's doing:
         * a preference changes because somebody chose it, not because a screen
         * was built or refreshed. -1 clears the choice. */
        fun select(i: Int) {
            quiet = true
            try { if (i in 0 until childCount) check(getChildAt(i).id) else clearCheck() }
            finally { quiet = false }
        }

        /** RadioGroup.isEnabled does not reach its buttons; this does. */
        fun enableAll(enabled: Boolean) {
            isEnabled = enabled
            for (i in 0 until childCount) getChildAt(i).isEnabled = enabled
        }

        init {
            orientation = VERTICAL
            setOnCheckedChangeListener { _, checkedId ->
                if (quiet || checkedId == View.NO_ID) return@setOnCheckedChangeListener
                val i = (0 until childCount).indexOfFirst { getChildAt(it).id == checkedId }
                if (i >= 0) onPick?.invoke(i)
            }
        }
    }

    private fun choice(name: String, items: List<String>, selected: Int,
                       onPick: (Int) -> Unit) = Choice(this).apply {
        id = View.generateViewId()
        tag = name
        for (item in items) addView(RadioButton(this@SettingsActivity).apply {
            id = View.generateViewId()
            text = item
            textSize = 16f
        })
        select(selected)
        this.onPick = onPick
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
                    "No engine data found.\nExpected a generation folder (tiger, " +
                    "leopard, snowleopard or lion) holding the extracted Speech and " +
                    "SpeechDictionary.framework folders, under:\n" +
                    PantheraEngine.dataRoot(this).absolutePath
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
        // Which generation the voice list shows: a generation name, or all.
        const val PREF_VOICE_FILTER = "voice_filter"
        const val FILTER_ALL = "all"
    }
}
