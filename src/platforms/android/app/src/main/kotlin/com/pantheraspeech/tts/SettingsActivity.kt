// The app's window, in two pages: Setup and Engine settings.
//
// Shaped after TG Speechbox, which solved this already: a TTS engine has no UI
// of its own -- the system's screen offers a rate slider and a flat list of
// every voice on the device -- so the engine's own app has to be both the
// settings window and a place to hear what the settings did. That is why
// "Speak" lives here beside the controls rather than on a page of its own: a
// rate you can hear is a rate you can choose.
//
// **Two pages rather than one long scroll**, because they answer different
// questions. Setup is a first-run errand, done once: where does the data go,
// is it there, does it make a sound. Engine settings is where somebody
// returns, months later, to change how numbers are read.
//
// **Voice and generation are pickers here, not ninety-two entries in the
// system list.** Somebody who has extracted Tiger, Leopard and Lion has more
// than sixty voices between them, and the platform's list is a flat
// alphabetical column with no notion of which engine a voice belongs to.
// Choosing the engine first and the voice second is the shape the data already
// has.
//
// Nothing on either page is a control over something the host cannot do. This
// project has shipped a settings panel whose every tunable was inert, and it
// took a night to notice.
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
    private lateinit var rateLabel: TextView
    private var voiceHolder: LinearLayout? = null
    private var pageHolders: List<View> = emptyList()
    private var pad = 0

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
        show(0)
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

    private fun show(page: Int) {
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
        sampleText = EditText(this).apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_FLAG_MULTI_LINE
            setText("Hello there. This is Panthera Speech.")
            contentDescription = "Text to speak"
            textSize = 15f
        }
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
    }

    // ---- page 2: engine settings -------------------------------------------

    private fun buildEngine(root: LinearLayout) {
        val p = PantheraEngine.prefs(this)

        root.addView(heading("Engine"))
        val gens = PantheraEngine.availableGens(this)
        if (gens.isEmpty()) {
            root.addView(body(
                "No engine data found yet. Finish Setup first — these settings " +
                "describe an engine that has to be present to be configured."))
        } else {
            root.addView(body(
                "Which generation of Apple's engine speaks. Each has its own " +
                "voices, and one runs at a time."))
            val at = gens.indexOf(PantheraEngine.activeGen(this)).coerceAtLeast(0)
            root.addView(spinner(gens.map { prettyGen(it) }, at) { i ->
                p.edit().putString(PantheraEngine.PREF_GEN, gens[i]).apply()
                rebuildVoices()          // the voice list belongs to the generation
                refresh()
            })

            root.addView(heading("Voice"))
            voiceHolder = LinearLayout(this).apply {
                orientation = LinearLayout.VERTICAL
            }
            root.addView(voiceHolder!!)
            rebuildVoices()
        }

        root.addView(heading("Rate"))
        root.addView(body(
            "By default this engine follows the rate in the system's own " +
            "text-to-speech screen. Setting a rate here overrides it — worth " +
            "doing when an app asks for a speed you did not choose."))
        val savedRate = p.getInt(PantheraEngine.PREF_RATE, 0)
        rateLabel = body(rateText(savedRate))
        root.addView(rateLabel)
        root.addView(SeekBar(this).apply {
            max = RATE_MAX - RATE_MIN + 1        // slot 0 is "follow the system"
            progress = wpmToProgress(savedRate)
            contentDescription = "Speech rate"
            setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
                override fun onProgressChanged(s: SeekBar, v: Int, user: Boolean) {
                    val wpm = progressToWpm(v)
                    rateLabel.text = rateText(wpm)
                    if (user) p.edit().putInt(PantheraEngine.PREF_RATE, wpm).apply()
                }
                override fun onStartTrackingTouch(s: SeekBar) {}
                override fun onStopTrackingTouch(s: SeekBar) {}
            })
        })

        root.addView(heading("Numbers"))
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
        val current = p.getString(PantheraEngine.PREF_NUMBER_STYLE,
            PantheraEngine.NUMBER_STYLE_DEFAULT)
        root.addView(spinner(numberNames,
            numberValues.indexOf(current).coerceAtLeast(0)) { i ->
            p.edit().putString(PantheraEngine.PREF_NUMBER_STYLE, numberValues[i]).apply()
        })
    }

    private fun rebuildVoices() {
        val holder = voiceHolder ?: return
        holder.removeAllViews()
        val voices = PantheraEngine.allVoices(this)
        if (voices.isEmpty()) {
            holder.addView(body("No voices in this generation's folder."))
            return
        }
        val p = PantheraEngine.prefs(this)
        val names = voices.map { it.name }
        val saved = p.getString(PantheraEngine.PREF_DEFAULT_VOICE, null)
        val at = names.indexOfFirst { it.equals(saved, true) }.coerceAtLeast(0)
        holder.addView(spinner(names, at) { i ->
            p.edit().putString(PantheraEngine.PREF_DEFAULT_VOICE, names[i]).apply()
        })
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
    }
    private fun body(t: String) = TextView(this).apply { text = t; textSize = 15f }
    private fun tabParams(wide: Boolean) =
        if (wide) LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f)
        else LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)

    private fun spinner(items: List<String>, selected: Int, onPick: (Int) -> Unit) =
        Spinner(this).apply {
            adapter = ArrayAdapter(this@SettingsActivity,
                android.R.layout.simple_spinner_dropdown_item, items)
            setSelection(selected, false)
            onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
                private var first = true
                override fun onItemSelected(a: AdapterView<*>?, v: View?, pos: Int, id: Long) {
                    // setSelection fires this once as the view attaches, and a
                    // preference should change because somebody chose it, not
                    // because a screen was built.
                    if (first) { first = false; return }
                    onPick(pos)
                }
                override fun onNothingSelected(a: AdapterView<*>?) {}
            }
        }

    private fun prettyGen(g: String) = when (g) {
        PantheraEngine.GEN_TIGER -> "Tiger (10.4)"
        PantheraEngine.GEN_LEOPARD -> "Leopard (10.5)"
        PantheraEngine.GEN_SNOW_LEOPARD -> "Snow Leopard (10.6)"
        PantheraEngine.GEN_LION -> "Lion (10.7)"
        else -> g
    }
    private fun rateText(wpm: Int) =
        if (wpm <= 0) "Follow the system's rate" else "$wpm words per minute"
    private fun wpmToProgress(wpm: Int) =
        if (wpm <= 0) 0 else (wpm - RATE_MIN + 1).coerceIn(1, RATE_MAX - RATE_MIN + 1)
    private fun progressToWpm(p: Int) =
        if (p == 0) 0 else (p + RATE_MIN - 1).coerceIn(RATE_MIN, RATE_MAX)

    // ---- behaviour ---------------------------------------------------------

    private fun refresh() {
        val verified = PantheraEngine.verified(this)
        testButton.isEnabled = verified
        val voices = PantheraEngine.allVoices(this)
        voicesView.text = if (voices.isEmpty()) "—"
            else voices.joinToString("\n") { "•  ${it.name}" }
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
        val voices = PantheraEngine.allVoices(this)
        val saved = PantheraEngine.prefs(this)
            .getString(PantheraEngine.PREF_DEFAULT_VOICE, null)
        // The voice the settings chose, so that the preview previews the
        // settings rather than a hard-coded Fred.
        val voice = voices.firstOrNull { it.name.equals(saved, true) }
            ?: voices.firstOrNull { it.name.equals("Fred", true) }
            ?: voices.firstOrNull()
        if (voice == null) { toast("No voice to speak."); return }
        val text = sampleText.text.toString().ifBlank { "Hello there." }
        val wpm = PantheraEngine.prefs(this).getInt(PantheraEngine.PREF_RATE, 0)
        testButton.isEnabled = false
        status.text = "Rendering ${voice.name}…"
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
                    n == 0 -> "Render produced no audio."
                    peak == 0 -> "Rendered $n samples but they are silent."
                    else -> "Playing $n samples from ${voice.name} (peak $peak)."
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
        const val RATE_MAX = 420
    }
}
