package com.pantheraspeech.tts

import android.app.Instrumentation
import android.app.Activity
import android.os.Bundle
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.media.AudioAttributes
import android.util.Log
import java.io.File
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** Device integration test: requires engine data and a successful Check Engine.
 * Uses the platform TTS client, including its Binder and synthesis callbacks.
 * Run with adb shell am instrument -w
 * com.pantheraspeech.tts.test/com.pantheraspeech.tts.EngineSmokeTest.
 */
class EngineSmokeTest : Instrumentation() {
    override fun onCreate(arguments: Bundle?) { super.onCreate(arguments); start() }

    override fun onStart() {
        var tts: TextToSpeech? = null
        val results = Bundle()
        try {
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
            val fred = client.voices.firstOrNull { it.name == "panthera-tiger-fred" }
                ?: error("Fred missing from platform voice list")
            check(client.setVoice(fred) == TextToSpeech.SUCCESS)
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

            var firstWav: ByteArray? = null
            for (i in 1..2) {
                val file = File(targetContext.filesDir, "framework-fred-$i.wav")
                utterance("file-$i") {
                    client.synthesizeToFile("Hello there.", Bundle(), file, "file-$i")
                }
                val bytes = file.readBytes()
                check(bytes.size > 2048) { "empty or very short WAV: ${bytes.size}" }
                check(String(bytes, 0, 4) == "RIFF")
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
            results.putString("stream", "PASS: initialization, repeated WAV synthesis, playback callback, stop/restart")
            finish(Activity.RESULT_OK, results)
        } catch (e: Throwable) {
            Log.e("PantheraTest", "FAILED", e)
            results.putString("stream", "FAIL: $e")
            finish(Activity.RESULT_CANCELED, results)
        } finally { tts?.shutdown() }
    }
}
