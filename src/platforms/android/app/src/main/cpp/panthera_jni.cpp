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

JNIEXPORT jshortArray JNICALL
Java_com_pantheraspeech_tts_PantheraNative_nativeRender(
        JNIEnv *env, jclass, jstring jvoice, jint creator, jint voiceId,
        jstring jtext, jint wpm) {
    const char *voice = env->GetStringUTFChars(jvoice, nullptr);
    const char *text  = env->GetStringUTFChars(jtext, nullptr);
    short   *pcm    = nullptr;
    unsigned frames = 0;
    int rc = (voice && text)
        ? panthera_render(voice, (unsigned)creator, (int)voiceId, text,
                          (int)wpm, &pcm, &frames)
        : -1;
    if (voice) env->ReleaseStringUTFChars(jvoice, voice);
    if (text)  env->ReleaseStringUTFChars(jtext, text);
    if (rc != 0 || pcm == nullptr) { free(pcm); return nullptr; }
    jshortArray arr = env->NewShortArray((jsize)frames);
    if (arr) env->SetShortArrayRegion(arr, 0, (jsize)frames,
                                      reinterpret_cast<const jshort *>(pcm));
    free(pcm);
    return arr;
}

JNIEXPORT void JNICALL
Java_com_pantheraspeech_tts_PantheraNative_nativeStop(JNIEnv *, jclass) {
    panthera_stop();
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
