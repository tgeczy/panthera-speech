// The Android TTS engine. One engine, many voices (like SAPI): every voice the
// present engine generations expose is one Android Voice, all served by the one
// emulated MacinTalk. The framework hands us text + rate; we render the whole
// utterance to PCM (the engine is faster than realtime for the formant voices)
// and stream it back, interruptible.
package com.pantheraspeech.tts

import android.media.AudioFormat
import android.speech.tts.SynthesisCallback
import android.speech.tts.SynthesisRequest
import android.speech.tts.TextToSpeech
import android.speech.tts.TextToSpeechService
import android.speech.tts.Voice
import java.util.Locale

class PantheraTtsService : TextToSpeechService() {

    @Volatile private var stopRequested = false

    // Every Mac voice at least speaks English; a few also carry other locales,
    // but v1 reports en-US, which is what Fred and the core Tiger voices are.
    private fun langAvailability(lang: String?): Int = when (lang) {
        "eng", "en" -> TextToSpeech.LANG_AVAILABLE
        else -> TextToSpeech.LANG_NOT_SUPPORTED
    }

    override fun onIsLanguageAvailable(lang: String?, country: String?, variant: String?): Int {
        // The gate: until the user has run Check Engine and the data is present,
        // the engine has no usable voices, so it reports its language missing.
        if (!PantheraEngine.verified(this)) return TextToSpeech.LANG_MISSING_DATA
        return langAvailability(lang)
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
        if (PantheraEngine.voiceById(this, name) != null) TextToSpeech.SUCCESS
        else TextToSpeech.ERROR

    override fun onLoadVoice(name: String?): Int = onIsValidVoiceName(name)

    override fun onGetDefaultVoiceNameFor(lang: String?, country: String?, variant: String?): String? {
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

        if (!PantheraEngine.verified(this)) {
            callback.error(TextToSpeech.ERROR_SERVICE)
            return
        }

        val voice = PantheraEngine.voiceById(this, request.voiceName)
            ?: PantheraEngine.voiceById(this, onGetDefaultVoiceNameFor(null, null, null))
            ?: run { callback.error(TextToSpeech.ERROR_SERVICE); return }

        val text = request.charSequenceText?.toString() ?: ""
        if (text.isBlank()) { callback.start(PantheraEngine.sampleRate(),
            AudioFormat.ENCODING_PCM_16BIT, 1); callback.done(); return }

        val pcm = PantheraEngine.render(this, voice, text, wpmFor(request.speechRate))
        if (pcm == null) { callback.error(TextToSpeech.ERROR_SYNTHESIS); return }

        val rate = PantheraEngine.sampleRate()
        if (callback.start(rate, AudioFormat.ENCODING_PCM_16BIT, 1) != TextToSpeech.SUCCESS) return

        // Stream the rendered PCM out in framework-sized chunks (little-endian
        // 16-bit), stopping promptly if the client interrupted us.
        val maxChunk = callback.maxBufferSize.coerceAtLeast(2) and 1.inv()  // even bytes
        val bytes = ByteArray(pcm.size * 2)
        var bi = 0
        for (s in pcm) {
            bytes[bi++] = (s.toInt() and 0xff).toByte()
            bytes[bi++] = ((s.toInt() shr 8) and 0xff).toByte()
        }
        var off = 0
        while (off < bytes.size && !stopRequested) {
            val len = minOf(maxChunk, bytes.size - off)
            if (callback.audioAvailable(bytes, off, len) != TextToSpeech.SUCCESS) break
            off += len
        }
        callback.done()
    }
}
