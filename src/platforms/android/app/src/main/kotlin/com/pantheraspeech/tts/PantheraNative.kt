// Raw JNI binding to libpanthera.so -- the emulated Mac OS X speech engine.
// Not thread-safe: the one process-global engine must be driven by one caller
// at a time. Only the stop flag and read-only completion query may be used
// concurrently; neither enters guest code.
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
     * engine default rate.  Null on failure (or an empty utterance).
     *
     * `text` is MacRoman bytes, not a String: the engine reads a single-byte
     * Mac encoding, so the conversion happens in MacRoman.encode before the
     * boundary rather than being guessed at after it. */
    external fun nativeRender(
        voiceDir: String, creator: Int, voiceId: Int, text: ByteArray, wpm: Int
    ): ShortArray?

    /** Begin an utterance for the streaming (low-latency) path; returns 0 or an
     * OSErr.  Synthesis then runs on the engine's worker -- drain it with
     * nativePull. */
    external fun nativeSpeakStart(
        voiceDir: String, creator: Int, voiceId: Int, text: ByteArray, wpm: Int): Int

    /** Fill [out] with up to out.size int16 samples produced so far; returns the
     * count (0 when the utterance is finished).  Blocks briefly for more. */
    external fun nativePull(out: ShortArray): Int

    /** Synthesis and callbacks ended, independently of PCM playback/draining.
     * Read-only; safe on the cancellation thread after nativeSpeakStart returns. */
    external fun nativeRenderComplete(): Boolean

    /** Ask the engine to stop the utterance in progress; makes a blocked
     * nativeRender / nativePull return early.  Safe from another thread. */
    external fun nativeStop()

    /** Complete cancellation on the synthesis thread before releasing ownership. */
    external fun nativeFinish()

    /** PCM sample rate nativeRender produces, in Hz. */
    external fun nativeSampleRate(): Int

    /**
     * How to read numbers the engine gets wrong: "off", "fix" or "words".
     *
     * The rules live in the host (tiger_host_numbers.c) rather than here, so
     * that this app, the NVDA driver and the SAPI voices all read a number
     * the same way. Android had no number handling at all before this: from
     * seven digits up the engine spells them out one at a time.
     */
    external fun nativeSetNumberStyle(style: String)

    external fun nativeSetVolume(level: Int, generation: String)
    external fun nativeSetExpandAbbreviations(expand: Boolean)
    external fun nativeSetPhrasing(style: String): Int

    /** Read a .SpeechVoice bundle's VoiceSpec: returns [creator, id], or null if
     * the bundle has no VoiceDescription.  A plain file read -- safe before
     * nativeOpen, which is how the voice list is built. */
    external fun nativeVoiceSpec(voiceDir: String): IntArray?
}
