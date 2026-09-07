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
    private val binder = object : IPantheraWorker.Stub() {
        override fun open(engine: String, dictionary: String): Int = runNative {
            if (!opened) opened = PantheraNative.nativeOpen(engine, dictionary) == 0
            if (opened) PantheraNative.nativeSampleRate() else -1
        }
        override fun start(voice: String, creator: Int, voiceId: Int, text: ByteArray,
                           wpm: Int, volume: Int, generation: String, numbers: String): Int = runNative {
            check(opened)
            PantheraNative.nativeSetVolume(volume, generation)
            PantheraNative.nativeSetNumberStyle(numbers)
            PantheraNative.nativeSpeakStart(voice, creator, voiceId, text, wpm)
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
        override fun stop() { if (opened) PantheraNative.nativeStop() }
        override fun finish() { runNative { if (opened) PantheraNative.nativeFinish() } }
    }
    override fun onBind(intent: Intent): IBinder = binder
    override fun onDestroy() {
        synthesis.shutdownNow()
        super.onDestroy()
    }
}
class TigerWorkerService : PantheraWorkerService()
class LeopardWorkerService : PantheraWorkerService()
class SnowLeopardWorkerService : PantheraWorkerService()
class LionWorkerService : PantheraWorkerService()
