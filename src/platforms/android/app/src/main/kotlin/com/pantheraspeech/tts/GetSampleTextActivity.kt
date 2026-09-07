// ACTION_GET_SAMPLE_TEXT: the phrase the system speaks when the user previews
// the engine in Settings.
package com.pantheraspeech.tts

import android.app.Activity
import android.content.Intent
import android.os.Bundle
import android.speech.tts.TextToSpeech

class GetSampleTextActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val data = Intent().putExtra(
            TextToSpeech.Engine.EXTRA_SAMPLE_TEXT,
            "Hello there. This is a Macintosh voice, speaking on your watch.")
        setResult(TextToSpeech.LANG_AVAILABLE, data)
        finish()
    }
}
