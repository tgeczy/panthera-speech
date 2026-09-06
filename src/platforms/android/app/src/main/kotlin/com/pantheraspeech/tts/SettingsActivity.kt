// The app's one screen. It does the thing the NVDA add-on's settings do:
// tell the user where the engine data goes, let them verify it is there
// ("Check Engine"), and -- until they have -- keep the engine ungated. Nothing
// here selects the TTS engine for the user; the OS does that in its own TTS
// settings, and only once Check Engine has passed (see CheckVoiceDataActivity).
package com.pantheraspeech.tts

import android.app.Activity
import android.content.Intent
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioTrack
import android.os.Bundle
import android.provider.Settings
import android.util.Log
import java.io.File
import java.io.FileOutputStream
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
            "Text-to-speech settings — jump straight there:"))
        root.addView(Button(this).apply {
            text = "Open Text-to-speech settings"
            setOnClickListener { openTtsSettings() }
        })

        val scroll = ScrollView(this).apply { addView(root) }
        status.movementMethod = ScrollingMovementMethod()
        setContentView(scroll)

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
}
