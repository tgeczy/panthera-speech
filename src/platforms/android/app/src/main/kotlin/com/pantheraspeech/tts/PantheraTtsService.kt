// Each voice selects a generation worker. Stream complete requests to preserve
// sentence continuity and breaths; PantheraText separates only explicit pause
// boundaries. Cancellation retires the private worker through its binding owner
// because our current native stop path can wait for pending synthesis to drain.
package com.pantheraspeech.tts

import android.media.AudioFormat
import android.speech.tts.SynthesisCallback
import android.speech.tts.SynthesisRequest
import android.speech.tts.TextToSpeech
import android.speech.tts.TextToSpeechService
import android.speech.tts.Voice
import android.util.Log
import java.util.Locale

class PantheraTtsService : TextToSpeechService() {

    @Volatile private var stopRequested = false

    override fun onCreate() {
        super.onCreate()
        // Warm the engine when the service binds, so the first utterance isn't
        // gated on the few-second image load -- a screen reader's first request
        // must not wait that long.
        Thread { try { PantheraEngine.warmUp(applicationContext) } catch (e: Throwable) {} }.start()
    }

    // Every Mac voice at least speaks English; a few also carry other locales,
    // but v1 reports en-US, which is what Fred and the core Tiger voices are.
    private fun langAvailability(lang: String?, country: String?): Int = when {
        lang != "eng" && lang != "en" -> TextToSpeech.LANG_NOT_SUPPORTED
        country == "USA" || country == "US" -> TextToSpeech.LANG_COUNTRY_AVAILABLE
        else -> TextToSpeech.LANG_AVAILABLE
    }

    override fun onIsLanguageAvailable(lang: String?, country: String?, variant: String?): Int {
        // The gate: until the user has run Check Engine and the data is present,
        // the engine has no usable voices, so it reports its language missing.
        if (!PantheraEngine.verified(this)) return TextToSpeech.LANG_MISSING_DATA
        return langAvailability(lang, country)
    }

    override fun onGetLanguage(): Array<String> = arrayOf("eng", "USA", "")

    override fun onLoadLanguage(lang: String?, country: String?, variant: String?): Int =
        onIsLanguageAvailable(lang, country, variant)

    override fun onGetVoices(): MutableList<Voice> {
        if (!PantheraEngine.verified(this)) return mutableListOf()
        return PantheraEngine.allVoices(this).map { v ->
            Voice(v.id, Locale.US, Voice.QUALITY_NORMAL, Voice.LATENCY_NORMAL, false, emptySet())
        }.toMutableList()
    }

    override fun onIsValidVoiceName(name: String?): Int =
        if (PantheraEngine.verified(this) && PantheraEngine.voiceById(this, name) != null) TextToSpeech.SUCCESS
        else TextToSpeech.ERROR

    override fun onLoadVoice(name: String?): Int = onIsValidVoiceName(name)

    override fun onGetDefaultVoiceNameFor(lang: String?, country: String?, variant: String?): String? {
        if (!PantheraEngine.verified(this)) return null
        if (lang != null && langAvailability(lang, country) < 0) return null
        // The default belongs to the active generation, not to the whole list.
        // Asking the flat list for "Fred" would otherwise answer with whichever
        // generation happened to sort first and quietly switch engines.
        val gen = PantheraEngine.activeGen(this)
        val saved = PantheraEngine.defaultVoiceName(this, gen)
        val voices = PantheraEngine.activeVoices(this)
        return voices.firstOrNull { it.name.equals(saved, true) }?.id
            ?: voices.firstOrNull { it.name.equals("Fred", true) }?.id
            ?: voices.firstOrNull()?.id
            ?: PantheraEngine.allVoices(this).firstOrNull()?.id
    }

    override fun onStop() {
        stopRequested = true
        PantheraEngine.stop()
    }

    override fun onSynthesizeText(request: SynthesisRequest, callback: SynthesisCallback) {
        stopRequested = false
        PantheraEngine.withSynthesis {
            if (!stopRequested) synthesize(request, callback)
        }
    }

    private fun synthesize(request: SynthesisRequest, callback: SynthesisCallback) {
        // Say that this thread carries speech.  The scheduler places by
        // priority, and on a watch with unequal cores -- a Galaxy Watch pairs a
        // Cortex-A78 with A55s -- being put on the wrong one is the difference
        // between the engine answering and the engine being unusable: an
        // in-order core is close to the worst case for emulated code. The
        // native worker and pacer threads ask for the same thing themselves.
        // Worth nothing on four identical A53s, which is what this was measured
        // on, and free either way.
        android.os.Process.setThreadPriority(android.os.Process.THREAD_PRIORITY_AUDIO)

        val text = request.charSequenceText?.toString() ?: ""
        // Opt-in device benchmark: tie the first audible sample to Android's
        // playback-position callback instead of mistaking onStart for sound.
        val latencyProbe = request.params.getBoolean("com.pantheraspeech.tts.latency_probe", false)
        var probeMarked = false
        Log.i("PantheraTts", "synth: voice=${request.voiceName} lang=${request.language} " +
            "rate=${request.speechRate} verified=${PantheraEngine.verified(this)} textLen=${text.length}")

        if (!PantheraEngine.verified(this)) {
            Log.w("PantheraTts", "not verified -> error"); callback.error(TextToSpeech.ERROR_SERVICE); return
        }

        // The voice chosen in settings wins unless the user says otherwise.
        // A screen reader asks this engine for a default voice once, when it
        // connects, and sends that name with every request afterwards; while
        // this defaulted off, a voice chosen in settings did not take effect
        // until the engine was next restarted, which is indistinguishable
        // from the setting being broken.
        val voice = PantheraEngine.voiceById(this, if (PantheraEngine.prefs(this).getBoolean("override_voice", true))
            onGetDefaultVoiceNameFor(null, null, null) else request.voiceName)
            ?: PantheraEngine.voiceById(this, onGetDefaultVoiceNameFor(null, null, null))
            ?: run { Log.w("PantheraTts", "no voice for '${request.voiceName}' -> error")
                     callback.error(TextToSpeech.ERROR_SERVICE); return }

        val snapshot = PantheraEngine.settings(this, voice.gen)
        val rate = PantheraEngine.sampleRate()
        if (callback.start(rate, AudioFormat.ENCODING_PCM_16BIT, 1) != TextToSpeech.SUCCESS) {
            Log.w("PantheraTts", "callback.start refused"); return
        }
        if (text.isBlank()) { callback.done(); return }

        val sampleCapacity = minOf(4096, callback.maxBufferSize / 2)
        if (sampleCapacity <= 0) {
            PantheraEngine.stop()
            callback.error(TextToSpeech.ERROR_OUTPUT)
            return
        }
        val samples = ShortArray(sampleCapacity)
        val bytes = ByteArray(samples.size * 2)
        var total = 0

        // Stream the complete request. Keeping sentence boundaries inside the
        // same utterance preserves the engine's pauses and Alex's breaths.
        // onStop posts cancellation to the worker without entering guest code.
        val pieces = PantheraText.pieces(text, snapshot, voice.gen)
        for (piece in pieces) {
            if (stopRequested) break
            val started = PantheraEngine.speakStart(
                this, voice, PantheraText.bytes(piece), snapshot.wpm(request.speechRate), snapshot)
            if (started != 0) {
                if (stopRequested) return
                Log.w("PantheraTts", "speakStart -> $started")
                // Anything already spoken is real audio the user heard; only a
                // failure on the very first piece is a failed utterance.
                if (total == 0) { callback.error(TextToSpeech.ERROR_SYNTHESIS); return }
                break
            }
            if (stopRequested) PantheraEngine.stop()
            while (!stopRequested) {
                val n = PantheraEngine.pull(samples)
                if (n < 0) {
                    if (stopRequested) return
                    Log.e("PantheraTts", "nativePull failed: $n")
                    PantheraEngine.stop()
                    callback.error(TextToSpeech.ERROR_SYNTHESIS)
                    return
                }
                if (n == 0) break
                if (latencyProbe && !probeMarked) {
                    val audible = (0 until n).firstOrNull { kotlin.math.abs(samples[it].toInt()) > 128 }
                    if (audible != null) {
                        callback.rangeStart(maxOf(1, total + audible), 0, minOf(1, text.length))
                        probeMarked = true
                    }
                }
                var bi = 0
                for (i in 0 until n) {
                    val s = samples[i].toInt()
                    bytes[bi++] = (s and 0xff).toByte()
                    bytes[bi++] = ((s shr 8) and 0xff).toByte()
                }
                if (callback.audioAvailable(bytes, 0, n * 2) != TextToSpeech.SUCCESS) {
                    Log.i("PantheraTts", "audio callback stopped after $total samples")
                    PantheraEngine.stop()
                    return
                }
                total += n
            }
        }
        Log.i("PantheraTts", "synth ${voice.name} streamed $total samples in " +
            "${pieces.size} piece(s), stop=$stopRequested")
        callback.done()
    }
}
