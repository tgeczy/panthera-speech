// The app's one screen. It does the thing the NVDA add-on's settings do:
// tell the user where the engine data goes, let them verify it is there
// ("Check Engine"), and -- until they have -- keep the engine ungated. Nothing
// here selects the TTS engine for the user; the OS does that in its own TTS
// settings, and only once Check Engine has passed (see CheckVoiceDataActivity).
package com.pantheraspeech.tts

import android.app.Activity
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import android.os.Bundle
import android.text.method.ScrollingMovementMethod
import android.view.Gravity
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast

class SettingsActivity : Activity() {

    private lateinit var status: TextView
    private lateinit var pathView: TextView
    private lateinit var voicesView: TextView
    private lateinit var testButton: Button

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        val pad = (16 * resources.displayMetrics.density).toInt()
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
        }

        fun heading(t: String) = TextView(this).apply {
            text = t; textSize = 20f; setPadding(0, pad, 0, pad / 2)
        }
        fun body(t: String) = TextView(this).apply { text = t; textSize = 15f }

        root.addView(TextView(this).apply {
            text = "Panthera Speech"; textSize = 26f; gravity = Gravity.CENTER
        })
        root.addView(body(
            "Macintosh speech voices, running under emulation. " +
            "Nothing of Apple's ships with this app — extract the engine " +
            "data from your own copy and put it in the folder below."))

        root.addView(heading("1.  Put engine data here"))
        pathView = TextView(this).apply {
            textSize = 13f
            setTextIsSelectable(true)
            text = PantheraEngine.dataRoot(this@SettingsActivity).absolutePath
        }
        root.addView(pathView)
        root.addView(body(
            "Inside it, one folder per engine generation — for now, a " +
            "“tiger” folder holding MacinTalk, the SpeechDictionary " +
            "framework, and a Voices folder of .SpeechVoice bundles."))

        root.addView(heading("2.  Check the engine"))
        val checkButton = Button(this).apply {
            text = "Check Engine"
            setOnClickListener { runCheck() }
        }
        root.addView(checkButton)
        status = body("Not checked yet.")
        root.addView(status)

        root.addView(heading("Voices found"))
        voicesView = body("—")
        root.addView(voicesView)

        root.addView(heading("3.  Try it"))
        testButton = Button(this).apply {
            text = "Speak a sample"
            isEnabled = false
            setOnClickListener { testSpeak() }
        }
        root.addView(testButton)
        root.addView(body(
            "Then pick Panthera Speech as your engine in the system's " +
            "Text-to-speech settings."))

        val scroll = ScrollView(this).apply { addView(root) }
        status.movementMethod = ScrollingMovementMethod()
        setContentView(scroll)

        refresh()
    }

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
        val voice = PantheraEngine.allVoices(this)
            .firstOrNull { it.name.equals("Fred", true) }
            ?: PantheraEngine.allVoices(this).firstOrNull()
        if (voice == null) { toast("No voice to speak."); return }
        testButton.isEnabled = false
        status.text = "Rendering ${voice.name}…"
        Thread {
            val pcm = PantheraEngine.render(
                this, voice, "Hello there. This is ${voice.name}, on your watch.", 0)
            runOnUiThread {
                testButton.isEnabled = true
                if (pcm == null || pcm.isEmpty()) { status.text = "Render failed."; return@runOnUiThread }
                status.text = "Playing ${pcm.size} samples from ${voice.name}."
            }
            if (pcm != null && pcm.isNotEmpty()) playPcm(pcm, PantheraEngine.sampleRate())
        }.start()
    }

    private fun playPcm(pcm: ShortArray, rate: Int) {
        val min = AudioTrack.getMinBufferSize(
            rate, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val track = AudioTrack(
            AudioAttributes.Builder()
                .setUsage(AudioAttributes.USAGE_MEDIA)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build(),
            AudioFormat.Builder()
                .setSampleRate(rate)
                .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                .setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build(),
            maxOf(min, pcm.size * 2), AudioTrack.MODE_STATIC, AudioManager.AUDIO_SESSION_ID_GENERATE)
        track.write(pcm, 0, pcm.size)
        track.play()
    }

    private fun toast(t: String) = Toast.makeText(this, t, Toast.LENGTH_SHORT).show()
}
