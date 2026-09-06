// Raw JNI binding to libpanthera.so -- the emulated Mac OS X speech engine.
// Not thread-safe: the one process-global engine must be driven by one caller
// at a time (PantheraEngine serialises render; stop is the deliberate
// exception, so it can interrupt a render in progress).
package com.pantheraspeech.tts

object PantheraNative {
    init {
        System.loadLibrary("panthera")
    }

    /** Bring the engine up from the MacinTalk synthesizer and SpeechDictionary
     * at the given paths.  Once per process; 0 on success, else an OSErr. */
    external fun nativeOpen(macinTalkPath: String, speechDictPath: String): Int

    /** Render one utterance with the given voice to signed-16-bit mono PCM at
     * [nativeSampleRate].  creator/voiceId are the VoiceSpec; wpm <= 0 keeps the
     * engine default rate.  Null on failure (or an empty utterance). */
    external fun nativeRender(
        voiceDir: String, creator: Int, voiceId: Int, text: String, wpm: Int
    ): ShortArray?

    /** Ask the engine to stop the utterance in progress; makes a blocked
     * nativeRender return early.  Safe to call from another thread. */
    external fun nativeStop()

    /** PCM sample rate nativeRender produces, in Hz. */
    external fun nativeSampleRate(): Int

    /** Read a .SpeechVoice bundle's VoiceSpec: returns [creator, id], or null if
     * the bundle has no VoiceDescription.  A plain file read -- safe before
     * nativeOpen, which is how the voice list is built. */
    external fun nativeVoiceSpec(voiceDir: String): IntArray?
}
