// panthera_jni.cpp -- JNI bridge from Kotlin (PantheraNative) to the engine.
//
// Thin: it marshals strings and the PCM buffer across the boundary and calls
// the C synthesis API (tiger_host_jni.h).  The engine itself -- the Mach-O
// loader, the shims, Unicorn -- is linked into this same .so as one C
// translation unit (tiger_host.c compiled with TIGER_JNI), so these functions
// call panthera_* directly, no IPC.
//
// The native handle is process-global (one engine per process), so every entry
// point is serialised on the Kotlin side (PantheraEngine's lock).

#include <jni.h>
#include <string>
#include <stdlib.h>
#include "tiger_host_jni.h"

extern "C" {

JNIEXPORT jint JNICALL
Java_com_pantheraspeech_tts_PantheraNative_nativeOpen(
        JNIEnv *env, jclass, jstring jmt, jstring jsd) {
    const char *mt = env->GetStringUTFChars(jmt, nullptr);
    const char *sd = env->GetStringUTFChars(jsd, nullptr);
    int rc = (mt && sd) ? panthera_init(mt, sd) : -1;
    if (mt) env->ReleaseStringUTFChars(jmt, mt);
    if (sd) env->ReleaseStringUTFChars(jsd, sd);
    return rc;
}

// The engine's text is MacRoman bytes, not UTF-8 -- Kotlin has already folded
// and encoded it (see MacRoman.kt), so it arrives as a byte array and must not
// be touched again here. Copied into a NUL-terminated std::string because the
// C API takes a C string; MacRoman has no embedded NULs to lose.
static bool pt_text_bytes(JNIEnv *env, jbyteArray jtext, std::string &out) {
    if (!jtext) return false;
    jsize n = env->GetArrayLength(jtext);
    jbyte *b = env->GetByteArrayElements(jtext, nullptr);
    if (!b) return false;
    out.assign(reinterpret_cast<const char *>(b), (size_t)n);
    env->ReleaseByteArrayElements(jtext, b, JNI_ABORT);
    return true;
}

JNIEXPORT jshortArray JNICALL
Java_com_pantheraspeech_tts_PantheraNative_nativeRender(
        JNIEnv *env, jclass, jstring jvoice, jint creator, jint voiceId,
        jbyteArray jtext, jint wpm) {
    const char *voice = env->GetStringUTFChars(jvoice, nullptr);
    std::string text;
    bool haveText = pt_text_bytes(env, jtext, text);
    short   *pcm    = nullptr;
    unsigned frames = 0;
    int rc = (voice && haveText)
        ? panthera_render(voice, (unsigned)creator, (int)voiceId, text.c_str(),
                          (int)wpm, &pcm, &frames)
        : -1;
    if (voice) env->ReleaseStringUTFChars(jvoice, voice);
    if (rc != 0 || pcm == nullptr) { free(pcm); return nullptr; }
    jshortArray arr = env->NewShortArray((jsize)frames);
    if (arr) env->SetShortArrayRegion(arr, 0, (jsize)frames,
                                      reinterpret_cast<const jshort *>(pcm));
    free(pcm);
    return arr;
}

JNIEXPORT jint JNICALL
Java_com_pantheraspeech_tts_PantheraNative_nativeSpeakStart(
        JNIEnv *env, jclass, jstring jvoice, jint creator, jint voiceId,
        jbyteArray jtext, jint wpm) {
    const char *voice = env->GetStringUTFChars(jvoice, nullptr);
    std::string text;
    bool haveText = pt_text_bytes(env, jtext, text);
    int rc = (voice && haveText)
        ? panthera_speak_start(voice, (unsigned)creator, (int)voiceId,
                               text.c_str(), (int)wpm)
        : -1;
    if (voice) env->ReleaseStringUTFChars(jvoice, voice);
    return rc;
}

// Fills `out` with up to out.length int16 samples; returns the count (0 = the
// utterance is finished). Blocks briefly waiting for the worker to produce more.
JNIEXPORT jint JNICALL
Java_com_pantheraspeech_tts_PantheraNative_nativePull(
        JNIEnv *env, jclass, jshortArray out) {
    jsize cap = env->GetArrayLength(out);
    jshort *buf = env->GetShortArrayElements(out, nullptr);
    if (!buf) return 0;
    int n = panthera_pull(reinterpret_cast<short *>(buf), (int)cap);
    env->ReleaseShortArrayElements(out, buf, 0);   // copy back to Kotlin
    return n;
}

JNIEXPORT void JNICALL
Java_com_pantheraspeech_tts_PantheraNative_nativeStop(JNIEnv *, jclass) {
    panthera_stop();
}

JNIEXPORT void JNICALL
Java_com_pantheraspeech_tts_PantheraNative_nativeFinish(JNIEnv *, jclass) {
    panthera_finish();
}

JNIEXPORT jint JNICALL
Java_com_pantheraspeech_tts_PantheraNative_nativeSampleRate(JNIEnv *, jclass) {
    return panthera_sample_rate();
}

JNIEXPORT jintArray JNICALL
Java_com_pantheraspeech_tts_PantheraNative_nativeVoiceSpec(
        JNIEnv *env, jclass, jstring jvoice) {
    const char *voice = env->GetStringUTFChars(jvoice, nullptr);
    unsigned creator = 0;
    int      id      = 0;
    int ok = voice ? panthera_voice_spec(voice, &creator, &id) : 0;
    if (voice) env->ReleaseStringUTFChars(jvoice, voice);
    if (!ok) return nullptr;
    jintArray arr = env->NewIntArray(2);
    if (arr) {
        jint vals[2] = { (jint)creator, (jint)id };
        env->SetIntArrayRegion(arr, 0, 2, vals);
    }
    return arr;
}

}  // extern "C"
