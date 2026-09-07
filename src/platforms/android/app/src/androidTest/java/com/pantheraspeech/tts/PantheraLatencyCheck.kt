package com.pantheraspeech.tts

import android.app.Activity
import android.app.Instrumentation
import android.os.Bundle
import android.os.SystemClock
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import android.util.Log
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/** Real TTS client: first PCM is not playback. The opt-in range marker follows
 * the first nonquiet sample through Android's playback-position notification.
 * Run EngineSmokeTest with -e latency true. Synthetic digits only; no user text. */
internal object PantheraLatencyCheck {
    private class Sample {
        val at = SystemClock.elapsedRealtimeNanos()
        val audio = CountDownLatch(1)
        val done = CountDownLatch(1)
        @Volatile var firstMs = -1L
        @Volatile var gateMs = -1L
        @Volatile var soundMs = -1L
        @Volatile var error = false
        @Volatile var outcome = "pending"
        var bytes = 0
        fun elapsed() = (SystemClock.elapsedRealtimeNanos() - at) / 1_000_000
    }

    fun run(test: Instrumentation, mode: String) {
        val ctx = test.targetContext
        val prefs = PantheraEngine.prefs(ctx)
        val original = prefs.all.toMap()
        val samples = ConcurrentHashMap<String, Sample>()
        val report = ArrayList<String>()
        var client: TextToSpeech? = null
        var code = Activity.RESULT_CANCELED
        val result = Bundle()
        try {
            val edit = prefs.edit().putBoolean("override_voice", false)
            for (gen in PantheraEngine.availableGens(ctx)) {
                edit.putInt(PantheraEngine.settingKey("rate_wpm", gen), 0)
                    .putInt(PantheraEngine.settingKey("inflection", gen), 50)
                    .putInt(PantheraEngine.settingKey("volume", gen), PantheraEngine.defaultVolume(gen))
            }
            edit.commit()
            if (mode == "native") {
                for (name in listOf("panthera-leopard-fred", "panthera-leopard-alex")) {
                    val voice = PantheraEngine.voiceById(ctx, name) ?: error("Missing $name")
                    repeat(16) { run ->
                        PantheraEngine.withSynthesis {
                            val at = SystemClock.elapsedRealtimeNanos()
                            fun elapsed() = (SystemClock.elapsedRealtimeNanos() - at) / 1_000_000
                            val text = if (run % 2 == 0) "Seven" else "Restart with debug logging enabled."
                            check(PantheraEngine.speakStart(ctx, voice, PantheraText.bytes(text), 387) == 0)
                            val started = elapsed()
                            var first = -1L
                            var audible = -1L
                            var frames = 0
                            val buffer = ShortArray(4096)
                            val hash = java.security.MessageDigest.getInstance("SHA-256")
                            while (true) {
                                val n = PantheraEngine.pull(buffer)
                                check(n >= 0) { "$name native pull failed" }
                                if (n == 0) break
                                if (first < 0) first = elapsed()
                                if (audible < 0 && (0 until n).any { kotlin.math.abs(buffer[it].toInt()) > 128 }) audible = elapsed()
                                for (i in 0 until n) {
                                    hash.update(buffer[i].toByte()); hash.update((buffer[i].toInt() shr 8).toByte())
                                }
                                frames += n
                            }
                            check(frames > 1000 && audible >= 0)
                            val line = "$name-native-$run start=$started first=$first audible=$audible done=${elapsed()} frames=$frames sha256=" +
                                hash.digest().joinToString("") { "%02x".format(it) }
                            report.add(line); Log.i("PantheraLatency", line)
                        }
                    }
                }
                code = Activity.RESULT_OK
                result.putString("stream", "PASS latency probe\n" + report.joinToString("\n"))
                return
            }
            val ready = CountDownLatch(1)
            var initialized = TextToSpeech.ERROR
            test.runOnMainSync {
                client = TextToSpeech(ctx, { initialized = it; ready.countDown() }, ctx.packageName)
            }
            check(ready.await(15, TimeUnit.SECONDS) && initialized == TextToSpeech.SUCCESS)
            val tts = client!!
            tts.setSpeechRate(2.15f)
            tts.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                override fun onStart(id: String) {}
                override fun onDone(id: String) { samples[id]?.let { it.outcome = "done"; it.done.countDown() } }
                override fun onError(id: String) { onError(id, TextToSpeech.ERROR) }
                override fun onError(id: String, errorCode: Int) {
                    Log.w("PantheraLatency", "$id error=$errorCode")
                    samples[id]?.let { it.error = true; it.outcome = "error=$errorCode"; it.done.countDown() }
                }
                override fun onStop(id: String, interrupted: Boolean) {
                    Log.i("PantheraLatency", "$id stopped interrupted=$interrupted")
                    samples[id]?.let { it.outcome = "stopped interrupted=$interrupted"; it.done.countDown() }
                }
                override fun onAudioAvailable(id: String, audio: ByteArray) {
                    samples[id]?.let {
                        if (it.firstMs < 0) it.firstMs = it.elapsed()
                        it.bytes += audio.size
                        if (it.gateMs < 0 && it.bytes >= 8192) it.gateMs = it.elapsed()
                        it.audio.countDown()
                    }
                }
                override fun onRangeStart(id: String, start: Int, end: Int, frame: Int) {
                    samples[id]?.let { if (it.soundMs < 0) it.soundMs = it.elapsed() }
                }
            })
            fun say(id: String, text: String): Sample {
                val sample = Sample(); samples[id] = sample
                val params = Bundle().apply { putBoolean("com.pantheraspeech.tts.latency_probe", true) }
                check(tts.speak(text, TextToSpeech.QUEUE_FLUSH, params, id) == TextToSpeech.SUCCESS)
                return sample
            }
            fun complete(id: String, text: String) {
                val s = say(id, text)
                check(s.done.await(12, TimeUnit.SECONDS)) { "$id completion timed out" }
                check(!s.error && s.bytes > 0 && s.outcome == "done") { "$id failed/dropped: ${s.outcome}, bytes=${s.bytes}" }
                // Short utterances can finish before the marker is delivered;
                // retain -1 in that case instead of inventing a playback time.
                val line = "$id first=${s.firstMs} gate=${s.gateMs} sound=${s.soundMs} done=${s.elapsed()} bytes=${s.bytes}"
                report.add(line); Log.i("PantheraLatency", line)
            }
            for (name in listOf("panthera-leopard-fred", "panthera-leopard-alex")) {
                check(tts.setVoice(tts.voices.first { it.name == name }) == TextToSpeech.SUCCESS)
                complete("$name-cold", "Seven")
                if (mode == "rapid") {
                    for (gap in listOf(150L, 100L)) {
                        val burst = ArrayList<Sample>()
                        repeat(30) { i ->
                            burst.add(say("$name-burst-$gap-$i", "Navigation item ${i + 1}, with more text to interrupt."))
                            Thread.sleep(gap)
                        }
                        complete("$name-burst-$gap-final", "Finished navigating")
                        val line = "$name-burst gap=$gap requests=${burst.size} firstBeforeNext=${burst.count { it.firstMs in 0 until gap }} " +
                            "playbackBeforeNext=${burst.count { it.soundMs in 0 until gap }} errors=${burst.count { it.error }}"
                        report.add(line); Log.i("PantheraLatency", line)
                        check(burst.none { it.error }) { "$name rapid replacement reported synthesis errors" }
                    }
                    continue
                }
                repeat(8) { complete("$name-digit-$it", "${it + 1}") }
                repeat(10) { i ->
                    val old = say("$name-interrupt-$i", "This is a long navigation item with several sentences. ".repeat(15))
                    check(old.audio.await(5, TimeUnit.SECONDS) && !old.error)
                    Thread.sleep(35)
                    complete("$name-swipe-$i", "Next item")
                }
            }
            code = Activity.RESULT_OK
            result.putString("stream", "PASS latency probe\n" + report.joinToString("\n"))
        } catch (e: Throwable) {
            result.putString("stream", "FAIL latency probe: ${Log.getStackTraceString(e)}\n" + report.joinToString("\n"))
        } finally {
            client?.stop(); client?.shutdown()
            val edit = prefs.edit().clear()
            for ((key, value) in original) when (value) {
                is String -> edit.putString(key, value)
                is Int -> edit.putInt(key, value)
                is Long -> edit.putLong(key, value)
                is Float -> edit.putFloat(key, value)
                is Boolean -> edit.putBoolean(key, value)
                is Set<*> -> edit.putStringSet(key, value.filterIsInstance<String>().toSet())
            }
            edit.commit()
            test.finish(code, result)
        }
    }
}
