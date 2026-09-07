package com.pantheraspeech.tts

import android.app.Service
import android.content.Intent
import android.os.IBinder
import java.util.concurrent.Callable
import java.util.concurrent.Executors

/** One generation per private process. The public TTS service survives switches.
 * Native calls use one persistent thread; cancellation bypasses that thread. */
open class PantheraWorkerService : Service() {
    private val synthesis = Executors.newSingleThreadExecutor()
    private fun <T> runNative(block: () -> T): T = synthesis.submit(Callable(block)).get()
    @Volatile private var opened = false
    private var phrasing: String? = null
    private val requestState = Any()
    private var activeRequest = false
    private val binder = object : IPantheraWorker.Stub() {
        override fun open(engine: String, dictionary: String, requestedPhrasing: String): Int = runNative {
            if (opened && phrasing != requestedPhrasing) return@runNative RECONFIGURE
            if (!opened) {
                if (PantheraNative.nativeSetPhrasing(requestedPhrasing) != 0) return@runNative -1
                opened = PantheraNative.nativeOpen(engine, dictionary) == 0
                if (opened) phrasing = requestedPhrasing
            }
            if (opened) PantheraNative.nativeSampleRate() else -1
        }
        // Only the app can bind this service. The owner waits for Binder death
        // before binding a new worker; the public TTS process stays alive.
        override fun shutdown() { android.os.Process.killProcess(android.os.Process.myPid()) }
        override fun start(voice: String, creator: Int, voiceId: Int, text: ByteArray,
                           wpm: Int, volume: Int, generation: String, numbers: String,
                           expandAbbreviations: Boolean): Int = runNative {
            check(opened)
            PantheraNative.nativeSetVolume(volume, generation)
            PantheraNative.nativeSetNumberStyle(numbers)
            PantheraNative.nativeSetExpandAbbreviations(expandAbbreviations)
            synchronized(requestState) { activeRequest = true }
            val status = PantheraNative.nativeSpeakStart(voice, creator, voiceId, text, wpm)
            if (status != 0) synchronized(requestState) { activeRequest = false }
            status
        }
        override fun pull(capacity: Int): ByteArray? = runNative {
            val samples = ShortArray(capacity.coerceIn(1, 4096))
            val count = PantheraNative.nativePull(samples)
            if (count < 0) null else ByteArray(count * 2).also { bytes ->
                for (i in 0 until count) {
                    bytes[i*2] = samples[i].toByte()
                    bytes[i*2+1] = (samples[i].toInt() shr 8).toByte()
                }
            }
        }
        override fun stop() {
            synchronized(requestState) {
                if (activeRequest) {
                    // StopSpeech can either drain an entire paragraph or leave
                    // deferred work that truncates the next utterance. Retire
                    // this private worker at an explicit cancellation boundary.
                    // The public service and client survive; normal completed
                    // utterances keep their warm worker and paragraph breaths.
                    android.util.Log.i("PantheraEngine", "Retiring cancelled engine worker")
                    android.os.Process.killProcess(android.os.Process.myPid())
                }
            }
        }
        override fun finish() { runNative {
            try { if (opened) PantheraNative.nativeFinish() }
            finally { synchronized(requestState) { activeRequest = false } }
        } }
    }
    override fun onBind(intent: Intent): IBinder = binder
    override fun onDestroy() {
        synthesis.shutdownNow()
        super.onDestroy()
    }
    companion object { const val RECONFIGURE = -2 }
}
class TigerWorkerService : PantheraWorkerService()
class LeopardWorkerService : PantheraWorkerService()
class SnowLeopardWorkerService : PantheraWorkerService()
class LionWorkerService : PantheraWorkerService()
