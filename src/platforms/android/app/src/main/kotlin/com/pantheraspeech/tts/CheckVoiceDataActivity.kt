// The framework fires ACTION_CHECK_TTS_DATA to ask whether the engine's voice
// data is usable. Ours is usable exactly when the user has run "Check Engine"
// and an engine generation's files are present -- the gate that keeps the
// engine from being selectable on an empty install.
package com.pantheraspeech.tts

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.speech.tts.TextToSpeech

class CheckVoiceDataActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val voices = if (PantheraEngine.verified(this))
            PantheraEngine.allVoices(this).map { it.id } else emptyList()
        val pass = voices.isNotEmpty()
        val data = Intent().apply {
            putStringArrayListExtra(
                TextToSpeech.Engine.EXTRA_AVAILABLE_VOICES, ArrayList(voices))
            putStringArrayListExtra(
                TextToSpeech.Engine.EXTRA_UNAVAILABLE_VOICES,
                if (pass) arrayListOf() else arrayListOf("eng-USA"))
        }
        setResult(
            if (pass) TextToSpeech.Engine.CHECK_VOICE_DATA_PASS
            else TextToSpeech.Engine.CHECK_VOICE_DATA_FAIL, data)
        finish()
    }
}
