// The Android TTS engine. One engine, many voices (like SAPI): every voice the
// active engine generation exposes is one Android Voice, all served by the one
// emulated MacinTalk. The framework hands us text + rate; we cut it into pieces
// (see PantheraText), hand the engine one at a time, and stream each piece's
// PCM back as it is produced.
//
// A piece at a time rather than the whole utterance because the engine cannot
// be interrupted: it renders what it was given and a stop waits for it, so the
// piece is what a cancellation costs. Streaming rather than render-then-play
// because TalkBack gives up waiting.
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
        val saved = PantheraEngine.prefs(this).getString(PantheraEngine.PREF_DEFAULT_VOICE, null)
        val voices = PantheraEngine.allVoices(this)
        return voices.firstOrNull { it.name.equals(saved, true) }?.id
            ?: voices.firstOrNull { it.name.equals("Fred", true) }?.id
            ?: voices.firstOrNull()?.id
    }

    override fun onStop() {
        stopRequested = true
        PantheraEngine.stop()
    }

    // Android speechRate is a percent of normal (100 = normal); the Mac voices'
    // own default is about 180 wpm, so 100% maps there.
    private fun wpmFor(speechRate: Int): Int =
        (180 * (if (speechRate <= 0) 100 else speechRate) / 100).coerceIn(80, 500)

    override fun onSynthesizeText(request: SynthesisRequest, callback: SynthesisCallback) {
        stopRequested = false
        PantheraEngine.withSynthesis {
            if (!stopRequested) synthesize(request, callback)
        }
    }

    private fun synthesize(request: SynthesisRequest, callback: SynthesisCallback) {

        val text = request.charSequenceText?.toString() ?: ""
        Log.i("PantheraTts", "synth: voice=${request.voiceName} lang=${request.language} " +
            "rate=${request.speechRate} verified=${PantheraEngine.verified(this)} textLen=${text.length}")

        if (!PantheraEngine.verified(this)) {
            Log.w("PantheraTts", "not verified -> error"); callback.error(TextToSpeech.ERROR_SERVICE); return
        }

        val voice = PantheraEngine.voiceById(this, request.voiceName)
            ?: PantheraEngine.voiceById(this, onGetDefaultVoiceNameFor(null, null, null))
            ?: run { Log.w("PantheraTts", "no voice for '${request.voiceName}' -> error")
                     callback.error(TextToSpeech.ERROR_SERVICE); return }

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

        // Hand the engine one piece at a time, and stream each piece's PCM as
        // it is produced -- audio starts within a chunk instead of after a whole
        // render. Render-then-stream went silent because TalkBack gave up
        // waiting the few seconds a full render took before any audio arrived.
        //
        // The pieces exist because the engine cannot be interrupted: it renders
        // whatever it was handed, and a stop waits for it. Cancelling therefore
        // costs the rest of the current piece, not the rest of the paragraph --
        // see PantheraText for the measurement and the cutting rules.
        val pieces = PantheraText.pieces(text)
        for (piece in pieces) {
            if (stopRequested) break
            val started = PantheraEngine.speakStart(
                this, voice, PantheraText.bytes(piece), wpmFor(request.speechRate))
            if (started != 0) {
                Log.w("PantheraTts", "speakStart -> $started")
                // Anything already spoken is real audio the user heard; only a
                // failure on the very first piece is a failed utterance.
                if (total == 0) { callback.error(TextToSpeech.ERROR_SYNTHESIS); return }
                break
            }
            while (!stopRequested) {
                val n = PantheraEngine.pull(samples)
                if (n < 0) {
                    Log.e("PantheraTts", "nativePull failed: $n")
                    PantheraEngine.stop()
                    callback.error(TextToSpeech.ERROR_SYNTHESIS)
                    return
                }
                if (n == 0) break
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
