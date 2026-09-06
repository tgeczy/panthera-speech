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
            // Fred from whichever generation is active, not Tiger's by name:
            // every generation ships a Fred and the engine loads one generation
            // per process, so pinning the id here would fail the moment the
            // device is set to Leopard rather than telling us anything.
            val fred = client.voices.firstOrNull { it.name.endsWith("-fred") }
                ?: error("Fred missing from platform voice list: " +
                         client.voices.joinToString { it.name })
            Log.i("PantheraTest", "generation under test: ${fred.name}")
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
                // Long enough to BE "Hello there.", not merely long enough to
                // be audio.
                //
                // This check is here because its absence let a broken port
                // look green: on the first arm64 build every phoneme duration
                // collapsed to its minimum, so the sentence came back at 4032
                // frames instead of the 18144 the desktop renders -- a quarter
                // of a second of gabble with a healthy peak and perfect
                // run-to-run determinism. Both the checks above passed it.
                //
                // 20000 bytes is about 0.45 s, comfortably under any real
                // render of this sentence at any generation's default rate and
                // far above a collapsed one. A tighter bound would have to
                // know which generation is loaded; this does not need to.
                check(bytes.size > 20000) {
                    "far too short for \"Hello there.\": ${bytes.size} bytes " +
                    "-- durations are collapsing, not merely quiet"
                }
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

            // The AAC voices, whichever of them this device has.
            //
            // Vicki and Alex share the `meow` engine, whose sample bank is AAC
            // rather than PCM, so these are the only voices that exercise the
            // decoder the device supplies -- everything else here is Fred, who
            // never touches one. A silent WAV is the failure that matters: a
            // decoder returning nothing looks exactly like success from here.
            for (want in listOf("vicki", "alex")) {
                val voice = client.voices.firstOrNull { it.name.endsWith("-$want") }
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
                val soakVoice = client.voices.firstOrNull { it.name.endsWith("-alex") }
                    ?: client.voices.firstOrNull { it.name.endsWith("-vicki") }
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
