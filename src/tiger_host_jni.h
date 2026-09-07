/* The in-process synthesis API used by Android and Linux libpanthera.
 *
 * The engine host is one translation unit built around process-global state
 * (one set of images, one channel), and host_open maps the images once per
 * process.  So this API is a thin front for that single engine: init once,
 * render an utterance to PCM, stop.  It is what panthera_jni.cpp binds to
 * Kotlin, and what the desktop build exercises via --jni-check so the same
 * bytes can be checked against the WAV oracle before any device is involved.
 *
 * Declared with C linkage so the C++ JNI unit can call into the C host.
 * Linux installs this header as panthera.h. Keep the library loaded for the
 * process lifetime: guest worker threads retain references to it. One engine
 * generation per process; serialize synthesis and settings calls. Only stop
 * is safe from a second thread while synthesis is in progress. Text arguments
 * are NUL-terminated MacRoman, while paths use the host's filesystem encoding.
 */
#ifndef TIGER_HOST_JNI_H
#define TIGER_HOST_JNI_H

#ifdef __cplusplus
extern "C" {
#endif

/* Bring the engine up: map the MacinTalk synthesizer at mtPath and the
 * SpeechDictionary at sdPath, open a speech channel.  Once per process --
 * a second call is a no-op that returns 0.  Returns 0, or the engine's OSErr. */
int  panthera_init(const char *mtPath, const char *sdPath);

/* Render one utterance with the given voice to signed 16-bit mono PCM at
 * panthera_sample_rate().  creator/voiceId are the VoiceSpec (e.g. 'mtk3'/1 for
 * Fred); voiceDir is the .SpeechVoice bundle; wpm <= 0 keeps the engine default
 * rate.  On success returns 0 and hands back a malloc'd buffer in *outPcm (the
 * caller frees it) and its length in frames in *outFrames.  Nonzero is an
 * OSErr or a negative host error. */
int  panthera_render(const char *voiceDir, unsigned creator, int voiceId,
                     const char *text, int wpm,
                     short **outPcm, unsigned *outFrames);

/* Streaming synthesis, for the low-latency TTS path.  Rather than render the
 * whole utterance before any sound (which makes a screen reader give up
 * waiting), begin the utterance and then pull PCM as the engine produces it:
 *   panthera_speak_start(...);            // returns as soon as it is accepted
 *   while ((n = panthera_pull(buf, cap)) > 0) // n samples ready; 0 = finished
 *       hand buf[0..n) to the framework;
 * so audio starts within one chunk.  speak_start returns 0 or an OSErr; pull
 * returns int16 samples written to out (0 when the utterance is done, or
 * a stop was asked for). A negative value reports a synthesis timeout/error. */
int  panthera_speak_start(const char *voiceDir, unsigned creator, int voiceId,
                          const char *text, int wpm);
int  panthera_pull(short *out, int maxSamples);

/* True when synthesis and its audio callbacks are finished, even if the caller
 * has not consumed all PCM. Read-only and safe from another thread after a
 * successful speak_start. It does not cancel or enter guest code. */
int  panthera_render_complete(void);

/* How to read numbers the engine gets wrong: "off", "fix" (the default) or
 * "words".  See tiger_host_numbers.c -- the rules are the NVDA driver's, and
 * they live in the host so that every front end reaches the same ones.
 *
 * A setting rather than an argument because it is a preference, not a property
 * of the utterance; it applies from the next utterance onwards. */
void panthera_set_number_style(const char *style);

/* Dictionary abbreviation rules; the front end also handles lexical tokens.
 * Set between utterances under synthesis ownership. */
void panthera_set_expand_abbreviations(int expand);
/* Leopard onward: fewest/fewer/more/most/leopard (engine default).
 * Set before panthera_init; changing it after init returns an error because
 * the engine caches the threshold beyond a channel's lifetime. */
int panthera_set_phrasing(const char *style);

/* NVDA scale, 0..100. Tiger defaults to 100, later generations to 90.
 * Generation selects its measured voice table. Set under synthesis ownership.
 * A negative level leaves volume to the caller's embedded commands. */
void panthera_set_volume(int level, const char *generation);

/* Ask the engine to stop the utterance in progress; makes a blocked
 * panthera_render / panthera_pull return with whatever it has.  Safe from
 * another thread. */
void panthera_stop(void);

/* Finish a cancelled stream on its owning synthesis thread, before starting
 * the next utterance. Required after pulling stops or the consumer declines
 * audio. panthera_render performs this cleanup itself. */
void panthera_finish(void);

/* The PCM sample rate panthera_render produces (Hz). */
int  panthera_sample_rate(void);

/* Read a .SpeechVoice bundle's VoiceSpec (creator OSType, id) from its
 * VoiceDescription -- a plain file read, no engine needed.  Returns 1 and fills
 * *creator/*voiceId on success, 0 if the bundle has no VoiceDescription. */
int  panthera_voice_spec(const char *voiceDir, unsigned *creator, int *voiceId);

#ifdef __cplusplus
}
#endif

#endif /* TIGER_HOST_JNI_H */
